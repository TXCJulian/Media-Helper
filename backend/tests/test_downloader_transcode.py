import os
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


class _BlockingStdout:
    """Iterator that blocks forever until `close()` is called, then stops.

    Simulates a stalled ffmpeg process that emits no `-progress` lines at
    all - and whose stdout pipe only reaches EOF once the process is killed,
    the same way a real OS pipe closes once the writing process dies.
    """

    def __init__(self):
        self._closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self._closed.wait()
        raise StopIteration

    def close(self):
        self._closed.set()


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


def _popen_writing_stderr(text, returncode=1):
    """Return a `subprocess.Popen` side_effect that writes `text` into the
    real tempfile transcode_file passes as `stderr=`, mirroring what a real
    ffmpeg child process would write into it."""

    def _fake_popen(cmd, **kwargs):
        stderr_file = kwargs["stderr"]
        stderr_file.write(text)
        stderr_file.flush()
        return _fake_proc(["out_time_ms=1000000\n"], returncode=returncode)

    return _fake_popen


def test_transcode_raises_on_nonzero_exit():
    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", side_effect=_popen_writing_stderr("Encoder not found")
    ):
        with pytest.raises(RuntimeError, match="Encoder not found"):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", threading.Event(), lambda _: None
            )


def test_transcode_surfaces_stderr_without_deadlock_on_large_output():
    """A chatty ffmpeg process must not deadlock the read loop, and its error
    text must still reach the raised RuntimeError. stderr is redirected to an
    unbounded temp file (not a PIPE) specifically so a large volume of output
    can never block the child on a full OS pipe buffer."""
    large_output = ("x" * 200_000) + "\nEncoder not found\n"

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", side_effect=_popen_writing_stderr(large_output)
    ):
        with pytest.raises(RuntimeError, match="Encoder not found"):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", threading.Event(), lambda _: None
            )


def test_transcode_kills_process_when_no_progress_lines_emitted():
    """Cancellation must be observed even when ffmpeg emits no progress lines
    at all (a stalled encode) - a cancel check that only runs when a new
    stdout line arrives would never fire in that case."""
    cancel = threading.Event()
    cancel.set()

    blocking_stdout = _BlockingStdout()
    proc = MagicMock()
    proc.stdout = blocking_stdout
    proc.poll.return_value = None  # still "running"

    def _fake_kill():
        proc.poll.return_value = -9
        blocking_stdout.close()

    proc.kill.side_effect = _fake_kill
    proc.wait.return_value = -9

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(transcode.TranscodeCancelled):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", cancel, lambda _: None
            )

    proc.kill.assert_called_once()


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


def test_transcode_removes_partial_output_on_inline_cancel(tmp_path):
    """Cancellation detected by the main loop's inline check (a progress line
    arrived while cancel_event was already set) must remove any partial
    output left behind - otherwise a truncated, unplayable file is left
    sitting where the finished file was expected."""
    dst = str(tmp_path / "out.mkv")
    with open(dst, "wb") as f:
        f.write(b"partial ffmpeg output")

    cancel = threading.Event()
    cancel.set()
    proc = _fake_proc(["out_time_ms=1000000\n"])

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(transcode.TranscodeCancelled):
            transcode.transcode_file("/in.mp4", dst, "h265", cancel, lambda _: None)

    assert not os.path.exists(dst)


def test_transcode_removes_partial_output_on_watcher_cancel(tmp_path):
    """Cancellation detected only by the watcher thread (a stalled encode
    with zero progress lines) must also remove any partial output. This is a
    separate raise site from the inline check above - a fix to one does not
    cover the other."""
    dst = str(tmp_path / "out.mkv")
    with open(dst, "wb") as f:
        f.write(b"partial ffmpeg output")

    cancel = threading.Event()
    cancel.set()

    blocking_stdout = _BlockingStdout()
    proc = MagicMock()
    proc.stdout = blocking_stdout
    proc.poll.return_value = None  # still "running"

    def _fake_kill():
        proc.poll.return_value = -9
        blocking_stdout.close()

    proc.kill.side_effect = _fake_kill
    proc.wait.return_value = -9

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(transcode.TranscodeCancelled):
            transcode.transcode_file("/in.mp4", dst, "h265", cancel, lambda _: None)

    assert not os.path.exists(dst)


class _StdoutThenCancel:
    """Yields the given lines, then sets `cancel_event` only once they've all
    been consumed - simulating a cancel request that arrives at (or just
    after) the exact moment ffmpeg finishes encoding on its own, rather than
    a cancel that actually interrupted the process."""

    def __init__(self, lines, cancel_event):
        self._lines = iter(lines)
        self._cancel_event = cancel_event

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._lines)
        except StopIteration:
            self._cancel_event.set()
            raise


def test_transcode_keeps_output_when_cancel_arrives_after_success(tmp_path):
    """A cancel_event that becomes set only after ffmpeg has already exited 0
    must be treated as a no-op, not a cancellation: the output is complete
    and correct, so transcode_file must return normally and must NOT delete
    it. Deleting a finished file here would be data loss, not cleanup - a
    killed process never exits 0 on any platform this app supports, so
    returncode == 0 is a reliable signal that nothing was actually killed."""
    dst = str(tmp_path / "out.mkv")
    with open(dst, "wb") as f:
        f.write(b"finished ffmpeg output")

    cancel = threading.Event()
    lines = ["out_time_ms=10000000\n", "progress=end\n"]
    proc = _fake_proc(lines, returncode=0)
    proc.stdout = _StdoutThenCancel(lines, cancel)

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        transcode.transcode_file("/in.mp4", dst, "h265", cancel, lambda _: None)

    assert os.path.exists(dst)


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


def test_every_supported_codec_has_a_container():
    """The UI offers exactly these codecs (RECODE in DownloadOptions.tsx). A
    codec with no container entry would send `container_for_codec` down its
    raise path at re-encode time, i.e. a job that fails after downloading."""
    codecs = set(transcode.CODEC_TO_ENCODER) | set(transcode.AUDIO_CODEC_TO_ENCODER)
    assert codecs == set(transcode.CODEC_TO_CONTAINER)
    assert codecs == set(transcode.CODEC_COMPATIBLE_CONTAINERS)


def test_default_container_is_always_an_accepted_one():
    """Format=Auto must never pick a container that an explicit Format of the
    same value would be rejected for."""
    for codec, container in transcode.CODEC_TO_CONTAINER.items():
        assert container in transcode.CODEC_COMPATIBLE_CONTAINERS[codec], codec
        transcode.assert_container_supports_codec(container, codec)


def test_container_for_codec_rejects_an_unknown_codec():
    with pytest.raises(ValueError):
        transcode.container_for_codec("notacodec")


def test_empty_container_is_always_accepted():
    """"" is the normalised form of the Format=Auto sentinel; it means "you
    choose", so there is nothing to contradict."""
    transcode.assert_container_supports_codec("", "flac")


def test_flac_in_an_mp4_container_is_rejected_with_a_useful_message():
    with pytest.raises(ValueError) as excinfo:
        transcode.assert_container_supports_codec("m4a", "flac")
    message = str(excinfo.value)
    assert "m4a" in message and "flac" in message
    # It has to say what to do instead, not just that it refused.
    assert ".flac" in message
