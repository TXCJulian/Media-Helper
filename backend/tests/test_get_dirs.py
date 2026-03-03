"""Tests for directory scanning logic."""
import os
import pytest
from app.get_dirs import get_dirs, has_valid_files


class TestHasValidFiles:
    def test_finds_valid_extension(self, tmp_path):
        (tmp_path / "test.mp4").write_bytes(b"\x00")
        assert has_valid_files(str(tmp_path), {".mp4"}) is True

    def test_no_valid_extension(self, tmp_path):
        (tmp_path / "test.txt").write_text("hello")
        assert has_valid_files(str(tmp_path), {".mp4"}) is False

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "test.MP4").write_bytes(b"\x00")
        assert has_valid_files(str(tmp_path), {".mp4"}) is True

    def test_empty_directory(self, tmp_path):
        assert has_valid_files(str(tmp_path), {".mp4"}) is False

    def test_nested_files(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "video.mkv").write_bytes(b"\x00")
        assert has_valid_files(str(tmp_path), {".mkv"}) is True


class TestGetDirs:
    def test_finds_directories_with_valid_files(self, tmp_path):
        sub = tmp_path / "show" / "season01"
        sub.mkdir(parents=True)
        (sub / "ep.mp4").write_bytes(b"\x00")

        result = get_dirs(str(tmp_path), {".mp4"})
        assert len(result) >= 1
        assert any("season01" in d for d in result)

    def test_excludes_trickplay(self, tmp_path):
        trick = tmp_path / ".trickplay"
        trick.mkdir()
        (trick / "thumb.mp4").write_bytes(b"\x00")

        result = get_dirs(str(tmp_path), {".mp4"})
        assert not any(".trickplay" in d for d in result)

    def test_empty_base(self, tmp_path):
        result = get_dirs(str(tmp_path), {".mp4"})
        assert result == []

    def test_returns_relative_paths(self, tmp_path):
        sub = tmp_path / "artist" / "album"
        sub.mkdir(parents=True)
        (sub / "song.flac").write_bytes(b"\x00")

        result = get_dirs(str(tmp_path), {".flac"})
        for d in result:
            assert not d.startswith(str(tmp_path))
            assert "/" in d or d == os.path.basename(d)

    def test_sorted_output(self, tmp_path):
        for name in ["c_dir", "a_dir", "b_dir"]:
            d = tmp_path / name
            d.mkdir()
            (d / "file.mp4").write_bytes(b"\x00")

        result = get_dirs(str(tmp_path), {".mp4"})
        assert result == sorted(result)
