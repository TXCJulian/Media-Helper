import sqlite3

import pytest

from app.downloader.store import Item, Job, JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "downloader.db"))


def test_create_and_get_job(store):
    job_id = store.create_job("https://example.com/v", {"type": "video"})
    job = store.get_job(job_id)
    assert job is not None
    assert job.url == "https://example.com/v"
    assert job.options == {"type": "video"}
    assert job.stage == "queued"
    assert job.items == []


def test_get_missing_job_returns_none(store):
    assert store.get_job("00000000-0000-0000-0000-000000000000") is None


def test_upsert_item_creates_then_updates(store):
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, title="Song A", stage="downloading", progress=10.0)
    store.upsert_item(job_id, 0, progress=55.5)

    items = store.get_job(job_id).items
    assert len(items) == 1
    assert items[0].title == "Song A"
    assert items[0].progress == 55.5
    assert items[0].stage == "downloading"


def test_multiple_items_keep_order(store):
    job_id = store.create_job("https://example.com/playlist", {})
    for i, title in enumerate(["A", "B", "C"]):
        store.upsert_item(job_id, i, title=title)
    assert [i.title for i in store.get_job(job_id).items] == ["A", "B", "C"]


def test_set_job_stage_is_not_read_modify_write(store):
    """Two independent writers must not lose each other's updates."""
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, progress=42.0)
    store.set_job_stage(job_id, "cancelled", error="Cancelled by user")

    job = store.get_job(job_id)
    assert job.stage == "cancelled"
    assert job.error == "Cancelled by user"
    assert job.items[0].progress == 42.0


def test_list_jobs_newest_first(store):
    first = store.create_job("https://example.com/1", {})
    second = store.create_job("https://example.com/2", {})
    assert [j.id for j in store.list_jobs()][:2] == [second, first]


def test_delete_job_removes_items(store):
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, title="A")
    assert store.delete_job(job_id) is True
    assert store.get_job(job_id) is None
    assert store.delete_job(job_id) is False


def test_reset_active_to_queued_recovers_after_restart(store):
    a = store.create_job("https://example.com/a", {})
    b = store.create_job("https://example.com/b", {})
    c = store.create_job("https://example.com/c", {})
    # Every one of these reached the queue; a job cannot get to `downloading`
    # or `transcoding` without having been enqueued first.
    for job_id in (a, b, c):
        store.mark_enqueued(job_id)
    store.set_job_stage(a, "downloading")
    store.set_job_stage(b, "transcoding")
    store.set_job_stage(c, "done")

    recovered = store.reset_active_to_queued()

    assert set(recovered) == {a, b}
    assert store.get_job(a).stage == "queued"
    assert store.get_job(b).stage == "queued"
    assert store.get_job(c).stage == "done"


def test_never_enqueued_job_is_not_recovered_after_restart(store):
    """I4: a job created with auto_start=false rests at `queued` with nothing
    in the schema distinguishing it from one waiting in the FIFO, so recovery
    used to start it at the next boot without the user pressing Start."""
    held = store.create_job("https://example.com/held", {"auto_start": False})
    started = store.create_job("https://example.com/started", {})
    store.mark_enqueued(started)

    recovered = store.reset_active_to_queued()

    assert recovered == [started]
    assert store.get_job(held).stage == "queued"


def test_a_job_interrupted_while_still_queued_is_still_recovered(store):
    """The shutdown path writes an interrupted running job straight back to
    `queued`, and that job must resume. Excluding `queued` wholesale would
    have stranded it, which is why recovery keys off having been enqueued
    rather than off the stage alone."""
    job_id = store.create_job("https://example.com/v", {})
    store.mark_enqueued(job_id)
    store.set_job_stage(job_id, "downloading")
    store.set_job_stage(job_id, "queued")  # what queue.stop() leaves behind

    assert store.reset_active_to_queued() == [job_id]


def test_enqueued_flag_survives_reopening_the_database(tmp_path):
    db = str(tmp_path / "d.db")
    store = JobStore(db)
    held = store.create_job("https://example.com/held", {"auto_start": False})
    started = store.create_job("https://example.com/started", {})
    store.mark_enqueued(started)
    store.close()

    reopened = JobStore(db)
    try:
        assert reopened.reset_active_to_queued() == [started]
        assert reopened.get_job(held).stage == "queued"
    finally:
        reopened.close()


def test_database_predating_the_enqueued_column_keeps_recovering(tmp_path):
    """Rows written before this column existed came from a build that
    enqueued every job it created, so the migration must backfill them as
    enqueued rather than silently stranding a queue full of live work."""
    db = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(db)
    legacy.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, options TEXT NOT NULL,
            stage TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO jobs (id, url, options, stage)
        VALUES ('legacy-1', 'https://example.com/v', '{}', 'downloading');
        """
    )
    legacy.commit()
    legacy.close()

    store = JobStore(db)
    try:
        assert store.reset_active_to_queued() == ["legacy-1"]
        # The migration's backfill must not leak into rows created afterwards:
        # the ALTER's column default has to stay 0, or every new job would be
        # recovered regardless of auto_start. (`legacy-1` still comes back on
        # a second call - it is queued and enqueued, so resuming it is right.)
        fresh = store.create_job("https://example.com/new", {"auto_start": False})
        assert fresh not in store.reset_active_to_queued()
        assert store.get_job(fresh).stage == "queued"
    finally:
        store.close()


def _legacy_database(path, rows):
    """Write a jobs table shaped the way it was before `enqueued` existed."""
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, options TEXT NOT NULL,
            stage TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    legacy.executemany(
        "INSERT INTO jobs (id, url, options, stage) VALUES (?, ?, ?, ?)",
        [
            (job_id, "https://example.com/v", options, stage)
            for job_id, options, stage in rows
        ],
    )
    legacy.commit()
    legacy.close()


@pytest.mark.parametrize(
    "options",
    [
        '{"auto_start": false}',  # JSON boolean, what the UI sends
        '{"auto_start": "false"}',  # string form
        '{"auto_start": "False"}',  # string form, other casing
        '{"auto_start": 0}',  # numeric form
        '{"auto_start": "0"}',
        '{"auto_start": "no"}',
    ],
)
def test_migration_does_not_mark_a_held_legacy_job_as_enqueued(tmp_path, options):
    """A pre-upgrade database can hold jobs resting at `queued` because the
    user never started them - `create_downloads` has always honoured
    `auto_start` - so a blanket backfill would make the first boot after the
    upgrade start exactly the jobs the column was added to protect."""
    db = str(tmp_path / "legacy.db")
    _legacy_database(db, [("held", options, "queued")])

    store = JobStore(db)
    try:
        assert store.reset_active_to_queued() == []
        assert store.get_job("held").stage == "queued"
    finally:
        store.close()


@pytest.mark.parametrize(
    "options",
    [
        "{}",  # no auto_start key at all: auto-start is the default
        '{"auto_start": true}',
        '{"auto_start": "true"}',
        '{"auto_start": 1}',
        '{"auto_start": null}',  # not one of the disabling values
        "not json at all",  # unparseable: fall back to the old behaviour
    ],
)
def test_migration_still_marks_a_legacy_job_that_was_enqueued(tmp_path, options):
    """The other half: a legacy row waiting in the FIFO at `queued` must still
    come back, or the upgrade strands a queue full of live work."""
    db = str(tmp_path / "legacy.db")
    _legacy_database(db, [("waiting", options, "queued")])

    store = JobStore(db)
    try:
        assert store.reset_active_to_queued() == ["waiting"]
    finally:
        store.close()


def test_migration_is_idempotent_across_restarts(tmp_path):
    """The backfill runs only when the column is absent, so reopening a
    migrated database must not re-mark the held row."""
    db = str(tmp_path / "legacy.db")
    _legacy_database(
        db,
        [
            ("held", '{"auto_start": false}', "queued"),
            ("running", "{}", "downloading"),
        ],
    )

    for _ in range(3):
        store = JobStore(db)
        try:
            assert store.reset_active_to_queued() == ["running"]
            assert store.get_job("held").stage == "queued"
        finally:
            store.close()


def test_delete_job_notifies_and_reports_the_deleted_id():
    """I2: delete_job was the only mutator that fired no notification, so a
    deleted job's card stayed on screen until the page was reloaded."""
    deleted = []
    store = JobStore(":memory:", on_delete=deleted.append)
    job_id = store.create_job("https://example.com/v", {})

    assert store.delete_job(job_id) is True
    assert deleted == [job_id]

    # A delete that removed nothing must not announce one.
    assert store.delete_job(job_id) is False
    assert deleted == [job_id]


def test_purge_expired_notifies_for_every_job_it_removes():
    deleted = []
    store = JobStore(":memory:", on_delete=deleted.append)
    old = store.create_job("https://example.com/old", {})
    store.set_job_stage(old, "done")
    store._conn.execute(
        "UPDATE jobs SET created_at = datetime('now', '-10 days') WHERE id = ?", (old,)
    )
    store._conn.commit()

    assert store.purge_expired(ttl_seconds=86400) == 1
    assert deleted == [old]


def test_purge_expired_never_deletes_a_job_without_announcing_it():
    """A row crossing the TTL boundary between collecting the ids and deleting
    them was removed with no notification - the stuck-card symptom this
    notification exists to remove - because each statement evaluated
    `datetime('now', ?)` on its own. One statement cannot straddle the
    boundary, so the outcome is now all-or-nothing either way."""
    deleted = []
    store = JobStore(":memory:", on_delete=deleted.append)
    job_id = store.create_job("https://example.com/v", {})
    store.set_job_stage(job_id, "done")

    real_conn = store._conn
    drifted = []

    class _BoundaryCrossingConnection:
        """Ages the row past the TTL once, in whatever gap the code leaves."""

        def __getattr__(self, name):
            return getattr(real_conn, name)

        def execute(self, sql, params=()):
            cursor = real_conn.execute(sql, params)
            if "FROM jobs" in sql and sql.lstrip().startswith("SELECT") and not drifted:
                drifted.append(True)
                real_conn.execute(
                    "UPDATE jobs SET created_at = datetime('now', '-10 days') "
                    "WHERE id = ?",
                    (job_id,),
                )
            return cursor

    store._conn = _BoundaryCrossingConnection()
    removed = store.purge_expired(ttl_seconds=86400)
    store._conn = real_conn

    gone = store.get_job(job_id) is None
    assert deleted == ([job_id] if gone else []), (
        "a purged job must always be announced"
    )
    assert removed == (1 if gone else 0)


def test_purge_expired_keeps_recent_and_active(store):
    old_done = store.create_job("https://example.com/old", {})
    store.set_job_stage(old_done, "done")
    store._conn.execute(
        "UPDATE jobs SET created_at = datetime('now', '-10 days') WHERE id = ?",
        (old_done,),
    )
    store._conn.commit()
    fresh = store.create_job("https://example.com/fresh", {})

    removed = store.purge_expired(ttl_seconds=86400)

    assert removed == 1
    assert store.get_job(old_done) is None
    assert store.get_job(fresh) is not None


def test_on_change_fires_for_stage_and_item_updates():
    seen = []
    store = JobStore(":memory:", on_change=seen.append)
    job_id = store.create_job("https://example.com/v", {})
    store.set_job_stage(job_id, "downloading")
    store.upsert_item(job_id, 0, progress=1.0)

    assert len(seen) == 3
    assert all(isinstance(j, Job) for j in seen)
    assert seen[-1].items[0].progress == 1.0


def test_on_change_captures_state_during_write_lock():
    """Each callback payload corresponds to its own write, not the latest committed state.

    This test asserts that when multiple threads write to the same job concurrently,
    each callback receives the job state from that specific write, not from whatever
    the latest committed state happens to be at callback invocation time.

    The test is deterministic against the fixed implementation (always passes) but is
    a best-effort detector of regressions. Reproducing the original race condition
    requires a specific thread scheduling in a narrow window between lock release and
    the next lock acquisition, which may not manifest in every run. A maintainer must
    not interpret this test passing as a guaranteed proof that concurrent callback
    ordering is thread-safe; it is a behavioral guard that will fail if the code
    regresses to the broken pattern of reading state after releasing the write lock.
    """
    import threading

    # Track both (stage, thread_id) to ensure each callback corresponds to a write
    seen = []
    lock = threading.Lock()

    def callback(job):
        with lock:
            seen.append((job.stage, job.error))

    store = JobStore(":memory:", on_change=callback)
    job_id = store.create_job("https://example.com/v", {})
    seen.clear()  # Clear the create callback

    # Use a barrier to make threads race
    barrier = threading.Barrier(2)

    def writer_a():
        barrier.wait()
        store.set_job_stage(job_id, "downloading", error=None)

    def writer_b():
        barrier.wait()
        store.set_job_stage(job_id, "transcoding", error="interrupted")

    threads = [
        threading.Thread(target=writer_a),
        threading.Thread(target=writer_b),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should have exactly 2 callbacks
    assert len(seen) == 2, f"Expected 2 callbacks, got {len(seen)}: {seen}"

    # Both stages must appear in the callbacks
    stages = [s for s, e in seen]
    assert "downloading" in stages, \
        f"'downloading' should be in callbacks, got {stages}"
    assert "transcoding" in stages, \
        f"'transcoding' should be in callbacks, got {stages}"

    # Each stage should have its corresponding error
    callbacks_dict = {s: e for s, e in seen}
    assert callbacks_dict.get("downloading") is None, \
        "downloading callback should have error=None"
    assert callbacks_dict.get("transcoding") == "interrupted", \
        "transcoding callback should have error='interrupted'"


def test_prune_items_keeps_only_the_named_indices(store):
    job_id = store.create_job("https://example.com/v", {})
    for index in range(4):
        store.upsert_item(job_id, index, title=f"row {index}", stage="done")

    assert store.prune_items(job_id, {1, 3}) == 2
    assert [i.index for i in store.get_job(job_id).items] == [1, 3]
    assert [i.title for i in store.get_job(job_id).items] == ["row 1", "row 3"]


def test_prune_items_with_an_empty_keep_set_removes_every_row(store):
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, title="row 0")

    assert store.prune_items(job_id, set()) == 1
    assert store.get_job(job_id).items == []


def test_prune_items_leaves_other_jobs_alone(store):
    keep = store.create_job("https://example.com/a", {})
    other = store.create_job("https://example.com/b", {})
    store.upsert_item(keep, 0, title="mine")
    store.upsert_item(other, 0, title="theirs")

    store.prune_items(keep, set())
    assert store.get_job(other).items[0].title == "theirs"


def test_prune_items_announces_the_surviving_job_once():
    """The pruned job has to reach subscribers, and a prune that removed
    nothing must not add a redundant frame."""
    seen = []
    store = JobStore(":memory:", on_change=seen.append)
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, title="intermediate")
    store.upsert_item(job_id, 1, title="real")
    seen.clear()

    assert store.prune_items(job_id, {1}) == 1
    assert len(seen) == 1
    assert [i.index for i in seen[0].items] == [1]

    assert store.prune_items(job_id, {1}) == 0
    assert len(seen) == 1
