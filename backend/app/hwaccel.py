"""FFmpeg hardware-accelerated video encoding detection and argument building.

Auto-detects available GPU encoders (NVIDIA NVENC, Intel QSV, AMD AMF, VAAPI)
at startup and transparently substitutes them for CPU encoders in FFmpeg commands.

Set ``HWACCEL=off`` to force CPU-only encoding.
"""

import logging
import subprocess

from app import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cached state (set once by detect_gpu)
# ---------------------------------------------------------------------------
_backend: str = "off"  # "nvidia" | "intel" | "amd" | "vaapi" | "off"
_available_encoders: set[str] = set()

# ---------------------------------------------------------------------------
# GPU encoder mapping: CPU encoder -> {backend -> GPU encoder}
# ---------------------------------------------------------------------------
_GPU_ENCODER_MAP: dict[str, dict[str, str]] = {
    "libx264": {
        "nvidia": "h264_nvenc",
        "intel": "h264_qsv",
        "amd": "h264_amf",
        "vaapi": "h264_vaapi",
    },
    "libx265": {
        "nvidia": "hevc_nvenc",
        "intel": "hevc_qsv",
        "amd": "hevc_amf",
        "vaapi": "hevc_vaapi",
    },
    "libvpx-vp9": {
        "intel": "vp9_qsv",
        "vaapi": "vp9_vaapi",
    },
    "libaom-av1": {
        "nvidia": "av1_nvenc",
        "amd": "av1_amf",
    },
}

# All GPU encoder names we know about (for parsing ffmpeg -encoders output)
_ALL_GPU_ENCODERS: set[str] = set()
for _backends in _GPU_ENCODER_MAP.values():
    _ALL_GPU_ENCODERS.update(_backends.values())

# Which GPU encoders belong to each backend
_BACKEND_ENCODERS: dict[str, set[str]] = {}
for _cpu_enc, _backends in _GPU_ENCODER_MAP.items():
    for _be, _gpu_enc in _backends.items():
        _BACKEND_ENCODERS.setdefault(_be, set()).add(_gpu_enc)

# Detection priority order
_DETECTION_ORDER = ("nvidia", "intel", "amd", "vaapi")

# Minimum required encoder per backend (H.264 is the baseline)
_MIN_ENCODER = {
    "nvidia": "h264_nvenc",
    "intel": "h264_qsv",
    "amd": "h264_amf",
    "vaapi": "h264_vaapi",
}

# ---------------------------------------------------------------------------
# Quality parameter translation per backend
# ---------------------------------------------------------------------------
_QUALITY_PARAMS: dict[str, dict] = {
    "nvidia": {
        "crf_flag": "-cq",
        "preset_flag": "-preset",
        "preview_preset": "p1",
        "pix_fmt": "yuv420p",
    },
    "intel": {
        "crf_flag": "-global_quality",
        "preset_flag": "-preset",
        "preview_preset": "veryfast",
        "pix_fmt": "nv12",
    },
    "amd": {
        "crf_flag": None,  # AMF uses -rc/-qp_i/-qp_p instead
        "preset_flag": "-quality",
        "preview_preset": "speed",
        "pix_fmt": "nv12",
    },
    "vaapi": {
        "crf_flag": "-global_quality",
        "preset_flag": None,
        "preview_preset": None,
        "pix_fmt": None,  # VAAPI uses -vf filter chain instead
    },
}

# ---------------------------------------------------------------------------
# HW decode input args per backend (inserted before -i)
# ---------------------------------------------------------------------------
_HWACCEL_INPUT_ARGS: dict[str, list[str]] = {
    "nvidia": ["-hwaccel", "cuda"],
    "intel": ["-hwaccel", "qsv"],
    "amd": ["-hwaccel", "auto"],
    "vaapi": ["-hwaccel", "vaapi", "-vaapi_device", config.VAAPI_DEVICE],
    "off": [],
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _query_encoders() -> set[str]:
    """Run ``ffmpeg -encoders`` and return the set of available encoder names."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders", "-hide_banner"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout + result.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()

    found: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        # Encoder lines look like: " V..... h264_nvenc           NVIDIA ..."
        parts = stripped.split()
        if len(parts) >= 2:
            name = parts[1]
            if name in _ALL_GPU_ENCODERS:
                found.add(name)
    return found


def _probe_encoder(encoder: str, backend: str | None = None) -> bool:
    """Run a tiny test encode to verify the GPU encoder actually works.

    Uses 256x256 because some HW encoders (e.g. NVENC) enforce a minimum
    resolution, and a single frame at 1 fps to keep it fast.
    """
    try:
        pre_input: list[str] = []
        vf_args: list[str] = []
        if backend == "vaapi":
            pre_input = ["-hwaccel", "vaapi", "-vaapi_device", config.VAAPI_DEVICE]
            vf_args = ["-vf", "format=nv12,hwupload"]

        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                *pre_input,
                "-f",
                "lavfi",
                "-i",
                "color=black:s=256x256:d=0.1:r=1",
                *vf_args,
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def detect_gpu() -> None:
    """Detect available GPU encoding backend. Call once at startup."""
    global _backend, _available_encoders

    if config.HWACCEL == "off":
        _backend = "off"
        logger.info("Hardware acceleration disabled via HWACCEL=off")
        return

    if config.HWACCEL and config.HWACCEL != "off":
        logger.warning(
            "Unrecognized HWACCEL value '%s' — ignoring, proceeding with auto-detection",
            config.HWACCEL,
        )

    _available_encoders = _query_encoders()
    if not _available_encoders:
        _backend = "off"
        logger.info("Hardware acceleration: no GPU encoders found, using CPU")
        return

    # Try backends in priority order
    for be in _DETECTION_ORDER:
        min_enc = _MIN_ENCODER[be]
        if min_enc in _available_encoders:
            # Verify with a test encode
            if _probe_encoder(min_enc, backend=be):
                _backend = be
                be_encoders = _BACKEND_ENCODERS.get(be, set())
                actual = _available_encoders & be_encoders
                logger.info(
                    "Hardware acceleration: %s (encoders: %s)",
                    be,
                    ", ".join(sorted(actual)),
                )
                return
            else:
                logger.warning(
                    "GPU encoder %s reported available but probe failed, skipping %s",
                    min_enc,
                    be,
                )

    _backend = "off"
    logger.info("Hardware acceleration: GPU encoders found but none passed probe, using CPU")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_backend() -> str:
    """Return the detected backend name (e.g. ``"nvidia"``, ``"off"``)."""
    return _backend


def resolve_video_encoder(cpu_encoder: str) -> str:
    """Return the GPU encoder for *cpu_encoder*, or the original if unavailable."""
    if _backend == "off":
        return cpu_encoder
    mapping = _GPU_ENCODER_MAP.get(cpu_encoder)
    if not mapping:
        return cpu_encoder
    gpu_enc = mapping.get(_backend)
    if gpu_enc and gpu_enc in _available_encoders:
        return gpu_enc
    return cpu_encoder


def get_hwaccel_input_args() -> list[str]:
    """Return FFmpeg input-side args for HW decoding (insert before ``-i``)."""
    return list(_HWACCEL_INPUT_ARGS.get(_backend, []))


def build_video_encode_args(
    cpu_encoder: str,
    *,
    crf: str | None = None,
    preset: str | None = None,
    pix_fmt: str | None = None,
    bitrate: str | None = None,
) -> list[str]:
    """Build FFmpeg video encoder args, substituting GPU equivalents when available.

    Parameters
    ----------
    cpu_encoder:
        The CPU encoder name (e.g. ``"libx264"``).
    crf:
        CRF / quality value (translated to backend-specific flag).
    preset:
        CPU preset (translated to backend-specific preset).
    pix_fmt:
        Pixel format for CPU path.
    bitrate:
        If set, use bitrate mode (``-b:v``) instead of CRF.

    Returns
    -------
    list[str]
        Args to append to the FFmpeg command (e.g.
        ``["-c:v", "h264_nvenc", "-cq", "23", "-preset", "p1"]``).
    """
    encoder = resolve_video_encoder(cpu_encoder)
    args: list[str] = ["-c:v", encoder]

    # CPU path — pass through original args
    if _backend == "off" or encoder == cpu_encoder:
        if bitrate:
            args += ["-b:v", bitrate]
        else:
            if preset:
                args += ["-preset", preset]
            if crf:
                args += ["-crf", crf]
        if pix_fmt:
            args += ["-pix_fmt", pix_fmt]
        return args

    # GPU path — translate quality params
    params = _QUALITY_PARAMS.get(_backend, {})

    if bitrate:
        # Bitrate mode works the same across all backends
        args += ["-b:v", bitrate]
    elif _backend == "amd":
        # AMF uses -rc cqp with explicit QP values
        if crf:
            args += ["-rc", "cqp", "-qp_i", crf, "-qp_p", crf]
    else:
        crf_flag = params.get("crf_flag")
        if crf and crf_flag:
            args += [crf_flag, crf]

    preset_flag = params.get("preset_flag")
    gpu_preset = params.get("preview_preset")
    if preset_flag and gpu_preset:
        args += [preset_flag, gpu_preset]

    # Pixel format / VAAPI filter chain
    if _backend == "vaapi":
        args += ["-vf", "format=nv12,hwupload"]
    else:
        gpu_pix_fmt = params.get("pix_fmt")
        if gpu_pix_fmt:
            args += ["-pix_fmt", gpu_pix_fmt]

    return args
