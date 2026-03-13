import types

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

    def fake_run(cmd, capture_output, timeout):
        captured["cmd"] = cmd
        out_path = cmd[-1]
        with open(out_path, "wb") as fp:
            fp.write(b"ok")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(cutter.subprocess, "run", fake_run)

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
