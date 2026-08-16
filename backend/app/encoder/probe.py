"""ffprobe inspection of candidate files.

Returns the facts rules evaluate and the UI displays. Runs locally: ffprobe is
already installed for the Cutter, is cheap, and needs no GPU -- so the encoder
service never has to be reachable just to decide whether a file is interesting.

Not built on ``cutter.probe_file``: that returns a normalised shape for the
Cutter and drops stream side data, ``format.size``, bit depth and subtitles,
which are exactly the fields rules and the info panel need. Widening it would
change a shape the Cutter's tests pin.
"""

import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30

# Substrings of ffprobe's side_data_type values. Matched case-insensitively on
# a substring because the exact strings have changed across ffmpeg releases
# ("DOVI configuration record", "Dolby Vision Metadata") while these stems
# have not.
_DOVI_MARKERS = ("dovi", "dolby vision")
_HDR_MARKERS = ("mastering display", "content light level")

# Transfer characteristics that mean HDR even with no side data attached.
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

# Which tool last wrote the container, normalised. A fresh MakeMKV rip carries
# `encoder: libmakemkv ...` plus `_STATISTICS_WRITING_APP` on every stream;
# HandBrake strips both and writes `ENCODER: Lavf...`, because it muxes through
# libavformat. So this answers "has anything already re-muxed this file?" --
# which is what the rule list actually wants to know.
#
# Codec cannot answer it: MakeMKV rips 4K Blu-rays as HEVC, so treating `hevc`
# as "already encoded" would skip a 92Mbps Dolby Vision rip.
_SOURCE_TOOLS = (("makemkv", "makemkv"), ("handbrake", "handbrake"), ("lavf", "lavf"))


class ProbeError(RuntimeError):
    """Raised when a file cannot be inspected."""


def probe(path: str) -> dict:
    """Inspect *path*, returning the facts rules and the UI need.

    Requires *path* to already be a real, existing file. ffprobe resolves
    protocols, not just paths -- handing it a URL makes it issue a real
    network request -- so this guard closes that hazard at the source rather
    than relying on every caller to have checked first. Callers that accept a
    path from outside this process (routes.py's ``/test``) must still resolve
    and containment-check it *before* calling in, since this check alone
    can't tell "legitimate library file" from "any other file on disk".
    """
    if not os.path.isfile(path):
        raise ProbeError(f"No such file: {path}")

    cmd = [
        "ffprobe",
        "-loglevel", "warning",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out for {path}") from exc
    except OSError as exc:
        raise ProbeError(f"Could not run ffprobe for {path}: {exc}") from exc

    if result.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned malformed JSON for {path}: {exc}") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            # Cover art is a video stream. Treating it as the real track would
            # report a thumbnail's dimensions and match the wrong rule.
            and not (s.get("disposition") or {}).get("attached_pic", 0)
        ),
        None,
    )
    if video is None:
        raise ProbeError(f"File has no video stream: {path}")

    side_types = [
        str(sd.get("side_data_type", "")).lower()
        for sd in (video.get("side_data_list") or [])
    ]
    dolby_vision = any(m in t for t in side_types for m in _DOVI_MARKERS)
    hdr = (
        dolby_vision
        or any(m in t for t in side_types for m in _HDR_MARKERS)
        or str(video.get("color_transfer", "")).lower() in _HDR_TRANSFERS
    )

    encoder_tag = _tag(fmt.get("tags"), "encoder")
    if not encoder_tag:
        # MakeMKV also stamps every stream. Falling back to it catches a rip
        # whose format-level tag was lost to an intermediate remux.
        for stream in streams:
            stat_app = _prefixed_tag(stream.get("tags"), "_STATISTICS_WRITING_APP")
            if stat_app:
                encoder_tag = stat_app
                break

    return {
        "width": _as_int(video.get("width")),
        "height": _as_int(video.get("height")),
        "video_codec": str(video.get("codec_name") or ""),
        "profile": str(video.get("profile") or ""),
        "bit_depth": _bit_depth(str(video.get("pix_fmt") or "")),
        "hdr": hdr,
        "dolby_vision": dolby_vision,
        "frame_rate": _rational(str(video.get("r_frame_rate") or "")),
        "size": _as_int(fmt.get("size")) or 0,
        "bit_rate": _as_int(fmt.get("bit_rate")) or 0,
        "duration": _as_float(fmt.get("duration")) or 0.0,
        "audio": [
            {
                "codec": str(s.get("codec_name") or ""),
                "channels": _as_int(s.get("channels")) or 0,
                "language": str((s.get("tags") or {}).get("language", "")),
                "title": str((s.get("tags") or {}).get("title", "")),
            }
            for s in streams
            if s.get("codec_type") == "audio"
        ],
        "subtitles": [
            {
                "codec": str(s.get("codec_name") or ""),
                "language": str((s.get("tags") or {}).get("language", "")),
            }
            for s in streams
            if s.get("codec_type") == "subtitle"
        ],
        "source_tool": _source_tool(encoder_tag),
        "encoder_tag": encoder_tag,
    }


def _tag(tags: object, name: str) -> str:
    """Case-insensitive tag lookup. MakeMKV writes `encoder`, libav `ENCODER`."""
    if not isinstance(tags, dict):
        return ""
    wanted = name.lower()
    for key, value in tags.items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _prefixed_tag(tags: object, prefix: str) -> str:
    """First tag whose key starts with *prefix*.

    Stream tags carry a language suffix -- `_STATISTICS_WRITING_APP-eng` -- so
    an exact key match would miss them.
    """
    if not isinstance(tags, dict):
        return ""
    wanted = prefix.lower()
    for key, value in tags.items():
        if str(key).lower().startswith(wanted):
            return str(value)
    return ""


def _source_tool(encoder_tag: str) -> str:
    lowered = encoder_tag.lower()
    if not lowered:
        return "unknown"
    for needle, label in _SOURCE_TOOLS:
        if needle in lowered:
            return label
    return "other"


def _as_int(value: object) -> int | None:
    # Some ffprobe builds emit float-formatted integers (e.g. bit_rate as
    # "24000000.0"); routing through float() first handles both that and the
    # plain-integer-string case in one path instead of silently coercing the
    # float form to None.
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _bit_depth(pix_fmt: str) -> int | None:
    """Bit depth from the pixel format name, e.g. yuv420p10le -> 10.

    Handles both naming conventions:
    - yuv420p10le, yuv420p9le, yuv422p12le → depth follows a 'p'
    - p010le, p016le → zero-padded depth follows a leading 'p'
    - yuv420p, nv12, rgb24 → 8 (no depth digits)

    Read from pix_fmt rather than bits_per_raw_sample because the latter is
    frequently absent on exactly the HDR files where depth decides the rule.
    """
    if not pix_fmt:
        return None
    match = re.search(r"p(\d{1,3})", pix_fmt)
    if match:
        depth_str = match.group(1)
        depth_int = int(depth_str)
        return depth_int
    return 8


def _rational(value: str) -> float | None:
    """Parse ffprobe's `24000/1001` frame-rate form."""
    if "/" not in value:
        return _as_float(value)
    num, _, den = value.partition("/")
    try:
        numerator, denominator = float(num), float(den)
    except ValueError:
        return None
    if denominator == 0:
        return None
    return numerator / denominator
