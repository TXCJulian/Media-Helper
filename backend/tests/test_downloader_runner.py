import os
import threading
from unittest.mock import patch

import pytest

from app.downloader import runner
from app.downloader.store import JobStore
from app.downloader.transcode import TranscodeCancelled


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "d.db"))


class FakeYDL:
    """Stands in for yt-dlp: drives the registered hooks, then returns info."""

    def __init__(self, opts, events, info):
        self._opts = opts
        self._events = events
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fire(self, event):
        for hook in self._opts["progress_hooks"]:
            hook(event)

    def extract_info(self, url, download=True):
        for event in self._events:
            self.fire(event)
        return self._info


def _install_fake_ydl(monkeypatch, events, info):
    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: FakeYDL(opts, events, info)
    )


def test_single_video_job_reaches_done(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    media = out / "Video.mp4"
    media.write_bytes(b"x" * 2048)

    _install_fake_ydl(
        monkeypatch,
        events=[
            # `filename` is present on real yt-dlp progress events (see the
            # progress_hooks docstring in YoutubeDL.py, and
            # FileDownloader.download which supplies it); the brief's original
            # double omitted it, which exercised a path-less branch that
            # production never takes and that is now deliberately dropped.
            {
                "status": "downloading",
                "filename": str(media),
                "downloaded_bytes": 1024,
                "total_bytes": 2048,
            },
            {"status": "finished", "filename": str(media)},
        ],
        info={"title": "Video", "requested_downloads": [{"filepath": str(media)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert len(job.items) == 1
    assert job.items[0].path == str(media)
    assert job.items[0].size == 2048
    assert job.items[0].progress == 100.0


def test_playlist_creates_one_item_per_entry(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    paths = []
    for name in ("A.mp3", "B.mp3", "C.mp3"):
        p = out / name
        p.write_bytes(b"x" * 10)
        paths.append(str(p))

    _install_fake_ydl(
        monkeypatch,
        events=[],
        info={
            "entries": [
                {"title": "A", "requested_downloads": [{"filepath": paths[0]}]},
                {"title": "B", "requested_downloads": [{"filepath": paths[1]}]},
                {"title": "C", "requested_downloads": [{"filepath": paths[2]}]},
            ]
        },
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert [i.title for i in job.items] == ["A", "B", "C"]
    assert [i.path for i in job.items] == paths
    assert all(i.stage == "done" for i in job.items)


def test_cancel_during_download_marks_cancelled(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    cancel = threading.Event()

    def cancelling_hook_events():
        cancel.set()
        return [{"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100}]

    _install_fake_ydl(monkeypatch, events=cancelling_hook_events(), info={})
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), cancel)

    job = store.get_job(job_id)
    assert job.stage == "cancelled"
    assert job.error == "Cancelled by user"


def test_extractor_failure_is_recorded_as_error(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()

    class BoomYDL(FakeYDL):
        def extract_info(self, url, download=True):
            raise RuntimeError("\x1b[31mERROR: Unsupported URL\x1b[0m")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: BoomYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))

    job_id = store.create_job("https://example.com/nope", {})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert job.error == "ERROR: Unsupported URL"


def test_login_required_error_suggests_cookies(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()

    class LoginYDL(FakeYDL):
        def extract_info(self, url, download=True):
            raise RuntimeError("Sign in to confirm your age")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: LoginYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))

    job_id = store.create_job("https://example.com/gated", {})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    assert "cookies" in store.get_job(job_id).error.lower()


def test_transcode_stage_runs_and_replaces_path(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"x" * 100)

    _install_fake_ydl(
        monkeypatch,
        # A genuine fetch: without a "downloading" event this file reads as
        # pre-existing, and a pre-existing source is deliberately preserved
        # rather than replaced (see the data-loss test further down).
        events=[
            {
                "status": "downloading",
                "filename": str(source),
                "downloaded_bytes": 50,
                "total_bytes": 100,
            },
            {"status": "finished", "filename": str(source)},
        ],
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    def fake_transcode(src, dst, codec, cancel_event, on_progress):
        on_progress(50.0)
        with open(dst, "wb") as f:
            f.write(b"y" * 50)

    monkeypatch.setattr(runner, "transcode_file", fake_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h265", "format": "mkv"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert job.items[0].path.endswith(".mkv")
    assert os.path.isfile(job.items[0].path)
    assert not os.path.isfile(str(source)), "source should be replaced after transcode"


def test_clean_error_strips_ansi():
    assert runner.clean_error("\x1b[0;31mboom\x1b[0m") == "boom"


def test_genuinely_downloaded_item_has_no_skip_error(store, tmp_path, monkeypatch):
    """An item that received a 'downloading' progress event was actually
    fetched this run, so it must not carry the pre-existing-file warning."""
    out = tmp_path / "downloads"
    out.mkdir()
    media = out / "Video.mp4"
    media.write_bytes(b"x" * 2048)

    _install_fake_ydl(
        monkeypatch,
        events=[
            # Real yt-dlp always includes "filename" on "downloading" events
            # (see progress_hooks docstring in YoutubeDL.py); include it here
            # so path-based "already existed" detection has something to key on.
            {
                "status": "downloading",
                "filename": str(media),
                "downloaded_bytes": 1024,
                "total_bytes": 2048,
            },
            {"status": "finished", "filename": str(media)},
        ],
        info={"title": "Video", "requested_downloads": [{"filepath": str(media)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert job.items[0].stage == "done"
    assert job.items[0].error is None


def test_pre_existing_file_skipped_by_ydl_is_flagged_not_hidden(store, tmp_path, monkeypatch):
    """yt-dlp's overwrites=False setting makes it silently skip a download
    when the destination file already exists (only a 'finished' hook event
    fires, no 'downloading' event ever arrives). The job must not pretend
    this file was freshly downloaded: it should still be recorded as done,
    but with a clear message that it was not re-downloaded."""
    out = tmp_path / "downloads"
    out.mkdir()
    media = out / "Video.mp4"
    media.write_bytes(b"x" * 2048)  # pre-existing before the "download" runs

    _install_fake_ydl(
        monkeypatch,
        events=[
            {"status": "finished", "filename": str(media)},
        ],
        info={"title": "Video", "requested_downloads": [{"filepath": str(media)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert job.items[0].stage == "done"
    assert job.items[0].path == str(media)
    assert job.items[0].error is not None
    assert "already existed" in job.items[0].error.lower()


def test_mid_playlist_failure_preserves_completed_items(store, tmp_path, monkeypatch):
    """A 3-entry playlist where the 2nd entry fails must not discard the 1st
    entry's already-completed download. Real yt-dlp raises out of
    extract_info as soon as one entry fails (ignoreerrors is unset), so the
    post-extract_info entries loop never runs at all. The only way the first
    entry's result survives is if the progress hook recorded it durably
    (path + stage=done) the moment its "finished" event fired, rather than
    waiting for the post-loop to "own" recording every item."""
    out = tmp_path / "downloads"
    out.mkdir()
    track1 = out / "Track1.mp3"
    track1.write_bytes(b"x" * 10)

    class PartialFailureYDL(FakeYDL):
        def extract_info(self, url, download=True):
            events = [
                {
                    "status": "downloading",
                    "playlist_index": 1,
                    "filename": str(track1),
                    "downloaded_bytes": 5,
                    "total_bytes": 10,
                },
                {"status": "finished", "playlist_index": 1, "filename": str(track1)},
            ]
            for event in events:
                for hook in self._opts["progress_hooks"]:
                    hook(event)
            # Simulates yt-dlp raising when the 2nd of 3 playlist entries
            # fails extraction/download, before returning the info dict.
            raise RuntimeError("ERROR: track 2 is unavailable")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: PartialFailureYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert "track 2 is unavailable" in job.error

    item0 = next(i for i in job.items if i.index == 0)
    assert item0.stage == "done"
    assert item0.path == str(track1)
    assert item0.error is None


def test_mismatched_playlist_index_does_not_false_flag_as_pre_existing(
    store, tmp_path, monkeypatch
):
    """downloaded_indices keyed by hook playlist_index and the final
    enumerate(entries) position only coincide in the simple, non-gappy case.
    Unavailable entries dropped from `entries`, a playliststart offset, or
    extractor renumbering can make yt-dlp report playlist_index values that
    don't line up 1:1 with entries list positions. Detection of "already
    existed" must be based on the downloaded file's path, not on matching
    those two independent index spaces."""
    out = tmp_path / "downloads"
    out.mkdir()
    paths = []
    for name in ("A.mp3", "B.mp3"):
        p = out / name
        p.write_bytes(b"x" * 10)
        paths.append(str(p))

    _install_fake_ydl(
        monkeypatch,
        events=[
            # Hook reports playlist_index 2 and 4 (1-based) for what end up
            # as entries list positions 0 and 1 -- e.g. earlier playlist
            # entries were unavailable and dropped from `entries`.
            {
                "status": "downloading",
                "playlist_index": 2,
                "filename": paths[0],
                "downloaded_bytes": 5,
                "total_bytes": 10,
            },
            {"status": "finished", "playlist_index": 2, "filename": paths[0]},
            {
                "status": "downloading",
                "playlist_index": 4,
                "filename": paths[1],
                "downloaded_bytes": 5,
                "total_bytes": 10,
            },
            {"status": "finished", "playlist_index": 4, "filename": paths[1]},
        ],
        info={
            "entries": [
                {"title": "A", "requested_downloads": [{"filepath": paths[0]}]},
                {"title": "B", "requested_downloads": [{"filepath": paths[1]}]},
            ]
        },
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    by_path = {i.path: i for i in job.items if i.path}
    assert by_path[paths[0]].error is None
    assert by_path[paths[1]].error is None

    # A mismatched playlist_index/entries-position keyspace must not
    # produce a phantom duplicate row: exactly one row per genuinely
    # distinct output file, each carrying its own entry-derived title.
    assert len(job.items) == 2
    assert {i.path for i in job.items} == set(paths)
    assert by_path[paths[0]].title == "A"
    assert by_path[paths[1]].title == "B"


def test_pathless_downloading_event_does_not_clobber_prior_completed_item(
    store, tmp_path, monkeypatch
):
    """A 'downloading' hook event with no filename yet (yt-dlp has not
    resolved a destination for the item currently being worked on) must not
    be keyed by yt-dlp's playlist_index as a fallback: that keyspace is
    independent of the allocator's own and can collide with an
    already-issued index, clobbering an already-completed row back toward a
    bare in-progress placeholder. Since a later entry then fails, there is
    no post-extract_info reconciliation pass to repair the damage."""
    out = tmp_path / "downloads"
    out.mkdir()
    track_a = out / "A.mp3"
    track_a.write_bytes(b"x" * 10)

    class PartialFailureYDL(FakeYDL):
        def extract_info(self, url, download=True):
            events = [
                {
                    "status": "downloading",
                    "filename": str(track_a),
                    "downloaded_bytes": 5,
                    "total_bytes": 10,
                },
                {"status": "finished", "filename": str(track_a)},
                # A second item starts, but yt-dlp has not resolved a
                # destination filename for it yet.
                {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10},
            ]
            for event in events:
                for hook in self._opts["progress_hooks"]:
                    hook(event)
            raise RuntimeError("ERROR: second track is unavailable")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: PartialFailureYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert len(job.items) == 1
    item = job.items[0]
    assert item.stage == "done"
    assert item.path == str(track_a)
    assert item.title == "A.mp3"
    assert item.progress == 100.0


def test_pathless_downloading_event_does_not_rewind_completed_sibling_progress(
    store, tmp_path, monkeypatch
):
    """A path-less 'downloading' event carries a percentage but nothing that
    identifies which file it belongs to. It must not be attributed to any
    existing row: the most recently allocated row belongs to the *previous*,
    already-finished item (allocation only happens once a path is known), so
    writing this event's fractional progress there corrupts a completed
    sibling into stage='done' with progress well under 100. A later entry
    fails here, so no post-extract_info reconciliation pass can repair it."""
    out = tmp_path / "downloads"
    out.mkdir()
    track_a = out / "A.mp3"
    track_a.write_bytes(b"x" * 10)

    class PartialFailureYDL(FakeYDL):
        def extract_info(self, url, download=True):
            events = [
                {
                    "status": "downloading",
                    "filename": str(track_a),
                    "downloaded_bytes": 5,
                    "total_bytes": 10,
                },
                {"status": "finished", "filename": str(track_a)},
                # A NEW item begins downloading; yt-dlp has not resolved its
                # destination filename yet, so this event names no file.
                {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10},
            ]
            for event in events:
                for hook in self._opts["progress_hooks"]:
                    hook(event)
            raise RuntimeError("ERROR: second track is unavailable")

    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: PartialFailureYDL(opts, [], {})
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert len(job.items) == 1
    item = job.items[0]
    assert item.stage == "done"
    assert item.path == str(track_a)
    # The completed sibling's progress must be untouched by the unrelated,
    # unidentifiable event that followed it.
    assert item.progress == 100.0


def test_pathless_finished_event_does_not_rewind_completed_sibling_progress(
    store, tmp_path, monkeypatch
):
    """Same as above for a 'finished' event with no usable path: it identifies
    no file, so it must not be written to any row. Here the in-flight item is
    only half downloaded when an unrelated, unresolvable 'finished' event
    arrives; attributing that event to the in-flight row would falsely show it
    as 100% complete."""
    out = tmp_path / "downloads"
    out.mkdir()
    track_a = out / "A.mp3"
    track_a.write_bytes(b"x" * 10)

    class PartialFailureYDL(FakeYDL):
        def extract_info(self, url, download=True):
            events = [
                {
                    "status": "downloading",
                    "filename": str(track_a),
                    "downloaded_bytes": 5,
                    "total_bytes": 10,
                },
                # An unrelated "finished" event whose file never materialised
                # (e.g. a failed postprocessing step), while track A is still
                # only half downloaded.
                {"status": "finished", "filename": str(out / "ghost.mp3")},
            ]
            for event in events:
                for hook in self._opts["progress_hooks"]:
                    hook(event)
            raise RuntimeError("ERROR: postprocessing failed")

    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: PartialFailureYDL(opts, [], {})
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert len(job.items) == 1
    assert job.items[0].path is None
    assert job.items[0].stage == "downloading"
    # The half-finished item must still read 50%, not be jumped to 100% by an
    # event that never identified it.
    assert job.items[0].progress == 50.0


def test_finished_event_with_unresolvable_filename_does_not_allocate_stray_row(
    store, tmp_path, monkeypatch
):
    """A 'finished' event whose filename does not point to an existing file
    (e.g. a stray or failed postprocessing callback) must not allocate a
    new row via the playlist_index fallback. Since a later entry then
    fails, there is no post-extract_info reconciliation pass that could
    clean up a stray row if one were created."""
    out = tmp_path / "downloads"
    out.mkdir()
    track_a = out / "A.mp3"
    track_a.write_bytes(b"x" * 10)

    class PartialFailureYDL(FakeYDL):
        def extract_info(self, url, download=True):
            events = [
                {
                    "status": "downloading",
                    "filename": str(track_a),
                    "downloaded_bytes": 5,
                    "total_bytes": 10,
                },
                {"status": "finished", "filename": str(track_a)},
                # A "finished" event carrying a playlist_index far from any
                # allocated so far, whose filename never actually landed on
                # disk (e.g. a failed postprocessing step).
                {
                    "status": "finished",
                    "playlist_index": 5,
                    "filename": str(out / "ghost.mp3"),
                },
            ]
            for event in events:
                for hook in self._opts["progress_hooks"]:
                    hook(event)
            raise RuntimeError("ERROR: second track never materialized")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: PartialFailureYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert len(job.items) == 1
    item = job.items[0]
    assert item.path == str(track_a)
    assert item.stage == "done"
    assert item.progress == 100.0


# ---------------------------------------------------------------------------
# Cross-module contract fixes (final whole-branch review)
# ---------------------------------------------------------------------------


def _fake_transcode(src, dst, codec, cancel_event, on_progress):
    """Stand-in for ffmpeg: writes a distinguishable output at `dst`."""
    on_progress(50.0)
    with open(dst, "wb") as f:
        f.write(b"encoded")


def _downloaded_events(path):
    """Hook events for a file genuinely fetched this run."""
    return [
        {
            "status": "downloading",
            "filename": str(path),
            "downloaded_bytes": 50,
            "total_bytes": 100,
        },
        {"status": "finished", "filename": str(path)},
    ]


def test_auto_format_with_codec_falls_back_to_source_extension(
    store, tmp_path, monkeypatch
):
    """C1: the UI's Format control defaults to the literal string "auto" and
    its "Auto" option sends it too. That is a sentinel, not a muxer name: it
    must be treated exactly as the empty string is, or the destination becomes
    `Title.auto`, ffmpeg cannot infer a container, and every re-encode job
    fails."""
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"x" * 100)

    _install_fake_ydl(
        monkeypatch,
        events=_downloaded_events(source),
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)
    monkeypatch.setattr(runner, "transcode_file", _fake_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h264", "format": "auto"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert not job.items[0].path.endswith(".auto")
    assert job.items[0].path.endswith(".mp4")
    assert os.path.isfile(job.items[0].path)


def test_thumbnail_job_records_the_written_thumbnail(store, tmp_path, monkeypatch):
    """C2: `skip_download` makes yt-dlp set info['filepath'] to the *media*
    temp filename, which is never written. The file it actually wrote is
    recorded per-thumbnail as info['thumbnails'][n]['filepath'] (see
    YoutubeDL._write_thumbnails). Without a thumbnails branch the runner
    locates no output and fails a job whose .jpg is sitting on disk."""
    out = tmp_path / "downloads"
    out.mkdir()
    thumb = out / "Video.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    never_written_media = out / "Video.mp4"

    _install_fake_ydl(
        monkeypatch,
        # skip_download runs no file downloader, so no progress hook ever fires.
        events=[],
        info={
            "title": "Video",
            "filepath": str(never_written_media),
            "thumbnails": [
                {"id": "0", "url": "https://example.com/small.jpg"},
                {
                    "id": "1",
                    "url": "https://example.com/big.jpg",
                    "filepath": str(thumb),
                },
            ],
        },
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "thumbnail"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done", job.error
    assert len(job.items) == 1
    assert job.items[0].stage == "done"
    assert job.items[0].path == str(thumb)
    # No progress hook can fire under skip_download, so "no downloading event
    # was seen" carries no information here and must not be reported to the
    # user as a pre-existing file that was skipped.
    assert job.items[0].error is None


def test_transcode_does_not_delete_a_pre_existing_source_file(
    store, tmp_path, monkeypatch
):
    """I1: re-submitting a URL whose file you already have, with a codec
    selected, must not destroy the original. The runner itself flagged this
    item as pre-existing-and-not-downloaded; the transcode stage has to
    honour that flag instead of unconditionally removing the source."""
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"the original the user already had")

    _install_fake_ydl(
        monkeypatch,
        # Only a "finished" event: yt-dlp's overwrites=False skipped the
        # download because the destination was already there.
        events=[{"status": "finished", "filename": str(source)}],
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)
    monkeypatch.setattr(runner, "transcode_file", _fake_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h265", "format": "mkv"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert source.exists(), "a pre-existing source must never be deleted"
    assert source.read_bytes() == b"the original the user already had"
    assert job.items[0].path.endswith(".mkv")
    assert os.path.isfile(job.items[0].path)
    assert job.items[0].error is not None
    assert "kept" in job.items[0].error.lower()


def test_same_container_transcode_keeps_the_plain_filename(
    store, tmp_path, monkeypatch
):
    """I5: encoding straight to `{stem}.{ext}` while the source still occupies
    that exact path makes unique_path divert to `Title (1).mp4` every time,
    having collided with nothing but the file about to be removed."""
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"x" * 100)

    _install_fake_ydl(
        monkeypatch,
        events=_downloaded_events(source),
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)
    monkeypatch.setattr(runner, "transcode_file", _fake_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h264", "format": "mp4"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert job.items[0].path == str(source)
    assert not (out / "Video (1).mp4").exists()
    assert source.read_bytes() == b"encoded"
    # The scratch file the encode was written to must not be left behind.
    assert sorted(p.name for p in out.iterdir()) == ["Video.mp4"]


def test_a_failed_rename_leaves_the_source_intact(store, tmp_path, monkeypatch):
    """Removing the source before moving the encode into place meant that a
    failure in the move - or in the root check in front of it - left the
    caller unlinking the scratch file too, destroying both the original and
    its re-encode."""
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"the only copy")

    encoded = []

    def failing_transcode(src, dst, codec, cancel_event, on_progress):
        _fake_transcode(src, dst, codec, cancel_event, on_progress)
        encoded.append(True)

    def reject_the_destination(path):
        # Everything up to and including the scratch name passes; the final
        # name fails, which is where the old ordering had already deleted the
        # source.
        if encoded and path == str(out / "Video.mp4"):
            raise ValueError("outside allowed roots")

    _install_fake_ydl(
        monkeypatch,
        events=_downloaded_events(source),
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(
        runner, "assert_within_allowed_roots", reject_the_destination
    )
    monkeypatch.setattr(runner, "transcode_file", failing_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h264", "format": "mp4"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    assert store.get_job(job_id).stage == "error"
    assert source.exists(), "the source must survive a failed rename"
    assert source.read_bytes() == b"the only copy"
    # The scratch file is still cleaned up; only the original is preserved.
    assert sorted(p.name for p in out.iterdir()) == ["Video.mp4"]


def test_transcode_never_overwrites_an_unrelated_existing_file(
    store, tmp_path, monkeypatch
):
    """The `(n)` suffix still has to happen when the destination name is
    genuinely taken by an unrelated file."""
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.webm"
    source.write_bytes(b"x" * 100)
    bystander = out / "Video.mp4"
    bystander.write_bytes(b"somebody else's file")

    _install_fake_ydl(
        monkeypatch,
        events=_downloaded_events(source),
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)
    monkeypatch.setattr(runner, "transcode_file", _fake_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h264", "format": "mp4"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert bystander.read_bytes() == b"somebody else's file"
    assert job.items[0].path == str(out / "Video (1).mp4")
    assert not source.exists()


def test_cancelled_transcode_leaves_no_scratch_file(store, tmp_path, monkeypatch):
    """Cancellation always yields `cancelled`, and the half-written scratch
    file the encode was going to must not survive it."""
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"x" * 100)
    cancel = threading.Event()

    _install_fake_ydl(
        monkeypatch,
        events=_downloaded_events(source),
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    def cancelling_transcode(src, dst, codec, cancel_event, on_progress):
        with open(dst, "wb") as f:
            f.write(b"partial")
        cancel.set()
        raise TranscodeCancelled("Cancelled by user")

    monkeypatch.setattr(runner, "transcode_file", cancelling_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h264", "format": "mkv"}
    )
    runner.run_job(store, store.get_job(job_id), cancel)

    job = store.get_job(job_id)
    assert job.stage == "cancelled"
    assert job.error == "Cancelled by user"
    assert sorted(p.name for p in out.iterdir()) == ["Video.mp4"]


def test_transcode_stage_tolerates_a_job_deleted_mid_flight(store):
    """M1: `store.get_job` returns None for a job deleted while it ran, and
    dereferencing `.items` on that raises AttributeError."""
    job_id = store.create_job("https://example.com/v", {"codec": "h264"})
    job = store.get_job(job_id)
    store.delete_job(job_id)

    runner._run_transcode_stage(store, job, threading.Event())


# ---------------------------------------------------------------------------
# Intermediate download files (merge / audio extraction)
#
# Every test above drives the fake yt-dlp with exactly one clean output path
# per entry. Real yt-dlp does not behave that way for the two format
# selectors this app actually uses:
#
#   * `bv*+ba` (every video job, see ydl.video_format_selector) makes
#     YoutubeDL.process_info assign `f['filepath'] = Title.f<id>.<ext>` to
#     each requested format, download them one at a time through
#     `self.dl(...)` - so the progress hook sees `downloading` *and*
#     `finished` for each stream - and only then run FFmpegMergerPP, which
#     writes `Title.mkv` and returns `info['__files_to_merge']` as
#     files-to-delete, so `_delete_downloaded_files` unlinks both streams.
#     `requested_downloads[0]['filepath']` is the merged file
#     (YoutubeDL.post_process sets `info['filepath'] = dl_filename`).
#
#   * audio jobs download `Title.webm`, then FFmpegExtractAudio rewrites
#     `information['filepath']` to `Title.opus` and returns `[orig_path]`,
#     so `Title.webm` is unlinked too.
#
# In both cases the hook has already allocated a store row per intermediate
# stream, and those rows point at files that no longer exist by the time the
# job finishes.
# ---------------------------------------------------------------------------


def _stream_events(path, size):
    """The `downloading` + `finished` pair yt-dlp fires per stream file."""
    return [
        {
            "status": "downloading",
            "filename": str(path),
            "downloaded_bytes": size // 2,
            "total_bytes": size,
        },
        {"status": "finished", "filename": str(path), "total_bytes": size},
    ]


def test_merged_video_records_only_the_merged_file(store, tmp_path, monkeypatch):
    """A `bv*+ba` download must produce exactly one item - the merged file -
    not one row per intermediate stream. The intermediates are gone by the
    time the job ends, so a row pointing at one 404s on download and feeds a
    missing path to the transcode stage."""
    out = tmp_path / "downloads"
    out.mkdir()
    video_stream = out / "Pastewka Theme.f137.mp4"
    audio_stream = out / "Pastewka Theme.f251.webm"
    merged = out / "Pastewka Theme.mkv"

    class MergingYDL(FakeYDL):
        def extract_info(self, url, download=True):
            for stream, size in ((video_stream, 932), (audio_stream, 346)):
                stream.write_bytes(b"x" * size)
                for event in _stream_events(stream, size):
                    self.fire(event)
            # FFmpegMergerPP writes the merged file, then run_pp deletes the
            # streams it returned as __files_to_merge.
            merged.write_bytes(b"m" * 1278)
            os.remove(video_stream)
            os.remove(audio_stream)
            return {
                "title": "Pastewka Theme",
                "requested_downloads": [{"filepath": str(merged)}],
            }

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: MergingYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done", job.error
    assert len(job.items) == 1
    item = job.items[0]
    assert item.path == str(merged)
    assert item.path.endswith(".mkv")
    assert item.stage == "done"
    assert item.progress == 100.0
    assert item.size == 1278
    # The merged file never receives a "downloading" event of its own - only
    # its streams do - so it must not be mistaken for a pre-existing file.
    assert item.error is None
    assert all(os.path.isfile(i.path) for i in job.items if i.path)


def test_extracted_audio_records_only_the_extracted_file(
    store, tmp_path, monkeypatch
):
    """FFmpegExtractAudio replaces the downloaded container with a new file
    and deletes the original. Only the extracted file may be recorded."""
    out = tmp_path / "downloads"
    out.mkdir()
    downloaded = out / "Pastewka Theme.webm"
    extracted = out / "Pastewka Theme.opus"

    class ExtractingYDL(FakeYDL):
        def extract_info(self, url, download=True):
            downloaded.write_bytes(b"x" * 400)
            for event in _stream_events(downloaded, 400):
                self.fire(event)
            extracted.write_bytes(b"o" * 380)
            os.remove(downloaded)
            return {
                "title": "Pastewka Theme",
                "requested_downloads": [{"filepath": str(extracted)}],
            }

    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: ExtractingYDL(opts, [], {})
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done", job.error
    assert len(job.items) == 1
    item = job.items[0]
    assert item.path == str(extracted)
    assert item.stage == "done"
    assert item.error is None
    assert all(os.path.isfile(i.path) for i in job.items if i.path)


def test_transcode_after_audio_extraction_uses_the_extracted_file(
    store, tmp_path, monkeypatch
):
    """The re-encode reads `item.path`. With a row left over for the deleted
    `.webm` the stage fed ffmpeg a path that no longer existed - the reported
    "No such file or directory" for `theme.webm` when `theme.opus` was what
    was actually on disk."""
    out = tmp_path / "downloads"
    out.mkdir()
    downloaded = out / "theme.webm"
    extracted = out / "theme.opus"
    sources = []

    class ExtractingYDL(FakeYDL):
        def extract_info(self, url, download=True):
            downloaded.write_bytes(b"x" * 400)
            for event in _stream_events(downloaded, 400):
                self.fire(event)
            extracted.write_bytes(b"o" * 380)
            os.remove(downloaded)
            return {
                "title": "theme",
                "requested_downloads": [{"filepath": str(extracted)}],
            }

    def recording_transcode(src, dst, codec, cancel_event, on_progress):
        sources.append(src)
        assert os.path.isfile(src), f"transcode source does not exist: {src}"
        _fake_transcode(src, dst, codec, cancel_event, on_progress)

    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: ExtractingYDL(opts, [], {})
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)
    monkeypatch.setattr(runner, "transcode_file", recording_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "audio", "codec": "flac", "format": "flac"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done", job.error
    assert sources == [str(extracted)]
    assert len(job.items) == 1
    assert job.items[0].path == str(out / "theme.flac")
    assert os.path.isfile(job.items[0].path)
    # The extracted source was produced by this run, so it is replaced rather
    # than preserved beside the encode.
    assert job.items[0].error is None
    assert not extracted.exists()


def test_playlist_of_merged_videos_records_one_item_per_entry(
    store, tmp_path, monkeypatch
):
    """N entries, each downloaded as two streams and merged, must yield
    exactly N items - not 3N."""
    out = tmp_path / "downloads"
    out.mkdir()
    titles = ("First", "Second", "Third")

    class MergingPlaylistYDL(FakeYDL):
        def extract_info(self, url, download=True):
            entries = []
            for title in titles:
                video_stream = out / f"{title}.f137.mp4"
                audio_stream = out / f"{title}.f251.webm"
                for stream in (video_stream, audio_stream):
                    stream.write_bytes(b"x" * 100)
                    for event in _stream_events(stream, 100):
                        self.fire(event)
                merged = out / f"{title}.mkv"
                merged.write_bytes(b"m" * 200)
                os.remove(video_stream)
                os.remove(audio_stream)
                entries.append(
                    {
                        "title": title,
                        "requested_downloads": [{"filepath": str(merged)}],
                    }
                )
            return {"entries": entries}

    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: MergingPlaylistYDL(opts, [], {})
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done", job.error
    assert len(job.items) == 3
    assert [i.title for i in job.items] == list(titles)
    assert [i.path for i in job.items] == [str(out / f"{t}.mkv") for t in titles]
    assert all(i.stage == "done" and i.error is None for i in job.items)
    assert all(os.path.isfile(i.path) for i in job.items)


def test_intermediate_rows_still_report_live_progress(store, tmp_path, monkeypatch):
    """Removing the intermediate rows must not cost the live progress they
    drive: the hook is the only thing that updates the UI during a download,
    so it has to keep writing while the streams are in flight."""
    out = tmp_path / "downloads"
    out.mkdir()
    video_stream = out / "Title.f137.mp4"
    merged = out / "Title.mkv"
    seen = []

    class MergingYDL(FakeYDL):
        def extract_info(self, url, download=True):
            video_stream.write_bytes(b"x" * 100)
            self.fire(
                {
                    "status": "downloading",
                    "filename": str(video_stream),
                    "downloaded_bytes": 40,
                    "total_bytes": 100,
                }
            )
            seen.append(
                [(i.title, i.progress, i.stage) for i in store.get_job(job_id).items]
            )
            self.fire({"status": "finished", "filename": str(video_stream)})
            merged.write_bytes(b"m" * 100)
            os.remove(video_stream)
            return {
                "title": "Title",
                "requested_downloads": [{"filepath": str(merged)}],
            }

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: MergingYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    assert seen == [[("Title.mp4", 40.0, "downloading")]]
    job = store.get_job(job_id)
    assert job.stage == "done", job.error
    assert len(job.items) == 1
    assert job.items[0].path == str(merged)
