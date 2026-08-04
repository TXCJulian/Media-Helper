import logging
import os
import subprocess
import tempfile
import threading
from typing import Callable

from app.hwaccel import build_video_encode_args, get_hwaccel_input_args

logger = logging.getLogger(__name__)

CODEC_TO_ENCODER = {
    "h264": "libx264",
    "h265": "libx265",
    "vp9": "libvpx-vp9",
    "av1": "libsvtav1",
}
AUDIO_CODEC_TO_ENCODER = {
    "mp3": "libmp3lame",
    "flac": "flac",
    "aac": "aac",
    "opus": "libopus",
    "wav": "pcm_s16le",
}


class TranscodeCancelled(RuntimeError):
    """Raised when the ffmpeg re-encode was cancelled by the user."""


def probe_duration(path: str) -> float:
    """Media duration in seconds, or 0.0 when it cannot be determined."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def build_transcode_command(src: str, dst: str, codec: str) -> list[str]:
    key = str(codec or "").lower()
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-y"]

    if key in CODEC_TO_ENCODER:
        encoder = CODEC_TO_ENCODER[key]
        hwaccel = get_hwaccel_input_args()
        if hwaccel and encoder != "libsvtav1":
            cmd += hwaccel
        cmd += ["-i", src]
        cmd += build_video_encode_args(encoder, crf="23")
        cmd += ["-c:a", "copy"]
    elif key in AUDIO_CODEC_TO_ENCODER:
        cmd += ["-i", src, "-vn", "-c:a", AUDIO_CODEC_TO_ENCODER[key]]
    else:
        raise ValueError(f"Unsupported transcode codec: {codec}")

    cmd.append(dst)
    return cmd


def _watch_cancel(proc: "subprocess.Popen[str]", cancel_event: threading.Event) -> None:
    """Kill `proc` as soon as `cancel_event` fires.

    Runs in a daemon thread alongside the progress-reading loop in
    `transcode_file`, so cancellation is observed even when ffmpeg emits no
    further `-progress` output (e.g. a stalled encode) - a stdout-line-driven
    cancel check alone would miss that case. Mirrors `app.cutter._monitor_cancel`.
    """
    while proc.poll() is None:
        if cancel_event.wait(timeout=0.25):
            if proc.poll() is None:
                proc.kill()
            return


def transcode_file(
    src: str,
    dst: str,
    codec: str,
    cancel_event: threading.Event,
    on_progress: Callable[[float], None],
) -> None:
    """Re-encode `src` to `dst`, reporting 0-100 progress and honouring cancel.

    Progress is elapsed encoded time over total duration, read from ffmpeg's
    `-progress` stream. When the duration is unknown, progress stays at 0
    until the process finishes rather than reporting a fabricated value.

    stderr is redirected to an unbounded temp file rather than a pipe: a
    chatty ffmpeg process could otherwise block on a full pipe buffer (nobody
    drains it while the stdout progress loop runs), which would also stall
    the stdout stream it shares a write path with. A daemon watcher thread
    (`_watch_cancel`) kills the process the moment `cancel_event` fires,
    independent of whether ffmpeg is still emitting progress lines, so
    cancellation stays responsive even on a stalled encode.
    """
    duration = probe_duration(src)
    cmd = build_transcode_command(src, dst, codec)

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            bufsize=1,
        )

        watcher = threading.Thread(
            target=_watch_cancel, args=(proc, cancel_event), daemon=True
        )
        watcher.start()

        try:
            for line in proc.stdout or []:
                if cancel_event.is_set():
                    proc.kill()
                    raise TranscodeCancelled("Cancelled by user")
                line = line.strip()
                if line.startswith("out_time_ms=") and duration > 0:
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    on_progress(min(seconds / duration * 100.0, 100.0))
                elif line == "progress=end":
                    on_progress(100.0)
        finally:
            if proc.poll() is None:
                proc.kill()
            returncode = proc.wait()
            watcher.join(timeout=1.0)

        if cancel_event.is_set():
            raise TranscodeCancelled("Cancelled by user")

        if returncode != 0:
            stderr_file.seek(0)
            stderr = stderr_file.read()
            _remove_partial(dst)
            raise RuntimeError(stderr.strip() or f"ffmpeg exited with code {returncode}")


def _remove_partial(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.warning("Could not remove partial transcode output %s", path)
