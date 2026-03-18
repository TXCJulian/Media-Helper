import os
from functools import lru_cache

from app.config import (
    BASE_PATHS,
    BASE_PATH_LABELS,
    TVSHOW_FOLDER_NAME,
    MUSIC_FOLDER_NAME,
    VALID_VIDEO_EXT,
    VALID_MUSIC_EXT,
    VALID_CUTTER_EXT,
)

# Reverse map: full path -> label
_path_to_label: dict[str, str] = {v: k for k, v in BASE_PATH_LABELS.items()}


def has_valid_files(path: str, extensions: set) -> bool:
    """Check if path or any subdirectory contains files matching extensions."""
    for _, _, files in os.walk(path):
        for f in files:
            if any(f.lower().endswith(ext.lower()) for ext in extensions):
                return True
    return False


def get_dirs(base: str, extensions: set) -> list[str]:
    """Walk base and return sorted relative directory paths containing matching files.
    Prunes .trickplay directories (Jellyfin metadata)."""
    if not os.path.isdir(base):
        return []
    directories: list[str] = []
    for root, dirs, _ in os.walk(base):
        dirs[:] = [
            d for d in dirs if not d.endswith(".trickplay") and ".trickplay" not in root
        ]
        for d in dirs:
            full_path = os.path.join(root, d)
            if has_valid_files(full_path, extensions):
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


@lru_cache(maxsize=1)
def _get_all_dirs_cached() -> list[dict[str, str]]:
    return get_tvshow_dirs()


@lru_cache(maxsize=1)
def _get_music_dirs_cached() -> list[dict[str, str]]:
    return get_music_dirs()


@lru_cache(maxsize=1)
def _get_cutter_dirs_cached() -> list[dict[str, str]]:
    return get_cutter_dirs()
