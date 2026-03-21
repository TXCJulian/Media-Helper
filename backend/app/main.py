from fastapi import FastAPI, Query, Form, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import asyncio
import json
import mimetypes
import os
import logging
import queue
import re
import shutil
import threading
from urllib.parse import unquote
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.config import (
    BASE_PATHS,
    BASE_PATH_LABELS,
    resolve_base,
    TVSHOW_FOLDER_NAME,
    MUSIC_FOLDER_NAME,
    VALID_MUSIC_EXT,
    VALID_CUTTER_EXT,
    CUTTER_JOBS_DIR,
    CUTTER_MAX_DIRECT_REMUX_BYTES,
    TRANSCRIBER_URL,
    ALLOWED_ORIGINS,
    ENABLED_FEATURES,
    ENABLED_FEATURES_SET,
)
from app.rename_episodes import rename_episodes
from app.rename_music import rename_music, load_audio_file, get_first_tag_value
from app.get_dirs import (
    _get_all_dirs_cached,
    _get_music_dirs_cached,
    _get_cutter_dirs_cached,
)
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
    generate_thumbnail_strip,
    generate_thumbnail_strip_cached,
    needs_transcoding,
    get_preview_path_if_ready,
    get_preview_status,
    start_background_transcode,
    get_track_preview,
    get_audio_track_preview,
    get_track_remux,
    cut_file,
    encode_file_id,
    decode_file_id,
    create_job,
    get_job_dir,
    load_job_metadata,
    save_job_metadata,
    list_jobs,
    delete_job,
    cleanup_old_jobs,
    get_job_meta_lock,
    start_background_audio_transcode,
    get_audio_transcode_status,
    wait_for_audio_transcode,
    transcode_audio_track_from_source,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def require_feature(name: str):
    """Raise 404 if feature is not enabled."""
    if name not in ENABLED_FEATURES_SET:
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
            _get_cutter_dirs_cached.cache_clear()

    def on_deleted(self, event):
        if event.is_directory:
            _get_all_dirs_cached.cache_clear()
            _get_music_dirs_cached.cache_clear()
            _get_cutter_dirs_cached.cache_clear()

    def on_moved(self, event):
        if event.is_directory:
            _get_all_dirs_cached.cache_clear()
            _get_music_dirs_cached.cache_clear()
            _get_cutter_dirs_cached.cache_clear()


# Global observer instances
_observers: list = []


async def _cleanup_cutter_jobs():
    """Periodically delete expired jobs."""
    while True:
        await asyncio.sleep(600)  # every 10 minutes
        try:
            cleanup_old_jobs()
        except Exception:
            logger.exception("Error during cutter job cleanup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan with startup and shutdown events."""
    global _observers

    # Startup
    handler = DirChangeHandler()
    _observers = []
    for bp in BASE_PATHS:
        if os.path.isdir(bp):
            obs = Observer()
            obs.schedule(handler, bp, recursive=True)
            obs.start()
            _observers.append(obs)
            logger.info("Watching %s for filesystem changes", bp)
        else:
            logger.warning("Base path does not exist, skipping watch: %s", bp)

    # Start cutter upload cleanup task only if cutter feature is enabled
    cleanup_task = None
    if "cutter" in ENABLED_FEATURES_SET:
        os.makedirs(CUTTER_JOBS_DIR, exist_ok=True)
        from app.cutter import migrate_jobs

        migrate_jobs()
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            logger.error("Cutter feature requires ffmpeg and ffprobe on PATH")
        else:
            from app.hwaccel import detect_gpu
            detect_gpu()
        cleanup_task = asyncio.create_task(_cleanup_cutter_jobs())

    yield

    # Shutdown
    if cleanup_task is not None:
        cleanup_task.cancel()
    for obs in _observers:
        obs.stop()
    for obs in _observers:
        obs.join()
    if _observers:
        logger.info("File watchers stopped.")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "transcriber": bool(TRANSCRIBER_URL),
        "features": ENABLED_FEATURES,
        "base_paths": list(BASE_PATH_LABELS.keys()),
    }


@app.get("/config")
def get_config():
    return {
        "features": ENABLED_FEATURES,
        "base_paths": list(BASE_PATH_LABELS.keys()),
    }


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
        filtered = [d for d in filtered if series_lc in d["path"].lower()]

    # nach Staffel filtern
    if season is not None:
        season_str = f"{season:02d}"
        pattern = f"/season {season_str}"
        filtered = [d for d in filtered if d["path"].lower().endswith(pattern)]

    return {"directories": filtered}


@app.get("/directories/music")
def list_music_directories(
    artist: str | None = Query(None, description="Artist filter", max_length=200),
    album: str | None = Query(None, description="Album filter", max_length=200),
):
    if "music" not in ENABLED_FEATURES_SET and "lyrics" not in ENABLED_FEATURES_SET:
        raise HTTPException(status_code=404, detail="No music features enabled")
    all_dirs = _get_music_dirs_cached()

    filtered = all_dirs
    if artist:
        artist_lc = artist.lower()
        filtered = [d for d in filtered if artist_lc in d["path"].lower()]

    if album:
        album_lc = album.lower()
        result = []
        for d in filtered:
            parts = d["path"].split("/")
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
    _get_cutter_dirs_cached.cache_clear()
    logger.info("Directory cache refreshed manually.")
    return {"status": "ok"}


@app.get("/directories/media")
def list_media_directories(
    search: str | None = Query(None, description="Text filter", max_length=200),
):
    require_feature("cutter")
    all_dirs = _get_cutter_dirs_cached()

    filtered = all_dirs
    if search:
        search_lc = search.lower()
        filtered = [d for d in filtered if search_lc in d["path"].lower()]

    return {"directories": filtered}


@app.post("/rename/episodes")
async def rename(
    series: str = Form(..., max_length=200),
    season: int = Form(..., ge=0, le=100),
    directory: str = Form(..., max_length=500),
    dry_run: bool = Form(...),
    assign_seq: bool = Form(...),
    threshold: float = Form(..., ge=0.0, le=1.0),
    lang: str = Form(..., max_length=5),
    base: str = Form(..., max_length=200),
):
    require_feature("episodes")
    try:
        base_path = resolve_base(base)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown base: '{base}'")
    tvshow_base = os.path.join(base_path, TVSHOW_FOLDER_NAME)
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
    base: str = Form(..., max_length=200),
):
    require_feature("music")
    try:
        base_path = resolve_base(base)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown base: '{base}'")
    music_base = os.path.join(base_path, MUSIC_FOLDER_NAME)
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
    base: str = Query(..., max_length=200),
):
    require_feature("lyrics")
    try:
        base_path = resolve_base(base)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown base: '{base}'")
    music_base = os.path.join(base_path, MUSIC_FOLDER_NAME)
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
    base: str = Form(..., max_length=200),
):
    require_feature("lyrics")
    if output_format not in ("lrc", "txt", "all"):
        raise HTTPException(
            status_code=422,
            detail="Invalid output format. Must be 'lrc', 'txt', or 'all'.",
        )
    if not TRANSCRIBER_URL:
        return {"error": "TRANSCRIBER_URL not set"}

    try:
        base_path = resolve_base(base)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown base: '{base}'")
    music_base = os.path.join(base_path, MUSIC_FOLDER_NAME)
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
# Workflow overview:
# 1) Probe/waveform/thumbnail endpoints inspect a selected source file.
# 2) /cutter/stream serves either original media or a browser-safe preview.
# 3) /cutter/cut starts an SSE-driven ffmpeg cut operation into job output.
# 4) Job metadata tracks readiness, errors, and downloadable outputs.


def resolve_cutter_path(
    path: str, source: str, job_id: str = "", base_label: str = ""
) -> str:
    """Resolve and validate a cutter file path based on source type."""
    if source == "server":
        try:
            base_path = resolve_base(base_label)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Unknown base: '{base_label}'"
            )
        return validate_path(base_path, path)
    elif source == "upload":
        if not job_id:
            raise HTTPException(
                status_code=400, detail="job_id required for upload source"
            )
        job_dir = get_job_dir(job_id)
        input_dir = os.path.join(job_dir, "input")
        return validate_path(input_dir, path)
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
    base: str = Query(..., max_length=200),
):
    require_feature("cutter")
    try:
        base_path = resolve_base(base)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown base: '{base}'")
    path = validate_path(base_path, directory)
    if not os.path.isdir(path):
        return {"files": []}

    files = []
    for entry in os.scandir(path):
        try:
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in VALID_CUTTER_EXT:
                files.append(
                    {
                        "name": entry.name,
                        "size": entry.stat().st_size,
                        "extension": ext,
                    }
                )
        except OSError:
            # File may disappear/change between scandir and stat on network shares.
            continue
    files.sort(key=lambda f: f["name"].lower())
    return {"files": files}


@app.get("/cutter/probe")
def cutter_probe(
    path: str = Query(..., max_length=500),
    source: str = Query(..., max_length=10),
    job_id: str = Query("", max_length=50),
    base: str = Query("", max_length=200),
):
    require_feature("cutter")
    resolved = resolve_cutter_path(path, source, job_id, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        info = probe_file(resolved)
        info["needs_transcoding"] = needs_transcoding(
            info.get("audio_codec", "unknown"),
            resolved,
            info.get("video_codec", "") or "",
        )

        if job_id:
            with get_job_meta_lock(job_id):
                meta = load_job_metadata(job_id)
                if meta:
                    meta["browser_ready"] = not info["needs_transcoding"]
                    save_job_metadata(job_id, meta)

        return info
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cutter/waveform")
def cutter_waveform(
    path: str = Query(..., max_length=500),
    source: str = Query(..., max_length=10),
    peaks: int = Query(2000, ge=100, le=10000),
    job_id: str = Query("", max_length=50),
    base: str = Query("", max_length=200),
):
    require_feature("cutter")
    resolved = resolve_cutter_path(path, source, job_id, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return {"peaks": generate_waveform(resolved, num_peaks=peaks)}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cutter/thumbnails")
def cutter_thumbnails(
    path: str = Query(..., max_length=500),
    source: str = Query(..., max_length=10),
    count: int = Query(30, ge=5, le=50),
    job_id: str = Query("", max_length=50),
    base: str = Query("", max_length=200),
):
    require_feature("cutter")
    from fastapi.responses import Response

    resolved = resolve_cutter_path(path, source, job_id, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        if job_id:
            try:
                jpeg_bytes = generate_thumbnail_strip_cached(
                    resolved, count=count, job_id=job_id
                )
            except ValueError:
                # Optional cache only; malformed/unknown job ids still get a thumbnail.
                jpeg_bytes = generate_thumbnail_strip(resolved, count=count)
        else:
            jpeg_bytes = generate_thumbnail_strip(resolved, count=count)
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cutter/stream/{file_id}")
def cutter_stream(
    file_id: str,
    request: Request,
    audio_stream: int | None = Query(None),
    transcode: bool = Query(False),
    audio_only: bool = Query(False),
    transcode_audio_only: bool = Query(False),
):
    require_feature("cutter")
    try:
        source, job_id, base, path = decode_file_id(file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    resolved = resolve_cutter_path(path, source, job_id, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    # Check if transcoding is needed
    try:
        probe = probe_file(resolved)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if audio_stream is not None:
        audio_indexes = {s.get("index") for s in probe.get("audio_streams", [])}
        if audio_stream not in audio_indexes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid audio stream index {audio_stream}. "
                    f"Available indexes: {sorted(audio_indexes)}"
                ),
            )

    needs_tx = needs_transcoding(
        probe.get("audio_codec", "unknown"),
        resolved,
        probe.get("video_codec", "") or "",
    )
    source_file_size = os.path.getsize(resolved)
    audio_streams = probe.get("audio_streams", [])
    default_audio_index = audio_streams[0].get("index") if audio_streams else None

    # Reject conflicting parameters
    if transcode_audio_only and (transcode or audio_only):
        raise HTTPException(
            status_code=400,
            detail="transcode_audio_only cannot be combined with transcode or audio_only",
        )

    if transcode_audio_only:
        if audio_stream is None:
            raise HTTPException(
                status_code=400,
                detail="audio_stream required for audio-only transcode",
            )
        if not job_id:
            raise HTTPException(
                status_code=400,
                detail="job_id required for audio-only transcode",
            )
        start_background_audio_transcode(resolved, audio_stream, job_id)
        audio_path = wait_for_audio_transcode(
            resolved, job_id, audio_stream, timeout=120
        )
        if not audio_path:
            try:
                audio_path = transcode_audio_track_from_source(
                    resolved, audio_stream, job_id
                )
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))
        resolved = audio_path
    elif transcode and needs_tx:
        if not job_id:
            raise HTTPException(
                status_code=400, detail="job_id required for transcoded preview"
            )

        start_background_transcode(resolved, job_id)
        status = get_preview_status(resolved, job_id)
        if status.get("state") == "error":
            logger.error("Stream: cached preview error state: %s", status.get("message"))
            raise HTTPException(
                status_code=500,
                detail=status.get("message") or "Preview transcode failed",
            )

        # Serve the master preview if ready; otherwise return 409 so the
        # frontend can poll /preview-status and retry when ready.
        master_path = get_preview_path_if_ready(resolved, job_id)
        if not master_path:
            raise HTTPException(
                status_code=409,
                detail="Preview not ready yet — poll /cutter/preview-status and retry",
            )
        # If a specific audio track is requested, extract it from the master
        if audio_stream is not None:
            try:
                if audio_only:
                    logger.info("Stream: extracting audio-only track %d from master", audio_stream)
                    resolved = get_audio_track_preview(
                        master_path, audio_stream, resolved, job_id
                    )
                else:
                    logger.info("Stream: extracting full track %d from master %s", audio_stream, master_path)
                    resolved = get_track_preview(
                        master_path, audio_stream, resolved, job_id
                    )
            except RuntimeError as e:
                logger.error("Stream: track extraction failed: %s", e)
                raise HTTPException(status_code=500, detail=str(e))
        else:
            resolved = master_path
    elif (
        audio_stream is not None
        and job_id
        and not needs_tx
        and audio_stream != default_audio_index
        and source_file_size <= CUTTER_MAX_DIRECT_REMUX_BYTES
    ):
        # Only remux for browser-native containers (MP4, WebM, etc.) where
        # non-default track isolation is meaningful and expected to be quick.
        # Files that need transcoding (MKV, AVI, etc.) or large originals are
        # served raw so preview startup doesn't block on a full-file remux.
        try:
            resolved = get_track_remux(resolved, audio_stream, job_id)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

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
        _range_re = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not _range_re:
            raise HTTPException(status_code=416, detail="Malformed Range header")
        try:
            start = int(_range_re.group(1)) if _range_re.group(1) else 0
            end = int(_range_re.group(2)) if _range_re.group(2) else file_size - 1
            end = min(end, file_size - 1)
        except ValueError:
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


@app.get("/cutter/preview-status/{file_id}")
def cutter_preview_status(
    file_id: str,
    audio_transcode_stream: int | None = Query(None),
):
    require_feature("cutter")
    try:
        source, job_id, base, path = decode_file_id(file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    resolved = resolve_cutter_path(path, source, job_id, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    # Audio-only transcode status — uses separate key, bypasses master preview check
    if audio_transcode_stream is not None:
        if not job_id:
            raise HTTPException(
                status_code=400,
                detail="job_id required for audio transcode status",
            )
        return get_audio_transcode_status(resolved, job_id, audio_transcode_stream)

    def _done_status() -> dict:
        return {
            "state": "done",
            "ready": True,
            "percent": 100.0,
            "eta_seconds": 0.0,
            "elapsed_seconds": 0.0,
            "message": "",
        }

    # Fast path for active jobs: avoid expensive ffprobe on every poll.
    if job_id:
        status = get_preview_status(resolved, job_id)
        if status.get("state") in {"running", "error"}:
            return status
        if status.get("state") == "done" and status.get("ready"):
            return status

        meta = load_job_metadata(job_id)
        if meta and (meta.get("browser_ready") or meta.get("preview_transcoded")):
            return _done_status()

    try:
        probe = probe_file(resolved)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not needs_transcoding(
        probe.get("audio_codec", "unknown"),
        resolved,
        probe.get("video_codec", "") or "",
    ):
        return _done_status()

    if not job_id:
        raise HTTPException(
            status_code=400, detail="job_id required for transcoded preview"
        )

    start_background_transcode(resolved, job_id)
    return get_preview_status(resolved, job_id)


@app.post("/cutter/upload")
async def cutter_upload(request: Request):
    require_feature("cutter")

    content_length = request.headers.get("content-length")
    max_upload_size = 50 * 1024 * 1024 * 1024  # 50 GB
    if content_length:
        try:
            if int(content_length) > max_upload_size:
                raise HTTPException(
                    status_code=413, detail="File exceeds 50 GB size limit"
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")

    raw_name = request.headers.get("x-file-name") or "unnamed"
    raw_name = unquote(raw_name)

    # Sanitize filename: strip path components and non-printable characters
    filename = os.path.basename(raw_name)
    filename = "".join(c for c in filename if c.isprintable())
    if not filename or filename in (".", ".."):
        filename = "unnamed"

    # Validate file extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in VALID_CUTTER_EXT:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file extension '{ext}'. Allowed: {', '.join(sorted(VALID_CUTTER_EXT))}",
        )

    # Create a job for this upload — mark as uploading until the stream completes
    job_id = create_job("upload", "", filename, initial_status="uploading")
    job_dir = get_job_dir(job_id)
    input_dir = os.path.join(job_dir, "input")
    dest = os.path.join(input_dir, filename)

    try:
        bytes_written = 0
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if bytes_written > max_upload_size:
                    raise HTTPException(
                        status_code=413, detail="File exceeds 50 GB size limit"
                    )
                f.write(chunk)
        if bytes_written == 0:
            raise HTTPException(status_code=422, detail="No file data received")
    except HTTPException:
        delete_job(job_id)
        raise
    except Exception as e:
        delete_job(job_id)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    with get_job_meta_lock(job_id):
        meta = load_job_metadata(job_id)
        if meta:
            meta["status"] = "ready"
            save_job_metadata(job_id, meta)

    return {
        "job_id": job_id,
        "file_id": encode_file_id("upload", filename, job_id, base=""),
        "filename": filename,
    }


@app.post("/cutter/jobs")
def cutter_create_job(
    path: str = Form(..., max_length=500),
    source: str = Form("server", max_length=10),
    base: str = Form("", max_length=200),
):
    """Create a job for a server-side file (no file copy, metadata only)."""
    require_feature("cutter")
    resolved = resolve_cutter_path(path, source, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    filename = os.path.basename(resolved)
    job_id = create_job(source, path, filename, base=base)

    try:
        probe = probe_file(resolved)
        browser_ready = not needs_transcoding(
            probe.get("audio_codec", "unknown"),
            resolved,
            probe.get("video_codec", "") or "",
        )
        with get_job_meta_lock(job_id):
            meta = load_job_metadata(job_id)
            if meta:
                meta["browser_ready"] = browser_ready
                save_job_metadata(job_id, meta)
    except RuntimeError:
        logger.warning("Could not evaluate browser compatibility for job %s", job_id)

    return {"job_id": job_id}


@app.get("/cutter/jobs")
def cutter_list_jobs():
    """List all active jobs."""
    require_feature("cutter")
    return {"jobs": list_jobs()}


@app.get("/cutter/jobs/{job_id}")
def cutter_get_job(job_id: str):
    """Get single job details."""
    require_feature("cutter")
    meta = load_job_metadata(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")
    return meta


@app.delete("/cutter/jobs/{job_id}")
def cutter_delete_job(job_id: str):
    """Delete a job and all its files."""
    require_feature("cutter")
    try:
        delete_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "deleted"}


@app.get("/cutter/jobs/{job_id}/download/{filename}")
def cutter_download(job_id: str, filename: str):
    """Download an output file from a job."""
    require_feature("cutter")
    from fastapi.responses import FileResponse

    try:
        job_dir = get_job_dir(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Sanitize filename to prevent traversal
    safe_name = os.path.basename(filename)
    file_path = os.path.join(job_dir, "output", safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        file_path,
        filename=safe_name,
        media_type="application/octet-stream",
    )


@app.post("/cutter/jobs/{job_id}/save/{filename}")
def cutter_save_to_source(job_id: str, filename: str):
    """Copy an output file back to the original file's directory (server sources only)."""
    require_feature("cutter")
    from app.fs_utils import collision_safe_path

    meta = load_job_metadata(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")

    if meta.get("source") != "server":
        raise HTTPException(
            status_code=400, detail="Save to Source only available for server files"
        )

    # Locate the output file in the job directory
    try:
        job_dir = get_job_dir(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    safe_name = os.path.basename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext and ext not in VALID_CUTTER_EXT:
        raise HTTPException(status_code=422, detail=f"Invalid output extension: {ext}")
    src_file = os.path.join(job_dir, "output", safe_name)
    if not os.path.isfile(src_file):
        raise HTTPException(status_code=404, detail="Output file not found")

    # Resolve the original file's directory
    original_path = meta.get("original_path", "")
    base_label = meta.get("base", "")
    try:
        base_path = resolve_base(base_label)
    except ValueError:
        raise HTTPException(status_code=400, detail="Cannot resolve original file path")
    try:
        resolved_original = validate_path(base_path, original_path)
    except HTTPException:
        raise HTTPException(status_code=400, detail="Cannot resolve original file path")

    dest_dir = os.path.dirname(resolved_original)
    if not os.path.isdir(dest_dir):
        raise HTTPException(
            status_code=400, detail="Original directory no longer exists"
        )

    dest_path = collision_safe_path(os.path.join(dest_dir, safe_name))
    try:
        shutil.copy2(src_file, dest_path)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to save output file: {exc}"
        ) from exc

    return {"status": "saved", "filename": os.path.basename(dest_path)}


@app.post("/cutter/cut")
def cutter_cut(
    path: str = Form(..., max_length=500),
    source: str = Form(..., max_length=10),
    job_id: str = Form(..., max_length=50),
    in_point: float = Form(..., ge=0.0),
    out_point: float = Form(..., ge=0.0),
    output_name: str = Form("", max_length=300),
    stream_copy: bool = Form(True),
    codec: str = Form("", max_length=20),
    container: str = Form("", max_length=20),
    audio_tracks_json: str = Form("[]", alias="audio_tracks", max_length=5000),
    keep_quality: bool = Form(False),
    base: str = Form("", max_length=200),
):
    require_feature("cutter")

    # Validate job
    try:
        job_dir = get_job_dir(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    resolved = resolve_cutter_path(path, source, job_id, base_label=base)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    if out_point <= in_point:
        raise HTTPException(
            status_code=422, detail="out_point must be greater than in_point"
        )

    valid_codecs = {
        "copy",
        "aac",
        "flac",
        "opus",
        "ac3",
        "mp3",
        "vorbis",
        "pcm_s16le",
        "pcm_s24le",
        "libx264",
        "libx265",
        "libvpx-vp9",
        "libaom-av1",
    }
    valid_audio_codecs = {
        "copy",
        "aac",
        "flac",
        "opus",
        "ac3",
        "mp3",
        "vorbis",
        "pcm_s16le",
        "pcm_s24le",
    }
    valid_containers = {
        "",
        "mp4",
        "mkv",
        "mov",
        "avi",
        "webm",
        "ogg",
        "mp3",
        "flac",
        "wav",
        "aac",
        "ac3",
        "opus",
        "m4a",
        "mka",
        "ts",
        "mts",
    }

    if codec and codec not in valid_codecs:
        raise HTTPException(status_code=422, detail=f"Invalid codec: {codec}")
    if container and container not in valid_containers:
        raise HTTPException(status_code=422, detail=f"Invalid container: {container}")

    try:
        audio_tracks_parsed = json.loads(audio_tracks_json)
        if not isinstance(audio_tracks_parsed, list):
            raise ValueError("audio_tracks must be a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid audio_tracks: {exc}")

    valid_modes = {"passthru", "reencode", "remove"}
    for track in audio_tracks_parsed:
        if not isinstance(track, dict):
            raise HTTPException(
                status_code=422, detail="Each audio track must be an object"
            )
        if track.get("mode") not in valid_modes:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid audio track mode: {track.get('mode')}",
            )
        if track["mode"] == "reencode":
            track_codec = track.get("codec", "")
            if track_codec and track_codec not in valid_audio_codecs:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid audio track codec: {track_codec}",
                )

    # Validate against file duration
    try:
        file_info = probe_file(resolved)
        file_duration = file_info.get("duration", 0)
        if file_duration > 0 and out_point > file_duration + 0.5:
            raise HTTPException(
                status_code=422,
                detail=f"out_point ({out_point:.2f}s) exceeds file duration ({file_duration:.2f}s)",
            )
    except RuntimeError as exc:
        logger.warning("Could not probe source for cutter validation: %s", exc)
        file_info = {}

    source_video_bitrate = file_info.get("video_bitrate") if keep_quality else None
    source_audio_bitrates = {}
    probe_audio_streams = file_info.get("audio_streams", [])
    if keep_quality:
        for stream in probe_audio_streams:
            source_audio_bitrates[stream["index"]] = stream.get("bit_rate", 0)

    # Determine output filename — use original name if no output_name given
    original_name = os.path.basename(resolved)
    original_ext = os.path.splitext(original_name)[1]  # e.g. ".mkv"

    if output_name:
        out_filename = os.path.basename(output_name)
    else:
        out_filename = original_name

    # Ensure the output filename has a proper extension so ffmpeg can
    # determine the muxer.  When stream-copying, keep the original
    # container extension; when re-encoding, use the chosen container.
    name_stem, name_ext = os.path.splitext(out_filename)
    if not name_ext:
        if stream_copy:
            out_filename = name_stem + original_ext
        elif container:
            out_filename = name_stem + "." + container
        else:
            out_filename = name_stem + original_ext

    output_dir = os.path.join(job_dir, "output")
    output_path = os.path.join(output_dir, out_filename)

    # Update job metadata
    with get_job_meta_lock(job_id):
        meta = load_job_metadata(job_id)
        if meta:
            meta["status"] = "cutting"
            meta["cut_settings"] = {
                "in_point": in_point,
                "out_point": out_point,
                "stream_copy": stream_copy,
                "codec": codec or None,
                "container": container or None,
                "audio_tracks": audio_tracks_parsed,
                "keep_quality": keep_quality,
                "output_name": output_name or None,
            }
            save_job_metadata(job_id, meta)

    msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    cancel_event = threading.Event()

    def run_cut():
        try:
            if cancel_event.is_set():
                msg_queue.put(("error_msg", "Cut cancelled before start"))
                msg_queue.put(("done", "Cut failed"))
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
                audio_tracks=audio_tracks_parsed if audio_tracks_parsed else None,
                container=container or None,
                progress_cb=progress_cb,
                keep_quality=keep_quality,
                source_video_bitrate=source_video_bitrate,
                source_audio_bitrates=(
                    source_audio_bitrates if source_audio_bitrates else None
                ),
                audio_streams=probe_audio_streams,
                job_id=job_id,
                cancel_event=cancel_event,
            )

            final_name = os.path.basename(final_path)

            # Update job metadata with output
            with get_job_meta_lock(job_id):
                meta = load_job_metadata(job_id)
                if meta:
                    meta["status"] = "done"
                    existing = meta.get("output_files", [])
                    if final_name not in existing:
                        existing.append(final_name)
                    meta["output_files"] = existing
                    save_job_metadata(job_id, meta)

            msg_queue.put(("done", f"Output: {final_name}"))
        except Exception as e:
            logger.exception("Cut failed")
            # Update job status to error
            with get_job_meta_lock(job_id):
                meta = load_job_metadata(job_id)
                if meta:
                    meta["status"] = "error"
                    save_job_metadata(job_id, meta)
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
