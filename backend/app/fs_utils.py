import os
import logging

logger = logging.getLogger(__name__)


def flush_directory(directory: str) -> None:
    """Flush directory metadata so mount clients (SMB/CIFS) notice changes."""
    try:
        if hasattr(os, "O_DIRECTORY"):
            dir_flag = getattr(os, "O_DIRECTORY", 0)
            dir_fd = os.open(directory, dir_flag | os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        else:
            sync_fn = getattr(os, "sync", None)
            if sync_fn:
                sync_fn()
    except OSError:
        try:
            sync_fn = getattr(os, "sync", None)
            if sync_fn:
                sync_fn()
        except OSError:
            pass


def collision_safe_path(dst: str, max_attempts: int = 10000) -> str:
    """If dst exists, append (1), (2), etc. until a free path is found."""
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    for k in range(1, max_attempts + 1):
        candidate = f"{base} ({k}){ext}"
        if not os.path.exists(candidate):
            return candidate
    raise OSError(
        f"Could not find a free filename after {max_attempts} attempts: {dst}"
    )
