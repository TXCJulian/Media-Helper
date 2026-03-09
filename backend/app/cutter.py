import base64
import functools
import json
import logging
import os
import re
import struct
import subprocess
from typing import Callable, Optional

from app.config import CUTTER_UPLOAD_DIR
from app.fs_utils import collision_safe_path

logger = logging.getLogger(__name__)

# Ensure upload directory exists
os.makedirs(CUTTER_UPLOAD_DIR, exist_ok=True)

# Codecs that need transcoding for browser preview playback
_TRANSCODE_CODECS = {"ac3", "eac3", "dts", "dts_hd", "truehd", "flac"}
_PASSTHROUGH_CODECS = {"aac", "mp3", "opus", "vorbis"}

# Map user-facing codec names to ffmpeg encoder names
_CODEC_TO_ENCODER = {
    "aac": "aac",
    "flac": "flac",
    "opus": "libopus",
    "ac3": "ac3",
    "mp3": "libmp3lame",
    "vorbis": "libvorbis",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
}


@functools.lru_cache(maxsize=50)
def _waveform_cached(filepath: str, mtime: float, num_peaks: int) -> list[float]:
    """Internal cached waveform generator. Keyed on (filepath, mtime, num_peaks)."""
    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
        "-t", "3600",
        "-i", filepath,
        "-ac", "1",
        "-ar", "8000",
        "-f", "f32le",
        "pipe:1",
    ]
    result = subprocess.run(
        cmd, capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg waveform extraction failed: {result.stderr.decode(errors='replace')}")

    raw = result.stdout
    num_samples = len(raw) // 4
    if num_samples == 0:
        return [0.0] * num_peaks

    samples = struct.unpack(f"<{num_samples}f", raw[:num_samples * 4])

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
        "-loglevel", "warning",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {filepath}: {result.stderr}")

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )

    info: dict = {
        "duration": float(fmt.get("duration", 0)),
        "video_codec": video_stream.get("codec_name") if video_stream else None,
        "audio_codec": audio_stream.get("codec_name", "unknown") if audio_stream else "unknown",
        "container": fmt.get("format_name", "unknown"),
        "bitrate": int(fmt.get("bit_rate", 0)),
        "width": int(video_stream["width"]) if video_stream and "width" in video_stream else None,
        "height": int(video_stream["height"]) if video_stream and "height" in video_stream else None,
        "sample_rate": int(audio_stream.get("sample_rate", 0)) if audio_stream else 0,
    }
    return info


def generate_waveform(filepath: str, num_peaks: int = 2000) -> list[float]:
    """Generate a normalized waveform peak list from an audio/video file.

    Uses ffmpeg to extract mono PCM f32le audio at 8kHz (capped at 1 hour),
    then buckets samples and takes the max absolute value per bucket. Results
    are cached (bounded LRU, max 50 entries) by (filepath, mtime) to avoid
    regeneration.
    """
    mtime = os.path.getmtime(filepath)
    return _waveform_cached(filepath, mtime, num_peaks)


def needs_transcoding(audio_codec: str) -> bool:
    """Return True if the audio codec requires transcoding for browser preview."""
    codec = audio_codec.lower()
    if codec in _TRANSCODE_CODECS:
        return True
    if codec in _PASSTHROUGH_CODECS:
        return False
    return True


def transcode_for_preview(filepath: str) -> subprocess.Popen:
    """Launch ffmpeg to transcode a file for browser-compatible streaming.

    Returns a Popen object whose stdout can be streamed as a response.
    For video files: re-mux video as-is, transcode audio to AAC.
    For audio-only files: transcode audio to AAC only.
    """
    # Probe to determine if there's a video stream
    probe_info = probe_file(filepath)
    has_video = probe_info["video_codec"] is not None

    cmd = ["ffmpeg", "-loglevel", "warning", "-i", filepath]
    if has_video:
        cmd += ["-c:v", "copy"]
    cmd += [
        "-c:a", "aac",
        "-b:a", "192k",
        "-f", "mp4",
        "-movflags", "frag_mp4+empty_moov",
        "pipe:1",
    ]

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def cut_file(
    filepath: str,
    in_point: float,
    out_point: float,
    output_path: str,
    stream_copy: bool,
    codec: Optional[str],
    container: Optional[str],
    progress_cb: Callable[[str], None],
) -> str:
    """Cut a segment from a media file using ffmpeg.

    Args:
        filepath: Source file path.
        in_point: Start time in seconds.
        out_point: End time in seconds.
        output_path: Desired output file path.
        stream_copy: If True, use -c copy (no re-encoding).
        codec: Target audio codec name (e.g. "aac", "flac", "opus").
        container: Target container format (used for output extension).
        progress_cb: Callback for progress messages.

    Returns:
        The final output file path (may differ from output_path if collision).
    """
    output_path = collision_safe_path(output_path)
    duration = out_point - in_point

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
        "-ss", str(in_point),
        "-t", str(duration),
        "-i", filepath,
    ]

    if stream_copy:
        cmd += ["-c", "copy"]
    else:
        if codec:
            encoder = _CODEC_TO_ENCODER.get(codec, codec)
            cmd += ["-c:a", encoder]

    if container:
        cmd += ["-f", container]

    cmd += ["-y", output_path]

    progress_cb(f"Cutting {os.path.basename(filepath)} [{in_point:.2f}s - {out_point:.2f}s]")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Parse stderr for time= progress lines
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
    duration = out_point - in_point

    for line in iter(proc.stderr.readline, ""):
        match = time_pattern.search(line)
        if match:
            h, m, s, cs = match.groups()
            current = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
            if duration > 0:
                pct = min(100.0, (current / duration) * 100)
                progress_cb(f"Progress: {pct:.1f}%")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg cut failed (exit {proc.returncode})"
        )

    progress_cb(f"Saved {os.path.basename(output_path)}")
    return output_path


def encode_file_id(source: str, path: str) -> str:
    """URL-safe base64 encode of 'source:path'."""
    raw = f"{source}:{path}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_file_id(file_id: str) -> tuple[str, str]:
    """Decode a file_id back to (source, path). Raises ValueError on invalid input.

    Security note: This function performs NO path validation. Callers MUST
    validate the returned path against allowed base directories before use
    (e.g., via ``validate_path()``) to prevent directory traversal attacks.
    """
    try:
        decoded = base64.urlsafe_b64decode(file_id.encode("ascii")).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Invalid file_id: {e}") from e

    parts = decoded.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid file_id format: expected 'source:path'")

    return parts[0], parts[1]
