"""Background re-evaluation of every configured encoder source."""

import logging
import os
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from app import config
from app.encoder.events import reprocess_to_payload

logger = logging.getLogger(__name__)


def _norm_dir(path: str) -> str:
    """Normalize a path and strip trailing slashes unless it is a bare root."""
    norm = os.path.normcase(os.path.normpath(path))
    return norm.rstrip("/\\") or norm


def _is_excluded_name(
    name: str,
    parent_dir: str | None = None,
    base: str | None = None,
    *,
    exclude_music: bool = True,
) -> bool:
    """Shared predicate for paths, directories, and ancestor components."""
    if (
        name.startswith(".")
        or name.endswith(".trickplay")
        or ".trickplay" in name.lower()
    ):
        return True
    if (
        exclude_music
        and name == config.MUSIC_FOLDER_NAME
        and parent_dir is not None
        and base is not None
    ):
        return _norm_dir(parent_dir) == _norm_dir(base)
    return False


def is_excluded_path(path: str, base: str) -> bool:
    """Whether a media-library walk must omit *path* and its descendants."""
    norm = os.path.normpath(path)
    return _is_excluded_name(
        name=os.path.basename(norm),
        parent_dir=os.path.dirname(norm),
        base=base,
        exclude_music=True,
    )


def prune_excluded_dirs(root: str, dirs: list[str], bases: Iterable[str]) -> None:
    """Update an ``os.walk`` directory list to avoid excluded subtrees."""
    dirs[:] = [
        name
        for name in dirs
        if not any(is_excluded_path(os.path.join(root, name), base) for base in bases)
    ]


def has_excluded_ancestor(path: str, base: str, *, exclude_music: bool = True) -> bool:
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
        norm = os.path.normpath(current)
        if _is_excluded_name(
            name=os.path.basename(norm),
            parent_dir=os.path.dirname(norm),
            base=resolved_base,
            exclude_music=exclude_music,
        ):
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


def _absolute(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


@dataclass(frozen=True)
class AuthorizedPathContext:
    lexical_roots: tuple[str, ...]
    resolved_roots: tuple[str, ...]
    request_roots: tuple[str, ...]
    lexical_bases: tuple[str, ...]
    resolved_bases: tuple[str, ...]
    request_bases: tuple[str, ...]

    @classmethod
    def from_roots_and_bases(
        cls, roots: Iterable[str], library_bases: Iterable[str]
    ) -> "AuthorizedPathContext":
        lexical_roots = tuple(_absolute(root) for root in roots)
        resolved_roots = tuple(os.path.realpath(root) for root in roots)
        request_roots = tuple(dict.fromkeys((*lexical_roots, *resolved_roots)))
        lexical_bases = tuple(_absolute(base) for base in library_bases)
        resolved_bases = tuple(os.path.realpath(base) for base in library_bases)
        request_bases = tuple(dict.fromkeys((*lexical_bases, *resolved_bases)))
        return cls(
            lexical_roots=lexical_roots,
            resolved_roots=resolved_roots,
            request_roots=request_roots,
            lexical_bases=lexical_bases,
            resolved_bases=resolved_bases,
            request_bases=request_bases,
        )


def resolve_authorized_path(
    path: str,
    roots: Iterable[str] | AuthorizedPathContext,
    library_bases: Iterable[str] | None = None,
) -> str | None:
    """Return the real path only when both path spellings are authorized."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    if isinstance(roots, AuthorizedPathContext):
        ctx = roots
    else:
        ctx = AuthorizedPathContext.from_roots_and_bases(roots, library_bases or ())

    if not ctx.lexical_roots:
        return None
    lexical = _absolute(path)
    resolved = os.path.realpath(lexical)
    # Directory picker results are canonical paths.  If a configured base or
    # watch root is itself a symlink, that canonical spelling is not lexically
    # below the configured spelling even though it identifies the same safe
    # subtree.  Authorize either spelling, then independently require the
    # resolved target to remain inside a resolved root.  The latter check is
    # what continues to reject symlink escapes.
    if not any(is_within(lexical, root) for root in ctx.request_roots):
        return None
    if not any(is_within(resolved, root) for root in ctx.resolved_roots):
        return None

    for candidate, allowed_roots, bases in (
        (lexical, ctx.request_roots, ctx.request_bases),
        (resolved, ctx.resolved_roots, ctx.resolved_bases),
    ):
        if any(
            has_excluded_ancestor(candidate, root, exclude_music=False)
            for root in allowed_roots
        ):
            return None
        if any(has_excluded_ancestor(candidate, base) for base in bases):
            return None
    return resolved


def _normalise_roots(paths: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate roots and remove children already covered by a parent.

    The returned tuple preserves user-configured lexical path representations for
    downstream authorization matching, while `os.path.realpath` is used for
    containment comparisons to avoid redundant directory walks.
    """
    roots: list[str] = []
    for path in paths:
        root = _absolute(path)
        resolved = os.path.realpath(root)
        if any(is_within(resolved, os.path.realpath(existing)) for existing in roots):
            continue
        roots = [
            existing
            for existing in roots
            if not is_within(os.path.realpath(existing), resolved)
        ]
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
        self._latest: dict | None = None
        self._stop_timeout = 35.0

    def start(self, paths: list[str]) -> dict[str, str]:
        """Start a scan, or return the existing run while one is in flight."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"run_id": self._run_id or "", "status": "already_running"}
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

    def stop(self, timeout: float | None = None) -> None:
        """Request a running scan to stop and wait for its planner thread."""
        self._stopping.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._stop_timeout if timeout is None else timeout)
            if thread.is_alive():
                logger.warning("Bulk reprocess is still finishing its current probe")

    def status(self) -> dict:
        """Return an authoritative snapshot for clients reconnecting to SSE."""
        with self._lock:
            event = dict(self._latest) if self._latest is not None else None
            active = self._thread is not None and self._thread.is_alive()
        return {"active": active, "event": event}

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
        payload = reprocess_to_payload(
            run_id,
            status,
            scanned=scanned,
            created=created,
            skipped=skipped,
            failed=failed,
            path=path,
            error=error,
        )
        with self._lock:
            self._latest = payload
        self._events.publish(payload)

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
        auth_ctx = AuthorizedPathContext.from_roots_and_bases(paths, library_bases)
        try:
            for base in paths:
                if self._stopping.is_set():
                    self._publish(
                        run_id,
                        "cancelled",
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
                if resolve_authorized_path(base, auth_ctx) is None or not any(
                    is_within(os.path.realpath(base), library_base)
                    for library_base in library_bases
                ):
                    logger.warning(
                        "Reprocess watch path is outside configured media roots: %s",
                        base,
                    )
                    continue
                for root, dirs, names in os.walk(base, onerror=_ignore_walk_error):
                    if self._stopping.is_set():
                        self._publish(
                            run_id,
                            "cancelled",
                            scanned=scanned,
                            created=created,
                            skipped=skipped,
                            failed=failed,
                            error="Bulk reprocess stopped",
                        )
                        return
                    if resolve_authorized_path(root, auth_ctx) is None:
                        dirs[:] = []
                        continue
                    prune_excluded_dirs(root, dirs, library_bases)
                    for name in names:
                        if self._stopping.is_set():
                            self._publish(
                                run_id,
                                "cancelled",
                                scanned=scanned,
                                created=created,
                                skipped=skipped,
                                failed=failed,
                                error="Bulk reprocess stopped",
                            )
                            return
                        path = resolve_authorized_path(
                            os.path.join(root, name), auth_ctx
                        )
                        if (
                            path is None
                            or path in visited
                            or os.path.splitext(name)[1].lower() not in self._extensions
                        ):
                            continue
                        visited.add(path)
                        scanned += 1
                        try:
                            result = self._queue.reprocess_path(
                                path, cancel_event=self._stopping
                            )
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
                        if self._stopping.is_set():
                            self._publish(
                                run_id,
                                "cancelled",
                                scanned=scanned,
                                created=created,
                                skipped=skipped,
                                failed=failed,
                                error="Bulk reprocess stopped",
                            )
                            return
                        if result.get("stage") == "failed":
                            failed += 1
                        elif result.get("stage") == "skipped":
                            skipped += 1
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
