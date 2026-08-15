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

_SCAN_INTERVAL = 5.0


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
        """Walk every watch path once, feeding candidates to the tracker."""
        for root in self._paths:
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    self._consider(os.path.join(dirpath, name))

    def _scan_loop(self) -> None:
        while not self._stopping.wait(timeout=_SCAN_INTERVAL):
            try:
                self.scan_existing()
            except Exception:
                logger.exception("Watch rescan failed")

    def _consider(self, path: str) -> None:
        if os.path.splitext(path)[1].lower() not in self._extensions:
            return
        if os.path.basename(path).startswith(".hbenc-"):
            # Our own in-progress output, not a new arrival.
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if path in self._store.active_source_paths():
            return
        if not self._tracker.saw(path, size):
            return
        self._tracker.forget(path)
        try:
            self._on_settled(path)
        except Exception:
            logger.exception("Failed to plan a job for %s", path)
