import queue
import threading
from dataclasses import asdict
from typing import Any

from app.downloader.store import Job


def job_to_payload(job: Job) -> dict[str, Any]:
    """Serialise a job for the wire. Options stay server-side."""
    return {
        "job_id": job.id,
        "url": job.url,
        "stage": job.stage,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "items": [asdict(item) for item in job.items],
    }


class EventBroadcaster:
    """Fan-out of job state changes to connected SSE clients.

    Publishing never blocks: a subscriber that cannot keep up drops its
    oldest event. Authoritative state always remains in the store, so a
    client that misses a delta still converges on reconnect.
    """

    def __init__(self, maxsize: int = 200) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass
