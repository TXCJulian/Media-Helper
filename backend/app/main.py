from fastapi import FastAPI, Query, Form, UploadFile, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import asyncio
import mimetypes
import os
import logging
import queue
import threading
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.config import (
    BASE_PATH,
    TVSHOW_FOLDER_NAME,
    MUSIC_FOLDER_NAME,
    VALID_MUSIC_EXT,
    VALID_CUTTER_EXT,
    CUTTER_UPLOAD_DIR,
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
from app.cutter import (
    probe_file,
    generate_waveform,
    needs_transcoding,
    transcode_for_preview,
    cut_file,
    encode_file_id,
    decode_file_id,
)
from app.fs_utils import collision_safe_path

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


async def _cleanup_cutter_uploads():
    """Periodically delete uploaded files older than 1 hour."""
    while True:
        await asyncio.sleep(600)  # every 10 minutes
        try:
            if not os.path.isdir(CUTTER_UPLOAD_DIR):
                continue
            now = time.time()
            for entry in os.scandir(CUTTER_UPLOAD_DIR):
                if entry.is_file():
                    age = now - entry.stat().st_mtime
                    if age > 3600:  # older than 1 hour
                        os.remove(entry.path)
                        logger.info("Cleaned up expired upload: %s", entry.name)
        except Exception:
            logger.exception("Error during cutter upload cleanup")


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

    # Start cutter upload cleanup task only if cutter feature is enabled
    cleanup_task = None
    if "cutter" in ENABLED_FEATURES:
        cleanup_task = asyncio.create_task(_cleanup_cutter_uploads())

    yield

    # Shutdown
    if cleanup_task is not None:
        cleanup_task.cancel()
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


# ── Cutter Endpoints ────────────────────────────────────────────────────


def resolve_cutter_path(path: str, source: str) -> str:
    """Resolve and validate a cutter file path based on source type."""
    if source == "server":
        return validate_path(BASE_PATH, path)
    elif source == "upload":
        return validate_path(CUTTER_UPLOAD_DIR, path)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid source: '{source}'")


# Content-type mapping for common media extensions
_MEDIA_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".aiff": "audio/aiff",
    ".ac3": "audio/ac3",
    ".dts": "audio/vnd.dts",
}


@app.get("/cutter/files")
def list_cutter_files(
    directory: str = Query(..., max_length=500),
):
    require_feature("cutter")
    path = validate_path(BASE_PATH, directory)
    if not os.path.isdir(path):
        return {"files": []}

    files = []
    for entry in os.scandir(path):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in VALID_CUTTER_EXT:
                files.append({
                    "name": entry.name,
                    "size": entry.stat().st_size,
                    "extension": ext,
                })
    files.sort(key=lambda f: f["name"].lower())
    return {"files": files}


@app.get("/cutter/probe")
def cutter_probe(
    path: str = Query(..., max_length=500),
    source: str = Query(..., max_length=10),
):
    require_feature("cutter")
    resolved = resolve_cutter_path(path, source)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return probe_file(resolved)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cutter/waveform")
def cutter_waveform(
    path: str = Query(..., max_length=500),
    source: str = Query(..., max_length=10),
    peaks: int = Query(2000, ge=100, le=10000),
):
    require_feature("cutter")
    resolved = resolve_cutter_path(path, source)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return {"peaks": generate_waveform(resolved, num_peaks=peaks)}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cutter/stream/{file_id}")
def cutter_stream(file_id: str, request: Request):
    require_feature("cutter")
    try:
        source, path = decode_file_id(file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    resolved = resolve_cutter_path(path, source)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    # Check if transcoding is needed
    try:
        probe = probe_file(resolved)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if needs_transcoding(probe.get("audio_codec", "unknown")):
        proc = transcode_for_preview(resolved)

        def stream_transcode():
            try:
                stdout = proc.stdout
                if stdout is None:
                    raise HTTPException(
                        status_code=500, detail="Transcoding process has no stdout"
                    )
                while True:
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.stdout:
                    proc.stdout.close()
                proc.wait()

        return StreamingResponse(
            stream_transcode(),
            media_type="video/mp4",
            headers={"Accept-Ranges": "none"},
        )

    # Serve raw file with HTTP Range support
    file_size = os.path.getsize(resolved)
    ext = os.path.splitext(resolved)[1].lower()
    content_type = _MEDIA_CONTENT_TYPES.get(
        ext,
        mimetypes.guess_type(resolved)[0] or "application/octet-stream",
    )

    range_header = request.headers.get("range")
    if range_header:
        # Parse Range: bytes=X-Y
        try:
            range_match = range_header.strip().replace("bytes=", "")
            parts = range_match.split("-", 1)
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            end = min(end, file_size - 1)
        except (ValueError, IndexError):
            raise HTTPException(status_code=416, detail="Malformed Range header")

        if start >= file_size or start > end:
            raise HTTPException(status_code=416, detail="Range not satisfiable")

        content_length = end - start + 1

        def range_generator():
            with open(resolved, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            range_generator(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
            },
        )

    # Full file response
    def file_generator():
        with open(resolved, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        file_generator(),
        media_type=content_type,
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


@app.post("/cutter/upload")
async def cutter_upload(file: UploadFile):
    require_feature("cutter")

    os.makedirs(CUTTER_UPLOAD_DIR, exist_ok=True)
    filename = file.filename or "unnamed"

    # Validate file extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in VALID_CUTTER_EXT:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file extension '{ext}'. Allowed: {', '.join(sorted(VALID_CUTTER_EXT))}",
        )

    dest = collision_safe_path(os.path.join(CUTTER_UPLOAD_DIR, filename))

    max_upload_size = 2 * 1024 * 1024 * 1024  # 2 GB
    try:
        bytes_written = 0
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_upload_size:
                    raise HTTPException(
                        status_code=413, detail="File exceeds 2 GB size limit"
                    )
                f.write(chunk)
    except HTTPException:
        # Clean up partial file on failure, re-raise HTTP exceptions as-is
        if os.path.exists(dest):
            os.remove(dest)
        raise
    except Exception as e:
        # Clean up partial file on failure
        if os.path.exists(dest):
            os.remove(dest)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    saved_name = os.path.basename(dest)
    relative_path = os.path.relpath(dest, CUTTER_UPLOAD_DIR)

    try:
        probe = probe_file(dest)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Probe failed: {e}")

    return {
        "file_id": encode_file_id("upload", relative_path),
        "filename": saved_name,
        "probe": probe,
    }


@app.post("/cutter/cut")
def cutter_cut(
    path: str = Form(..., max_length=500),
    source: str = Form(..., max_length=10),
    in_point: float = Form(..., ge=0.0),
    out_point: float = Form(..., ge=0.0),
    output_name: str = Form("", max_length=300),
    stream_copy: bool = Form(True),
    codec: str = Form("", max_length=20),
    container: str = Form("", max_length=20),
):
    require_feature("cutter")
    resolved = resolve_cutter_path(path, source)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    if out_point <= in_point:
        raise HTTPException(
            status_code=422, detail="out_point must be greater than in_point"
        )

    # Determine output path
    src_dir = os.path.dirname(resolved)
    if output_name:
        output_name = os.path.basename(output_name)
        output_path = os.path.join(src_dir, output_name)
    else:
        output_path = resolved

    msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    cancel_event = threading.Event()

    def run_cut():
        try:
            if cancel_event.is_set():
                msg_queue.put(("error_msg", "Cut cancelled before start"))
                msg_queue.put(("done", "Cut cancelled"))
                return

            def progress_cb(msg: str):
                msg_queue.put(("progress", msg))

            final_path = cut_file(
                filepath=resolved,
                in_point=in_point,
                out_point=out_point,
                output_path=output_path,
                stream_copy=stream_copy,
                codec=codec or None,
                container=container or None,
                progress_cb=progress_cb,
            )
            msg_queue.put(("done", f"Output: {os.path.basename(final_path)}"))
        except Exception as e:
            logger.exception("Cut failed")
            msg_queue.put(("error_msg", str(e)))
            msg_queue.put(("done", "Cut failed"))

    thread = threading.Thread(target=run_cut, daemon=True)
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
