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
    store.set_job_stage(a, "downloading")
    store.set_job_stage(b, "transcoding")
    store.set_job_stage(c, "done")

    recovered = store.reset_active_to_queued()

    assert set(recovered) == {a, b}
    assert store.get_job(a).stage == "queued"
    assert store.get_job(b).stage == "queued"
    assert store.get_job(c).stage == "done"


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
