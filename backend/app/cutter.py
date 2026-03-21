import base64
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from app.config import resolve_base, BASE_PATH_LABELS, CUTTER_JOBS_DIR, CUTTER_JOB_TTL
from app.fs_utils import collision_safe_path
from app.hwaccel import build_video_encode_args, get_hwaccel_input_args, resolve_video_encoder

logger = logging.getLogger(__name__)

# Codecs that need transcoding for browser preview playback
_TRANSCODE_CODECS = {"ac3", "eac3", "dts", "dts_hd", "truehd"}
_PASSTHROUGH_CODECS = {
    "aac",
    "mp3",
    "opus",
    "vorbis",
    "flac",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "pcm_f32le",
}
_BROWSER_VIDEO_CODECS = {"h264", "hevc", "h265", "vp8", "vp9", "av1"}
_PREVIEW_X264_PRESET = "superfast"
_PREVIEW_MAX_THREADS = "2"

# File extensions browsers can play natively
_BROWSER_EXTENSIONS = {
    ".mp4",
    ".m4a",
    ".m4v",
    ".mov",
    ".webm",
    ".ogg",
    ".mp3",
    ".wav",
    ".aac",
    ".flac",
}
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_FFMPEG_PROGRESS_RE = re.compile(r"^frame=\s*\d+")
_DELETE_WAIT_SECONDS = 15.0
_DELETE_RETRY_ATTEMPTS = 8
_DELETE_RETRY_DELAY = 0.2


def _monitor_cancel(proc, cancel_event):
    """Monitor cancel event and terminate process if triggered."""
    while proc.poll() is None:
        if cancel_event.is_set():
            proc.terminate()
            return
        try:
            cancel_event.wait(timeout=0.25)
        except Exception:
            pass


class _JobActivityState:
    """Mutable synchronization state for long-running operations per cutter job."""

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.active_operations = 0
        self.deleting = False
        self.cancel_events: set[threading.Event] = set()
        self.processes: set[object] = set()


def _audio_relative_index(audio_streams: list[dict], absolute_index: int) -> int:
    """Convert an absolute ffprobe stream index to an audio-type-relative index.

    e.g. if streams are [video(0), audio(1), sub(2), audio(3)],
    absolute_index=3 → audio-relative index 1 (second audio stream).
    """
    for i, s in enumerate(audio_streams):
        if int(s.get("index", -1)) == absolute_index:
            return i
    raise RuntimeError(
        f"Audio stream index {absolute_index} not found in "
        f"{[s.get('index') for s in audio_streams]}"
    )


# Map user-facing codec names to ffmpeg encoder names
_CODEC_TO_ENCODER = {
    "copy": "copy",
    "aac": "aac",
    "flac": "flac",
    "opus": "libopus",
    "ac3": "ac3",
    "mp3": "libmp3lame",
    "vorbis": "libvorbis",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
}

_VIDEO_ENCODERS = {"libx264", "libx265", "libvpx-vp9", "libaom-av1"}

_VALID_CONTAINERS = {
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

# ffmpeg muxer names that differ from the common file extension
_CONTAINER_TO_FFMPEG_FORMAT: dict[str, str] = {
    "mkv": "matroska",
    "mka": "matroska",
    "m4a": "ipod",
    "mts": "mpegts",
    "ts": "mpegts",
    "aac": "adts",
}


def _safe_getmtime(filepath: str) -> float:
    """Get file mtime, raising RuntimeError instead of OSError for cleaner error messages."""
    try:
        return os.path.getmtime(filepath)
    except OSError as e:
        raise RuntimeError(f"Cannot access file {filepath}: {e}") from e


def _extract_window(filepath: str, position: float, window_secs: float = 5.0) -> bytes:
    """Extract a short audio window from a file using fast seek."""
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-ss",
        str(position),
        "-t",
        str(window_secs),
        "-i",
        filepath,
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Waveform sample timed out at %.2fs for %s",
            position,
            filepath,
        )
        return b""
    if result.returncode != 0:
        logger.warning(
            "Waveform extraction failed at %.2fs for %s: %s",
            position,
            filepath,
            result.stderr.decode(errors="replace").strip(),
        )
        return b""  # Skip failed windows gracefully
    return result.stdout


@functools.lru_cache(maxsize=50)
def _waveform_cached(filepath: str, mtime: float, num_peaks: int) -> list[float]:
    """Cached waveform generator using sampling windows for large files."""
    info = probe_file(filepath)
    duration = info["duration"]

    if duration <= 0:
        return [0.0] * num_peaks

    # For short files (<= 120s), decode the whole thing directly
    if duration <= 120:
        cmd = [
            "ffmpeg",
            "-loglevel",
            "warning",
            "-i",
            filepath,
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "f32le",
            "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ffmpeg waveform timed out for {filepath}") from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg waveform failed: {result.stderr.decode(errors='replace')}"
            )
        raw = result.stdout
    else:
        # Sample N windows spread across the file using fast seek
        num_windows = 20
        window_secs = 5.0
        raw = b""
        for i in range(num_windows):
            position = (i / num_windows) * duration
            raw += _extract_window(filepath, position, window_secs)

    num_samples = len(raw) // 4
    if num_samples == 0:
        return [0.0] * num_peaks

    samples = struct.unpack(f"<{num_samples}f", raw[: num_samples * 4])

    bucket_size = max(1, num_samples // num_peaks)
    peaks: list[float] = []
    for i in range(num_peaks):
        start = i * bucket_size
        end = min(start + bucket_size, num_samples)
        if start >= num_samples:
            peaks.append(0.0)
        else:
            peak = max(abs(s) for s in samples[start:end])
            peaks.append(peak)

    # Normalize to 0.0-1.0
    max_peak = max(peaks) if peaks else 1.0
    if max_peak > 0:
        peaks = [p / max_peak for p in peaks]

    return peaks


def probe_file(filepath: str) -> dict:
    """Run ffprobe and return parsed media info."""
    cmd = [
        "ffprobe",
        "-loglevel",
        "warning",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        filepath,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out for {filepath}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {filepath}: {result.stderr}")

    if not result.stdout:
        raise RuntimeError(f"ffprobe produced no output for {filepath}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"ffprobe returned malformed JSON for {filepath}: {e}"
        ) from e
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic", 0)
        ),
        None,
    )

    display_aspect_ratio = None
    if video_stream:
        raw_dar = str(video_stream.get("display_aspect_ratio") or "").strip()
        if raw_dar and raw_dar not in {"0:1", "N/A"}:
            parts = raw_dar.split(":", 1)
            if len(parts) == 2:
                try:
                    dar_width = int(parts[0])
                    dar_height = int(parts[1])
                except ValueError:
                    display_aspect_ratio = None
                else:
                    if dar_width > 0 and dar_height > 0:
                        display_aspect_ratio = f"{dar_width} / {dar_height}"

    audio_streams = [
        {
            "index": int(s["index"]),
            "codec": s.get("codec_name", "unknown"),
            "channels": int(s.get("channels", 0)),
            "sample_rate": int(s.get("sample_rate", 0)),
            "bit_rate": int(s.get("bit_rate", 0)),
            "language": s.get("tags", {}).get("language", ""),
            "title": s.get("tags", {}).get("title", ""),
        }
        for s in streams
        if s.get("codec_type") == "audio"
    ]
    first_audio = audio_streams[0] if audio_streams else None

    info: dict = {
        "duration": float(fmt.get("duration", 0)),
        "video_codec": video_stream.get("codec_name") if video_stream else None,
        "audio_codec": first_audio["codec"] if first_audio else "unknown",
        "container": fmt.get("format_name", "unknown"),
        "bitrate": int(fmt.get("bit_rate", 0)),
        "width": (
            int(video_stream["width"])
            if video_stream and "width" in video_stream
            else None
        ),
        "height": (
            int(video_stream["height"])
            if video_stream and "height" in video_stream
            else None
        ),
        "display_aspect_ratio": display_aspect_ratio,
        "sample_rate": first_audio["sample_rate"] if first_audio else 0,
        "video_bitrate": int(video_stream.get("bit_rate", 0)) if video_stream else None,
        "audio_streams": audio_streams,
    }
    return info


def generate_waveform(filepath: str, num_peaks: int = 2000) -> list[float]:
    """Generate a normalized waveform peak list from an audio/video file.

    Uses ffmpeg to extract mono PCM f32le audio at 8kHz,
    then buckets samples and takes the max absolute value per bucket. Results
    are cached (bounded LRU, max 50 entries) by (filepath, mtime) to avoid
    regeneration.
    """
    mtime = _safe_getmtime(filepath)
    return _waveform_cached(filepath, mtime, num_peaks)


def needs_transcoding(
    audio_codec: str, filepath: str = "", video_codec: str = ""
) -> bool:
    """Return True if the file needs transcoding for browser preview.

    Checks file extension plus audio/video codecs. Browsers only
    support a limited set of containers (MP4, WebM, etc.) — files in
    unsupported containers (MKV, AVI, etc.) must always be transcoded.
    """
    # Check file extension — more reliable than ffprobe format_name
    if filepath:
        ext = os.path.splitext(filepath)[1].lower()
        if ext and ext not in _BROWSER_EXTENSIONS:
            return True
    # Check audio codec
    codec = audio_codec.lower()
    if codec in _TRANSCODE_CODECS:
        return True
    if codec in _PASSTHROUGH_CODECS:
        pass
    else:
        return True

    if video_codec:
        vcodec = video_codec.lower()
        if vcodec and vcodec not in _BROWSER_VIDEO_CODECS:
            return True

    return False


def transcode_for_preview(
    filepath: str, audio_stream_index: int | None = None
) -> subprocess.Popen:
    """Remux/transcode into fragmented MP4 for browser-compatible streaming.

    Video is stream-copied only when browser-compatible, otherwise re-encoded
    to H.264. Audio is copied when browser-compatible, or transcoded to AAC.
    Subtitle streams are dropped (often incompatible with MP4).
    """
    info = probe_file(filepath)
    has_video = info.get("video_codec") is not None

    # Determine audio codec and channel count for the selected stream
    audio_streams = info.get("audio_streams", [])
    audio_channels = 0
    if audio_stream_index is not None and audio_streams:
        sel = next((s for s in audio_streams if s["index"] == audio_stream_index), None)
        audio_codec = (
            sel["codec"] if sel else info.get("audio_codec", "unknown")
        ).lower()
        audio_channels = sel.get("channels", 0) if sel else 0
    else:
        audio_codec = info.get("audio_codec", "unknown").lower()
        first_audio = audio_streams[0] if audio_streams else None
        audio_channels = first_audio.get("channels", 0) if first_audio else 0

    video_codec = str(info.get("video_codec") or "").lower()
    needs_video_reencode = has_video and video_codec not in _BROWSER_VIDEO_CODECS

    cmd = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    if needs_video_reencode:
        cmd += get_hwaccel_input_args()
    cmd += ["-i", filepath]

    # Map specific streams when audio stream is selected
    if audio_stream_index is not None:
        if has_video:
            cmd += ["-map", "0:v:0"]
        rel = _audio_relative_index(audio_streams, audio_stream_index)
        cmd += ["-map", f"0:a:{rel}"]
    elif has_video:
        pass  # default mapping picks first video + first audio

    if has_video:
        if video_codec in _BROWSER_VIDEO_CODECS:
            cmd += ["-c:v", "copy"]
        else:
            cmd += build_video_encode_args(
                "libx264", crf="23", preset=_PREVIEW_X264_PRESET, pix_fmt="yuv420p",
            )
            cmd += ["-threads", _PREVIEW_MAX_THREADS]
    else:
        cmd += ["-vn"]

    # Copy audio if browser-compatible, otherwise transcode to AAC
    if audio_codec in _PASSTHROUGH_CODECS:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
        # AAC doesn't support height/object channels (e.g. 5.1.2 Atmos
        # layouts), so downmix to stereo when channels > 6.
        if audio_channels > 6:
            cmd += ["-ac", "2"]

    cmd += [
        "-sn",  # Drop subtitles (ASS/SSA not MP4-compatible)
        "-f",
        "mp4",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _preview_cache_key(filepath: str) -> str:
    """Build a cache key for the master preview (includes all audio tracks)."""
    mtime = _safe_getmtime(filepath)
    key = f"{filepath}:{mtime}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _preview_status_key(filepath: str, job_id: str) -> str:
    return f"{job_id}:{_preview_cache_key(filepath)}"


def _audio_transcode_status_key(
    filepath: str, job_id: str, audio_stream_index: int
) -> str:
    """Status key for audio-only transcode — distinct from master preview key."""
    return f"{job_id}:{_preview_cache_key(filepath)}:srcaudio{audio_stream_index}"


def _compact_process_error(stderr: str, stdout: str) -> str:
    merged = "\n".join(
        part for part in ((stderr or ""), (stdout or "")) if part
    ).strip()
    if not merged:
        return "no stderr/stdout output from ffmpeg"

    lines = [line.strip() for line in merged.splitlines() if line.strip()]

    # Drop ffmpeg -stats progress spam to keep actual warnings/errors visible.
    meaningful = [
        line
        for line in lines
        if not _FFMPEG_PROGRESS_RE.match(line)
        and "bitrate=" not in line
        and "speed=" not in line
        and "elapsed=" not in line
    ]

    if meaningful:
        return " | ".join(meaningful[-6:])[-1200:]
    return merged[-1200:]


def _seconds_from_ffmpeg_time(line: str) -> float | None:
    match = _FFMPEG_TIME_RE.search(line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _safe_remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        return


def _close_pipe(pipe: object) -> None:
    close = getattr(pipe, "close", None)
    if callable(close):
        try:
            close()
        except OSError:
            pass


def _preview_file_path(filepath: str, job_id: str) -> str:
    suffix = _preview_cache_key(filepath)
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    return os.path.join(job_dir, f"preview_{suffix}.mp4")


def _prune_job_activity_state(job_id: str, state: _JobActivityState) -> None:
    with _job_activity_guard:
        current = _job_activity.get(job_id)
        if current is not state:
            return
        if (
            state.active_operations == 0
            and not state.cancel_events
            and not state.processes
            and not state.deleting
        ):
            _job_activity.pop(job_id, None)


def _get_job_activity_state(
    job_id: str, create: bool = False
) -> _JobActivityState | None:
    with _job_activity_guard:
        state = _job_activity.get(job_id)
        if state is None and create:
            state = _JobActivityState()
            _job_activity[job_id] = state
        return state


def _begin_job_operation(
    job_id: str, cancel_event: threading.Event | None = None
) -> None:
    state = _get_job_activity_state(job_id, create=True)
    assert state is not None
    with state.condition:
        if state.deleting:
            raise RuntimeError(f"Job {job_id} is being deleted")
        state.active_operations += 1
        if cancel_event is not None:
            state.cancel_events.add(cancel_event)


def _end_job_operation(
    job_id: str, cancel_event: threading.Event | None = None
) -> None:
    state = _get_job_activity_state(job_id)
    if state is None:
        return
    with state.condition:
        if cancel_event is not None:
            state.cancel_events.discard(cancel_event)
        if state.active_operations > 0:
            state.active_operations -= 1
        state.condition.notify_all()
    _prune_job_activity_state(job_id, state)


def _register_job_process(job_id: str, proc: object) -> None:
    state = _get_job_activity_state(job_id, create=True)
    assert state is not None
    with state.condition:
        state.processes.add(proc)
        state.condition.notify_all()


def _unregister_job_process(job_id: str, proc: object) -> None:
    state = _get_job_activity_state(job_id)
    if state is None:
        return
    with state.condition:
        state.processes.discard(proc)
        state.condition.notify_all()
    _prune_job_activity_state(job_id, state)


def _stop_process(proc: object, kill: bool = False) -> None:
    poll = getattr(proc, "poll", None)
    try:
        if callable(poll) and poll() is not None:
            return
    except Exception:
        pass
    action = getattr(proc, "kill" if kill else "terminate", None)
    if callable(action):
        try:
            action()
        except ProcessLookupError:
            pass
        except Exception:
            logger.warning("Error stopping process %s", proc, exc_info=True)


def _wait_for_process_shutdown(proc: object, timeout: float) -> None:
    wait = getattr(proc, "wait", None)
    if callable(wait):
        try:
            wait(timeout=timeout)
        except ProcessLookupError:
            pass
        except Exception:
            logger.warning("Error waiting for process %s shutdown", proc, exc_info=True)


def _job_has_active_operations(job_id: str) -> bool:
    state = _get_job_activity_state(job_id)
    if state is None:
        return False
    with state.condition:
        return state.active_operations > 0 or bool(state.processes)


def _clear_job_runtime_state(job_id: str) -> None:
    with _job_activity_guard:
        _job_activity.pop(job_id, None)

    job_dir_prefix = os.path.normcase(os.path.join(CUTTER_JOBS_DIR, job_id) + os.sep)
    with _preview_build_lock_guard:
        stale_lock_keys = [
            key
            for key in _preview_build_locks
            if os.path.normcase(key).startswith(job_dir_prefix)
        ]
        for key in stale_lock_keys:
            _preview_build_locks.pop(key, None)

    status_prefix = f"{job_id}:"
    with _preview_status_guard:
        stale_keys = [key for key in _preview_status if key.startswith(status_prefix)]
        for key in stale_keys:
            _preview_status.pop(key, None)

    with _job_meta_lock_guard:
        _job_meta_locks.pop(job_id, None)


def _cancel_job_operations(job_id: str, timeout: float = _DELETE_WAIT_SECONDS) -> None:
    state = _get_job_activity_state(job_id)
    if state is None:
        return

    with state.condition:
        state.deleting = True
        cancel_events = list(state.cancel_events)
        processes = list(state.processes)
        state.condition.notify_all()

    for event in cancel_events:
        event.set()
    for proc in processes:
        _stop_process(proc)

    deadline = time.monotonic() + timeout
    while True:
        with state.condition:
            if state.active_operations == 0 and not state.processes:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            state.condition.wait(timeout=min(0.25, remaining))

    with state.condition:
        processes = list(state.processes)

    for proc in processes:
        _stop_process(proc, kill=True)
        _wait_for_process_shutdown(proc, timeout=1.0)

    deadline = time.monotonic() + 2.0
    while True:
        with state.condition:
            if state.active_operations == 0 and not state.processes:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"Job {job_id} is still busy and could not be deleted"
                )
            state.condition.wait(timeout=min(0.25, remaining))


def _remove_tree_with_retries(job_dir: str) -> None:
    for attempt in range(_DELETE_RETRY_ATTEMPTS):
        try:
            shutil.rmtree(job_dir)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if attempt == _DELETE_RETRY_ATTEMPTS - 1:
                raise RuntimeError(f"Failed to delete {job_dir}: {exc}") from exc
            time.sleep(_DELETE_RETRY_DELAY * (attempt + 1))


def get_or_transcode_preview(
    filepath: str,
    job_id: str,
) -> str:
    """Return path to a seekable transcoded master preview, creating it if needed.

    The master preview includes ALL audio tracks so that per-track remuxes
    can be created instantly without re-encoding.  Stored in the job directory
    so it gets cleaned up with the job.
    """
    cancel_event = threading.Event()
    _begin_job_operation(job_id, cancel_event)
    try:
        job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)

        suffix = _preview_cache_key(filepath)
        status_key = _preview_status_key(filepath, job_id)
        preview_path = os.path.join(job_dir, f"preview_{suffix}.mp4")

        if os.path.isfile(preview_path):
            _set_preview_status(
                status_key,
                {
                    "state": "done",
                    "ready": True,
                    "percent": 100.0,
                    "eta_seconds": 0.0,
                    "elapsed_seconds": 0.0,
                    "message": "",
                    "updated_at": time.time(),
                },
            )
            return preview_path

        with _get_preview_build_lock(preview_path):
            if os.path.isfile(preview_path):
                return preview_path
            if cancel_event.is_set():
                raise RuntimeError(f"Preview transcode cancelled for job {job_id}")

            info = probe_file(filepath)
            has_video = info.get("video_codec") is not None
            duration = max(0.0, float(info.get("duration", 0.0) or 0.0))

            video_codec = str(info.get("video_codec") or "").lower()
            needs_video_reencode = has_video and video_codec not in _BROWSER_VIDEO_CODECS

            cmd = ["ffmpeg", "-nostdin", "-loglevel", "warning", "-stats", "-y"]
            if needs_video_reencode:
                cmd += get_hwaccel_input_args()
            cmd += ["-i", filepath]

            if has_video:
                cmd += ["-map", "0:v:0", "-map", "0:a?"]
                if video_codec in _BROWSER_VIDEO_CODECS:
                    cmd += ["-c:v", "copy"]
                else:
                    cmd += build_video_encode_args(
                        "libx264", crf="23", preset=_PREVIEW_X264_PRESET, pix_fmt="yuv420p",
                    )
                    cmd += ["-threads", _PREVIEW_MAX_THREADS]
            else:
                cmd += ["-map", "0:a", "-vn"]

            audio_streams = info.get("audio_streams", [])
            for i, stream in enumerate(audio_streams):
                codec = stream.get("codec", "unknown").lower()
                if codec in _PASSTHROUGH_CODECS:
                    cmd += [f"-c:a:{i}", "copy"]
                else:
                    cmd += [f"-c:a:{i}", "aac", f"-b:a:{i}", "192k"]
                    # AAC doesn't support height/object channels (e.g. 5.1.2
                    # Atmos layouts), so downmix to stereo when channels > 6.
                    channels = stream.get("channels", 0)
                    if channels > 6:
                        cmd += [f"-ac:a:{i}", "2"]

            if not audio_streams:
                audio_codec = info.get("audio_codec", "unknown").lower()
                if audio_codec in _PASSTHROUGH_CODECS:
                    cmd += ["-c:a", "copy"]
                else:
                    cmd += ["-c:a", "aac", "-b:a", "192k"]

            tmp_path = f"{preview_path}.{uuid.uuid4().hex}.tmp"
            cmd += [
                "-sn",
                "-f",
                "mp4",
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                tmp_path,
            ]

            start_ts = time.monotonic()
            _set_preview_status(
                status_key,
                {
                    "state": "running",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": 0.0,
                    "message": "Starting preview transcode...",
                    "updated_at": time.time(),
                },
            )

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
            _register_job_process(job_id, proc)

            stderr_lines: list[str] = []
            stdout_lines: list[str] = []

            try:
                while True:
                    if cancel_event.is_set() and proc.poll() is None:
                        proc.terminate()
                    line = proc.stderr.readline() if proc.stderr else ""
                    if not line:
                        if proc.poll() is not None:
                            break
                        continue
                    stderr_lines.append(line.rstrip())
                    out_seconds = _seconds_from_ffmpeg_time(line)
                    if out_seconds is None or duration <= 0:
                        continue
                    elapsed = max(0.001, time.monotonic() - start_ts)
                    speed = out_seconds / elapsed
                    progress_ratio = max(0.0, min(1.0, out_seconds / duration))

                    # ffmpeg can report media time at/near full duration before mux/finalize
                    # work is complete; keep ETA and percent conservative until done is real.
                    if progress_ratio >= 0.995:
                        eta_seconds = None
                        percent = 99.0
                        message = "Finalizing preview file"
                    else:
                        remaining = max(0.0, duration - out_seconds)
                        eta_seconds = remaining / speed if speed > 0 else None
                        percent = max(0.0, min(98.9, progress_ratio * 100.0))
                        message = "Transcoding preview"

                    _set_preview_status(
                        status_key,
                        {
                            "state": "running",
                            "ready": False,
                            "percent": percent,
                            "eta_seconds": eta_seconds,
                            "elapsed_seconds": elapsed,
                            "message": message,
                            "updated_at": time.time(),
                        },
                    )
                if proc.stderr:
                    remaining_stderr = proc.stderr.read()
                    if remaining_stderr:
                        stderr_lines.append(remaining_stderr.rstrip())
                if proc.stdout:
                    stdout_blob = proc.stdout.read()
                    if stdout_blob:
                        stdout_lines.append(stdout_blob.rstrip())
                proc.wait(timeout=600)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": f"Preview transcode timed out: {exc}",
                        "updated_at": time.time(),
                    },
                )
                _safe_remove_file(tmp_path)
                raise RuntimeError(f"Preview transcode timed out: {exc}") from exc
            finally:
                _unregister_job_process(job_id, proc)
                _close_pipe(proc.stdout)
                _close_pipe(proc.stderr)

            if cancel_event.is_set():
                _safe_remove_file(tmp_path)
                message = (
                    f"Preview transcode cancelled because job {job_id} is being deleted"
                )
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": message,
                        "updated_at": time.time(),
                    },
                )
                raise RuntimeError(message)

            if proc.returncode != 0:
                _safe_remove_file(tmp_path)
                detail = _compact_process_error(
                    "\n".join(stderr_lines), "\n".join(stdout_lines)
                )
                logger.error(
                    "Preview transcode failed (exit %d): %s\nCommand: %s",
                    proc.returncode,
                    detail,
                    subprocess.list2cmdline(cmd),
                )
                message = f"Preview transcode failed (exit {proc.returncode}): {detail}"
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": message,
                        "updated_at": time.time(),
                    },
                )
                raise RuntimeError(message)

            _set_preview_status(
                status_key,
                {
                    "state": "running",
                    "ready": False,
                    "percent": 99.5,
                    "eta_seconds": None,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": "Finalizing preview file",
                    "updated_at": time.time(),
                },
            )

            for attempt in range(5):
                try:
                    os.replace(tmp_path, preview_path)
                    _set_preview_status(
                        status_key,
                        {
                            "state": "done",
                            "ready": True,
                            "percent": 100.0,
                            "eta_seconds": 0.0,
                            "elapsed_seconds": time.monotonic() - start_ts,
                            "message": "",
                            "updated_at": time.time(),
                        },
                    )
                    return preview_path
                except PermissionError as exc:
                    if os.path.isfile(preview_path):
                        _safe_remove_file(tmp_path)
                        _set_preview_status(
                            status_key,
                            {
                                "state": "done",
                                "ready": True,
                                "percent": 100.0,
                                "eta_seconds": 0.0,
                                "elapsed_seconds": time.monotonic() - start_ts,
                                "message": "",
                                "updated_at": time.time(),
                            },
                        )
                        return preview_path
                    if attempt == 4:
                        _safe_remove_file(tmp_path)
                        _set_preview_status(
                            status_key,
                            {
                                "state": "error",
                                "ready": False,
                                "percent": 0.0,
                                "eta_seconds": None,
                                "elapsed_seconds": time.monotonic() - start_ts,
                                "message": f"Preview finalize failed for {preview_path}: {exc}",
                                "updated_at": time.time(),
                            },
                        )
                        raise RuntimeError(
                            f"Preview finalize failed for {preview_path}: {exc}"
                        ) from exc
                    time.sleep(0.1 * (attempt + 1))

        return preview_path
    finally:
        _end_job_operation(job_id, cancel_event)


def get_track_preview(
    master_path: str,
    audio_stream_index: int,
    filepath: str,
    job_id: str,
) -> str:
    """Fast stream-copy remux selecting a single audio track from the master preview.

    Returns a cached per-track file.  Since the master already contains
    browser-compatible codecs this is a pure remux (no re-encoding).
    """
    cancel_event = threading.Event()
    _begin_job_operation(job_id, cancel_event)
    try:
        suffix = _preview_cache_key(filepath)
        info = probe_file(filepath)
        rel = _audio_relative_index(info.get("audio_streams", []), audio_stream_index)
        job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        track_path = os.path.join(
            job_dir, f"preview_{suffix}_trackabs{audio_stream_index}.mp4"
        )

        if os.path.isfile(track_path):
            return track_path

        with _get_preview_build_lock(track_path):
            # Avoid duplicate expensive remuxes when multiple browser range requests
            # arrive before the first remux has finished.
            if os.path.isfile(track_path):
                return track_path

            tmp_path = f"{track_path}.{uuid.uuid4().hex}.tmp.mp4"
            cmd = [
                "ffmpeg",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                master_path,
                "-map",
                "0:v?",
                "-map",
                f"0:a:{rel}",
                "-c",
                "copy",
                "-movflags",
                "frag_keyframe+empty_moov",
                "-f",
                "mp4",
                tmp_path,
            ]

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _register_job_process(job_id, proc)
            try:
                monitor = threading.Thread(
                    target=_monitor_cancel, args=(proc, cancel_event), daemon=True
                )
                monitor.start()
                try:
                    stdout_blob, stderr_blob = proc.communicate(timeout=300)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout_blob, stderr_blob = proc.communicate(timeout=10)
            finally:
                _unregister_job_process(job_id, proc)
                _close_pipe(proc.stdout)
                _close_pipe(proc.stderr)

            if cancel_event.is_set():
                _safe_remove_file(tmp_path)
                raise RuntimeError(
                    f"Track preview remux cancelled because job {job_id} is being deleted"
                )

            if proc.returncode != 0:
                _safe_remove_file(tmp_path)
                raise RuntimeError(
                    f"Track remux failed: {stderr_blob.decode(errors='replace')}"
                )

            for attempt in range(5):
                try:
                    os.replace(tmp_path, track_path)
                    return track_path
                except PermissionError as exc:
                    if os.path.isfile(track_path):
                        _safe_remove_file(tmp_path)
                        return track_path
                    if attempt == 4:
                        _safe_remove_file(tmp_path)
                        raise RuntimeError(
                            f"Track preview finalize failed for {track_path}: {exc}"
                        ) from exc
                    time.sleep(0.1 * (attempt + 1))

        return track_path
    finally:
        _end_job_operation(job_id, cancel_event)


def get_audio_track_preview(
    master_path: str,
    audio_stream_index: int,
    filepath: str,
    job_id: str,
) -> str:
    """Extract a single audio track from the master preview (no video).

    Returns a cached audio-only MP4 file.  Much faster than
    ``get_track_preview`` because it skips the multi-GB video stream copy.
    """
    cancel_event = threading.Event()
    _begin_job_operation(job_id, cancel_event)
    try:
        suffix = _preview_cache_key(filepath)
        info = probe_file(filepath)
        rel = _audio_relative_index(info.get("audio_streams", []), audio_stream_index)
        job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        audio_path = os.path.join(
            job_dir, f"preview_{suffix}_audioabs{audio_stream_index}.mp4"
        )

        if os.path.isfile(audio_path):
            return audio_path

        with _get_preview_build_lock(audio_path):
            if os.path.isfile(audio_path):
                return audio_path

            tmp_path = f"{audio_path}.{uuid.uuid4().hex}.tmp.mp4"
            cmd = [
                "ffmpeg",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                master_path,
                "-map",
                f"0:a:{rel}",
                "-vn",
                "-c",
                "copy",
                "-f",
                "mp4",
                "-movflags",
                "+faststart",
                tmp_path,
            ]

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _register_job_process(job_id, proc)
            try:
                monitor = threading.Thread(
                    target=_monitor_cancel, args=(proc, cancel_event), daemon=True
                )
                monitor.start()
                try:
                    stdout_blob, stderr_blob = proc.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout_blob, stderr_blob = proc.communicate(timeout=10)
            finally:
                _unregister_job_process(job_id, proc)
                _close_pipe(proc.stdout)
                _close_pipe(proc.stderr)

            if cancel_event.is_set():
                _safe_remove_file(tmp_path)
                raise RuntimeError(
                    f"Audio track extraction cancelled because job {job_id} is being deleted"
                )

            if proc.returncode != 0:
                _safe_remove_file(tmp_path)
                raise RuntimeError(
                    f"Audio track extraction failed: {stderr_blob.decode(errors='replace')}"
                )

            for attempt in range(5):
                try:
                    os.replace(tmp_path, audio_path)
                    return audio_path
                except PermissionError as exc:
                    if os.path.isfile(audio_path):
                        _safe_remove_file(tmp_path)
                        return audio_path
                    if attempt == 4:
                        _safe_remove_file(tmp_path)
                        raise RuntimeError(
                            f"Audio track finalize failed for {audio_path}: {exc}"
                        ) from exc
                    time.sleep(0.1 * (attempt + 1))

        return audio_path
    finally:
        _end_job_operation(job_id, cancel_event)


def transcode_audio_track_from_source(
    filepath: str,
    audio_stream_index: int,
    job_id: str,
) -> str:
    """Transcode a single audio track from the source file to AAC MP4.

    Unlike ``get_audio_track_preview`` (which extracts from a master preview),
    this works directly on the source file — no master preview needed.
    Returns the path to the cached audio-only MP4.
    """
    cancel_event = threading.Event()
    _begin_job_operation(job_id, cancel_event)
    try:
        info = probe_file(filepath)
        duration = max(0.0, float(info.get("duration", 0.0) or 0.0))
        audio_streams = info.get("audio_streams", [])
        rel = _audio_relative_index(audio_streams, audio_stream_index)

        # Find channel count for this stream
        channels = 0
        for s in audio_streams:
            if s.get("index") == audio_stream_index:
                channels = s.get("channels", 0)
                break

        suffix = _preview_cache_key(filepath)
        job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        audio_path = os.path.join(
            job_dir, f"preview_{suffix}_srcaudio{audio_stream_index}.mp4"
        )
        status_key = _audio_transcode_status_key(filepath, job_id, audio_stream_index)

        if os.path.isfile(audio_path):
            _set_preview_status(
                status_key,
                {
                    "state": "done",
                    "ready": True,
                    "percent": 100.0,
                    "eta_seconds": 0.0,
                    "elapsed_seconds": 0.0,
                    "message": "",
                    "updated_at": time.time(),
                },
            )
            return audio_path

        with _get_preview_build_lock(audio_path):
            if os.path.isfile(audio_path):
                return audio_path
            if cancel_event.is_set():
                raise RuntimeError(
                    f"Audio transcode cancelled for job {job_id}"
                )

            cmd = [
                "ffmpeg", "-nostdin", "-loglevel", "warning", "-stats", "-y",
                "-i", filepath,
                "-map", f"0:a:{rel}",
                "-vn",
                "-c:a", "aac", "-b:a", "192k",
            ]
            if channels > 6:
                cmd += ["-ac", "2"]

            tmp_path = f"{audio_path}.{uuid.uuid4().hex}.tmp"
            cmd += ["-f", "mp4", "-movflags", "+faststart", tmp_path]

            start_ts = time.monotonic()
            _set_preview_status(
                status_key,
                {
                    "state": "running",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": 0.0,
                    "message": "Starting audio transcode...",
                    "updated_at": time.time(),
                },
            )

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
            _register_job_process(job_id, proc)

            stderr_lines: list[str] = []

            try:
                while True:
                    if cancel_event.is_set() and proc.poll() is None:
                        proc.terminate()
                    line = proc.stderr.readline() if proc.stderr else ""
                    if not line:
                        if proc.poll() is not None:
                            break
                        continue
                    stderr_lines.append(line.rstrip())
                    out_seconds = _seconds_from_ffmpeg_time(line)
                    if out_seconds is None or duration <= 0:
                        continue
                    elapsed = max(0.001, time.monotonic() - start_ts)
                    speed = out_seconds / elapsed
                    progress_ratio = max(0.0, min(1.0, out_seconds / duration))

                    if progress_ratio >= 0.995:
                        eta_seconds = None
                        percent = 99.0
                        message = "Finalizing audio file"
                    else:
                        remaining = max(0.0, duration - out_seconds)
                        eta_seconds = remaining / speed if speed > 0 else None
                        percent = max(0.0, min(98.9, progress_ratio * 100.0))
                        message = "Transcoding audio"

                    _set_preview_status(
                        status_key,
                        {
                            "state": "running",
                            "ready": False,
                            "percent": percent,
                            "eta_seconds": eta_seconds,
                            "elapsed_seconds": elapsed,
                            "message": message,
                            "updated_at": time.time(),
                        },
                    )
                if proc.stderr:
                    remaining_stderr = proc.stderr.read()
                    if remaining_stderr:
                        stderr_lines.append(remaining_stderr.rstrip())
                proc.wait(timeout=300)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": f"Audio transcode timed out: {exc}",
                        "updated_at": time.time(),
                    },
                )
                _safe_remove_file(tmp_path)
                raise RuntimeError(f"Audio transcode timed out: {exc}") from exc
            finally:
                _unregister_job_process(job_id, proc)
                _close_pipe(proc.stdout)
                _close_pipe(proc.stderr)

            if cancel_event.is_set():
                _safe_remove_file(tmp_path)
                message = f"Audio transcode cancelled because job {job_id} is being deleted"
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": message,
                        "updated_at": time.time(),
                    },
                )
                raise RuntimeError(message)

            if proc.returncode != 0:
                _safe_remove_file(tmp_path)
                detail = _compact_process_error("\n".join(stderr_lines), "")
                logger.error(
                    "Audio transcode failed (exit %d): %s\nCommand: %s",
                    proc.returncode,
                    detail,
                    subprocess.list2cmdline(cmd),
                )
                message = f"Audio transcode failed (exit {proc.returncode}): {detail}"
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": message,
                        "updated_at": time.time(),
                    },
                )
                raise RuntimeError(message)

            for attempt in range(5):
                try:
                    os.replace(tmp_path, audio_path)
                    break
                except PermissionError as exc:
                    if os.path.isfile(audio_path):
                        _safe_remove_file(tmp_path)
                        break
                    if attempt == 4:
                        _safe_remove_file(tmp_path)
                        message = f"Audio transcode finalize failed for {audio_path}: {exc}"
                        _set_preview_status(
                            status_key,
                            {
                                "state": "error",
                                "ready": False,
                                "percent": 0.0,
                                "eta_seconds": None,
                                "elapsed_seconds": time.monotonic() - start_ts,
                                "message": message,
                                "updated_at": time.time(),
                            },
                        )
                        raise RuntimeError(message) from exc
                    time.sleep(0.1 * (attempt + 1))

            _set_preview_status(
                status_key,
                {
                    "state": "done",
                    "ready": True,
                    "percent": 100.0,
                    "eta_seconds": 0.0,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": "",
                    "updated_at": time.time(),
                },
            )

            # Record transcoded track in job metadata
            try:
                meta = load_job_metadata(job_id)
                if meta is not None:
                    tracks = meta.get("audio_transcoded_tracks", [])
                    if audio_stream_index not in tracks:
                        tracks.append(audio_stream_index)
                        meta["audio_transcoded_tracks"] = tracks
                        save_job_metadata(job_id, meta)
            except Exception:
                logger.debug("Could not update audio_transcoded_tracks for job %s", job_id, exc_info=True)

            return audio_path

        return audio_path
    finally:
        _end_job_operation(job_id, cancel_event)


def get_track_remux(
    filepath: str,
    audio_stream_index: int,
    job_id: str,
) -> str:
    """Stream-copy remux selecting a single audio track from the original file.

    Keeps the original container/extension for browser-compatible sources and
    caches the output per track to avoid repeated work.
    """
    cancel_event = threading.Event()
    _begin_job_operation(job_id, cancel_event)
    try:
        suffix = _preview_cache_key(filepath)
        info = probe_file(filepath)
        rel = _audio_relative_index(info.get("audio_streams", []), audio_stream_index)
        job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)

        ext = os.path.splitext(filepath)[1].lower()
        force_format: str | None = None
        if not ext:
            ext = ".mp4"
            force_format = "mp4"

        track_path = os.path.join(
            job_dir, f"preview_{suffix}_trackabs{audio_stream_index}{ext}"
        )

        if os.path.isfile(track_path):
            return track_path

        with _get_preview_build_lock(track_path):
            if os.path.isfile(track_path):
                return track_path

            tmp_path = f"{track_path}.{uuid.uuid4().hex}.tmp{ext}"
            cmd = [
                "ffmpeg",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                filepath,
                "-map",
                "0:v?",
                "-map",
                f"0:a:{rel}",
                "-c",
                "copy",
                "-sn",
            ]
            if ext in {".mp4", ".m4a", ".m4v", ".mov"}:
                cmd += ["-movflags", "+faststart"]
            if force_format:
                cmd += ["-f", force_format]
            cmd.append(tmp_path)

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _register_job_process(job_id, proc)
            try:
                monitor = threading.Thread(
                    target=_monitor_cancel, args=(proc, cancel_event), daemon=True
                )
                monitor.start()
                try:
                    stdout_blob, stderr_blob = proc.communicate(timeout=300)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout_blob, stderr_blob = proc.communicate(timeout=10)
            finally:
                _unregister_job_process(job_id, proc)
                _close_pipe(proc.stdout)
                _close_pipe(proc.stderr)

            if cancel_event.is_set():
                _safe_remove_file(tmp_path)
                raise RuntimeError(
                    f"Track remux cancelled because job {job_id} is being deleted"
                )

            if proc.returncode != 0:
                _safe_remove_file(tmp_path)
                raise RuntimeError(
                    f"Track remux failed: {stderr_blob.decode(errors='replace')}"
                )

            for attempt in range(5):
                try:
                    os.replace(tmp_path, track_path)
                    return track_path
                except PermissionError as exc:
                    if os.path.isfile(track_path):
                        _safe_remove_file(tmp_path)
                        return track_path
                    if attempt == 4:
                        _safe_remove_file(tmp_path)
                        raise RuntimeError(
                            f"Track remux finalize failed for {track_path}: {exc}"
                        ) from exc
                    time.sleep(0.1 * (attempt + 1))

        return track_path
    finally:
        _end_job_operation(job_id, cancel_event)


# Track in-progress background transcodes to avoid duplicates
_transcode_locks: dict[str, threading.Event] = {}
_transcode_lock_guard = threading.Lock()
_preview_build_locks: dict[str, threading.Lock] = {}
_preview_build_lock_guard = threading.Lock()
_preview_status: dict[str, dict] = {}
_preview_status_guard = threading.Lock()
_job_activity: dict[str, _JobActivityState] = {}
_job_activity_guard = threading.Lock()
_job_meta_locks: dict[str, threading.Lock] = {}
_job_meta_lock_guard = threading.Lock()
_transcode_semaphore = threading.Semaphore(3)


def get_job_meta_lock(job_id: str) -> threading.Lock:
    """Get or create a per-job lock for metadata access."""
    with _job_meta_lock_guard:
        return _job_meta_locks.setdefault(job_id, threading.Lock())


def _set_preview_status(status_key: str, payload: dict) -> None:
    with _preview_status_guard:
        current = _preview_status.get(status_key, {})
        current.update(payload)
        _preview_status[status_key] = current


def get_preview_status(filepath: str, job_id: str) -> dict:
    status_key = _preview_status_key(filepath, job_id)
    with _preview_status_guard:
        status = dict(_preview_status.get(status_key, {}))

    ready_path = get_preview_path_if_ready(filepath, job_id)
    if ready_path:
        status.update(
            {
                "state": "done",
                "ready": True,
                "percent": 100.0,
                "eta_seconds": 0.0,
                "message": "",
                "updated_at": time.time(),
            }
        )
    else:
        status.setdefault("state", "idle")
        status.setdefault("ready", False)
        status.setdefault("percent", 0.0)
        status.setdefault("eta_seconds", None)
        status.setdefault("elapsed_seconds", 0.0)
        status.setdefault("message", "")
        status.setdefault("updated_at", time.time())
    return status


def _get_preview_build_lock(preview_path: str) -> threading.Lock:
    """Return a stable per-preview lock to serialize transcode writes."""
    with _preview_build_lock_guard:
        lock = _preview_build_locks.get(preview_path)
        if lock is None:
            lock = threading.Lock()
            _preview_build_locks[preview_path] = lock
        return lock


def start_background_transcode(
    filepath: str,
    job_id: str,
) -> None:
    """Kick off a background transcode so the master preview is ready for seeking."""
    preview_path = _preview_file_path(filepath, job_id)

    # Already done
    if os.path.isfile(preview_path):
        meta = load_job_metadata(job_id)
        if meta and not meta.get("preview_transcoded"):
            meta["preview_transcoded"] = True
            meta.pop("transcode_error", None)
            save_job_metadata(job_id, meta)
        return

    with _transcode_lock_guard:
        if preview_path in _transcode_locks:
            return  # Already in progress
        event = threading.Event()
        _transcode_locks[preview_path] = event

    status_key = _preview_status_key(filepath, job_id)
    _set_preview_status(
        status_key,
        {
            "state": "running",
            "ready": False,
            "percent": 0.0,
            "eta_seconds": None,
            "elapsed_seconds": 0.0,
            "message": "Queued for preview transcode",
            "updated_at": time.time(),
        },
    )

    # Mark job as transcoding while the preview is being built
    _jmeta = load_job_metadata(job_id)
    if _jmeta and _jmeta.get("status") == "ready":
        _jmeta["status"] = "full_transcoding"
        _jmeta["preview_transcoded"] = False
        _jmeta.pop("transcode_error", None)
        save_job_metadata(job_id, _jmeta)

    def _run():
        _transcode_semaphore.acquire()
        try:
            get_or_transcode_preview(filepath, job_id)
            # Transcode succeeded — restore ready status
            _meta = load_job_metadata(job_id)
            if _meta:
                if _meta.get("status") == "full_transcoding":
                    _meta["status"] = "ready"
                _meta["preview_transcoded"] = True
                _meta.pop("transcode_error", None)
                save_job_metadata(job_id, _meta)
        except Exception as exc:
            _set_preview_status(
                status_key,
                {
                    "state": "error",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "message": str(exc),
                    "updated_at": time.time(),
                },
            )
            logger.exception("Background preview transcode failed")
            # Restore ready status but record the error for the UI
            _meta = load_job_metadata(job_id)
            if _meta:
                if _meta.get("status") == "full_transcoding":
                    _meta["status"] = "ready"
                _meta["preview_transcoded"] = False
                _meta["transcode_error"] = str(exc)
                save_job_metadata(job_id, _meta)
        finally:
            _transcode_semaphore.release()
            event.set()
            with _transcode_lock_guard:
                _transcode_locks.pop(preview_path, None)

    threading.Thread(target=_run, daemon=True).start()


def _audio_transcode_file_path(
    filepath: str, job_id: str, audio_stream_index: int
) -> str:
    suffix = _preview_cache_key(filepath)
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    return os.path.join(
        job_dir, f"preview_{suffix}_srcaudio{audio_stream_index}.mp4"
    )


def start_background_audio_transcode(
    filepath: str,
    audio_stream_index: int,
    job_id: str,
) -> None:
    """Kick off a background audio-only transcode for a single track."""
    audio_path = _audio_transcode_file_path(filepath, job_id, audio_stream_index)

    if os.path.isfile(audio_path):
        return

    with _transcode_lock_guard:
        if audio_path in _transcode_locks:
            return  # Already in progress
        event = threading.Event()
        _transcode_locks[audio_path] = event

    status_key = _audio_transcode_status_key(filepath, job_id, audio_stream_index)
    _set_preview_status(
        status_key,
        {
            "state": "running",
            "ready": False,
            "percent": 0.0,
            "eta_seconds": None,
            "elapsed_seconds": 0.0,
            "message": "Queued for audio transcode",
            "updated_at": time.time(),
        },
    )

    # Mark job as transcoding while audio is being built
    _jmeta = load_job_metadata(job_id)
    if _jmeta and _jmeta.get("status") == "ready":
        _jmeta["status"] = "audio_transcoding"
        _jmeta.pop("transcode_error", None)
        save_job_metadata(job_id, _jmeta)

    def _run():
        _transcode_semaphore.acquire()
        try:
            transcode_audio_track_from_source(filepath, audio_stream_index, job_id)
            # Restore ready status on success
            _meta = load_job_metadata(job_id)
            if _meta and _meta.get("status") == "audio_transcoding":
                _meta["status"] = "ready"
                _meta.pop("transcode_error", None)
                save_job_metadata(job_id, _meta)
        except Exception as exc:
            _set_preview_status(
                status_key,
                {
                    "state": "error",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": 0.0,
                    "message": str(exc),
                    "updated_at": time.time(),
                },
            )
            logger.exception("Background audio transcode failed")
            # Restore ready status but record the error
            _meta = load_job_metadata(job_id)
            if _meta:
                if _meta.get("status") == "audio_transcoding":
                    _meta["status"] = "ready"
                _meta["transcode_error"] = str(exc)
                save_job_metadata(job_id, _meta)
        finally:
            _transcode_semaphore.release()
            event.set()
            with _transcode_lock_guard:
                _transcode_locks.pop(audio_path, None)

    threading.Thread(target=_run, daemon=True).start()


def get_audio_transcode_status(
    filepath: str, job_id: str, audio_stream_index: int
) -> dict:
    """Return transcode status for a specific audio track from source."""
    status_key = _audio_transcode_status_key(filepath, job_id, audio_stream_index)
    with _preview_status_guard:
        status = dict(_preview_status.get(status_key, {}))

    # Check if the output file already exists (done)
    audio_path = _audio_transcode_file_path(filepath, job_id, audio_stream_index)
    if os.path.isfile(audio_path):
        status.update(
            {
                "state": "done",
                "ready": True,
                "percent": 100.0,
                "eta_seconds": 0.0,
                "message": "",
                "updated_at": time.time(),
            }
        )
    else:
        status.setdefault("state", "idle")
        status.setdefault("ready", False)
        status.setdefault("percent", 0.0)
        status.setdefault("eta_seconds", None)
        status.setdefault("elapsed_seconds", 0.0)
        status.setdefault("message", "")
        status.setdefault("updated_at", time.time())
    return status


def wait_for_audio_transcode(
    filepath: str, job_id: str, audio_stream_index: int, timeout: float = 120
) -> str | None:
    """Wait for a background audio transcode to finish. Returns path or None."""
    audio_path = _audio_transcode_file_path(filepath, job_id, audio_stream_index)

    with _transcode_lock_guard:
        event = _transcode_locks.get(audio_path)

    if event:
        event.wait(timeout=timeout)

    return audio_path if os.path.isfile(audio_path) else None


def get_preview_path_if_ready(
    filepath: str,
    job_id: str,
) -> str | None:
    """Return the master preview file path if it exists, or None."""
    preview_path = _preview_file_path(filepath, job_id)
    return preview_path if os.path.isfile(preview_path) else None



def cut_file(
    filepath: str,
    in_point: float,
    out_point: float,
    output_path: str,
    stream_copy: bool,
    codec: Optional[str],
    audio_tracks: list[dict] | None,
    container: Optional[str],
    progress_cb: Callable[[str], None],
    keep_quality: bool = False,
    source_video_bitrate: int | None = None,
    source_audio_bitrates: dict[int, int] | None = None,
    audio_streams: list[dict] | None = None,
    audio_stream_index: int | None = None,
    audio_codec: Optional[str] = None,
    job_id: str | None = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Cut a segment from a media file using ffmpeg.

    Args:
        filepath: Source file path.
        in_point: Start time in seconds.
        out_point: End time in seconds.
        output_path: Desired output file path.
        stream_copy: If True, use -c copy (no re-encoding).
        codec: Target codec name (video codec for video, audio codec for audio-only).
        audio_tracks: Per-track audio output settings. If None, uses legacy behavior.
        container: Target container format (used for output extension).
        progress_cb: Callback for progress messages.
        keep_quality: Preserve source bitrate when re-encoding where available.
        source_video_bitrate: Optional source video bitrate from ffprobe.
        source_audio_bitrates: Optional map of source audio stream index -> bitrate.
        audio_streams: Optional source audio stream list from ffprobe.
        audio_stream_index: Absolute ffprobe audio stream index to map.
        audio_codec: Legacy single audio codec for video re-encode path.
        job_id: Optional cutter job id for cancellation/cleanup tracking.
        cancel_event: Optional event used to cancel long-running ffmpeg tasks.

    Returns:
        The final output file path (may differ from output_path if collision).
    """
    # FLAC stream-copy frequently preserves stale container duration metadata.
    # Re-encoding flac->flac avoids "full-length" phantom tails in output.
    ext = os.path.splitext(filepath)[1].lower()
    if stream_copy and ext == ".flac":
        stream_copy = False
        codec = "flac"
        if not container:
            container = "flac"
        if audio_tracks:
            audio_tracks = [
                (
                    {**track, "mode": "reencode", "codec": "flac"}
                    if track.get("mode") == "passthru"
                    else track
                )
                for track in audio_tracks
            ]

    output_path = collision_safe_path(output_path)
    duration = out_point - in_point

    if stream_copy:
        # -ss before -i: fast demuxer-level seek (keyframe-granularity, fine for copy)
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "warning",
            "-stats",
            "-ss",
            str(in_point),
            "-t",
            str(duration),
            "-i",
            filepath,
        ]
    else:
        # -ss after -i: decode-level seek (frame-accurate, avoids black frames /
        # distortion at the start when re-encoding)
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "warning", "-stats"]
        cmd += get_hwaccel_input_args()
        cmd += [
            "-i",
            filepath,
            "-ss",
            str(in_point),
            "-t",
            str(duration),
        ]

    if audio_tracks is not None:
        cmd += ["-map", "0:v?"]

        included_tracks = [
            track for track in audio_tracks if track.get("mode") != "remove"
        ]
        probe_streams = audio_streams or []

        for track in included_tracks:
            rel_idx = _audio_relative_index(probe_streams, int(track["index"]))
            cmd += ["-map", f"0:a:{rel_idx}"]
    elif audio_stream_index is not None:
        rel_idx = _audio_relative_index(
            audio_streams or probe_file(filepath).get("audio_streams", []),
            audio_stream_index,
        )
        cmd += ["-map", "0:v?", "-map", f"0:a:{rel_idx}"]

    # Validate codec/container against allowlists
    _valid_codecs = set(_CODEC_TO_ENCODER.keys()) | _VIDEO_ENCODERS
    if codec and codec not in _valid_codecs:
        raise ValueError(f"Invalid codec: '{codec}'. Allowed: {sorted(_valid_codecs)}")
    if audio_tracks is None and audio_codec and audio_codec not in _CODEC_TO_ENCODER:
        raise ValueError(
            f"Invalid audio codec: '{audio_codec}'. Allowed: {sorted(_CODEC_TO_ENCODER.keys())}"
        )
    if container and container not in _VALID_CONTAINERS:
        raise ValueError(
            f"Invalid container: '{container}'. Allowed: {sorted(_VALID_CONTAINERS)}"
        )

    if audio_tracks is not None:
        if stream_copy:
            cmd += ["-c:v", "copy"]
        elif codec:
            encoder = _CODEC_TO_ENCODER.get(codec, codec)
            if encoder in _VIDEO_ENCODERS:
                cmd += ["-c:v", resolve_video_encoder(encoder)]
                if keep_quality and source_video_bitrate and source_video_bitrate > 0:
                    cmd += ["-b:v", str(source_video_bitrate)]

        bitrates = source_audio_bitrates or {}
        for out_idx, track in enumerate(included_tracks):
            mode = track.get("mode")
            if mode == "passthru":
                cmd += [f"-c:a:{out_idx}", "copy"]
            elif mode == "reencode":
                raw_codec = str(track.get("codec") or "aac")
                if raw_codec not in _CODEC_TO_ENCODER:
                    raise ValueError(
                        f"Invalid audio track codec: '{raw_codec}'. "
                        f"Allowed: {sorted(_CODEC_TO_ENCODER.keys())}"
                    )
                enc = _CODEC_TO_ENCODER[raw_codec]
                cmd += [f"-c:a:{out_idx}", enc]
                if keep_quality:
                    br = int(bitrates.get(int(track["index"]), 0) or 0)
                    if br > 0:
                        cmd += [f"-b:a:{out_idx}", str(br)]
    else:
        if stream_copy:
            cmd += ["-c", "copy"]
        else:
            if codec:
                encoder = _CODEC_TO_ENCODER.get(codec, codec)
                if encoder in _VIDEO_ENCODERS:
                    cmd += ["-c:v", resolve_video_encoder(encoder)]
                    # Audio codec for video re-encode: explicit or copy
                    if audio_codec:
                        a_enc = _CODEC_TO_ENCODER.get(audio_codec, audio_codec)
                        cmd += ["-c:a", a_enc]
                    else:
                        cmd += ["-c:a", "copy"]
                else:
                    cmd += ["-c:a", encoder]

    if container:
        ffmpeg_fmt = _CONTAINER_TO_FFMPEG_FORMAT.get(container, container)
        cmd += ["-f", ffmpeg_fmt]

    cmd += ["-y", output_path]

    progress_cb(
        f"Cutting {os.path.basename(filepath)} [{in_point:.2f}s - {out_point:.2f}s]"
    )

    if job_id is not None:
        _begin_job_operation(job_id, cancel_event)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if job_id is not None:
        _register_job_process(job_id, proc)

    try:
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        stderr_lines: list[str] = []

        stderr_iter = iter(proc.stderr.readline, "") if proc.stderr else iter([])
        for line in stderr_iter:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                proc.wait(timeout=10)
                if os.path.isfile(output_path):
                    _safe_remove_file(output_path)
                raise RuntimeError(
                    "Cut cancelled because job was deleted or client disconnected"
                )

            match = time_pattern.search(line)
            if match:
                h, m, s, cs = match.groups()
                current = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
                if duration > 0:
                    pct = min(100.0, (current / duration) * 100)
                    progress_cb(f"Progress: {pct:.1f}%")
            else:
                stripped = line.strip()
                if stripped:
                    stderr_lines.append(stripped)
                    progress_cb(f"[ffmpeg] {stripped}")

        try:
            proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            _safe_remove_file(output_path)
            raise RuntimeError("ffmpeg cut timed out after 1 hour")
        if proc.returncode != 0:
            detail = "; ".join(stderr_lines[-5:]) if stderr_lines else "no details"
            _safe_remove_file(output_path)
            raise RuntimeError(f"ffmpeg cut failed (exit {proc.returncode}): {detail}")

        progress_cb(f"Saved {os.path.basename(output_path)}")
        return output_path
    finally:
        if job_id is not None:
            _unregister_job_process(job_id, proc)
            _end_job_operation(job_id, cancel_event)
        _close_pipe(proc.stdout)
        _close_pipe(proc.stderr)


def generate_thumbnail_strip(filepath: str, count: int = 30) -> bytes:
    """Generate a horizontal sprite sheet of video thumbnails.

    Uses fast keyframe seeking (``-ss`` before ``-i``) for each position,
    so even large files over network shares complete quickly.
    """
    count = min(max(count, 1), 50)  # Cap to avoid excessive ffmpeg inputs/filters
    info = probe_file(filepath)
    duration = info["duration"]
    if duration <= 0:
        raise RuntimeError("Cannot generate thumbnails for zero-duration file")

    # Build command with multiple fast-seek inputs
    cmd = ["ffmpeg", "-loglevel", "warning"]
    for i in range(count):
        pos = (i / count) * duration
        cmd += ["-ss", f"{pos:.3f}", "-i", filepath]

    # Scale each input and tile horizontally
    filters = []
    for i in range(count):
        filters.append(
            f"[{i}:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale=160:-1,setsar=1[t{i}]"
        )
    tile_inputs = "".join(f"[t{i}]" for i in range(count))
    filters.append(f"{tile_inputs}hstack=inputs={count}")

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-q:v",
        "5",
        "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg thumbnail generation timed out: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg thumbnail generation failed: {result.stderr.decode(errors='replace')}"
        )
    return result.stdout


def _thumbnail_cache_key(filepath: str, count: int) -> str:
    mtime = _safe_getmtime(filepath)
    key = f"{filepath}:{mtime}:thumbs:{count}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def generate_thumbnail_strip_cached(filepath: str, count: int, job_id: str) -> bytes:
    """Generate thumbnail strip and cache it under the cutter job directory."""
    job_dir = get_job_dir(job_id)
    cache_name = f"thumbs_{_thumbnail_cache_key(filepath, count)}.jpg"
    cache_path = os.path.join(job_dir, cache_name)

    if os.path.isfile(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()

    with _get_preview_build_lock(cache_path):
        if os.path.isfile(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()

        jpeg = generate_thumbnail_strip(filepath, count=count)
        tmp_path = f"{cache_path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp_path, "wb") as f:
                f.write(jpeg)
            os.replace(tmp_path, cache_path)
        finally:
            if os.path.exists(tmp_path):
                _safe_remove_file(tmp_path)
        return jpeg


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def create_job(
    source: str,
    original_path: str,
    original_name: str,
    initial_status: str = "ready",
    base: str = "",
) -> str:
    """Create a new job directory structure and return the job_id."""
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    os.makedirs(os.path.join(job_dir, "input"), exist_ok=True)
    os.makedirs(os.path.join(job_dir, "output"), exist_ok=True)

    metadata = {
        "schema_version": 1,
        "job_id": job_id,
        "source": source,
        "original_name": original_name,
        "original_path": original_path,
        "base": base,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": initial_status,
        "preview_transcoded": False,
        "audio_transcoded_tracks": [],
        "browser_ready": False,
        "cut_settings": None,
        "output_files": [],
    }
    save_job_metadata(job_id, metadata)
    return job_id


def get_job_dir(job_id: str) -> str:
    """Return validated job directory path. Raises ValueError if invalid."""
    if not _UUID_RE.match(job_id):
        raise ValueError(f"Invalid job_id format: {job_id}")
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        raise ValueError(f"Job not found: {job_id}")
    return job_dir


def save_job_metadata(job_id: str, metadata: dict) -> None:
    """Write job metadata to job.json atomically."""
    if not _UUID_RE.match(job_id):
        raise ValueError(f"Invalid job_id format: {job_id}")
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    final_path = os.path.join(job_dir, "job.json")
    tmp_path = final_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(metadata, f, indent=2)
        os.replace(tmp_path, final_path)
    finally:
        if os.path.exists(tmp_path):
            _safe_remove_file(tmp_path)


def load_job_metadata(job_id: str) -> dict | None:
    """Read job metadata from job.json. Returns None if not found."""
    try:
        job_dir = get_job_dir(job_id)
    except ValueError:
        return None
    meta_path = os.path.join(job_dir, "job.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed job metadata file: %s", meta_path)
        return None


def _resolve_job_source_path(meta: dict) -> str | None:
    source = meta.get("source")
    if source == "upload":
        job_id = meta.get("job_id", "")
        original_name = meta.get("original_name", "")
        if not job_id or not original_name:
            return None
        try:
            job_dir = get_job_dir(job_id)
        except ValueError:
            return None
        return os.path.join(job_dir, "input", original_name)

    if source == "server":
        base_label = meta.get("base", "")
        try:
            base_path = resolve_base(base_label)
        except ValueError:
            return None
        original_path = meta.get("original_path", "")
        if not original_path:
            return None
        if os.path.isabs(original_path):
            base_real = os.path.realpath(base_path)
            path_real = os.path.realpath(original_path)
            if path_real == base_real or path_real.startswith(base_real + os.sep):
                return path_real
            return None
        resolved = os.path.realpath(os.path.join(base_path, original_path))
        base_real = os.path.realpath(base_path)
        if resolved == base_real or resolved.startswith(base_real + os.sep):
            return resolved
        return None

    return None


def _infer_browser_ready(meta: dict) -> bool:
    source_path = _resolve_job_source_path(meta)
    if not source_path or not os.path.isfile(source_path):
        return False
    info = probe_file(source_path)
    return not needs_transcoding(info.get("audio_codec", "unknown"), source_path)


def list_jobs() -> list[dict]:
    """List all jobs sorted by created_at descending."""
    jobs = []
    if not os.path.isdir(CUTTER_JOBS_DIR):
        return jobs
    for name in os.listdir(CUTTER_JOBS_DIR):
        if not _UUID_RE.match(name):
            continue
        meta = load_job_metadata(name)
        if meta:
            if "browser_ready" not in meta:
                try:
                    meta["browser_ready"] = _infer_browser_ready(meta)
                    save_job_metadata(name, meta)
                except Exception:
                    logger.warning(
                        "Failed to infer browser_ready for job %s",
                        name,
                        exc_info=True,
                    )
                    meta["browser_ready"] = False
            jobs.append(meta)
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


def _infer_base_label(meta: dict, default: str) -> str:
    """Try to infer the correct base label from original_path; fall back to *default*."""
    original_path = meta.get("original_path", "")
    if original_path and os.path.isabs(original_path):
        real_path = os.path.realpath(original_path)
        for label, base_path in BASE_PATH_LABELS.items():
            real_base = os.path.realpath(base_path)
            if real_path == real_base or real_path.startswith(real_base + os.sep):
                return label
    return default


def migrate_jobs() -> int:
    """Migrate all job.json files to the current schema. Returns count of migrated jobs."""
    if not os.path.isdir(CUTTER_JOBS_DIR):
        return 0

    default_base = next(iter(BASE_PATH_LABELS), "")
    migrated = 0

    for name in os.listdir(CUTTER_JOBS_DIR):
        if not _UUID_RE.match(name):
            continue
        meta = load_job_metadata(name)
        if not meta:
            continue

        version = meta.get("schema_version", 0)
        if version >= 1:
            continue

        # --- v0 -> v1: add "base" field ---
        if not meta.get("base"):
            meta["base"] = _infer_base_label(meta, default_base)

        meta["schema_version"] = 1
        # Ensure schema_version appears first in the JSON output
        meta = {"schema_version": meta.pop("schema_version"), **meta}

        try:
            save_job_metadata(name, meta)
            migrated += 1
            logger.debug("Migrated job %s to schema v1 (base=%s)", name, meta["base"])
        except OSError:
            logger.warning(
                "Failed to write migrated metadata for job %s", name, exc_info=True
            )

    if migrated:
        logger.info("Migrated %d cutter job(s) to current schema", migrated)
    return migrated


def delete_job(job_id: str) -> None:
    """Delete a job and all its files."""
    job_dir = get_job_dir(job_id)
    _cancel_job_operations(job_id)
    _remove_tree_with_retries(job_dir)
    _clear_job_runtime_state(job_id)


def cleanup_old_jobs() -> None:
    """Remove jobs older than CUTTER_JOB_TTL seconds."""
    if not os.path.isdir(CUTTER_JOBS_DIR):
        return
    now = datetime.now(timezone.utc)
    for name in os.listdir(CUTTER_JOBS_DIR):
        if not _UUID_RE.match(name):
            continue
        job_dir = os.path.join(CUTTER_JOBS_DIR, name)
        meta_path = os.path.join(job_dir, "job.json")
        try:
            if _job_has_active_operations(name):
                continue
            if os.path.isfile(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
                created = datetime.fromisoformat(meta["created_at"])
                if (now - created).total_seconds() > CUTTER_JOB_TTL:
                    delete_job(name)
                    logger.info("Cleaned up expired job %s", name)
            else:
                # No metadata — check dir mtime
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(job_dir), tz=timezone.utc
                )
                if (now - mtime).total_seconds() > CUTTER_JOB_TTL:
                    delete_job(name)
        except Exception:
            logger.warning("Error checking job %s for cleanup", name, exc_info=True)

    # Clean up stale in-memory state for job IDs that no longer have directories
    active_job_ids = set()
    if os.path.isdir(CUTTER_JOBS_DIR):
        for name in os.listdir(CUTTER_JOBS_DIR):
            if _UUID_RE.match(name):
                active_job_ids.add(name)

    with _preview_build_lock_guard:
        stale_keys = [
            key
            for key in _preview_build_locks
            if not any(
                os.path.normcase(key).startswith(
                    os.path.normcase(os.path.join(CUTTER_JOBS_DIR, jid) + os.sep)
                )
                for jid in active_job_ids
            )
        ]
        for key in stale_keys:
            _preview_build_locks.pop(key, None)

    with _preview_status_guard:
        stale_keys = [
            key
            for key in _preview_status
            if ":" in key and key.split(":", 1)[0] not in active_job_ids
        ]
        for key in stale_keys:
            _preview_status.pop(key, None)

    with _transcode_lock_guard:
        stale_keys = [
            key
            for key in _transcode_locks
            if not any(
                os.path.normcase(key).startswith(
                    os.path.normcase(os.path.join(CUTTER_JOBS_DIR, jid) + os.sep)
                )
                for jid in active_job_ids
            )
        ]
        for key in stale_keys:
            _transcode_locks.pop(key, None)

    with _job_meta_lock_guard:
        stale_keys = [jid for jid in _job_meta_locks if jid not in active_job_ids]
        for key in stale_keys:
            _job_meta_locks.pop(key, None)

    # Warn if in-memory state dictionaries are growing unexpectedly large
    _DICT_SIZE_WARN_THRESHOLD = 500
    for name, d in [
        ("_preview_build_locks", _preview_build_locks),
        ("_preview_status", _preview_status),
        ("_transcode_locks", _transcode_locks),
        ("_job_meta_locks", _job_meta_locks),
        ("_job_activity", _job_activity),
    ]:
        if len(d) > _DICT_SIZE_WARN_THRESHOLD:
            logger.warning(
                "Cutter runtime dict %s has %d entries (threshold %d) — "
                "possible memory leak",
                name,
                len(d),
                _DICT_SIZE_WARN_THRESHOLD,
            )


def encode_file_id(source: str, path: str, job_id: str = "", base: str = "") -> str:
    """URL-safe base64 encode of 'source|job_id|base|path'."""
    raw = f"{source}|{job_id}|{base}|{path}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_file_id(file_id: str) -> tuple[str, str, str, str]:
    """Decode a file_id back to (source, job_id, base, path). Raises ValueError on invalid input.

    Security note: This function performs NO path validation. Callers MUST
    validate the returned path against allowed base directories before use
    (e.g., via ``validate_path()``) to prevent directory traversal attacks.
    """
    try:
        # Re-add padding stripped by the frontend (btoa → strip '=')
        padding = 4 - len(file_id) % 4
        if padding != 4:
            file_id += "=" * padding
        decoded = base64.urlsafe_b64decode(file_id.encode("ascii")).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Invalid file_id: {e}") from e

    # Try new format first (pipe-separated): "source|job_id|base|path"
    if "|" in decoded:
        parts = decoded.split("|", 3)
        if len(parts) == 4:
            return parts[0], parts[1], parts[2], parts[3]
        raise ValueError("Invalid file_id format: expected 'source|job_id|base|path'")

    # Legacy colon-separated formats (no Windows paths)
    parts = decoded.split(":", 3)
    if len(parts) == 2:
        # Legacy format: "source:path"
        return parts[0], "", "", parts[1]
    if len(parts) == 3:
        # Legacy format: "source:job_id:path"
        return parts[0], parts[1], "", parts[2]
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]

    raise ValueError("Invalid file_id format: expected 'source|job_id|base|path' or legacy format")

