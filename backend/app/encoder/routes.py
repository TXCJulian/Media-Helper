"""HTTP surface for the auto-encoder."""

import asyncio
import json
import logging
import os
import queue as queue_mod
import time
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import config
from app.encoder.client import EncoderClient
from app.encoder.events import EventBroadcaster, job_to_payload
from app.encoder.presets import PresetError, parse_document
from app.encoder.probe import ProbeError, probe
from app.encoder.queue import EncodeQueue
from app.encoder.rules import (
    _BOOL_FIELDS,
    SKIP,
    Condition,
    Rule,
    RuleError,
    evaluate,
)
from app.encoder.store import EncoderStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/encoder")

_HEARTBEAT_SECONDS = 15.0
_POLL_SECONDS = 0.25

_store: EncoderStore | None = None
_client: EncoderClient | None = None
_events: EventBroadcaster | None = None
_queue: EncodeQueue | None = None

# Set by `mark_startup_failed()` when main.py's lifespan catches an exception
# partway through building the store/queue/watcher. Without this, `get_store()`
# and `get_queue()` would just lazily rebuild on the next call -- handing back
# a queue with no worker thread reading it, so `approve` would report success
# ("queued") for a job nobody will ever dispatch. Once set, both fail loud
# instead, matching the downloader's `get_store()`/`get_queue()`, which raise
# unconditionally when `init_downloader()` was never called or was undone by
# `shutdown_downloader()`.
_startup_failed = False


def get_store() -> EncoderStore:
    global _store
    if _startup_failed:
        raise RuntimeError(
            "Encoder failed to start; check the server logs for the cause"
        )
    if _store is None:
        _store = EncoderStore(config.ENCODER_DB)
    return _store


def get_client() -> EncoderClient:
    global _client
    if _client is None:
        _client = EncoderClient(config.ENCODER_URL)
    return _client


def get_events() -> EventBroadcaster:
    global _events
    if _startup_failed:
        raise RuntimeError(
            "Encoder failed to start; check the server logs for the cause"
        )
    if _events is None:
        _events = EventBroadcaster()
    return _events


def get_queue() -> EncodeQueue:
    global _queue
    if _startup_failed:
        raise RuntimeError(
            "Encoder failed to start; check the server logs for the cause"
        )
    if _queue is None:
        _queue = EncodeQueue(
            get_store(),
            get_client(),
            get_events(),
            mode=config.ENCODER_MODE,
            original_ttl=config.ENCODER_ORIGINAL_TTL,
            holding_dir=f"{config.ENCODER_DATA_DIR}/originals",
        )
    return _queue


def mark_startup_failed() -> None:
    """Record a contained lifespan startup failure and discard live state.

    Called from `main.py`'s lifespan *after* it has already stopped whatever
    of the queue/watcher had managed to start. Resets the singletons (so
    nothing hands back a half-built or already-stopped object) and flips the
    guard `get_store()`/`get_queue()` check, so every subsequent call -- from
    a route handler or anywhere else -- fails loud instead of silently
    reconstructing (a fresh `EncodeQueue()` whose `.start()` nobody calls
    again is exactly as dead as the stopped one it would replace).
    """
    global _store, _queue, _events, _startup_failed
    if _store is not None:
        _store.close()
    _store = None
    _queue = None
    _events = None
    _startup_failed = True


def reset_state_for_tests() -> None:
    global _store, _events, _queue, _startup_failed
    if _store is not None:
        _store.close()
    _store = None
    _events = None
    _queue = None
    _startup_failed = False


class PresetImport(BaseModel):
    document: dict


class ConditionIn(BaseModel):
    field: str
    op: str
    value: Any


class RuleIn(BaseModel):
    id: str
    conditions: list[ConditionIn]
    target: str


class RulesIn(BaseModel):
    rules: list[RuleIn]
    fallback: str


class TestIn(BaseModel):
    path: str


def _error(status: int, code: str, reason: str) -> JSONResponse:
    """Build an error response with the code/reason at the top level.

    FastAPI's default HTTPException handler wraps ``detail`` under a
    ``"detail"`` key, which would bury ``code``/``reason`` a level deeper
    than every consumer of this API (including this module's own tests)
    expects. Returning the response directly, rather than raising an
    HTTPException, keeps the wire shape flat.
    """
    return JSONResponse(status_code=status, content={"code": code, "reason": reason})


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Reshape FastAPI's own validation-error response to match `_error()`.

    A malformed body (a missing required field, a wrong JSON type) never
    reaches `_error()` -- Pydantic rejects it before an endpoint function
    runs, and FastAPI's default handler for that returns
    ``{"detail": [...]}``. Every other error path in this module returns
    ``{"code", "reason"}`` at the top level, so leaving this one alone would
    give plan 2b's UI a different shape for this entire class of error.

    Registered globally (exception handlers can only be attached to a
    ``FastAPI`` app, not a bare ``APIRouter``), but scoped by path prefix so
    it only changes behaviour for the encoder's own routes -- any other
    feature's validation errors fall through to FastAPI's unmodified
    default handler.
    """
    path = request.url.path
    # Segment-bounded, not a bare prefix check: `path == router.prefix`
    # covers the (currently unused) bare "/api/encoder" path itself, and
    # `path.startswith(router.prefix + "/")` requires the next character
    # after the prefix to be a path separator. Without the separator, a
    # future route like "/api/encoderXYZ" would have its validation errors
    # silently reshaped into this envelope too.
    if not (path == router.prefix or path.startswith(router.prefix + "/")):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    reason = "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', ()) if p != 'body')}: {e.get('msg', '')}"
        for e in errors
    ) or "Invalid request body"
    return JSONResponse(
        status_code=422, content={"code": "invalid_request", "reason": reason}
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach the encoder's validation-error reshaping to *app*.

    Must be called explicitly (from `main.py`'s app setup, and from this
    module's own test fixtures) because FastAPI has no mechanism to carry an
    `APIRouter`'s exception handlers along when it is `include_router()`-ed.
    """
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)


def _vendor(encoders: list[str]) -> str:
    """Which hardware family this encoder can actually use.

    Derived rather than read from the service: /health has no gpu_name, and
    what the machine can encode with is a more useful thing to show than a
    device string anyway.
    """
    for prefix, label in (("nvenc_", "NVENC"), ("qsv_", "QSV"), ("vce_", "VCE")):
        if any(e.startswith(prefix) for e in encoders):
            return label
    return "CPU"


def _validate_condition_value(condition: ConditionIn) -> None:
    """Reject a condition whose value/operator cannot mean what it claims to.

    `rules.py`'s evaluation semantics compare booleans with
    ``bool(condition.value)`` -- and ``bool("false")`` is ``True`` in Python.
    A client sending the JSON *string* ``"false"`` for a bool field would
    silently invert the rule. Likewise `contains` is documented as
    text-fields-only, but `rules.py` only guards it against numeric fields,
    not bool ones. Both are cheap to catch here, at save time, before a rule
    is stored and silently misbehaves in production.
    """
    if condition.field not in _BOOL_FIELDS:
        return
    if condition.op == "contains":
        raise RuleError(
            f"Operator 'contains' applies to text fields only, not "
            f"{condition.field!r}"
        )
    if not isinstance(condition.value, bool):
        raise RuleError(
            f"Field {condition.field!r} is a true/false field; got "
            f"{condition.value!r} of type {type(condition.value).__name__}"
        )


@router.get("/health")
def encoder_health() -> dict:
    body = get_client().health()
    body["vendor"] = _vendor(body.get("encoders") or [])
    return body


@router.get("/config")
def encoder_config() -> dict:
    return {
        "watch_paths": config.ENCODER_WATCH_PATHS,
        "mode": config.ENCODER_MODE,
        "settle_seconds": config.ENCODER_SETTLE_SECONDS,
        "original_ttl": config.ENCODER_ORIGINAL_TTL,
        "job_ttl": config.ENCODER_JOB_TTL,
    }


@router.get("/presets")
def list_presets() -> list[dict]:
    return [
        {"name": p.name, "encoder": p.encoder, "video_preset": p.video_preset,
         "file_format": p.file_format}
        for p in get_store().list_presets()
    ]


@router.post("/presets", response_model=None)
def import_presets(payload: PresetImport) -> dict | JSONResponse:
    try:
        presets = parse_document(payload.document)
    except PresetError as exc:
        return _error(400, "invalid_preset", str(exc))
    if not presets:
        return _error(400, "invalid_preset", "The document contains no presets")

    # Reject at upload rather than at dispatch: an unusable preset should never
    # enter the system, so the failure arrives while the user is looking at the
    # upload rather than an hour into an encode that cannot run.
    available = set(get_client().health().get("encoders") or [])
    missing = sorted({p.encoder for p in presets} - available)
    if missing:
        return _error(
            400,
            "encoder_unavailable",
            f"The connected encoder does not provide: {', '.join(missing)}",
        )

    get_store().replace_presets(presets)
    return {"imported": [p.name for p in presets]}


@router.delete("/presets/{name}", status_code=204, response_model=None)
def delete_preset(name: str):
    if not get_store().delete_preset(name):
        return _error(404, "preset_not_found", f"No preset named {name!r}")


@router.get("/rules")
def list_rules() -> dict:
    store = get_store()
    return {
        "rules": [
            {"id": r.id,
             "conditions": [{"field": c.field, "op": c.op, "value": c.value}
                            for c in r.conditions],
             "target": r.target}
            for r in store.list_rules()
        ],
        "fallback": store.get_setting("fallback_target", SKIP),
    }


@router.put("/rules", response_model=None)
def replace_rules(payload: RulesIn) -> dict | JSONResponse:
    store = get_store()
    known = {p.name for p in store.list_presets()} | {SKIP}

    # Reject bool-field conditions whose value/operator don't mean what they
    # claim (carry-overs A and B) before anything else touches them.
    for rule_in in payload.rules:
        for condition_in in rule_in.conditions:
            try:
                _validate_condition_value(condition_in)
            except RuleError as exc:
                return _error(400, "invalid_rule", str(exc))

    rules = [
        Rule(id=r.id,
             conditions=[Condition(c.field, c.op, c.value) for c in r.conditions],
             target=r.target)
        for r in payload.rules
    ]
    for rule in [*rules]:
        if rule.target not in known:
            return _error(400, "unknown_target",
                          f"Rule {rule.id!r} targets {rule.target!r}, which is "
                          "not a stored preset")
    if payload.fallback not in known:
        return _error(400, "unknown_target",
                      f"Fallback targets {payload.fallback!r}, which is not a "
                      "stored preset")

    # Validate fields and operators now, against a probe-shaped sample. A typo
    # would otherwise be a rule that silently never fires.
    # Every field must be present, not just named: `_holds` returns False for a
    # missing fact before it can reject, say, `contains` on a numeric field.
    sample = {"height": 0, "width": 0, "size": 0, "bit_rate": 0, "bit_depth": 0,
              "frame_rate": 0.0, "duration": 0.0, "video_codec": "", "profile": "",
              "hdr": False, "dolby_vision": False, "source_tool": "", "encoder_tag": ""}
    try:
        evaluate(sample, rules, payload.fallback)
    except RuleError as exc:
        return _error(400, "invalid_rule", str(exc))

    store.replace_rules(rules)
    store.set_setting("fallback_target", payload.fallback)
    return {"saved": len(rules)}


def _resolve_watch_path(path: str) -> str | None:
    """Resolve *path* against the watch-path allow-list, or None if it escapes.

    Mirrors `resolve_cutter_path`/`validate_path` in main.py: realpath first
    so a symlink cannot point outside the configured roots, then require
    containment under one of them. Kept as a plain absolute-path check --
    rather than adopting the cutter's `{path, base}` + label shape -- because
    `ENCODER_WATCH_PATHS` has no label system the way `BASE_PATH_LABELS`
    does; every entry is already a full in-container path.
    """
    resolved = os.path.realpath(path)
    roots = [os.path.realpath(p) for p in config.ENCODER_WATCH_PATHS]
    for root in roots:
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    return None


@router.post("/test", response_model=None)
def test_against_file(payload: TestIn) -> dict | JSONResponse:
    """Probe a candidate file against the stored rules, without encoding it.

    Unauthenticated by default (auth is off unless AUTH_USERNAME/PASSWORD are
    set) and ffprobe resolves protocols, not just paths -- so without this
    containment check this endpoint would let any caller who can reach the
    API make the server issue outbound requests (`{"path":
    "http://internal-host/..."}` ) or use the probe-failure message as a
    file-existence/permission oracle for arbitrary paths on the container.
    Requiring the resolved path to be a real file under a configured watch
    root closes both.
    """
    store = get_store()
    resolved = _resolve_watch_path(payload.path)
    if resolved is None:
        return _error(400, "invalid_path",
                      "Path is not within a configured watch root")
    if not os.path.isfile(resolved):
        return _error(400, "invalid_path", "No such file")
    try:
        facts = probe(resolved)
    except ProbeError as exc:
        return _error(400, "probe_failed", str(exc))
    rules = store.list_rules()
    fallback = store.get_setting("fallback_target", SKIP)
    try:
        match = evaluate(facts, rules, fallback)
    except RuleError as exc:
        return _error(400, "invalid_rule", str(exc))
    return {
        "facts": facts,
        "matched_rule": match.rule_id,
        "target": match.target,
        "evaluated": match.evaluated,
        "not_evaluated": [r.id for r in rules if r.id not in match.evaluated],
    }


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return [job_to_payload(j) for j in get_store().list_jobs()]


@router.post("/jobs/{job_id}/approve", response_model=None)
def approve_job(job_id: str):
    store = get_store()
    job = store.get_job(job_id)
    if job is None:
        return _error(404, "job_not_found", f"No job {job_id!r}")
    if job.stage not in {"pending", "blocked"}:
        return _error(409, "not_awaiting_approval",
                      f"Job is {job.stage}, not awaiting approval")
    if job.error_code == "swap_interrupted":
        # This job carries a remote_job_id pointing at a remote encode that
        # is already terminal (its swap was interrupted by a restart, per
        # queue.py's recover()). EncodeQueue._run() routes any job with a
        # remote_job_id through _reattach rather than a fresh submit, which
        # here would poll that finished remote job and could re-run
        # `_publish_result` against a source that may already be the encoded
        # file. The job's own error message already tells the user to
        # confirm the file's state manually; approving here would bypass
        # that. Deleting the job (which does not touch the file) lets the
        # watcher pick the source up fresh once it has been checked.
        return _error(
            409,
            "swap_interrupted",
            "This job's swap was interrupted by a restart and its on-disk "
            "state is unknown. Confirm the file manually, then delete this "
            "job to let the watcher re-plan it.",
        )
    get_queue().enqueue(job_id)
    return {"stage": "queued"}


@router.delete("/jobs/{job_id}", status_code=204, response_model=None)
def delete_job(job_id: str):
    store = get_store()
    if store.get_job(job_id) is None:
        return _error(404, "job_not_found", f"No job {job_id!r}")
    get_queue().cancel(job_id)
    store.delete_job(job_id)


@router.get("/events")
async def events() -> StreamingResponse:
    broadcaster = get_events()
    # Resolved *before* the StreamingResponse is constructed -- once that
    # response starts, the 200 status and SSE headers are already committed,
    # so a store lookup that fails inside the generator would surface as a
    # stream that dies mid-body instead of a normal error status. Calling it
    # here lets a contained startup failure (get_store() raising) become the
    # ordinary 500 every other guarded route already gives.
    store = get_store()
    subscription = broadcaster.subscribe()

    async def stream():
        last_beat = time.monotonic()
        try:
            for job in store.list_jobs():
                yield f"data: {json.dumps(job_to_payload(job))}\n\n"
            while True:
                try:
                    event = subscription.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                    continue
                except queue_mod.Empty:
                    pass
                if time.monotonic() - last_beat >= _HEARTBEAT_SECONDS:
                    last_beat = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(_POLL_SECONDS)
        finally:
            broadcaster.unsubscribe(subscription)

    return StreamingResponse(stream(), media_type="text/event-stream")
