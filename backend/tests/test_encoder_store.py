import time

import pytest

from app.encoder.presets import NamedPreset
from app.encoder.rules import Condition, Rule
from app.encoder.store import EncoderStore


@pytest.fixture
def store(tmp_path):
    s = EncoderStore(str(tmp_path / "encoder.db"))
    yield s
    s.close()


def test_create_and_read_a_job(store):
    job = store.create_job("/media3/Movies/x.mkv")
    assert job.stage == "settling"
    assert store.get_job(job.id).source_path == "/media3/Movies/x.mkv"


def test_stage_and_progress_updates_persist(store):
    job = store.create_job("/media3/x.mkv")
    store.set_stage(job.id, "encoding")
    store.set_progress(job.id, 42.5)
    fetched = store.get_job(job.id)
    assert (fetched.stage, fetched.progress) == ("encoding", 42.5)


def test_failure_records_both_message_and_code(store):
    """The code drives behaviour -- retry vs stop -- and the message is for the
    human. Storing only the message would make retry logic parse prose."""
    job = store.create_job("/media3/x.mkv")
    store.set_stage(job.id, "failed", error="no such preset",
                    error_code="preset_not_found")
    fetched = store.get_job(job.id)
    assert (fetched.error, fetched.error_code) == ("no such preset", "preset_not_found")


def test_only_one_active_job_per_source_path(store):
    """The watcher can see one file twice -- a second event, a rescan after
    restart. Two jobs encoding one file would race for the same output."""
    store.create_job("/media3/x.mkv")
    with pytest.raises(Exception):
        store.create_job("/media3/x.mkv")


def test_a_terminal_job_frees_its_source_path(store):
    job = store.create_job("/media3/x.mkv")
    store.set_stage(job.id, "done")
    assert store.create_job("/media3/x.mkv").id != job.id


def test_active_source_paths_excludes_terminal_jobs(store):
    a = store.create_job("/media3/a.mkv")
    store.create_job("/media3/b.mkv")
    store.set_stage(a.id, "done")
    assert store.active_source_paths() == {"/media3/b.mkv"}


def test_plan_fields_round_trip(store):
    job = store.create_job("/media3/x.mkv")
    store.set_plan(job.id, "CPU 4K", "r1", {"height": 2160}, 21_902_137_344)
    fetched = store.get_job(job.id)
    assert fetched.preset_name == "CPU 4K"
    assert fetched.rule_id == "r1"
    assert fetched.facts == {"height": 2160}
    assert fetched.original_size == 21_902_137_344


def test_recovery_requeues_interrupted_jobs(store):
    """After a restart a job left `encoding` has no worker. It must come back
    as `queued`, not sit mid-stage forever."""
    a, b, c = (store.create_job(f"/media3/{n}.mkv") for n in "abc")
    store.set_stage(a.id, "encoding")
    store.set_stage(b.id, "swapping")
    store.set_stage(c.id, "done")
    recovered = store.reset_active_for_recovery()
    assert {j.id for j in recovered} == {a.id, b.id}
    assert store.get_job(a.id).stage == "queued"
    assert store.get_job(c.id).stage == "done"


@pytest.mark.parametrize("stage", ["blocked", "pending"])
def test_stages_awaiting_a_human_are_not_auto_recovered(store, stage):
    """Both mean 'a person has not answered yet'. Resuming would answer for
    them -- dispatching an encode the user was asked to approve."""
    job = store.create_job("/media3/x.mkv")
    store.set_stage(job.id, stage)
    assert store.reset_active_for_recovery() == []
    assert store.get_job(job.id).stage == stage


def test_purge_expired_removes_only_old_terminal_jobs(store):
    old, fresh, running = (store.create_job(f"/media3/{n}.mkv")
                           for n in ("old", "fresh", "run"))
    store.set_stage(old.id, "done")
    store.set_stage(fresh.id, "done")
    store.set_stage(running.id, "encoding")
    store._conn.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-10 days') WHERE id = ?",
        (old.id,))
    assert store.purge_expired(ttl_seconds=86400) == 1
    assert store.get_job(old.id) is None
    assert store.get_job(fresh.id) is not None
    assert store.get_job(running.id) is not None


def test_due_retentions_returns_only_elapsed_ones(store):
    a, b = store.create_job("/media3/a.mkv"), store.create_job("/media3/b.mkv")
    now = time.time()
    store.set_retention(a.id, "/media3/.orig-a.mkv", expires_at=now - 5)
    store.set_retention(b.id, "/media3/.orig-b.mkv", expires_at=now + 3600)
    assert [j.id for j in store.due_retentions(now)] == [a.id]


def test_presets_round_trip_with_the_verbatim_body(store):
    body = {"PresetName": "P", "VideoEncoder": "x264", "Extra": {"deep": [1, 2]}}
    store.replace_presets([NamedPreset("P", "x264", "veryfast", "av_mkv", body)])
    stored = store.list_presets()
    assert (stored[0].name, stored[0].encoder, stored[0].body) == ("P", "x264", body)


def test_importing_a_second_file_does_not_drop_the_first(store):
    store.replace_presets([NamedPreset("A", "x264", "fast", "av_mkv", {"n": "A"})])
    store.replace_presets([NamedPreset("B", "x265", "slow", "av_mkv", {"n": "B"})])
    assert {p.name for p in store.list_presets()} == {"A", "B"}


def test_reimporting_a_name_overwrites_it(store):
    store.replace_presets([NamedPreset("A", "x264", "fast", "av_mkv", {"v": 1})])
    store.replace_presets([NamedPreset("A", "x265", "slow", "av_mkv", {"v": 2})])
    assert [p.encoder for p in store.list_presets()] == ["x265"]


def test_delete_preset(store):
    store.replace_presets([NamedPreset("A", "x264", "fast", "av_mkv", {})])
    assert store.delete_preset("A") is True
    assert store.delete_preset("A") is False


def test_rules_round_trip_in_order(store):
    rules = [Rule("r1", [Condition("height", ">=", 2160)], "CPU 4K"),
             Rule("r2", [Condition("video_codec", "==", "hevc")], "skip")]
    store.replace_rules(rules)
    assert store.list_rules() == rules


def test_replace_rules_is_a_full_replacement(store):
    """Rule order is the semantics. Merging would make the effective order
    depend on insertion history rather than on what the user arranged."""
    store.replace_rules([Rule("r1", [], "A")])
    store.replace_rules([Rule("r2", [], "B")])
    assert [r.id for r in store.list_rules()] == ["r2"]


def test_settings_round_trip(store):
    assert store.get_setting("fallback_target", "skip") == "skip"
    store.set_setting("fallback_target", "NVENC 1080p")
    assert store.get_setting("fallback_target", "skip") == "NVENC 1080p"


def test_schema_survives_reopening(store, tmp_path):
    store.replace_rules([Rule("r1", [Condition("height", ">=", 1)], "A")])
    store.close()
    reopened = EncoderStore(str(tmp_path / "encoder.db"))
    try:
        assert [r.id for r in reopened.list_rules()] == ["r1"]
    finally:
        reopened.close()
