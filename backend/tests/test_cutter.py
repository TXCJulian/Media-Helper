import json
import threading
import types
from datetime import datetime, timezone

import pytest

from app import cutter


def test_audio_relative_index_raises_for_unknown_stream(monkeypatch):
    monkeypatch.setattr(
        cutter,
        "probe_file",
        lambda _path: {
            "audio_streams": [
                {"index": 1},
                {"index": 3},
            ]
        },
    )

    with pytest.raises(RuntimeError, match="Audio stream index 2 not found"):
        cutter._audio_relative_index("demo.mkv", 2)


def test_get_track_preview_uses_mp4_output_and_absolute_track_cache_key(tmp_path, monkeypatch):
    job_id = "job-1"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "_preview_cache_key", lambda _path: "abc123")
    monkeypatch.setattr(cutter, "_audio_relative_index", lambda _path, _idx: 1)

    def fake_isfile(_path):
        return False

    monkeypatch.setattr(cutter.os.path, "isfile", fake_isfile)

    captured = {}

    class FakePipe:
        def close(self):
            return None

    class FakePopen:
        def __init__(self, cmd, stdout=None, stderr=None):
            captured["cmd"] = cmd
            out_path = cmd[-1]
            with open(out_path, "wb") as fp:
                fp.write(b"ok")
            self.returncode = 0
            self.stdout = FakePipe()
            self.stderr = FakePipe()

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -1

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

        def communicate(self, timeout=None):
            return (b"", b"")

    monkeypatch.setattr(cutter.subprocess, "Popen", FakePopen)

    replaced = {}

    def fake_replace(src, dst):
        replaced["src"] = src
        replaced["dst"] = dst

    monkeypatch.setattr(cutter.os, "replace", fake_replace)

    result = cutter.get_track_preview("master.mp4", 7, "source.mkv", job_id)

    assert result.endswith("preview_abc123_trackabs7.mp4")
    assert replaced["dst"].endswith("preview_abc123_trackabs7.mp4")
    assert captured["cmd"][captured["cmd"].index("-f") + 1] == "mp4"
    assert captured["cmd"][-1].endswith(".tmp.mp4")


def test_get_preview_status_defaults_to_idle_when_not_ready(monkeypatch):
    monkeypatch.setattr(cutter, "_preview_cache_key", lambda _path: "abc123")
    monkeypatch.setattr(cutter, "get_preview_path_if_ready", lambda _path, _job: None)

    status = cutter.get_preview_status("source.mkv", "job-1")

    assert status["state"] == "idle"
    assert status["ready"] is False
    assert status["percent"] == 0.0


def test_delete_job_cancels_active_operations_and_removes_dir(tmp_path, monkeypatch):
    job_id = "11111111-1111-1111-1111-111111111111"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "preview.tmp.mp4").write_bytes(b"temp")

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))

    cancel_event = threading.Event()
    cutter._begin_job_operation(job_id, cancel_event)

    class FakeProc:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.terminated = True
            return 0

    proc = FakeProc()
    cutter._register_job_process(job_id, proc)

    def release_when_cancelled():
        assert cancel_event.wait(timeout=2)
        cutter._unregister_job_process(job_id, proc)
        cutter._end_job_operation(job_id, cancel_event)

    thread = threading.Thread(target=release_when_cancelled)
    thread.start()

    cutter.delete_job(job_id)

    thread.join(timeout=2)
    assert cancel_event.is_set() is True
    assert proc.terminated is True
    assert not job_dir.exists()


def test_cleanup_old_jobs_skips_active_jobs_and_deletes_inactive_expired(tmp_path, monkeypatch):
    active_job = "22222222-2222-2222-2222-222222222222"
    inactive_job = "33333333-3333-3333-3333-333333333333"

    active_dir = tmp_path / active_job
    inactive_dir = tmp_path / inactive_job
    active_dir.mkdir(parents=True)
    inactive_dir.mkdir(parents=True)

    expired = datetime.now(timezone.utc).timestamp() - 3600
    payload = {
        "job_id": active_job,
        "source": "server",
        "original_name": "clip.mkv",
        "original_path": "clip.mkv",
        "created_at": datetime.fromtimestamp(expired, tz=timezone.utc).isoformat(),
        "status": "ready",
        "cut_settings": None,
        "output_files": [],
    }
    (active_dir / "job.json").write_text(json.dumps(payload))
    payload["job_id"] = inactive_job
    (inactive_dir / "job.json").write_text(json.dumps(payload))

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "CUTTER_JOB_TTL", 60)

    cancel_event = threading.Event()
    cutter._begin_job_operation(active_job, cancel_event)

    cutter.cleanup_old_jobs()

    assert active_dir.exists()
    assert not inactive_dir.exists()

    cutter._end_job_operation(active_job, cancel_event)
    cutter._clear_job_runtime_state(active_job)


def test_encode_decode_file_id_round_trip():
    encoded = cutter.encode_file_id(
        "server",
        "Movies/Show Name/Season 01/Episode:01.mkv",
        "11111111-1111-1111-1111-111111111111",
    )

    source, job_id, path = cutter.decode_file_id(encoded)
    assert source == "server"
    assert job_id == "11111111-1111-1111-1111-111111111111"
    assert path == "Movies/Show Name/Season 01/Episode:01.mkv"


def test_decode_file_id_rejects_malformed_input():
    with pytest.raises(ValueError, match="Invalid file_id"):
        cutter.decode_file_id("!!!not-base64!!!")


def test_decode_file_id_handles_unpadded_base64():
    encoded = cutter.encode_file_id("upload", "clip.mp4", "")
    unpadded = encoded.rstrip("=")

    source, job_id, path = cutter.decode_file_id(unpadded)
    assert source == "upload"
    assert job_id == ""
    assert path == "clip.mp4"


@pytest.mark.parametrize(
    ("audio_codec", "filepath", "video_codec", "expected"),
    [
        ("aac", "clip.mkv", "h264", True),
        ("aac", "clip.mp4", "h264", False),
        ("dts", "clip.mp4", "h264", True),
        ("unknown", "clip.mp4", "h264", True),
    ],
)
def test_needs_transcoding_cases(audio_codec, filepath, video_codec, expected):
    assert cutter.needs_transcoding(audio_codec, filepath, video_codec) is expected


def test_get_job_dir_rejects_non_uuid(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="Invalid job_id format"):
        cutter.get_job_dir("not-a-uuid")
