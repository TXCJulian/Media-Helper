import logging
import os
import re
import threading
from typing import Any

from yt_dlp import YoutubeDL

from app.downloader.store import Job, JobStore
from app.downloader.transcode import TranscodeCancelled, transcode_file
from app.downloader.ydl import (
    assert_within_allowed_roots,
    build_ydl_opts,
    needs_transcode,
    resolve_output_root,
    unique_path,
)

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FORMAT_ID_RE = re.compile(r"\.f\d+(?=\.[^.]+$)")

_LOGIN_HINTS = (
    "sign in",
    "login required",
    "private video",
    "members-only",
    "age-restricted",
    "confirm your age",
)

# Message recorded on an item whose destination file already existed before
# this run, so yt-dlp's overwrites=False silently skipped downloading it
# (only a "finished" hook event fires for such a file, never "downloading").
_ALREADY_EXISTS_MESSAGE = "File already existed and was not re-downloaded."


class DownloadCancelled(RuntimeError):
    """Raised when a running download job is cancelled."""


def _ydl_factory(opts: dict[str, Any]):
    """Indirection point so tests can substitute a fake yt-dlp."""
    return YoutubeDL(opts)


def clean_error(text: str) -> str:
    return _ANSI_RE.sub("", str(text)).strip()


def _friendly_error(exc: Exception) -> str:
    message = clean_error(str(exc))
    lowered = message.lower()
    if any(hint in lowered for hint in _LOGIN_HINTS):
        return f"{message} — this URL needs cookies; upload a cookies.txt file."
    if "ffmpeg" in lowered and "not found" in lowered:
        return "ffmpeg is not available on the server."
    if "no space left" in lowered:
        return "The destination disk is full."
    if "requested format" in lowered or "no video formats" in lowered:
        return f"{message} — try a different quality setting."
    return message or "Download failed"


def _display_name(path: Any) -> str:
    if not path:
        return ""
    return _FORMAT_ID_RE.sub("", os.path.basename(str(path)))


def _entry_path(entry: dict[str, Any]) -> str | None:
    candidate = entry.get("filepath") or entry.get("_filename")
    if candidate and os.path.isfile(str(candidate)):
        return str(candidate)
    for download in entry.get("requested_downloads") or []:
        candidate = (download or {}).get("filepath") or (download or {}).get(
            "_filename"
        )
        if candidate and os.path.isfile(str(candidate)):
            return str(candidate)
    return None


def _entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise a yt-dlp info dict to a flat list of downloaded entries."""
    raw = info.get("entries")
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return [info] if info else []


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def run_job(
    store: JobStore,
    job: Job,
    cancel_event: threading.Event,
    cookie_path: str | None = None,
) -> None:
    """Run one job to completion. Never raises; terminal state lands in the store."""
    try:
        store.set_job_stage(job.id, "downloading")
        output_root = resolve_output_root(job.options)
        os.makedirs(output_root, exist_ok=True)
        opts = build_ydl_opts(job.options, output_root, cookie_path)
        # Realpaths that received at least one "downloading" hook event this
        # run, i.e. were genuinely fetched rather than skipped because the
        # destination already existed (see overwrites=False in build_ydl_opts
        # and _ALREADY_EXISTS_MESSAGE below). Keyed by path rather than the
        # hook's playlist_index: that index and the final entries-list
        # position only coincide in the simple, non-gappy case (dropped
        # unavailable entries, playliststart offsets, or extractor
        # renumbering all break that assumption), whereas the path is
        # unambiguous.
        downloaded_paths: set[str] = set()
        opts["progress_hooks"].append(
            _make_hook(store, job.id, cancel_event, downloaded_paths)
        )

        # A failing entry (e.g. one unavailable track in a playlist) makes
        # extract_info raise before it ever returns, so the entries loop
        # below never runs at all. Items that already completed via the
        # hook's "finished" branch were already written durably to the
        # store (path/size/stage="done") the moment they finished, so an
        # exception here does not lose them - it only sets the job-level
        # error via the outer except below.
        with _ydl_factory(opts) as ydl:
            info = ydl.extract_info(job.url, download=True)

        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")

        entries = _entries(info if isinstance(info, dict) else {})
        if not entries:
            raise RuntimeError("Download produced no output")

        # Reconcile with whatever the hook already wrote durably during the
        # run, rather than assuming this loop is the sole owner of each
        # item: an entry the hook already completed keeps its recorded
        # path/stage, and this loop only backfills a title if one is
        # missing. Entries the hook never saw a "finished" event for (no
        # hook events at all in some test doubles, or a hook event that
        # arrived under a different index - see downloaded_paths above) are
        # still recorded here from the entry's own data.
        current_items = {item.index: item for item in store.get_job(job.id).items}
        recorded = 0
        for index, entry in enumerate(entries):
            path = _entry_path(entry)
            title = str(entry.get("title") or _display_name(path) or f"Item {index + 1}")
            already_done = current_items.get(index)
            if already_done is not None and already_done.stage == "done" and already_done.path:
                if title and not already_done.title:
                    store.upsert_item(job.id, index, title=title)
                recorded += 1
                continue
            if path is None:
                store.upsert_item(
                    job.id,
                    index,
                    title=title,
                    stage="error",
                    error="Output file could not be located",
                )
                continue
            assert_within_allowed_roots(path)
            item_fields: dict[str, Any] = dict(
                title=title,
                path=path,
                size=_file_size(path),
                progress=100.0,
                stage="done",
            )
            if os.path.realpath(path) not in downloaded_paths:
                # No "downloading" event was ever seen for this path, yet a
                # file exists at the destination: yt-dlp's overwrites=False
                # skipped it because it was already there. Record it as done
                # (the file is present and usable) but don't claim it was
                # freshly downloaded.
                item_fields["error"] = _ALREADY_EXISTS_MESSAGE
            store.upsert_item(job.id, index, **item_fields)
            recorded += 1

        if recorded == 0:
            store.set_job_stage(job.id, "error", "No output files were produced")
            return

        if needs_transcode(job.options):
            _run_transcode_stage(store, job, cancel_event)

        store.set_job_stage(job.id, "done")

    except (DownloadCancelled, TranscodeCancelled):
        store.set_job_stage(job.id, "cancelled", "Cancelled by user")
    except Exception as exc:
        logger.error("Download job %s failed: %s", job.id, exc, exc_info=True)
        store.set_job_stage(job.id, "error", _friendly_error(exc))


def _make_hook(
    store: JobStore,
    job_id: str,
    cancel_event: threading.Event,
    downloaded_paths: set[str],
):
    def hook(data: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")
        status = str(data.get("status") or "")
        # This index is only used to pick which store row this particular
        # hook call updates in real time; it is not relied on to decide
        # whether the file was pre-existing (see downloaded_paths, keyed by
        # path instead - playlist_index and the final entries-list position
        # are not guaranteed to agree).
        index = int(data.get("playlist_index") or 1) - 1
        index = max(index, 0)
        filename = data.get("filename")
        if status == "downloading":
            if filename:
                downloaded_paths.add(os.path.realpath(str(filename)))
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            progress = (downloaded / total * 100.0) if total else 0.0
            store.upsert_item(
                job_id,
                index,
                title=_display_name(filename),
                progress=min(progress, 100.0),
                stage="downloading",
            )
        elif status == "finished":
            path = str(filename) if filename and os.path.isfile(str(filename)) else None
            if path is None:
                # No usable path yet (e.g. a test double that only sends a
                # bare "finished" event); leave final recording to the
                # post-extract_info reconciliation loop, which has the
                # entry's own data to fall back on.
                store.upsert_item(
                    job_id, index, title=_display_name(filename), progress=100.0
                )
                return
            assert_within_allowed_roots(path)
            fields: dict[str, Any] = dict(
                title=_display_name(path),
                path=path,
                size=_file_size(path),
                progress=100.0,
                stage="done",
            )
            if os.path.realpath(path) not in downloaded_paths:
                fields["error"] = _ALREADY_EXISTS_MESSAGE
            # Record durably now: if a later playlist entry fails,
            # extract_info raises before the post-loop below ever runs, and
            # this write is what keeps this item's result from being lost.
            store.upsert_item(job_id, index, **fields)

    return hook


def _run_transcode_stage(
    store: JobStore, job: Job, cancel_event: threading.Event
) -> None:
    codec = str(job.options.get("codec") or "").lower()
    container = str(job.options.get("format") or "").lower()
    store.set_job_stage(job.id, "transcoding")

    for item in store.get_job(job.id).items:
        if item.stage != "done" or not item.path:
            continue

        extension = container or os.path.splitext(item.path)[1].lstrip(".")
        stem = os.path.splitext(item.path)[0]
        destination = unique_path(f"{stem}.{extension}")
        if os.path.realpath(destination) == os.path.realpath(item.path):
            destination = unique_path(f"{stem}.transcoded.{extension}")
        assert_within_allowed_roots(destination)

        store.upsert_item(job.id, item.index, stage="transcoding", progress=0.0)

        def report(percent: float, index: int = item.index) -> None:
            store.upsert_item(job.id, index, progress=percent)

        transcode_file(item.path, destination, codec, cancel_event, report)

        try:
            os.remove(item.path)
        except OSError:
            logger.warning("Could not remove pre-transcode source %s", item.path)

        store.upsert_item(
            job.id,
            item.index,
            path=destination,
            size=_file_size(destination),
            progress=100.0,
            stage="done",
            # Clear any stale "already existed" note from the download
            # stage: this run genuinely produced `destination` via ffmpeg,
            # so that message would no longer describe the current file.
            error=None,
        )
