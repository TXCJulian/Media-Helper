import logging
import queue as queue_mod
import threading
from typing import Callable

from app.downloader.store import Job, JobStore

logger = logging.getLogger(__name__)

_SHUTDOWN = object()

# Why a running job's cancel event was set. Recorded at the moment the event is
# set, because the two are indistinguishable afterwards and they mean opposite
# things: a user cancellation is final, a shutdown interruption must resume.
_USER_CANCEL = "user"
_SHUTDOWN_CANCEL = "shutdown"

JobRunner = Callable[[JobStore, Job, threading.Event], None]


class DownloadQueue:
    """Worker pool draining a FIFO of job ids.

    Jobs beyond the worker count wait in the queue; they are never rejected.
    Worker threads outlive any HTTP request, so a disconnecting client cannot
    interrupt or orphan work.
    """

    def __init__(self, store: JobStore, runner: JobRunner, workers: int = 3) -> None:
        self._store = store
        self._runner = runner
        self._worker_count = max(1, int(workers))
        self._queue: queue_mod.Queue = queue_mod.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._active: dict[str, threading.Event] = {}
        self._cancelled_while_queued: set[str] = set()
        self._cancel_reason: dict[str, str] = {}
        self._pending: set[str] = set()
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        for i in range(self._worker_count):
            thread = threading.Thread(
                target=self._work, name=f"downloader-worker-{i}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            for job_id, event in self._active.items():
                # setdefault: a user cancellation already recorded for this job
                # stands. The user's intent outranks the shutdown.
                self._cancel_reason.setdefault(job_id, _SHUTDOWN_CANCEL)
                event.set()
        for _ in self._threads:
            self._queue.put(_SHUTDOWN)
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    def enqueue(self, job_id: str) -> None:
        # Deliberately outside self._lock: nothing below depends on the flag,
        # and this keeps the single store write on the enqueue path off the
        # queue -> store lock nesting. Every route that starts a job goes
        # through here, so this is the one place recovery's "was it ever
        # started" flag has to be set.
        self._store.mark_enqueued(job_id)
        with self._lock:
            self._cancelled_while_queued.discard(job_id)
            self._pending.add(job_id)
        self._queue.put(job_id)

    def depth(self) -> int:
        return self._queue.qsize()

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._active

    def cancel(self, job_id: str) -> bool:
        """Cancel a running or queued job. Returns False if it is neither."""
        # The store read happens under the lock on purpose: releasing it first
        # lets a worker move the job from queued to active in between, so the
        # cancel would be recorded in the store while the job runs on
        # unwatched, with no cancel event ever set. Holding the lock forces
        # either `_run_one` to win (job is active, we set its event) or this
        # call to win (job is marked before `_run_one` can claim it).
        with self._lock:
            event = self._active.get(job_id)
            if event is not None:
                # Plain assignment, not setdefault: an explicit user
                # cancellation overrides a shutdown already in progress, so
                # `cancelled` always means the user asked for it.
                self._cancel_reason[job_id] = _USER_CANCEL
                event.set()
                return True

            job = self._store.get_job(job_id)
            if job is None or job.stage != "queued":
                return False

            self._cancelled_while_queued.add(job_id)

        self._store.set_job_stage(job_id, "cancelled", "Cancelled by user")
        return True

    def recover(self) -> None:
        """Re-enqueue jobs left mid-flight by a previous process.

        `reset_active_to_queued()` resets every job in an active stage,
        including any this process is already running or has queued, so jobs
        this process owns are skipped rather than enqueued a second time.
        """
        for job_id in self._store.reset_active_to_queued():
            with self._lock:
                owned = job_id in self._active or job_id in self._pending
            if owned:
                logger.debug("Job %s is already live here; not recovering", job_id)
                continue
            self.enqueue(job_id)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SHUTDOWN:
                    return
                with self._lock:
                    self._pending.discard(str(item))
                    running = self._running
                if not running:
                    # Shutting down. The sentinels sit behind whatever backlog
                    # was queued, so without this check stop() would run the
                    # entire remaining queue before any worker saw one. Skipped
                    # jobs keep stage `queued` and recover() re-enqueues them.
                    continue
                self._run_one(str(item))
            finally:
                self._queue.task_done()

    def _reason_for(self, job_id: str) -> str | None:
        with self._lock:
            return self._cancel_reason.get(job_id)

    def _run_one(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._cancelled_while_queued:
                self._cancelled_while_queued.discard(job_id)
                return
            if job_id in self._active:
                # A duplicate enqueue of a job that is already running. Taking
                # the slot would overwrite the live runner's cancel event with
                # one nothing holds, leaving that runner uncancellable.
                logger.warning(
                    "Job %s is already running; ignoring duplicate enqueue", job_id
                )
                return
            cancel_event = threading.Event()
            self._active[job_id] = cancel_event

        try:
            job = self._store.get_job(job_id)
            if job is None:
                return
            if job.stage == "cancelled":
                return
            self._runner(self._store, job, cancel_event)
        except Exception as exc:
            # A runner that aborts by raising once cancelled must still land in
            # `cancelled`: cancellation never produces `error`.
            if cancel_event.is_set():
                logger.info("Job %s aborted after cancellation: %s", job_id, exc)
                if self._reason_for(job_id) == _SHUTDOWN_CANCEL:
                    # Written straight to `queued` so no observer ever sees a
                    # shutdown briefly claiming to be a user cancellation.
                    stage, message = "queued", None
                else:
                    stage, message = "cancelled", "Cancelled by user"
            else:
                logger.error(
                    "Worker failed on job %s: %s", job_id, exc, exc_info=True
                )
                stage, message = "error", str(exc)
            try:
                self._store.set_job_stage(job_id, stage, message)
            except Exception:
                logger.exception("Could not record failure for job %s", job_id)
        finally:
            # The reason and the stage it implies are settled in one locked
            # section. Reading the reason and writing the stage separately
            # loses a cancel: `cancel()` can overwrite the reason in between,
            # returning True to the API while the write that lands is the one
            # the superseded reason chose.
            with self._lock:
                self._active.pop(job_id, None)
                reason = self._cancel_reason.pop(job_id, None)
                if reason is not None:
                    self._settle_cancelled_stage_locked(job_id, reason)

    def _settle_cancelled_stage_locked(self, job_id: str, reason: str) -> None:
        """Reconcile the stored stage with why the job's cancel event was set.

        Call while holding self._lock, so a concurrent cancel() cannot change
        the reason between this decision and the write it implies.
        """
        job = self._store.get_job(job_id)
        if job is None:
            return
        if reason == _SHUTDOWN_CANCEL and job.stage == "cancelled":
            # The process going down is not something the user did, so the job
            # must not be left claiming they cancelled it. `queued` is the
            # honest resting state for "interrupted before finishing", and it
            # is what the next process's recover() resumes from.
            self._store.set_job_stage(job_id, "queued")
        elif reason == _USER_CANCEL and job.stage == "queued":
            # The user cancelled while the job was unwinding under a shutdown,
            # so the shutdown's `queued` landed last. Their cancellation is the
            # authoritative outcome, and the API already told them it took.
            self._store.set_job_stage(job_id, "cancelled", "Cancelled by user")
