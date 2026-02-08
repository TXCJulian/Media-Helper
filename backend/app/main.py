from fastapi import FastAPI, Query, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os
import ast
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from rename_episodes import rename_episodes
from rename_music import rename_music
from get_dirs import _get_all_dirs_cached, _get_music_dirs_cached
from auth import get_current_user
from auth_routes import router as auth_router

load_dotenv("dependencies/.env")

BASE_PATH = os.getenv("BASE_PATH") or "/media"
TVSHOW_FOLDER_NAME = os.getenv("TVSHOW_FOLDER_NAME") or "TV Shows"
MUSIC_FOLDER_NAME = os.getenv("MUSIC_FOLDER_NAME") or "Music"
VALID_VIDEO_EXT = set(
    ast.literal_eval(os.getenv("VALID_VIDEO_EXT", "{'.mp4', '.mkv', '.mov', '.avi'}"))
)
VALID_MUSIC_EXT = set(
    ast.literal_eval(os.getenv("VALID_MUSIC_EXT", "{'.mp3', '.flac', '.m4a', '.wav'}"))
)


class DirChangeHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            _get_all_dirs_cached.cache_clear()
            _get_music_dirs_cached.cache_clear()

    def on_deleted(self, event):
        if event.is_directory:
            _get_all_dirs_cached.cache_clear()
            _get_music_dirs_cached.cache_clear()

    def on_moved(self, event):
        if event.is_directory:
            _get_all_dirs_cached.cache_clear()
            _get_music_dirs_cached.cache_clear()


# Global observer instance
_observer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan with startup and shutdown events."""
    global _observer

    # Startup
    os.makedirs("/app/data", exist_ok=True)
    handler = DirChangeHandler()
    if os.path.isdir(BASE_PATH):
        _observer = Observer()
        _observer.schedule(handler, BASE_PATH, recursive=True)
        _observer.start()
    else:
        logging.warning(
            "BASE_PATH '%s' does not exist; file watcher not started.", BASE_PATH
        )
        _observer = None

    yield

    # Shutdown
    if _observer:
        _observer.stop()
        _observer.join()


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/directories/tvshows")
def list_directories(
    series: str | None = Query(None, description="Series filter"),
    season: int | None = Query(None, description="Season number"),
    current_user: dict = Depends(get_current_user),
):
    all_dirs = _get_all_dirs_cached()

    # nach Serie filtern
    filtered = all_dirs
    if series:
        series_lc = series.lower()
        filtered = [d for d in filtered if series_lc in d.lower()]

    # nach Staffel filtern
    if season is not None:
        season_str = f"{season:02d}"
        pattern = f"/season {season_str}"
        filtered = [d for d in filtered if d.lower().endswith(pattern)]

    return {"directories": filtered}


@app.get("/directories/music")
def list_music_directories(
    artist: str | None = Query(None, description="Artist filter"),
    album: str | None = Query(None, description="Album filter"),
    current_user: dict = Depends(get_current_user),
):
    all_dirs = _get_music_dirs_cached()

    filtered = all_dirs
    if artist:
        artist_lc = artist.lower()
        filtered = [d for d in filtered if artist_lc in d.lower()]

    if album:
        album_lc = album.lower()
        result = []
        for d in filtered:
            parts = d.split("/")
            if len(parts) >= 2:
                rest_path = "/".join(parts[1:]).lower()
                if album_lc in rest_path:
                    result.append(d)
        filtered = result

    return {"directories": filtered}


@app.post("/directories/refresh")
def refresh_directories(current_user: dict = Depends(get_current_user)):
    _get_all_dirs_cached.cache_clear()
    _get_music_dirs_cached.cache_clear()
    return {"status": "ok"}


@app.post("/rename/episodes")
async def rename(
    series: str = Form(...),
    season: int = Form(...),
    directory: str = Form(...),
    dry_run: bool = Form(...),
    assign_seq: bool = Form(...),
    threshold: float = Form(...),
    lang: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    path = os.path.join(BASE_PATH, TVSHOW_FOLDER_NAME, directory)
    if not os.path.isdir(path):
        return {
            "success": False,
            "error": "Directory not found",
            "log": [],
            "directories": _get_all_dirs_cached(),
        }

    logs, error = rename_episodes(
        series=series,
        season=season,
        directory=path,
        lang=lang,
        dry_run=dry_run,
        threshold=threshold,
        assign_seq=assign_seq,
    )

    return {
        "success": error is None,
        "error": error,
        "log": logs,
        "directories": _get_all_dirs_cached(),
    }


@app.post("/rename/music")
async def rename_music_route(
    directory: str = Form(...),
    dry_run: bool = Form(...),
    current_user: dict = Depends(get_current_user),
):
    path = os.path.join(BASE_PATH, MUSIC_FOLDER_NAME, directory)
    if not os.path.isdir(path):
        return {
            "success": False,
            "error": "Directory not found",
            "log": [],
            "directories": _get_music_dirs_cached(),
        }

    logs, error = rename_music(directory=path, dry_run=dry_run)

    return {
        "success": error is None,
        "error": error,
        "log": logs,
        "directories": _get_music_dirs_cached(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", port=3332)
