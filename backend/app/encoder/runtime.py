"""Own the encoder watcher and its persisted watch-folder configuration."""

import json
import threading

from app.encoder.store import EncoderStore
from app.encoder.watcher import EncoderWatcher


class EncoderRuntime:
    """Replace encoder watch folders without leaving the prior watcher running."""

    def __init__(
        self,
        store: EncoderStore,
        queue,
        *,
        default_paths: list[str],
        settle_seconds: int,
        valid_extensions: set[str],
    ) -> None:
        self._store = store
        self._queue = queue
        self._default_paths = list(default_paths)
        self._settle_seconds = settle_seconds
        self._valid_extensions = valid_extensions
        self._watcher: EncoderWatcher | None = None
        self._lock = threading.RLock()

    @property
    def watch_paths(self) -> list[str]:
        raw = self._store.get_setting(
            "watch_paths", json.dumps(self._default_paths)
        )
        return json.loads(raw)

    def _make_watcher(self, paths: list[str]) -> EncoderWatcher:
        return EncoderWatcher(
            self._store,
            on_settled=self._queue.plan_new,
            paths=paths,
            settle_seconds=self._settle_seconds,
            valid_extensions=self._valid_extensions,
        )

    def start(self) -> None:
        """Seed legacy installations once, then start their configured watcher."""
        with self._lock:
            if self._store.get_setting("watch_paths", None) is None:
                self._store.set_setting(
                    "watch_paths", json.dumps(self._default_paths)
                )
            self._watcher = self._make_watcher(self.watch_paths)
            self._watcher.start()

    def stop(self) -> None:
        with self._lock:
            if self._watcher is not None:
                self._watcher.stop()
                self._watcher = None

    def replace_watch_paths(self, paths: list[str]) -> list[str]:
        """Activate *paths* before making them the durable configuration.

        Starting the replacement first keeps the current setting and watcher
        paired if watchdog rejects the new paths.  A write-first sequence has
        no safe recovery when the attempt to write the old JSON back fails:
        the old watcher would run while a restart adopted the new JSON.
        """
        with self._lock:
            old_watcher = self._watcher
            replacement = self._make_watcher(paths)
            try:
                replacement.start()
            except Exception as exc:
                try:
                    replacement.stop()
                except Exception:
                    pass
                raise RuntimeError("Could not replace encoder watch paths") from exc

            try:
                self._store.set_setting("watch_paths", json.dumps(paths))
            except Exception as exc:
                try:
                    replacement.stop()
                except Exception:
                    pass
                raise RuntimeError("Could not replace encoder watch paths") from exc

            self._watcher = replacement
            if old_watcher is not None:
                try:
                    old_watcher.stop()
                except Exception as exc:
                    raise RuntimeError("Could not stop encoder watch paths") from exc
            return paths
