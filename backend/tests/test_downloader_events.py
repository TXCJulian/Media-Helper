import queue

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


def test_publish_delivers_newest_event_despite_concurrent_drain():
    """Regression test for a race in the Full-recovery path.

    Sequence under test: put_nowait raises Full, then -- before the
    recovery get_nowait runs -- a concurrent consumer (e.g. the SSE
    handler reading this same queue on another thread) drains the queue
    completely. The recovery get_nowait then raises Empty. The newest
    event must still be delivered once capacity is available; only the
    oldest entry may ever be sacrificed, per the class docstring.

    A real background thread cannot be forced into this exact
    interleaving deterministically -- it would only be probabilistic,
    and a probabilistic test here would be flaky (most runs the retry
    put would just race past instead of hitting the empty case). Per
    the brief's own guidance, we instead simulate the concurrent drain
    deterministically by monkeypatching get_nowait to perform the drain
    itself (standing in for the other thread) before restoring the real
    method, so the interleaving described above is exercised on every
    run without any timing dependency.
    """
    bus = EventBroadcaster(maxsize=1)
    q = bus.subscribe()
    q.put_nowait({"n": "old"})  # fill to capacity so the next publish hits Full

    real_get_nowait = q.get_nowait

    def concurrent_drain_then_empty():
        # Simulate another thread draining the queue first, then our own
        # recovery get_nowait finding nothing left -- exactly the
        # interleaving that previously caused the new event to be lost.
        q.get_nowait = real_get_nowait
        real_get_nowait()
        raise queue.Empty()

    q.get_nowait = concurrent_drain_then_empty

    bus.publish({"n": "new"})

    assert q.get_nowait() == {"n": "new"}
