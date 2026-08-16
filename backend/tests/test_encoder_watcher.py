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
    watcher = make_watcher(store, tmp_path, settled.append)

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
    watcher = make_watcher(store, tmp_path, settled.append)

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
    watcher = make_watcher(store, tmp_path, settled.append)

    watcher.scan_existing()
    watcher.scan_existing()

    assert settled == []


def test_files_with_an_active_job_are_not_redispatched(store, tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"data")
    store.create_job(str(target))  # active source path (stage "settling")
    settled = []
    watcher = make_watcher(store, tmp_path, settled.append)

    watcher.scan_existing()
    watcher.scan_existing()

    assert settled == []


def test_non_video_extensions_are_ignored(store, tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"data")
    settled = []
    watcher = make_watcher(store, tmp_path, settled.append)

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
    watcher = make_watcher(store, tmp_path, settled.append)

    watcher._consider(str(target))  # first sight: tracker now holds an entry
    target.unlink()
    watcher._consider(str(target))  # must not raise, and must not dispatch

    assert settled == []
    assert str(target) not in watcher._tracker._seen


def test_start_and_stop_leave_no_thread_running(store, tmp_path):
    watcher = make_watcher(store, tmp_path, lambda path: None)
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
    watcher = make_watcher(store, tmp_path, lambda path: None)

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
        on_settled=lambda path: None,
        paths=[],
        settle_seconds=0,
        valid_extensions={".mkv"},
    )
    watcher.start()
    assert watcher._observer is None
    assert watcher._scanner is None
    watcher.stop()  # must be safe to call even though start() was a no-op
