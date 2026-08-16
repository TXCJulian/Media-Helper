import sqlite3
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


def test_set_stage_rejects_an_unknown_stage(store):
    job = store.create_job("/media3/x.mkv")
    with pytest.raises(ValueError):
        store.set_stage(job.id, "encdoing")
    assert store.get_job(job.id).stage == "settling"


def test_set_stage_accepts_every_valid_stage(store):
    job = store.create_job("/media3/x.mkv")
    for stage in ("pending", "queued", "encoding", "swapping", "blocked",
                  "skipped", "cancelled", "failed", "done"):
        job = store.create_job(f"/media3/{stage}.mkv")
        store.set_stage(job.id, stage)
        assert store.get_job(job.id).stage == stage


def test_only_one_active_job_per_source_path(store):
    """The watcher can see one file twice -- a second event, a rescan after
    restart. Two jobs encoding one file would race for the same output."""
    store.create_job("/media3/x.mkv")
    with pytest.raises(sqlite3.IntegrityError):
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


def test_purge_expired_also_purges_a_retained_original(store, tmp_path):
    """`original_kept_path` is the only pointer to a preserved original --
    purging the row without also purging the file it points at leaks that
    file in the holding area forever."""
    kept = tmp_path / "hold" / "old-original.mkv"
    kept.parent.mkdir()
    kept.write_bytes(b"x")
    job = store.create_job("/media3/old.mkv")
    store.set_stage(job.id, "done")
    store.set_retention(job.id, str(kept), expires_at=time.time() + 3600)
    store._conn.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-10 days') WHERE id = ?",
        (job.id,))

    assert store.purge_expired(ttl_seconds=86400) == 1
    assert store.get_job(job.id) is None
    assert not kept.exists()


def test_delete_job_also_purges_a_retained_original(store, tmp_path):
    kept = tmp_path / "hold" / "deleted-original.mkv"
    kept.parent.mkdir()
    kept.write_bytes(b"x")
    job = store.create_job("/media3/gone.mkv")
    store.set_stage(job.id, "done")
    store.set_retention(job.id, str(kept), expires_at=time.time() + 3600)

    assert store.delete_job(job.id) is True
    assert not kept.exists()


def test_delete_job_with_no_retained_original_is_unaffected(store):
    """A job that never retained anything must delete cleanly with no
    filesystem side effect attempted."""
    job = store.create_job("/media3/plain.mkv")
    assert store.delete_job(job.id) is True
    assert store.get_job(job.id) is None


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


# ---------------------------------------------------------------------------
# Atomicity of the multi-write methods.
#
# Each of these writes more than one row and then commits. Committing at the
# end is not a transaction: an exception between the writes leaves the earlier
# ones sitting in the connection's open transaction, and the next unrelated
# commit persists them. Every test here reopens the database, because state
# that survived only in the live connection would hide exactly that bug.
# ---------------------------------------------------------------------------


class _FailingConnection:
    """Delegates to a real connection but raises on one statement.

    `sqlite3.Connection.execute` is read-only, so the failure is injected by
    wrapping rather than by patching. `__enter__`/`__exit__` forward to the
    real connection so the transaction semantics under test are the genuine
    ones.
    """

    def __init__(self, real, failing_sql):
        self._real = real
        self._failing_sql = failing_sql

    def _guard(self, sql):
        if self._failing_sql in sql:
            raise sqlite3.IntegrityError("injected failure")

    def execute(self, sql, *args, **kwargs):
        self._guard(sql)
        return self._real.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self._guard(sql)
        return self._real.executemany(sql, *args, **kwargs)

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *exc_info):
        return self._real.__exit__(*exc_info)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_create_job_rolls_back_when_the_fingerprint_write_fails(tmp_path):
    """A half-written job is worse than no job: it holds the active-source
    unique index, `settling` is skipped by restart recovery, and the watcher
    will not reconsider a file whose job already exists. It drops out of the
    system entirely, while looking like work in progress."""
    db = tmp_path / "encoder.db"
    store = EncoderStore(str(db))
    real = store._conn
    store._conn = _FailingConnection(real, "INSERT INTO seen_files")

    with pytest.raises(sqlite3.IntegrityError):
        store.create_job("/media/movie.mkv", 4096, 1_700_000_000_000_000_000)

    store._conn = real
    # Force a later, unrelated commit: this is what used to persist the
    # orphaned job insert left behind in the open transaction.
    store.set_setting("unrelated", "value")
    store.close()

    reopened = EncoderStore(str(db))
    try:
        assert reopened.list_jobs() == [], "an orphaned job survived"
        assert reopened.seen_fingerprints() == {}
    finally:
        reopened.close()


def test_replace_rules_rolls_back_when_the_insert_fails(tmp_path):
    """The DELETE and the INSERT must stand or fall together, or a failed save
    silently discards every rule the user had."""
    db = tmp_path / "encoder.db"
    store = EncoderStore(str(db))
    store.replace_rules([
        Rule(id="keep", conditions=[Condition("height", ">=", 1080)],
             target="skip"),
    ])
    assert [r.id for r in store.list_rules()] == ["keep"]

    real = store._conn
    store._conn = _FailingConnection(real, "INSERT INTO rules")
    with pytest.raises(sqlite3.IntegrityError):
        store.replace_rules([
            Rule(id="new", conditions=[Condition("width", ">=", 1920)],
                 target="skip"),
        ])

    store._conn = real
    store.set_setting("unrelated", "value")
    store.close()

    reopened = EncoderStore(str(db))
    try:
        assert [r.id for r in reopened.list_rules()] == ["keep"], \
            "a failed save wiped the existing rules"
    finally:
        reopened.close()


def test_create_job_without_a_fingerprint_still_commits(tmp_path):
    """Control: the fingerprint arguments are optional, and omitting them must
    not change the job's own durability."""
    db = tmp_path / "encoder.db"
    store = EncoderStore(str(db))
    job = store.create_job("/media/movie.mkv")
    store.close()

    reopened = EncoderStore(str(db))
    try:
        assert [j.id for j in reopened.list_jobs()] == [job.id]
        assert reopened.seen_fingerprints() == {}
    finally:
        reopened.close()


def test_create_job_commits_both_rows_together(tmp_path):
    """Control for the success path: both rows must be durable, not just the
    job -- the fingerprint is what stops the file being re-probed forever."""
    db = tmp_path / "encoder.db"
    store = EncoderStore(str(db))
    store.create_job("/media/movie.mkv", 4096, 1_700_000_000_000_000_000)
    store.close()

    reopened = EncoderStore(str(db))
    try:
        assert len(reopened.list_jobs()) == 1
        assert reopened.seen_fingerprints() == {
            "/media/movie.mkv": (4096, 1_700_000_000_000_000_000)
        }
    finally:
        reopened.close()
