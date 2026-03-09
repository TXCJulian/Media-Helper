"""Tests for FastAPI endpoints."""
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
