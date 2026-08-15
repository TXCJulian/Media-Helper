import pytest
import requests

from app.encoder import client as client_mod
from app.encoder.client import (
    EncoderClient,
    EncoderRejected,
    EncoderUnreachable,
    is_retryable,
)


class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def client():
    return EncoderClient("http://encoder:3335")


def test_submit_returns_the_remote_job_id(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "post",
                        lambda *a, **k: _Response(202, {"job_id": "abc123"}))
    assert client.submit("/media3/x.mkv", {"PresetName": "P"}, "P") == "abc123"


def test_submit_never_sends_output_path(client, monkeypatch):
    """The encoder derives it. Sending one was an arbitrary-write primitive and
    is no longer accepted."""
    seen = {}

    def _post(url, json=None, timeout=None, **_k):
        seen["body"] = json
        return _Response(202, {"job_id": "x"})

    monkeypatch.setattr(client_mod.requests, "post", _post)
    client.submit("/media3/x.mkv", {"PresetName": "P"}, "P")
    assert set(seen["body"]) == {"source_path", "preset_json", "preset_name"}


def test_submit_wraps_the_leaf_in_a_preset_document(client, monkeypatch):
    """The encoder parses a document, not a bare leaf."""
    seen = {}

    def _post(url, json=None, timeout=None, **_k):
        seen["body"] = json
        return _Response(202, {"job_id": "x"})

    monkeypatch.setattr(client_mod.requests, "post", _post)
    client.submit("/media3/x.mkv", {"PresetName": "P"}, "P")
    assert seen["body"]["preset_json"] == {"PresetList": [{"PresetName": "P"}]}


def test_a_4xx_becomes_a_rejection_carrying_the_code(client, monkeypatch):
    monkeypatch.setattr(
        client_mod.requests, "post",
        lambda *a, **k: _Response(409, {"code": "encoder_unavailable",
                                        "reason": "no nvenc",
                                        "encoder": "nvenc_h265"}))
    with pytest.raises(EncoderRejected) as exc:
        client.submit("/media3/x.mkv", {}, "P")
    assert exc.value.code == "encoder_unavailable"
    assert exc.value.detail["encoder"] == "nvenc_h265"


def test_queue_full_is_retryable_and_carries_retry_after(client, monkeypatch):
    monkeypatch.setattr(
        client_mod.requests, "post",
        lambda *a, **k: _Response(503, {"code": "queue_full"},
                                  headers={"Retry-After": "60"}))
    with pytest.raises(EncoderRejected) as exc:
        client.submit("/media3/x.mkv", {}, "P")
    assert is_retryable(exc.value) is True
    assert exc.value.retry_after == 60


def test_service_unavailable_is_retryable(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "post",
                        lambda *a, **k: _Response(503, {"code": "service_unavailable"}))
    with pytest.raises(EncoderRejected) as exc:
        client.submit("/media3/x.mkv", {}, "P")
    assert is_retryable(exc.value) is True


@pytest.mark.parametrize("status,code", [
    (400, "preset_not_found"), (400, "invalid_video_preset"),
    (403, "path_not_allowed"), (404, "source_not_found_on_encoder"),
    (409, "encoder_unavailable"), (413, "request_too_large"),
])
def test_every_4xx_code_is_not_retryable(client, monkeypatch, status, code):
    """Retrying these would hide a configuration fault behind a job that
    requeues forever."""
    monkeypatch.setattr(client_mod.requests, "post",
                        lambda *a, **k: _Response(status, {"code": code}))
    with pytest.raises(EncoderRejected) as exc:
        client.submit("/media3/x.mkv", {}, "P")
    assert is_retryable(exc.value) is False


def test_connection_errors_and_timeouts_are_unreachable(client, monkeypatch):
    for error in (requests.ConnectionError("refused"), requests.Timeout("slow")):
        def _raise(*_a, **_k):
            raise error
        monkeypatch.setattr(client_mod.requests, "post", _raise)
        with pytest.raises(EncoderUnreachable):
            client.submit("/media3/x.mkv", {}, "P")
    assert is_retryable(EncoderUnreachable("x")) is True


def test_an_unparseable_error_body_still_yields_a_rejection(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "post",
                        lambda *a, **k: _Response(500, "<html>502</html>"))
    with pytest.raises(EncoderRejected) as exc:
        client.submit("/media3/x.mkv", {}, "P")
    assert exc.value.code == "unknown"


def test_an_unrecognised_5xx_is_not_retryable(client, monkeypatch):
    """An unrecognized 5xx (e.g. encoder bug producing uncaught exception)
    must fail immediately to avoid silent infinite requeue. Failing safely
    leaves the source file untouched for manual requeue after fixing."""
    monkeypatch.setattr(client_mod.requests, "post",
                        lambda *a, **k: _Response(500, "bad gateway"))
    with pytest.raises(EncoderRejected) as exc:
        client.submit("/media3/x.mkv", {}, "P")
    assert is_retryable(exc.value) is False


def test_poll_returns_the_job_body(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "get",
                        lambda *a, **k: _Response(200, {"status": "running",
                                                        "progress": 12.5}))
    assert client.poll("abc")["progress"] == 12.5


def test_polling_a_vanished_job_raises_with_its_code(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "get",
                        lambda *a, **k: _Response(404, {"code": "job_not_found"}))
    with pytest.raises(EncoderRejected) as exc:
        client.poll("abc")
    assert exc.value.code == "job_not_found"


def test_health_returns_the_body(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "get",
                        lambda *a, **k: _Response(200, {"status": "ok",
                                                        "encoders": ["x264"]}))
    assert client.health()["encoders"] == ["x264"]


def test_health_reports_unreachable_rather_than_raising(client, monkeypatch):
    """The health pill must render 'Offline', not 500 the whole panel."""
    def _raise(*_a, **_k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(client_mod.requests, "get", _raise)
    assert client.health()["status"] == "unreachable"


def test_cancel_is_true_on_204_and_false_on_404(client, monkeypatch):
    monkeypatch.setattr(client_mod.requests, "delete", lambda *a, **k: _Response(204))
    assert client.cancel("abc") is True
    monkeypatch.setattr(client_mod.requests, "delete", lambda *a, **k: _Response(404))
    assert client.cancel("abc") is False
