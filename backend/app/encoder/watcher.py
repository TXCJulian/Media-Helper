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
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.encoder.store import EncoderStore

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
        on_settled: Callable[[str], None],
        paths: list[str],
        settle_seconds: int,
        valid_extensions: set[str],
    ) -> None:
        self._store = store
        self._on_settled = on_settled
        self._paths = paths
        self._extensions = {e.lower() for e in valid_extensions}
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
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            self._observer = None
        if self._scanner is not None:
            self._scanner.join(timeout=timeout)
            self._scanner = None

    def scan_existing(self) -> None:
        """Walk every watch path once, feeding candidates to the tracker.

        Fetches the active-source-paths set and the observed-sizes map once
        for the whole scan rather than once per file: on a 10k-file library
        that was 10k SQL queries every scan for no benefit, since nothing in
        the loop below changes either partway through a single walk.
        """
        active = self._store.active_source_paths()
        observed = self._store.observed_sizes()
        for root in self._paths:
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    self._consider(os.path.join(dirpath, name), active, observed)

    def _scan_loop(self) -> None:
        while not self._stopping.wait(timeout=_SCAN_INTERVAL):
            try:
                self.scan_existing()
            except Exception:
                logger.exception("Watch rescan failed")

    def _consider(
        self,
        path: str,
        active: set[str] | None = None,
        observed: dict[str, set[int]] | None = None,
    ) -> None:
        """Consider *path* for dispatch.

        *active* and *observed* let `scan_existing()` pass in one shared
        `active_source_paths()` / `observed_sizes()` result for the whole walk.
        Event-driven calls (from the watchdog handler) have no such batch to
        share and fall back to fresh, single-file queries -- events are
        comparatively rare next to a full rescan, so those per-call queries
        are not the hot path this exists to fix.
        """
        if os.path.splitext(path)[1].lower() not in self._extensions:
            return
        if os.path.basename(path).startswith(".hbenc-"):
            # Our own in-progress output, not a new arrival.
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            # The file vanished between being listed and being stat'd (a
            # cancelled rip cleaned up, a failed copy removed). Without this,
            # SettleTracker._seen would keep an entry for a path that will
            # never be observed again, growing unbounded over the watcher's
            # lifetime.
            self._tracker.forget(path)
            return
        if active is None:
            active = self._store.active_source_paths()
        if path in active:
            return
        if observed is None:
            observed = self._store.observed_sizes()
        if size in observed.get(path, ()):
            # We have already decided about this exact file. A job that
            # reached a terminal stage is in neither `active` nor the tracker,
            # so without this the next rescan would treat it as a new arrival
            # and re-probe it -- one ffprobe subprocess and one job row per
            # file per scan, indefinitely. Comparing the size (rather than
            # just the path) keeps a genuinely replaced file detectable.
            return
        if not self._tracker.saw(path, size):
            return
        self._tracker.forget(path)
        try:
            self._on_settled(path)
        except Exception:
            logger.exception("Failed to plan a job for %s", path)
