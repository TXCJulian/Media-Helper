from fastapi import FastAPI, Query, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import os
import logging
import queue
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.config import (
    BASE_PATH,
    TVSHOW_FOLDER_NAME,
    MUSIC_FOLDER_NAME,
    VALID_MUSIC_EXT,
    TRANSCRIBER_URL,
    ALLOWED_ORIGINS,
    ENABLED_FEATURES,
)
from app.rename_episodes import rename_episodes
from app.rename_music import rename_music, load_audio_file, get_first_tag_value
from app.get_dirs import _get_all_dirs_cached, _get_music_dirs_cached
from app.transcribe_lyrics import (
    check_transcriber_health,
    get_music_files,
    get_file_lyrics_status,
    check_existing_lyrics,
    transcribe_file,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def require_feature(name: str):
    """Raise 404 if feature is not enabled."""
    if name not in ENABLED_FEATURES:
        raise HTTPException(status_code=404, detail=f"Feature '{name}' is not enabled")


def validate_path(base: str, user_input: str) -> str:
    """Validate that resolved path stays within base directory."""
    resolved = os.path.realpath(os.path.join(base, user_input))
    base_resolved = os.path.realpath(base)
    if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
        raise HTTPException(status_code=400, detail="Invalid directory path")
    return resolved


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
    handler = DirChangeHandler()
    if os.path.isdir(BASE_PATH):
        _observer = Observer()
        _observer.schedule(handler, BASE_PATH, recursive=True)
        _observer.start()
        logger.info("File watcher started on '%s'", BASE_PATH)
    else:
        logger.warning(
            "BASE_PATH '%s' does not exist; file watcher not started.", BASE_PATH
        )
        _observer = None

    yield

    # Shutdown
    if _observer:
        _observer.stop()
        _observer.join()
        logger.info("File watcher stopped.")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "transcriber": bool(TRANSCRIBER_URL),
        "features": sorted(ENABLED_FEATURES),
    }


@app.get("/config")
def get_config():
    return {"features": sorted(ENABLED_FEATURES)}


@app.get("/directories/tvshows")
def list_directories(
    series: str | None = Query(None, description="Series filter", max_length=200),
    season: int | None = Query(None, description="Season number", ge=0, le=100),
):
    require_feature("episodes")
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
    artist: str | None = Query(None, description="Artist filter", max_length=200),
    album: str | None = Query(None, description="Album filter", max_length=200),
):
    if "music" not in ENABLED_FEATURES and "lyrics" not in ENABLED_FEATURES:
        raise HTTPException(status_code=404, detail="No music features enabled")
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
def refresh_directories():
    _get_all_dirs_cached.cache_clear()
    _get_music_dirs_cached.cache_clear()
    logger.info("Directory cache refreshed manually.")
    return {"status": "ok"}


@app.post("/rename/episodes")
async def rename(
    series: str = Form(..., max_length=200),
    season: int = Form(..., ge=0, le=100),
    directory: str = Form(..., max_length=500),
    dry_run: bool = Form(...),
    assign_seq: bool = Form(...),
    threshold: float = Form(..., ge=0.0, le=1.0),
    lang: str = Form(..., max_length=5),
):
    require_feature("episodes")
    tvshow_base = os.path.join(BASE_PATH, TVSHOW_FOLDER_NAME)
    path = validate_path(tvshow_base, directory)
    if not os.path.isdir(path):
        return {
            "success": False,
            "error": "Directory not found",
            "log": [],
            "directories": _get_all_dirs_cached(),
        }

    logger.info(
        "Renaming episodes: series=%s, season=%d, dir=%s, dry_run=%s",
        series,
        season,
        directory,
        dry_run,
    )

    logs, error = rename_episodes(
        series=series,
        season=season,
        directory=path,
        lang=lang,
        dry_run=dry_run,
        threshold=threshold,
        assign_seq=assign_seq,
    )

    if error:
        logger.error("Episode rename failed: %s", error)

    return {
        "success": error is None,
        "error": error,
        "log": logs,
        "directories": _get_all_dirs_cached(),
    }


@app.post("/rename/music")
async def rename_music_route(
    directory: str = Form(..., max_length=500),
    dry_run: bool = Form(...),
):
    require_feature("music")
    music_base = os.path.join(BASE_PATH, MUSIC_FOLDER_NAME)
    path = validate_path(music_base, directory)
    if not os.path.isdir(path):
        return {
            "success": False,
            "error": "Directory not found",
            "log": [],
            "directories": _get_music_dirs_cached(),
        }

    logger.info("Renaming music: dir=%s, dry_run=%s", directory, dry_run)

    logs, error = rename_music(directory=path, dry_run=dry_run)

    if error:
        logger.error("Music rename failed: %s", error)

    return {
        "success": error is None,
        "error": error,
        "log": logs,
        "directories": _get_music_dirs_cached(),
    }


# ── Transcriber Endpoints ─────────────────────────────────────────────────


@app.get("/transcribe/health")
def transcriber_health():
    require_feature("lyrics")
    if not TRANSCRIBER_URL:
        return {"status": "not_configured", "error": "TRANSCRIBER_URL not set"}
    return check_transcriber_health(TRANSCRIBER_URL)


@app.get("/transcribe/files")
def list_transcribable_files(
    directory: str = Query(..., max_length=500),
):
    require_feature("lyrics")
    music_base = os.path.join(BASE_PATH, MUSIC_FOLDER_NAME)
    path = validate_path(music_base, directory)
    if not os.path.isdir(path):
        return {"files": [], "error": "Directory not found"}
    music_files = get_music_files(path, VALID_MUSIC_EXT)
    return {"files": [get_file_lyrics_status(f) for f in music_files]}


@app.post("/transcribe/start")
def start_transcription(
    directory: str = Form(..., max_length=500),
    files: str = Form("", max_length=5000),
    output_format: str = Form("lrc", max_length=5),
    skip_existing: bool = Form(True),
    language: str = Form("", max_length=10),
    no_separation: bool = Form(False),
    no_correction: bool = Form(False),
):
    require_feature("lyrics")
    if output_format not in ("lrc", "txt", "all"):
        raise HTTPException(
            status_code=422,
            detail="Invalid output format. Must be 'lrc', 'txt', or 'all'.",
        )
    if not TRANSCRIBER_URL:
        return {"error": "TRANSCRIBER_URL not set"}

    music_base = os.path.join(BASE_PATH, MUSIC_FOLDER_NAME)
    path = validate_path(music_base, directory)
    if not os.path.isdir(path):
        return {"error": "Directory not found"}

    if files:
        # Sanitize filenames to basenames only (prevent path traversal via filenames)
        selected = [os.path.join(path, os.path.basename(f)) for f in files.split(",")]
        selected = [f for f in selected if os.path.isfile(f)]
    else:
        selected = get_music_files(path, VALID_MUSIC_EXT)

    if not selected:
        return {"error": "No music files found"}

    logger.info(
        "Starting transcription: dir=%s, files=%d, format=%s",
        directory,
        len(selected),
        output_format,
    )

    msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    cancel_event = threading.Event()

    def run_batch():
        total = len(selected)
        completed = 0
        errors = 0

        for i, filepath in enumerate(selected, 1):
            if cancel_event.is_set():
                msg_queue.put(("done", f"Cancelled. Completed: {completed}/{total}"))
                return

            filename = os.path.basename(filepath)

            if i > 1:
                msg_queue.put(("progress", ""))  # blank line between songs

            # Check if lyrics already exist
            if skip_existing:
                effective_format = check_existing_lyrics(filepath, output_format)
                if effective_format is None:
                    msg_queue.put(
                        ("progress", f"[SKIP]\t\t\t{filename} — lyrics already exist")
                    )
                    continue
            else:
                effective_format = output_format

            # Extract metadata for Genius correction
            file_artist = None
            file_title = None
            if not no_correction:
                audio = load_audio_file(filepath)
                if audio:
                    file_artist = get_first_tag_value(audio, "artist")
                    file_title = get_first_tag_value(audio, "title")

            def progress_cb(msg):
                msg_queue.put(("progress", msg))

            logs, error = transcribe_file(
                filepath=filepath,
                transcriber_url=TRANSCRIBER_URL,
                output_format=effective_format,
                no_separation=no_separation,
                language=language or None,
                artist=file_artist,
                title=file_title,
                no_correction=no_correction,
                progress_callback=progress_cb,
            )

            if error:
                errors += 1
                msg_queue.put(("error_msg", error))
            else:
                completed += 1

        summary = f"Completed: {completed}/{total}"
        if errors:
            summary += f", Errors: {errors}"
        msg_queue.put(("done", summary))

    thread = threading.Thread(target=run_batch, daemon=True)
    thread.start()

    def event_generator():
        try:
            while True:
                try:
                    event_type, data = msg_queue.get(timeout=30)
                    yield f"event: {event_type}\ndata: {data}\n\n"
                    if event_type == "done":
                        break
                except queue.Empty:
                    # Heartbeat to keep connection alive
                    yield "event: progress\ndata: heartbeat\n\n"
        finally:
            cancel_event.set()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
