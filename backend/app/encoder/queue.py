"""Planning and dispatch: probe, choose, encode, swap.

One worker. The encoder service runs a single encode at a time by default and
refuses at its own limit, so a second dispatcher here would only generate
`queue_full` responses.

The stage machine:

    settling -> pending -> queued -> encoding -> swapping -> done
                   |          |          |
                   |          |          +-> failed / blocked / cancelled
                   +-> skipped
"""

import logging
import queue as queue_mod
import threading
import time

from app.encoder.client import EncoderRejected, EncoderUnreachable, is_retryable
from app.encoder.events import EventBroadcaster, job_to_payload
from app.encoder.probe import ProbeError, probe
from app.encoder.rules import SKIP, RuleError, evaluate
from app.encoder.store import EncoderStore
from app.encoder.swap import SwapError, purge_original, swap_in

logger = logging.getLogger(__name__)

_REQUEUE_SECONDS = 30.0


class EncodeQueue:
    def __init__(
        self,
        store: EncoderStore,
        client,
        broadcaster: EventBroadcaster,
        *,
        mode: str = "review",
        original_ttl: int = 604800,
        holding_dir: str = "/data/encoder/originals",
        poll_interval: float = 2.0,
    ) -> None:
        self._store = store
        self._client = client
        self._events = broadcaster
        self._mode = mode
        self._original_ttl = original_ttl
        self._holding_dir = holding_dir
        self._poll_interval = poll_interval
        self._queue: queue_mod.Queue = queue_mod.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._worker, name="encoder-dispatch", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def recover(self) -> None:
        """Requeue whatever a restart interrupted."""
        for job in self._store.reset_active_for_recovery():
            logger.info("Resuming interrupted encode job %s", job.id)
            self.enqueue(job.id)

    def enqueue(self, job_id: str) -> None:
        self._store.set_stage(job_id, "queued")
        self._publish(job_id)
        self._queue.put(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._store.get_job(job_id)
        if job is None:
            return False
        if job.remote_job_id:
            try:
                self._client.cancel(job.remote_job_id)
            except (EncoderRejected, EncoderUnreachable):
                logger.warning("Could not cancel remote job %s", job.remote_job_id)
        self._store.set_stage(job_id, "cancelled")
        self._publish(job_id)
        return True

    # ---- planning --------------------------------------------------------

    def plan(self, job_id: str) -> str:
        """Probe the file, pick a target, and record the decision.

        Returns the resulting stage. In `review` mode this stops at `pending`
        so a human confirms; in `auto` it enqueues. That split is the safety
        property of the feature, which is why `review` is the default.
        """
        job = self._store.get_job(job_id)
        if job is None:
            return "failed"

        try:
            facts = probe(job.source_path)
        except ProbeError as exc:
            self._fail(job_id, str(exc), "probe_failed")
            return "failed"

        rules = self._store.list_rules()
        fallback = self._store.get_setting("fallback_target", SKIP)
        try:
            match = evaluate(facts, rules, fallback)
        except RuleError as exc:
            self._fail(job_id, str(exc), "invalid_rule")
            return "failed"

        self._store.set_plan(
            job_id,
            preset_name=None if match.target == SKIP else match.target,
            rule_id=match.rule_id,
            facts=facts,
            original_size=facts.get("size") or 0,
        )

        if match.target == SKIP:
            self._store.set_stage(job_id, "skipped")
            self._publish(job_id)
            return "skipped"

        if not any(p.name == match.target for p in self._store.list_presets()):
            # A rule pointing at a deleted preset. Caught here rather than at
            # the encoder, which would report it as a preset_not_found failure
            # after the job had already been dispatched.
            self._fail(
                job_id,
                f"Rule selected preset {match.target!r}, which no longer exists",
                "preset_missing",
            )
            return "failed"

        if self._mode == "auto":
            self.enqueue(job_id)
            return "queued"

        self._store.set_stage(job_id, "pending")
        self._publish(job_id)
        return "pending"

    # ---- retention -------------------------------------------------------

    def run_retentions(self) -> int:
        """Purge originals whose TTL has elapsed. Safe to call repeatedly."""
        purged = 0
        for job in self._store.due_retentions(time.time()):
            if job.original_kept_path and purge_original(job.original_kept_path):
                purged += 1
            self._store.clear_retention(job.id)
        return purged

    # ---- worker ----------------------------------------------------------

    def _worker(self) -> None:
        while not self._stopping.is_set():
            job_id = self._queue.get()
            if job_id is None:
                return
            try:
                self._run(job_id)
            except Exception:
                logger.exception("Encode job %s crashed the dispatcher", job_id)
                self._fail(job_id, "Internal error; see server logs", "internal")

    def _run(self, job_id: str) -> None:
        job = self._store.get_job(job_id)
        if job is None or job.stage == "cancelled":
            return

        preset = next(
            (p for p in self._store.list_presets() if p.name == job.preset_name), None
        )
        if preset is None:
            self._fail(job_id, f"Preset {job.preset_name!r} no longer exists",
                       "preset_missing")
            return

        try:
            remote_id = self._client.submit(job.source_path, preset.body, preset.name)
        except (EncoderRejected, EncoderUnreachable) as exc:
            self._handle_dispatch_error(job_id, exc)
            return

        self._store.set_remote_job(job_id, remote_id)
        self._store.set_stage(job_id, "encoding")
        self._publish(job_id)

        result = self._await_remote(job_id, remote_id)
        if result is None:
            return

        self._publish_result(job_id, job.source_path, result)

    def _await_remote(self, job_id: str, remote_id: str) -> dict | None:
        """Poll until the remote job is terminal. Returns its final body."""
        while not self._stopping.is_set():
            job = self._store.get_job(job_id)
            if job is None or job.stage == "cancelled":
                return None
            try:
                body = self._client.poll(remote_id)
            except EncoderUnreachable:
                # Transient: the encode is still running on the other side.
                time.sleep(self._poll_interval)
                continue
            except EncoderRejected as exc:
                self._fail(job_id, exc.reason, exc.code)
                return None

            progress = body.get("progress")
            if isinstance(progress, (int, float)):
                self._store.set_progress(job_id, float(progress))
                self._publish(job_id)

            status = body.get("status")
            if status == "completed":
                return body
            if status in {"failed", "cancelled"}:
                self._fail(job_id, body.get("error") or f"Encode {status}",
                           "encode_failed")
                return None
            time.sleep(self._poll_interval)
        return None

    def _publish_result(self, job_id: str, source_path: str, body: dict) -> None:
        output_path = body.get("output_path")
        if not output_path:
            self._fail(job_id, "Encoder reported success without an output path",
                       "no_output")
            return

        self._store.set_stage(job_id, "swapping")
        self._publish(job_id)
        try:
            result = swap_in(
                source_path,
                output_path,
                original_ttl=self._original_ttl,
                holding_dir=self._holding_dir,
            )
        except SwapError as exc:
            # The source is intact by construction *unless* the original had
            # already been moved into holding and the restore-back also
            # failed -- in that case exc.kept_path names where it survives,
            # and saying "left untouched" would be a lie the user would act
            # on (they'd go looking for it at source and not find it).
            if exc.kept_path is None:
                self._fail(job_id, f"{exc} (the original was left untouched)",
                           "swap_failed")
            else:
                self._fail(
                    job_id,
                    f"{exc} The original was preserved at {exc.kept_path}.",
                    "swap_failed",
                )
            return

        self._store.set_result(job_id, result.final_path, result.encoded_size)
        if result.kept_path:
            self._store.set_retention(
                job_id, result.kept_path, time.time() + self._original_ttl
            )
        self._store.set_progress(job_id, 100.0)
        self._store.set_stage(job_id, "done")
        self._publish(job_id)

    def _handle_dispatch_error(self, job_id: str, exc: Exception) -> None:
        if isinstance(exc, EncoderRejected) and exc.code == "encoder_unavailable":
            # Never a silent CPU fallback: the user chooses. Blocked jobs are
            # excluded from restart recovery for the same reason.
            self._store.set_stage(job_id, "blocked", error=exc.reason,
                                  error_code=exc.code)
            self._publish(job_id)
            return

        if is_retryable(exc):
            delay = getattr(exc, "retry_after", None) or _REQUEUE_SECONDS
            logger.info("Encoder busy or unreachable; requeueing %s in %ss",
                        job_id, delay)
            self._store.set_stage(job_id, "queued")
            self._publish(job_id)
            threading.Timer(float(delay), self._queue.put, args=(job_id,)).start()
            return

        code = getattr(exc, "code", "dispatch_failed")
        self._fail(job_id, str(exc), code)

    def _fail(self, job_id: str, error: str, code: str) -> None:
        self._store.set_stage(job_id, "failed", error=error, error_code=code)
        self._publish(job_id)

    def _publish(self, job_id: str) -> None:
        job = self._store.get_job(job_id)
        if job is not None:
            self._events.publish(job_to_payload(job))
