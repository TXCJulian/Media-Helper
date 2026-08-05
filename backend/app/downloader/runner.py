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

# Message recorded on an item whose source file was pre-existing (see above)
# and which was then re-encoded. The re-encode is written alongside the
# original rather than replacing it, because destroying a file this run did
# not create would be data loss.
_KEPT_SOURCE_MESSAGE = (
    "The pre-existing original was kept; the re-encode was saved alongside it."
)

# The UI's Format control sends this sentinel for "let the source decide"; it
# is both the default and the explicit "Auto" option. It is not a container
# name, so it must be reduced to "" before it can reach a filename extension.
_AUTO = "auto"


class DownloadCancelled(RuntimeError):
    """Raised when a running download job is cancelled."""


class _IndexAllocator:
    """Assigns store row indices from a single keyspace, keyed by resolved
    output path.

    Both the progress hook and the post-download reconciliation loop share
    one instance per run, so a given file always lands in the same store
    row regardless of which of them writes it first. This replaces an
    earlier design where the hook derived its index from yt-dlp's
    `playlist_index` and the reconciliation loop derived its index from
    `enumerate(entries)` position: those two index spaces only coincide in
    the simple, non-gappy case, and disagreeing between them produced
    phantom/duplicate item rows for the same underlying file.

    `for_path` and `new_index` are deliberately the only two ways to obtain
    an index. In particular there is no "most recently allocated index"
    accessor: an event that carries no path has no identity, and attributing
    it to the last-allocated row is wrong, because allocation only happens
    once a path is known - so while a *new* item is still resolving its
    destination, the last-allocated row still belongs to the *previous*,
    already-finished item. Writing to it there corrupts a completed sibling.
    Such events are dropped instead (see `_make_hook`).
    """

    def __init__(self) -> None:
        self._by_path: dict[str, int] = {}
        self._next = 0

    def for_path(self, path: str) -> int:
        """Return the row index for `path`, allocating one on first sight."""
        real = os.path.realpath(path)
        index = self._by_path.get(real)
        if index is None:
            index = self._next
            self._next += 1
            self._by_path[real] = index
        return index

    def new_index(self) -> int:
        """Allocate a row index not tied to any known path (e.g. an entry
        whose output file could not be located at all)."""
        index = self._next
        self._next += 1
        return index


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
    # Thumbnail-only jobs run with `skip_download`, and in that mode yt-dlp
    # sets `info_dict['filepath']` to the *media* temp filename it never
    # writes (YoutubeDL.process_info), so the checks above find nothing. The
    # file it did write is recorded per-thumbnail as
    # `info['thumbnails'][n]['filepath']` by `YoutubeDL._write_thumbnails`;
    # `FFmpegThumbnailsConvertorPP` rewrites that same key in place when a
    # target format was requested. `_write_thumbnails` walks the list back to
    # front and stops at the first one it writes, so scanning in reverse
    # finds the written entry first.
    for thumbnail in reversed(entry.get("thumbnails") or []):
        candidate = (thumbnail or {}).get("filepath")
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
        # Single source of truth for both "which store row is this file"
        # and "was this path genuinely fetched this run". One allocator
        # instance is shared by the hook and the reconciliation loop below,
        # so a given output file always lands in exactly one row no matter
        # which of them writes it first.
        allocator = _IndexAllocator()
        # Realpaths that received at least one "downloading" hook event this
        # run, i.e. were genuinely fetched rather than skipped because the
        # destination already existed (see overwrites=False in
        # build_ydl_opts and _ALREADY_EXISTS_MESSAGE below).
        downloaded_paths: set[str] = set()
        # ...but that inference only holds when a file downloader runs at all.
        # `skip_download` (thumbnail jobs) bypasses it entirely, so no progress
        # hook ever fires and "never saw a downloading event" stops carrying
        # any information. Flagging every thumbnail as a skipped pre-existing
        # file would be a plain lie, now visible to the user in the item row.
        track_pre_existing = not opts.get("skip_download")
        opts["progress_hooks"].append(
            _make_hook(
                store,
                job.id,
                cancel_event,
                downloaded_paths,
                allocator,
                track_pre_existing,
            )
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
        # run: each entry's row index comes from the same path-keyed
        # allocator the hook used, so an entry the hook already completed
        # is simply re-written with the (equivalent or better) data
        # available here - never a different row. Entries the hook never
        # saw a "finished" event for (no hook events at all in some test
        # doubles) get their row allocated here for the first time.
        recorded = 0
        for entry in entries:
            path = _entry_path(entry)
            if path is None:
                index = allocator.new_index()
                title = str(entry.get("title") or f"Item {index + 1}")
                store.upsert_item(
                    job.id,
                    index,
                    title=title,
                    stage="error",
                    error="Output file could not be located",
                )
                continue
            assert_within_allowed_roots(path)
            index = allocator.for_path(path)
            title = str(entry.get("title") or _display_name(path) or f"Item {index + 1}")
            item_fields: dict[str, Any] = dict(
                title=title,
                path=path,
                size=_file_size(path),
                progress=100.0,
                stage="done",
            )
            if track_pre_existing and os.path.realpath(path) not in downloaded_paths:
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
    allocator: "_IndexAllocator",
    track_pre_existing: bool = True,
):
    def hook(data: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")
        status = str(data.get("status") or "")
        filename = data.get("filename")
        if status == "downloading":
            if not filename:
                # A percentage with no file attached identifies nothing. The
                # allocator only knows a row once a path is known, so the
                # most recently allocated row belongs to the *previous*,
                # already-finished item whenever a new item is still
                # resolving its destination - writing here would stamp this
                # item's fractional progress onto a completed sibling
                # (upsert_item is a partial update, so that row would keep
                # stage="done" while its progress rewound). Real yt-dlp
                # always supplies `filename` on progress events, so this is
                # not a path production ever takes; drop the event.
                return
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            progress = min((downloaded / total * 100.0) if total else 0.0, 100.0)
            downloaded_paths.add(os.path.realpath(str(filename)))
            index = allocator.for_path(str(filename))
            store.upsert_item(
                job_id,
                index,
                title=_display_name(filename),
                progress=progress,
                stage="downloading",
            )
        elif status == "finished":
            path = str(filename) if filename and os.path.isfile(str(filename)) else None
            if path is None:
                # Same reasoning: a "finished" event with no usable path
                # (missing filename, or one that never landed on disk)
                # identifies no file and must not be written to any row. The
                # post-extract_info reconciliation loop below has
                # entry-derived identity and already covers entries that
                # produced no usable hook event.
                return
            assert_within_allowed_roots(path)
            index = allocator.for_path(path)
            fields: dict[str, Any] = dict(
                title=_display_name(path),
                path=path,
                size=_file_size(path),
                progress=100.0,
                stage="done",
            )
            if track_pre_existing and os.path.realpath(path) not in downloaded_paths:
                fields["error"] = _ALREADY_EXISTS_MESSAGE
            # Record durably now: if a later playlist entry fails,
            # extract_info raises before the post-loop below ever runs, and
            # this write is what keeps this item's result from being lost.
            store.upsert_item(job_id, index, **fields)

    return hook


def _remove_quietly(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.warning("Could not remove %s", path)


def _finalise_transcode(
    source: str, scratch: str, stem: str, extension: str, keep_source: bool
) -> str:
    """Move a finished encode from its scratch name to its real one.

    Returns the path the output ended up at. Never overwrites an existing
    file, and never removes `source` when `keep_source` is set.
    """
    if not keep_source:
        # Freeing the source's name first is what lets a same-container
        # re-encode land on the plain `Title.mp4` rather than being pushed to
        # `Title (1).mp4` by a collision with the very file it replaces.
        try:
            os.remove(source)
        except OSError:
            logger.warning("Could not remove pre-transcode source %s", source)

    destination = f"{stem}.{extension}"
    if os.path.exists(source) and os.path.realpath(destination) == os.path.realpath(
        source
    ):
        # Same container, and the source is still on disk - either kept
        # deliberately (it pre-dated this run) or its removal failed. Either
        # way it must not be clobbered, so the encode gets its own name.
        destination = unique_path(f"{stem}.transcoded.{extension}")
    else:
        # Any other pre-existing file at this name belongs to somebody else.
        destination = unique_path(destination)

    assert_within_allowed_roots(destination)
    os.replace(scratch, destination)
    return destination


def _run_transcode_stage(
    store: JobStore, job: Job, cancel_event: threading.Event
) -> None:
    codec = str(job.options.get("codec") or "").lower()
    container = str(job.options.get("format") or "").lower()
    if container == _AUTO:
        # Mirrors `needs_transcode`, which already collapses codec "auto" and
        # "" to the same thing. Left as "auto" this becomes the destination's
        # file extension, ffmpeg cannot infer a muxer from it, and every
        # re-encode job fails - including the common case of picking a codec
        # in Advanced without touching the Format control at all.
        container = ""
    store.set_job_stage(job.id, "transcoding")

    current = store.get_job(job.id)
    if current is None:
        # Deleted while the download stage was running; nothing to transcode
        # and nowhere to record a result.
        return

    for item in current.items:
        if item.stage != "done" or not item.path:
            continue

        source = item.path
        # An item the download stage flagged as pre-existing was on disk
        # before this run and was never fetched. Removing it after the encode
        # would silently destroy a file the user already had - exactly what
        # the spec's "an existing file is never silently destroyed" forbids.
        keep_source = item.error == _ALREADY_EXISTS_MESSAGE
        stem, source_ext = os.path.splitext(source)
        extension = container or source_ext.lstrip(".")

        # Encode to a scratch name first. Choosing the final name up front
        # cannot work: the natural destination `{stem}.{extension}` *is* the
        # source for a same-container re-encode, so `unique_path` would divert
        # to `Title (1).ext` every single time, having collided with nothing
        # but the file about to be replaced.
        scratch = unique_path(f"{stem}.transcoding.{extension}")
        assert_within_allowed_roots(scratch)

        store.upsert_item(job.id, item.index, stage="transcoding", progress=0.0)

        def report(percent: float, index: int = item.index) -> None:
            store.upsert_item(job.id, index, progress=percent)

        try:
            transcode_file(source, scratch, codec, cancel_event, report)
            destination = _finalise_transcode(
                source, scratch, stem, extension, keep_source
            )
        except Exception:
            # `transcode_file` already unlinks its own output on failure and
            # on cancellation; this also covers a failure in the rename step,
            # so a scratch file never outlives the stage that created it.
            _remove_quietly(scratch)
            raise

        store.upsert_item(
            job.id,
            item.index,
            path=destination,
            size=_file_size(destination),
            progress=100.0,
            stage="done",
            # The download stage's "already existed" note no longer describes
            # the current file, but the fact that the original was preserved
            # beside it does, and the user needs to know it is still there.
            error=_KEPT_SOURCE_MESSAGE if keep_source else None,
        )
