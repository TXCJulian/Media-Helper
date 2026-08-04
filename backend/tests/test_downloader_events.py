from app.downloader.events import EventBroadcaster, job_to_payload
from app.downloader.store import Item, Job


def _job() -> Job:
    return Job(
        id="abc",
        url="https://example.com/v",
        options={"type": "video"},
        stage="downloading",
        error=None,
        created_at="2026-08-04 10:00:00",
        updated_at="2026-08-04 10:00:01",
        items=[Item(index=0, title="A", progress=12.5, stage="downloading")],
    )


def test_job_to_payload_shape():
    payload = job_to_payload(_job())
    assert payload["job_id"] == "abc"
    assert payload["stage"] == "downloading"
    assert payload["items"][0]["progress"] == 12.5
    assert "options" not in payload


def test_publish_reaches_all_subscribers():
    bus = EventBroadcaster()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"job_id": "abc"})
    assert a.get_nowait() == {"job_id": "abc"}
    assert b.get_nowait() == {"job_id": "abc"}


def test_unsubscribe_stops_delivery():
    bus = EventBroadcaster()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish({"job_id": "abc"})
    assert q.empty()
    assert bus.subscriber_count() == 0


def test_slow_subscriber_does_not_block_publisher():
    bus = EventBroadcaster(maxsize=2)
    q = bus.subscribe()
    for i in range(10):
        bus.publish({"n": i})
    assert q.qsize() == 2
