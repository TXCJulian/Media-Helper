import pytest


@pytest.fixture
def tmp_media_dir(tmp_path):
    """Create a temporary media directory structure."""
    tvshows = tmp_path / "TV Shows"
    music = tmp_path / "Music"
    tvshows.mkdir()
    music.mkdir()
    return tmp_path


@pytest.fixture
def tmp_tvshow_dir(tmp_media_dir):
    """Create a temporary TV show directory with sample video files."""
    show_dir = tmp_media_dir / "TV Shows" / "TestShow" / "Season 01"
    show_dir.mkdir(parents=True)

    for name in ["Episode.One.S01E01.mkv", "Episode.Two.S01E02.mkv", "Episode.Three.S01E03.mkv"]:
        (show_dir / name).write_bytes(b"\x00" * 100)

    return show_dir


@pytest.fixture
def tmp_music_dir(tmp_media_dir):
    """Create a temporary music directory."""
    artist_dir = tmp_media_dir / "Music" / "TestArtist" / "TestAlbum"
    artist_dir.mkdir(parents=True)
    return artist_dir


@pytest.fixture
def tmp_tvshow_pattern_dir(tmp_media_dir):
    """Create a TV show directory with pattern-only filenames (DVD/Blu-ray style)."""
    show_dir = tmp_media_dir / "TV Shows" / "PatternShow" / "Season 01"
    show_dir.mkdir(parents=True)

    for name in ["S01E01.mkv", "S01E02.mkv", "S01E03.mkv"]:
        (show_dir / name).write_bytes(b"\x00" * 100)

    return show_dir


@pytest.fixture
def tmp_tvshow_mixed_dir(tmp_media_dir):
    """Create a TV show directory with mixed filenames — some with titles, some pattern-only."""
    show_dir = tmp_media_dir / "TV Shows" / "MixedShow" / "Season 01"
    show_dir.mkdir(parents=True)

    for name in ["Episode.One.S01E01.mkv", "S01E02.mkv", "Wrong.Name.S01E03.mkv"]:
        (show_dir / name).write_bytes(b"\x00" * 100)

    return show_dir
