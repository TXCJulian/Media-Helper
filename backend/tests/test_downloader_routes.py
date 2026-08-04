import asyncio
import gc
import importlib
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    media = tmp_path / "media"
    (media / "Music").mkdir(parents=True)
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    with patch.dict(
        os.environ,
        {
            "BASE_PATHS": str(media),
            "TMDB_API_KEY": "test_key",
            "AUTH_USERNAME": "",
            "AUTH_PASSWORD": "",
            "SECRET_KEY": "test-secret-key",
            "ENABLED_FEATURES": "download",
            "DOWNLOADS_DIR": str(downloads),
            "DOWNLOADER_DATA_DIR": str(tmp_path / "dl-data"),
            "DOWNLOADER_DB": str(tmp_path / "dl-data" / "downloader.db"),
            "YT_DLP_COOKIES": "",
        },
    ):
        import app.config as config_mod

        importlib.reload(config_mod)
        import app.auth as auth_mod

        importlib.reload(auth_mod)
        import app.downloader.ydl as ydl_mod

        importlib.reload(ydl_mod)
        import app.downloader.routes as routes_mod

        importlib.reload(routes_mod)
        import app.main as main_mod

        importlib.reload(main_mod)

        with TestClient(main_mod.app) as c:
            yield c


def test_status_reports_version_and_queue_depth(client):
    response = client.get("/download/status")
    assert response.status_code == 200
    body = response.json()
    assert "yt_dlp_version" in body
    assert body["cookies_present"] is False
    assert body["queue_depth"] == 0


def test_create_accepts_multiple_urls_in_one_request(client):
    response = client.post(
        "/download",
        json={
            "urls": ["https://example.com/a", "https://example.com/b"],
            "options": {"type": "audio", "auto_start": False},
        },
    )
    assert response.status_code == 200
    job_ids = response.json()["job_ids"]
    assert len(job_ids) == 2

    listed = client.get("/download/jobs").json()["jobs"]
    assert {j["job_id"] for j in listed} == set(job_ids)
    assert all(j["stage"] == "queued" for j in listed)


def test_create_rejects_non_http_scheme(client):
    response = client.post(
        "/download", json={"urls": ["file:///etc/passwd"], "options": {}}
    )
    assert response.status_code == 422


def test_create_rejects_empty_url_list(client):
    response = client.post("/download", json={"urls": [], "options": {}})
    assert response.status_code == 422


def test_get_job_hides_internal_options(client):
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    body = client.get(f"/download/jobs/{job_id}").json()
    assert body["job_id"] == job_id
    assert "options" not in body


def test_get_missing_job_returns_404(client):
    response = client.get("/download/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_invalid_job_id_returns_422(client):
    assert client.get("/download/jobs/not-a-uuid").status_code == 422


def test_cancel_then_delete_a_queued_job(client):
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    assert client.post(f"/download/jobs/{job_id}/cancel").status_code == 200
    assert client.get(f"/download/jobs/{job_id}").json()["stage"] == "cancelled"
    assert client.delete(f"/download/jobs/{job_id}").status_code == 200
    assert client.get(f"/download/jobs/{job_id}").status_code == 404


def test_item_file_404_when_job_not_done(client):
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    assert client.get(f"/download/jobs/{job_id}/items/0/file").status_code == 404


def test_cookie_upload_and_delete(client):
    response = client.post(
        "/download/cookies",
        files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\n", "text/plain")},
    )
    assert response.status_code == 200
    assert client.get("/download/status").json()["cookies_present"] is True

    assert client.delete("/download/cookies").status_code == 200
    assert client.get("/download/status").json()["cookies_present"] is False


async def _first_sse_chunk(response):
    """Pull one chunk from a StreamingResponse, then close the stream.

    The event stream is deliberately endless, and Starlette's TestClient runs
    the whole ASGI call to completion before returning a response object
    (`portal.call(self.app, ...)` in `_TestClientTransport.handle_request`), so
    `client.stream("GET", "/download/events")` would block forever. Driving the
    response's own body iterator exercises the real endpoint without needing a
    stream that ends.
    """
    iterator = response.body_iterator
    chunk = await iterator.__anext__()
    subscribers_while_open = routes_module().get_broadcaster().subscriber_count()
    await iterator.aclose()
    return chunk, subscribers_while_open


def routes_module():
    import app.downloader.routes as routes_mod

    return routes_mod


def test_downloader_routes_are_registered(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/download/events" in paths
    assert "/download" in paths
    assert "/download/jobs/{job_id}" in paths


def test_events_stream_emits_initial_snapshot(client):
    client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    )

    response = routes_module().download_events()
    assert response.media_type == "text/event-stream"

    chunk, subscribers_while_open = asyncio.run(_first_sse_chunk(response))

    assert subscribers_while_open == 1
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    assert text.startswith("data: ")
    payload = json.loads(text[6:])
    assert payload["type"] == "snapshot"
    assert len(payload["jobs"]) == 1


def test_events_stream_unsubscribes_when_the_client_goes_away(client):
    broadcaster = routes_module().get_broadcaster()
    assert broadcaster.subscriber_count() == 0

    response = routes_module().download_events()
    asyncio.run(_first_sse_chunk(response))

    # Closing the stream must release the subscription, or every reconnect
    # would leave a queue behind for publish() to fan out to forever.
    gc.collect()
    assert broadcaster.subscriber_count() == 0


def test_events_stream_receives_deltas_after_the_snapshot(client):
    async def snapshot_then_delta():
        response = routes_module().download_events()
        iterator = response.body_iterator
        await iterator.__anext__()  # snapshot
        client.post(
            "/download",
            json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
        )
        delta = await iterator.__anext__()
        await iterator.aclose()
        return delta

    chunk = asyncio.run(snapshot_then_delta())
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    payload = json.loads(text[6:])
    assert payload["type"] == "job"
    assert payload["job"]["stage"] == "queued"
    assert "options" not in payload["job"]

