"""Tests for episode renaming logic."""
import pytest
from app.rename_episodes import normalize_string, strip_accents, de_translit, best_match, clean_filename


class TestNormalizeString:
    def test_removes_episode_pattern(self):
        result = normalize_string("My.Show.S01E05.Episode.Title.mkv")
        assert "s01e05" not in result
        assert "mkv" not in result

    def test_lowercases(self):
        result = normalize_string("HELLO WORLD")
        assert result == "hello world"

    def test_removes_special_chars(self):
        result = normalize_string("hello-world_test!")
        assert result == "hello world test"

    def test_strips_extension(self):
        result = normalize_string("video.mp4")
        assert "mp4" not in result

    def test_normalizes_whitespace(self):
        result = normalize_string("too   many   spaces")
        assert "  " not in result


class TestStripAccents:
    def test_removes_accents(self):
        assert strip_accents("café") == "cafe"
        assert strip_accents("naïve") == "naive"

    def test_no_accents_unchanged(self):
        assert strip_accents("hello") == "hello"


class TestDeTranslit:
    def test_german_umlauts(self):
        assert de_translit("ä") == "ae"
        assert de_translit("ö") == "oe"
        assert de_translit("ü") == "ue"
        assert de_translit("ß") == "ss"

    def test_uppercase_umlauts(self):
        assert de_translit("Ä") == "Ae"
        assert de_translit("Ö") == "Oe"
        assert de_translit("Ü") == "Ue"

    def test_no_umlauts_unchanged(self):
        assert de_translit("hello") == "hello"


class TestBestMatch:
    def test_exact_match(self):
        candidates = ["episode one", "episode two", "episode three"]
        idx, score = best_match("episode one", candidates)
        assert idx == 0
        assert score == 1.0

    def test_closest_match(self):
        candidates = ["the beginning", "the middle", "the end"]
        idx, score = best_match("the beginning of time", candidates)
        assert idx == 0
        assert score > 0.5

    def test_no_good_match(self):
        candidates = ["completely different", "also different"]
        idx, score = best_match("xyz abc", candidates)
        assert score < 0.5

    def test_empty_candidates(self):
        idx, score = best_match("test", [])
        assert idx == -1
        assert score == 0.0


class TestCleanFilename:
    def test_removes_illegal_chars(self):
        assert clean_filename('file:name?test*') == "filenametest"

    def test_preserves_valid_chars(self):
        assert clean_filename("valid name (1)") == "valid name (1)"

    def test_strips_whitespace(self):
        assert clean_filename("  name  ") == "name"


from app.rename_episodes import extract_episode_number


class TestExtractEpisodeNumber:
    def test_sxxexx_standard(self):
        assert extract_episode_number("S01E05.mkv") == 5
        assert extract_episode_number("S03E28.mkv") == 28

    def test_sxxexx_lowercase(self):
        assert extract_episode_number("s01e05.mkv") == 5

    def test_sxxexx_short(self):
        assert extract_episode_number("s1e3.mkv") == 3

    def test_exx_pattern(self):
        assert extract_episode_number("E05.mkv") == 5
        assert extract_episode_number("e5.mkv") == 5

    def test_plain_number(self):
        assert extract_episode_number("03.mkv") == 3
        assert extract_episode_number("3.mkv") == 3

    def test_no_pattern(self):
        assert extract_episode_number("My Episode Title.mkv") is None
        assert extract_episode_number("Some Random Name.mkv") is None

    def test_sxxexx_with_text(self):
        """SxxExx embedded in text still extracts the number."""
        assert extract_episode_number("My.Show.S01E05.Episode.Title.mkv") == 5

    def test_exx_with_text(self):
        assert extract_episode_number("Show E05 Title.mkv") == 5

    def test_plain_number_not_extracted_from_text(self):
        """Plain numbers should NOT be extracted if other text is present."""
        assert extract_episode_number("The 100.mkv") is None
        assert extract_episode_number("Episode 5 Title.mkv") is None


from app.rename_episodes import is_pattern_only


class TestIsPatternOnly:
    def test_sxxexx_only(self):
        assert is_pattern_only("S03E28.mkv") is True
        assert is_pattern_only("s1e3.mkv") is True

    def test_exx_only(self):
        assert is_pattern_only("E05.mkv") is True
        assert is_pattern_only("e5.mkv") is True

    def test_plain_number_only(self):
        assert is_pattern_only("03.mkv") is True
        assert is_pattern_only("3.mkv") is True

    def test_sxxexx_with_text(self):
        assert is_pattern_only("S03E28 Some Title.mkv") is False
        assert is_pattern_only("My Episode S01E03.mkv") is False

    def test_no_pattern(self):
        assert is_pattern_only("My Episode Title.mkv") is False

    def test_sxxexx_with_dashes_dots(self):
        """Separators without text are still pattern-only."""
        assert is_pattern_only("S03E28 - .mkv") is True
        assert is_pattern_only("S03E28..mkv") is True
