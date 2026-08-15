"""SSE fan-out of encode-job state.

Same contract as the downloader's broadcaster: publishing never blocks, a
subscriber that cannot keep up drops its oldest event, and the store remains
authoritative so a client that misses a delta converges on reconnect.
"""

import queue
import threading
from typing import Any

from app.encoder.store import Job


def job_to_payload(job: Job) -> dict[str, Any]:
    """Serialise a job for the wire.

    ``saved_bytes`` is computed here rather than in the client because the done
    card leads with it -- it is the number that says whether a preset is worth
    keeping -- and two clients should not each reimplement the arithmetic.
    """
    saved = None
    if job.original_size and job.encoded_size:
        saved = job.original_size - job.encoded_size
    return {
        "job_id": job.id,
        "source_path": job.source_path,
        "stage": job.stage,
        "progress": round(job.progress, 1),
        "preset_name": job.preset_name,
        "rule_id": job.rule_id,
        "error": job.error,
        "error_code": job.error_code,
        "output_path": job.output_path,
        "facts": job.facts,
        "original_size": job.original_size,
        "encoded_size": job.encoded_size,
        "saved_bytes": saved,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


class EventBroadcaster:
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
