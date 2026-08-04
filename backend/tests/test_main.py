"""Tests for FastAPI endpoints."""
import json
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "base_paths" in data


class TestConfigEndpoint:
    def test_config_returns_base_paths(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "features" in data
        assert "base_paths" in data
        assert isinstance(data["base_paths"], list)
        assert len(data["base_paths"]) >= 1


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
        paths = [d["path"] for d in dirs]
        assert any("Breaking Bad" in p for p in paths)

    def test_season_filter(self, client, tmp_media_dir):
        show_dir = tmp_media_dir / "TV Shows" / "TestShow" / "Season 02"
        show_dir.mkdir(parents=True)
        (show_dir / "ep.mp4").write_bytes(b"\x00")

        client.post("/directories/refresh")

        resp = client.get("/directories/tvshows", params={"season": "2"})
        assert resp.status_code == 200
        dirs = resp.json()["directories"]
        paths = [d["path"] for d in dirs]
        assert all("season 02" in p.lower() for p in paths)

    def test_directories_have_base_field(self, client, tmp_media_dir):
        show_dir = tmp_media_dir / "TV Shows" / "SomeShow" / "Season 01"
        show_dir.mkdir(parents=True)
        (show_dir / "ep.mp4").write_bytes(b"\x00")

        client.post("/directories/refresh")

        resp = client.get("/directories/tvshows")
        dirs = resp.json()["directories"]
        assert len(dirs) > 0
        assert "path" in dirs[0]
        assert "base" in dirs[0]

    def test_media_directories_available_for_download_feature(self, client, monkeypatch):
        import app.main as main_mod

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"download"})
        monkeypatch.setattr(
            main_mod,
            "_get_cutter_dirs_cached",
            lambda: [{"path": "Downloads", "base": "media"}],
        )

        resp = client.get("/directories/media")

        assert resp.status_code == 200
        assert resp.json()["directories"] == [{"path": "Downloads", "base": "media"}]


class TestInputValidation:
    def test_threshold_out_of_range(self, client, base_label):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": 1,
            "directory": "test",
            "base": base_label,
            "dry_run": True,
            "assign_seq": False,
            "threshold": 2.0,
            "lang": "en",
        })
        assert resp.status_code == 422

    def test_negative_season(self, client, base_label):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": -1,
            "directory": "test",
            "base": base_label,
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 422

    def test_series_too_long(self, client, base_label):
        resp = client.post("/rename/episodes", data={
            "series": "x" * 300,
            "season": 1,
            "directory": "test",
            "base": base_label,
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 422


class TestPathTraversal:
    def test_episode_rename_path_traversal(self, client, base_label):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": 1,
            "directory": "../../../etc",
            "base": base_label,
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 400

    def test_music_rename_path_traversal(self, client, base_label):
        resp = client.post("/rename/music", data={
            "directory": "../../../etc",
            "base": base_label,
            "dry_run": True,
        })
        assert resp.status_code == 400

    def test_transcribe_files_path_traversal(self, client, base_label, monkeypatch):
        # 'lyrics' is off in the default feature set, so require_feature() would
        # short-circuit with a 404 and the traversal check would never run.
        import app.main as main_mod

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"lyrics"})

        resp = client.get("/transcribe/files", params={
            "directory": "../../../etc",
            "base": base_label,
        })
        assert resp.status_code == 400

    def test_unknown_base_returns_400(self, client):
        resp = client.post("/rename/episodes", data={
            "series": "test",
            "season": 1,
            "directory": "test",
            "base": "nonexistent_base",
            "dry_run": True,
            "assign_seq": False,
            "threshold": 0.5,
            "lang": "en",
        })
        assert resp.status_code == 400
        assert "Unknown base" in resp.json()["detail"]


class TestDownloaderEndpoints:
    def test_download_status_shape(self, client):
        resp = client.get("/download/status")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {
            "yt_dlp_version",
            "cookies_present",
            "downloads_dir",
            "queue_depth",
            "workers",
        }

    def test_download_create_returns_job_ids_without_starting_work(self, client):
        resp = client.post(
            "/download",
            json={
                "urls": ["https://example.com/watch?v=demo"],
                "options": {"type": "video", "format": "mp4", "auto_start": False},
            },
        )

        assert resp.status_code == 200
        job_ids = resp.json()["job_ids"]
        assert len(job_ids) == 1

        job = client.get(f"/download/jobs/{job_ids[0]}").json()
        assert job["stage"] == "queued"
        assert job["url"] == "https://example.com/watch?v=demo"

    def test_download_create_rejects_non_http_url(self, client):
        resp = client.post(
            "/download", json={"urls": ["ftp://example.com/x"], "options": {}}
        )

        assert resp.status_code == 422

    def test_download_delete_unknown_job_returns_404(self, client):
        resp = client.delete("/download/jobs/11111111-1111-1111-1111-111111111111")

        assert resp.status_code == 404

    def test_download_cookie_upload_writes_file_bytes(self, client):
        import app.downloader.routes as routes_mod

        resp = client.post(
            "/download/cookies",
            files={"file": ("cookies.txt", b"cookie-data", "text/plain")},
        )

        assert resp.status_code == 200
        with open(routes_mod.cookie_path(), "rb") as f:
            assert f.read() == b"cookie-data"

    def test_download_routes_absent_when_feature_disabled(self, tmp_path):
        """The feature flag gates route registration, not just the handler."""
        import importlib

        with patch.dict(os.environ, {
            "BASE_PATHS": str(tmp_path),
            "TMDB_API_KEY": "test_key",
            "AUTH_USERNAME": "",
            "AUTH_PASSWORD": "",
            "SECRET_KEY": "test-secret-key",
            "ENABLED_FEATURES": "episodes",
        }):
            import app.config as config_mod
            importlib.reload(config_mod)
            import app.auth as auth_mod
            importlib.reload(auth_mod)
            import app.downloader.routes as routes_mod
            importlib.reload(routes_mod)
            import app.main as main_mod
            importlib.reload(main_mod)

            with TestClient(main_mod.app) as c:
                paths = c.get("/openapi.json").json()["paths"]
                assert not any(p.startswith("/download") for p in paths)
                assert c.get("/download/status").status_code == 404
                # No store or worker pool was built either.
                assert routes_mod._store is None
                assert routes_mod._queue is None


class TestCutterStreamValidation:
    def test_cutter_stream_rejects_invalid_audio_index(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mkv"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "", "", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="", base_label="": str(media_file),
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

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "", "", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="", base_label="": str(media_file),
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

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "job-1", "", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="", base_label="": str(media_file),
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
        transcode_called = False

        def _no_transcode(*_args, **_kwargs):
            nonlocal transcode_called
            transcode_called = True

        monkeypatch.setattr(main_mod, "start_background_transcode", _no_transcode)

        resp = client.get("/cutter/stream/demo")
        assert resp.status_code == 200
        assert resp.content == b"demo"
        assert not transcode_called, "start_background_transcode should not be called without transcode=true"

    def test_cutter_stream_transcodes_when_flag_enabled(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mkv"
        preview_file = tmp_path / "preview.mp4"
        media_file.write_bytes(b"orig")
        preview_file.write_bytes(b"preview")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "job-1", "", "clip.mkv"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="", base_label="": str(media_file),
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
        monkeypatch.setattr(main_mod, "get_preview_path_if_ready", lambda *_args, **_kwargs: str(preview_file))
        transcode_called = False

        def _fake_transcode(*_args, **_kwargs):
            nonlocal transcode_called
            transcode_called = True

        monkeypatch.setattr(main_mod, "start_background_transcode", _fake_transcode)

        resp = client.get("/cutter/stream/demo", params={"transcode": "true"})
        assert resp.status_code == 200
        assert resp.content == b"preview"
        assert transcode_called, "start_background_transcode should be called when transcode=true"

    def test_stream_returns_409_when_preview_not_ready(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mp4"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "job-1", "", "clip.mp4"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="", base_label="": str(media_file),
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
        monkeypatch.setattr(main_mod, "start_background_transcode", lambda *_args, **_kwargs: None)

        resp = client.get("/cutter/stream/demo", params={"transcode": "true"})
        assert resp.status_code == 409


class TestCutterPreviewStatus:
    def test_preview_status_non_transcoding_is_done(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "clip.mp4"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "decode_file_id", lambda _file_id: ("server", "", "", "clip.mp4"))
        monkeypatch.setattr(
            main_mod,
            "resolve_cutter_path",
            lambda _path, _source, _job_id="", base_label="": str(media_file),
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

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})

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

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
        monkeypatch.setattr(main_mod, "get_job_dir", lambda _job_id: str(tmp_path))
        monkeypatch.setattr(main_mod, "resolve_cutter_path", lambda *_args, **_kwargs: str(media_file))

        resp = client.post(
            "/cutter/cut",
            data={
                "path": "clip.mp4",
                "source": "server",
                "base": "",
                "job_id": "11111111-1111-1111-1111-111111111111",
                "in_point": "10",
                "out_point": "5",
                "stream_copy": "true",
            },
        )

        assert resp.status_code == 422
        assert "out_point" in resp.json()["detail"]

    def test_resolve_cutter_path_blocks_server_traversal(self, client, base_label):
        import app.main as main_mod
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            main_mod.resolve_cutter_path("../../../etc/passwd", "server", base_label=base_label)

        assert exc_info.value.status_code == 400

    def test_cutter_cut_rejects_invalid_audio_track_codec(self, client, tmp_path, monkeypatch):
        import app.main as main_mod

        media_file = tmp_path / "test.mp4"
        media_file.write_bytes(b"demo")

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"episodes", "music", "cutter"})
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
                "base": "",
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


class TestTranscribeStart:
    """Regression tests for /transcribe/start file selection."""

    def _setup(self, monkeypatch, tmp_media_dir, names):
        import app.main as main_mod

        album = tmp_media_dir / "Music" / "SSIO" / "BB.U.M.SS.N"
        album.mkdir(parents=True)
        for name in names:
            (album / name).write_bytes(b"\x00" * 100)

        monkeypatch.setattr(main_mod, "ENABLED_FEATURES_SET", {"lyrics", "music"})
        monkeypatch.setattr(main_mod, "TRANSCRIBER_URL", "http://transcriber:3334")
        monkeypatch.setattr(
            main_mod, "transcribe_file", lambda **_kwargs: ([], None)
        )
        return "SSIO/BB.U.M.SS.N"

    def test_filename_with_comma_is_selected(
        self, client, tmp_media_dir, base_label, monkeypatch
    ):
        comma_name = "01-13 Illegal, legal, egal....flac"
        directory = self._setup(
            monkeypatch, tmp_media_dir, [comma_name, "01-01 Other.flac"]
        )

        resp = client.post(
            "/transcribe/start",
            data={
                "directory": directory,
                "base": base_label,
                "files": json.dumps([comma_name]),
                "output_format": "lrc",
                "skip_existing": "false",
            },
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "event: done" in resp.text
        assert "Completed: 1/1" in resp.text

    def test_no_matching_files_still_returns_sse(
        self, client, tmp_media_dir, base_label, monkeypatch
    ):
        directory = self._setup(monkeypatch, tmp_media_dir, ["01-01 Other.flac"])

        resp = client.post(
            "/transcribe/start",
            data={
                "directory": directory,
                "base": base_label,
                "files": json.dumps(["does-not-exist.flac"]),
                "output_format": "lrc",
            },
        )

        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "event: error_msg" in resp.text
        assert "event: done" in resp.text

    def test_rejects_unknown_whisper_model(
        self, client, tmp_media_dir, base_label, monkeypatch
    ):
        directory = self._setup(monkeypatch, tmp_media_dir, ["01-01 Other.flac"])

        resp = client.post(
            "/transcribe/start",
            data={
                "directory": directory,
                "base": base_label,
                "output_format": "lrc",
                "whisper_model": "definitely-not-a-model",
            },
        )

        assert resp.status_code == 422
        assert "Invalid whisper model" in resp.json()["detail"]

    def test_overrides_applied_for_single_file(
        self, client, tmp_media_dir, base_label, monkeypatch
    ):
        import app.main as main_mod

        directory = self._setup(monkeypatch, tmp_media_dir, ["01-01 Other.flac"])
        calls: list[dict] = []
        monkeypatch.setattr(
            main_mod, "transcribe_file", lambda **kw: (calls.append(kw), ([], None))[1]
        )

        resp = client.post(
            "/transcribe/start",
            data={
                "directory": directory,
                "base": base_label,
                "output_format": "lrc",
                "skip_existing": "false",
                "whisper_model": "medium",
                "artist_override": "SSIO",
                "title_override": "Nullkommaneun",
            },
        )

        assert "event: done" in resp.text
        assert len(calls) == 1
        assert calls[0]["whisper_model"] == "medium"
        assert calls[0]["artist"] == "SSIO"
        assert calls[0]["title"] == "Nullkommaneun"

    def test_overrides_ignored_for_multi_file(
        self, client, tmp_media_dir, base_label, monkeypatch
    ):
        import app.main as main_mod

        directory = self._setup(
            monkeypatch, tmp_media_dir, ["01-01 A.flac", "01-02 B.flac"]
        )
        calls: list[dict] = []
        monkeypatch.setattr(
            main_mod, "transcribe_file", lambda **kw: (calls.append(kw), ([], None))[1]
        )

        resp = client.post(
            "/transcribe/start",
            data={
                "directory": directory,
                "base": base_label,
                "output_format": "lrc",
                "skip_existing": "false",
                "artist_override": "SSIO",
                "title_override": "Nullkommaneun",
            },
        )

        assert "event: done" in resp.text
        assert len(calls) == 2
        assert all(c["title"] != "Nullkommaneun" for c in calls)


class TestRenameMusicLyricsAction:
    def test_rejects_unknown_lyrics_action(self, client, base_label):
        resp = client.post("/rename/music", data={
            "directory": "ABBA",
            "base": base_label,
            "dry_run": True,
            "lyrics_action": "shred",
        })
        assert resp.status_code == 422
        assert "Invalid lyrics action" in resp.json()["detail"]

    def test_action_is_forwarded(self, client, tmp_media_dir, base_label, monkeypatch):
        import app.main as main_mod

        album = tmp_media_dir / "Music" / "ABBA"
        album.mkdir(parents=True)
        (album / "song.flac").write_bytes(b"\x00")

        seen: dict = {}
        monkeypatch.setattr(
            main_mod, "rename_music", lambda **kw: (seen.update(kw), ([], None))[1]
        )

        resp = client.post("/rename/music", data={
            "directory": "ABBA",
            "base": base_label,
            "dry_run": True,
            "lyrics_action": "delete",
        })

        assert resp.status_code == 200
        assert seen["lyrics_action"] == "delete"

    def test_defaults_to_rename(self, client, tmp_media_dir, base_label, monkeypatch):
        import app.main as main_mod

        album = tmp_media_dir / "Music" / "ABBA"
        album.mkdir(parents=True)
        (album / "song.flac").write_bytes(b"\x00")

        seen: dict = {}
        monkeypatch.setattr(
            main_mod, "rename_music", lambda **kw: (seen.update(kw), ([], None))[1]
        )

        client.post("/rename/music", data={
            "directory": "ABBA",
            "base": base_label,
            "dry_run": True,
        })

        assert seen["lyrics_action"] == "rename"


def test_upload_cookies_rejects_oversized_file(client):
    """Cookie files should be limited to 1 MB."""
    huge = b"x" * (1024 * 1024 + 1)
    response = client.post(
        "/download/cookies",
        files={"file": ("cookies.txt", huge, "text/plain")},
    )
    assert response.status_code == 413


def test_upload_cookies_accepts_valid_netscape_format(client):
    import app.downloader.routes as routes_mod

    content = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n"
    response = client.post(
        "/download/cookies",
        files={"file": ("cookies.txt", content, "text/plain")},
    )
    assert response.status_code == 200
    with open(routes_mod.cookie_path(), "rb") as f:
        assert f.read() == content
