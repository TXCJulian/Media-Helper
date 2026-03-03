import os
from functools import lru_cache

from app.config import (
    BASE_PATH,
    TVSHOW_FOLDER_NAME,
    MUSIC_FOLDER_NAME,
    VALID_VIDEO_EXT,
    VALID_MUSIC_EXT,
)


def has_valid_files(path: str, extensions: set) -> bool:
    for _, _, files in os.walk(path):
        for f in files:
            if any(f.lower().endswith(ext.lower()) for ext in extensions):
                return True
    return False


def get_dirs(base: str, extensions: set) -> list[str]:
    directories = []
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


def get_tvshow_dirs() -> list[str]:
    tvshow_base = os.path.join(BASE_PATH, TVSHOW_FOLDER_NAME)
    if not os.path.isdir(tvshow_base):
        return []
    return get_dirs(tvshow_base, VALID_VIDEO_EXT)


def get_music_dirs() -> list[str]:
    music_base = os.path.join(BASE_PATH, MUSIC_FOLDER_NAME)
    if not os.path.isdir(music_base):
        return []
    return get_dirs(music_base, VALID_MUSIC_EXT)


@lru_cache(maxsize=1)
def _get_all_dirs_cached() -> list[str]:
    return get_tvshow_dirs()


@lru_cache(maxsize=1)
def _get_music_dirs_cached() -> list[str]:
    return get_music_dirs()
