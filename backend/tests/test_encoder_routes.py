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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_mod.config, "ENCODER_DB", str(tmp_path / "e.db"))
    monkeypatch.setattr(routes_mod.config, "ENCODER_WATCH_PATHS", ["/media3/Movies"])
    monkeypatch.setattr(routes_mod, "_client", FakeClient())
    routes_mod.reset_state_for_tests()
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


def test_importing_a_preset_document_stores_its_leaves(client):
    r = client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    assert r.status_code == 200
    assert [p["name"] for p in client.get("/api/encoder/presets").json()] == ["NVENC"]


def test_a_preset_needing_an_absent_encoder_is_rejected(client, monkeypatch):
    """Rejecting at upload keeps an unusable preset out of the system entirely,
    rather than surfacing as a failed job an hour later."""
    monkeypatch.setattr(routes_mod, "_client", FakeClient(encoders=("x264",)))
    r = client.post("/api/encoder/presets", json={"document": PRESET_DOC})
    assert r.status_code == 400
    assert r.json()["code"] == "encoder_unavailable"
    assert "nvenc_h265" in r.json()["reason"]


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


def test_test_endpoint_reports_the_match_without_encoding(client, monkeypatch):
    """Rule-ordering mistakes are otherwise invisible until an hour of 4K
    encoding has been spent."""
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
    body = client.post("/api/encoder/test", json={"path": "/media3/x.mkv"}).json()
    assert body["matched_rule"] == "r2"
    assert body["target"] == "NVENC"
    assert body["evaluated"] == ["r1", "r2"]
    assert body["facts"]["height"] == 1080


def test_test_endpoint_reports_a_probe_failure_as_a_400(client, monkeypatch):
    def _boom(_p):
        raise routes_mod.ProbeError("not a video")

    monkeypatch.setattr(routes_mod, "probe", _boom)
    r = client.post("/api/encoder/test", json={"path": "/media3/x.txt"})
    assert r.status_code == 400
    assert r.json()["code"] == "probe_failed"


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
