import threading
import time

import pytest

from app.downloader.queue import DownloadQueue
from app.downloader.store import JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "d.db"))


def test_jobs_beyond_worker_count_wait_instead_of_failing(store):
    started = threading.Semaphore(0)
    release = threading.Event()
    finished: list[str] = []

    def slow_runner(store_, job, cancel_event):
        started.release()
        release.wait(timeout=5)
        finished.append(job.id)
        store_.set_job_stage(job.id, "done")

    q = DownloadQueue(store, slow_runner, workers=2)
    q.start()
    try:
        job_ids = [store.create_job(f"https://example.com/{i}", {}) for i in range(5)]
        for job_id in job_ids:
            q.enqueue(job_id)

        assert started.acquire(timeout=5)
        assert started.acquire(timeout=5)
        time.sleep(0.2)
        assert len(finished) == 0, "only 2 workers should be running"
        assert q.depth() >= 1, "the rest must be waiting, not rejected"

        release.set()
        deadline = time.monotonic() + 10
        while len(finished) < 5 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(finished) == 5
    finally:
        release.set()
        q.stop()


def test_cancel_queued_job_marks_cancelled_without_running(store):
    gate = threading.Event()
    ran: list[str] = []

    def blocking_runner(store_, job, cancel_event):
        ran.append(job.id)
        gate.wait(timeout=5)
        store_.set_job_stage(job.id, "done")

    q = DownloadQueue(store, blocking_runner, workers=1)
    q.start()
    try:
        first = store.create_job("https://example.com/1", {})
        second = store.create_job("https://example.com/2", {})
        q.enqueue(first)
        q.enqueue(second)

        deadline = time.monotonic() + 5
        while not ran and time.monotonic() < deadline:
            time.sleep(0.02)

        assert q.cancel(second) is True
        assert store.get_job(second).stage == "cancelled"

        gate.set()
        time.sleep(0.3)
        assert second not in ran
    finally:
        gate.set()
        q.stop()


def test_cancel_running_job_sets_its_event(store):
    observed = threading.Event()

    def watching_runner(store_, job, cancel_event):
        for _ in range(100):
            if cancel_event.is_set():
                observed.set()
                store_.set_job_stage(job.id, "cancelled", "Cancelled by user")
                return
            time.sleep(0.02)
        store_.set_job_stage(job.id, "done")

    q = DownloadQueue(store, watching_runner, workers=1)
    q.start()
    try:
        job_id = store.create_job("https://example.com/1", {})
        q.enqueue(job_id)

        deadline = time.monotonic() + 5
        while not q.is_active(job_id) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert q.cancel(job_id) is True
        assert observed.wait(timeout=5)
        assert store.get_job(job_id).stage == "cancelled"
    finally:
        q.stop()


def test_cancel_unknown_job_returns_false(store):
    q = DownloadQueue(store, lambda s, j, c: None, workers=1)
    assert q.cancel("00000000-0000-0000-0000-000000000000") is False


def test_runner_exception_does_not_kill_the_worker(store):
    done = threading.Event()

    def flaky_runner(store_, job, cancel_event):
        if job.url.endswith("1"):
            raise RuntimeError("boom")
        store_.set_job_stage(job.id, "done")
        done.set()

    q = DownloadQueue(store, flaky_runner, workers=1)
    q.start()
    try:
        first = store.create_job("https://example.com/1", {})
        second = store.create_job("https://example.com/2", {})
        q.enqueue(first)
        q.enqueue(second)

        assert done.wait(timeout=5)
        assert store.get_job(first).stage == "error"
        assert store.get_job(second).stage == "done"
    finally:
        q.stop()


class _ParkingStore:
    """Delegates to a real JobStore but parks `get_job` for one named thread.

    Lets a test hold `cancel()` precisely inside its store read, which is the
    window in which a worker can pick the same job up off the queue.
    """

    def __init__(self, store, thread_name, entered, proceed):
        self._store = store
        self._thread_name = thread_name
        self._entered = entered
        self._proceed = proceed

    def __getattr__(self, name):
        return getattr(self._store, name)

    def get_job(self, job_id):
        if threading.current_thread().name == self._thread_name:
            self._entered.set()
            self._proceed.wait(timeout=5)
        return self._store.get_job(job_id)


def test_cancel_is_not_lost_when_a_worker_picks_the_job_up(store):
    entered = threading.Event()
    proceed = threading.Event()
    started = threading.Event()
    result: dict[str, bool] = {}

    def runner(store_, job, cancel_event):
        started.set()

    parking = _ParkingStore(store, "canceller", entered, proceed)
    q = DownloadQueue(parking, runner, workers=1)
    q.start()
    job_id = store.create_job("https://example.com/1", {})
    canceller = threading.Thread(
        target=lambda: result.__setitem__("returned", q.cancel(job_id)),
        name="canceller",
    )
    canceller.start()
    try:
        assert entered.wait(timeout=5)
        q.enqueue(job_id)
        assert not started.wait(0.5), "worker ran a job that was being cancelled"

        proceed.set()
        canceller.join(timeout=5)
        assert result["returned"] is True
        assert not started.wait(0.5), "worker ran a job that was already cancelled"
        assert store.get_job(job_id).stage == "cancelled"
    finally:
        proceed.set()
        canceller.join(timeout=5)
        q.stop()


def test_stop_does_not_run_the_pending_backlog(store):
    started = threading.Semaphore(0)
    release = threading.Event()
    ran: list[str] = []

    def runner(store_, job, cancel_event):
        ran.append(job.id)
        started.release()
        while not cancel_event.is_set() and not release.is_set():
            time.sleep(0.01)
        store_.set_job_stage(job.id, "cancelled" if cancel_event.is_set() else "done")

    q = DownloadQueue(store, runner, workers=1)
    q.start()
    job_ids = [store.create_job(f"https://example.com/{i}", {}) for i in range(3)]
    try:
        for job_id in job_ids:
            q.enqueue(job_id)
        assert started.acquire(timeout=5)

        q.stop(timeout=1.0)
        assert ran == [job_ids[0]], "stop() must not start queued jobs"
        assert store.get_job(job_ids[1]).stage == "queued"
        assert store.get_job(job_ids[2]).stage == "queued"
    finally:
        release.set()
        q.stop()


def test_cancelled_job_whose_runner_raises_is_not_marked_error(store):
    raised = threading.Event()

    def angry_runner(store_, job, cancel_event):
        while not cancel_event.is_set():
            time.sleep(0.01)
        raised.set()
        raise RuntimeError("aborted")

    q = DownloadQueue(store, angry_runner, workers=1)
    q.start()
    try:
        job_id = store.create_job("https://example.com/1", {})
        q.enqueue(job_id)

        deadline = time.monotonic() + 5
        while not q.is_active(job_id) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert q.cancel(job_id) is True
        assert raised.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_job(job_id).stage in {"cancelled", "error"}:
                break
            time.sleep(0.02)
        assert store.get_job(job_id).stage == "cancelled"
    finally:
        q.stop()


def test_job_interrupted_by_shutdown_ends_queued_and_is_recovered(store):
    """A process shutdown is not a user cancellation and must not be recorded
    as one; the job has to come back on the next process's recover()."""
    started = threading.Semaphore(0)

    def runner(store_, job, cancel_event):
        started.release()
        cancel_event.wait(timeout=5)
        store_.set_job_stage(job.id, "cancelled", "Cancelled by user")

    q = DownloadQueue(store, runner, workers=1)
    q.start()
    try:
        job_id = store.create_job("https://example.com/1", {})
        q.enqueue(job_id)
        assert started.acquire(timeout=5)

        q.stop(timeout=5)
        interrupted = store.get_job(job_id)
        assert interrupted.stage == "queued", "shutdown must not claim the user cancelled"
        assert interrupted.error is None
    finally:
        q.stop()

    seen: list[str] = []
    done = threading.Event()

    def next_process_runner(store_, job, cancel_event):
        seen.append(job.id)
        store_.set_job_stage(job.id, "done")
        done.set()

    fresh = DownloadQueue(store, next_process_runner, workers=1)
    fresh.start()
    try:
        fresh.recover()
        assert done.wait(timeout=5)
        assert seen == [job_id]
    finally:
        fresh.stop()


def test_user_cancelled_job_is_not_resurrected_by_a_later_recover(store):
    def runner(store_, job, cancel_event):
        cancel_event.wait(timeout=5)
        store_.set_job_stage(job.id, "cancelled", "Cancelled by user")

    q = DownloadQueue(store, runner, workers=1)
    q.start()
    try:
        job_id = store.create_job("https://example.com/1", {})
        q.enqueue(job_id)

        deadline = time.monotonic() + 5
        while not q.is_active(job_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert q.cancel(job_id) is True

        deadline = time.monotonic() + 5
        while q.is_active(job_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert store.get_job(job_id).stage == "cancelled"
    finally:
        q.stop()

    assert store.get_job(job_id).stage == "cancelled"

    seen: list[str] = []
    fresh = DownloadQueue(store, lambda s, j, c: seen.append(j.id), workers=1)
    fresh.start()
    try:
        fresh.recover()
        time.sleep(0.3)
        assert seen == [], "a user cancellation must not be resurrected"
    finally:
        fresh.stop()


def test_user_cancel_still_in_flight_when_stop_lands_stays_cancelled(store):
    """The user cancelled first; a shutdown arriving before the runner has
    finished unwinding must not rewrite their cancellation into `queued`."""
    saw_cancel = threading.Event()
    release = threading.Event()

    def runner(store_, job, cancel_event):
        cancel_event.wait(timeout=5)
        saw_cancel.set()
        release.wait(timeout=5)
        store_.set_job_stage(job.id, "cancelled", "Cancelled by user")

    q = DownloadQueue(store, runner, workers=1)
    q.start()
    job_id = store.create_job("https://example.com/1", {})
    try:
        q.enqueue(job_id)
        deadline = time.monotonic() + 5
        while not q.is_active(job_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert q.cancel(job_id) is True
        assert saw_cancel.wait(timeout=5)

        stopper = threading.Thread(target=lambda: q.stop(timeout=5))
        stopper.start()
        # A sentinel on the queue proves stop() has passed its locked section,
        # so its shutdown bookkeeping has already run against this job.
        deadline = time.monotonic() + 5
        while q.depth() < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        release.set()
        stopper.join(timeout=10)

        job = store.get_job(job_id)
        assert job.stage == "cancelled", "user cancellation must win over shutdown"
        assert job.error == "Cancelled by user"
    finally:
        release.set()
        q.stop()


def test_recover_requeues_interrupted_jobs(store):
    done = threading.Event()
    seen: list[str] = []

    def runner(store_, job, cancel_event):
        seen.append(job.id)
        store_.set_job_stage(job.id, "done")
        done.set()

    orphan = store.create_job("https://example.com/orphan", {})
    store.set_job_stage(orphan, "downloading")

    q = DownloadQueue(store, runner, workers=1)
    q.start()
    try:
        q.recover()
        assert done.wait(timeout=5)
        assert seen == [orphan]
    finally:
        q.stop()
