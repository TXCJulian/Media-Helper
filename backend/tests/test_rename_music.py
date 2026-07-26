"""Tests for music renaming logic."""
import os
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


class TestRenameSchema:
    """The output filename schema: DxxTxx Title.ext"""

    @staticmethod
    def _fake_tags(monkeypatch, tags: dict):
        """Stub tag reading so the schema can be tested without real audio."""
        import app.rename_music as rm

        monkeypatch.setattr(rm, "load_audio_file", lambda _path: object())
        monkeypatch.setattr(
            rm, "get_first_tag_value", lambda _audio, name: tags.get(name)
        )

    def test_disc_and_track_are_prefixed(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        (tmp_path / "whatever.flac").write_bytes(b"\x00")
        self._fake_tags(
            monkeypatch,
            {"title": "Illegal, legal, egal...", "tracknumber": "13", "discnumber": "1"},
        )

        _logs, error = rename_music(str(tmp_path), dry_run=False)

        assert error is None
        assert (tmp_path / "D01T13 Illegal, legal, egal....flac").is_file()

    def test_track_number_from_x_of_y_form(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        (tmp_path / "whatever.mp3").write_bytes(b"\x00")
        self._fake_tags(
            monkeypatch, {"title": "Song", "tracknumber": "7/12", "discnumber": "2/2"}
        )

        rename_music(str(tmp_path), dry_run=False)

        assert (tmp_path / "D02T07 Song.mp3").is_file()

    def test_double_digit_disc_number(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        (tmp_path / "whatever.flac").write_bytes(b"\x00")
        self._fake_tags(
            monkeypatch, {"title": "Song", "tracknumber": "3", "discnumber": "12"}
        )

        rename_music(str(tmp_path), dry_run=False)

        assert (tmp_path / "D12T03 Song.flac").is_file()

    def test_already_correct_is_not_renamed(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        (tmp_path / "D01T05 Song.flac").write_bytes(b"\x00")
        self._fake_tags(
            monkeypatch, {"title": "Song", "tracknumber": "5", "discnumber": "1"}
        )

        logs, _error = rename_music(str(tmp_path), dry_run=False)

        assert (tmp_path / "D01T05 Song.flac").is_file()
        assert any("already correct" in line for line in logs)


class TestLeadingInt:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("7", 7),
            ("07", 7),
            ("  9 ", 9),
            ("7/12", 7),      # ID3 TRCK number-of-total form
            ("12/12", 12),
            ("12", 12),       # must not truncate to the first digit
            (12, 12),
            ("", 0),
            (None, 0),
            ("A2", 0),        # vinyl-style side/track, no leading digits
        ],
    )
    def test_parses_leading_integer(self, raw, expected):
        from app.rename_music import leading_int

        assert leading_int(raw) == expected


class TestSidecarLyrics:
    """.lrc/.txt files sitting next to the audio file."""

    @staticmethod
    def _album(tmp_path, monkeypatch, stem="01-01 Mamma Mia", sidecars=(".lrc", ".txt")):
        import app.rename_music as rm

        (tmp_path / f"{stem}.flac").write_bytes(b"\x00")
        for ext in sidecars:
            (tmp_path / f"{stem}{ext}").write_text("lyrics", encoding="utf-8")

        tags = {"title": "Mamma Mia", "tracknumber": "1", "discnumber": "1"}
        monkeypatch.setattr(rm, "load_audio_file", lambda _p: object())
        monkeypatch.setattr(rm, "get_first_tag_value", lambda _a, n: tags.get(n))

    def test_rename_keeps_lyrics_alongside_audio(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch)
        rename_music(str(tmp_path), dry_run=False, lyrics_action="rename")

        assert (tmp_path / "D01T01 Mamma Mia.flac").is_file()
        assert (tmp_path / "D01T01 Mamma Mia.lrc").is_file()
        assert (tmp_path / "D01T01 Mamma Mia.txt").is_file()
        assert not (tmp_path / "01-01 Mamma Mia.lrc").exists()
        assert not (tmp_path / "01-01 Mamma Mia.txt").exists()

    def test_rename_preserves_lyrics_content(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch)
        rename_music(str(tmp_path), dry_run=False, lyrics_action="rename")

        assert (tmp_path / "D01T01 Mamma Mia.lrc").read_text(encoding="utf-8") == "lyrics"

    def test_delete_removes_lyrics(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch)
        rename_music(str(tmp_path), dry_run=False, lyrics_action="delete")

        assert (tmp_path / "D01T01 Mamma Mia.flac").is_file()
        assert not (tmp_path / "D01T01 Mamma Mia.lrc").exists()
        assert not (tmp_path / "01-01 Mamma Mia.lrc").exists()

    def test_default_action_keeps_lyrics(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch)
        rename_music(str(tmp_path), dry_run=False)

        assert (tmp_path / "D01T01 Mamma Mia.lrc").is_file()

    def test_dry_run_touches_nothing(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch)
        logs, _ = rename_music(str(tmp_path), dry_run=True, lyrics_action="rename")

        assert (tmp_path / "01-01 Mamma Mia.lrc").is_file()
        assert not (tmp_path / "D01T01 Mamma Mia.lrc").exists()
        assert any("D01T01 Mamma Mia.lrc" in line for line in logs)

    def test_existing_target_lyric_is_not_clobbered(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch, sidecars=(".lrc",))
        # An unrelated orphan already occupying the destination name.
        (tmp_path / "D01T01 Mamma Mia.lrc").write_text("orphan", encoding="utf-8")

        rename_music(str(tmp_path), dry_run=False, lyrics_action="rename")

        surviving = {
            p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.lrc")
        }
        assert "orphan" in surviving.values()
        assert "lyrics" in surviving.values()

    def test_invalid_action_rejected(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._album(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            rename_music(str(tmp_path), dry_run=False, lyrics_action="explode")


class TestSummaryCounts:
    """The trailing 'Summary:' line must reflect what actually (would) happen."""

    @staticmethod
    def _mixed_album(tmp_path, monkeypatch):
        """2 renameable, 1 already correct, 1 skipped (no title tag)."""
        import app.rename_music as rm

        per_file = {
            "01-01 Mamma Mia.flac": {
                "title": "Mamma Mia", "tracknumber": "1", "discnumber": "1"
            },
            "01-04 SOS.flac": {
                "title": "SOS", "tracknumber": "4", "discnumber": "1"
            },
            "D01T07 Honey, Honey.flac": {
                "title": "Honey, Honey", "tracknumber": "7", "discnumber": "1"
            },
            "untagged.flac": {"tracknumber": "9", "discnumber": "1"},
        }
        for name in per_file:
            (tmp_path / name).write_bytes(b"\x00")

        # The stub receives the audio handle, so hand back the name it maps to.
        monkeypatch.setattr(rm, "load_audio_file", lambda p: os.path.basename(p))
        monkeypatch.setattr(
            rm, "get_first_tag_value", lambda name, tag: per_file[name].get(tag)
        )

    def test_dry_run_counts_pending_renames(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._mixed_album(tmp_path, monkeypatch)
        logs, _ = rename_music(str(tmp_path), dry_run=True)

        summary = logs[-1]
        assert "2 files would be renamed" in summary
        assert "1 already correct" in summary
        assert "1 skipped" in summary

    def test_dry_run_summary_matches_real_run(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._mixed_album(tmp_path, monkeypatch)
        dry_logs, _ = rename_music(str(tmp_path), dry_run=True)

        self._mixed_album(tmp_path, monkeypatch)
        real_logs, _ = rename_music(str(tmp_path), dry_run=False)

        def counts(line):
            import re
            return re.findall(r"\d+", line)

        assert counts(dry_logs[-1]) == counts(real_logs[-1])

    def test_dry_run_leaves_files_untouched(self, tmp_path, monkeypatch):
        from app.rename_music import rename_music

        self._mixed_album(tmp_path, monkeypatch)
        before = sorted(p.name for p in tmp_path.iterdir())
        rename_music(str(tmp_path), dry_run=True)

        assert sorted(p.name for p in tmp_path.iterdir()) == before
