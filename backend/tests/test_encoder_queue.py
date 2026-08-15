import threading
import time

import pytest

from app.encoder.client import EncoderRejected, EncoderUnreachable
from app.encoder.events import EventBroadcaster
from app.encoder.presets import NamedPreset
from app.encoder.queue import EncodeQueue
from app.encoder.rules import Condition, Rule
from app.encoder.store import EncoderStore


class FakeClient:
    """Stands in for the encoder service. Deterministic, no HTTP."""

    def __init__(self):
        self.submitted = []
        self.polls = []
        self.submit_error = None
        self.terminal = {"status": "completed", "progress": 100.0,
                         "output_path": None, "encoder_used": "x264"}

    def submit(self, source_path, preset_body, preset_name):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append((source_path, preset_name))
        return "remote-1"

    def poll(self, remote_job_id):
        self.polls.append(remote_job_id)
        return self.terminal

    def cancel(self, remote_job_id):
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = EncoderStore(str(tmp_path / "e.db"))
    store.replace_presets([
        NamedPreset("NVENC", "nvenc_h265", "medium", "av_mkv",
                    {"PresetName": "NVENC", "VideoEncoder": "nvenc_h265"})
    ])
    store.replace_rules([Rule("r1", [Condition("height", ">=", 720)], "NVENC")])

    movies = tmp_path / "Movies"
    movies.mkdir()
    source = movies / "Film.mkv"
    source.write_bytes(b"O" * 4096)

    client = FakeClient()

    import app.encoder.queue as queue_mod
    monkeypatch.setattr(queue_mod, "probe",
                        lambda _p: {"height": 1080, "size": 4096, "video_codec": "h264"})

    yield store, client, movies, source, queue_mod
    store.close()


def _queue(store, client, mode="auto", ttl=0, holding="/tmp/hold"):
    return EncodeQueue(store, client, EventBroadcaster(), mode=mode,
                       original_ttl=ttl, holding_dir=holding, poll_interval=0.01)


def test_plan_records_the_matched_rule_and_preset(env):
    store, client, movies, source, _ = env
    q = _queue(store, client, mode="review")
    job = store.create_job(str(source))
    q.plan(job.id)
    fetched = store.get_job(job.id)
    assert fetched.preset_name == "NVENC"
    assert fetched.rule_id == "r1"
    assert fetched.facts["height"] == 1080


def test_review_mode_stops_at_pending(env):
    """The safety property: review must not dispatch without a human."""
    store, client, movies, source, _ = env
    q = _queue(store, client, mode="review")
    job = store.create_job(str(source))
    assert q.plan(job.id) == "pending"
    assert store.get_job(job.id).stage == "pending"
    assert client.submitted == []


def test_auto_mode_queues_immediately(env):
    store, client, movies, source, _ = env
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    assert q.plan(job.id) == "queued"
    assert store.get_job(job.id).stage == "queued"


def test_a_skip_target_ends_the_job_without_dispatching(env):
    store, client, movies, source, _ = env
    store.replace_rules([Rule("r1", [Condition("height", ">=", 720)], "skip")])
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    assert q.plan(job.id) == "skipped"
    assert client.submitted == []


def test_an_unknown_preset_target_fails_the_job_rather_than_dispatching(env):
    """A rule pointing at a deleted preset must not reach the encoder."""
    store, client, movies, source, _ = env
    store.replace_rules([Rule("r1", [], "Ghost")])
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    assert q.plan(job.id) == "failed"
    assert "Ghost" in store.get_job(job.id).error


def test_a_full_run_encodes_swaps_and_reports_sizes(env):
    store, client, movies, source, _ = env
    encoded = movies / ".hbenc-remote-1.mkv"
    encoded.write_bytes(b"E" * 1024)
    client.terminal = {"status": "completed", "progress": 100.0,
                       "output_path": str(encoded), "encoder_used": "nvenc_h265"}
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        _wait(lambda: store.get_job(job.id).stage == "done")
    finally:
        q.stop()
    fetched = store.get_job(job.id)
    assert fetched.original_size == 4096
    assert fetched.encoded_size == 1024
    assert source.read_bytes() == b"E" * 1024


def test_a_failed_remote_encode_leaves_the_source_intact(env):
    store, client, movies, source, _ = env
    client.terminal = {"status": "failed", "error": "HandBrake exploded"}
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        _wait(lambda: store.get_job(job.id).stage == "failed")
    finally:
        q.stop()
    assert source.read_bytes() == b"O" * 4096
    assert "HandBrake exploded" in store.get_job(job.id).error


def test_encoder_unavailable_blocks_rather_than_fails(env):
    """The spec is explicit: never a silent CPU fallback. The user chooses."""
    store, client, movies, source, _ = env
    client.submit_error = EncoderRejected("encoder_unavailable", "no nvenc", 409)
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        _wait(lambda: store.get_job(job.id).stage == "blocked")
    finally:
        q.stop()
    assert store.get_job(job.id).error_code == "encoder_unavailable"


def test_a_transient_rejection_requeues_instead_of_failing(env):
    store, client, movies, source, _ = env
    client.submit_error = EncoderRejected("queue_full", "busy", 503, retry_after=0)
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        time.sleep(0.2)
        assert store.get_job(job.id).stage in {"queued", "encoding"}
    finally:
        q.stop()
    assert store.get_job(job.id).stage != "failed"


def test_an_unreachable_encoder_keeps_the_job_queued(env):
    """Jobs queue in the renamer rather than being lost, as the downloader
    already does."""
    store, client, movies, source, _ = env
    client.submit_error = EncoderUnreachable("refused")
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        time.sleep(0.2)
        assert store.get_job(job.id).stage != "failed"
    finally:
        q.stop()


def test_a_non_retryable_rejection_fails_the_job(env):
    store, client, movies, source, _ = env
    client.submit_error = EncoderRejected(
        "invalid_video_preset", "bad speed preset", 400,
        detail={"valid_presets": ["speed", "balanced"]})
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        _wait(lambda: store.get_job(job.id).stage == "failed")
    finally:
        q.stop()
    assert store.get_job(job.id).error_code == "invalid_video_preset"


def test_recover_requeues_interrupted_jobs(env):
    store, client, movies, source, _ = env
    job = store.create_job(str(source))
    store.set_stage(job.id, "encoding")
    q = _queue(store, client, mode="auto")
    q.recover()
    assert store.get_job(job.id).stage == "queued"


def test_run_retentions_purges_elapsed_originals(env, tmp_path):
    store, client, movies, source, _ = env
    kept = tmp_path / "hold" / "old.mkv"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"x")
    job = store.create_job("/media3/other.mkv")
    store.set_retention(job.id, str(kept), expires_at=time.time() - 1)
    q = _queue(store, client)
    q.run_retentions()
    assert not kept.exists()
    assert store.get_job(job.id).original_kept_path is None


def test_swap_failure_with_original_untouched_says_so(env):
    """SwapError with no kept_path: the original genuinely never moved."""
    store, client, movies, source, _ = env
    client.terminal = {"status": "completed", "progress": 100.0,
                       "output_path": str(movies / "missing.mkv"),
                       "encoder_used": "nvenc_h265"}
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        _wait(lambda: store.get_job(job.id).stage == "failed")
    finally:
        q.stop()
    fetched = store.get_job(job.id)
    assert "the original was left untouched" in fetched.error
    assert source.read_bytes() == b"O" * 4096


def test_swap_failure_with_kept_path_names_it(env, monkeypatch):
    """SwapError with kept_path set: the original moved and must be named."""
    store, client, movies, source, queue_mod = env
    kept = str(movies / "kept-original.mkv")

    from app.encoder.swap import SwapError

    def fake_swap_in(*args, **kwargs):
        raise SwapError(
            f"Could not publish encoded to final: boom. The original could "
            f"not be restored from holding; it survives at {kept}.",
            kept_path=kept,
        )

    monkeypatch.setattr(queue_mod, "swap_in", fake_swap_in)

    client.terminal = {"status": "completed", "progress": 100.0,
                       "output_path": str(movies / "out.mkv"),
                       "encoder_used": "nvenc_h265"}
    q = _queue(store, client, mode="auto")
    job = store.create_job(str(source))
    q.plan(job.id)
    q.start()
    try:
        _wait(lambda: store.get_job(job.id).stage == "failed")
    finally:
        q.stop()
    fetched = store.get_job(job.id)
    assert "the original was left untouched" not in fetched.error
    assert kept in fetched.error
    assert "preserved" in fetched.error.lower()


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")
