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


def test_swap_refuses_to_overwrite_an_unrelated_file_at_the_destination(library, tmp_path):
    """final_path is derived by extension substitution alone. If a different,
    unrelated movie already sits at that path (e.g. the library holds both
    "Film (2019).mkv" and a distinct "Film (2019).mp4"), publishing must not
    silently destroy it -- that file was never part of this job."""
    movies, source, encoded = library
    encoded = movies / ".hbenc-job1.mp4"
    encoded.write_bytes(b"E" * 1024)
    unrelated = movies / "Film (2019).mp4"
    unrelated.write_bytes(b"U" * 2048)

    with pytest.raises(SwapError):
        swap_in(str(source), str(encoded), original_ttl=0,
                holding_dir=str(tmp_path / "hold"))

    assert source.read_bytes() == b"O" * 4096
    assert unrelated.read_bytes() == b"U" * 2048


def test_the_original_is_restored_when_publish_fails_after_it_was_preserved(
    library, tmp_path, monkeypatch
):
    """The highest-consequence branch in the module: the original has
    physically been moved into holding, and then the publish itself fails.
    It must be moved back to source rather than left stranded."""
    movies, source, encoded = library
    holding = tmp_path / "hold"

    import app.encoder.swap as swap_mod

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(swap_mod.os, "replace", _boom)
    with pytest.raises(SwapError):
        swap_in(str(source), str(encoded), original_ttl=604800,
                holding_dir=str(holding))

    assert source.read_bytes() == b"O" * 4096
    assert not holding.exists() or list(holding.glob("*")) == []


def test_swap_error_carries_the_kept_path_when_restore_also_fails(
    library, tmp_path, monkeypatch
):
    """If the original was moved into holding and *both* the publish and the
    subsequent restore-to-source fail, the movie survives at kept_path but
    not at source. Callers (Task 8's error handling, the UI) need that
    pointer -- a log line alone is not a recovery path."""
    movies, source, encoded = library
    holding = tmp_path / "hold"

    import app.encoder.swap as swap_mod

    def _boom(*_a, **_k):
        raise OSError("disk full")

    # Captured before patching, so it's the real function object rather than
    # a module-attribute lookup that would recurse into the patch below.
    _real_move = swap_mod.shutil.move
    move_calls = {"n": 0}

    def _flaky_move(src, dst):
        # Let the initial preserve-to-holding move succeed for real, so the
        # scenario under test is specifically "restore back to source" --
        # not "could not preserve in the first place", which is already
        # covered by test_the_original_survives_when_the_holding_move_fails.
        move_calls["n"] += 1
        if move_calls["n"] == 1:
            return _real_move(src, dst)
        raise OSError("disk full")

    monkeypatch.setattr(swap_mod.os, "replace", _boom)
    monkeypatch.setattr(swap_mod.shutil, "move", _flaky_move)

    with pytest.raises(SwapError) as excinfo:
        swap_in(str(source), str(encoded), original_ttl=604800,
                holding_dir=str(holding))

    assert excinfo.value.kept_path is not None
    assert os.path.exists(excinfo.value.kept_path)
    assert open(excinfo.value.kept_path, "rb").read() == b"O" * 4096


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


def test_sweep_protects_a_live_job_with_a_hyphenated_id(library):
    """Job ids come verbatim from the encoder service (client.py) and are not
    guaranteed to be hyphen-free -- a canonical str(uuid4()) is exactly this
    shape. A sweep that split on the first hyphen to find the job id would
    slice such an id in half and mistake a live job's own partial, in either
    its published or staging form, for an orphan."""
    movies, _, _ = library
    live_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    (movies / f".hbenc-{live_id}.mkv").write_bytes(b"x")
    (movies / f".hbenc-{live_id}-9f3a1c.mkv").write_bytes(b"x")
    (movies / ".hbenc-dead1.mkv").write_bytes(b"x")

    removed = sweep_orphans(str(movies), active_job_ids={live_id, "job1"})

    assert sorted(os.path.basename(p) for p in removed) == [".hbenc-dead1.mkv"]
    assert (movies / f".hbenc-{live_id}.mkv").exists()
    assert (movies / f".hbenc-{live_id}-9f3a1c.mkv").exists()


def test_sweep_never_touches_real_media(library):
    movies, source, encoded = library
    sweep_orphans(str(movies), active_job_ids=set())
    assert source.exists()


def test_sweep_tolerates_a_missing_directory():
    assert sweep_orphans("/nonexistent/path", active_job_ids=set()) == []


def test_sweep_finds_orphans_in_nested_per_title_folders(tmp_path):
    """Movies live in per-title subfolders in a normal library layout
    (`Movies/Film (2019)/Film (2019).mkv`), and EncoderWatcher schedules
    itself with `recursive=True`. A flat `os.listdir` sweep -- called only on
    the watch-path roots -- would find nothing at all in that layout,
    silently disabling orphan cleanup in every real deployment."""
    movies = tmp_path / "Movies"
    title_dir = movies / "Film (2019)"
    title_dir.mkdir(parents=True)
    (title_dir / ".hbenc-dead1.mkv").write_bytes(b"x")
    (title_dir / ".hbenc-dead2-9f3a1c.mkv").write_bytes(b"x")

    other_title = movies / "Other Film (2020)"
    other_title.mkdir()
    (other_title / ".hbenc-alive.mkv").write_bytes(b"x")

    removed = sweep_orphans(str(movies), active_job_ids={"alive"})

    assert sorted(os.path.basename(p) for p in removed) == [
        ".hbenc-dead1.mkv", ".hbenc-dead2-9f3a1c.mkv"
    ]
    assert (other_title / ".hbenc-alive.mkv").exists()


def test_purge_original_removes_a_kept_file(tmp_path):
    kept = tmp_path / "kept.mkv"
    kept.write_bytes(b"x")
    assert purge_original(str(kept)) is True
    assert not kept.exists()
    assert purge_original(str(kept)) is False


# ---- Important 11: holding-area names must not collide at 1s granularity --


def test_two_same_basename_preserves_in_the_same_second_both_survive(tmp_path, monkeypatch):
    """`f"{int(time.time())}-{base}"` with `shutil.move` silently overwrites.
    Two sources sharing a basename (common with disc-rip naming) preserved
    in the same second must not destroy the earlier retained original."""
    import app.encoder.swap as swap_mod

    # Freeze time so both preserves land in the same second deterministically
    # -- the bug is specifically about same-second collisions, and relying on
    # real timing would make this test flaky.
    monkeypatch.setattr(swap_mod.time, "time", lambda: 1000.0)

    movie_a_dir = tmp_path / "Movies" / "A"
    movie_b_dir = tmp_path / "Movies" / "B"
    movie_a_dir.mkdir(parents=True)
    movie_b_dir.mkdir(parents=True)
    source_a = movie_a_dir / "movie.mkv"
    source_b = movie_b_dir / "movie.mkv"
    source_a.write_bytes(b"A" * 10)
    source_b.write_bytes(b"B" * 10)

    holding = tmp_path / "hold"
    target_a = swap_mod._preserve(str(source_a), str(holding))
    target_b = swap_mod._preserve(str(source_b), str(holding))

    assert target_a != target_b
    assert os.path.exists(target_a)
    assert os.path.exists(target_b)
    assert open(target_a, "rb").read() == b"A" * 10
    assert open(target_b, "rb").read() == b"B" * 10


# ---- Important 7: cross-device holding directory should warn, not fail ----


def test_a_cross_device_holding_dir_logs_a_warning_on_first_use(tmp_path, monkeypatch, caplog):
    import app.encoder.swap as swap_mod

    swap_mod._cross_device_warned.clear()
    source = tmp_path / "Movies" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"O" * 10)
    holding = tmp_path / "hold"
    holding.mkdir()

    class _FakeStat:
        def __init__(self, st_dev):
            self.st_dev = st_dev

    def _fake_stat(path, *a, **k):
        # Report the holding directory as living on a different device than
        # everything else, regardless of what the real filesystem says --
        # tmp_path is a single filesystem in the test environment, so this is
        # the only way to exercise the cross-device branch deterministically.
        if os.path.abspath(path) == os.path.abspath(str(holding)):
            return _FakeStat(999)
        return _FakeStat(1)

    monkeypatch.setattr(swap_mod, "_stat", _fake_stat)

    with caplog.at_level("WARNING", logger=swap_mod.logger.name):
        swap_mod._preserve(str(source), str(holding))

    assert any("different filesystem" in r.message for r in caplog.records)


def test_the_cross_device_warning_fires_only_once_per_holding_dir(tmp_path, monkeypatch, caplog):
    import app.encoder.swap as swap_mod

    swap_mod._cross_device_warned.clear()
    holding = tmp_path / "hold"
    holding.mkdir()

    class _FakeStat:
        def __init__(self, st_dev):
            self.st_dev = st_dev

    def _fake_stat(path, *a, **k):
        if os.path.abspath(path) == os.path.abspath(str(holding)):
            return _FakeStat(999)
        return _FakeStat(1)

    monkeypatch.setattr(swap_mod, "_stat", _fake_stat)

    for i in range(2):
        source = tmp_path / f"movie{i}.mkv"
        source.write_bytes(b"O")
        with caplog.at_level("WARNING", logger=swap_mod.logger.name):
            swap_mod._preserve(str(source), str(holding))

    warnings = [r for r in caplog.records if "different filesystem" in r.message]
    assert len(warnings) == 1


# ---- Trivial: _copy_ownership must not run before the collision check -----


def test_ownership_is_not_touched_when_the_swap_aborts_on_collision(library, tmp_path, monkeypatch):
    """An aborting job (destination collision) must not chmod/chown the
    encoded file it is refusing to publish."""
    movies, source, _ = library
    encoded = movies / ".hbenc-job1.mp4"
    encoded.write_bytes(b"E" * 1024)
    unrelated = movies / "Film (2019).mp4"
    unrelated.write_bytes(b"U" * 2048)

    import app.encoder.swap as swap_mod

    chmod_calls = []
    monkeypatch.setattr(swap_mod.os, "chmod", lambda *a, **k: chmod_calls.append(a))

    with pytest.raises(SwapError):
        swap_in(str(source), str(encoded), original_ttl=0,
                holding_dir=str(tmp_path / "hold"))

    assert chmod_calls == []
