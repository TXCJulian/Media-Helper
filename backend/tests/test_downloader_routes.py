import asyncio
import importlib
import json
import os
from unittest.mock import patch

import anyio.to_thread
import pytest
from fastapi.testclient import TestClient

# Comfortably longer than the endpoint's poll interval, so a test that waits
# this long is guaranteed to have crossed at least one poll cycle.
_POLL_MARGIN = 0.4


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


def routes_module():
    import app.downloader.routes as routes_mod

    return routes_mod


def sse_payload(chunk):
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    assert text.startswith("data: "), f"not a data frame: {text!r}"
    return json.loads(text[6:])


async def _first_sse_chunk():
    """Pull one chunk from the real endpoint, then close the stream.

    The event stream is deliberately endless, and Starlette's TestClient runs
    the whole ASGI call to completion before returning a response object
    (`portal.call(self.app, ...)` in `_TestClientTransport.handle_request`), so
    `client.stream("GET", "/download/events")` would block forever. Driving the
    response's own body iterator exercises the real endpoint - it is the same
    object uvicorn consumes - without needing a stream that ends.

    Both subscriber counts are read inside the loop, before `asyncio.run`'s
    `shutdown_asyncgens()` could close the generator for us. That keeps the
    endpoint's own `finally` load-bearing rather than incidentally covered.
    """
    response = await routes_module().download_events()
    assert response.media_type == "text/event-stream"
    iterator = response.body_iterator
    chunk = await iterator.__anext__()
    while_open = routes_module().get_broadcaster().subscriber_count()
    await iterator.aclose()
    after_close = routes_module().get_broadcaster().subscriber_count()
    return chunk, while_open, after_close


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

    chunk, while_open, _ = asyncio.run(_first_sse_chunk())

    assert while_open == 1
    payload = sse_payload(chunk)
    assert payload["type"] == "snapshot"
    assert len(payload["jobs"]) == 1


def test_events_stream_unsubscribes_when_the_client_goes_away(client):
    broadcaster = routes_module().get_broadcaster()
    assert broadcaster.subscriber_count() == 0

    _, while_open, after_close = asyncio.run(_first_sse_chunk())

    # Closing the stream must release the subscription, or every reconnect
    # would leave a queue behind for publish() to fan out to forever.
    assert while_open == 1
    assert after_close == 0
    assert broadcaster.subscriber_count() == 0


def test_events_stream_unsubscribes_when_the_request_is_cancelled(client):
    """A disconnecting browser cancels the ASGI task, it does not aclose().

    This is the path that actually runs in production: Starlette cancels
    `stream_response` when `listen_for_disconnect` fires. The CancelledError is
    delivered into the generator at its `await`, so the `finally` must unwind
    the subscription there too - not merely when someone politely closes it.
    """
    broadcaster = routes_module().get_broadcaster()
    assert broadcaster.subscriber_count() == 0

    async def consume_until_cancelled():
        response = await routes_module().download_events()
        iterator = response.body_iterator

        async def consume():
            async for _chunk in iterator:
                pass

        task = asyncio.create_task(consume())
        # Wait until the generator is past the snapshot and parked in its poll
        # loop, so we are cancelling a genuinely idle stream.
        for _ in range(200):
            if broadcaster.subscriber_count() == 1:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("stream never subscribed")
        await asyncio.sleep(_POLL_MARGIN)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return broadcaster.subscriber_count()

    assert asyncio.run(consume_until_cancelled()) == 0


def test_events_stream_receives_deltas_after_the_snapshot(client):
    async def snapshot_then_delta():
        response = await routes_module().download_events()
        iterator = response.body_iterator
        await iterator.__anext__()  # snapshot
        client.post(
            "/download",
            json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
        )
        delta = await iterator.__anext__()
        await iterator.aclose()
        return delta

    payload = sse_payload(asyncio.run(snapshot_then_delta()))
    assert payload["type"] == "job"
    assert payload["job"]["stage"] == "queued"
    assert "options" not in payload["job"]


def test_events_stream_announces_a_deleted_job(client):
    """I2: without a deletion event the client keeps rendering a card for a
    job the server no longer has, and a second Delete returns 404."""

    async def snapshot_then_delete():
        response = await routes_module().download_events()
        iterator = response.body_iterator
        await iterator.__anext__()  # snapshot
        created = client.post(
            "/download",
            json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
        )
        job_id = created.json()["job_ids"][0]
        assert client.delete(f"/download/jobs/{job_id}").status_code == 200
        # Several "job" deltas precede it: the creation, and the cancel the
        # delete route issues first. Only the last frame is under test.
        frames = [await iterator.__anext__() for _ in range(3)]
        await iterator.aclose()
        return job_id, frames

    job_id, frames = asyncio.run(snapshot_then_delete())
    payloads = [sse_payload(frame) for frame in frames]
    assert {"type": "job_deleted", "job_id": job_id} in payloads


def test_idle_event_stream_borrows_no_anyio_worker_thread(client):
    """The whole point of the async rewrite: an idle stream holds no thread.

    A sync generator blocking on `subscription.get(timeout=...)` sits inside
    Starlette's `iterate_in_threadpool`, which borrows a token from AnyIO's
    default 40-token thread limiter for the entire wait. One stream per open
    browser tab would exhaust it and starve every other `def` endpoint in the
    application.

    Measured with a consumer task actively awaiting the next frame, because
    that is what `stream_response` does in production and it is the only state
    in which a sync implementation would be holding its token. Sampling while
    no `__anext__` is pending would pass under either implementation. The
    limiter is also the right instrument rather than `threading.active_count()`:
    the snapshot's `asyncio.to_thread(store.list_jobs)` materialises a thread in
    asyncio's own executor, which would mask the signal in a raw thread count.
    """
    broadcaster = routes_module().get_broadcaster()

    async def borrowed_while_idle():
        limiter = anyio.to_thread.current_default_thread_limiter()
        baseline = limiter.borrowed_tokens
        response = await routes_module().download_events()
        iterator = response.body_iterator

        async def consume():
            async for _chunk in iterator:
                pass

        task = asyncio.create_task(consume())
        for _ in range(200):
            if broadcaster.subscriber_count() == 1:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("stream never subscribed")
        # Several poll cycles with __anext__ pending throughout.
        await asyncio.sleep(_POLL_MARGIN * 2)
        borrowed = limiter.borrowed_tokens

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return baseline, borrowed

    baseline, borrowed = asyncio.run(borrowed_while_idle())
    assert baseline == 0
    assert borrowed == 0



def test_job_id_lookup_accepts_uppercase(client):
    """A caller that upper-cases a job id should still reach the job.

    uuid4 only ever emits lowercase, so this cannot happen with ids we issue --
    but rejecting the same id in different case is needlessly strict, and
    accepting it without normalising would just move the failure to a confusing
    404 because the store looks up the exact string.
    """
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    resp = client.get(f"/download/jobs/{job_id.upper()}")

    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id
