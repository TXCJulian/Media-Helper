import importlib
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def downloader_env(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    downloads_dir = tmp_path / "downloads"
    media_root = tmp_path / "media"
    cookies_path = tmp_path / "cookies.txt"
    jobs_dir.mkdir()
    downloads_dir.mkdir()
    media_root.mkdir()

    import app.config as config_mod

    monkeypatch.setattr(config_mod, "DOWNLOADER_JOBS_DIR", str(jobs_dir), raising=False)
    monkeypatch.setattr(config_mod, "DOWNLOADS_DIR", str(downloads_dir), raising=False)
    monkeypatch.setattr(config_mod, "DOWNLOADER_JOB_TTL", 60, raising=False)
    monkeypatch.setattr(config_mod, "YT_DLP_COOKIES", str(cookies_path), raising=False)

    def _resolve_base(label: str) -> str:
        if label == "media":
            return str(media_root)
        raise ValueError(f"Unknown base: {label}")

    monkeypatch.setattr(config_mod, "resolve_base", _resolve_base, raising=False)

    import app.download as download_mod

    importlib.reload(download_mod)
    yield {
        "download": download_mod,
        "jobs_dir": jobs_dir,
        "downloads_dir": downloads_dir,
        "media_root": media_root,
        "cookies_path": cookies_path,
    }
    importlib.reload(download_mod)


def test_create_job_initial_metadata(downloader_env):
    download = downloader_env["download"]

    job_id = download.create_job("https://example.com/watch?v=demo", {"type": "video"})
    meta = download.load_job_metadata(job_id)

    assert meta is not None
    assert meta["job_id"] == job_id
    assert meta["status"] == "queued"
    assert meta["progress"] == 0.0
    assert meta["speed"] is None
    assert meta["eta"] is None


def test_list_jobs_sorted_desc(downloader_env):
    download = downloader_env["download"]

    first = download.create_job("https://example.com/one", {})
    time.sleep(0.01)
    second = download.create_job("https://example.com/two", {})

    jobs = download.list_jobs()

    assert [job["job_id"] for job in jobs] == [second, first]


def test_cleanup_old_jobs_skips_active_and_removes_old_terminal(downloader_env):
    download = downloader_env["download"]
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    active_job = download.create_job("https://example.com/active", {})
    active_meta = download.load_job_metadata(active_job)
    assert active_meta is not None
    active_meta["status"] = "downloading"
    active_meta["created_at"] = old_timestamp
    download.save_job_metadata(active_job, active_meta)

    old_done_job = download.create_job("https://example.com/old", {})
    done_meta = download.load_job_metadata(old_done_job)
    assert done_meta is not None
    done_meta["status"] = "done"
    done_meta["created_at"] = old_timestamp
    download.save_job_metadata(old_done_job, done_meta)

    new_job = download.create_job("https://example.com/new", {})

    download.cleanup_old_jobs()

    assert download.load_job_metadata(active_job) is not None
    assert download.load_job_metadata(old_done_job) is None
    assert download.load_job_metadata(new_job) is not None


def test_get_status_payload_reports_cookie_presence(downloader_env):
    download = downloader_env["download"]
    cookies_path = downloader_env["cookies_path"]
    cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    status = download.get_status_payload()

    assert status["cookies_present"] is True
    assert status["downloads_dir"] == str(downloader_env["downloads_dir"])
    assert isinstance(status["yt_dlp_version"], str)


def test_get_ydl_opts_video_mapping_uses_download_root_and_encoder(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "video",
        "codec": "h264",
        "format": "mp4",
        "quality": "720p",
        "sub_folder": "Clips",
        "custom_prefix": "YT-",
        "item_limit": 3,
        "split_chapters": True,
    }

    with patch("app.download.resolve_video_encoder", return_value="h264_nvenc"):
        opts = download.get_ydl_opts(options)

    assert opts["format"] == "bv*[height<=720]+ba/b[height<=720]"
    assert opts["playlistend"] == 3
    assert opts["split_chapters"] is True
    assert str(downloader_env["downloads_dir"] / "Clips") in opts["outtmpl"]
    assert "YT-%(title)s.%(ext)s" in opts["outtmpl"]

    pp_list = opts.get("postprocessors", [])
    convertor = next((pp for pp in pp_list if pp["key"] == "FFmpegVideoConvertor"), None)
    assert convertor is not None
    assert convertor["prefformat"] == "mp4"

    assert opts["postprocessor_args"]["FFmpegVideoConvertor+ffmpeg"] == ["-c:v", "h264_nvenc"]


def test_get_ydl_opts_video_auto_codec_skips_postprocessors(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "video",
        "codec": "auto",
        "format": "mp4",
        "quality": "best",
    }

    opts = download.get_ydl_opts(options)

    assert opts["format"] == "bv*+ba/best"
    assert opts.get("merge_output_format") == "mp4"
    pp_list = opts.get("postprocessors", [])
    assert not any(pp["key"] == "FFmpegVideoConvertor" for pp in pp_list)
    assert "postprocessor_args" not in opts


def test_get_ydl_opts_audio_mapping_uses_postprocessor(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "audio",
        "codec": "flac",
        "format": "flac",
        "quality": "best",
        "base": "media",
        "output_dir": "Albums",
        "sub_folder": "Live",
        "item_limit": 2,
    }

    opts = download.get_ydl_opts(options)

    assert opts["format"] == "bestaudio/best"
    assert opts["playlistend"] == 2
    assert str(downloader_env["media_root"] / "Albums" / "Live") in opts["outtmpl"]

    pp_list = opts.get("postprocessors", [])
    extract = next((pp for pp in pp_list if pp["key"] == "FFmpegExtractAudio"), None)
    assert extract is not None
    assert extract["preferredcodec"] == "flac"
    assert "postprocessor_args" not in opts


def test_get_ydl_opts_audio_auto_format_uses_best(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "audio",
        "codec": "auto",
        "format": "auto",
        "quality": "best",
    }

    opts = download.get_ydl_opts(options)

    assert opts["format"] == "bestaudio/best"
    pp_list = opts.get("postprocessors", [])
    assert not any(pp["key"] == "FFmpegExtractAudio" for pp in pp_list)


def test_get_ydl_opts_thumbnail_mapping(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "thumbnail",
        "codec": "jpg",
        "format": "jpg",
        "quality": "best",
    }

    opts = download.get_ydl_opts(options)

    assert opts["skip_download"] is True
    assert opts["writethumbnail"] is True

    pp_list = opts.get("postprocessors", [])
    thumb_pp = next((pp for pp in pp_list if pp["key"] == "FFmpegThumbnailsConvertor"), None)
    assert thumb_pp is not None
    assert thumb_pp["format"] == "jpg"


def test_delete_job_waits_for_active_runtime_to_finish(downloader_env):
    download = downloader_env["download"]

    job_id = download.create_job("https://example.com/cancel", {})
    cancel_event = download._begin_job(job_id)

    def _complete_job():
        time.sleep(0.2)
        download._finish_job(job_id)

    thread = threading.Thread(target=_complete_job, daemon=True)
    thread.start()

    download.delete_job(job_id)

    assert cancel_event.is_set() is True
    assert not os.path.isdir(downloader_env["jobs_dir"] / job_id)


def test_get_ydl_opts_audio_kbps_sets_preferred_quality(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "audio",
        "format": "mp3",
        "quality": "192kbps",
    }

    opts = download.get_ydl_opts(options)

    pp_list = opts.get("postprocessors", [])
    extract = next((pp for pp in pp_list if pp["key"] == "FFmpegExtractAudio"), None)
    assert extract is not None
    assert extract["preferredcodec"] == "mp3"
    assert extract["preferredquality"] == "192"


def test_get_ydl_opts_custom_filename_sanitised(downloader_env):
    download = downloader_env["download"]

    options = {
        "type": "video",
        "codec": "auto",
        "format": "mp4",
        "quality": "best",
        "custom_filename": "../../etc/passwd",
    }

    opts = download.get_ydl_opts(options)

    # Path separators in the filename portion should be replaced with underscores
    filename_part = os.path.basename(opts["outtmpl"]).replace(".%(ext)s", "")
    assert "/" not in filename_part
    assert "\\" not in filename_part
    assert filename_part == ".._.._etc_passwd"


def test_download_manager_run_success_updates_metadata_and_events(downloader_env):
    download = downloader_env["download"]
    job_id = download.create_job(
        "https://example.com/watch?v=demo",
        {"type": "video", "codec": "h264", "format": "mp4", "quality": "best"},
    )
    msg_queue: queue.Queue = queue.Queue()

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=True):
            assert url == "https://example.com/watch?v=demo"
            assert download is True
            filepath = (
                self.opts["outtmpl"]
                .replace("%(title)s", "demo")
                .replace("%(ext)s", "mp4")
            )
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            for hook in self.opts["progress_hooks"]:
                hook(
                    {
                        "status": "downloading",
                        "filename": filepath,
                        "_percent_str": "50.0%",
                        "_speed_str": "1.0MiB/s",
                        "_eta_str": "00:05",
                    }
                )
                hook({"status": "finished", "filename": filepath})
            for hook in self.opts["postprocessor_hooks"]:
                hook({"status": "started", "info_dict": {"filepath": filepath}})
            Path(filepath).write_bytes(b"demo")
            return {"filepath": filepath, "filesize": 4}

    with patch("app.download.resolve_video_encoder", return_value="libx264"), patch(
        "app.download.YoutubeDL", FakeYDL
    ):
        manager = download.DownloadManager(
            job_id,
            "https://example.com/watch?v=demo",
            {"type": "video", "codec": "h264", "format": "mp4", "quality": "best"},
        )
        manager.run(msg_queue)

    events = []
    while not msg_queue.empty():
        events.append(msg_queue.get_nowait())

    meta = download.load_job_metadata(job_id)
    assert meta is not None
    assert meta["status"] == "done"
    assert meta["filename"] == "demo.mp4"
    assert meta["size"] == "4B"
    assert any(event_type == "progress" for event_type, _ in events)
    assert any(event_type == "done" and payload["status"] == "done" for event_type, payload in events)
