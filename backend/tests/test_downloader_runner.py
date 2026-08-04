import os
import threading
from unittest.mock import patch

import pytest

from app.downloader import runner
from app.downloader.store import JobStore


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

    def extract_info(self, url, download=True):
        for event in self._events:
            for hook in self._opts["progress_hooks"]:
                hook(event)
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
            {"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 2048},
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
        events=[],
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
            {"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 2048},
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
