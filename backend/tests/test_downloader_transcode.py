import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.downloader import transcode


def test_build_transcode_command_video_codec():
    cmd = transcode.build_transcode_command("/in.mp4", "/out.mkv", "h265")
    assert cmd[0] == "ffmpeg"
    assert "/in.mp4" in cmd
    assert cmd[-1] == "/out.mkv"
    assert "libx265" in cmd or "hevc" in " ".join(cmd)
    assert "-progress" in cmd


def test_build_transcode_command_audio_codec():
    cmd = transcode.build_transcode_command("/in.webm", "/out.flac", "flac")
    assert "-vn" in cmd
    assert "flac" in " ".join(cmd)


def test_build_transcode_command_rejects_unknown_codec():
    with pytest.raises(ValueError):
        transcode.build_transcode_command("/in.mp4", "/out.mp4", "notacodec")


def _fake_proc(lines, returncode=0):
    proc = MagicMock()
    proc.stdout = iter(lines)
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    return proc


def test_transcode_reports_progress_from_ffmpeg_output():
    seen: list[float] = []
    lines = [
        "out_time_ms=5000000\n",
        "out_time_ms=10000000\n",
        "progress=end\n",
    ]

    with patch.object(transcode, "probe_duration", return_value=20.0), patch(
        "subprocess.Popen", return_value=_fake_proc(lines)
    ):
        transcode.transcode_file(
            "/in.mp4", "/out.mkv", "h265", threading.Event(), seen.append
        )

    assert seen[0] == pytest.approx(25.0)
    assert seen[1] == pytest.approx(50.0)
    assert seen[-1] == 100.0


def test_transcode_raises_on_nonzero_exit():
    proc = _fake_proc(["out_time_ms=1000000\n"], returncode=1)
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = "Encoder not found"

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(RuntimeError, match="Encoder not found"):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", threading.Event(), lambda _: None
            )


def test_transcode_kills_process_when_cancelled():
    cancel = threading.Event()
    cancel.set()
    proc = _fake_proc(["out_time_ms=1000000\n"])

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(transcode.TranscodeCancelled):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", cancel, lambda _: None
            )

    proc.kill.assert_called_once()


def test_probe_duration_parses_ffprobe():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="123.45\n", stderr=""
    )
    with patch("subprocess.run", return_value=completed):
        assert transcode.probe_duration("/in.mp4") == pytest.approx(123.45)


def test_probe_duration_returns_zero_when_unknown():
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="boom"
    )
    with patch("subprocess.run", return_value=completed):
        assert transcode.probe_duration("/in.mp4") == 0.0
