"""Tests for session-based authentication middleware and auth endpoints."""
import importlib
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_client(tmp_media_dir):
    """TestClient with authentication enabled (admin / testpass123)."""
    env = {
        "BASE_PATHS": str(tmp_media_dir),
        "TVSHOW_FOLDER_NAME": "TV Shows",
        "MUSIC_FOLDER_NAME": "Music",
        "TMDB_API_KEY": "test_key",
        "AUTH_USERNAME": "admin",
        "AUTH_PASSWORD": "testpass123",
        "SECRET_KEY": "test-secret-key-for-auth-tests",
    }
    with patch.dict(os.environ, env):
        import app.config as config_mod
        importlib.reload(config_mod)
        import app.auth as auth_mod
        importlib.reload(auth_mod)
        import app.get_dirs as get_dirs_mod
        importlib.reload(get_dirs_mod)
        import app.main as main_mod
        importlib.reload(main_mod)

        with TestClient(main_mod.app) as c:
            yield c


@pytest.fixture
def noauth_client(tmp_media_dir):
    """TestClient with authentication disabled (empty credentials)."""
    env = {
        "BASE_PATHS": str(tmp_media_dir),
        "TVSHOW_FOLDER_NAME": "TV Shows",
        "MUSIC_FOLDER_NAME": "Music",
        "TMDB_API_KEY": "test_key",
        "AUTH_USERNAME": "",
        "AUTH_PASSWORD": "",
        "SECRET_KEY": "test-secret-key-for-noauth-tests",
    }
    with patch.dict(os.environ, env):
        import app.config as config_mod
        importlib.reload(config_mod)
        import app.auth as auth_mod
        importlib.reload(auth_mod)
        import app.get_dirs as get_dirs_mod
        importlib.reload(get_dirs_mod)
        import app.main as main_mod
        importlib.reload(main_mod)

        with TestClient(main_mod.app) as c:
            yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _do_login(client: TestClient, username: str = "admin", password: str = "testpass123"):
    """POST /auth/login and return the response."""
    return client.post("/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------------------
# TestAuthEnabled
# ---------------------------------------------------------------------------


class TestAuthEnabled:
    def test_protected_route_returns_401_without_session(self, auth_client):
        resp = auth_client.get("/directories/tvshows")
        assert resp.status_code == 401

    def test_exempt_routes_accessible_without_session(self, auth_client):
        for path in ("/health", "/auth/status"):
            resp = auth_client.get(path)
            assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"

    def test_config_requires_auth(self, auth_client):
        resp = auth_client.get("/config")
        assert resp.status_code == 401

    def test_login_with_valid_credentials(self, auth_client):
        resp = _do_login(auth_client)
        assert resp.status_code == 200
        assert "session" in resp.cookies

    def test_login_with_invalid_credentials(self, auth_client):
        resp = _do_login(auth_client, password="wrongpassword")
        assert resp.status_code == 401

    def test_authenticated_request_succeeds(self, auth_client):
        login_resp = _do_login(auth_client)
        assert login_resp.status_code == 200

        session_cookie = login_resp.cookies.get("session")
        assert session_cookie is not None

        resp = auth_client.get(
            "/directories/tvshows",
            cookies={"session": session_cookie},
        )
        assert resp.status_code == 200

    def test_logout_clears_session(self, auth_client):
        login_resp = _do_login(auth_client)
        assert login_resp.status_code == 200

        logout_resp = auth_client.post("/auth/logout")
        assert logout_resp.status_code == 200
        # The Set-Cookie header should delete/clear the session cookie
        set_cookie = logout_resp.headers.get("set-cookie", "")
        assert "session" in set_cookie

    def test_auth_status_shows_enabled_and_unauthenticated(self, auth_client):
        resp = auth_client.get("/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is True
        assert data["authenticated"] is False

    def test_auth_status_shows_authenticated_after_login(self, auth_client):
        login_resp = _do_login(auth_client)
        assert login_resp.status_code == 200

        session_cookie = login_resp.cookies.get("session")
        assert session_cookie is not None

        resp = auth_client.get(
            "/auth/status",
            cookies={"session": session_cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is True
        assert data["authenticated"] is True


# ---------------------------------------------------------------------------
# TestAuthDisabled
# ---------------------------------------------------------------------------


class TestAuthDisabled:
    def test_all_routes_accessible(self, noauth_client):
        for path in ("/health", "/config"):
            resp = noauth_client.get(path)
            assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"

    def test_auth_status_shows_disabled(self, noauth_client):
        resp = noauth_client.get("/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is False
        assert data["authenticated"] is True

    def test_login_returns_404_when_disabled(self, noauth_client):
        resp = _do_login(noauth_client)
        assert resp.status_code == 404
