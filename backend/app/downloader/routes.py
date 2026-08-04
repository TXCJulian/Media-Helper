import json
import logging
import os
import queue as queue_mod
import re
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from yt_dlp import version as yt_dlp_version

from app.config import (
    DOWNLOADER_DATA_DIR,
    DOWNLOADER_DB,
    DOWNLOADER_WORKERS,
    DOWNLOADS_DIR,
    YT_DLP_COOKIES,
)
from app.downloader.events import EventBroadcaster, job_to_payload
from app.downloader.queue import DownloadQueue
from app.downloader.runner import run_job
from app.downloader.store import Job, JobStore

logger = logging.getLogger(__name__)
router = APIRouter()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_HEARTBEAT_SECONDS = 15.0

_store: JobStore | None = None
_queue: DownloadQueue | None = None
_broadcaster: EventBroadcaster | None = None


def cookie_path() -> str:
    return YT_DLP_COOKIES or os.path.join(DOWNLOADER_DATA_DIR, "cookies.txt")


def get_broadcaster() -> EventBroadcaster:
    if _broadcaster is None:
        raise RuntimeError("Downloader is not initialised")
    return _broadcaster


def get_store() -> JobStore:
    if _store is None:
        raise RuntimeError("Downloader is not initialised")
    return _store


def get_queue() -> DownloadQueue:
    if _queue is None:
        raise RuntimeError("Downloader is not initialised")
    return _queue


def init_downloader() -> None:
    """Build the store, broadcaster, and worker pool. Called from lifespan."""
    global _store, _queue, _broadcaster

    # The test suite reloads app.main repeatedly, and a startup that raised
    # after assigning a global leaves this module half-initialised with no
    # shutdown having run. Tearing down whatever is there first keeps a second
    # init from orphaning worker threads and an open SQLite connection.
    if _store is not None or _queue is not None:
        logger.warning("Downloader was already initialised; tearing it down first")
        shutdown_downloader()

    os.makedirs(DOWNLOADER_DATA_DIR, exist_ok=True)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    broadcaster = EventBroadcaster()

    def publish(job: Job) -> None:
        # Only ever touches the broadcaster. This runs on a worker thread that
        # still holds DownloadQueue._lock, which is not reentrant, so calling
        # back into the queue here would deadlock that worker permanently.
        broadcaster.publish({"type": "job", "job": job_to_payload(job)})

    store = JobStore(DOWNLOADER_DB, on_change=publish)

    def runner(store_arg: JobStore, job: Job, cancel_event) -> None:
        run_job(store_arg, job, cancel_event, cookie_path())

    job_queue = DownloadQueue(store, runner, workers=DOWNLOADER_WORKERS)
    _broadcaster, _store, _queue = broadcaster, store, job_queue
    job_queue.start()
    # Exactly once, at startup, never concurrently: recover()'s ownership check
    # and its enqueue are not atomic, so overlapping calls could double-enqueue.
    job_queue.recover()


def shutdown_downloader() -> None:
    global _store, _queue, _broadcaster
    if _queue is not None:
        _queue.stop()
    if _store is not None:
        _store.close()
    _store = _queue = _broadcaster = None


def _require_valid_id(job_id: str) -> None:
    if not _UUID_RE.match(job_id):
        raise HTTPException(status_code=422, detail=f"Invalid job_id: {job_id}")


class CreateDownloadRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=200)
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("/download/status")
def download_status() -> dict[str, Any]:
    return {
        "yt_dlp_version": yt_dlp_version.__version__,
        "cookies_present": os.path.isfile(cookie_path()),
        "downloads_dir": DOWNLOADS_DIR,
        "queue_depth": get_queue().depth(),
        "workers": DOWNLOADER_WORKERS,
    }


@router.post("/download")
def create_downloads(request: CreateDownloadRequest) -> dict[str, list[str]]:
    for url in request.urls:
        if urlparse(url).scheme not in ("http", "https"):
            raise HTTPException(
                status_code=422, detail=f"Only http(s) URLs are supported: {url}"
            )

    auto_start = str(request.options.get("auto_start", True)).lower() not in (
        "false",
        "0",
        "no",
    )

    store, job_queue = get_store(), get_queue()
    job_ids = []
    for url in request.urls:
        job_id = store.create_job(url, request.options)
        job_ids.append(job_id)
        if auto_start:
            job_queue.enqueue(job_id)
    return {"job_ids": job_ids}


@router.get("/download/jobs")
def list_download_jobs() -> dict[str, list[dict[str, Any]]]:
    return {"jobs": [job_to_payload(job) for job in get_store().list_jobs()]}


@router.get("/download/jobs/{job_id}")
def get_download_job(job_id: str) -> dict[str, Any]:
    _require_valid_id(job_id)
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_to_payload(job)


@router.post("/download/jobs/{job_id}/start")
def start_download_job(job_id: str) -> dict[str, str]:
    _require_valid_id(job_id)
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.stage != "queued":
        raise HTTPException(status_code=409, detail="Job is not queued")
    get_queue().enqueue(job_id)
    return {"status": "started"}


@router.post("/download/jobs/{job_id}/cancel")
def cancel_download_job(job_id: str) -> dict[str, str]:
    _require_valid_id(job_id)
    if get_store().get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not get_queue().cancel(job_id):
        raise HTTPException(status_code=409, detail="Job is not active")
    return {"status": "cancelled"}


@router.delete("/download/jobs/{job_id}")
def delete_download_job(job_id: str) -> dict[str, str]:
    _require_valid_id(job_id)
    job_queue = get_queue()
    job_queue.cancel(job_id)
    # Wait for the worker to let go of the job. `is_active` only goes False
    # inside the same locked section that writes the job's final stage, so once
    # it is False no worker write to this job is still outstanding.
    for _ in range(50):
        if not job_queue.is_active(job_id):
            break
        time.sleep(0.1)
    else:
        # A download that will not stop within 5s (a wedged network read, say).
        # Deleting anyway is deliberate: the row goes, and the worker's later
        # writes hit a missing row and are discarded rather than resurrecting it.
        logger.warning("Job %s still active after cancel; deleting anyway", job_id)
    if not get_store().delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "deleted"}


@router.get("/download/jobs/{job_id}/items/{index}/file")
def download_item_file(job_id: str, index: int) -> FileResponse:
    _require_valid_id(job_id)
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    item = next((i for i in job.items if i.index == index), None)
    if item is None or item.stage != "done" or not item.path:
        raise HTTPException(status_code=404, detail="Output file not available")
    if not os.path.isfile(item.path):
        raise HTTPException(status_code=404, detail="Output file is gone")

    return FileResponse(
        item.path,
        filename=os.path.basename(item.path),
        media_type="application/octet-stream",
    )


@router.get("/download/events")
def download_events() -> StreamingResponse:
    broadcaster = get_broadcaster()
    store = get_store()

    def generate():
        subscription = broadcaster.subscribe()
        try:
            snapshot = {
                "type": "snapshot",
                "jobs": [job_to_payload(job) for job in store.list_jobs()],
            }
            yield f"data: {json.dumps(snapshot)}\n\n"
            while True:
                try:
                    event = subscription.get(timeout=_HEARTBEAT_SECONDS)
                except queue_mod.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            broadcaster.unsubscribe(subscription)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/download/cookies")
async def upload_cookies(file: UploadFile = File(...)) -> dict[str, str]:
    path = cookie_path()
    max_size = 1024 * 1024
    content = await file.read(max_size + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Cookie file is empty")
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="Cookie file is too large")

    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return {"status": "ok"}


@router.delete("/download/cookies")
def delete_cookies() -> dict[str, str]:
    path = cookie_path()
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "deleted"}
