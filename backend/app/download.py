import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from yt_dlp import YoutubeDL
from yt_dlp import version as yt_dlp_version

from app.config import (
    DOWNLOADER_JOBS_DIR,
    DOWNLOADER_JOB_TTL,
    DOWNLOADS_DIR,
    YT_DLP_COOKIES,
    resolve_base,
)
from app.hwaccel import resolve_video_encoder

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ACTIVE_STATUSES = {"queued", "downloading", "processing"}
_STATUS_MAP = {
    "pending": "queued",
    "running": "downloading",
    "completed": "done",
    "failed": "error",
    "aborted": "error",
}
_VIDEO_CODEC_TO_ENCODER = {
    "h264": "libx264",
    "h265": "libx265",
    "vp9": "libvpx-vp9",
    "av1": "libsvtav1",
}
_QUALITY_HEIGHTS = {
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
}
_AUDIO_QUALITY_KBPS = {
    "320kbps": 320,
    "256kbps": 256,
    "192kbps": 192,
    "128kbps": 128,
    "96kbps": 96,
}
_THUMBNAIL_FORMATS = {"jpg", "png", "webp"}
_AUDIO_FORMATS = {"mp3", "m4a", "flac", "opus", "wav"}
_VIDEO_FORMATS = {"mp4", "mkv", "webm", "mov"}
_CANCEL_WAIT_SECONDS = 15.0
_metadata_lock = threading.Lock()
_runtime_guard = threading.Lock()
_job_runtimes: dict[str, "_JobRuntime"] = {}


class DownloadCancelled(RuntimeError):
    """Raised when a running yt-dlp job is cancelled."""


class _JobRuntime:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.active = False
        self.cancel_event = threading.Event()


def _normalize_status(status: Any) -> str:
    status_str = str(status or "").lower().strip()
    return _STATUS_MAP.get(status_str, status_str or "queued")


def _validate_job_id(job_id: str) -> None:
    if not _UUID_RE.match(job_id):
        raise ValueError(f"Invalid job_id format: {job_id}")


def _get_job_dir(job_id: str) -> str:
    _validate_job_id(job_id)
    return os.path.join(DOWNLOADER_JOBS_DIR, job_id)


def _get_runtime(job_id: str, *, create: bool = False) -> _JobRuntime | None:
    with _runtime_guard:
        runtime = _job_runtimes.get(job_id)
        if runtime is None and create:
            runtime = _JobRuntime()
            _job_runtimes[job_id] = runtime
        return runtime


def _clear_runtime(job_id: str) -> None:
    with _runtime_guard:
        runtime = _job_runtimes.get(job_id)
        if runtime is None:
            return
        with runtime.condition:
            if runtime.active:
                return
        _job_runtimes.pop(job_id, None)


def _begin_job(job_id: str) -> threading.Event:
    runtime = _get_runtime(job_id, create=True)
    assert runtime is not None
    with runtime.condition:
        if runtime.active:
            raise RuntimeError(f"Job {job_id} is already running")
        runtime.active = True
        runtime.cancel_event.clear()
        return runtime.cancel_event


def _finish_job(job_id: str) -> None:
    runtime = _get_runtime(job_id)
    if runtime is None:
        return
    with runtime.condition:
        runtime.active = False
        runtime.condition.notify_all()
    _clear_runtime(job_id)


def _job_is_active(job_id: str) -> bool:
    runtime = _get_runtime(job_id)
    if runtime is None:
        return False
    with runtime.condition:
        return runtime.active


def _validate_relative_path(root: str, relative_path: str) -> str:
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(os.path.join(root_real, relative_path))
    if target_real != root_real and not target_real.startswith(root_real + os.sep):
        raise ValueError("Invalid directory path")
    return target_real


def _safe_prefix(prefix: Any) -> str:
    raw = str(prefix or "").strip()
    if not raw:
        return ""
    raw = raw.replace("/", "_").replace("\\", "_").replace(os.sep, "_")
    return raw


def _format_speed(value: Any) -> str | None:
    if value is None:
        return None
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return None
    units = ["B/s", "KiB/s", "MiB/s", "GiB/s"]
    unit_idx = 0
    while speed >= 1024 and unit_idx < len(units) - 1:
        speed /= 1024
        unit_idx += 1
    if unit_idx == 0:
        return f"{speed:.0f}{units[unit_idx]}"
    return f"{speed:.1f}{units[unit_idx]}"


def _format_eta(value: Any) -> str | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    minutes, secs = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _compute_progress(data: dict[str, Any]) -> float:
    """Extract progress percentage from yt-dlp hook data, preferring byte counts."""
    downloaded = data.get("downloaded_bytes")
    total = data.get("total_bytes") or data.get("total_bytes_estimate")
    if downloaded is not None and total:
        try:
            return min(float(downloaded) / float(total) * 100.0, 100.0)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    percent_raw = str(data.get("_percent_str", "0")).replace("%", "").replace(",", ".").strip()
    try:
        return float(percent_raw)
    except ValueError:
        return 0.0


def _format_size_bytes(value: Any) -> str | None:
    if value is None:
        return None
    try:
        size = float(value)
    except (TypeError, ValueError):
        return None
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    if unit_idx == 0:
        return f"{size:.0f}{units[unit_idx]}"
    return f"{size:.1f}{units[unit_idx]}"


def _video_format_selector(quality: str) -> str:
    quality_key = quality.lower()
    if quality_key == "worst":
        return "wv*+wa/worst"
    if quality_key == "best":
        return "bv*+ba/best"
    height = _QUALITY_HEIGHTS.get(quality_key)
    if height:
        return f"bv*[height<={height}]+ba/b[height<={height}]"
    return "bv*+ba/best"


def _audio_format_selector(quality: str) -> str:
    quality_key = quality.lower()
    if quality_key == "worst":
        return "worstaudio/worst"
    kbps = _AUDIO_QUALITY_KBPS.get(quality_key)
    if kbps:
        return f"bestaudio[abr<={kbps}]/bestaudio/best"
    return "bestaudio/best"


def _resolve_output_root(options: dict[str, Any]) -> str:
    base_label = str(options.get("base") or "").strip()
    output_dir = str(options.get("output_dir") or "").strip()
    sub_folder = str(options.get("sub_folder") or "").strip()

    if base_label and output_dir:
        base_root = resolve_base(base_label)
        root = _validate_relative_path(base_root, output_dir)
    else:
        root = os.path.realpath(DOWNLOADS_DIR)

    if sub_folder:
        root = _validate_relative_path(root, sub_folder)
    return root


def get_cookie_path() -> str:
    return YT_DLP_COOKIES or os.path.join(DOWNLOADER_JOBS_DIR, "cookies.txt")


def get_status_payload() -> dict[str, Any]:
    cookie_path = get_cookie_path()
    return {
        "yt_dlp_version": yt_dlp_version.__version__,
        "cookies_present": os.path.isfile(cookie_path),
        "downloads_dir": DOWNLOADS_DIR,
    }


def create_job(url: str, options: dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    os.makedirs(_get_job_dir(job_id), exist_ok=True)
    metadata = {
        "schema_version": 2,
        "job_id": job_id,
        "url": url,
        "options": options,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "queued",
        "progress": 0.0,
        "speed": None,
        "eta": None,
        "filename": None,
        "error": None,
        "size": None,
    }
    save_job_metadata(job_id, metadata)
    return job_id


def save_job_metadata(job_id: str, data: dict[str, Any]) -> None:
    _validate_job_id(job_id)
    job_dir = _get_job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)
    payload = dict(data)
    payload["status"] = _normalize_status(payload.get("status"))
    final_path = os.path.join(job_dir, "job.json")
    tmp_path = final_path + ".tmp"
    with _metadata_lock:
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, final_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def load_job_metadata(job_id: str) -> dict[str, Any] | None:
    if not _UUID_RE.match(job_id):
        return None
    meta_path = os.path.join(_get_job_dir(job_id), "job.json")
    if not os.path.isfile(meta_path):
        return None
    with _metadata_lock:
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load metadata for job %s: %s", job_id, e)
            return None
    data["status"] = _normalize_status(data.get("status"))
    data.setdefault("speed", None)
    data.setdefault("eta", None)
    data.setdefault("filename", None)
    data.setdefault("error", None)
    data.setdefault("size", None)
    return data


def list_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if not os.path.isdir(DOWNLOADER_JOBS_DIR):
        return jobs
    for name in os.listdir(DOWNLOADER_JOBS_DIR):
        if not _UUID_RE.match(name):
            continue
        meta = load_job_metadata(name)
        if meta:
            jobs.append(meta)
    jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
    return jobs


def delete_job(job_id: str) -> None:
    _validate_job_id(job_id)
    job_dir = _get_job_dir(job_id)
    if not os.path.isdir(job_dir):
        raise ValueError(f"Job not found: {job_id}")

    runtime = _get_runtime(job_id)
    if runtime is not None:
        with runtime.condition:
            if runtime.active:
                runtime.cancel_event.set()
                meta = load_job_metadata(job_id)
                if meta:
                    meta["status"] = "error"
                    meta["error"] = "Cancelled by user"
                    save_job_metadata(job_id, meta)
                deadline = time.monotonic() + _CANCEL_WAIT_SECONDS
                while runtime.active and time.monotonic() < deadline:
                    runtime.condition.wait(timeout=max(0.1, deadline - time.monotonic()))
                if runtime.active:
                    raise RuntimeError(f"Job {job_id} is still busy and could not be deleted")

    shutil.rmtree(job_dir, ignore_errors=True)
    _clear_runtime(job_id)


def cleanup_old_jobs() -> None:
    if not os.path.isdir(DOWNLOADER_JOBS_DIR):
        return
    now = datetime.now(timezone.utc)
    for name in os.listdir(DOWNLOADER_JOBS_DIR):
        if not _UUID_RE.match(name):
            continue
        meta = load_job_metadata(name)
        job_dir = _get_job_dir(name)
        try:
            if meta is None:
                mtime = datetime.fromtimestamp(os.path.getmtime(job_dir), tz=timezone.utc)
                if (now - mtime).total_seconds() > DOWNLOADER_JOB_TTL:
                    delete_job(name)
                continue
            created_at = datetime.fromisoformat(meta["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            status = _normalize_status(meta.get("status"))
            if status in _ACTIVE_STATUSES or _job_is_active(name):
                continue
            if (now - created_at).total_seconds() > DOWNLOADER_JOB_TTL:
                delete_job(name)
        except Exception:
            logger.warning("Error checking download job %s for cleanup", name, exc_info=True)


def _build_postprocessor_args(options: dict[str, Any]) -> dict[str, list[str]]:
    media_type = str(options.get("type") or "video").lower()
    if media_type != "video":
        return {}
    codec = str(options.get("codec") or "").lower()
    cpu_encoder = _VIDEO_CODEC_TO_ENCODER.get(codec)
    if not cpu_encoder:
        return {}
    return {
        "FFmpegVideoConvertor+ffmpeg": ["-c:v", resolve_video_encoder(cpu_encoder)],
    }


def get_ydl_opts(options: dict[str, Any]) -> dict[str, Any]:
    media_type = str(options.get("type") or "video").lower()
    codec = str(options.get("codec") or "").lower()
    requested_format = str(options.get("format") or "").lower()
    quality = str(options.get("quality") or "best")
    output_root = _resolve_output_root(options)
    prefix = _safe_prefix(options.get("custom_prefix"))
    custom_filename = _safe_prefix(options.get("custom_filename"))

    if custom_filename:
        outtmpl = os.path.join(output_root, f"{prefix}{custom_filename}.%(ext)s")
    else:
        outtmpl = os.path.join(output_root, f"{prefix}%(title)s.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "progress_hooks": [],
        "postprocessor_hooks": [],
        "outtmpl": outtmpl,
        "postprocessors": [],
    }

    cookie_path = get_cookie_path()
    if os.path.isfile(cookie_path):
        ydl_opts["cookiefile"] = cookie_path

    item_limit = int(options.get("item_limit") or 0)
    if item_limit > 0:
        ydl_opts["playlistend"] = item_limit

    if bool(options.get("split_chapters")):
        ydl_opts["split_chapters"] = True

    if media_type == "audio":
        ydl_opts["format"] = _audio_format_selector(quality)
        pp: dict[str, Any] = {"key": "FFmpegExtractAudio"}
        if requested_format in _AUDIO_FORMATS:
            pp["preferredcodec"] = requested_format
        else:
            # Auto format: let ffmpeg pick best audio codec, ensures proper
            # audio extension instead of keeping video containers like .mp4/.webm
            pp["preferredcodec"] = "best"
        kbps = _AUDIO_QUALITY_KBPS.get(quality.lower())
        if kbps:
            pp["preferredquality"] = str(kbps)
        ydl_opts["postprocessors"].append(pp)
    elif media_type == "thumbnail":
        ydl_opts["format"] = "best"
        ydl_opts["skip_download"] = True
        ydl_opts["writethumbnail"] = True
        if requested_format in _THUMBNAIL_FORMATS:
            ydl_opts["postprocessors"].append({
                "key": "FFmpegThumbnailsConvertor",
                "format": requested_format,
            })
    else:
        # Video
        ydl_opts["format"] = _video_format_selector(quality)
        if codec != "auto" and requested_format in _VIDEO_FORMATS:
            ydl_opts["postprocessors"].append({
                "key": "FFmpegVideoConvertor",
                "prefformat": requested_format,
            })
            pp_args = _build_postprocessor_args(options)
            if pp_args:
                ydl_opts["postprocessor_args"] = pp_args
        elif requested_format in _VIDEO_FORMATS:
            # Auto codec but specific format: just set merge format
            ydl_opts["merge_output_format"] = requested_format

    # Remove empty postprocessors list to keep opts clean
    if not ydl_opts["postprocessors"]:
        del ydl_opts["postprocessors"]

    return ydl_opts


def _display_name(filename: Any) -> str | None:
    if not filename:
        return None
    return os.path.basename(str(filename))


def _job_payload(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": meta.get("job_id"),
        "url": meta.get("url"),
        "status": _normalize_status(meta.get("status")),
        "progress": float(meta.get("progress") or 0.0),
        "speed": meta.get("speed"),
        "eta": meta.get("eta"),
        "filename": meta.get("filename"),
        "error": meta.get("error"),
        "created_at": meta.get("created_at"),
        "size": meta.get("size"),
    }


def _extract_final_path(info: Mapping[str, Any]) -> str | None:
    filepath = info.get("filepath") or info.get("_filename")
    if filepath and os.path.isfile(str(filepath)):
        return str(filepath)
    requested = info.get("requested_downloads") or []
    if requested and isinstance(requested, list):
        first = requested[0] or {}
        candidate = first.get("filepath") or first.get("_filename")
        if candidate and os.path.isfile(str(candidate)):
            return str(candidate)
    return None


def _extract_thumbnail_path(info: Mapping[str, Any], outtmpl: str) -> str | None:
    """Find the written thumbnail file from yt-dlp info dict."""
    thumbnails = info.get("thumbnails") or []
    for thumb in reversed(thumbnails):
        fp = thumb.get("filepath")
        if fp and os.path.isfile(str(fp)):
            return str(fp)
    # Fallback: scan the output directory for an image matching the title
    title = info.get("title")
    if not title:
        return None
    base_dir = os.path.dirname(outtmpl)
    if not os.path.isdir(base_dir):
        return None
    safe_title = _safe_prefix(title)
    for ext in ("jpg", "jpeg", "png", "webp"):
        for candidate_name in (f"{title}.{ext}", f"{safe_title}.{ext}"):
            candidate = os.path.join(base_dir, candidate_name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _extract_final_size(info: Mapping[str, Any], filepath: str | None) -> str | None:
    if filepath and os.path.isfile(filepath):
        try:
            return _format_size_bytes(os.path.getsize(filepath))
        except OSError:
            return None
    for key in ("filesize", "filesize_approx"):
        size = _format_size_bytes(info.get(key))
        if size:
            return size
    requested = info.get("requested_downloads") or []
    if requested and isinstance(requested, list):
        first = requested[0] or {}
        for key in ("filesize", "filesize_approx"):
            size = _format_size_bytes(first.get(key))
            if size:
                return size
    return None


class DownloadManager:
    def __init__(self, job_id: str, url: str, options: dict[str, Any]):
        self.job_id = job_id
        self.url = url
        self.options = dict(options)
        self.progress_queue: Any = None
        self.cancel_event: threading.Event | None = None

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.progress_queue is not None:
            self.progress_queue.put((event_type, payload))

    def _load_meta(self) -> dict[str, Any]:
        meta = load_job_metadata(self.job_id)
        if meta is None:
            raise RuntimeError(f"Job not found: {self.job_id}")
        return meta

    def _save_meta(self, **changes: Any) -> dict[str, Any]:
        meta = self._load_meta()
        meta.update(changes)
        save_job_metadata(self.job_id, meta)
        return meta

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")

    def progress_hook(self, data: dict[str, Any]) -> None:
        self._raise_if_cancelled()
        status = _normalize_status(data.get("status"))
        if status == "downloading":
            progress = _compute_progress(data)
            filename = _display_name(data.get("filename"))
            meta = self._save_meta(
                status="downloading",
                progress=progress,
                speed=_format_speed(data.get("speed")),
                eta=_format_eta(data.get("eta")),
                filename=filename,
                error=None,
            )
            self._emit("progress", _job_payload(meta))
        elif status == "finished":
            meta = self._save_meta(
                status="processing",
                progress=100.0,
                filename=_display_name(data.get("filename")),
                speed=None,
                eta=None,
            )
            self._emit("progress", _job_payload(meta))
        elif status == "error":
            raise RuntimeError("yt-dlp reported a download error")

    def postprocessor_hook(self, data: dict[str, Any]) -> None:
        self._raise_if_cancelled()
        status = str(data.get("status") or "").lower()
        if status in {"started", "processing"}:
            info_dict = data.get("info_dict") or {}
            meta = self._save_meta(
                status="processing",
                progress=100.0,
                filename=_display_name(_extract_final_path(info_dict)) or self._load_meta().get("filename"),
                speed=None,
                eta=None,
            )
            self._emit("progress", _job_payload(meta))

    def run(self, q: Any = None) -> None:
        self.progress_queue = q
        self.cancel_event = _begin_job(self.job_id)
        try:
            ydl_opts = get_ydl_opts(self.options)
            ydl_opts["progress_hooks"].append(self.progress_hook)
            ydl_opts["postprocessor_hooks"].append(self.postprocessor_hook)
            self._emit("progress", _job_payload(self._load_meta()))

            with YoutubeDL(ydl_opts) as ydl:  # type: ignore[reportArgumentType]
                info = ydl.extract_info(self.url, download=True)

            info_dict = info if isinstance(info, dict) else {}
            media_type = str(self.options.get("type") or "video").lower()
            if media_type == "thumbnail":
                final_path = _extract_thumbnail_path(info_dict, ydl_opts.get("outtmpl", ""))
            else:
                final_path = _extract_final_path(info_dict)
            meta = self._save_meta(
                status="done",
                progress=100.0,
                speed=None,
                eta=None,
                filename=_display_name(final_path) or self._load_meta().get("filename"),
                size=_extract_final_size(info_dict, final_path),
                error=None,
                output_path=final_path,
            )
            self._emit("done", _job_payload(meta))
        except DownloadCancelled as exc:
            meta = self._save_meta(
                status="error",
                error=str(exc),
                speed=None,
                eta=None,
            )
            payload = _job_payload(meta)
            self._emit("error_msg", payload)
            self._emit("done", payload)
        except Exception as exc:
            logger.error("Download failed for job %s: %s", self.job_id, exc, exc_info=True)
            meta = self._save_meta(
                status="error",
                error=_ANSI_RE.sub("", str(exc)),
                speed=None,
                eta=None,
            )
            payload = _job_payload(meta)
            self._emit("error_msg", payload)
            self._emit("done", payload)
        finally:
            _finish_job(self.job_id)
