import os
import time

import pytest

from app.encoder.swap import SwapError, SwapResult, purge_original, sweep_orphans, swap_in


@pytest.fixture
def library(tmp_path):
    movies = tmp_path / "Movies"
    movies.mkdir()
    source = movies / "Film (2019).mkv"
    source.write_bytes(b"O" * 4096)
    encoded = movies / ".hbenc-job1.mkv"
    encoded.write_bytes(b"E" * 1024)
    return movies, source, encoded


def test_the_encoded_file_takes_the_source_path(library, tmp_path):
    movies, source, encoded = library
    result = swap_in(str(source), str(encoded), original_ttl=0,
                     holding_dir=str(tmp_path / "hold"))
    assert result.final_path == str(source)
    assert source.read_bytes() == b"E" * 1024
    assert not encoded.exists()


def test_the_extension_follows_the_encoded_container(library, tmp_path):
    """A preset may transcode mkv to mp4. The published file must carry the
    real container, not the source's extension over different bytes."""
    movies, source, _ = library
    encoded = movies / ".hbenc-job1.mp4"
    encoded.write_bytes(b"E" * 1024)
    result = swap_in(str(source), str(encoded), original_ttl=0,
                     holding_dir=str(tmp_path / "hold"))
    assert result.final_path.endswith("Film (2019).mp4")
    assert not source.exists()


def test_zero_ttl_deletes_the_original_immediately(library, tmp_path):
    movies, source, encoded = library
    result = swap_in(str(source), str(encoded), original_ttl=0,
                     holding_dir=str(tmp_path / "hold"))
    assert result.kept_path is None
    holding = tmp_path / "hold"
    assert not holding.exists() or list(holding.glob("*")) == []


def test_a_positive_ttl_moves_the_original_to_the_holding_area(library, tmp_path):
    movies, source, encoded = library
    holding = tmp_path / "hold"
    result = swap_in(str(source), str(encoded), original_ttl=604800,
                     holding_dir=str(holding))
    assert result.kept_path is not None
    assert os.path.exists(result.kept_path)
    assert open(result.kept_path, "rb").read() == b"O" * 4096
    assert source.read_bytes() == b"E" * 1024


def test_sizes_are_reported_for_the_payoff_figure(library, tmp_path):
    """The done card leads with '4.0 KiB -> 1.0 KiB'; those numbers come from
    here, measured before the original is disturbed."""
    movies, source, encoded = library
    result = swap_in(str(source), str(encoded), original_ttl=0,
                     holding_dir=str(tmp_path / "hold"))
    assert result.original_size == 4096
    assert result.encoded_size == 1024


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership/mode only")
def test_mode_is_copied_from_the_source(library, tmp_path):
    movies, source, encoded = library
    os.chmod(source, 0o664)
    os.chmod(encoded, 0o600)
    result = swap_in(str(source), str(encoded), original_ttl=0,
                     holding_dir=str(tmp_path / "hold"))
    assert oct(os.stat(result.final_path).st_mode)[-3:] == "664"


def test_a_missing_encoded_file_raises_and_leaves_the_source(library, tmp_path):
    movies, source, _ = library
    with pytest.raises(SwapError, match="not found"):
        swap_in(str(source), str(movies / "nope.mkv"), original_ttl=0,
                holding_dir=str(tmp_path / "hold"))
    assert source.read_bytes() == b"O" * 4096


def test_an_empty_encoded_file_is_refused(library, tmp_path):
    """A zero-byte output means the encode produced nothing. Swapping it in
    would destroy the movie and report success."""
    movies, source, encoded = library
    encoded.write_bytes(b"")
    with pytest.raises(SwapError, match="empty"):
        swap_in(str(source), str(encoded), original_ttl=0,
                holding_dir=str(tmp_path / "hold"))
    assert source.read_bytes() == b"O" * 4096


def test_a_vanished_source_raises_and_does_not_publish(library, tmp_path):
    movies, source, encoded = library
    source.unlink()
    with pytest.raises(SwapError):
        swap_in(str(source), str(encoded), original_ttl=0,
                holding_dir=str(tmp_path / "hold"))


def test_the_original_survives_when_the_holding_move_fails(library, tmp_path, monkeypatch):
    """If the original cannot be preserved as asked, the swap must abort with
    the library untouched rather than proceed and destroy it anyway."""
    movies, source, encoded = library

    import app.encoder.swap as swap_mod

    def _boom(*_a, **_k):
        raise OSError("no space left on device")

    monkeypatch.setattr(swap_mod.shutil, "move", _boom)
    with pytest.raises(SwapError):
        swap_in(str(source), str(encoded), original_ttl=604800,
                holding_dir=str(tmp_path / "hold"))
    assert source.read_bytes() == b"O" * 4096
    assert encoded.exists()


def test_the_original_survives_when_publish_fails_at_zero_ttl(library, tmp_path, monkeypatch):
    """At TTL 0 the original must not be deleted until the encoded file is
    verifiably published. If the publish step itself fails, the source must
    still be there afterwards -- deleting it up front and hoping the publish
    works is exactly the ordering bug this module exists to avoid."""
    movies, source, encoded = library

    import app.encoder.swap as swap_mod

    def _boom(*_a, **_k):
        raise OSError("publish failed")

    monkeypatch.setattr(swap_mod.os, "replace", _boom)
    with pytest.raises(SwapError):
        swap_in(str(source), str(encoded), original_ttl=0,
                holding_dir=str(tmp_path / "hold"))
    assert source.read_bytes() == b"O" * 4096


def test_sweep_removes_partials_from_dead_jobs(library):
    """After a crash the encoder's partials are unowned. Both the published
    and the staging shapes can be left behind."""
    movies, _, _ = library
    (movies / ".hbenc-dead1.mkv").write_bytes(b"x")
    (movies / ".hbenc-dead2-9f3a1c.mkv").write_bytes(b"x")
    (movies / ".hbenc-alive.mkv").write_bytes(b"x")
    removed = sweep_orphans(str(movies), active_job_ids={"alive", "job1"})
    assert sorted(os.path.basename(p) for p in removed) == [
        ".hbenc-dead1.mkv", ".hbenc-dead2-9f3a1c.mkv"
    ]
    assert (movies / ".hbenc-alive.mkv").exists()


def test_sweep_never_touches_real_media(library):
    movies, source, encoded = library
    sweep_orphans(str(movies), active_job_ids=set())
    assert source.exists()


def test_sweep_tolerates_a_missing_directory():
    assert sweep_orphans("/nonexistent/path", active_job_ids=set()) == []


def test_purge_original_removes_a_kept_file(tmp_path):
    kept = tmp_path / "kept.mkv"
    kept.write_bytes(b"x")
    assert purge_original(str(kept)) is True
    assert not kept.exists()
    assert purge_original(str(kept)) is False
