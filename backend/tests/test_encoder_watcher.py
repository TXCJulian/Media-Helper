import os

import pytest

from app.encoder.store import EncoderStore
from app.encoder.watcher import EncoderWatcher, SettleTracker


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_a_file_is_not_settled_on_first_sight():
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=30, now=clock)
    assert tracker.saw("/media3/x.mkv", 1000) is False


def test_a_file_settles_once_its_size_holds_for_the_window():
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=30, now=clock)
    tracker.saw("/media3/x.mkv", 1000)
    clock.advance(31)
    assert tracker.saw("/media3/x.mkv", 1000) is True


def test_growth_restarts_the_window():
    """A rip still being copied must not be probed: ffprobe would report
    whatever has landed so far and match the wrong rule."""
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=30, now=clock)
    tracker.saw("/media3/x.mkv", 1000)
    clock.advance(20)
    tracker.saw("/media3/x.mkv", 5000)   # still growing
    clock.advance(20)                     # 40s since first sight, 20s since growth
    assert tracker.saw("/media3/x.mkv", 5000) is False
    clock.advance(11)
    assert tracker.saw("/media3/x.mkv", 5000) is True


def test_shrinking_also_restarts_the_window():
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=30, now=clock)
    tracker.saw("/media3/x.mkv", 5000)
    clock.advance(20)
    tracker.saw("/media3/x.mkv", 1000)
    clock.advance(20)
    assert tracker.saw("/media3/x.mkv", 1000) is False


def test_files_are_tracked_independently():
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=30, now=clock)
    tracker.saw("/media3/a.mkv", 100)
    clock.advance(31)
    tracker.saw("/media3/b.mkv", 100)
    assert tracker.saw("/media3/a.mkv", 100) is True
    assert tracker.saw("/media3/b.mkv", 100) is False


def test_forget_drops_state_so_a_replaced_file_starts_over():
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=30, now=clock)
    tracker.saw("/media3/x.mkv", 100)
    tracker.forget("/media3/x.mkv")
    clock.advance(31)
    assert tracker.saw("/media3/x.mkv", 100) is False


def test_a_zero_settle_window_settles_on_second_sight():
    """Zero means 'no waiting', not 'settle instantly on first sight' -- one
    observation cannot establish that a size is stable."""
    clock = FakeClock()
    tracker = SettleTracker(settle_seconds=0, now=clock)
    assert tracker.saw("/media3/x.mkv", 100) is False
    assert tracker.saw("/media3/x.mkv", 100) is True


# ---------------------------------------------------------------------------
# EncoderWatcher
#
# These drive scan_existing()/_consider directly with settle_seconds=0 so a
# file settles on its second identical-size observation without any real
# elapsed time -- no test here sleeps or waits on a background thread's
# timing.
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = EncoderStore(str(tmp_path / "encoder.db"))
    yield s
    s.close()


class Planner:
    """Stands in for main.py's wiring: create the job, then plan it.

    Tests drive the real `create_job(path, size, mtime_ns)` rather than a bare
    recorder, because the fingerprint that suppresses re-detection is written
    by that call -- a fake callback would exercise none of it.

    *fails* injects a throw either "before" or "after" the job row exists,
    which is the difference between a file that must be retried and one that
    must not.
    """

    def __init__(self, store, fails=None):
        self._store = store
        self._fails = fails
        self.paths: list[str] = []
        self.job_ids: list[str] = []

    def __call__(self, path, size, mtime_ns):
        self.paths.append(path)
        if self._fails == "before":
            raise RuntimeError("planning failed before the job existed")
        job_id = self._store.create_job(path, size, mtime_ns).id
        self.job_ids.append(job_id)
        if self._fails == "after":
            # Mirrors EncodeQueue.plan_new, which converts an unexpected
            # planning failure into a `failed` job rather than leaving the row
            # in `settling` -- a stage that is neither terminal nor resumable.
            self._store.set_stage(job_id, "failed", error="boom",
                                  error_code="plan_failed")
            raise RuntimeError("planning failed after the job existed")


def make_watcher(store, tmp_path, on_settled, settle_seconds=0):
    return EncoderWatcher(
        store=store,
        on_settled=on_settled,
        paths=[str(tmp_path)],
        settle_seconds=settle_seconds,
        valid_extensions={".mkv", ".mp4"},
    )


def test_scan_existing_dispatches_a_settled_file(store, tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"data")
    settled = []
    watcher = make_watcher(
        store, tmp_path, lambda path, _s, _m: settled.append(path))

    watcher.scan_existing()  # first sight: not settled yet
    assert settled == []

    watcher.scan_existing()  # same size on second sight: settled
    assert settled == [str(tmp_path / "movie.mkv")]


def test_a_still_growing_file_is_never_dispatched(store, tmp_path):
    """The staging files this feature writes itself must never be picked up
    as new work, and a file that is still being written must not be
    dispatched mid-copy."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data")
    settled = []
    watcher = make_watcher(
        store, tmp_path, lambda path, _s, _m: settled.append(path))

    watcher.scan_existing()  # first sight
    target.write_bytes(b"data" * 100)  # still growing between scans
    watcher.scan_existing()  # size changed: window restarts
    assert settled == []

    # Now it holds steady across a scan and settles.
    watcher.scan_existing()
    assert settled == [str(target)]


def test_hbenc_staging_files_are_never_dispatched(store, tmp_path):
    """Our own in-progress output (.hbenc-<job_id>.<ext> or
    .hbenc-<job_id>-<token>.<ext>) must never be queued as new work -- a
    watcher that queues its own encoder's output would loop forever."""
    (tmp_path / ".hbenc-abc123.mkv").write_bytes(b"data")
    (tmp_path / ".hbenc-abc123-tok.mkv").write_bytes(b"data")
    settled = []
    watcher = make_watcher(
        store, tmp_path, lambda path, _s, _m: settled.append(path))

    watcher.scan_existing()
    watcher.scan_existing()

    assert settled == []


def test_files_with_an_active_job_are_not_redispatched(store, tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data")
    store.create_job(str(target))  # active source path (stage "settling")
    settled = []
    watcher = make_watcher(
        store, tmp_path, lambda path, _s, _m: settled.append(path))

    watcher.scan_existing()
    watcher.scan_existing()

    assert settled == []


def test_non_video_extensions_are_ignored(store, tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"data")
    settled = []
    watcher = make_watcher(
        store, tmp_path, lambda path, _s, _m: settled.append(path))

    watcher.scan_existing()
    watcher.scan_existing()

    assert settled == []


def test_a_file_vanishing_mid_settle_is_never_dispatched(store, tmp_path):
    """A rip that gets deleted or moved out from under the watcher (a failed
    copy cleaned up, a user cancelling a rip) must not crash and must not be
    dispatched. os.path.getsize raises OSError for a path that no longer
    exists, and _consider must swallow that -- and must also evict the
    tracker entry, or SettleTracker._seen would grow unboundedly over the
    watcher's lifetime, keeping an entry for every path ever seen and later
    removed.

    Calls `_consider` directly rather than through `scan_existing()`: an
    already-deleted file simply never appears in `os.walk`'s listing, so
    going through a full scan wouldn't reach the OSError branch at all. The
    real-time watchdog event handler hits exactly this path directly when a
    create event is immediately followed by a delete.
    """
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data")
    settled = []
    watcher = make_watcher(
        store, tmp_path, lambda path, _s, _m: settled.append(path))

    watcher._consider(str(target))  # first sight: tracker now holds an entry
    target.unlink()
    watcher._consider(str(target))  # must not raise, and must not dispatch

    assert settled == []
    assert str(target) not in watcher._tracker._seen


def test_start_and_stop_leave_no_thread_running(store, tmp_path):
    watcher = make_watcher(store, tmp_path, lambda *_a: None)
    watcher.start()
    try:
        assert watcher._scanner is not None
        assert watcher._scanner.is_alive()
    finally:
        watcher.stop(timeout=5.0)

    assert watcher._observer is None
    assert watcher._scanner is None


def test_scan_existing_queries_active_source_paths_once_per_scan(store, tmp_path, monkeypatch):
    """Before the fix, `_consider` queried `active_source_paths()` once per
    file -- on a 10k-file library that is 10k SQL queries every scan. A
    single walk of several files must produce exactly one query, not one per
    file."""
    for i in range(5):
        (tmp_path / f"movie{i}.mkv").write_bytes(b"data")
    calls = []
    original = store.active_source_paths

    def _spy():
        calls.append(1)
        return original()

    monkeypatch.setattr(store, "active_source_paths", _spy)
    watcher = make_watcher(store, tmp_path, lambda *_a: None)

    watcher.scan_existing()

    assert len(calls) == 1


def test_the_scan_interval_is_generous_given_the_settle_window():
    """A 5s rescan cadence combined with a per-file DB query was a continuous
    stat storm on a large library, worst on exactly the network mounts this
    watcher targets. 30s is still ample against the default 30s settle
    window (and configurable ones), since the watchdog observer -- not the
    rescan -- is what reacts to real filesystem events promptly."""
    from app.encoder import watcher as watcher_mod

    assert watcher_mod._SCAN_INTERVAL >= 30.0


def test_start_with_no_paths_does_not_start_threads(store):
    watcher = EncoderWatcher(
        store=store,
        on_settled=lambda *_a: None,
        paths=[],
        settle_seconds=0,
        valid_extensions={".mkv"},
    )
    watcher.start()
    assert watcher._observer is None
    assert watcher._scanner is None
    watcher.stop()  # must be safe to call even though start() was a no-op


# ---------------------------------------------------------------------------
# Re-detection of files already judged.
#
# Found in production: the deployed watcher created a fresh job for the same
# file every ~60s (scan interval + settle window) forever, because a job in a
# *terminal* stage leaves its path in neither `active_source_paths()` nor the
# settle tracker. Each pass costs an ffprobe subprocess and a job row, for
# every file no rule matches -- which is most of a library.
# ---------------------------------------------------------------------------


def _finish(store, job_id, stage, output_path=None, encoded_size=None):
    """Take an existing job to a terminal stage, as the queue would."""
    if output_path is not None:
        store.set_result(job_id, output_path, encoded_size)
    store.set_stage(job_id, stage)


def _republish(store, path):
    """Record what the queue records after a successful swap."""
    st = os.stat(path)
    store.mark_seen(path, st.st_size, st.st_mtime_ns)


@pytest.mark.parametrize("stage", ["skipped", "failed", "done", "cancelled"])
def test_a_file_with_a_terminal_job_is_not_rejobbed(store, tmp_path, stage):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data" * 100)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target)]

    _finish(store, planner.job_ids[0], stage)

    # Ten further passes stand in for "forever". Before the fix this appended
    # one dispatch every second scan.
    for _ in range(10):
        watcher.scan_existing()
    assert planner.paths == [str(target)], f"re-dispatched after reaching {stage!r}"


def test_a_replaced_file_is_reconsidered(store, tmp_path):
    """Suppression is keyed on the file, not the path: a genuine re-rip must
    still be picked up, or one skipped job would blacklist a path forever."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data" * 100)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    _finish(store, planner.job_ids[0], "skipped")

    watcher.scan_existing()
    assert planner.paths == [str(target)]  # unchanged file stays suppressed

    target.write_bytes(b"different content entirely" * 50)
    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target), str(target)]


def test_a_same_size_replacement_is_detected(store, tmp_path):
    """Re-copying a file restores its byte count but not its mtime.

    Found in live testing: the original had been copied back over a completed
    encode, its size matched the historical source size, and a size-only check
    meant the watcher never looked at it again.
    """
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"A" * 4096)
    os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target)]
    _finish(store, planner.job_ids[0], "done")

    watcher.scan_existing()
    assert planner.paths == [str(target)]  # identical fingerprint, suppressed

    target.write_bytes(b"B" * 4096)
    os.utime(target, ns=(1_700_000_060_000_000_000, 1_700_000_060_000_000_000))
    assert target.stat().st_size == 4096  # same size, new mtime

    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target), str(target)]


def test_our_own_published_output_is_not_rejobbed(store, tmp_path):
    """After a successful swap the file at the source path IS the encode, at a
    new size and mtime. Without re-fingerprinting it, a still-matching rule
    would re-encode it on every pass -- silent generation loss."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"original" * 100)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target)]

    target.write_bytes(b"encoded" * 10)  # the swap
    _finish(store, planner.job_ids[0], "done",
            output_path=str(target), encoded_size=target.stat().st_size)
    _republish(store, str(target))

    for _ in range(6):
        watcher.scan_existing()
    assert planner.paths == [str(target)]


def test_output_published_under_a_new_extension_is_not_rejobbed(store, tmp_path):
    """A container change publishes beside the source under a different name.
    That file is our own output and must not look like a new arrival."""
    source = tmp_path / "movie.mkv"
    published = tmp_path / "movie.mp4"
    source.write_bytes(b"original" * 100)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(source)]

    source.unlink()
    published.write_bytes(b"encoded" * 10)
    _finish(store, planner.job_ids[0], "done",
            output_path=str(published), encoded_size=published.stat().st_size)
    _republish(store, str(published))

    for _ in range(6):
        watcher.scan_existing()
    assert planner.paths == [str(source)]


def test_dedup_survives_job_history_expiry(store, tmp_path):
    """Job rows are history and expire; "have I looked at this file" must not.

    Deriving dedup from the jobs table meant that one ENCODER_JOB_TTL after an
    encode, `purge_expired()` removed the evidence and the whole library became
    eligible for reprocessing -- re-encoding published output under any rule
    that still matched.
    """
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data" * 100)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    _finish(store, planner.job_ids[0], "done",
            output_path=str(target), encoded_size=target.stat().st_size)

    assert store.purge_expired(0) >= 1
    assert store.list_jobs() == []

    for _ in range(6):
        watcher.scan_existing()
    assert planner.paths == [str(target)], "re-detected once its history expired"


def test_a_dispatch_that_fails_before_the_job_exists_is_retried(store, tmp_path):
    """The fingerprint must not outlive a failed dispatch.

    Reproduced by review: the callback ran, no job was created, yet the
    fingerprint was already durable -- so every later scan suppressed the file
    and it was never processed. Writing the fingerprint with the job row makes
    "decided" and "recorded" the same event.
    """
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data" * 100)
    planner = Planner(store, fails="before")
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target)]      # dispatch attempted
    assert store.list_jobs() == []             # but no job exists
    assert store.seen_fingerprints() == {}     # so nothing may be suppressed

    watcher.scan_existing()
    watcher.scan_existing()
    assert len(planner.paths) == 2, "a failed dispatch was never retried"


def test_a_dispatch_that_fails_after_the_job_exists_is_not_retried(store, tmp_path):
    """The converse: once the job row exists the decision is recorded, so the
    file must not be probed again every scan even though planning threw."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data" * 100)
    planner = Planner(store, fails="after")
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert len(planner.paths) == 1
    assert len(store.list_jobs()) == 1

    # The job must be terminal, not left in `settling`: that stage holds the
    # source path against the unique active index forever, is skipped by
    # restart recovery, and is invisible to the watcher.
    assert store.get_job(planner.job_ids[0]).stage == "failed"

    for _ in range(6):
        watcher.scan_existing()
    assert len(planner.paths) == 1


def test_reprocess_clears_the_record_so_the_file_is_seen_again(store, tmp_path):
    """The explicit escape hatch: a rule change must not require touching the
    file on disk or editing the database to get it reconsidered."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data" * 100)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    _finish(store, planner.job_ids[0], "skipped")

    watcher.scan_existing()
    assert planner.paths == [str(target)]  # suppressed

    assert store.forget_seen(str(target)) is True
    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(target), str(target)]


def test_records_for_deleted_files_are_pruned(store, tmp_path):
    """Otherwise the table only grows: every file ever deleted or renamed
    leaves a row behind, so library churn accumulates without bound."""
    keep = tmp_path / "keep.mkv"
    gone = tmp_path / "gone.mkv"
    keep.write_bytes(b"data" * 100)
    gone.write_bytes(b"data" * 200)
    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)

    watcher.scan_existing()
    watcher.scan_existing()
    assert set(store.seen_fingerprints()) == {str(keep), str(gone)}

    gone.unlink()
    watcher.scan_existing()
    assert set(store.seen_fingerprints()) == {str(keep)}


def test_an_unreadable_watch_root_does_not_prune_its_records(store, tmp_path):
    """An unmounted share walks as empty. Pruning on that would discard every
    record it holds and re-detect the whole share on remount."""
    root = tmp_path / "media"
    root.mkdir()
    target = root / "movie.mkv"
    target.write_bytes(b"data" * 100)

    planner = Planner(store)
    watcher = EncoderWatcher(
        store=store,
        on_settled=planner,
        paths=[str(root)],
        settle_seconds=0,
        valid_extensions={".mkv", ".mp4"},
    )
    watcher.scan_existing()
    watcher.scan_existing()
    assert set(store.seen_fingerprints()) == {str(target)}

    # Simulate the mount disappearing: the root itself is gone.
    target.unlink()
    root.rmdir()
    watcher.scan_existing()
    assert set(store.seen_fingerprints()) == {str(target)}, \
        "records were pruned for a root that could not be read"


def test_a_fingerprint_written_during_the_scan_is_not_pruned(store, tmp_path):
    """The queue fingerprints a file the instant it publishes it, which can
    land after that file's directory was already walked.

    Reproduced by review: a job published movie.mp4 mid-scan, the new record
    was absent from the walk's results, and the prune deleted it -- so the
    next scan saw our own published output as a new arrival. For an MKV->MP4
    job that means re-encoding the encode.
    """
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"data" * 100)
    published = tmp_path / "movie.mp4"

    planner = Planner(store)
    watcher = make_watcher(store, tmp_path, planner)
    watcher.scan_existing()
    watcher.scan_existing()
    _finish(store, planner.job_ids[0], "done")

    real_walk = os.walk

    def walk_then_publish(top, **kwargs):
        """Publish after the directory has been walked, as the queue would."""
        for entry in real_walk(top, **kwargs):
            yield entry
        published.write_bytes(b"encoded" * 10)
        st = published.stat()
        store.mark_seen(str(published), st.st_size, st.st_mtime_ns)

    import app.encoder.watcher as watcher_mod
    original = watcher_mod.os.walk
    watcher_mod.os.walk = walk_then_publish
    try:
        watcher.scan_existing()
    finally:
        watcher_mod.os.walk = original

    assert str(published) in store.seen_fingerprints(), \
        "a fingerprint written mid-scan was pruned"

    # And therefore the published output is never treated as a new arrival.
    watcher.scan_existing()
    watcher.scan_existing()
    assert planner.paths == [str(source)]


def test_an_unreadable_subtree_does_not_prune_its_root(store, tmp_path):
    """os.walk swallows errors by default, so an unreadable subtree looks
    identical to an empty one -- and every file under it looks deleted.
    A transient read error on a mounted share must not cost its fingerprints.
    """
    root = tmp_path / "media"
    sub = root / "Movies"
    sub.mkdir(parents=True)
    target = sub / "movie.mkv"
    target.write_bytes(b"data" * 100)

    planner = Planner(store)
    watcher = EncoderWatcher(
        store=store,
        on_settled=planner,
        paths=[str(root)],
        settle_seconds=0,
        valid_extensions={".mkv", ".mp4"},
    )
    watcher.scan_existing()
    watcher.scan_existing()
    assert set(store.seen_fingerprints()) == {str(target)}

    real_walk = os.walk

    def walk_with_error(top, onerror=None, **kwargs):
        """Yield the root, then report the subtree as unreadable."""
        yield (str(root), ["Movies"], [])
        if onerror is not None:
            err = OSError(13, "Permission denied")
            err.filename = str(sub)
            onerror(err)

    import app.encoder.watcher as watcher_mod
    original = watcher_mod.os.walk
    watcher_mod.os.walk = walk_with_error
    try:
        watcher.scan_existing()
    finally:
        watcher_mod.os.walk = original

    assert set(store.seen_fingerprints()) == {str(target)}, \
        "fingerprints were pruned after a walk error"
