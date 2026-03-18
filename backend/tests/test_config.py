import os
from unittest.mock import patch


def _reload_config(**env_overrides):
    """Reload config module (and get_dirs which imports from it) with given env vars."""
    import importlib
    import app.config as config_mod
    with patch.dict(os.environ, env_overrides, clear=False):
        importlib.reload(config_mod)
    return config_mod


def test_single_base_path():
    cfg = _reload_config(BASE_PATHS="/media")
    assert cfg.BASE_PATHS == ["/media"]
    assert cfg.BASE_PATH_LABELS == {"media": "/media"}


def test_multiple_base_paths():
    cfg = _reload_config(BASE_PATHS="/media1,/media2")
    assert cfg.BASE_PATHS == ["/media1", "/media2"]
    assert cfg.BASE_PATH_LABELS == {"media1": "/media1", "media2": "/media2"}


def test_label_from_last_segment():
    cfg = _reload_config(BASE_PATHS="/mnt/nas/media1,/mnt/usb/media2")
    assert cfg.BASE_PATH_LABELS == {"media1": "/mnt/nas/media1", "media2": "/mnt/usb/media2"}


def test_label_collision_suffixed():
    cfg = _reload_config(BASE_PATHS="/mnt/a/media,/mnt/b/media")
    assert cfg.BASE_PATH_LABELS == {"media": "/mnt/a/media", "media-2": "/mnt/b/media"}


def test_default_when_unset():
    env = os.environ.copy()
    env.pop("BASE_PATHS", None)
    env.pop("BASE_PATH", None)
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import app.config as config_mod
        importlib.reload(config_mod)
        assert config_mod.BASE_PATHS == ["/media"]


def test_resolve_base_valid():
    cfg = _reload_config(BASE_PATHS="/media1,/media2")
    assert cfg.resolve_base("media1") == "/media1"
    assert cfg.resolve_base("media2") == "/media2"


def test_resolve_base_unknown():
    cfg = _reload_config(BASE_PATHS="/media1")
    import pytest
    with pytest.raises(ValueError, match="Unknown base"):
        cfg.resolve_base("nonexistent")
