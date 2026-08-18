"""Background re-evaluation of every configured encoder source."""

import logging
import os
import threading
import uuid
from collections.abc import Iterable

from app import config
from app.encoder.events import reprocess_to_payload

logger = logging.getLogger(__name__)


def is_excluded_path(path: str, base: str) -> bool:
    """Whether a media-library walk must omit *path* and its descendants."""
    name = os.path.basename(os.path.normpath(path))
    if name.startswith(".") or name == ".trickplay":
        return True
    return name == config.MUSIC_FOLDER_NAME and os.path.normcase(
        os.path.dirname(os.path.normpath(path))
    ) == os.path.normcase(os.path.normpath(base))


def prune_excluded_dirs(root: str, dirs: list[str], bases: Iterable[str]) -> None:
    """Update an ``os.walk`` directory list to avoid excluded subtrees."""
    dirs[:] = [
        name
        for name in dirs
        if not any(is_excluded_path(os.path.join(root, name), base) for base in bases)
    ]


def has_excluded_ancestor(path: str, base: str) -> bool:
    """Whether *path* is inside an excluded child of *base*."""
    resolved_path = os.path.normpath(path)
    resolved_base = os.path.normpath(base)
    try:
        if os.path.normcase(
            os.path.commonpath([resolved_path, resolved_base])
        ) != os.path.normcase(resolved_base):
            return False
    except ValueError:
        return False
    relative = os.path.relpath(resolved_path, resolved_base)
    if relative == ".":
        return False
    current = resolved_base
    for component in relative.split(os.sep):
        current = os.path.join(current, component)
        if is_excluded_path(current, resolved_base):
            return True
    return False


def is_within(path: str, base: str) -> bool:
    """Whether *path* is contained by *base*, including a filesystem root."""
    try:
        return os.path.normcase(os.path.commonpath([path, base])) == os.path.normcase(
            base
        )
    except ValueError:
        return False


def _normalise_roots(paths: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate roots and remove children already covered by a parent."""
    roots: list[str] = []
    for path in paths:
        root = os.path.realpath(path)
        if any(is_within(root, existing) for existing in roots):
            continue
        roots = [existing for existing in roots if not is_within(existing, root)]
        roots.append(root)
    return tuple(roots)


class ReprocessManager:
    """Run one library-wide re-evaluation at a time on a daemon thread."""

    def __init__(self, queue, events, *, valid_extensions: set[str]) -> None:
        self._queue = queue
        self._events = events
        self._extensions = {extension.lower() for extension in valid_extensions}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_id: str | None = None

    def start(self, paths: list[str]) -> dict[str, str]:
        """Start a scan, or return the existing run while one is in flight."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"run_id": self._run_id or "", "status": "started"}
            run_id = str(uuid.uuid4())
            snapshot = _normalise_roots(paths)
            self._run_id = run_id
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(run_id, snapshot),
                name="encoder-reprocess-all",
                daemon=True,
            )
            self._thread.start()
            return {"run_id": run_id, "status": "started"}

    def stop(self, timeout: float = 5.0) -> None:
        """Request a running scan to stop and wait for its planner thread."""
        self._stopping.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise RuntimeError("Bulk reprocess did not stop within the timeout")

    def _publish(
        self,
        run_id: str,
        status: str,
        *,
        scanned: int,
        created: int,
        skipped: int,
        failed: int,
        path: str | None = None,
        error: str | None = None,
    ) -> None:
        self._events.publish(
            reprocess_to_payload(
                run_id,
                status,
                scanned=scanned,
                created=created,
                skipped=skipped,
                failed=failed,
                path=path,
                error=error,
            )
        )

    def _run(self, run_id: str, paths: tuple[str, ...]) -> None:
        scanned = created = skipped = failed = 0
        library_bases = tuple(os.path.realpath(base) for base in config.BASE_PATHS)
        visited: set[str] = set()
        self._publish(
            run_id,
            "started",
            scanned=scanned,
            created=created,
            skipped=skipped,
            failed=failed,
        )
        try:
            for base in paths:
                if self._stopping.is_set():
                    self._publish(
                        run_id,
                        "failed",
                        scanned=scanned,
                        created=created,
                        skipped=skipped,
                        failed=failed,
                        error="Bulk reprocess stopped",
                    )
                    return
                if not os.path.isdir(base):
                    logger.warning("Reprocess watch path is not readable: %s", base)
                    continue
                if not any(
                    is_within(base, library_base) for library_base in library_bases
                ):
                    logger.warning(
                        "Reprocess watch path is outside configured media roots: %s",
                        base,
                    )
                    continue
                if any(
                    has_excluded_ancestor(base, library_base)
                    for library_base in library_bases
                ):
                    logger.warning("Reprocess watch path is excluded: %s", base)
                    continue
                for root, dirs, names in os.walk(base, onerror=_ignore_walk_error):
                    if self._stopping.is_set():
                        self._publish(
                            run_id,
                            "failed",
                            scanned=scanned,
                            created=created,
                            skipped=skipped,
                            failed=failed,
                            error="Bulk reprocess stopped",
                        )
                        return
                    if is_excluded_path(root, base) or any(
                        has_excluded_ancestor(root, library_base)
                        for library_base in library_bases
                    ):
                        dirs[:] = []
                        continue
                    prune_excluded_dirs(root, dirs, (base, *library_bases))
                    for name in names:
                        if self._stopping.is_set():
                            self._publish(
                                run_id,
                                "failed",
                                scanned=scanned,
                                created=created,
                                skipped=skipped,
                                failed=failed,
                                error="Bulk reprocess stopped",
                            )
                            return
                        path = os.path.realpath(os.path.join(root, name))
                        if (
                            path in visited
                            or is_excluded_path(path, base)
                            or not any(
                                is_within(path, watch_root) for watch_root in paths
                            )
                            or not any(
                                is_within(path, library_base)
                                for library_base in library_bases
                            )
                            or any(
                                has_excluded_ancestor(path, library_base)
                                for library_base in library_bases
                            )
                            or os.path.splitext(name)[1].lower() not in self._extensions
                        ):
                            continue
                        visited.add(path)
                        scanned += 1
                        try:
                            result = self._queue.reprocess_path(path)
                        except Exception:
                            failed += 1
                            logger.exception("Could not reprocess %s", path)
                            self._publish(
                                run_id,
                                "running",
                                scanned=scanned,
                                created=created,
                                skipped=skipped,
                                failed=failed,
                                path=path,
                                error="Could not reprocess file; see server logs",
                            )
                            continue
                        if result.get("stage") == "failed":
                            failed += 1
                        elif result.get("created"):
                            created += 1
                        else:
                            skipped += 1
                        self._publish(
                            run_id,
                            "running",
                            scanned=scanned,
                            created=created,
                            skipped=skipped,
                            failed=failed,
                            path=path,
                        )
        except Exception:
            logger.exception("Bulk reprocess %s failed", run_id)
            self._publish(
                run_id,
                "failed",
                scanned=scanned,
                created=created,
                skipped=skipped,
                failed=failed,
                error="Bulk reprocess failed; see server logs",
            )
            return
        self._publish(
            run_id,
            "completed",
            scanned=scanned,
            created=created,
            skipped=skipped,
            failed=failed,
        )


def _ignore_walk_error(error: OSError) -> None:
    logger.warning("Could not read %s during bulk reprocess: %s", error.filename, error)
