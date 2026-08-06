import importlib

import pytest


@pytest.fixture
def ydl(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    media = tmp_path / "media"
    downloads.mkdir()
    (media / "Music").mkdir(parents=True)

    import app.config as config_mod

    monkeypatch.setattr(config_mod, "DOWNLOADS_DIR", str(downloads), raising=False)
    monkeypatch.setattr(config_mod, "BASE_PATHS", [str(media)], raising=False)
    monkeypatch.setattr(
        config_mod,
        "resolve_base",
        lambda label: str(media) if label == "media" else (_ for _ in ()).throw(
            ValueError(label)
        ),
        raising=False,
    )

    import app.downloader.ydl as ydl_mod

    importlib.reload(ydl_mod)
    ydl_mod._test_dirs = {"downloads": downloads, "media": media}
    return ydl_mod


def test_safe_component_strips_separators(ydl):
    assert ydl.safe_component("a/b\\c") == "a_b_c"
    assert ydl.safe_component("  spaced  ") == "spaced"
    assert ydl.safe_component(None) == ""
    assert ydl.safe_component("..") == "__"


def test_unique_path_suffixes_collisions(ydl, tmp_path):
    target = tmp_path / "song.mp3"
    assert ydl.unique_path(str(target)) == str(target)

    target.write_bytes(b"x")
    assert ydl.unique_path(str(target)) == str(tmp_path / "song (1).mp3")

    (tmp_path / "song (1).mp3").write_bytes(b"x")
    assert ydl.unique_path(str(target)) == str(tmp_path / "song (2).mp3")


def test_video_format_selector(ydl):
    assert ydl.video_format_selector("1080p") == "bv*[height<=1080]+ba/b[height<=1080]"
    assert ydl.video_format_selector("best") == "bv*+ba/best"
    assert ydl.video_format_selector("worst") == "wv*+wa/worst"
    assert ydl.video_format_selector("nonsense") == "bv*+ba/best"


def test_audio_format_selector(ydl):
    assert ydl.audio_format_selector("192kbps") == "bestaudio[abr<=192]/bestaudio/best"
    assert ydl.audio_format_selector("best") == "bestaudio/best"
    assert ydl.audio_format_selector("worst") == "worstaudio/worst"


def test_resolve_output_root_defaults_to_downloads_dir(ydl):
    root = ydl.resolve_output_root({})
    assert root == str(ydl._test_dirs["downloads"].resolve())


def test_resolve_output_root_uses_base_and_subfolder(ydl):
    root = ydl.resolve_output_root(
        {"base": "media", "output_dir": "Music", "sub_folder": "Albums"}
    )
    assert root.endswith("Music" + __import__("os").sep + "Albums")


def test_resolve_output_root_rejects_traversal(ydl):
    with pytest.raises(ValueError):
        ydl.resolve_output_root(
            {"base": "media", "output_dir": "Music", "sub_folder": "../../etc"}
        )


def test_assert_within_allowed_roots(ydl, tmp_path):
    inside = ydl._test_dirs["downloads"] / "file.mp4"
    ydl.assert_within_allowed_roots(str(inside))

    with pytest.raises(ValueError):
        ydl.assert_within_allowed_roots(str(tmp_path / "elsewhere" / "file.mp4"))


def test_build_ydl_opts_video_container_only_never_transcodes(ydl):
    opts = ydl.build_ydl_opts(
        {"type": "video", "format": "mkv", "quality": "1080p"}, "/tmp/out", None
    )
    assert opts["merge_output_format"] == "mkv"
    assert "postprocessors" not in opts
    assert opts["overwrites"] is False


def test_build_ydl_opts_audio_extracts_with_codec(ydl):
    opts = ydl.build_ydl_opts(
        {"type": "audio", "format": "mp3", "quality": "320kbps"}, "/tmp/out", None
    )
    pp = opts["postprocessors"][0]
    assert pp["key"] == "FFmpegExtractAudio"
    assert pp["preferredcodec"] == "mp3"
    assert pp["preferredquality"] == "320"


def test_build_ydl_opts_thumbnail_skips_download(ydl):
    opts = ydl.build_ydl_opts({"type": "thumbnail", "format": "png"}, "/tmp/out", None)
    assert opts["skip_download"] is True
    assert opts["writethumbnail"] is True
    assert opts["postprocessors"][0]["key"] == "FFmpegThumbnailsConvertor"


def test_build_ydl_opts_applies_cookies_and_playlist_limit(ydl, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape")
    opts = ydl.build_ydl_opts({"item_limit": 5}, "/tmp/out", str(cookies))
    assert opts["cookiefile"] == str(cookies)
    assert opts["playlistend"] == 5


def test_build_ydl_opts_custom_filename_and_prefix(ydl):
    opts = ydl.build_ydl_opts(
        {"custom_prefix": "SSIO_", "custom_filename": "track"}, "/tmp/out", None
    )
    assert opts["outtmpl"].endswith("SSIO_track.%(ext)s")


def test_needs_transcode_only_when_codec_requested(ydl):
    assert ydl.needs_transcode({"type": "video", "codec": "h265"}) is True
    assert ydl.needs_transcode({"type": "video", "codec": "auto"}) is False
    assert ydl.needs_transcode({"type": "video"}) is False
    assert ydl.needs_transcode({"type": "thumbnail", "codec": "h265"}) is False
