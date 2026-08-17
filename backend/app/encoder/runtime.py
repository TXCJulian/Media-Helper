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
        self._failure: str | None = None
        self._lock = threading.RLock()

    @property
    def watch_paths(self) -> list[str]:
        with self._lock:
            if self._failure is not None:
                raise RuntimeError(self._failure)
            return self._load_watch_paths()

    def _load_watch_paths(self) -> list[str]:
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

    def _unavailable(
        self, watcher: EncoderWatcher | None, reason: str
    ) -> RuntimeError:
        """Record an explicit state when watcher activity is uncertain."""
        self._watcher = watcher
        self._failure = f"{reason}; encoder watch runtime unavailable until restart"
        return RuntimeError(self._failure)

    def _discard_replacement(self, replacement: EncoderWatcher) -> None:
        """Stop the sole candidate, or retain it in an explicit error state."""
        try:
            replacement.stop()
        except Exception as exc:
            raise self._unavailable(
                replacement, "Could not clean up replacement watcher"
            ) from exc
        self._watcher = None

    def _restore_watcher(self, paths: list[str]) -> None:
        """Restore the durable configuration after the sole candidate stops."""
        restored: EncoderWatcher | None = None
        try:
            restored = self._make_watcher(paths)
            self._watcher = restored
            restored.start()
        except Exception as exc:
            raise self._unavailable(
                restored, "Could not restore previous watcher"
            ) from exc

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
        """Replace paths without ever overlapping two active watchers.

        The old watcher is retired before constructing the candidate. Once
        there can be only one live watcher, cleanup failures can be represented
        as an explicit unavailable state instead of an incoherent healthy one.
        """
        with self._lock:
            old_paths = self.watch_paths
            old_watcher = self._watcher
            if old_watcher is None:
                raise RuntimeError("Encoder watch runtime is unavailable")

            try:
                old_watcher.stop()
            except Exception as exc:
                raise RuntimeError("Could not stop encoder watch paths") from exc
            self._watcher = None

            replacement: EncoderWatcher | None = None
            try:
                replacement = self._make_watcher(paths)
                replacement.start()
            except Exception as exc:
                if replacement is not None:
                    self._discard_replacement(replacement)
                self._restore_watcher(old_paths)
                raise RuntimeError("Could not replace encoder watch paths") from exc

            try:
                self._store.set_setting("watch_paths", json.dumps(paths))
            except Exception as exc:
                try:
                    durable_paths = self._load_watch_paths()
                except Exception as read_exc:
                    raise self._unavailable(
                        replacement, "Could not determine persisted watch paths"
                    ) from read_exc

                if durable_paths == paths:
                    self._watcher = replacement
                    raise RuntimeError(
                        "Could not confirm replacement watch-path persistence"
                    ) from exc

                self._discard_replacement(replacement)
                self._restore_watcher(durable_paths)
                raise RuntimeError("Could not replace encoder watch paths") from exc

            self._watcher = replacement
            return paths
