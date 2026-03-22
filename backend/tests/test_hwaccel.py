"""Tests for app.hwaccel — GPU hardware acceleration detection and arg building."""

from unittest.mock import patch
import subprocess
import pytest


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

CompletedResult = subprocess.CompletedProcess


# ---------------------------------------------------------------------------
# Cleanup: stop any lingering patcher after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _cleanup_hwaccel_patcher():
    """Stop the config patcher left active by _reload_hwaccel after each test."""
    yield
    import app.hwaccel as hwaccel_mod
    patcher = getattr(hwaccel_mod, "_test_hwaccel_patcher", None)
    if patcher:
        patcher.stop()
        hwaccel_mod._test_hwaccel_patcher = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Realistic ffmpeg -encoders output snippets
_NVENC_ENCODERS_OUTPUT = """\
Encoders:
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V..... hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 V..... av1_nvenc            NVIDIA NVENC AV1 encoder (codec av1)
"""

_QSV_ENCODERS_OUTPUT = """\
Encoders:
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... h264_qsv             H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (Quick Sync Video acceleration) (codec h264)
 V..... hevc_qsv             HEVC (Quick Sync Video acceleration) (codec hevc)
 V..... vp9_qsv              VP9 (Quick Sync Video acceleration) (codec vp9)
"""

_AMF_ENCODERS_OUTPUT = """\
Encoders:
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... h264_amf             AMD AMF H.264 Encoder (codec h264)
 V..... hevc_amf             AMD AMF HEVC Encoder (codec hevc)
"""

_VAAPI_ENCODERS_OUTPUT = """\
Encoders:
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 V..... hevc_vaapi           H.265/HEVC (VAAPI) (codec hevc)
"""

_NO_GPU_ENCODERS_OUTPUT = """\
Encoders:
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... libx265              libx265 H.265 / HEVC (codec hevc)
 A..... aac                  AAC (Advanced Audio Coding) (codec aac)
"""


def _make_run_side_effect(encoders_output: str, probe_succeeds: bool = True):
    """Return a side_effect function for subprocess.run that handles both
    ffmpeg -encoders queries and probe encodes."""

    def side_effect(cmd, **kwargs):
        if "-encoders" in cmd:
            return CompletedResult(
                args=cmd, returncode=0, stdout=encoders_output, stderr=""
            )
        # Probe encode (color source test)
        if "color=" in str(cmd):
            return CompletedResult(
                args=cmd,
                returncode=0 if probe_succeeds else 1,
                stdout=b"",
                stderr=b"",
            )
        return CompletedResult(args=cmd, returncode=0, stdout="", stderr="")

    return side_effect


def _reload_hwaccel(hwaccel_value: str = ""):
    """Reload hwaccel module with a given HWACCEL config value.

    The config patch stays active so detect_gpu() can read it, but each call
    stops the previous patcher to prevent accumulation/leaks between tests.
    """
    import importlib
    from unittest.mock import patch as _patch
    import app.config as config_mod
    import app.hwaccel as hwaccel_mod

    # Stop any previously active patcher from a prior call
    old_patcher = getattr(hwaccel_mod, "_test_hwaccel_patcher", None)
    if old_patcher:
        old_patcher.stop()

    patcher = _patch.object(config_mod, "HWACCEL", hwaccel_value)
    patcher.start()
    importlib.reload(hwaccel_mod)
    hwaccel_mod._test_hwaccel_patcher = patcher
    return hwaccel_mod


# ---------------------------------------------------------------------------
# Tests: Detection
# ---------------------------------------------------------------------------

class TestDetectGpu:

    def test_detect_nvidia(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "nvidia"

    def test_detect_intel_qsv(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_QSV_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "intel"

    def test_detect_amd_amf(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_AMF_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "amd"

    def test_detect_vaapi(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_VAAPI_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "vaapi"

    def test_detect_no_gpu(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NO_GPU_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "off"

    def test_hwaccel_off_skips_detection(self):
        hwaccel = _reload_hwaccel("off")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            hwaccel.detect_gpu()
            # Should not call ffmpeg at all
            mock_run.assert_not_called()
            assert hwaccel.get_backend() == "off"

    def test_unrecognized_hwaccel_value_logs_warning_and_autodetects(self, caplog):
        hwaccel = _reload_hwaccel("banana")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            import logging
            with caplog.at_level(logging.WARNING, logger="app.hwaccel"):
                hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "nvidia"
            assert "banana" in caplog.text

    def test_probe_failure_falls_back_to_off(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(
                _NVENC_ENCODERS_OUTPUT, probe_succeeds=False
            )
            hwaccel.detect_gpu()
            # NVENC probe failed, and no other backends available -> off
            assert hwaccel.get_backend() == "off"

    def test_nvidia_priority_over_vaapi(self):
        """When both NVENC and VAAPI are available, NVIDIA wins."""
        combined = _NVENC_ENCODERS_OUTPUT + "\n" + _VAAPI_ENCODERS_OUTPUT
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(combined)
            hwaccel.detect_gpu()
            assert hwaccel.get_backend() == "nvidia"


# ---------------------------------------------------------------------------
# Tests: Encoder resolution
# ---------------------------------------------------------------------------

class TestResolveVideoEncoder:

    def test_nvidia_resolves_libx264(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        assert hwaccel.resolve_video_encoder("libx264") == "h264_nvenc"

    def test_nvidia_resolves_libx265(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        assert hwaccel.resolve_video_encoder("libx265") == "hevc_nvenc"

    def test_off_returns_original(self):
        hwaccel = _reload_hwaccel("off")
        hwaccel.detect_gpu()
        assert hwaccel.resolve_video_encoder("libx264") == "libx264"

    def test_unknown_encoder_returns_original(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        assert hwaccel.resolve_video_encoder("libsomecodec") == "libsomecodec"

    def test_intel_resolves_vp9(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_QSV_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        assert hwaccel.resolve_video_encoder("libvpx-vp9") == "vp9_qsv"

    def test_nvidia_no_vp9(self):
        """NVIDIA doesn't have VP9 encoder — should fall back to CPU."""
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        assert hwaccel.resolve_video_encoder("libvpx-vp9") == "libvpx-vp9"


# ---------------------------------------------------------------------------
# Tests: HW decode input args
# ---------------------------------------------------------------------------

class TestGetHwaccelInputArgs:

    def test_nvidia_args(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.get_hwaccel_input_args()
        assert args == ["-hwaccel", "cuda"]

    def test_intel_args(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_QSV_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.get_hwaccel_input_args()
        assert args == ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]

    def test_off_returns_empty(self):
        hwaccel = _reload_hwaccel("off")
        hwaccel.detect_gpu()
        assert hwaccel.get_hwaccel_input_args() == []

    def test_returns_copy_not_reference(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        a = hwaccel.get_hwaccel_input_args()
        b = hwaccel.get_hwaccel_input_args()
        assert a == b
        assert a is not b  # should be independent lists


# ---------------------------------------------------------------------------
# Tests: Build video encode args
# ---------------------------------------------------------------------------

class TestBuildVideoEncodeArgs:

    def test_cpu_fallback_crf(self):
        hwaccel = _reload_hwaccel("off")
        hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args(
            "libx264", crf="23", preset="superfast", pix_fmt="yuv420p"
        )
        assert args == [
            "-c:v", "libx264", "-preset", "superfast", "-crf", "23", "-pix_fmt", "yuv420p"
        ]

    def test_nvidia_crf_translation(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args(
            "libx264", crf="23", preset="superfast", pix_fmt="yuv420p"
        )
        assert "-c:v" in args
        assert "h264_nvenc" in args
        assert "-cq" in args
        assert "23" in args
        assert "-preset" in args
        assert "p1" in args
        assert "-pix_fmt" in args
        assert "yuv420p" in args

    def test_intel_qsv_args(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_QSV_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args(
            "libx264", crf="23", preset="superfast", pix_fmt="yuv420p"
        )
        assert "h264_qsv" in args
        assert "-global_quality" in args
        # QSV uses hwaccel_output_format, no explicit pix_fmt
        assert "-pix_fmt" not in args

    def test_amd_amf_args(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_AMF_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args(
            "libx264", crf="23", preset="superfast", pix_fmt="yuv420p"
        )
        assert "h264_amf" in args
        assert "-rc" in args
        assert "cqp" in args
        assert "-quality" in args
        assert "speed" in args

    def test_vaapi_filter_chain(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_VAAPI_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args(
            "libx264", crf="23", preset="superfast", pix_fmt="yuv420p"
        )
        assert "h264_vaapi" in args
        assert "-vf" in args
        assert "format=nv12,hwupload" in args

    def test_bitrate_mode(self):
        hwaccel = _reload_hwaccel("")
        with patch("app.hwaccel.subprocess.run") as mock_run:
            mock_run.side_effect = _make_run_side_effect(_NVENC_ENCODERS_OUTPUT)
            hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args("libx264", bitrate="5000000")
        assert "-b:v" in args
        assert "5000000" in args
        # CRF flags should NOT be present in bitrate mode
        assert "-cq" not in args
        assert "-crf" not in args

    def test_cpu_bitrate_mode(self):
        hwaccel = _reload_hwaccel("off")
        hwaccel.detect_gpu()
        args = hwaccel.build_video_encode_args("libx264", bitrate="5000000")
        assert args == ["-c:v", "libx264", "-b:v", "5000000"]
