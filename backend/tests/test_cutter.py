import json
import threading
import types
from datetime import datetime, timezone

import pytest

from app import cutter
from app import hwaccel


def test_audio_relative_index_raises_for_unknown_stream():
    audio_streams = [{"index": 1}, {"index": 3}]
    with pytest.raises(RuntimeError, match="Audio stream index 2 not found"):
        cutter._audio_relative_index(audio_streams, 2)


def test_audio_relative_index_returns_correct_relative_index():
    audio_streams = [{"index": 1}, {"index": 3}, {"index": 5}]
    assert cutter._audio_relative_index(audio_streams, 1) == 0
    assert cutter._audio_relative_index(audio_streams, 3) == 1
    assert cutter._audio_relative_index(audio_streams, 5) == 2


def test_probe_file_includes_audio_bitrate(monkeypatch):
    fake_output = json.dumps(
        {
            "format": {
                "duration": "60.0",
                "bit_rate": "1000000",
                "format_name": "matroska",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "48000",
                    "bit_rate": "192000",
                    "tags": {},
                },
                {
                    "index": 2,
                    "codec_type": "audio",
                    "codec_name": "ac3",
                    "channels": 6,
                    "sample_rate": "48000",
                    "bit_rate": "384000",
                    "tags": {"language": "ger"},
                },
            ],
        }
    )
    monkeypatch.setattr(
        cutter.subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout=fake_output, stderr=""),
    )

    info = cutter.probe_file("/fake/file.mkv")
    assert info["audio_streams"][0]["bit_rate"] == 192000
    assert info["audio_streams"][1]["bit_rate"] == 384000
    # Video stream has no bit_rate; estimated from container total minus audio
    # 1000000 - 192000 - 384000 = 424000
    assert info["video_bitrate"] == 424000


def test_get_track_preview_uses_mp4_output_and_absolute_track_cache_key(tmp_path, monkeypatch):
    job_id = "job-1"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "_preview_cache_key", lambda _path: "abc123")
    monkeypatch.setattr(cutter, "_audio_relative_index", lambda _streams, _idx: 1)
    monkeypatch.setattr(cutter, "probe_file", lambda _path: {"audio_streams": [{"index": 7}]})

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

    source, job_id, base, path = cutter.decode_file_id(encoded)
    assert source == "server"
    assert job_id == "11111111-1111-1111-1111-111111111111"
    assert base == ""
    assert path == "Movies/Show Name/Season 01/Episode:01.mkv"


def test_decode_file_id_rejects_malformed_input():
    with pytest.raises(ValueError, match="Invalid file_id"):
        cutter.decode_file_id("!!!not-base64!!!")


def test_decode_file_id_handles_unpadded_base64():
    encoded = cutter.encode_file_id("upload", "clip.mp4", "")
    unpadded = encoded.rstrip("=")

    source, job_id, base, path = cutter.decode_file_id(unpadded)
    assert source == "upload"
    assert job_id == ""
    assert base == ""
    assert path == "clip.mp4"


def test_encode_decode_file_id_roundtrip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    import importlib, app.config
    importlib.reload(app.config)
    importlib.reload(cutter)

    file_id = cutter.encode_file_id("server", "path/to/file.mp4", job_id="", base="media")
    source, job_id, base, path = cutter.decode_file_id(file_id)
    assert source == "server"
    assert path == "path/to/file.mp4"
    assert base == "media"


def test_decode_file_id_rejects_tampered_signature(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    import importlib, app.config
    importlib.reload(app.config)
    importlib.reload(cutter)

    file_id = cutter.encode_file_id("server", "path/to/file.mp4")
    # Tamper with the file_id by flipping a character
    import base64
    decoded = base64.urlsafe_b64decode(file_id + "==").decode("utf-8")
    tampered = decoded[:-1] + ("a" if decoded[-1] != "a" else "b")
    tampered_id = base64.urlsafe_b64encode(tampered.encode("utf-8")).decode("ascii")

    with pytest.raises(ValueError, match="signature"):
        cutter.decode_file_id(tampered_id)


def test_decode_file_id_rejects_unsigned_legacy_format(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-key")
    import importlib, app.config
    importlib.reload(app.config)
    importlib.reload(cutter)

    import base64
    raw = "server||media|path/to/file.mp4"
    unsigned_id = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    with pytest.raises(ValueError, match="signature"):
        cutter.decode_file_id(unsigned_id)


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


def test_cut_file_multi_track_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = None
        stdout = None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return 0

    def fake_popen(cmd, **_kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [
        {"index": 1, "bit_rate": 192000},
        {"index": 3, "bit_rate": 384000},
        {"index": 5, "bit_rate": 128000},
    ]
    audio_tracks = [
        {"index": 1, "mode": "passthru", "codec": None},
        {"index": 3, "mode": "reencode", "codec": "aac"},
        {"index": 5, "mode": "remove", "codec": None},
    ]

    out = tmp_path / "output.mkv"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mkv",
        in_point=10.0,
        out_point=40.0,
        output_path=str(out),
        stream_copy=True,
        codec=None,
        audio_tracks=audio_tracks,
        container="mkv",
        progress_cb=lambda _msg: None,
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    assert "-map" in cmd
    map_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "0:v?" in map_args
    assert "0:a:0" in map_args
    assert "0:a:1" in map_args
    assert "0:a:2" not in map_args

    v_idx = cmd.index("-c:v")
    assert cmd[v_idx + 1] == "copy"

    a0_idx = cmd.index("-c:a:0")
    assert cmd[a0_idx + 1] == "copy"

    a1_idx = cmd.index("-c:a:1")
    assert cmd[a1_idx + 1] == "aac"


def test_cut_file_keep_quality_adds_bitrate_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)
    monkeypatch.setattr(hwaccel, "_detected", True)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = None
        stdout = None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return 0

    def fake_popen(cmd, **_kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 1, "bit_rate": 192000}]
    audio_tracks = [{"index": 1, "mode": "reencode", "codec": "aac"}]

    out = tmp_path / "output.mp4"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mp4",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=False,
        codec="libx264",
        audio_tracks=audio_tracks,
        container="mp4",
        progress_cb=lambda _msg: None,
        keep_quality=True,
        source_video_bitrate=5000000,
        source_audio_bitrates={1: 192000},
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    bv_idx = cmd.index("-b:v")
    assert cmd[bv_idx + 1] == "5000000"
    ba_idx = cmd.index("-b:a:0")
    assert cmd[ba_idx + 1] == "192000"


def test_cut_file_keep_quality_skips_zero_bitrate(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)
    monkeypatch.setattr(hwaccel, "_detected", True)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = None
        stdout = None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return 0

    def fake_popen(cmd, **_kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 1, "bit_rate": 0}]
    audio_tracks = [{"index": 1, "mode": "reencode", "codec": "aac"}]

    out = tmp_path / "output.mp4"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mp4",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=False,
        codec="libx264",
        audio_tracks=audio_tracks,
        container="mp4",
        progress_cb=lambda _msg: None,
        keep_quality=True,
        source_video_bitrate=0,
        source_audio_bitrates={1: 0},
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    assert "-b:v" not in cmd
    assert "-b:a:0" not in cmd


def test_cut_file_all_tracks_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = None
        stdout = None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return 0

    def fake_popen(cmd, **_kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 1, "bit_rate": 192000}]
    audio_tracks = [{"index": 1, "mode": "remove", "codec": None}]

    out = tmp_path / "output.mkv"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mkv",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=True,
        codec=None,
        audio_tracks=audio_tracks,
        container="mkv",
        progress_cb=lambda _msg: None,
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    map_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "0:v?" in map_args
    assert not any(arg.startswith("0:a:") for arg in map_args)


def test_cut_file_audio_only_per_track(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = None
        stdout = None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return 0

    def fake_popen(cmd, **_kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 0, "bit_rate": 320000}]
    audio_tracks = [{"index": 0, "mode": "reencode", "codec": "aac"}]

    out = tmp_path / "output.m4a"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.flac",
        in_point=0.0,
        out_point=60.0,
        output_path=str(out),
        stream_copy=False,
        codec=None,
        audio_tracks=audio_tracks,
        container="m4a",
        progress_cb=lambda _msg: None,
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    map_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "0:v?" in map_args
    a0_idx = cmd.index("-c:a:0")
    assert cmd[a0_idx + 1] == "aac"
    assert "-c:v" not in cmd


def test_cut_file_backwards_compat_no_audio_tracks(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = None
        stdout = None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return 0

    def fake_popen(cmd, **_kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    out = tmp_path / "output.mp4"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mp4",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=True,
        codec=None,
        audio_tracks=None,
        container="mp4",
        progress_cb=lambda _msg: None,
    )

    cmd = captured_cmd["cmd"]
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"


# ---------------------------------------------------------------------------
# Job migration tests
# ---------------------------------------------------------------------------


def _write_job_json(jobs_dir, job_id, meta):
    """Helper: write a job.json into a UUID-named subdirectory."""
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(meta))


def test_migrate_jobs_adds_base_to_old_job(tmp_path, monkeypatch):
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _write_job_json(tmp_path, job_id, {
        "job_id": job_id,
        "source": "server",
        "original_name": "clip.mkv",
        "original_path": "Movies/clip.mkv",
        "status": "ready",
    })

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "BASE_PATH_LABELS", {"media": "/media"})

    count = cutter.migrate_jobs()

    assert count == 1
    meta = json.loads((tmp_path / job_id / "job.json").read_text())
    assert meta["base"] == "media"
    assert meta["schema_version"] == 2


def test_migrate_jobs_infers_base_from_absolute_path(tmp_path, monkeypatch):
    # Use real temp dirs so os.path.realpath works cross-platform
    nas1 = tmp_path / "nas1"
    nas2 = tmp_path / "nas2"
    nas1.mkdir()
    nas2.mkdir()
    (nas2 / "Movies").mkdir()

    job_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    _write_job_json(jobs_dir, job_id, {
        "job_id": job_id,
        "source": "server",
        "original_name": "clip.mkv",
        "original_path": str(nas2 / "Movies" / "clip.mkv"),
        "status": "ready",
    })

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(jobs_dir))
    monkeypatch.setattr(
        cutter, "BASE_PATH_LABELS",
        {"nas1": str(nas1), "nas2": str(nas2)},
    )

    cutter.migrate_jobs()

    meta = json.loads((jobs_dir / job_id / "job.json").read_text())
    assert meta["base"] == "nas2"


def test_migrate_jobs_skips_already_migrated(tmp_path, monkeypatch):
    job_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    _write_job_json(tmp_path, job_id, {
        "job_id": job_id,
        "source": "server",
        "original_name": "clip.mkv",
        "original_path": "clip.mkv",
        "base": "media",
        "schema_version": 2,
        "status": "ready",
    })

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "BASE_PATH_LABELS", {"media": "/media"})

    count = cutter.migrate_jobs()
    assert count == 0


def test_migrate_jobs_survives_corrupt_json(tmp_path, monkeypatch):
    job_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text("{corrupt json!!!")

    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "BASE_PATH_LABELS", {"media": "/media"})

    count = cutter.migrate_jobs()
    assert count == 0


def test_create_job_includes_schema_version(tmp_path, monkeypatch):
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))

    job_id = cutter.create_job(
        source="upload",
        original_name="clip.mp4",
        original_path="clip.mp4",
        base="media",
    )

    meta = cutter.load_job_metadata(job_id)
    assert meta["schema_version"] == 2
