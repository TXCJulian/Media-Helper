"""Tests for audio-only transcode from source."""

from unittest.mock import patch, MagicMock


class TestAudioTranscodeStatusKey:

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    def test_key_includes_stream_index(self, mock_mtime):
        from app.cutter import _audio_transcode_status_key, _preview_cache_key
        key = _audio_transcode_status_key("/media/test.mkv", "job123", 2)
        hash_part = _preview_cache_key("/media/test.mkv")
        assert key == f"job123:{hash_part}:srcaudio2"

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    def test_different_streams_produce_different_keys(self, mock_mtime):
        from app.cutter import _audio_transcode_status_key
        k1 = _audio_transcode_status_key("/media/test.mkv", "job1", 1)
        k2 = _audio_transcode_status_key("/media/test.mkv", "job1", 3)
        assert k1 != k2

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    def test_key_differs_from_preview_status_key(self, mock_mtime):
        from app.cutter import _audio_transcode_status_key, _preview_status_key
        audio_key = _audio_transcode_status_key("/media/test.mkv", "job1", 1)
        preview_key = _preview_status_key("/media/test.mkv", "job1")
        assert audio_key != preview_key


class TestTranscodeAudioTrackFromSource:

    @patch("app.cutter.probe_file")
    @patch("app.cutter.subprocess.Popen")
    @patch("app.cutter.os.path.isfile", return_value=False)
    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    @patch("app.cutter.os.makedirs")
    @patch("app.cutter.os.replace")
    @patch("app.cutter._begin_job_operation")
    @patch("app.cutter._end_job_operation")
    @patch("app.cutter._register_job_process")
    @patch("app.cutter._unregister_job_process")
    @patch("app.cutter.get_or_create_audio_master")
    def test_produces_cached_audio_file(
        self, mock_audio_master, mock_unreg, mock_reg, mock_end, mock_begin,
        mock_replace, mock_makedirs, mock_getmtime, mock_isfile, mock_popen, mock_probe,
    ):
        mock_audio_master.return_value = ("/tmp/audio_master.mka", False)
        mock_probe.return_value = {
            "duration": 120.0,
            "audio_streams": [
                {"index": 1, "codec": "truehd", "channels": 8},
            ],
        }
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, 0]
        mock_proc.stderr.readline.side_effect = [
            "size=    100kB time=00:01:00.00 bitrate= 100.0kbits/s\n",
            "",
            "",
        ]
        mock_proc.stderr.read.return_value = ""
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.read.return_value = ""
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        from app.cutter import transcode_audio_track_from_source
        result = transcode_audio_track_from_source("/media/test.mkv", 1, "job123")

        assert "srcaudio1" in result
        assert result.endswith(".mp4")

        # Verify FFmpeg was called with audio master as input (not source)
        call_args = mock_popen.call_args[0][0]
        assert "-i" in call_args
        input_idx = call_args.index("-i")
        assert call_args[input_idx + 1] == "/tmp/audio_master.mka"
        assert "-vn" in call_args
        assert "-c:a" in call_args
        assert "aac" in call_args
        # Channels > 6 should trigger downmix
        assert "-ac" in call_args
        assert "2" in call_args

    @patch("app.cutter.probe_file")
    @patch("app.cutter.os.path.isfile", return_value=True)
    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    @patch("app.cutter._begin_job_operation")
    @patch("app.cutter._end_job_operation")
    def test_returns_cached_file_if_exists(
        self, mock_end, mock_begin, mock_getmtime, mock_isfile, mock_probe,
    ):
        mock_probe.return_value = {
            "duration": 60.0,
            "audio_streams": [{"index": 1, "codec": "truehd", "channels": 2}],
        }
        from app.cutter import transcode_audio_track_from_source
        result = transcode_audio_track_from_source("/media/test.mkv", 1, "job123")
        assert "srcaudio1" in result


class TestStartBackgroundAudioTranscode:

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    @patch("app.cutter.os.path.isfile", return_value=True)
    def test_skips_if_file_already_exists(self, mock_isfile, mock_getmtime):
        from app.cutter import start_background_audio_transcode
        # Should not raise or start a thread
        start_background_audio_transcode("/media/test.mkv", 1, "job123")

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    @patch("app.cutter.os.path.isfile", return_value=False)
    def test_starts_background_thread(self, mock_isfile, mock_getmtime):
        import threading
        from app.cutter import start_background_audio_transcode

        started = threading.Event()
        original_called = threading.Event()

        def mock_transcode(*args, **kwargs):
            started.set()
            original_called.wait(timeout=2)

        with patch("app.cutter.transcode_audio_track_from_source", side_effect=mock_transcode):
            start_background_audio_transcode("/media/test.mkv", 1, "job_bg")
            assert started.wait(timeout=2), "Background thread did not start"
            # Second call should be a no-op (already in progress)
            start_background_audio_transcode("/media/test.mkv", 1, "job_bg")
            original_called.set()


class TestGetAudioTranscodeStatus:

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    @patch("app.cutter.os.path.isfile", return_value=False)
    def test_returns_idle_when_no_status(self, mock_isfile, mock_getmtime):
        from app.cutter import get_audio_transcode_status
        status = get_audio_transcode_status("/media/nonexistent.mkv", "jobX", 1)
        assert status["state"] == "idle"
        assert status["ready"] is False

    @patch("app.cutter.os.path.getmtime", return_value=1000.0)
    @patch("app.cutter.os.path.isfile", return_value=True)
    def test_returns_done_when_file_exists(self, mock_isfile, mock_getmtime):
        from app.cutter import get_audio_transcode_status
        status = get_audio_transcode_status("/media/test.mkv", "job123", 1)
        assert status["state"] == "done"
        assert status["ready"] is True
