"""Own the encoder watcher and its persisted watch-folder configuration."""

import json
import threading
from collections.abc import Callable

from app.encoder.store import EncoderStore
from app.encoder.reprocess import ReprocessManager
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
        validate_paths: Callable[[list[str]], list[str]] | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._default_paths = list(default_paths)
        self._settle_seconds = settle_seconds
        self._valid_extensions = valid_extensions
        self._validate_paths = validate_paths or (lambda paths: list(paths))
        self._watcher: EncoderWatcher | None = None
        self._reprocess: ReprocessManager | None = None
        self._failure: str | None = None
        self._resolved_paths: list[str] | None = None
        self._lock = threading.RLock()

    @property
    def watch_paths(self) -> list[str]:
        with self._lock:
            if self._failure is not None:
                raise RuntimeError(self._failure)
            if self._resolved_paths is None:
                self._resolved_paths = self._resolve_paths()
            return list(self._resolved_paths)

    def _load_watch_paths(self) -> list[str]:
        raw = self._store.get_setting("watch_paths", None)
        if raw is None:
            return list(self._default_paths)
        try:
            paths = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Persisted encoder watch paths are not valid JSON"
            ) from exc
        if not isinstance(paths, list) or any(
            not isinstance(path, str) for path in paths
        ):
            raise RuntimeError("Persisted encoder watch paths are not a string list")
        return paths

    def _resolve_paths(self) -> list[str]:
        persisted = self._store.get_setting("watch_paths", None)
        paths = self._load_watch_paths()
        try:
            validated = self._validate_paths(paths)
        except Exception as exc:
            # Do not rewrite a bad persisted value. Keeping it intact lets an
            # operator correct the roots and retry instead of silently falling
            # back to an environment default (or watching outside BASE_PATHS).
            raise RuntimeError(f"Encoder watch paths are unavailable: {exc}") from exc
        if persisted is None:
            self._store.set_setting("watch_paths", json.dumps(validated))
        return validated

    def _make_watcher(self, paths: list[str]) -> EncoderWatcher:
        return EncoderWatcher(
            self._store,
            on_settled=self._queue.plan_new,
            paths=paths,
            settle_seconds=self._settle_seconds,
            valid_extensions=self._valid_extensions,
        )

    def _unavailable(self, watcher: EncoderWatcher | None, reason: str) -> RuntimeError:
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
            paths = self.watch_paths
            self._watcher = self._make_watcher(paths)
            self._watcher.start()

    def stop(self) -> None:
        with self._lock:
            if self._reprocess is not None:
                self._reprocess.stop()
            if self._watcher is not None:
                self._watcher.stop()
                self._watcher = None
            self._resolved_paths = None

    def start_reprocess_all(self) -> dict[str, str]:
        """Re-evaluate the current library once without restarting its watcher."""
        with self._lock:
            paths = self.watch_paths
            if self._watcher is None:
                raise RuntimeError("Encoder watch runtime is unavailable")
            if self._reprocess is None:
                self._reprocess = ReprocessManager(
                    self._queue, self._queue.events, valid_extensions=self._valid_extensions
                )
            return self._reprocess.start(paths)

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
            self._resolved_paths = list(paths)
            return paths
