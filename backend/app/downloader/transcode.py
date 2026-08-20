import logging
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable

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

# The container a re-encode lands in when the user left Format on "Auto".
#
# ffmpeg picks its muxer from the *output file extension*, so this map is what
# makes the extension describe the codec inside. Deriving it from the source
# file instead produced the reported silent file: encoding FLAC over a source
# named `theme.opus` kept the `.opus` extension, ffmpeg inferred the Ogg muxer,
# and FLAC-in-Ogg is legal enough that nothing errored - but a player opening a
# `.opus` file expects Opus and plays silence.
#
# Notes on the less obvious entries:
#   aac  -> m4a  A bare `.aac` file is a raw ADTS stream with no index; players
#                handle it unevenly and cannot seek it reliably. MP4/M4A is the
#                normal home for AAC.
#   av1  -> mkv  AV1 has no single universal container, and the video branch
#                copies the source audio track unchanged (`-c:a copy`). WebM
#                would *reject* a copied AAC track outright, and AV1-in-MP4
#                player support is still patchy; Matroska accepts every
#                combination and plays everywhere we care about.
#   opus -> opus `.opus` is the standard Ogg-Opus extension and ffmpeg maps it
#                to the Ogg muxer, which is correct here - it was only wrong
#                above because the *codec* was not Opus.
CODEC_TO_CONTAINER = {
    "mp3": "mp3",
    "flac": "flac",
    "aac": "m4a",
    "opus": "opus",
    "wav": "wav",
    "h264": "mp4",
    "h265": "mp4",
    "vp9": "webm",
    "av1": "mkv",
}

# Containers that can actually carry each codec, used to reject an explicit
# Format choice that contradicts the chosen codec instead of muxing a file no
# player will open. Supersets of CODEC_TO_CONTAINER's value for each codec.
CODEC_COMPATIBLE_CONTAINERS = {
    "mp3": frozenset({"mp3"}),
    "flac": frozenset({"flac"}),
    "aac": frozenset({"m4a", "mp4", "aac"}),
    "opus": frozenset({"opus", "ogg", "webm"}),
    "wav": frozenset({"wav"}),
    "h264": frozenset({"mp4", "mkv", "mov"}),
    "h265": frozenset({"mp4", "mkv", "mov"}),
    "vp9": frozenset({"webm", "mkv"}),
    "av1": frozenset({"mkv", "mp4", "webm"}),
}


def container_for_codec(codec: str) -> str:
    """The file extension a re-encode to `codec` must use."""
    key = str(codec or "").lower()
    try:
        return CODEC_TO_CONTAINER[key]
    except KeyError:
        raise ValueError(f"Unsupported transcode codec: {codec}") from None


def assert_container_supports_codec(container: str, codec: str) -> None:
    """Reject an explicit container that cannot carry `codec`.

    An explicit Format choice is honoured whenever the pairing is playable,
    because it is a deliberate decision. When it is not, this fails loudly
    rather than falling back silently: a silent fallback would ignore what the
    user asked for, and honouring it would recreate the very bug this map
    exists to prevent - a file that muxes without complaint and plays as
    silence or not at all. The pairing is contradictory either way, and the
    check runs before any encoding starts, so nothing is half-written.
    """
    key = str(codec or "").lower()
    name = str(container or "").lower()
    if not name:
        return
    allowed = CODEC_COMPATIBLE_CONTAINERS.get(key)
    if allowed is None:
        raise ValueError(f"Unsupported transcode codec: {codec}")
    if name not in allowed:
        raise ValueError(
            f"Container '{name}' cannot hold {key} audio/video. "
            f"Choose Format 'Auto' (which uses .{CODEC_TO_CONTAINER[key]}) "
            f"or one of: {', '.join(sorted(allowed))}."
        )


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
            check=False,
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

    with tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="replace"
    ) as stderr_file:
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
                    _remove_partial(dst)
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

        # A cancel that arrives after ffmpeg already exited successfully is a
        # no-op, not a cancellation: the output is complete and correct, and
        # a killed process never exits 0 on any platform we support. Only
        # treat this as a genuine cancellation when the process didn't
        # actually finish cleanly - deleting a finished file here would be
        # data loss, not cleanup.
        if cancel_event.is_set() and returncode != 0:
            _remove_partial(dst)
            raise TranscodeCancelled("Cancelled by user")

        if returncode != 0:
            stderr_file.seek(0)
            stderr = stderr_file.read()
            _remove_partial(dst)
            raise RuntimeError(
                stderr.strip() or f"ffmpeg exited with code {returncode}"
            )


def _remove_partial(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.warning("Could not remove partial transcode output %s", path)
