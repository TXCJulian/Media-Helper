import queue

import pytest

from app.encoder.events import EventBroadcaster, reprocess_to_payload
from app.encoder.presets import NamedPreset
from app.encoder.queue import EncodeQueue
from app.encoder.rules import Condition, Rule
from app.encoder.runtime import EncoderRuntime
from app.encoder.store import EncoderStore


class IdleWatcher:
    """Avoid watchdog threads while exercising the runtime's reprocess path."""

    def __init__(self, _store, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _events_for_run(subscription, run_id):
    events = []
    while True:
        event = subscription.get(timeout=2)
        if event.get("type") == "reprocess" and event["run_id"] == run_id:
            events.append(event)
            if event["status"] in {"completed", "failed"}:
                return events


def _wait_for_terminal(subscription, run_id):
    return _events_for_run(subscription, run_id)[-1]


@pytest.fixture
def runtime_env(tmp_path, monkeypatch):
    import app.config as config
    import app.encoder.queue as queue_mod
    import app.encoder.runtime as runtime_mod

    monkeypatch.setattr(config, "BASE_PATHS", [str(tmp_path)])
    monkeypatch.setattr(runtime_mod, "EncoderWatcher", IdleWatcher)
    monkeypatch.setattr(
        queue_mod,
        "probe",
        lambda _path: {"height": 1080, "video_codec": "h264"},
    )
    store = EncoderStore(str(tmp_path / "encoder.db"))
    store.replace_presets(
        [
            NamedPreset(
                "NVENC",
                "nvenc_h265",
                "medium",
                "av_mkv",
                {"PresetName": "NVENC", "VideoEncoder": "nvenc_h265"},
            )
        ]
    )
    store.replace_rules([Rule("r1", [Condition("height", ">=", 720)], "NVENC")])
    root = tmp_path / "Movies"
    root.mkdir()
    events = EventBroadcaster()

    def build(mode="review", paths=None):
        encoder_queue = EncodeQueue(
            store,
            object(),
            events,
            mode=mode,
            holding_dir=str(tmp_path / "originals"),
        )
        runtime = EncoderRuntime(
            store,
            encoder_queue,
            default_paths=list(paths) if paths is not None else [str(root)],
            settle_seconds=0,
            valid_extensions={".mkv"},
        )
        runtime.start()
        return runtime, encoder_queue

    yield store, root, events, build
    store.close()


def test_bulk_reprocess_reconsiders_a_previously_skipped_file(runtime_env):
    """Removing the reprocess call would leave this skipped source suppressed."""
    store, root, events, build = runtime_env
    source = root / "movie.mkv"
    source.write_bytes(b"video")
    old = store.create_job(str(source), source.stat().st_size, source.stat().st_mtime_ns)
    store.set_stage(old.id, "skipped")
    store.mark_seen(str(source), source.stat().st_size, source.stat().st_mtime_ns)
    runtime, _queue = build()

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    run_events = _events_for_run(subscription, run["run_id"])
    terminal = run_events[-1]

    assert [event["status"] for event in run_events] == [
        "started",
        "running",
        "completed",
    ]
    assert all(
        set(event) == {
            "type", "run_id", "status", "scanned", "created", "skipped",
            "failed", "path", "error",
        }
        for event in run_events
    )
    assert terminal == {
        "type": "reprocess",
        "run_id": run["run_id"],
        "status": "completed",
        "scanned": 1,
        "created": 1,
        "skipped": 0,
        "failed": 0,
        "path": None,
        "error": None,
    }
    assert len(store.list_jobs()) == 2
    assert store.newest_job_for_source(str(source)).stage == "pending"


def test_bulk_reprocess_leaves_an_active_source_as_a_single_job(runtime_env):
    """A manager that creates despite an active queue row would duplicate work."""
    store, root, events, build = runtime_env
    source = root / "active.mkv"
    source.write_bytes(b"video")
    active = store.create_job(str(source), source.stat().st_size, source.stat().st_mtime_ns)
    runtime, _queue = build()

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["created"] == 0
    assert terminal["skipped"] == 1
    assert len(store.list_jobs()) == 1
    assert store.active_job_for_source(str(source)).id == active.id


def test_bulk_reprocess_uses_the_picker_exclusions(runtime_env):
    """Walking hidden or top-level music trees would plan files the picker hides."""
    store, root, events, build = runtime_env
    (root / ".cache").mkdir()
    (root / ".cache" / "hidden.mkv").write_bytes(b"video")
    (root / "Music").mkdir()
    (root / "Music" / "song.mkv").write_bytes(b"video")
    (root / "visible.mkv").write_bytes(b"video")
    runtime, _queue = build()

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["scanned"] == 1
    assert [job.source_path for job in store.list_jobs()] == [str(root / "visible.mkv")]


@pytest.mark.parametrize("excluded", ["Music", ".hidden", ".trickplay"])
def test_bulk_reprocess_ignores_a_watch_root_inside_an_excluded_tree(
    runtime_env, excluded
):
    """A root inside a picker-hidden tree must not become an escape hatch."""
    store, root, events, build = runtime_env
    excluded_root = root.parent / excluded / "nested"
    excluded_root.mkdir(parents=True)
    (excluded_root / "secret.mkv").write_bytes(b"video")
    runtime, _queue = build(paths=[str(excluded_root)])

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["scanned"] == 0
    assert store.list_jobs() == []


def test_bulk_reprocess_skips_a_file_symlink_outside_its_watch_root(runtime_env):
    """Following a file symlink could probe and encode outside the chosen root."""
    store, root, events, build = runtime_env
    outside = root.parent / "outside.mkv"
    outside.write_bytes(b"video")
    link = root / "escape.mkv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")
    runtime, _queue = build()

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["scanned"] == 0
    assert store.list_jobs() == []


def test_bulk_reprocess_deduplicates_nested_watch_roots(runtime_env):
    """Overlapping roots must not inflate the scan or re-evaluate one file twice."""
    store, root, events, build = runtime_env
    nested = root / "nested"
    nested.mkdir()
    source = nested / "once.mkv"
    source.write_bytes(b"video")
    runtime, _queue = build(paths=[str(root), str(nested)])

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["scanned"] == 1
    assert len(store.list_jobs()) == 1


def test_bulk_reprocess_counts_a_failed_plan_as_failed(runtime_env):
    """A created row that fails planning must not look like a successful plan."""
    store, root, events, build = runtime_env
    source = root / "missing-preset.mkv"
    source.write_bytes(b"video")
    store.replace_rules([Rule("missing", [], "not-installed")])
    runtime, _queue = build()

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["created"] == 0
    assert terminal["failed"] == 1
    assert store.newest_job_for_source(str(source)).stage == "failed"


@pytest.mark.parametrize("mode, stage", [("review", "pending"), ("auto", "queued")])
def test_bulk_reprocess_uses_the_queue_mode(runtime_env, mode, stage):
    """Bypassing the queue would ignore the configured review/auto safety mode."""
    store, root, events, build = runtime_env
    source = root / "mode.mkv"
    source.write_bytes(b"video")
    runtime, _queue = build(mode)

    subscription = events.subscribe()
    run = runtime.start_reprocess_all()
    terminal = _wait_for_terminal(subscription, run["run_id"])

    assert terminal["created"] == 1
    assert store.newest_job_for_source(str(source)).stage == stage


def test_bulk_reprocess_returns_the_running_run_id(runtime_env, monkeypatch):
    """A second start must join the in-flight scan, not launch another walk."""
    _store, root, events, build = runtime_env
    source = root / "slow.mkv"
    source.write_bytes(b"video")
    runtime, encoder_queue = build()
    started = queue.Queue()
    release = queue.Queue()
    original = encoder_queue.reprocess_path

    def slow_reprocess(path):
        started.put(path)
        release.get(timeout=2)
        return original(path)

    monkeypatch.setattr(encoder_queue, "reprocess_path", slow_reprocess)

    subscription = events.subscribe()
    first = runtime.start_reprocess_all()
    assert started.get(timeout=2) == str(source)
    assert runtime.start_reprocess_all() == first
    release.put(None)
    assert _wait_for_terminal(subscription, first["run_id"])["status"] == "completed"


def test_runtime_stop_joins_the_active_bulk_reprocess(runtime_env, monkeypatch):
    """Stopping the runtime must leave no planner thread behind the queue."""
    _store, root, _events, build = runtime_env
    (root / "first.mkv").write_bytes(b"video")
    (root / "second.mkv").write_bytes(b"video")
    runtime, encoder_queue = build()
    started = queue.Queue()
    release = queue.Queue()
    original = encoder_queue.reprocess_path

    def slow_reprocess(path):
        started.put(path)
        release.get(timeout=2)
        return original(path)

    monkeypatch.setattr(encoder_queue, "reprocess_path", slow_reprocess)
    runtime.start_reprocess_all()
    assert started.get(timeout=2) == str(root / "first.mkv")

    try:
        runtime.stop()
        assert not runtime._reprocess._thread.is_alive()
    finally:
        release.put(None)
        runtime._reprocess._thread.join(timeout=2)


@pytest.mark.parametrize(
    "field, value",
    [
        ("run_id", 1),
        ("scanned", True),
        ("created", True),
        ("skipped", True),
        ("failed", True),
    ],
)
def test_reprocess_events_reject_non_string_ids_and_boolean_counts(field, value):
    """JSON-looking truthy values must not bypass the declared SSE schema."""
    payload = {
        "run_id": "run-1",
        "status": "started",
        "scanned": 0,
        "created": 0,
        "skipped": 0,
        "failed": 0,
    }
    payload[field] = value

    with pytest.raises(ValueError):
        reprocess_to_payload(**payload)
