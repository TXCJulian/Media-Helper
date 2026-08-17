import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.encoder import routes as routes_mod

PRESET_DOC = {
    "PresetList": [
        {"PresetName": "NVENC", "VideoEncoder": "nvenc_h265",
         "VideoPreset": "medium", "FileFormat": "av_mkv"}
    ]
}


class FakeClient:
    def __init__(self, encoders=("x264", "nvenc_h265"), status="ok"):
        self._encoders = list(encoders)
        self._status = status

    def health(self):
        return {"status": self._status, "handbrake_version": "1.9.2",
                "encoders": self._encoders, "allowed_roots": ["/media3"],
                "workers": 1}


class FakeRuntime:
    def __init__(self, watch_paths):
        self.watch_paths = watch_paths
        self.replacements = []

    def replace_watch_paths(self, paths):
        self.replacements.append(paths)
        self.watch_paths = paths
        return paths


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_mod.config, "ENCODER_DB", str(tmp_path / "e.db"))
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", ["/media3/Movies"])
    monkeypatch.setattr(routes_mod, "_client", FakeClient())
    routes_mod.reset_state_for_tests()
    routes_mod.register_runtime(FakeRuntime(["/media3/Movies"]))
    app = FastAPI()
    app.include_router(routes_mod.router)
    routes_mod.register_error_handlers(app)
    with TestClient(app) as c:
        yield c
    routes_mod.reset_state_for_tests()


def test_health_derives_a_vendor_label_from_the_encoder_list(client):
    """/health has no gpu_name, so the pill's label is derived from what the
    machine can actually do -- which is truer than a device name anyway."""
    body = client.get("/api/encoder/health").json()
    assert body["status"] == "ok"
    assert body["vendor"] == "NVENC"


def test_vendor_is_cpu_when_no_hardware_encoder_is_present(client, monkeypatch):
    monkeypatch.setattr(routes_mod, "_client", FakeClient(encoders=("x264", "x265")))
    assert client.get("/api/encoder/health").json()["vendor"] == "CPU"


def test_config_reports_the_watch_paths(client):
    assert client.get("/api/encoder/config").json()["watch_paths"] == ["/media3/Movies"]


def test_config_saves_an_explicit_empty_list(client, tmp_path, monkeypatch):
    """An explicit empty configuration pauses watching rather than resetting defaults."""
    movies = tmp_path / "Movies"
    movies.mkdir()
    runtime = FakeRuntime([str(movies)])
    routes_mod.register_runtime(runtime)
    monkeypatch.setattr(routes_mod.config, "BASE_PATHS", [str(tmp_path)])

    response = client.put("/api/encoder/config", json={"watch_paths": []})

    assert response.status_code == 200
    assert response.json()["watch_paths"] == []
    assert runtime.replacements == [[]]


def test_config_rejects_a_path_outside_base_without_replacing(client, tmp_path, monkeypatch):
    """Rejecting an invalid update must leave the active watcher untouched."""
    runtime = FakeRuntime(["/media/Movies"])
    routes_mod.register_runtime(runtime)
    monkeypatch.setattr(routes_mod.config, "BASE_PATHS", [str(tmp_path / "allowed")])

    response = client.put(
        "/api/encoder/config", json={"watch_paths": [str(tmp_path / "outside")]}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_watch_path"
    assert runtime.replacements == []


def test_importing_a_preset_document_stores_its_leaves(client):
    r = client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    assert r.status_code == 200
    assert [p["name"] for p in client.get("/api/encoder/presets").json()] == ["NVENC"]


def test_a_document_with_no_usable_preset_at_all_is_rejected(client, monkeypatch):
    """Filtering at upload keeps an unusable preset out of the system entirely,
    rather than surfacing as a failed job an hour later. With nothing left to
    import there is no partial success to report, so this is still a 400."""
    monkeypatch.setattr(routes_mod, "_client", FakeClient(encoders=("x264",)))
    r = client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    assert r.status_code == 400
    assert r.json()["code"] == "encoder_unavailable"
    assert "nvenc_h265" in r.json()["reason"]


def test_unusable_presets_are_skipped_rather_than_failing_the_document(client):
    """A stock HandBrake export describes every vendor's hardware presets --
    QSV, VCN, Media Foundation, VideoToolbox -- and no single machine has them
    all. Rejecting the document over presets the user was never going to use
    made their own HandBrake export un-importable, which is how this was found.
    """
    document = {
        "PresetList": [
            {"PresetName": "NVENC", "VideoEncoder": "nvenc_h265",
             "VideoPreset": "medium", "FileFormat": "av_mkv"},
            {"PresetName": "QSV", "VideoEncoder": "qsv_h265",
             "VideoPreset": "speed", "FileFormat": "av_mp4"},
        ]
    }
    r = client.post("/api/encoder/presets", json={"document": document})
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == ["NVENC"]
    assert [s["name"] for s in body["skipped"]] == ["QSV"]
    assert "qsv_h265" in body["skipped"][0]["reason"]

    # Only the usable one is actually stored.
    assert [p["name"] for p in client.get("/api/encoder/presets").json()] == ["NVENC"]


def test_import_says_so_when_the_encoder_list_cannot_be_read(client, monkeypatch):
    """An unreachable encoder must not be reported as one that lacks every
    encoder in the document -- that sends the user to fix their presets when
    the real problem is the service being down."""
    monkeypatch.setattr(routes_mod, "_client", FakeClient(encoders=()))
    r = client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    assert r.status_code == 503
    assert r.json()["code"] == "encoder_unreachable"


def test_a_malformed_document_is_rejected(client):
    r = client.post("/api/encoder/presets", json={"document": {"PresetList": [{}]}})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_preset"


def test_deleting_a_preset(client):
    client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    assert client.delete("/api/encoder/presets/NVENC").status_code == 204
    assert client.delete("/api/encoder/presets/NVENC").status_code == 404


def test_rules_round_trip(client):
    payload = {
        "rules": [{"id": "r1",
                   "conditions": [{"field": "height", "op": ">=", "value": 2160}],
                   "target": "skip"}],
        "fallback": "skip",
    }
    assert client.put("/api/encoder/rules", json=payload).status_code == 200
    body = client.get("/api/encoder/rules").json()
    assert body["rules"][0]["id"] == "r1"
    assert body["fallback"] == "skip"


def test_a_rule_naming_an_unknown_field_is_rejected(client):
    """Caught at save time: a typo'd field would otherwise be a rule that
    silently never fires."""
    payload = {"rules": [{"id": "r1",
                          "conditions": [{"field": "heigth", "op": ">=", "value": 1}],
                          "target": "skip"}],
               "fallback": "skip"}
    r = client.put("/api/encoder/rules", json=payload)
    assert r.status_code == 400
    assert "heigth" in r.json()["reason"]


def test_a_rule_targeting_an_unknown_preset_is_rejected(client):
    payload = {"rules": [{"id": "r1", "conditions": [], "target": "Ghost"}],
               "fallback": "skip"}
    assert client.put("/api/encoder/rules", json=payload).status_code == 400


def test_test_endpoint_reports_the_match_without_encoding(client, monkeypatch, tmp_path):
    """Rule-ordering mistakes are otherwise invisible until an hour of 4K
    encoding has been spent."""
    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    candidate = watch_root / "x.mkv"
    candidate.write_bytes(b"")
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])

    client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    client.put("/api/encoder/rules", json={
        "rules": [
            {"id": "r1", "conditions": [{"field": "height", "op": ">=", "value": 4320}],
             "target": "NVENC"},
            {"id": "r2", "conditions": [{"field": "height", "op": ">=", "value": 720}],
             "target": "NVENC"},
        ],
        "fallback": "skip",
    })
    monkeypatch.setattr(routes_mod, "probe",
                        lambda _p: {"height": 1080, "size": 1, "video_codec": "h264"})
    body = client.post("/api/encoder/test", json={"path": str(candidate)}).json()
    assert body["matched_rule"] == "r2"
    assert body["target"] == "NVENC"
    assert body["evaluated"] == ["r1", "r2"]
    assert body["facts"]["height"] == 1080


def test_test_endpoint_reports_a_probe_failure_as_a_400(client, monkeypatch, tmp_path):
    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    candidate = watch_root / "x.txt"
    candidate.write_bytes(b"")
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])

    def _boom(_p):
        raise routes_mod.ProbeError("not a video")

    monkeypatch.setattr(routes_mod, "probe", _boom)
    r = client.post("/api/encoder/test", json={"path": str(candidate)})
    assert r.status_code == 400
    assert r.json()["code"] == "probe_failed"


# ---- Critical 1: /test is an unauthenticated SSRF and file-existence -----
# ---- oracle without containment/isfile checks -----------------------------


def test_test_endpoint_rejects_a_url_style_path(client, monkeypatch):
    """ffprobe resolves protocols, not just paths -- `ffprobe http://...`
    issues a real HTTP request. Without a containment check this endpoint,
    unauthenticated by default, would let a caller make the server reach
    anything on its network."""
    probed = []
    monkeypatch.setattr(routes_mod, "probe", lambda p: probed.append(p) or {})
    r = client.post("/api/encoder/test", json={"path": "http://10.0.0.5:8080/admin"})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_path"
    assert probed == []  # probe() must never have been reached


def test_test_endpoint_rejects_a_path_outside_the_watch_roots(client, monkeypatch, tmp_path):
    """An absolute path outside every configured root, even if it happens to
    exist on disk, must not be probed or reveal anything about itself."""
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir()
    outside.write_bytes(b"shhh")
    probed = []
    monkeypatch.setattr(routes_mod, "probe", lambda p: probed.append(p) or {})
    r = client.post("/api/encoder/test", json={"path": str(outside)})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_path"
    assert probed == []


def test_test_endpoint_rejects_a_symlink_escaping_the_watch_root(client, monkeypatch, tmp_path):
    """realpath must run before the containment check, or a symlink inside
    the watch root pointing outside it would bypass the allow-list."""
    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"real content")
    link = watch_root / "innocent.mkv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])
    probed = []
    monkeypatch.setattr(routes_mod, "probe", lambda p: probed.append(p) or {})
    r = client.post("/api/encoder/test", json={"path": str(link)})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_path"
    assert probed == []


def test_test_endpoint_accepts_a_legitimate_in_root_file(client, monkeypatch, tmp_path):
    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    candidate = watch_root / "Film.mkv"
    candidate.write_bytes(b"")
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])
    monkeypatch.setattr(routes_mod, "probe", lambda _p: {"height": 1080})
    r = client.post("/api/encoder/test", json={"path": str(candidate)})
    assert r.status_code == 200


def test_test_endpoint_rejects_a_nonexistent_path_inside_the_watch_root(client, monkeypatch, tmp_path):
    """Containment alone is not enough: a path that resolves inside a watch
    root but does not exist must not be handed to probe()."""
    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    probed = []
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])
    monkeypatch.setattr(routes_mod, "probe", lambda p: probed.append(p) or {})
    r = client.post("/api/encoder/test", json={"path": str(watch_root / "ghost.mkv")})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_path"
    assert probed == []


def test_jobs_list_is_empty_initially(client):
    assert client.get("/api/encoder/jobs").json() == []


def test_deleting_an_unknown_job_is_404(client):
    assert client.delete("/api/encoder/jobs/nope").status_code == 404


def test_approving_an_unknown_job_is_404(client):
    assert client.post("/api/encoder/jobs/nope/approve").status_code == 404


# ---- Carry-over A: reject a non-bool value for a bool field at save time ----


def test_a_non_bool_value_for_a_bool_field_is_rejected(client):
    """`bool("false")` is True in Python: a client sending the string "false"
    for a bool field would silently invert the rule's meaning unless this is
    caught at save time."""
    payload = {"rules": [{"id": "r1",
                          "conditions": [{"field": "dolby_vision", "op": "==",
                                          "value": "false"}],
                          "target": "skip"}],
               "fallback": "skip"}
    r = client.put("/api/encoder/rules", json=payload)
    assert r.status_code == 400
    assert "dolby_vision" in r.json()["reason"]


def test_a_real_bool_value_for_a_bool_field_is_accepted(client):
    payload = {"rules": [{"id": "r1",
                          "conditions": [{"field": "dolby_vision", "op": "==",
                                          "value": False}],
                          "target": "skip"}],
               "fallback": "skip"}
    assert client.put("/api/encoder/rules", json=payload).status_code == 200


# ---- Carry-over B: reject `contains` on a bool field at save time ----------


def test_contains_on_a_bool_field_is_rejected(client):
    payload = {"rules": [{"id": "r1",
                          "conditions": [{"field": "dolby_vision", "op": "contains",
                                          "value": "tr"}],
                          "target": "skip"}],
               "fallback": "skip"}
    r = client.put("/api/encoder/rules", json=payload)
    assert r.status_code == 400
    assert "dolby_vision" in r.json()["reason"]


# ---- Carry-over C: approve must not reattach to a terminal remote job -----


def test_approving_a_swap_interrupted_job_is_refused(client):
    """A `blocked` job with error_code `swap_interrupted` carries a
    remote_job_id pointing at an already-completed remote encode. Approving
    it must not dispatch the queue's normal reattach path against that
    terminal job -- it could re-run `_publish_result` on a source that may
    already be the encoded file."""
    store = routes_mod.get_store()
    job = store.create_job("/media3/Movies/x.mkv")
    store.set_remote_job(job.id, "remote-123")
    store.set_stage(job.id, "blocked", error="restart mid-swap",
                     error_code="swap_interrupted")

    r = client.post(f"/api/encoder/jobs/{job.id}/approve")
    assert r.status_code == 409
    assert r.json()["code"] == "swap_interrupted"

    # The job must not have been silently requeued.
    assert store.get_job(job.id).stage == "blocked"


def test_approving_an_encoder_unavailable_job_still_works(client):
    """A `blocked` job with no remote_job_id (submit itself failed) is safe
    to approve normally."""
    store = routes_mod.get_store()
    job = store.create_job("/media3/Movies/y.mkv")
    store.set_stage(job.id, "blocked", error="no gpu",
                     error_code="encoder_unavailable")

    r = client.post(f"/api/encoder/jobs/{job.id}/approve")
    assert r.status_code == 200
    assert store.get_job(job.id).stage == "queued"


# ---- Review item 1: FastAPI-native validation errors must use the flat ----
# ---- {code, reason} envelope too, not just the ones routed through -------
# ---- _error(). ------------------------------------------------------------


def test_a_malformed_body_missing_a_required_field_gets_the_flat_envelope(client):
    """A body that fails Pydantic validation (e.g. missing `document`) never
    reaches an endpoint function, so it never goes through `_error()` --
    without `register_error_handlers`, FastAPI's default handler would
    return `{"detail": [...]}` instead, a different shape than every other
    error path in this module."""
    r = client.post("/api/encoder/presets", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid_request"
    assert "document" in body["reason"]
    assert "detail" not in body


def test_the_flat_envelope_does_not_leak_into_other_routes():
    """The handler is necessarily registered app-wide (FastAPI cannot attach
    an exception handler to a bare APIRouter), so it must check the request
    path itself and leave every other route's validation-error shape alone
    -- otherwise turning this on for the encoder would silently change the
    downloader's (or any other feature's) error responses too."""
    app = FastAPI()
    routes_mod.register_error_handlers(app)

    class _Body(BaseModel):
        required_field: str

    @app.post("/api/other/thing")
    def _other(body: _Body) -> dict:
        return {"ok": True}

    with TestClient(app) as c:
        r = c.post("/api/other/thing", json={})
        assert r.status_code == 422
        body = r.json()
        # FastAPI's untouched default shape: a "detail" list, no "code".
        assert "detail" in body
        assert "code" not in body


def test_the_flat_envelope_check_is_segment_bounded():
    """A bare `str.startswith(router.prefix)` would also match
    "/api/encoderXYZ" -- a route that doesn't exist today, but a future one
    starting with the same characters must not have its errors silently
    reshaped just because it happens to share a prefix with no path
    separator between them."""
    app = FastAPI()
    routes_mod.register_error_handlers(app)

    class _Body(BaseModel):
        required_field: str

    @app.post("/api/encoderXYZ/thing")
    def _lookalike(body: _Body) -> dict:
        return {"ok": True}

    with TestClient(app) as c:
        r = c.post("/api/encoderXYZ/thing", json={})
        assert r.status_code == 422
        body = r.json()
        assert "detail" in body
        assert "code" not in body


# ---- Review round 2, item 2: a contained startup failure must not leave --
# ---- get_store()/get_queue() handing back a dead singleton silently -----


def test_mark_startup_failed_makes_store_and_queue_fail_loud(client):
    """`main.py`'s lifespan calls this after a contained startup failure.
    Without it, `get_queue()` would keep lazily rebuilding a fresh
    `EncodeQueue` that nobody ever calls `.start()` on again -- `approve`
    would report "queued" for a job that will silently never be dispatched.
    Failing loud here is what makes that impossible."""
    routes_mod.get_store()  # populate the singletons first, non-vacuously
    routes_mod.get_queue()
    assert routes_mod._store is not None
    assert routes_mod._queue is not None

    routes_mod.mark_startup_failed()

    assert routes_mod._store is None
    assert routes_mod._queue is None
    with pytest.raises(RuntimeError):
        routes_mod.get_store()
    with pytest.raises(RuntimeError):
        routes_mod.get_queue()


def test_a_contained_startup_failure_surfaces_at_the_route_not_a_fake_200(client):
    """The caller-observable version of the above: hitting an encoder route
    after a startup failure must not come back as a quiet success."""
    routes_mod.mark_startup_failed()

    with pytest.raises(RuntimeError):
        client.get("/api/encoder/jobs")


# ---- Important 10: /events was the one unguarded accessor -----------------


def test_events_endpoint_fails_loud_after_a_contained_startup_failure(client):
    """`get_events()` had no `_startup_failed` check, and `get_store()` was
    called *inside* the StreamingResponse generator -- after the 200 status
    and SSE headers were already committed. A client hitting /events after a
    contained startup failure got a successful-looking stream that died
    mid-body instead of a normal error status."""
    routes_mod.mark_startup_failed()

    with pytest.raises(RuntimeError):
        client.get("/api/encoder/events")


def test_events_endpoint_resolves_the_store_before_the_streaming_response(client, monkeypatch):
    """get_store() must be called before StreamingResponse is constructed --
    otherwise a failure surfaces after 200 + SSE headers are already sent.

    Calls the route coroutine directly rather than through TestClient: the
    event stream is deliberately endless, and TestClient runs the whole ASGI
    call to completion before returning, so `client.get(...)` here would
    block forever (see test_downloader_routes.py's `_first_sse_chunk`).
    """
    import asyncio

    calls = []
    original_get_store = routes_mod.get_store

    def _spy():
        calls.append(1)
        return original_get_store()

    monkeypatch.setattr(routes_mod, "get_store", _spy)

    async def _drive():
        response = await routes_mod.events()
        # get_store() must already have run by the time the coroutine
        # returns the response -- before any body iteration -- which is
        # exactly what distinguishes "resolved before the StreamingResponse"
        # from "resolved on first read inside the generator".
        assert calls, "get_store() must be called before the response is built"
        await response.body_iterator.aclose()

    asyncio.run(_drive())


def test_test_endpoint_accepts_a_file_under_a_base_path_outside_the_watch_roots(
    client, monkeypatch, tmp_path
):
    """/test only reads, and its question -- "which rule would win for this
    file" -- is exactly what you ask about a folder you are *considering*
    watching. Scoping it to the watch paths made that unanswerable, and made
    an install with no watch paths reject every path, which reads as a broken
    endpoint rather than an unconfigured one. BASE_PATHS is already this
    application's boundary for paths a user may point at.
    """
    media = tmp_path / "media"
    watched = media / "Movies"
    unwatched = media / "Home Videos"
    watched.mkdir(parents=True)
    unwatched.mkdir(parents=True)
    candidate = unwatched / "clip.mkv"
    candidate.write_bytes(b"data")

    monkeypatch.setattr(routes_mod.config, "BASE_PATHS", [str(media)])
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watched)])
    monkeypatch.setattr(routes_mod, "probe", lambda p: {"size": 4, "height": 1080})

    r = client.post("/api/encoder/test", json={"path": str(candidate)})
    assert r.status_code == 200, r.json()
    assert r.json()["target"] == "skip"  # no rules configured


def test_test_endpoint_still_refuses_a_path_outside_every_root(
    client, monkeypatch, tmp_path
):
    """Widening to BASE_PATHS must not become "anything on the filesystem"."""
    media = tmp_path / "media"
    media.mkdir()
    outside = tmp_path / "elsewhere.mkv"
    outside.write_bytes(b"data")

    probed = []
    monkeypatch.setattr(routes_mod.config, "BASE_PATHS", [str(media)])
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [])
    monkeypatch.setattr(routes_mod, "probe", lambda p: probed.append(p) or {})

    r = client.post("/api/encoder/test", json={"path": str(outside)})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_path"
    assert probed == []


def test_health_fails_loud_after_a_contained_startup_failure(client):
    """/health is the first screen an operator checks. Probing only the remote
    encoder let it report "ok" while the local store and queue were dead --
    green during exactly the failure it exists to reveal."""
    routes_mod.mark_startup_failed()
    with pytest.raises(RuntimeError):
        client.get("/api/encoder/health")


def test_probe_failures_do_not_leak_ffprobe_output_to_the_caller(
    client, monkeypatch, tmp_path
):
    """ffprobe's stderr quotes the path and distinguishes "no such file" from
    "permission denied", which turns this endpoint into a filesystem oracle --
    and auth is off unless credentials are configured. The detail belongs in
    the server log, not the response."""
    from app.encoder.probe import ProbeError

    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    candidate = watch_root / "x.mkv"
    candidate.write_bytes(b"")
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])

    secret = "/etc/shadow: Permission denied"
    monkeypatch.setattr(
        routes_mod, "probe",
        lambda _p: (_ for _ in ()).throw(ProbeError(secret)),
    )

    r = client.post("/api/encoder/test", json={"path": str(candidate)})
    assert r.status_code == 400
    assert r.json()["code"] == "probe_failed"
    assert "shadow" not in r.text
    assert "Permission denied" not in r.text


def test_reprocess_clears_the_dedup_record(client, monkeypatch, tmp_path):
    watch_root = tmp_path / "Movies"
    watch_root.mkdir()
    candidate = watch_root / "x.mkv"
    candidate.write_bytes(b"data")
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [str(watch_root)])

    store = routes_mod.get_store()
    st = candidate.stat()
    store.mark_seen(str(candidate), st.st_size, st.st_mtime_ns)
    assert str(candidate) in store.seen_fingerprints()

    r = client.post("/api/encoder/reprocess", json={"path": str(candidate)})
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    assert str(candidate) not in store.seen_fingerprints()

    # Idempotent: clearing again is not an error, it just clears nothing.
    assert client.post("/api/encoder/reprocess",
                       json={"path": str(candidate)}).json()["cleared"] is False


def test_reprocess_refuses_a_path_outside_every_root(client, monkeypatch, tmp_path):
    outside = tmp_path / "elsewhere.mkv"
    outside.write_bytes(b"data")
    monkeypatch.setattr(routes_mod.config, "BASE_PATHS", [])
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", [])

    r = client.post("/api/encoder/reprocess", json={"path": str(outside)})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_path"
