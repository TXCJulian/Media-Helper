"""Tests for FastAPI endpoints."""
import json
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_media_dir):
    """Create a test client with mocked paths."""
    with patch.dict(os.environ, {
        "BASE_PATH": str(tmp_media_dir),
        "TVSHOW_FOLDER_NAME": "TV Shows",
        "MUSIC_FOLDER_NAME": "Music",
        "TMDB_API_KEY": "test_key",
    }):
        # Re-import to pick up patched env
        import importlib
        import app.main as main_mod
        importlib.reload(main_mod)

        # Override the module-level vars
        main_mod.BASE_PATH = str(tmp_media_dir)
        main_mod.TVSHOW_FOLDER_NAME = "TV Shows"
        main_mod.MUSIC_FOLDER_NAME = "Music"

        with TestClient(main_mod.app) as c:
            yield c


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestDirectoryEndpoints:
    def test_list_tvshows_empty(self, client):
        resp = client.get("/directories/tvshows")
        assert resp.status_code == 200
        assert "directories" in resp.json()

    def test_list_music_empty(self, client):
        resp = client.get("/directories/music")
        assert resp.status_code == 200
        assert "directories" in resp.json()

    def test_refresh_directories(self, client):
        resp = client.post("/directories/refresh")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_series_filter(self, client, tmp_media_dir):
        show_dir = tmp_media_dir / "TV Shows" / "Breaking Bad" / "Season 01"
        show_dir.mkdir(parents=True)
        (show_dir / "ep.mp4").write_bytes(b"\x00")

        # Clear cache
        client.post("/directories/refresh")

        resp = client.get("/directories/tvshows", params={"series": "Breaking"})
        assert resp.status_code == 200
        dirs = resp.json()["directories"]
        assert any("Breaking Bad" in d for d in dirs)

    def test_season_filter(self, client, tmp_media_dir):
        show_dir = tmp_media_dir / "TV Shows" / "TestShow" / "Season 02"
        show_dir.mkdir(parents=True)
        (show_dir / "ep.mp4").write_bytes(b"\x00")

        client.post("/directories/refresh")

        resp = client.get("/directories/tvshows", params={"season": "2"})
        assert resp.status_code == 200
        dirs = resp.json()["directories"]
        assert all("season 02" in d.lower() for d in dirs)


class TestInputValidation:
    def test_threshold_out_of_range(self, client):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": 1,
            "directory": "test",
            "dry_run": True,
            "assign_seq": False,
            "threshold": 2.0,
            "lang": "en",
        })
        assert resp.status_code == 422

    def test_negative_season(self, client):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": -1,
            "directory": "test",
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 422

    def test_series_too_long(self, client):
        resp = client.post("/rename/episodes", data={
            "series": "x" * 300,
            "season": 1,
            "directory": "test",
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 422


class TestPathTraversal:
    def test_episode_rename_path_traversal(self, client):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": 1,
            "directory": "../../../etc",
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 400

    def test_music_rename_path_traversal(self, client):
        resp = client.post("/rename/music", data={
            "directory": "../../../etc",
            "dry_run": True,
        })
        assert resp.status_code == 400

    def test_transcribe_files_path_traversal(self, client):
        resp = client.get("/transcribe/files", params={
            "directory": "../../../etc",
        })
        assert resp.status_code == 400


class TestCutterStreamValidation:
    def test_cutter_stream_rejects_invalid_audio_index(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mkv"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="": str(media_file),
        )
        monkeypatch.setattr(
            main_mod,
            "probe_file",
            lambda _path: {
                "audio_codec": "aac",
                "audio_streams": [{"index": 1}, {"index": 2}],
            },
        )
        monkeypatch.setattr(main_mod, "needs_transcoding", lambda *_args, **_kwargs: False)

        resp = client.get("/cutter/stream/demo", params={"audio_stream": 99})
        assert resp.status_code == 400
        assert "Invalid audio stream index" in resp.json()["detail"]

    def test_cutter_stream_allows_valid_audio_index(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mkv"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="": str(media_file),
        )
        monkeypatch.setattr(
            main_mod,
            "probe_file",
            lambda _path: {
                "audio_codec": "aac",
                "audio_streams": [{"index": 1}, {"index": 2}],
            },
        )
        monkeypatch.setattr(main_mod, "needs_transcoding", lambda *_args, **_kwargs: False)

        resp = client.get("/cutter/stream/demo", params={"audio_stream": 1})
        assert resp.status_code == 200
        assert resp.content == b"demo"

    def test_cutter_stream_does_not_transcode_without_flag(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mkv"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "job-1", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="": str(media_file),
        )
        monkeypatch.setattr(
            main_mod,
            "probe_file",
            lambda _path: {
                "audio_codec": "dts",
                "audio_streams": [{"index": 1}],
            },
        )
        monkeypatch.setattr(main_mod, "needs_transcoding", lambda *_args, **_kwargs: True)

        called = {"value": False}

        def fail_if_called(*_args, **_kwargs):
            called["value"] = True
            raise AssertionError("get_or_transcode_preview should not be called without transcode flag")

        monkeypatch.setattr(main_mod, "get_or_transcode_preview", fail_if_called)

        resp = client.get("/cutter/stream/demo")
        assert resp.status_code == 200
        assert resp.content == b"demo"
        assert called["value"] is False

    def test_cutter_stream_transcodes_when_flag_enabled(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mkv"
        preview_file = tmp_path / "preview.mp4"
        media_file.write_bytes(b"orig")
        preview_file.write_bytes(b"preview")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "job-1", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="": str(media_file),
        )
        monkeypatch.setattr(
            main_mod,
            "probe_file",
            lambda _path: {
                "audio_codec": "dts",
                "audio_streams": [{"index": 1}],
            },
        )
        monkeypatch.setattr(main_mod, "needs_transcoding", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(main_mod, "get_preview_status", lambda *_args, **_kwargs: {"state": "idle"})
        monkeypatch.setattr(main_mod, "get_preview_path_if_ready", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(main_mod, "wait_for_preview", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(main_mod, "get_or_transcode_preview", lambda *_args, **_kwargs: str(preview_file))

        resp = client.get("/cutter/stream/demo", params={"transcode": "true"})
        assert resp.status_code == 200
        assert resp.content == b"preview"


class TestCutterPreviewStatus:
    def test_preview_status_non_transcoding_is_done(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mp4"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "", "clip.mp4"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="": str(media_file),
        )
        monkeypatch.setattr(
            main_mod,
            "probe_file",
            lambda _path: {
                "audio_codec": "aac",
                "audio_streams": [{"index": 1}],
            },
        )
        monkeypatch.setattr(main_mod, "needs_transcoding", lambda *_args, **_kwargs: False)

        resp = client.get("/cutter/preview-status/demo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "done"
        assert data["ready"] is True
        assert data["percent"] == 100.0


class TestCutterDeleteJob:
    def test_delete_job_returns_conflict_for_busy_job(self, client, monkeypatch):
        import app.main as main_mod

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})

        def fake_delete_job(_job_id):
            raise RuntimeError("Job is still busy and could not be deleted")

        monkeypatch.setattr(main_mod, "delete_job", fake_delete_job)

        resp = client.delete("/cutter/jobs/11111111-1111-1111-1111-111111111111")

        assert resp.status_code == 409
        assert "still busy" in resp.json()["detail"]


class TestCutterValidation:
    def test_cutter_cut_rejects_out_point_before_in_point(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mp4"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "get_job_dir", lambda _job_id: str(tmp_path))
        monkeypatch.setattr(main_mod, "resolve_cutter_path", lambda *_args, **_kwargs: str(media_file))

        resp = client.post(
            "/cutter/cut",
            data={
                "path": "clip.mp4",
                "source": "server",
                "job_id": "11111111-1111-1111-1111-111111111111",
                "in_point": "10",
                "out_point": "5",
                "stream_copy": "true",
            },
        )

        assert resp.status_code == 422
        assert "out_point" in resp.json()["detail"]

    def test_resolve_cutter_path_blocks_server_traversal(self, tmp_path, monkeypatch):
        import app.main as main_mod
        from fastapi import HTTPException

        monkeypatch.setattr(main_mod, "BASE_PATH", str(tmp_path))

        with pytest.raises(HTTPException) as exc_info:
            main_mod.resolve_cutter_path("../../../etc/passwd", "server")

        assert exc_info.value.status_code == 400

    def test_cutter_cut_rejects_invalid_audio_track_codec(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "test.mp4"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "get_job_dir", lambda _job_id: str(tmp_path))
        monkeypatch.setattr(main_mod, "resolve_cutter_path", lambda *_args, **_kwargs: str(media_file))

        audio_tracks = json.dumps([
            {"index": 1, "mode": "reencode", "codec": "evil_codec"}
        ])
        response = client.post(
            "/cutter/cut",
            data={
                "path": "test.mp4",
                "source": "server",
                "job_id": "11111111-1111-1111-1111-111111111111",
                "in_point": "0",
                "out_point": "30",
                "stream_copy": "false",
                "codec": "libx264",
                "container": "mp4",
                "audio_tracks": audio_tracks,
                "keep_quality": "false",
            },
        )
        assert response.status_code == 422
        assert "Invalid audio track codec" in response.json()["detail"]
