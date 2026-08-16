import importlib

import pytest

from app import config


def _reload(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(config)


def test_defaults_match_the_spec(monkeypatch):
    cfg = _reload(
        monkeypatch,
        ENCODER_URL=None,
        ENCODER_WATCH_PATHS=None,
        ENCODER_MODE=None,
        ENCODER_ORIGINAL_TTL=None,
        ENCODER_SETTLE_SECONDS=None,
        ENCODER_JOB_TTL=None,
    )
    assert cfg.ENCODER_URL == "http://video-encoder:3335"
    assert cfg.ENCODER_WATCH_PATHS == []
    assert cfg.ENCODER_MODE == "review"
    assert cfg.ENCODER_ORIGINAL_TTL == 604800
    assert cfg.ENCODER_SETTLE_SECONDS == 30
    assert cfg.ENCODER_JOB_TTL == 604800


def test_watch_paths_are_split_and_stripped(monkeypatch):
    cfg = _reload(monkeypatch, ENCODER_WATCH_PATHS=" /media3/Movies , /media4/Films ")
    assert cfg.ENCODER_WATCH_PATHS == ["/media3/Movies", "/media4/Films"]


def test_blank_watch_paths_disable_the_watcher(monkeypatch):
    cfg = _reload(monkeypatch, ENCODER_WATCH_PATHS=" , ,, ")
    assert cfg.ENCODER_WATCH_PATHS == []


def test_mode_falls_back_to_review_when_invalid(monkeypatch):
    """review is the safe default: auto mode rewrites files without asking."""
    cfg = _reload(monkeypatch, ENCODER_MODE="banana")
    assert cfg.ENCODER_MODE == "review"


def test_mode_is_case_insensitive(monkeypatch):
    cfg = _reload(monkeypatch, ENCODER_MODE="AUTO")
    assert cfg.ENCODER_MODE == "auto"


def test_zero_original_ttl_is_preserved_not_defaulted(monkeypatch):
    """0 means 'delete the original immediately' -- a real setting, not absence."""
    cfg = _reload(monkeypatch, ENCODER_ORIGINAL_TTL="0")
    assert cfg.ENCODER_ORIGINAL_TTL == 0


def test_encoder_is_a_valid_feature(monkeypatch):
    cfg = _reload(monkeypatch, ENABLED_FEATURES="encoder")
    assert cfg.ENABLED_FEATURES == ["encoder"]
