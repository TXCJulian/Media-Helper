import os
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

from app.config import (
    BASE_PATH_LABELS,
    BASE_PATHS,
    MUSIC_FOLDER_NAME,
    TVSHOW_FOLDER_NAME,
    VALID_CUTTER_EXT,
    VALID_MUSIC_EXT,
    VALID_VIDEO_EXT,
)

# Reverse map: full path -> label
_path_to_label: dict[str, str] = {v: k for k, v in BASE_PATH_LABELS.items()}
T = TypeVar("T")
DIRECTORY_CACHE_TTL_SECONDS = 15.0


class ExpiringCache(Generic[T]):
    def __init__(
        self,
        loader: Callable[[], T],
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._value: T | None = None
        self._expires_at = 0.0

    def __call__(self) -> T:
        now = self._clock()
        with self._lock:
            if self._value is None or now >= self._expires_at:
                self._value = self._loader()
                self._expires_at = now + self._ttl_seconds
            return self._value

    def cache_clear(self) -> None:
        with self._lock:
            self._value = None
            self._expires_at = 0.0


def has_valid_files(path: str, extensions: set) -> bool:
    """Check if path or any subdirectory contains files matching extensions."""
    for _, _, files in os.walk(path):
        for f in files:
            if any(f.lower().endswith(ext.lower()) for ext in extensions):
                return True
    return False


def get_dirs(base: str, extensions: set[str] | None) -> list[str]:
    """Return relative directories; filter by file extension unless extensions is None."""
    if not os.path.isdir(base):
        return []
    directories: list[str] = []
    for root, dirs, _ in os.walk(base):
        dirs[:] = [
            d for d in dirs if not d.endswith(".trickplay") and ".trickplay" not in root
        ]
        for directory in dirs:
            full_path = os.path.join(root, directory)
            if extensions is None or has_valid_files(full_path, extensions):
                rel_path = os.path.relpath(full_path, base)
                directories.append(rel_path.replace("\\", "/"))
    return sorted(directories)


def _label_for(base_path: str) -> str:
    return _path_to_label.get(base_path, os.path.basename(base_path))


def get_tvshow_dirs() -> list[dict[str, str]]:
    """Scan all base paths for TV show directories."""
    results: list[dict[str, str]] = []
    for base_path in BASE_PATHS:
        label = _label_for(base_path)
        tvshow_base = os.path.join(base_path, TVSHOW_FOLDER_NAME)
        for rel_path in get_dirs(tvshow_base, VALID_VIDEO_EXT):
            results.append({"path": rel_path, "base": label})
    return sorted(results, key=lambda d: d["path"])


def get_music_dirs() -> list[dict[str, str]]:
    """Scan all base paths for music directories."""
    results: list[dict[str, str]] = []
    for base_path in BASE_PATHS:
        label = _label_for(base_path)
        music_base = os.path.join(base_path, MUSIC_FOLDER_NAME)
        for rel_path in get_dirs(music_base, VALID_MUSIC_EXT):
            results.append({"path": rel_path, "base": label})
    return sorted(results, key=lambda d: d["path"])


def get_cutter_dirs() -> list[dict[str, str]]:
    """Scan all base paths entirely for directories with media files."""
    results: list[dict[str, str]] = []
    for base_path in BASE_PATHS:
        label = _label_for(base_path)
        for rel_path in get_dirs(base_path, VALID_CUTTER_EXT):
            results.append({"path": rel_path, "base": label})
    return sorted(results, key=lambda d: d["path"])


def get_download_dirs() -> list[dict[str, str]]:
    """List every selectable destination directory below all media roots."""
    results: list[dict[str, str]] = []
    for base_path in BASE_PATHS:
        label = _label_for(base_path)
        for rel_path in get_dirs(base_path, None):
            results.append({"path": rel_path, "base": label})
    return sorted(results, key=lambda item: (item["path"], item["base"]))


_get_all_dirs_cached = ExpiringCache(get_tvshow_dirs, DIRECTORY_CACHE_TTL_SECONDS)
_get_music_dirs_cached = ExpiringCache(get_music_dirs, DIRECTORY_CACHE_TTL_SECONDS)
_get_cutter_dirs_cached = ExpiringCache(get_cutter_dirs, DIRECTORY_CACHE_TTL_SECONDS)
_get_download_dirs_cached = ExpiringCache(get_download_dirs, DIRECTORY_CACHE_TTL_SECONDS)
