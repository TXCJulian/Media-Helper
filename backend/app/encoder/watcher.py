"""Watch folders for new video files and hand settled ones to the planner.

Two parts, split so the interesting half needs no filesystem and no sleeping:
``SettleTracker`` is pure apart from an injectable clock, and ``EncoderWatcher``
is the Watchdog plumbing around it.

The settle window exists because a rip lands over minutes. ffprobe on a growing
file happily reports whatever has arrived so far, which would match the wrong
rule and encode a truncated movie.
"""

import logging
import os
import threading
import time
from collections.abc import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app import config
from app.encoder.events import EventBroadcaster, job_to_payload
from app.encoder.reprocess import (
    AuthorizedPathContext,
    prune_excluded_dirs,
    resolve_authorized_path,
)
from app.encoder.store import EncoderStore, Job

logger = logging.getLogger(__name__)

# Ample given the settle window (ENCODER_SETTLE_SECONDS defaults to 30s): a
# file that just started copying does not need to be rechecked every 5s, and
# on a 10k-file library (a normal movie collection with per-title
# subfolders) a 5s cadence meant a full os.walk plus one active_source_paths
# query *per file* every five seconds -- a continuous stat storm made worse
# on network-mounted libraries, which is exactly where this watcher runs.
_SCAN_INTERVAL = 30.0


class SettleTracker:
    """Decides when a file has stopped changing.

    ``saw`` returns True the first time a path is observed at the same size for
    at least ``settle_seconds``. It never returns True on first sight: a single
    observation cannot establish that a size is stable.
    """

    def __init__(self, settle_seconds: int, now: Callable[[], float] = time.time):
        self._settle = settle_seconds
        self._now = now
        self._seen: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def saw(self, path: str, size: int) -> bool:
        with self._lock:
            previous = self._seen.get(path)
            now = self._now()
            if previous is None or previous[0] != size:
                # First sight, or the size changed: (re)start the window.
                self._seen[path] = (size, now)
                return False
            return now - previous[1] >= self._settle

    def forget(self, path: str) -> None:
        with self._lock:
            self._seen.pop(path, None)


class _Handler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._on_change = on_change

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._on_change(str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        # A rip tool that writes to a temp name and renames on completion looks
        # like a move, not a create.
        if not event.is_directory:
            self._on_change(str(event.dest_path))


class EncoderWatcher:
    """Watchdog observer plus a periodic rescan.

    The rescan is not redundant: filesystem events are missed across restarts
    and are unreliable on network mounts, which is exactly where a media
    library lives. It is also what re-checks a file whose settle window has not
    yet elapsed, since no further event will arrive once copying finishes.
    """

    def __init__(
        self,
        store: EncoderStore,
        # (path, size, mtime_ns). The fingerprint is passed through so the
        # callback can persist it atomically with the job it creates; see the
        # note at the dispatch site.
        on_settled: Callable[[str, int, int], None],
        paths: list[str],
        settle_seconds: int,
        valid_extensions: set[str],
        events: EventBroadcaster | None = None,
    ) -> None:
        self._store = store
        self._on_settled = on_settled
        self._paths = paths
        self._extensions = {e.lower() for e in valid_extensions}
        self._events = events
        self._tracker = SettleTracker(settle_seconds)
        self._observer: Observer | None = None
        self._stopping = threading.Event()
        self._scanner: threading.Thread | None = None

    def start(self) -> None:
        if not self._paths:
            logger.info("ENCODER_WATCH_PATHS is empty; the watcher stays off")
            return
        self._observer = Observer()
        handler = _Handler(self._consider)
        for path in self._paths:
            if not os.path.isdir(path):
                logger.warning("Watch path does not exist, skipping: %s", path)
                continue
            self._observer.schedule(handler, path, recursive=True)
        self._observer.start()
        self._scanner = threading.Thread(
            target=self._scan_loop, name="encoder-watch-scan", daemon=True
        )
        self._scanner.start()
        logger.info("Watching %s for new video files", ", ".join(self._paths))

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        failures: list[str] = []
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            if self._observer.is_alive():
                failures.append("observer")
            else:
                self._observer = None
        if self._scanner is not None:
            self._scanner.join(timeout=timeout)
            if self._scanner.is_alive():
                failures.append("scanner")
            else:
                self._scanner = None
        if failures:
            raise RuntimeError(
                "Encoder watcher did not stop within the timeout: "
                + ", ".join(failures)
            )

    def scan_existing(self) -> None:
        """Walk every watch path once, feeding candidates to the tracker.

        Fetches the active-jobs map and the seen-fingerprints map once
        for the whole scan rather than once per file: on a 10k-file library
        that was 10k SQL queries every scan for no benefit, since nothing in
        the loop below changes either partway through a single walk.
        """
        active_jobs = self._store.active_jobs_by_source()
        seen = self._store.seen_fingerprints()
        auth_ctx = AuthorizedPathContext.from_roots_and_bases(
            self._paths, config.BASE_PATHS
        )
        present: set[str] = set()
        walked: list[str] = []

        for root in self._paths:
            if not os.path.isdir(root):
                # An unmounted share walks as empty. Treating it as walked
                # would let the prune below delete every record it holds and
                # re-detect the whole share when it came back.
                logger.warning("Watch path %s is not readable; skipping", root)
                continue

            # os.walk swallows errors by default, so an unreadable subtree is
            # indistinguishable from an empty one -- and its files would then
            # look deleted. Any error anywhere under a root disqualifies that
            # root from pruning; the scan itself still proceeds.
            failed = False

            def _on_walk_error(exc: OSError, _root: str = root) -> None:
                nonlocal failed
                failed = True
                logger.warning(
                    "Could not read %s under %s: %s",
                    getattr(exc, "filename", "?"),
                    _root,
                    exc,
                )

            for dirpath, dirs, files in os.walk(root, onerror=_on_walk_error):
                if (
                    resolve_authorized_path(dirpath, auth_ctx)
                    is None
                ):
                    dirs[:] = []
                    continue
                prune_excluded_dirs(dirpath, dirs, config.BASE_PATHS)
                for name in files:
                    path = os.path.join(dirpath, name)
                    present.add(path)
                    self._consider(path, active_jobs, seen, auth_ctx=auth_ctx)

            if not failed:
                walked.append(root)

        # Drop records for files that have since been deleted or renamed;
        # otherwise the table grows with library churn forever.
        #
        # Only records that existed when this scan STARTED are eligible, and
        # each delete carries the fingerprint it saw. Both halves matter: the
        # queue writes a fingerprint the moment it publishes, which can land
        # after that file's directory was walked. Scoping to the opening
        # snapshot keeps a brand-new path safe; carrying the fingerprint keeps
        # an *updated* one safe, for the case where retention moves the
        # original aside, the scan catches the path mid-gap, and the encode is
        # then published at that same path. Without the fingerprint in the
        # WHERE clause that delete removes the fresh record, and the next scan
        # re-encodes our own output.
        stale = [
            (path, *fingerprint)
            for path, fingerprint in seen.items()
            if path not in present
            and any(path == root or path.startswith(root + os.sep) for root in walked)
        ]
        if stale:
            removed = self._store.forget_seen_entries(stale)
            logger.info(
                "Pruned %d dedup record(s) for files no longer present", removed
            )

    def _scan_loop(self) -> None:
        while not self._stopping.wait(timeout=_SCAN_INTERVAL):
            try:
                self.scan_existing()
            except Exception:
                logger.exception("Watch rescan failed")

    def _consider(
        self,
        path: str,
        active_jobs: dict[str, Job] | None = None,
        seen: dict[str, tuple[int, int]] | None = None,
        auth_ctx: AuthorizedPathContext | None = None,
    ) -> None:
        """Consider *path* for dispatch.

        *active_jobs* and *seen* let `scan_existing()` pass in one shared
        `active_jobs_by_source()` / `seen_fingerprints()` result for the whole
        walk. Event-driven calls (from the watchdog handler) have no such batch
        to share and fall back to fresh, single-file queries -- events are
        comparatively rare next to a full rescan, so those per-call queries
        are not the hot path this exists to fix.
        """
        if os.path.splitext(path)[1].lower() not in self._extensions:
            return
        authorized = resolve_authorized_path(
            path, auth_ctx if auth_ctx is not None else self._paths, config.BASE_PATHS
        )
        if authorized is None:
            return
        path = authorized
        if os.path.basename(path).startswith(".hbenc-"):
            # Our own in-progress output, not a new arrival.
            return
        try:
            stat = os.stat(path)
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            # The file vanished between being listed and being stat'd (a
            # cancelled rip cleaned up, a failed copy removed). Without this,
            # SettleTracker._seen would keep an entry for a path that will
            # never be observed again, growing unbounded over the watcher's
            # lifetime.
            self._tracker.forget(path)
            return

        if active_jobs is None:
            active_jobs = self._store.active_jobs_by_source()

        active_job = active_jobs.get(path)
        if active_job is not None:
            if active_job.stage in {"encoding", "swapping"}:
                return
            if active_job.stage == "blocked" and active_job.remote_job_id:
                return
            if active_job.stage in {"settling", "pending", "blocked"}:
                if seen is None:
                    seen = self._store.seen_fingerprints()
                seen_fp = seen.get(path)
                if seen_fp is None or seen_fp == (size, mtime_ns):
                    # File has not changed on disk (or is not yet fingerprinted); job remains valid.
                    return

                logger.info(
                    "Source file %s modified on disk (new size=%d, mtime=%d); cancelling stale %s job %s",
                    path,
                    size,
                    mtime_ns,
                    active_job.stage,
                    active_job.id,
                )
                self._store.set_stage(
                    active_job.id,
                    "cancelled",
                    error="Source file was modified on disk; invalidating stale job",
                    error_code="file_modified",
                )
                if self._events is not None:
                    updated = self._store.get_job(active_job.id)
                    if updated is not None:
                        self._events.publish(job_to_payload(updated))
                self._store.forget_seen(path)
                self._tracker.forget(path)
                active_jobs.pop(path, None)
                if seen is not None:
                    seen.pop(path, None)

        if seen is None:
            seen = self._store.seen_fingerprints()
        if seen.get(path) == (size, mtime_ns):
            # Already decided about this exact file. A job that reached a
            # terminal stage is in neither `active` nor the tracker, so
            # without this the next rescan would treat it as a new arrival and
            # re-probe it -- one ffprobe subprocess and one job row per file
            # per scan, indefinitely.
            #
            # Matching on (size, mtime_ns) rather than size alone is what makes
            # a *replacement* visible: re-copying a file restores its byte
            # count but gives it a new mtime, and a size-only check silently
            # ignored exactly that case.
            return
        if not self._tracker.saw(path, size):
            return
        self._tracker.forget(path)
        # The fingerprint travels with the dispatch rather than being written
        # here first: the callback records it atomically with the job row, so
        # a failure to create that row leaves the file un-suppressed and it is
        # retried. Writing it here meant a throw between the two left a
        # durable fingerprint with no job, and the file was never looked at
        # again.
        try:
            self._on_settled(path, size, mtime_ns)
        except Exception:
            logger.exception("Failed to plan a job for %s", path)
