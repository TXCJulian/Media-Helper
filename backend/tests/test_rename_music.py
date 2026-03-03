"""Tests for music renaming logic."""
import pytest
from app.rename_music import (
    try_decode_bytes,
    fix_mojibake_if_needed,
    sanitize_tag_value,
)
from app.get_dirs import has_valid_files
from app.config import VALID_MUSIC_EXT


class TestTryDecodeBytes:
    def test_utf8(self):
        assert try_decode_bytes("hello".encode("utf-8")) == "hello"

    def test_latin1_fallback(self):
        # Latin-1 encoded text that's not valid UTF-8
        raw = bytes([0xE4, 0xF6, 0xFC])  # äöü in latin-1
        result = try_decode_bytes(raw)
        assert result  # should decode without error

    def test_replacement_on_garbage(self):
        result = try_decode_bytes(bytes([0x80, 0x81, 0x82]))
        assert result  # should still return something


class TestFixMojibakeIfNeeded:
    def test_no_mojibake(self):
        assert fix_mojibake_if_needed("hello world") == "hello world"

    def test_suspicious_chars_triggers_fix(self):
        # String with suspicious Ã character
        result = fix_mojibake_if_needed("Ãber")
        assert isinstance(result, str)

    def test_replacement_char_triggers_fix(self):
        result = fix_mojibake_if_needed("hello�world")
        assert isinstance(result, str)


class TestSanitizeTagValue:
    def test_none_returns_empty(self):
        assert sanitize_tag_value(None) == ""

    def test_strips_whitespace(self):
        assert sanitize_tag_value("  hello  ") == "hello"

    def test_removes_control_chars(self):
        result = sanitize_tag_value("hello\x00world")
        assert "\x00" not in result

    def test_removes_path_separators(self):
        result = sanitize_tag_value("path/to\\file")
        assert "/" not in result
        assert "\\" not in result

    def test_bytes_input(self):
        result = sanitize_tag_value(b"hello")
        assert result == "hello"

    def test_unicode_normalization(self):
        # NFC normalization: combining e + accent -> é
        import unicodedata
        decomposed = unicodedata.normalize("NFD", "é")
        result = sanitize_tag_value(decomposed)
        assert result == unicodedata.normalize("NFC", "é")


class TestHasValidMusicFiles:
    def test_with_valid_files(self, tmp_path):
        (tmp_path / "song.flac").write_bytes(b"\x00")
        assert has_valid_files(str(tmp_path), VALID_MUSIC_EXT) is True

    def test_without_valid_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        assert has_valid_files(str(tmp_path), VALID_MUSIC_EXT) is False

    def test_empty_directory(self, tmp_path):
        assert has_valid_files(str(tmp_path), VALID_MUSIC_EXT) is False

    def test_nested_valid_files(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "song.mp3").write_bytes(b"\x00")
        assert has_valid_files(str(tmp_path), VALID_MUSIC_EXT) is True
