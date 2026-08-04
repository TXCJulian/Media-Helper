# Downloader Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the downloader feature with a worker-owned, queue-backed implementation whose job model tells the truth about multi-file outputs, cancellation, and transcoding progress.

**Architecture:** A SQLite-backed job store is the single writer of state. A worker pool started at application lifespan drains a persistent queue — no work ever begins inside a request handler. Each job runs a download stage (yt-dlp) and an optional transcode stage (ffmpeg, killable, with real progress), writing per-item rows. A single server-sent event stream broadcasts state changes to one reconnecting client-side stream.

**Tech Stack:** Python 3.14, FastAPI, yt-dlp, ffmpeg (jellyfin-ffmpeg7 in the image), SQLite (stdlib `sqlite3`), pytest. React 19, TypeScript, Tailwind CSS 4, Vitest.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-08-04-downloader-rewrite-design.md`.
- All new backend code lives in `backend/app/downloader/`. `backend/app/download.py` is deleted in Task 7, not before.
- The whole downloader router is registered only when `"download" in ENABLED_FEATURES_SET`, so no endpoint exists when the feature is off.
- **Deliberate deviation from the spec:** the spec lists `GET /download/jobs/{id}/events` (a per-job stream). No client consumes it — the panel holds one all-jobs stream — so it is not built. Add it only if a caller appears.
- Accent colour is cyan `#22d3ee` as `--accent-6`; amber `--accent-5` must no longer be referenced by downloader code.
- Stage enum, used verbatim everywhere (backend and frontend): `queued`, `downloading`, `transcoding`, `done`, `cancelled`, `error`.
- Cancellation always produces stage `cancelled`, never `error`.
- No network and no real encoding in tests — yt-dlp and `subprocess` are mocked.
- Path containment: every resolved output path must sit inside `DOWNLOADS_DIR` or one of `BASE_PATHS`, checked with `os.path.realpath`.
- Existing files are never overwritten; collisions get a numeric suffix.
- Backend tests run with `cd backend && python -m pytest`. Frontend tests run with `cd frontend && npm run test`.
- Follow the existing test convention in `backend/tests/`: monkeypatch `app.config` attributes, then `importlib.reload` the module under test.

---

## File Structure

**Backend — created:**

| File | Responsibility |
| --- | --- |
| `backend/app/downloader/__init__.py` | Package exports |
| `backend/app/downloader/store.py` | SQLite job/item persistence; sole writer of state |
| `backend/app/downloader/events.py` | In-process pub/sub broadcaster for SSE |
| `backend/app/downloader/ydl.py` | yt-dlp option building, path resolution, collision handling |
| `backend/app/downloader/transcode.py` | ffmpeg re-encode stage with progress and kill |
| `backend/app/downloader/runner.py` | Executes one job: download stage then optional transcode stage |
| `backend/app/downloader/queue.py` | Worker pool, enqueue, cancel, restart recovery |
| `backend/app/downloader/routes.py` | FastAPI router for `/download/*` |

**Backend — modified:** `backend/app/config.py`, `backend/app/main.py`.
**Backend — deleted:** `backend/app/download.py`, `backend/tests/test_download.py` (replaced).

**Frontend — created:** `frontend/src/lib/eventStream.ts`, `frontend/src/hooks/useDownloadStream.ts`, `frontend/src/components/downloader/DownloadJobCard.tsx`, `frontend/src/components/downloader/DownloadOptions.tsx`.
**Frontend — modified:** `frontend/src/index.css`, `frontend/src/components/Landing.tsx`, `frontend/src/lib/api.ts`, `frontend/src/lib/sse.ts`, `frontend/src/types.ts`, `frontend/src/components/DownloaderPanel.tsx`.

---

### Task 1: Job store

**Files:**

- Create: `backend/app/downloader/__init__.py`, `backend/app/downloader/store.py`
- Test: `backend/tests/test_downloader_store.py`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `Item` dataclass: `index: int`, `title: str`, `path: str | None`, `size: int | None`, `progress: float`, `stage: str`, `error: str | None`
  - `Job` dataclass: `id: str`, `url: str`, `options: dict`, `stage: str`, `error: str | None`, `created_at: str`, `updated_at: str`, `items: list[Item]`
  - `JobStore(db_path: str, on_change: Callable[[Job], None] | None = None)` with methods:
    `create_job(url: str, options: dict, stage: str = "queued") -> str`,
    `get_job(job_id: str) -> Job | None`,
    `list_jobs(limit: int = 200) -> list[Job]`,
    `set_job_stage(job_id: str, stage: str, error: str | None = None) -> None`,
    `upsert_item(job_id: str, index: int, **fields) -> None`,
    `delete_job(job_id: str) -> bool`,
    `reset_active_to_queued() -> list[str]`,
    `purge_expired(ttl_seconds: int) -> int`
  - `STAGES: frozenset[str]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader_store.py`:

```python
import pytest

from app.downloader.store import Item, Job, JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "downloader.db"))


def test_create_and_get_job(store):
    job_id = store.create_job("https://example.com/v", {"type": "video"})
    job = store.get_job(job_id)
    assert job is not None
    assert job.url == "https://example.com/v"
    assert job.options == {"type": "video"}
    assert job.stage == "queued"
    assert job.items == []


def test_get_missing_job_returns_none(store):
    assert store.get_job("00000000-0000-0000-0000-000000000000") is None


def test_upsert_item_creates_then_updates(store):
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, title="Song A", stage="downloading", progress=10.0)
    store.upsert_item(job_id, 0, progress=55.5)

    items = store.get_job(job_id).items
    assert len(items) == 1
    assert items[0].title == "Song A"
    assert items[0].progress == 55.5
    assert items[0].stage == "downloading"


def test_multiple_items_keep_order(store):
    job_id = store.create_job("https://example.com/playlist", {})
    for i, title in enumerate(["A", "B", "C"]):
        store.upsert_item(job_id, i, title=title)
    assert [i.title for i in store.get_job(job_id).items] == ["A", "B", "C"]


def test_set_job_stage_is_not_read_modify_write(store):
    """Two independent writers must not lose each other's updates."""
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, progress=42.0)
    store.set_job_stage(job_id, "cancelled", error="Cancelled by user")

    job = store.get_job(job_id)
    assert job.stage == "cancelled"
    assert job.error == "Cancelled by user"
    assert job.items[0].progress == 42.0


def test_list_jobs_newest_first(store):
    first = store.create_job("https://example.com/1", {})
    second = store.create_job("https://example.com/2", {})
    assert [j.id for j in store.list_jobs()][:2] == [second, first]


def test_delete_job_removes_items(store):
    job_id = store.create_job("https://example.com/v", {})
    store.upsert_item(job_id, 0, title="A")
    assert store.delete_job(job_id) is True
    assert store.get_job(job_id) is None
    assert store.delete_job(job_id) is False


def test_reset_active_to_queued_recovers_after_restart(store):
    a = store.create_job("https://example.com/a", {})
    b = store.create_job("https://example.com/b", {})
    c = store.create_job("https://example.com/c", {})
    store.set_job_stage(a, "downloading")
    store.set_job_stage(b, "transcoding")
    store.set_job_stage(c, "done")

    recovered = store.reset_active_to_queued()

    assert set(recovered) == {a, b}
    assert store.get_job(a).stage == "queued"
    assert store.get_job(b).stage == "queued"
    assert store.get_job(c).stage == "done"


def test_purge_expired_keeps_recent_and_active(store):
    old_done = store.create_job("https://example.com/old", {})
    store.set_job_stage(old_done, "done")
    store._conn.execute(
        "UPDATE jobs SET created_at = datetime('now', '-10 days') WHERE id = ?",
        (old_done,),
    )
    store._conn.commit()
    fresh = store.create_job("https://example.com/fresh", {})

    removed = store.purge_expired(ttl_seconds=86400)

    assert removed == 1
    assert store.get_job(old_done) is None
    assert store.get_job(fresh) is not None


def test_on_change_fires_for_stage_and_item_updates():
    seen = []
    store = JobStore(":memory:", on_change=seen.append)
    job_id = store.create_job("https://example.com/v", {})
    store.set_job_stage(job_id, "downloading")
    store.upsert_item(job_id, 0, progress=1.0)

    assert len(seen) == 3
    assert all(isinstance(j, Job) for j in seen)
    assert seen[-1].items[0].progress == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.downloader'`

- [ ] **Step 3: Create the package marker**

Create `backend/app/downloader/__init__.py`:

```python
"""Downloader feature: queue-backed yt-dlp downloads with optional transcode."""
```

- [ ] **Step 4: Implement the store**

Create `backend/app/downloader/store.py`:

```python
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

STAGES = frozenset(
    {"queued", "downloading", "transcoding", "done", "cancelled", "error"}
)
ACTIVE_STAGES = frozenset({"queued", "downloading", "transcoding"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    options     TEXT NOT NULL,
    stage       TEXT NOT NULL,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS items (
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    path        TEXT,
    size        INTEGER,
    progress    REAL NOT NULL DEFAULT 0.0,
    stage       TEXT NOT NULL DEFAULT 'queued',
    error       TEXT,
    PRIMARY KEY (job_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

_ITEM_FIELDS = ("title", "path", "size", "progress", "stage", "error")


@dataclass
class Item:
    index: int
    title: str = ""
    path: str | None = None
    size: int | None = None
    progress: float = 0.0
    stage: str = "queued"
    error: str | None = None


@dataclass
class Job:
    id: str
    url: str
    options: dict[str, Any]
    stage: str
    error: str | None
    created_at: str
    updated_at: str
    items: list[Item] = field(default_factory=list)


class JobStore:
    """SQLite-backed job persistence. The only writer of downloader state."""

    def __init__(
        self, db_path: str, on_change: Callable[[Job], None] | None = None
    ) -> None:
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self._on_change = on_change

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _notify(self, job_id: str) -> None:
        if self._on_change is None:
            return
        job = self.get_job(job_id)
        if job is not None:
            self._on_change(job)

    def create_job(
        self, url: str, options: dict[str, Any], stage: str = "queued"
    ) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, url, options, stage) VALUES (?, ?, ?, ?)",
                (job_id, url, json.dumps(options), stage),
            )
            self._conn.commit()
        self._notify(job_id)
        return job_id

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            item_rows = self._conn.execute(
                "SELECT * FROM items WHERE job_id = ? ORDER BY idx", (job_id,)
            ).fetchall()
        return _to_job(row, item_rows)

    def list_jobs(self, limit: int = 200) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            item_rows = self._conn.execute(
                "SELECT * FROM items ORDER BY idx"
            ).fetchall()
        by_job: dict[str, list[sqlite3.Row]] = {}
        for item in item_rows:
            by_job.setdefault(item["job_id"], []).append(item)
        return [_to_job(row, by_job.get(row["id"], [])) for row in rows]

    def set_job_stage(
        self, job_id: str, stage: str, error: str | None = None
    ) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}")
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET stage = ?, error = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (stage, error, job_id),
            )
            self._conn.commit()
        self._notify(job_id)

    def upsert_item(self, job_id: str, index: int, **fields: Any) -> None:
        unknown = set(fields) - set(_ITEM_FIELDS)
        if unknown:
            raise ValueError(f"Unknown item fields: {sorted(unknown)}")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO items (job_id, idx) VALUES (?, ?)",
                (job_id, index),
            )
            if fields:
                assignments = ", ".join(f"{name} = ?" for name in fields)
                self._conn.execute(
                    f"UPDATE items SET {assignments} WHERE job_id = ? AND idx = ?",
                    (*fields.values(), job_id, index),
                )
            self._conn.execute(
                "UPDATE jobs SET updated_at = datetime('now') WHERE id = ?", (job_id,)
            )
            self._conn.commit()
        self._notify(job_id)

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def reset_active_to_queued(self) -> list[str]:
        """After a restart, return interrupted jobs to the queue."""
        placeholders = ", ".join("?" * len(ACTIVE_STAGES))
        active = sorted(ACTIVE_STAGES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id FROM jobs WHERE stage IN ({placeholders})", active
            ).fetchall()
            job_ids = [row["id"] for row in rows if row["id"]]
            self._conn.execute(
                f"UPDATE jobs SET stage = 'queued', error = NULL "
                f"WHERE stage IN ({placeholders})",
                active,
            )
            self._conn.commit()
        return [jid for jid in job_ids]

    def purge_expired(self, ttl_seconds: int) -> int:
        placeholders = ", ".join("?" * len(ACTIVE_STAGES))
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM jobs WHERE stage NOT IN ({placeholders}) "
                f"AND created_at < datetime('now', ?)",
                (*sorted(ACTIVE_STAGES), f"-{int(ttl_seconds)} seconds"),
            )
            self._conn.commit()
            return cursor.rowcount


def _to_job(row: sqlite3.Row, item_rows: list[sqlite3.Row]) -> Job:
    return Job(
        id=row["id"],
        url=row["url"],
        options=json.loads(row["options"]),
        stage=row["stage"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        items=[
            Item(
                index=item["idx"],
                title=item["title"],
                path=item["path"],
                size=item["size"],
                progress=item["progress"],
                stage=item["stage"],
                error=item["error"],
            )
            for item in item_rows
        ],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader_store.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/downloader/__init__.py backend/app/downloader/store.py backend/tests/test_downloader_store.py
git commit -m "feat(downloader): add SQLite job store with job -> items model"
```

---

### Task 2: Event broadcaster

**Files:**

- Create: `backend/app/downloader/events.py`
- Test: `backend/tests/test_downloader_events.py`

**Interfaces:**

- Consumes: `Job` from Task 1.
- Produces: `EventBroadcaster` with `subscribe() -> queue.Queue[dict]`, `unsubscribe(q) -> None`, `publish(event: dict) -> None`, `subscriber_count() -> int`; and `job_to_payload(job: Job) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader_events.py`:

```python
from app.downloader.events import EventBroadcaster, job_to_payload
from app.downloader.store import Item, Job


def _job() -> Job:
    return Job(
        id="abc",
        url="https://example.com/v",
        options={"type": "video"},
        stage="downloading",
        error=None,
        created_at="2026-08-04 10:00:00",
        updated_at="2026-08-04 10:00:01",
        items=[Item(index=0, title="A", progress=12.5, stage="downloading")],
    )


def test_job_to_payload_shape():
    payload = job_to_payload(_job())
    assert payload["job_id"] == "abc"
    assert payload["stage"] == "downloading"
    assert payload["items"][0]["progress"] == 12.5
    assert "options" not in payload


def test_publish_reaches_all_subscribers():
    bus = EventBroadcaster()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"job_id": "abc"})
    assert a.get_nowait() == {"job_id": "abc"}
    assert b.get_nowait() == {"job_id": "abc"}


def test_unsubscribe_stops_delivery():
    bus = EventBroadcaster()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish({"job_id": "abc"})
    assert q.empty()
    assert bus.subscriber_count() == 0


def test_slow_subscriber_does_not_block_publisher():
    bus = EventBroadcaster(maxsize=2)
    q = bus.subscribe()
    for i in range(10):
        bus.publish({"n": i})
    assert q.qsize() == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.downloader.events'`

- [ ] **Step 3: Implement the broadcaster**

Create `backend/app/downloader/events.py`:

```python
import queue
import threading
from dataclasses import asdict
from typing import Any

from app.downloader.store import Job


def job_to_payload(job: Job) -> dict[str, Any]:
    """Serialise a job for the wire. Options stay server-side."""
    return {
        "job_id": job.id,
        "url": job.url,
        "stage": job.stage,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "items": [asdict(item) for item in job.items],
    }


class EventBroadcaster:
    """Fan-out of job state changes to connected SSE clients.

    Publishing never blocks: a subscriber that cannot keep up drops its
    oldest event. Authoritative state always remains in the store, so a
    client that misses a delta still converges on reconnect.
    """

    def __init__(self, maxsize: int = 200) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader_events.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/downloader/events.py backend/tests/test_downloader_events.py
git commit -m "feat(downloader): add non-blocking event broadcaster"
```

---

### Task 3: yt-dlp option building and path resolution

**Files:**

- Create: `backend/app/downloader/ydl.py`
- Test: `backend/tests/test_downloader_ydl.py`

**Interfaces:**

- Consumes: `app.config.resolve_base`, `app.config.DOWNLOADS_DIR`, `app.config.BASE_PATHS`.
- Produces:
  - `safe_component(value: Any) -> str`
  - `unique_path(path: str) -> str`
  - `video_format_selector(quality: str) -> str`
  - `audio_format_selector(quality: str) -> str`
  - `resolve_output_root(options: dict) -> str`
  - `assert_within_allowed_roots(path: str) -> None` (raises `ValueError`)
  - `build_ydl_opts(options: dict, output_root: str, cookie_path: str | None) -> dict`
  - `needs_transcode(options: dict) -> bool`
  - `AUDIO_CONTAINERS`, `VIDEO_CONTAINERS`, `THUMBNAIL_FORMATS`, `VIDEO_CODECS`, `AUDIO_CODECS` (frozensets)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader_ydl.py`:

```python
import importlib

import pytest


@pytest.fixture
def ydl(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    media = tmp_path / "media"
    downloads.mkdir()
    (media / "Music").mkdir(parents=True)

    import app.config as config_mod

    monkeypatch.setattr(config_mod, "DOWNLOADS_DIR", str(downloads), raising=False)
    monkeypatch.setattr(config_mod, "BASE_PATHS", [str(media)], raising=False)
    monkeypatch.setattr(
        config_mod,
        "resolve_base",
        lambda label: str(media) if label == "media" else (_ for _ in ()).throw(
            ValueError(label)
        ),
        raising=False,
    )

    import app.downloader.ydl as ydl_mod

    importlib.reload(ydl_mod)
    ydl_mod._test_dirs = {"downloads": downloads, "media": media}
    return ydl_mod


def test_safe_component_strips_separators(ydl):
    assert ydl.safe_component("a/b\\c") == "a_b_c"
    assert ydl.safe_component("  spaced  ") == "spaced"
    assert ydl.safe_component(None) == ""
    assert ydl.safe_component("..") == "__"


def test_unique_path_suffixes_collisions(ydl, tmp_path):
    target = tmp_path / "song.mp3"
    assert ydl.unique_path(str(target)) == str(target)

    target.write_bytes(b"x")
    assert ydl.unique_path(str(target)) == str(tmp_path / "song (1).mp3")

    (tmp_path / "song (1).mp3").write_bytes(b"x")
    assert ydl.unique_path(str(target)) == str(tmp_path / "song (2).mp3")


def test_video_format_selector(ydl):
    assert ydl.video_format_selector("1080p") == "bv*[height<=1080]+ba/b[height<=1080]"
    assert ydl.video_format_selector("best") == "bv*+ba/best"
    assert ydl.video_format_selector("worst") == "wv*+wa/worst"
    assert ydl.video_format_selector("nonsense") == "bv*+ba/best"


def test_audio_format_selector(ydl):
    assert ydl.audio_format_selector("192kbps") == "bestaudio[abr<=192]/bestaudio/best"
    assert ydl.audio_format_selector("best") == "bestaudio/best"
    assert ydl.audio_format_selector("worst") == "worstaudio/worst"


def test_resolve_output_root_defaults_to_downloads_dir(ydl):
    root = ydl.resolve_output_root({})
    assert root == str(ydl._test_dirs["downloads"].resolve())


def test_resolve_output_root_uses_base_and_subfolder(ydl):
    root = ydl.resolve_output_root(
        {"base": "media", "output_dir": "Music", "sub_folder": "Albums"}
    )
    assert root.endswith("Music" + __import__("os").sep + "Albums")


def test_resolve_output_root_rejects_traversal(ydl):
    with pytest.raises(ValueError):
        ydl.resolve_output_root(
            {"base": "media", "output_dir": "Music", "sub_folder": "../../etc"}
        )


def test_assert_within_allowed_roots(ydl, tmp_path):
    inside = ydl._test_dirs["downloads"] / "file.mp4"
    ydl.assert_within_allowed_roots(str(inside))

    with pytest.raises(ValueError):
        ydl.assert_within_allowed_roots(str(tmp_path / "elsewhere" / "file.mp4"))


def test_build_ydl_opts_video_container_only_never_transcodes(ydl):
    opts = ydl.build_ydl_opts(
        {"type": "video", "format": "mkv", "quality": "1080p"}, "/tmp/out", None
    )
    assert opts["merge_output_format"] == "mkv"
    assert "postprocessors" not in opts
    assert opts["overwrites"] is False


def test_build_ydl_opts_audio_extracts_with_codec(ydl):
    opts = ydl.build_ydl_opts(
        {"type": "audio", "format": "mp3", "quality": "320kbps"}, "/tmp/out", None
    )
    pp = opts["postprocessors"][0]
    assert pp["key"] == "FFmpegExtractAudio"
    assert pp["preferredcodec"] == "mp3"
    assert pp["preferredquality"] == "320"


def test_build_ydl_opts_thumbnail_skips_download(ydl):
    opts = ydl.build_ydl_opts({"type": "thumbnail", "format": "png"}, "/tmp/out", None)
    assert opts["skip_download"] is True
    assert opts["writethumbnail"] is True
    assert opts["postprocessors"][0]["key"] == "FFmpegThumbnailsConvertor"


def test_build_ydl_opts_applies_cookies_and_playlist_limit(ydl, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape")
    opts = ydl.build_ydl_opts({"item_limit": 5}, "/tmp/out", str(cookies))
    assert opts["cookiefile"] == str(cookies)
    assert opts["playlistend"] == 5


def test_build_ydl_opts_custom_filename_and_prefix(ydl):
    opts = ydl.build_ydl_opts(
        {"custom_prefix": "SSIO_", "custom_filename": "track"}, "/tmp/out", None
    )
    assert opts["outtmpl"].endswith("SSIO_track.%(ext)s")


def test_needs_transcode_only_when_codec_requested(ydl):
    assert ydl.needs_transcode({"type": "video", "codec": "h265"}) is True
    assert ydl.needs_transcode({"type": "video", "codec": "auto"}) is False
    assert ydl.needs_transcode({"type": "video"}) is False
    assert ydl.needs_transcode({"type": "thumbnail", "codec": "h265"}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_ydl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.downloader.ydl'`

- [ ] **Step 3: Implement option building**

Create `backend/app/downloader/ydl.py`:

```python
import os
from typing import Any

from app.config import BASE_PATHS, DOWNLOADS_DIR, resolve_base

VIDEO_CONTAINERS = frozenset({"mp4", "mkv", "webm", "mov"})
AUDIO_CONTAINERS = frozenset({"mp3", "m4a", "flac", "opus", "wav", "aac"})
THUMBNAIL_FORMATS = frozenset({"jpg", "png", "webp"})
VIDEO_CODECS = frozenset({"h264", "h265", "vp9", "av1"})
AUDIO_CODECS = frozenset({"mp3", "flac", "aac", "opus", "wav"})

_QUALITY_HEIGHTS = {
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
}
_AUDIO_QUALITY_KBPS = {
    "320kbps": 320,
    "256kbps": 256,
    "192kbps": 192,
    "128kbps": 128,
    "96kbps": 96,
}


def safe_component(value: Any) -> str:
    """Reduce a user-supplied name fragment to a single safe path component."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    for sep in ("/", "\\", os.sep):
        raw = raw.replace(sep, "_")
    if set(raw) == {"."}:
        raw = "_" * len(raw)
    return raw


def unique_path(path: str) -> str:
    """Return `path`, or the first free ` (n)` variant if it already exists."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    counter = 1
    while True:
        candidate = f"{stem} ({counter}){ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def video_format_selector(quality: str) -> str:
    key = str(quality or "best").lower()
    if key == "worst":
        return "wv*+wa/worst"
    height = _QUALITY_HEIGHTS.get(key)
    if height:
        return f"bv*[height<={height}]+ba/b[height<={height}]"
    return "bv*+ba/best"


def audio_format_selector(quality: str) -> str:
    key = str(quality or "best").lower()
    if key == "worst":
        return "worstaudio/worst"
    kbps = _AUDIO_QUALITY_KBPS.get(key)
    if kbps:
        return f"bestaudio[abr<={kbps}]/bestaudio/best"
    return "bestaudio/best"


def _contain(root: str, relative: str) -> str:
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relative))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ValueError("Path escapes its allowed root")
    return target


def allowed_roots() -> list[str]:
    return [os.path.realpath(DOWNLOADS_DIR)] + [
        os.path.realpath(p) for p in BASE_PATHS
    ]


def assert_within_allowed_roots(path: str) -> None:
    resolved = os.path.realpath(path)
    for root in allowed_roots():
        if resolved == root or resolved.startswith(root + os.sep):
            return
    raise ValueError(f"Path is outside the allowed roots: {path}")


def resolve_output_root(options: dict[str, Any]) -> str:
    base_label = str(options.get("base") or "").strip()
    output_dir = str(options.get("output_dir") or "").strip()
    sub_folder = str(options.get("sub_folder") or "").strip()

    if base_label and output_dir:
        root = _contain(resolve_base(base_label), output_dir)
    else:
        root = os.path.realpath(DOWNLOADS_DIR)

    if sub_folder:
        root = _contain(root, sub_folder)

    assert_within_allowed_roots(root)
    return root


def needs_transcode(options: dict[str, Any]) -> bool:
    """A re-encode runs only when a concrete codec was chosen for A/V."""
    media_type = str(options.get("type") or "video").lower()
    if media_type == "thumbnail":
        return False
    codec = str(options.get("codec") or "").lower()
    if codec in ("", "auto"):
        return False
    return codec in VIDEO_CODECS or codec in AUDIO_CODECS


def build_ydl_opts(
    options: dict[str, Any], output_root: str, cookie_path: str | None
) -> dict[str, Any]:
    media_type = str(options.get("type") or "video").lower()
    container = str(options.get("format") or "").lower()
    quality = str(options.get("quality") or "best")
    prefix = safe_component(options.get("custom_prefix"))
    filename = safe_component(options.get("custom_filename"))

    stem = f"{prefix}{filename}" if filename else f"{prefix}%(title)s"
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "overwrites": False,
        "outtmpl": os.path.join(output_root, f"{stem}.%(ext)s"),
        "progress_hooks": [],
        "postprocessor_hooks": [],
    }

    if cookie_path and os.path.isfile(cookie_path):
        opts["cookiefile"] = cookie_path

    try:
        item_limit = int(options.get("item_limit") or 0)
    except (TypeError, ValueError):
        item_limit = 0
    if item_limit > 0:
        opts["playlistend"] = item_limit

    if media_type == "audio":
        opts["format"] = audio_format_selector(quality)
        pp: dict[str, Any] = {"key": "FFmpegExtractAudio"}
        pp["preferredcodec"] = (
            container if container in AUDIO_CONTAINERS else "best"
        )
        kbps = _AUDIO_QUALITY_KBPS.get(quality.lower())
        if kbps:
            pp["preferredquality"] = str(kbps)
        opts["postprocessors"] = [pp]
    elif media_type == "thumbnail":
        opts["format"] = "best"
        opts["skip_download"] = True
        opts["writethumbnail"] = True
        if container in THUMBNAIL_FORMATS:
            opts["postprocessors"] = [
                {"key": "FFmpegThumbnailsConvertor", "format": container}
            ]
    else:
        opts["format"] = video_format_selector(quality)
        if container in VIDEO_CONTAINERS:
            opts["merge_output_format"] = container

    return opts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader_ydl.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/downloader/ydl.py backend/tests/test_downloader_ydl.py
git commit -m "feat(downloader): add yt-dlp option building with containment and collision handling"
```

---

### Task 4: Transcode stage

**Files:**

- Create: `backend/app/downloader/transcode.py`
- Test: `backend/tests/test_downloader_transcode.py`

**Interfaces:**

- Consumes: `app.hwaccel.build_video_encode_args`, `app.hwaccel.get_hwaccel_input_args`, `unique_path` from Task 3.
- Produces:
  - `CODEC_TO_ENCODER: dict[str, str]`
  - `probe_duration(path: str) -> float`
  - `build_transcode_command(src: str, dst: str, codec: str) -> list[str]`
  - `transcode_file(src, dst, codec, cancel_event, on_progress) -> None` — raises `TranscodeCancelled` or `RuntimeError`
  - `TranscodeCancelled(RuntimeError)`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader_transcode.py`:

```python
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.downloader import transcode


def test_build_transcode_command_video_codec():
    cmd = transcode.build_transcode_command("/in.mp4", "/out.mkv", "h265")
    assert cmd[0] == "ffmpeg"
    assert "/in.mp4" in cmd
    assert cmd[-1] == "/out.mkv"
    assert "libx265" in cmd or "hevc" in " ".join(cmd)
    assert "-progress" in cmd


def test_build_transcode_command_audio_codec():
    cmd = transcode.build_transcode_command("/in.webm", "/out.flac", "flac")
    assert "-vn" in cmd
    assert "flac" in " ".join(cmd)


def test_build_transcode_command_rejects_unknown_codec():
    with pytest.raises(ValueError):
        transcode.build_transcode_command("/in.mp4", "/out.mp4", "notacodec")


def _fake_proc(lines, returncode=0):
    proc = MagicMock()
    proc.stdout = iter(lines)
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    return proc


def test_transcode_reports_progress_from_ffmpeg_output():
    seen: list[float] = []
    lines = [
        "out_time_ms=5000000\n",
        "out_time_ms=10000000\n",
        "progress=end\n",
    ]

    with patch.object(transcode, "probe_duration", return_value=20.0), patch(
        "subprocess.Popen", return_value=_fake_proc(lines)
    ):
        transcode.transcode_file(
            "/in.mp4", "/out.mkv", "h265", threading.Event(), seen.append
        )

    assert seen[0] == pytest.approx(25.0)
    assert seen[1] == pytest.approx(50.0)
    assert seen[-1] == 100.0


def test_transcode_raises_on_nonzero_exit():
    proc = _fake_proc(["out_time_ms=1000000\n"], returncode=1)
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = "Encoder not found"

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(RuntimeError, match="Encoder not found"):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", threading.Event(), lambda _: None
            )


def test_transcode_kills_process_when_cancelled():
    cancel = threading.Event()
    cancel.set()
    proc = _fake_proc(["out_time_ms=1000000\n"])

    with patch.object(transcode, "probe_duration", return_value=10.0), patch(
        "subprocess.Popen", return_value=proc
    ):
        with pytest.raises(transcode.TranscodeCancelled):
            transcode.transcode_file(
                "/in.mp4", "/out.mkv", "h265", cancel, lambda _: None
            )

    proc.kill.assert_called_once()


def test_probe_duration_parses_ffprobe():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="123.45\n", stderr=""
    )
    with patch("subprocess.run", return_value=completed):
        assert transcode.probe_duration("/in.mp4") == pytest.approx(123.45)


def test_probe_duration_returns_zero_when_unknown():
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="boom"
    )
    with patch("subprocess.run", return_value=completed):
        assert transcode.probe_duration("/in.mp4") == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_transcode.py -v`
Expected: FAIL with `ImportError: cannot import name 'transcode'`

- [ ] **Step 3: Implement the transcode stage**

Create `backend/app/downloader/transcode.py`:

```python
import logging
import os
import subprocess
import threading
from typing import Callable

from app.hwaccel import build_video_encode_args, get_hwaccel_input_args

logger = logging.getLogger(__name__)

CODEC_TO_ENCODER = {
    "h264": "libx264",
    "h265": "libx265",
    "vp9": "libvpx-vp9",
    "av1": "libsvtav1",
}
AUDIO_CODEC_TO_ENCODER = {
    "mp3": "libmp3lame",
    "flac": "flac",
    "aac": "aac",
    "opus": "libopus",
    "wav": "pcm_s16le",
}


class TranscodeCancelled(RuntimeError):
    """Raised when the ffmpeg re-encode was cancelled by the user."""


def probe_duration(path: str) -> float:
    """Media duration in seconds, or 0.0 when it cannot be determined."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def build_transcode_command(src: str, dst: str, codec: str) -> list[str]:
    key = str(codec or "").lower()
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-y"]

    if key in CODEC_TO_ENCODER:
        encoder = CODEC_TO_ENCODER[key]
        hwaccel = get_hwaccel_input_args()
        if hwaccel and encoder != "libsvtav1":
            cmd += hwaccel
        cmd += ["-i", src]
        cmd += build_video_encode_args(encoder, crf="23")
        cmd += ["-c:a", "copy"]
    elif key in AUDIO_CODEC_TO_ENCODER:
        cmd += ["-i", src, "-vn", "-c:a", AUDIO_CODEC_TO_ENCODER[key]]
    else:
        raise ValueError(f"Unsupported transcode codec: {codec}")

    cmd.append(dst)
    return cmd


def transcode_file(
    src: str,
    dst: str,
    codec: str,
    cancel_event: threading.Event,
    on_progress: Callable[[float], None],
) -> None:
    """Re-encode `src` to `dst`, reporting 0-100 progress and honouring cancel.

    Progress is elapsed encoded time over total duration, read from ffmpeg's
    `-progress` stream. When the duration is unknown, progress stays at 0
    until the process finishes rather than reporting a fabricated value.
    """
    duration = probe_duration(src)
    cmd = build_transcode_command(src, dst, codec)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        for line in proc.stdout or []:
            if cancel_event.is_set():
                proc.kill()
                raise TranscodeCancelled("Cancelled by user")
            line = line.strip()
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(min(seconds / duration * 100.0, 100.0))
            elif line == "progress=end":
                on_progress(100.0)
    finally:
        if proc.poll() is None:
            proc.kill()
        returncode = proc.wait()

    if cancel_event.is_set():
        raise TranscodeCancelled("Cancelled by user")

    if returncode != 0:
        stderr = ""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read() or ""
            except (OSError, ValueError):
                stderr = ""
        _remove_partial(dst)
        raise RuntimeError(stderr.strip() or f"ffmpeg exited with code {returncode}")


def _remove_partial(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.warning("Could not remove partial transcode output %s", path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader_transcode.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/downloader/transcode.py backend/tests/test_downloader_transcode.py
git commit -m "feat(downloader): add cancellable transcode stage with real progress"
```

---

### Task 5: Job runner

**Files:**

- Create: `backend/app/downloader/runner.py`
- Test: `backend/tests/test_downloader_runner.py`

**Interfaces:**

- Consumes: `JobStore`, `Job` (Task 1); `build_ydl_opts`, `resolve_output_root`, `needs_transcode`, `unique_path`, `assert_within_allowed_roots` (Task 3); `transcode_file`, `TranscodeCancelled` (Task 4).
- Produces:
  - `DownloadCancelled(RuntimeError)`
  - `run_job(store: JobStore, job: Job, cancel_event: threading.Event, cookie_path: str | None = None) -> None`
  - `clean_error(text: str) -> str`
  - `_ydl_factory` module attribute (a callable returning a `YoutubeDL`), so tests can substitute a fake

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader_runner.py`:

```python
import os
import threading
from unittest.mock import patch

import pytest

from app.downloader import runner
from app.downloader.store import JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "d.db"))


class FakeYDL:
    """Stands in for yt-dlp: drives the registered hooks, then returns info."""

    def __init__(self, opts, events, info):
        self._opts = opts
        self._events = events
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        for event in self._events:
            for hook in self._opts["progress_hooks"]:
                hook(event)
        return self._info


def _install_fake_ydl(monkeypatch, events, info):
    monkeypatch.setattr(
        runner, "_ydl_factory", lambda opts: FakeYDL(opts, events, info)
    )


def test_single_video_job_reaches_done(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    media = out / "Video.mp4"
    media.write_bytes(b"x" * 2048)

    _install_fake_ydl(
        monkeypatch,
        events=[
            {"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 2048},
            {"status": "finished", "filename": str(media)},
        ],
        info={"title": "Video", "requested_downloads": [{"filepath": str(media)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert len(job.items) == 1
    assert job.items[0].path == str(media)
    assert job.items[0].size == 2048
    assert job.items[0].progress == 100.0


def test_playlist_creates_one_item_per_entry(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    paths = []
    for name in ("A.mp3", "B.mp3", "C.mp3"):
        p = out / name
        p.write_bytes(b"x" * 10)
        paths.append(str(p))

    _install_fake_ydl(
        monkeypatch,
        events=[],
        info={
            "entries": [
                {"title": "A", "requested_downloads": [{"filepath": paths[0]}]},
                {"title": "B", "requested_downloads": [{"filepath": paths[1]}]},
                {"title": "C", "requested_downloads": [{"filepath": paths[2]}]},
            ]
        },
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    job_id = store.create_job("https://example.com/list", {"type": "audio"})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert [i.title for i in job.items] == ["A", "B", "C"]
    assert [i.path for i in job.items] == paths
    assert all(i.stage == "done" for i in job.items)


def test_cancel_during_download_marks_cancelled(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    cancel = threading.Event()

    def cancelling_hook_events():
        cancel.set()
        return [{"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100}]

    _install_fake_ydl(monkeypatch, events=cancelling_hook_events(), info={})
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))

    job_id = store.create_job("https://example.com/v", {"type": "video"})
    runner.run_job(store, store.get_job(job_id), cancel)

    job = store.get_job(job_id)
    assert job.stage == "cancelled"
    assert job.error == "Cancelled by user"


def test_extractor_failure_is_recorded_as_error(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()

    class BoomYDL(FakeYDL):
        def extract_info(self, url, download=True):
            raise RuntimeError("\x1b[31mERROR: Unsupported URL\x1b[0m")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: BoomYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))

    job_id = store.create_job("https://example.com/nope", {})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "error"
    assert job.error == "ERROR: Unsupported URL"


def test_login_required_error_suggests_cookies(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()

    class LoginYDL(FakeYDL):
        def extract_info(self, url, download=True):
            raise RuntimeError("Sign in to confirm your age")

    monkeypatch.setattr(runner, "_ydl_factory", lambda opts: LoginYDL(opts, [], {}))
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))

    job_id = store.create_job("https://example.com/gated", {})
    runner.run_job(store, store.get_job(job_id), threading.Event())

    assert "cookies" in store.get_job(job_id).error.lower()


def test_transcode_stage_runs_and_replaces_path(store, tmp_path, monkeypatch):
    out = tmp_path / "downloads"
    out.mkdir()
    source = out / "Video.mp4"
    source.write_bytes(b"x" * 100)

    _install_fake_ydl(
        monkeypatch,
        events=[],
        info={"title": "Video", "requested_downloads": [{"filepath": str(source)}]},
    )
    monkeypatch.setattr(runner, "resolve_output_root", lambda options: str(out))
    monkeypatch.setattr(runner, "assert_within_allowed_roots", lambda path: None)

    def fake_transcode(src, dst, codec, cancel_event, on_progress):
        on_progress(50.0)
        with open(dst, "wb") as f:
            f.write(b"y" * 50)

    monkeypatch.setattr(runner, "transcode_file", fake_transcode)

    job_id = store.create_job(
        "https://example.com/v", {"type": "video", "codec": "h265", "format": "mkv"}
    )
    runner.run_job(store, store.get_job(job_id), threading.Event())

    job = store.get_job(job_id)
    assert job.stage == "done"
    assert job.items[0].path.endswith(".mkv")
    assert os.path.isfile(job.items[0].path)
    assert not os.path.isfile(str(source)), "source should be replaced after transcode"


def test_clean_error_strips_ansi():
    assert runner.clean_error("\x1b[0;31mboom\x1b[0m") == "boom"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_runner.py -v`
Expected: FAIL with `ImportError: cannot import name 'runner'`

- [ ] **Step 3: Implement the runner**

Create `backend/app/downloader/runner.py`:

```python
import logging
import os
import re
import threading
from typing import Any

from yt_dlp import YoutubeDL

from app.downloader.store import Job, JobStore
from app.downloader.transcode import TranscodeCancelled, transcode_file
from app.downloader.ydl import (
    assert_within_allowed_roots,
    build_ydl_opts,
    needs_transcode,
    resolve_output_root,
    unique_path,
)

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FORMAT_ID_RE = re.compile(r"\.f\d+(?=\.[^.]+$)")

_LOGIN_HINTS = (
    "sign in",
    "login required",
    "private video",
    "members-only",
    "age-restricted",
    "confirm your age",
)


class DownloadCancelled(RuntimeError):
    """Raised when a running download job is cancelled."""


def _ydl_factory(opts: dict[str, Any]):
    """Indirection point so tests can substitute a fake yt-dlp."""
    return YoutubeDL(opts)


def clean_error(text: str) -> str:
    return _ANSI_RE.sub("", str(text)).strip()


def _friendly_error(exc: Exception) -> str:
    message = clean_error(str(exc))
    lowered = message.lower()
    if any(hint in lowered for hint in _LOGIN_HINTS):
        return f"{message} — this URL needs cookies; upload a cookies.txt file."
    if "ffmpeg" in lowered and "not found" in lowered:
        return "ffmpeg is not available on the server."
    if "no space left" in lowered:
        return "The destination disk is full."
    if "requested format" in lowered or "no video formats" in lowered:
        return f"{message} — try a different quality setting."
    return message or "Download failed"


def _display_name(path: Any) -> str:
    if not path:
        return ""
    return _FORMAT_ID_RE.sub("", os.path.basename(str(path)))


def _entry_path(entry: dict[str, Any]) -> str | None:
    candidate = entry.get("filepath") or entry.get("_filename")
    if candidate and os.path.isfile(str(candidate)):
        return str(candidate)
    for download in entry.get("requested_downloads") or []:
        candidate = (download or {}).get("filepath") or (download or {}).get(
            "_filename"
        )
        if candidate and os.path.isfile(str(candidate)):
            return str(candidate)
    return None


def _entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise a yt-dlp info dict to a flat list of downloaded entries."""
    raw = info.get("entries")
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return [info] if info else []


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def run_job(
    store: JobStore,
    job: Job,
    cancel_event: threading.Event,
    cookie_path: str | None = None,
) -> None:
    """Run one job to completion. Never raises; terminal state lands in the store."""
    try:
        store.set_job_stage(job.id, "downloading")
        output_root = resolve_output_root(job.options)
        os.makedirs(output_root, exist_ok=True)
        opts = build_ydl_opts(job.options, output_root, cookie_path)
        opts["progress_hooks"].append(_make_hook(store, job.id, cancel_event))

        with _ydl_factory(opts) as ydl:
            info = ydl.extract_info(job.url, download=True)

        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")

        entries = _entries(info if isinstance(info, dict) else {})
        if not entries:
            raise RuntimeError("Download produced no output")

        recorded = 0
        for index, entry in enumerate(entries):
            path = _entry_path(entry)
            title = str(entry.get("title") or _display_name(path) or f"Item {index + 1}")
            if path is None:
                store.upsert_item(
                    job.id,
                    index,
                    title=title,
                    stage="error",
                    error="Output file could not be located",
                )
                continue
            assert_within_allowed_roots(path)
            store.upsert_item(
                job.id,
                index,
                title=title,
                path=path,
                size=_file_size(path),
                progress=100.0,
                stage="done",
            )
            recorded += 1

        if recorded == 0:
            store.set_job_stage(job.id, "error", "No output files were produced")
            return

        if needs_transcode(job.options):
            _run_transcode_stage(store, job, cancel_event)

        store.set_job_stage(job.id, "done")

    except (DownloadCancelled, TranscodeCancelled):
        store.set_job_stage(job.id, "cancelled", "Cancelled by user")
    except Exception as exc:
        logger.error("Download job %s failed: %s", job.id, exc, exc_info=True)
        store.set_job_stage(job.id, "error", _friendly_error(exc))


def _make_hook(store: JobStore, job_id: str, cancel_event: threading.Event):
    def hook(data: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")
        status = str(data.get("status") or "")
        index = int(data.get("playlist_index") or 1) - 1
        index = max(index, 0)
        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            progress = (downloaded / total * 100.0) if total else 0.0
            store.upsert_item(
                job_id,
                index,
                title=_display_name(data.get("filename")),
                progress=min(progress, 100.0),
                stage="downloading",
            )
        elif status == "finished":
            store.upsert_item(
                job_id,
                index,
                title=_display_name(data.get("filename")),
                progress=100.0,
                stage="downloading",
            )

    return hook


def _run_transcode_stage(
    store: JobStore, job: Job, cancel_event: threading.Event
) -> None:
    codec = str(job.options.get("codec") or "").lower()
    container = str(job.options.get("format") or "").lower()
    store.set_job_stage(job.id, "transcoding")

    for item in store.get_job(job.id).items:
        if item.stage != "done" or not item.path:
            continue

        extension = container or os.path.splitext(item.path)[1].lstrip(".")
        stem = os.path.splitext(item.path)[0]
        destination = unique_path(f"{stem}.{extension}")
        if os.path.realpath(destination) == os.path.realpath(item.path):
            destination = unique_path(f"{stem}.transcoded.{extension}")
        assert_within_allowed_roots(destination)

        store.upsert_item(job.id, item.index, stage="transcoding", progress=0.0)

        def report(percent: float, index: int = item.index) -> None:
            store.upsert_item(job.id, index, progress=percent)

        transcode_file(item.path, destination, codec, cancel_event, report)

        try:
            os.remove(item.path)
        except OSError:
            logger.warning("Could not remove pre-transcode source %s", item.path)

        store.upsert_item(
            job.id,
            item.index,
            path=destination,
            size=_file_size(destination),
            progress=100.0,
            stage="done",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader_runner.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/downloader/runner.py backend/tests/test_downloader_runner.py
git commit -m "feat(downloader): add job runner with per-item progress and transcode stage"
```

---

### Task 6: Worker queue

**Files:**

- Create: `backend/app/downloader/queue.py`
- Test: `backend/tests/test_downloader_queue.py`

**Interfaces:**

- Consumes: `JobStore` (Task 1).
- Produces: `DownloadQueue(store, runner, workers=3)` where `runner` is
  `Callable[[JobStore, Job, threading.Event], None]`, with methods
  `start()`, `stop(timeout: float = 10.0)`, `enqueue(job_id: str)`,
  `cancel(job_id: str) -> bool`, `is_active(job_id: str) -> bool`,
  `depth() -> int`, `recover()`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader_queue.py`:

```python
import threading
import time

import pytest

from app.downloader.queue import DownloadQueue
from app.downloader.store import JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "d.db"))


def test_jobs_beyond_worker_count_wait_instead_of_failing(store):
    started = threading.Semaphore(0)
    release = threading.Event()
    finished: list[str] = []

    def slow_runner(store_, job, cancel_event):
        started.release()
        release.wait(timeout=5)
        finished.append(job.id)
        store_.set_job_stage(job.id, "done")

    q = DownloadQueue(store, slow_runner, workers=2)
    q.start()
    try:
        job_ids = [store.create_job(f"https://example.com/{i}", {}) for i in range(5)]
        for job_id in job_ids:
            q.enqueue(job_id)

        assert started.acquire(timeout=5)
        assert started.acquire(timeout=5)
        time.sleep(0.2)
        assert len(finished) == 0, "only 2 workers should be running"
        assert q.depth() >= 1, "the rest must be waiting, not rejected"

        release.set()
        deadline = time.monotonic() + 10
        while len(finished) < 5 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(finished) == 5
    finally:
        release.set()
        q.stop()


def test_cancel_queued_job_marks_cancelled_without_running(store):
    gate = threading.Event()
    ran: list[str] = []

    def blocking_runner(store_, job, cancel_event):
        ran.append(job.id)
        gate.wait(timeout=5)
        store_.set_job_stage(job.id, "done")

    q = DownloadQueue(store, blocking_runner, workers=1)
    q.start()
    try:
        first = store.create_job("https://example.com/1", {})
        second = store.create_job("https://example.com/2", {})
        q.enqueue(first)
        q.enqueue(second)

        deadline = time.monotonic() + 5
        while not ran and time.monotonic() < deadline:
            time.sleep(0.02)

        assert q.cancel(second) is True
        assert store.get_job(second).stage == "cancelled"

        gate.set()
        time.sleep(0.3)
        assert second not in ran
    finally:
        gate.set()
        q.stop()


def test_cancel_running_job_sets_its_event(store):
    observed = threading.Event()

    def watching_runner(store_, job, cancel_event):
        for _ in range(100):
            if cancel_event.is_set():
                observed.set()
                store_.set_job_stage(job.id, "cancelled", "Cancelled by user")
                return
            time.sleep(0.02)
        store_.set_job_stage(job.id, "done")

    q = DownloadQueue(store, watching_runner, workers=1)
    q.start()
    try:
        job_id = store.create_job("https://example.com/1", {})
        q.enqueue(job_id)

        deadline = time.monotonic() + 5
        while not q.is_active(job_id) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert q.cancel(job_id) is True
        assert observed.wait(timeout=5)
        assert store.get_job(job_id).stage == "cancelled"
    finally:
        q.stop()


def test_cancel_unknown_job_returns_false(store):
    q = DownloadQueue(store, lambda s, j, c: None, workers=1)
    assert q.cancel("00000000-0000-0000-0000-000000000000") is False


def test_runner_exception_does_not_kill_the_worker(store):
    done = threading.Event()

    def flaky_runner(store_, job, cancel_event):
        if job.url.endswith("1"):
            raise RuntimeError("boom")
        store_.set_job_stage(job.id, "done")
        done.set()

    q = DownloadQueue(store, flaky_runner, workers=1)
    q.start()
    try:
        first = store.create_job("https://example.com/1", {})
        second = store.create_job("https://example.com/2", {})
        q.enqueue(first)
        q.enqueue(second)

        assert done.wait(timeout=5)
        assert store.get_job(first).stage == "error"
        assert store.get_job(second).stage == "done"
    finally:
        q.stop()


def test_recover_requeues_interrupted_jobs(store):
    done = threading.Event()
    seen: list[str] = []

    def runner(store_, job, cancel_event):
        seen.append(job.id)
        store_.set_job_stage(job.id, "done")
        done.set()

    orphan = store.create_job("https://example.com/orphan", {})
    store.set_job_stage(orphan, "downloading")

    q = DownloadQueue(store, runner, workers=1)
    q.start()
    try:
        q.recover()
        assert done.wait(timeout=5)
        assert seen == [orphan]
    finally:
        q.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.downloader.queue'`

- [ ] **Step 3: Implement the queue**

Create `backend/app/downloader/queue.py`:

```python
import logging
import queue as queue_mod
import threading
from typing import Callable

from app.downloader.store import Job, JobStore

logger = logging.getLogger(__name__)

_SHUTDOWN = object()

JobRunner = Callable[[JobStore, Job, threading.Event], None]


class DownloadQueue:
    """Worker pool draining a FIFO of job ids.

    Jobs beyond the worker count wait in the queue; they are never rejected.
    Worker threads outlive any HTTP request, so a disconnecting client cannot
    interrupt or orphan work.
    """

    def __init__(self, store: JobStore, runner: JobRunner, workers: int = 3) -> None:
        self._store = store
        self._runner = runner
        self._worker_count = max(1, int(workers))
        self._queue: queue_mod.Queue = queue_mod.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._active: dict[str, threading.Event] = {}
        self._cancelled_while_queued: set[str] = set()
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        for i in range(self._worker_count):
            thread = threading.Thread(
                target=self._work, name=f"downloader-worker-{i}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            for event in self._active.values():
                event.set()
        for _ in self._threads:
            self._queue.put(_SHUTDOWN)
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            self._cancelled_while_queued.discard(job_id)
        self._queue.put(job_id)

    def depth(self) -> int:
        return self._queue.qsize()

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._active

    def cancel(self, job_id: str) -> bool:
        """Cancel a running or queued job. Returns False if it is neither."""
        with self._lock:
            event = self._active.get(job_id)
            if event is not None:
                event.set()
                return True

        job = self._store.get_job(job_id)
        if job is None or job.stage != "queued":
            return False

        with self._lock:
            self._cancelled_while_queued.add(job_id)
        self._store.set_job_stage(job_id, "cancelled", "Cancelled by user")
        return True

    def recover(self) -> None:
        """Re-enqueue jobs left mid-flight by a previous process."""
        for job_id in self._store.reset_active_to_queued():
            self.enqueue(job_id)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SHUTDOWN:
                    return
                self._run_one(str(item))
            finally:
                self._queue.task_done()

    def _run_one(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._cancelled_while_queued:
                self._cancelled_while_queued.discard(job_id)
                return
            cancel_event = threading.Event()
            self._active[job_id] = cancel_event

        try:
            job = self._store.get_job(job_id)
            if job is None:
                return
            if job.stage == "cancelled":
                return
            self._runner(self._store, job, cancel_event)
        except Exception as exc:
            logger.error("Worker failed on job %s: %s", job_id, exc, exc_info=True)
            try:
                self._store.set_job_stage(job_id, "error", str(exc))
            except Exception:
                logger.exception("Could not record failure for job %s", job_id)
        finally:
            with self._lock:
                self._active.pop(job_id, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader_queue.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/downloader/queue.py backend/tests/test_downloader_queue.py
git commit -m "feat(downloader): add worker pool that queues instead of rejecting"
```

---

### Task 7: Routes, config, and wiring

**Files:**

- Create: `backend/app/downloader/routes.py`
- Modify: `backend/app/config.py`, `backend/app/main.py`
- Delete: `backend/app/download.py`, `backend/tests/test_download.py`
- Test: `backend/tests/test_downloader_routes.py`

**Interfaces:**

- Consumes: everything from Tasks 1–6.
- Produces: `router: APIRouter`, `init_downloader(app) -> None`, `shutdown_downloader() -> None`, and module-level `get_store()`, `get_queue()`, `get_broadcaster()`, `cookie_path() -> str`.

- [ ] **Step 1: Add configuration**

Modify `backend/app/config.py` — replace the `DOWNLOADER_JOBS_DIR` line and add the new settings:

```python
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "/downloads")
YT_DLP_COOKIES = os.getenv("YT_DLP_COOKIES", "")
DOWNLOADER_DATA_DIR = os.getenv("DOWNLOADER_DATA_DIR", "/data/downloader")
DOWNLOADER_DB = os.getenv("DOWNLOADER_DB", os.path.join(DOWNLOADER_DATA_DIR, "downloader.db"))
DOWNLOADER_WORKERS = int(os.getenv("DOWNLOADER_WORKERS", "3"))
DOWNLOADER_JOB_TTL = int(os.getenv("DOWNLOADER_JOB_TTL", "604800"))
```

- [ ] **Step 2: Write the failing route tests**

Create `backend/tests/test_downloader_routes.py`:

```python
import importlib
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    media = tmp_path / "media"
    (media / "Music").mkdir(parents=True)
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    with patch.dict(
        os.environ,
        {
            "BASE_PATHS": str(media),
            "TMDB_API_KEY": "test_key",
            "AUTH_USERNAME": "",
            "AUTH_PASSWORD": "",
            "SECRET_KEY": "test-secret-key",
            "ENABLED_FEATURES": "download",
            "DOWNLOADS_DIR": str(downloads),
            "DOWNLOADER_DATA_DIR": str(tmp_path / "dl-data"),
            "DOWNLOADER_DB": str(tmp_path / "dl-data" / "downloader.db"),
        },
    ):
        import app.config as config_mod

        importlib.reload(config_mod)
        import app.auth as auth_mod

        importlib.reload(auth_mod)
        import app.downloader.ydl as ydl_mod

        importlib.reload(ydl_mod)
        import app.downloader.routes as routes_mod

        importlib.reload(routes_mod)
        import app.main as main_mod

        importlib.reload(main_mod)

        with TestClient(main_mod.app) as c:
            yield c


def test_status_reports_version_and_queue_depth(client):
    response = client.get("/download/status")
    assert response.status_code == 200
    body = response.json()
    assert "yt_dlp_version" in body
    assert body["cookies_present"] is False
    assert body["queue_depth"] == 0


def test_create_accepts_multiple_urls_in_one_request(client):
    response = client.post(
        "/download",
        json={
            "urls": ["https://example.com/a", "https://example.com/b"],
            "options": {"type": "audio", "auto_start": False},
        },
    )
    assert response.status_code == 200
    job_ids = response.json()["job_ids"]
    assert len(job_ids) == 2

    listed = client.get("/download/jobs").json()["jobs"]
    assert {j["job_id"] for j in listed} == set(job_ids)
    assert all(j["stage"] == "queued" for j in listed)


def test_create_rejects_non_http_scheme(client):
    response = client.post(
        "/download", json={"urls": ["file:///etc/passwd"], "options": {}}
    )
    assert response.status_code == 422


def test_create_rejects_empty_url_list(client):
    response = client.post("/download", json={"urls": [], "options": {}})
    assert response.status_code == 422


def test_get_job_hides_internal_options(client):
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    body = client.get(f"/download/jobs/{job_id}").json()
    assert body["job_id"] == job_id
    assert "options" not in body


def test_get_missing_job_returns_404(client):
    response = client.get("/download/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_invalid_job_id_returns_422(client):
    assert client.get("/download/jobs/not-a-uuid").status_code == 422


def test_cancel_then_delete_a_queued_job(client):
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    assert client.post(f"/download/jobs/{job_id}/cancel").status_code == 200
    assert client.get(f"/download/jobs/{job_id}").json()["stage"] == "cancelled"
    assert client.delete(f"/download/jobs/{job_id}").status_code == 200
    assert client.get(f"/download/jobs/{job_id}").status_code == 404


def test_item_file_404_when_job_not_done(client):
    job_id = client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    ).json()["job_ids"][0]

    assert client.get(f"/download/jobs/{job_id}/items/0/file").status_code == 404


def test_cookie_upload_and_delete(client):
    response = client.post(
        "/download/cookies",
        files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\n", "text/plain")},
    )
    assert response.status_code == 200
    assert client.get("/download/status").json()["cookies_present"] is True

    assert client.delete("/download/cookies").status_code == 200
    assert client.get("/download/status").json()["cookies_present"] is False


def test_events_stream_emits_initial_snapshot(client):
    client.post(
        "/download",
        json={"urls": ["https://example.com/a"], "options": {"auto_start": False}},
    )

    with client.stream("GET", "/download/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                assert payload["type"] == "snapshot"
                assert len(payload["jobs"]) == 1
                break
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.downloader.routes'`

- [ ] **Step 4: Implement the routes**

Create `backend/app/downloader/routes.py`:

```python
import json
import logging
import os
import queue as queue_mod
import re
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from yt_dlp import version as yt_dlp_version

from app.config import (
    DOWNLOADER_DATA_DIR,
    DOWNLOADER_DB,
    DOWNLOADER_WORKERS,
    DOWNLOADS_DIR,
    YT_DLP_COOKIES,
)
from app.downloader.events import EventBroadcaster, job_to_payload
from app.downloader.queue import DownloadQueue
from app.downloader.runner import run_job
from app.downloader.store import JobStore

logger = logging.getLogger(__name__)
router = APIRouter()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_HEARTBEAT_SECONDS = 15.0

_store: JobStore | None = None
_queue: DownloadQueue | None = None
_broadcaster: EventBroadcaster | None = None


def cookie_path() -> str:
    return YT_DLP_COOKIES or os.path.join(DOWNLOADER_DATA_DIR, "cookies.txt")


def get_broadcaster() -> EventBroadcaster:
    if _broadcaster is None:
        raise RuntimeError("Downloader is not initialised")
    return _broadcaster


def get_store() -> JobStore:
    if _store is None:
        raise RuntimeError("Downloader is not initialised")
    return _store


def get_queue() -> DownloadQueue:
    if _queue is None:
        raise RuntimeError("Downloader is not initialised")
    return _queue


def init_downloader() -> None:
    """Build the store, broadcaster, and worker pool. Called from lifespan."""
    global _store, _queue, _broadcaster

    os.makedirs(DOWNLOADER_DATA_DIR, exist_ok=True)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    _broadcaster = EventBroadcaster()

    def publish(job) -> None:
        assert _broadcaster is not None
        _broadcaster.publish({"type": "job", "job": job_to_payload(job)})

    _store = JobStore(DOWNLOADER_DB, on_change=publish)

    def runner(store: JobStore, job, cancel_event) -> None:
        run_job(store, job, cancel_event, cookie_path())

    _queue = DownloadQueue(_store, runner, workers=DOWNLOADER_WORKERS)
    _queue.start()
    _queue.recover()


def shutdown_downloader() -> None:
    global _store, _queue, _broadcaster
    if _queue is not None:
        _queue.stop()
    if _store is not None:
        _store.close()
    _store = _queue = _broadcaster = None


def _require_valid_id(job_id: str) -> None:
    if not _UUID_RE.match(job_id):
        raise HTTPException(status_code=422, detail=f"Invalid job_id: {job_id}")


class CreateDownloadRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=200)
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("/download/status")
def download_status() -> dict[str, Any]:
    return {
        "yt_dlp_version": yt_dlp_version.__version__,
        "cookies_present": os.path.isfile(cookie_path()),
        "downloads_dir": DOWNLOADS_DIR,
        "queue_depth": get_queue().depth(),
        "workers": DOWNLOADER_WORKERS,
    }


@router.post("/download")
def create_downloads(request: CreateDownloadRequest) -> dict[str, list[str]]:
    for url in request.urls:
        if urlparse(url).scheme not in ("http", "https"):
            raise HTTPException(
                status_code=422, detail=f"Only http(s) URLs are supported: {url}"
            )

    auto_start = str(request.options.get("auto_start", True)).lower() not in (
        "false",
        "0",
        "no",
    )

    store, job_queue = get_store(), get_queue()
    job_ids = []
    for url in request.urls:
        job_id = store.create_job(url, request.options)
        job_ids.append(job_id)
        if auto_start:
            job_queue.enqueue(job_id)
    return {"job_ids": job_ids}


@router.get("/download/jobs")
def list_download_jobs() -> dict[str, list[dict[str, Any]]]:
    return {"jobs": [job_to_payload(job) for job in get_store().list_jobs()]}


@router.get("/download/jobs/{job_id}")
def get_download_job(job_id: str) -> dict[str, Any]:
    _require_valid_id(job_id)
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_to_payload(job)


@router.post("/download/jobs/{job_id}/start")
def start_download_job(job_id: str) -> dict[str, str]:
    _require_valid_id(job_id)
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.stage != "queued":
        raise HTTPException(status_code=409, detail="Job is not queued")
    get_queue().enqueue(job_id)
    return {"status": "started"}


@router.post("/download/jobs/{job_id}/cancel")
def cancel_download_job(job_id: str) -> dict[str, str]:
    _require_valid_id(job_id)
    if get_store().get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not get_queue().cancel(job_id):
        raise HTTPException(status_code=409, detail="Job is not active")
    return {"status": "cancelled"}


@router.delete("/download/jobs/{job_id}")
def delete_download_job(job_id: str) -> dict[str, str]:
    _require_valid_id(job_id)
    job_queue = get_queue()
    job_queue.cancel(job_id)
    for _ in range(50):
        if not job_queue.is_active(job_id):
            break
        time.sleep(0.1)
    if not get_store().delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "deleted"}


@router.get("/download/jobs/{job_id}/items/{index}/file")
def download_item_file(job_id: str, index: int) -> FileResponse:
    _require_valid_id(job_id)
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    item = next((i for i in job.items if i.index == index), None)
    if item is None or item.stage != "done" or not item.path:
        raise HTTPException(status_code=404, detail="Output file not available")
    if not os.path.isfile(item.path):
        raise HTTPException(status_code=404, detail="Output file is gone")

    return FileResponse(
        item.path,
        filename=os.path.basename(item.path),
        media_type="application/octet-stream",
    )


@router.get("/download/events")
def download_events() -> StreamingResponse:
    broadcaster = get_broadcaster()
    store = get_store()

    def generate():
        subscription = broadcaster.subscribe()
        try:
            snapshot = {
                "type": "snapshot",
                "jobs": [job_to_payload(job) for job in store.list_jobs()],
            }
            yield f"data: {json.dumps(snapshot)}\n\n"
            while True:
                try:
                    event = subscription.get(timeout=_HEARTBEAT_SECONDS)
                except queue_mod.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            broadcaster.unsubscribe(subscription)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/download/cookies")
async def upload_cookies(file: UploadFile = File(...)) -> dict[str, str]:
    path = cookie_path()
    max_size = 1024 * 1024
    content = await file.read(max_size + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Cookie file is empty")
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="Cookie file is too large")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return {"status": "ok"}


@router.delete("/download/cookies")
def delete_cookies() -> dict[str, str]:
    path = cookie_path()
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "deleted"}
```

- [ ] **Step 5: Wire into main.py**

In `backend/app/main.py`, delete the entire `# ── Downloader Endpoints ───` section (from the comment through the end of `download_delete_cookies`), delete the `from app.download import (...)` block, delete `_download_semaphore` and `_download_sse_response`, and delete `_cleanup_downloader_jobs`. Then add near the other imports:

```python
from app.downloader.routes import (
    router as downloader_router,
    init_downloader,
    shutdown_downloader,
    get_store as get_downloader_store,
)
```

Register the router immediately after the `app = FastAPI(...)` construction, guarded by the feature flag. Conditional registration is the gate: when the feature is off the routes do not exist at all, which is stronger than a per-route `require_feature` check.

```python
if "download" in ENABLED_FEATURES_SET:
    app.include_router(downloader_router)
```

Replace the downloader block inside `lifespan` startup with:

```python
    downloader_cleanup_task = None
    if "download" in ENABLED_FEATURES_SET:
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            logger.error(
                "Downloader requires ffmpeg and ffprobe on PATH; jobs will fail."
            )
        init_downloader()
        downloader_cleanup_task = asyncio.create_task(_cleanup_downloader_jobs())
```

Add the cleanup coroutine next to the cutter one:

```python
async def _cleanup_downloader_jobs():
    """Periodically purge expired download jobs."""
    from app.config import DOWNLOADER_JOB_TTL

    while True:
        await asyncio.sleep(600)
        try:
            get_downloader_store().purge_expired(DOWNLOADER_JOB_TTL)
        except Exception:
            logger.exception("Error during download job cleanup")
```

And in the shutdown half of `lifespan`, after cancelling the task:

```python
    if downloader_cleanup_task is not None:
        downloader_cleanup_task.cancel()
        shutdown_downloader()
```

- [ ] **Step 6: Delete the old implementation**

```bash
git rm backend/app/download.py backend/tests/test_download.py
```

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS. If `tests/test_main.py` references removed downloader routes, update those assertions to the new paths (`POST /download` with a JSON body instead of `POST /download/start` with form fields).

- [ ] **Step 8: Commit**

```bash
git add backend/app/downloader/routes.py backend/app/config.py backend/app/main.py backend/tests/test_downloader_routes.py
git commit -m "feat(downloader): add routes, wire worker pool into lifespan, drop old module"
```

---

### Task 8: Cyan accent

**Files:**

- Modify: `frontend/src/index.css`, `frontend/src/components/Landing.tsx`

**Interfaces:**

- Consumes: nothing.
- Produces: CSS custom properties `--accent-6`, `--accent-6-glow`; utility classes `.input-field.input-cyan:focus`, `.btn-submit.btn-cyan`.

- [ ] **Step 1: Add the custom properties**

In `frontend/src/index.css`, after the `--accent-5-glow` declaration:

```css
  --accent-6: #22d3ee;
  --accent-6-glow: rgba(34, 211, 238, 0.18);
```

- [ ] **Step 2: Add the input focus rule**

Alongside the other `.input-field.input-*:focus` rules:

```css
  .input-field.input-cyan:focus {
    border-color: var(--accent-6);
    box-shadow:
      0 0 0 3px var(--accent-6-glow),
      inset 0 1px 2px rgba(0, 0, 0, 0.2);
  }
```

- [ ] **Step 3: Add the button rule**

Alongside the other `.btn-submit.btn-*` rules:

```css
  .btn-submit.btn-cyan {
    background: var(--accent-6);
  }
  .btn-submit.btn-cyan:hover:not(:disabled) {
    background: #06b6d4;
  }
```

- [ ] **Step 4: Repoint the Landing card**

In `frontend/src/components/Landing.tsx`, change the downloader entry's `iconClass` from `bg-[var(--accent-5-glow)] text-[var(--accent-5)]` to `bg-[var(--accent-6-glow)] text-[var(--accent-6)]`, and change its `radial-gradient(... var(--accent-5-glow) ...)` branch to `var(--accent-6-glow)`.

- [ ] **Step 5: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/index.css frontend/src/components/Landing.tsx
git commit -m "feat(downloader): move feature accent from amber to cyan"
```

---

### Task 9: Reconnecting event stream client

**Files:**

- Create: `frontend/src/lib/eventStream.ts`
- Modify: `frontend/src/lib/sse.ts`
- Test: `frontend/src/__tests__/eventStream.test.ts`

**Interfaces:**

- Consumes: `API_BASE` from `@/lib/http`.
- Produces:
  - `parseSSEChunk(buffer: string): { events: Array<{ type: string; data: string }>; rest: string }` exported from `lib/sse.ts`
  - `openEventStream(path: string, onEvent: (data: string) => void, options?: { onStateChange?: (connected: boolean) => void; retryMs?: number }): () => void` from `lib/eventStream.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/__tests__/eventStream.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseSSEChunk } from '@/lib/sse'
import { openEventStream } from '@/lib/eventStream'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('parseSSEChunk', () => {
  it('extracts complete frames and keeps the remainder', () => {
    const { events, rest } = parseSSEChunk('data: one\n\ndata: tw')
    expect(events).toEqual([{ type: 'message', data: 'one' }])
    expect(rest).toBe('data: tw')
  })

  it('reads a named event type', () => {
    const { events } = parseSSEChunk('event: progress\ndata: {"a":1}\n\n')
    expect(events).toEqual([{ type: 'progress', data: '{"a":1}' }])
  })

  it('ignores comment-only heartbeat frames', () => {
    const { events } = parseSSEChunk(': heartbeat\n\n')
    expect(events).toEqual([])
  })

  it('joins multi-line data payloads', () => {
    const { events } = parseSSEChunk('data: a\ndata: b\n\n')
    expect(events).toEqual([{ type: 'message', data: 'a\nb' }])
  })
})

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder()
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]!) }
            : { done: true, value: undefined },
        cancel: async () => {},
      }),
    },
  } as unknown as Response
}

describe('openEventStream', () => {
  it('delivers each event payload to the callback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamOf(['data: one\n\ndata: two\n\n'])))
    const seen: string[] = []
    const close = openEventStream('/download/events', (d) => seen.push(d), { retryMs: 10_000 })

    await vi.waitFor(() => expect(seen).toEqual(['one', 'two']))
    close()
  })

  it('reports connection state transitions', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamOf(['data: x\n\n'])))
    const states: boolean[] = []
    const close = openEventStream('/download/events', () => {}, {
      onStateChange: (c) => states.push(c),
      retryMs: 10_000,
    })

    await vi.waitFor(() => expect(states[0]).toBe(true))
    close()
  })

  it('reconnects after the stream ends', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(streamOf(['data: first\n\n']))
      .mockResolvedValue(streamOf(['data: second\n\n']))
    vi.stubGlobal('fetch', fetchMock)

    const seen: string[] = []
    const close = openEventStream('/download/events', (d) => seen.push(d), { retryMs: 1 })

    await vi.waitFor(() => expect(seen).toContain('second'), { timeout: 2000 })
    close()
  })

  it('stops reconnecting once closed', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamOf(['data: x\n\n']))
    vi.stubGlobal('fetch', fetchMock)

    const close = openEventStream('/download/events', () => {}, { retryMs: 1 })
    close()
    const callsAfterClose = fetchMock.mock.calls.length
    await new Promise((r) => setTimeout(r, 50))
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(callsAfterClose + 1)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm run test -- eventStream`
Expected: FAIL — `parseSSEChunk` and `openEventStream` are not exported.

- [ ] **Step 3: Extract the frame parser into sse.ts**

Add to `frontend/src/lib/sse.ts` (exported, above `connectSSE`):

```typescript
export interface SSEFrame {
  type: string
  data: string
}

/** Split a buffer into complete SSE frames, returning the unparsed remainder. */
export function parseSSEChunk(buffer: string): { events: SSEFrame[]; rest: string } {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  const events: SSEFrame[] = []

  for (const part of parts) {
    if (!part.trim()) continue

    let type = 'message'
    const dataLines: string[] = []

    for (const line of part.split('\n')) {
      if (line.startsWith(':')) continue
      if (line.startsWith('event: ')) {
        type = line.slice(7)
      } else if (line.startsWith('data: ')) {
        dataLines.push(line.slice(6))
      }
    }

    const data = dataLines.join('\n')
    if (data) events.push({ type, data })
  }

  return { events, rest }
}
```

Then replace the inline parsing loop inside `connectSSE` (the block starting `const parts = buffer.split('\n\n')` through the `switch` statement) with:

```typescript
        const { events, rest } = parseSSEChunk(buffer)
        buffer = rest

        for (const { type, data } of events) {
          switch (type) {
            case 'progress':
              callbacks.onProgress(data)
              break
            case 'error_msg':
              callbacks.onError(data)
              break
            case 'done':
              receivedDone = true
              callbacks.onDone(data)
              return
          }
        }
```

- [ ] **Step 4: Implement the persistent stream**

Create `frontend/src/lib/eventStream.ts`:

```typescript
import { API_BASE } from './http'
import { parseSSEChunk } from './sse'

interface EventStreamOptions {
  onStateChange?: (connected: boolean) => void
  /** Base reconnect delay in ms; doubles up to 30s while the server is down. */
  retryMs?: number
}

/**
 * Hold a long-lived GET SSE connection, reconnecting until closed.
 *
 * Unlike `connectSSE`, this never terminates on a `done` event — the server
 * stream is a continuous state feed, and every reconnect replays a full
 * snapshot, so a client that misses deltas still converges.
 */
export function openEventStream(
  path: string,
  onEvent: (data: string) => void,
  options: EventStreamOptions = {},
): () => void {
  const baseRetry = options.retryMs ?? 1000
  let closed = false
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let attempt = 0

  const setConnected = (connected: boolean) => options.onStateChange?.(connected)

  const scheduleReconnect = () => {
    if (closed) return
    const delay = Math.min(baseRetry * 2 ** attempt, 30_000)
    attempt += 1
    timer = setTimeout(connect, delay)
  }

  async function connect(): Promise<void> {
    if (closed) return
    controller = new AbortController()

    let response: Response
    try {
      response = await fetch(new URL(path, API_BASE).toString(), {
        signal: controller.signal,
        credentials: 'include',
        headers: { Accept: 'text/event-stream' },
      })
    } catch {
      setConnected(false)
      scheduleReconnect()
      return
    }

    if (!response.ok || !response.body) {
      setConnected(false)
      scheduleReconnect()
      return
    }

    attempt = 0
    setConnected(true)

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const { events, rest } = parseSSEChunk(buffer)
        buffer = rest
        for (const frame of events) onEvent(frame.data)
      }
    } catch {
      // Fall through to the reconnect path below.
    }

    setConnected(false)
    scheduleReconnect()
  }

  void connect()

  return () => {
    closed = true
    if (timer) clearTimeout(timer)
    controller?.abort()
    setConnected(false)
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm run test -- eventStream`
Expected: PASS (8 tests)

- [ ] **Step 6: Run the full frontend suite to catch sse.ts regressions**

Run: `cd frontend && npm run test`
Expected: PASS — existing `api.test.ts` still passes against the refactored `connectSSE`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/eventStream.ts frontend/src/lib/sse.ts frontend/src/__tests__/eventStream.test.ts
git commit -m "feat(downloader): add reconnecting SSE client and share the frame parser"
```

---

### Task 10: Types and API client

**Files:**

- Modify: `frontend/src/types.ts`, `frontend/src/lib/api.ts`
- Test: `frontend/src/__tests__/downloadApi.test.ts`

**Interfaces:**

- Consumes: `openEventStream` (Task 9).
- Produces:
  - Types `DownloadStage`, `DownloadItem`, `DownloadJob`, `DownloadForm`, `DownloaderStatus`
  - `createDownloads(urls, options)`, `fetchDownloadJobs()`, `fetchDownloaderStatus()`, `startDownloadJob(jobId)`, `cancelDownloadJob(jobId)`, `deleteDownloadJob(jobId)`, `openDownloadStream(onEvent, onStateChange)`, `getDownloadItemFileUrl(jobId, index)`, `postCookies(file)`, `deleteCookies()`

- [ ] **Step 1: Replace the downloader types**

In `frontend/src/types.ts`, replace the existing `DownloadJob` and `DownloadForm` declarations with:

```typescript
export type DownloadStage =
  | 'queued'
  | 'downloading'
  | 'transcoding'
  | 'done'
  | 'cancelled'
  | 'error'

export interface DownloadItem {
  index: number
  title: string
  path: string | null
  size: number | null
  progress: number
  stage: DownloadStage
  error: string | null
}

export interface DownloadJob {
  job_id: string
  url: string
  stage: DownloadStage
  error: string | null
  created_at: string
  updated_at: string
  items: DownloadItem[]
}

export interface DownloadForm {
  url: string
  type: 'video' | 'audio' | 'thumbnail'
  codec: string
  format: string
  quality: string
  output_dir: string
  base: string
  auto_start: boolean
  sub_folder: string
  custom_prefix: string
  custom_filename: string
  item_limit: number
}

export interface DownloaderStatus {
  yt_dlp_version: string
  cookies_present: boolean
  downloads_dir: string
  queue_depth: number
  workers: number
}
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/__tests__/downloadApi.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  cancelDownloadJob,
  createDownloads,
  getDownloadItemFileUrl,
  startDownloadJob,
} from '@/lib/api'

afterEach(() => {
  vi.restoreAllMocks()
})

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

describe('createDownloads', () => {
  it('sends every url in a single JSON request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ job_ids: ['a', 'b'] }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await createDownloads(['https://x/1', 'https://x/2'], { type: 'audio' })

    expect(result.job_ids).toEqual(['a', 'b'])
    expect(fetchMock).toHaveBeenCalledTimes(1)

    const [, init] = fetchMock.mock.calls[0]!
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body as string)
    expect(body.urls).toEqual(['https://x/1', 'https://x/2'])
    expect(body.options.type).toBe('audio')
  })
})

describe('cancelDownloadJob', () => {
  it('posts to the cancel endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'cancelled' }))
    vi.stubGlobal('fetch', fetchMock)

    await cancelDownloadJob('job-1')

    expect(fetchMock.mock.calls[0]![0]).toContain('/download/jobs/job-1/cancel')
  })
})

describe('startDownloadJob', () => {
  it('posts to the start endpoint and returns a promise', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'started' }))
    vi.stubGlobal('fetch', fetchMock)

    await startDownloadJob('job-1')

    expect(fetchMock.mock.calls[0]![0]).toContain('/download/jobs/job-1/start')
  })
})

describe('getDownloadItemFileUrl', () => {
  it('encodes the job id and includes the item index', () => {
    expect(getDownloadItemFileUrl('a b', 2)).toBe('/download/jobs/a%20b/items/2/file')
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npm run test -- downloadApi`
Expected: FAIL — `createDownloads` is not exported.

- [ ] **Step 4: Replace the downloader API client**

In `frontend/src/lib/api.ts`, delete `postDownload`, `createDownloadJob`, `startDownloadJob`, and `getDownloaderFileUrl`, then add:

```typescript
import { openEventStream } from '@/lib/eventStream'

export async function createDownloads(
  urls: string[],
  options: Omit<import('@/types').DownloadForm, 'url'> | Record<string, unknown>,
): Promise<{ job_ids: string[] }> {
  return fetchJson('/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls, options }),
  })
}

export async function startDownloadJob(jobId: string): Promise<{ status: string }> {
  return fetchJson(`/download/jobs/${encodeURIComponent(jobId)}/start`, { method: 'POST' })
}

export async function cancelDownloadJob(jobId: string): Promise<{ status: string }> {
  return fetchJson(`/download/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
}

export function openDownloadStream(
  onEvent: (data: string) => void,
  onStateChange?: (connected: boolean) => void,
): () => void {
  return openEventStream('/download/events', onEvent, { onStateChange })
}

export function getDownloadItemFileUrl(jobId: string, index: number): string {
  return `/download/jobs/${encodeURIComponent(jobId)}/items/${index}/file`
}
```

Verify that the existing `fetchJson` helper accepts a `RequestInit` second argument. If it does not, extend its signature to `fetchJson(path: string, init?: RequestInit)` and pass `init` through to `fetch`, preserving the existing timeout and `assertAuthenticated` behaviour.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm run test -- downloadApi`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/lib/api.ts frontend/src/__tests__/downloadApi.test.ts
git commit -m "feat(downloader): replace client types and API surface for the new job model"
```

---

### Task 11: Download stream hook

**Files:**

- Create: `frontend/src/hooks/useDownloadStream.ts`
- Test: `frontend/src/__tests__/useDownloadStream.test.ts`

**Interfaces:**

- Consumes: `openDownloadStream` (Task 10), `DownloadJob` (Task 10).
- Produces:
  - `applyStreamEvent(jobs: DownloadJob[], raw: string): DownloadJob[]` (exported for testing)
  - `useDownloadStream(): { jobs: DownloadJob[]; connected: boolean }`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/__tests__/useDownloadStream.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { applyStreamEvent } from '@/hooks/useDownloadStream'
import type { DownloadJob } from '@/types'

function job(id: string, stage: DownloadJob['stage'] = 'queued'): DownloadJob {
  return {
    job_id: id,
    url: `https://example.com/${id}`,
    stage,
    error: null,
    created_at: '2026-08-04 10:00:00',
    updated_at: '2026-08-04 10:00:00',
    items: [],
  }
}

describe('applyStreamEvent', () => {
  it('replaces all state on a snapshot', () => {
    const next = applyStreamEvent([job('stale')], JSON.stringify({
      type: 'snapshot',
      jobs: [job('a'), job('b')],
    }))
    expect(next.map((j) => j.job_id)).toEqual(['a', 'b'])
  })

  it('updates an existing job in place without reordering', () => {
    const next = applyStreamEvent(
      [job('a'), job('b')],
      JSON.stringify({ type: 'job', job: job('a', 'downloading') }),
    )
    expect(next.map((j) => j.job_id)).toEqual(['a', 'b'])
    expect(next[0]!.stage).toBe('downloading')
  })

  it('prepends a job it has not seen before', () => {
    const next = applyStreamEvent([job('a')], JSON.stringify({ type: 'job', job: job('new') }))
    expect(next.map((j) => j.job_id)).toEqual(['new', 'a'])
  })

  it('ignores malformed payloads', () => {
    const before = [job('a')]
    expect(applyStreamEvent(before, 'not json')).toBe(before)
    expect(applyStreamEvent(before, JSON.stringify({ type: 'unknown' }))).toBe(before)
  })

  it('a snapshot after reconnect does not duplicate jobs', () => {
    let state = applyStreamEvent([], JSON.stringify({ type: 'snapshot', jobs: [job('a')] }))
    state = applyStreamEvent(state, JSON.stringify({ type: 'snapshot', jobs: [job('a')] }))
    expect(state).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm run test -- useDownloadStream`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/hooks/useDownloadStream.ts`:

```typescript
import { useEffect, useState } from 'react'
import { openDownloadStream } from '@/lib/api'
import type { DownloadJob } from '@/types'

interface SnapshotEvent {
  type: 'snapshot'
  jobs: DownloadJob[]
}

interface JobEvent {
  type: 'job'
  job: DownloadJob
}

/**
 * Fold one server event into job state.
 *
 * The server is the only writer: a snapshot replaces everything, and a job
 * event replaces exactly one entry. Returning the previous array unchanged
 * for unrecognised payloads keeps React from re-rendering on noise.
 */
export function applyStreamEvent(jobs: DownloadJob[], raw: string): DownloadJob[] {
  let event: SnapshotEvent | JobEvent
  try {
    event = JSON.parse(raw)
  } catch {
    return jobs
  }

  if (event.type === 'snapshot' && Array.isArray(event.jobs)) {
    return event.jobs
  }

  if (event.type === 'job' && event.job?.job_id) {
    const incoming = event.job
    const index = jobs.findIndex((j) => j.job_id === incoming.job_id)
    if (index === -1) return [incoming, ...jobs]
    const next = jobs.slice()
    next[index] = incoming
    return next
  }

  return jobs
}

export function useDownloadStream(): { jobs: DownloadJob[]; connected: boolean } {
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    return openDownloadStream(
      (data) => setJobs((prev) => applyStreamEvent(prev, data)),
      setConnected,
    )
  }, [])

  return { jobs, connected }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm run test -- useDownloadStream`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useDownloadStream.ts frontend/src/__tests__/useDownloadStream.test.ts
git commit -m "feat(downloader): add single-source download stream hook"
```

---

### Task 12: Rebuild the panel

**Files:**

- Create: `frontend/src/components/downloader/DownloadJobCard.tsx`, `frontend/src/components/downloader/DownloadOptions.tsx`
- Rewrite: `frontend/src/components/DownloaderPanel.tsx`

**Interfaces:**

- Consumes: `useDownloadStream` (Task 11); `createDownloads`, `cancelDownloadJob`, `deleteDownloadJob`, `startDownloadJob`, `getDownloadItemFileUrl`, `fetchDownloaderStatus`, `fetchMediaDirectories`, `postCookies`, `deleteCookies` (Task 10).
- Produces: `DownloadJobCard`, `DownloadOptions`, and the rewritten default-export `DownloaderPanel`.

- [ ] **Step 1: Build the job card**

Create `frontend/src/components/downloader/DownloadJobCard.tsx`:

```tsx
import { getDownloadItemFileUrl } from '@/lib/api'
import type { DownloadJob, DownloadStage } from '@/types'

const STAGE_LABELS: Record<DownloadStage, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  transcoding: 'Transcoding',
  done: 'Done',
  cancelled: 'Cancelled',
  error: 'Failed',
}

const PIPELINE: DownloadStage[] = ['downloading', 'transcoding', 'done']

function formatSize(bytes: number | null): string {
  if (bytes === null) return ''
  const units = ['B', 'KiB', 'MiB', 'GiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

interface Props {
  job: DownloadJob
  showTranscodeStage: boolean
  onCancel: (jobId: string) => void
  onDelete: (jobId: string) => void
  onStart: (jobId: string) => void
  onRetry: (url: string) => void
}

export default function DownloadJobCard({
  job,
  showTranscodeStage,
  onCancel,
  onDelete,
  onStart,
  onRetry,
}: Props) {
  const isActive = ['queued', 'downloading', 'transcoding'].includes(job.stage)
  const stages = showTranscodeStage ? PIPELINE : PIPELINE.filter((s) => s !== 'transcoding')
  const reached = (stage: DownloadStage) =>
    job.stage === 'done' || stages.indexOf(job.stage) >= stages.indexOf(stage)

  const overall =
    job.items.length > 0
      ? job.items.reduce((sum, item) => sum + item.progress, 0) / job.items.length
      : 0

  return (
    <div className="glass-light rounded-[14px] p-4">
      <div className="mb-2 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-[0.88rem] font-medium text-[var(--text-primary)]">
            {job.items[0]?.title || job.url}
          </p>
          <div className="mt-1 flex items-center gap-2">
            {stages.map((stage) => (
              <span
                key={stage}
                className={`text-[0.68rem] uppercase tracking-[0.1em] ${
                  job.stage === stage
                    ? 'text-[var(--accent-6)]'
                    : reached(stage)
                      ? 'text-[var(--text-secondary)]'
                      : 'text-[var(--text-tertiary)]/40'
                }`}
              >
                {STAGE_LABELS[stage]}
              </span>
            ))}
            {!isActive && (
              <span className="text-[0.68rem] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                {STAGE_LABELS[job.stage]}
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {job.stage === 'queued' && (
            <button
              type="button"
              onClick={() => onStart(job.job_id)}
              className="rounded-lg border border-[var(--accent-6)]/30 px-3 py-1 text-[0.72rem] font-medium text-[var(--accent-6)] transition-all hover:bg-[var(--accent-6)]/10"
            >
              Start
            </button>
          )}
          {isActive && (
            <button
              type="button"
              onClick={() => onCancel(job.job_id)}
              className="rounded-lg border border-white/8 px-3 py-1 text-[0.72rem] text-[var(--text-secondary)] transition-all hover:border-red-500/30 hover:text-red-400"
            >
              Cancel
            </button>
          )}
          {(job.stage === 'error' || job.stage === 'cancelled') && (
            <button
              type="button"
              onClick={() => onRetry(job.url)}
              className="rounded-lg border border-[var(--accent-6)]/30 px-3 py-1 text-[0.72rem] text-[var(--accent-6)] transition-all hover:bg-[var(--accent-6)]/10"
            >
              Retry
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(job.job_id)}
            className="rounded-lg border border-white/8 px-3 py-1 text-[0.72rem] text-[var(--text-secondary)] transition-all hover:border-red-500/30 hover:text-red-400"
          >
            Delete
          </button>
        </div>
      </div>

      {isActive && (
        <>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/6">
            <div
              className="h-full rounded-full bg-[var(--accent-6)] transition-all duration-300"
              style={{ width: `${Math.max(0, Math.min(overall, 100))}%` }}
            />
          </div>
          <p className="mt-1.5 text-[0.72rem] tabular-nums text-[var(--text-secondary)]">
            {overall.toFixed(1)}%
          </p>
        </>
      )}

      {job.items.length > 1 && (
        <ul className="mt-3 space-y-1.5 border-t border-white/6 pt-3">
          {job.items.map((item) => (
            <li key={item.index} className="flex items-center gap-3 text-[0.75rem]">
              <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                {item.title}
              </span>
              <span className="tabular-nums text-[var(--text-tertiary)]">
                {item.stage === 'done' ? formatSize(item.size) : `${item.progress.toFixed(0)}%`}
              </span>
              {item.stage === 'done' && (
                <a
                  href={getDownloadItemFileUrl(job.job_id, item.index)}
                  download
                  className="text-[var(--accent-6)]"
                >
                  Save
                </a>
              )}
            </li>
          ))}
        </ul>
      )}

      {job.items.length === 1 && job.stage === 'done' && (
        <div className="mt-2 flex items-center gap-3 text-[0.72rem] text-[var(--text-tertiary)]">
          <span>{formatSize(job.items[0]!.size)}</span>
          <a
            href={getDownloadItemFileUrl(job.job_id, 0)}
            download
            className="text-[var(--accent-6)]"
          >
            Save
          </a>
        </div>
      )}

      {job.error && <p className="mt-2 text-[0.78rem] text-red-400">{job.error}</p>}
    </div>
  )
}
```

- [ ] **Step 2: Build the options block**

Create `frontend/src/components/downloader/DownloadOptions.tsx`:

```tsx
import DirectorySelect from '../ui/DirectorySelect'
import StyledSelect from '../ui/StyledSelect'
import type { DirectoryEntry, DownloadForm } from '@/types'

const CONTAINERS: Record<string, { label: string; value: string }[]> = {
  video: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP4', value: 'mp4' },
    { label: 'MKV', value: 'mkv' },
    { label: 'WebM', value: 'webm' },
    { label: 'MOV', value: 'mov' },
  ],
  audio: [
    { label: 'Auto', value: 'auto' },
    { label: 'MP3', value: 'mp3' },
    { label: 'M4A', value: 'm4a' },
    { label: 'FLAC', value: 'flac' },
    { label: 'Opus', value: 'opus' },
    { label: 'WAV', value: 'wav' },
  ],
  thumbnail: [
    { label: 'Auto', value: 'auto' },
    { label: 'JPG', value: 'jpg' },
    { label: 'PNG', value: 'png' },
    { label: 'WebP', value: 'webp' },
  ],
}

const RECODE: Record<string, { label: string; value: string }[]> = {
  video: [
    { label: 'No re-encode', value: 'auto' },
    { label: 'H.264', value: 'h264' },
    { label: 'H.265', value: 'h265' },
    { label: 'VP9', value: 'vp9' },
    { label: 'AV1', value: 'av1' },
  ],
  audio: [
    { label: 'No re-encode', value: 'auto' },
    { label: 'MP3', value: 'mp3' },
    { label: 'FLAC', value: 'flac' },
    { label: 'AAC', value: 'aac' },
    { label: 'Opus', value: 'opus' },
  ],
  thumbnail: [],
}

const VIDEO_QUALITY = [
  { label: 'Best', value: 'best' },
  { label: '2160p', value: '2160p' },
  { label: '1440p', value: '1440p' },
  { label: '1080p', value: '1080p' },
  { label: '720p', value: '720p' },
  { label: '480p', value: '480p' },
  { label: 'Worst', value: 'worst' },
]

const AUDIO_QUALITY = [
  { label: 'Best', value: 'best' },
  { label: '320kbps', value: '320kbps' },
  { label: '256kbps', value: '256kbps' },
  { label: '192kbps', value: '192kbps' },
  { label: '128kbps', value: '128kbps' },
  { label: '96kbps', value: '96kbps' },
  { label: 'Worst', value: 'worst' },
]

interface Props {
  form: DownloadForm
  onChange: (patch: Partial<DownloadForm>) => void
  directories: DirectoryEntry[]
  onRefreshDirectories: () => void
  isRefreshingDirectories: boolean
  showBaseLabel?: boolean
  advancedOpen: boolean
  onToggleAdvanced: () => void
}

export default function DownloadOptions({
  form,
  onChange,
  directories,
  onRefreshDirectories,
  isRefreshingDirectories,
  showBaseLabel,
  advancedOpen,
  onToggleAdvanced,
}: Props) {
  const isThumbnail = form.type === 'thumbnail'
  const quality = form.type === 'audio' ? AUDIO_QUALITY : VIDEO_QUALITY

  return (
    <>
      <div className={`grid gap-3 ${isThumbnail ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3'}`}>
        <StyledSelect
          label="Type"
          options={[
            { label: 'Video', value: 'video' },
            { label: 'Audio', value: 'audio' },
            { label: 'Thumbnail', value: 'thumbnail' },
          ]}
          value={form.type}
          onChange={(v) =>
            onChange({
              type: v as DownloadForm['type'],
              codec: 'auto',
              format: 'auto',
              quality: 'best',
            })
          }
        />
        <StyledSelect
          label="Format"
          options={CONTAINERS[form.type] ?? []}
          value={form.format}
          onChange={(v) => onChange({ format: v })}
        />
        {!isThumbnail && (
          <StyledSelect
            label="Quality"
            options={quality}
            value={form.quality}
            onChange={(v) => onChange({ quality: v })}
          />
        )}
      </div>

      <div className="rounded-[14px] border border-white/6 bg-white/[0.02]">
        <button
          type="button"
          onClick={onToggleAdvanced}
          className="flex w-full items-center justify-between px-5 py-3"
        >
          <span className="text-[0.78rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
            Advanced Options
          </span>
          <span className="text-[0.75rem] text-[var(--text-tertiary)]">
            {advancedOpen ? '▴ collapse' : '▾ expand'}
          </span>
        </button>

        {advancedOpen && (
          <div className="border-t border-white/6 px-5 pb-5 pt-4">
            {!isThumbnail && (
              <div className="mb-5 rounded-[10px] border border-white/6 bg-white/[0.02] p-4">
                <StyledSelect
                  label="Re-encode to codec"
                  options={RECODE[form.type] ?? []}
                  value={form.codec}
                  onChange={(v) => onChange({ codec: v })}
                />
                {form.codec !== 'auto' && (
                  <p className="mt-2 text-[0.72rem] text-amber-400/80">
                    Re-encoding runs after the download and can take much longer than the
                    download itself. Leave this on “No re-encode” unless you need a specific
                    codec.
                  </p>
                )}
              </div>
            )}

            <div className="grid gap-5 md:grid-cols-2">
              <StyledSelect
                label="Auto Start"
                options={[
                  { label: 'Yes', value: 'yes' },
                  { label: 'No', value: 'no' },
                ]}
                value={form.auto_start ? 'yes' : 'no'}
                onChange={(v) => onChange({ auto_start: v === 'yes' })}
              />

              <DirectorySelect
                color="cyan"
                directories={directories}
                onRefresh={onRefreshDirectories}
                isLoading={isRefreshingDirectories}
                value={form.output_dir}
                base={form.base}
                onChange={(path, base) => onChange({ output_dir: path, base })}
                showBaseLabel={showBaseLabel}
              />

              <div>
                <label className="field-label">Subfolder</label>
                <input
                  type="text"
                  value={form.sub_folder}
                  placeholder="e.g. music/albums"
                  onChange={(e) => onChange({ sub_folder: e.target.value })}
                  className="input-field input-cyan"
                />
              </div>

              <div>
                <label className="field-label">Custom Name Prefix</label>
                <input
                  type="text"
                  value={form.custom_prefix}
                  onChange={(e) => onChange({ custom_prefix: e.target.value })}
                  className="input-field input-cyan"
                />
              </div>

              <div>
                <label className="field-label">Custom Output Filename</label>
                <input
                  type="text"
                  value={form.custom_filename}
                  onChange={(e) => onChange({ custom_filename: e.target.value })}
                  className="input-field input-cyan"
                />
              </div>

              <div>
                <label className="field-label">Playlist Item Limit</label>
                <input
                  type="number"
                  min="0"
                  value={form.item_limit}
                  onChange={(e) => onChange({ item_limit: Number(e.target.value) || 0 })}
                  className="input-field input-cyan"
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
```

Check `frontend/src/components/ui/DirectorySelect.tsx` for its `color` prop type. If it is a union of literals, add `'cyan'` to it and add the matching branch wherever it maps colour to CSS variables.

- [ ] **Step 3: Rewrite the panel**

Replace the entire contents of `frontend/src/components/DownloaderPanel.tsx`:

```tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import PanelLayout from './PanelLayout'
import DownloadJobCard from './downloader/DownloadJobCard'
import DownloadOptions from './downloader/DownloadOptions'
import { useDownloadStream } from '@/hooks/useDownloadStream'
import {
  cancelDownloadJob,
  createDownloads,
  deleteCookies,
  deleteDownloadJob,
  fetchDownloaderStatus,
  fetchMediaDirectories,
  postCookies,
  startDownloadJob,
} from '@/lib/api'
import type { DirectoryEntry, DownloadForm, DownloaderStatus } from '@/types'

const STORAGE_KEY = 'downloader-settings'

const DEFAULT_FORM: Omit<DownloadForm, 'url'> = {
  type: 'video',
  codec: 'auto',
  format: 'auto',
  quality: 'best',
  output_dir: '',
  base: '',
  auto_start: true,
  sub_folder: '',
  custom_prefix: '',
  custom_filename: '',
  item_limit: 0,
}

function loadSettings(): Omit<DownloadForm, 'url'> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? { ...DEFAULT_FORM, ...JSON.parse(raw) } : DEFAULT_FORM
  } catch {
    return DEFAULT_FORM
  }
}

/** Split pasted input into URLs — one per line, blanks ignored. */
export function parseUrls(input: string): string[] {
  return input
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

interface DownloaderPanelProps {
  onError: (err: string) => void
  onBack: () => void
  error: string
  showBaseLabel?: boolean
}

export default function DownloaderPanel({
  onError,
  onBack,
  error,
  showBaseLabel,
}: DownloaderPanelProps) {
  const { jobs, connected } = useDownloadStream()
  const [status, setStatus] = useState<DownloaderStatus | null>(null)
  const [directories, setDirectories] = useState<DirectoryEntry[]>([])
  const [isRefreshingDirs, setIsRefreshingDirs] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [localError, setLocalError] = useState('')
  const [form, setForm] = useState<DownloadForm>(() => ({ url: '', ...loadSettings() }))

  const urls = useMemo(() => parseUrls(form.url), [form.url])

  useEffect(() => {
    const id = window.setTimeout(() => {
      const { url: _ignored, ...settings } = form
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    }, 300)
    return () => window.clearTimeout(id)
  }, [form])

  const refreshStatus = useCallback(async () => {
    setStatus(await fetchDownloaderStatus())
  }, [])

  const refreshDirectories = useCallback(async () => {
    setIsRefreshingDirs(true)
    try {
      setDirectories((await fetchMediaDirectories()).directories)
    } finally {
      setIsRefreshingDirs(false)
    }
  }, [])

  useEffect(() => {
    void refreshStatus().catch(() => {})
    void refreshDirectories().catch(() => {})
  }, [refreshDirectories, refreshStatus])

  const patchForm = useCallback((patch: Partial<DownloadForm>) => {
    setForm((prev) => ({ ...prev, ...patch }))
  }, [])

  const submit = async (override?: string[]) => {
    const targets = override ?? urls
    if (targets.length === 0) {
      setLocalError('Please enter at least one URL')
      return
    }
    setLocalError('')
    onError('')
    const { url: _ignored, ...options } = form
    try {
      await createDownloads(targets, options)
      if (!override) setForm((prev) => ({ ...prev, url: '' }))
      await refreshStatus()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to create download')
    }
  }

  const guard = (action: Promise<unknown>, message: string) =>
    action.catch((err) => onError(err instanceof Error ? err.message : message))

  const active = jobs.filter((j) =>
    ['queued', 'downloading', 'transcoding'].includes(j.stage),
  )
  const history = jobs.filter((j) => ['done', 'error', 'cancelled'].includes(j.stage))

  return (
    <PanelLayout title="Downloader" onBack={onBack} maxWidth="920px">
      <div className="space-y-6">
        <div className="flex flex-col gap-3">
          <textarea
            value={form.url}
            placeholder="Paste a URL — or several, one per line"
            rows={urls.length > 1 ? Math.min(urls.length + 1, 8) : 1}
            onChange={(e) => patchForm({ url: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && urls.length <= 1) {
                e.preventDefault()
                void submit()
              }
            }}
            className="input-field input-cyan resize-y"
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void submit()}
              className="btn-submit btn-cyan h-[42px] !w-auto px-5 text-[0.85rem]"
            >
              {urls.length > 1 ? `Download ${urls.length} URLs` : 'Download'}
            </button>
            {urls.length > 1 && (
              <span className="text-[0.78rem] text-[var(--text-tertiary)]">
                {urls.length} URLs detected
              </span>
            )}
          </div>
        </div>

        {(localError || error) && (
          <div
            className="flex items-center justify-between rounded-lg border border-red-500/15 bg-red-500/[0.06] px-4 py-2.5"
            role="alert"
          >
            <p className="text-[0.8rem] text-red-400">{localError || error}</p>
            <button
              type="button"
              className="ml-3 shrink-0 text-[0.7rem] text-red-400/50"
              onClick={() => {
                setLocalError('')
                onError('')
              }}
              aria-label="Dismiss error"
            >
              dismiss
            </button>
          </div>
        )}

        <DownloadOptions
          form={form}
          onChange={patchForm}
          directories={directories}
          onRefreshDirectories={() => void refreshDirectories()}
          isRefreshingDirectories={isRefreshingDirs}
          showBaseLabel={showBaseLabel}
          advancedOpen={advancedOpen}
          onToggleAdvanced={() => setAdvancedOpen((v) => !v)}
        />

        <div className="flex flex-wrap items-center gap-3 rounded-[14px] border border-white/6 bg-white/[0.02] px-5 py-3">
          <span className="text-[0.72rem] font-medium uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
            Cookies
          </span>
          <label className="cursor-pointer rounded-lg border border-[var(--glass-border)] bg-[var(--bg-glass)] px-3 py-1.5 text-[0.8rem] text-[var(--text-secondary)] transition-all hover:border-[var(--glass-border-hover)] hover:text-[var(--text-primary)]">
            Upload cookies.txt
            <input
              type="file"
              className="hidden"
              accept=".txt"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) {
                  void guard(
                    postCookies(file).then(refreshStatus),
                    'Failed to upload cookies',
                  )
                }
                e.target.value = ''
              }}
            />
          </label>
          {status?.cookies_present && (
            <button
              type="button"
              onClick={() =>
                void guard(deleteCookies().then(refreshStatus), 'Failed to delete cookies')
              }
              className="rounded-lg border border-red-500/20 px-3 py-1.5 text-[0.8rem] text-red-400 transition-all hover:bg-red-500/10"
            >
              Remove
            </button>
          )}
          <span className="text-[0.75rem] text-[var(--text-tertiary)]">
            {status?.cookies_present ? 'cookies.txt loaded' : 'No cookies configured'}
          </span>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-[0.92rem] font-semibold">Active Downloads</h3>
            <span className="text-[0.78rem] text-[var(--text-tertiary)]">
              {active.length > 0 ? `${active.length} active` : 'idle'}
            </span>
          </div>
          {active.length > 0 ? (
            active.map((job) => (
              <DownloadJobCard
                key={job.job_id}
                job={job}
                showTranscodeStage={form.codec !== 'auto'}
                onCancel={(id) => void guard(cancelDownloadJob(id), 'Failed to cancel')}
                onDelete={(id) => void guard(deleteDownloadJob(id), 'Failed to delete')}
                onStart={(id) => void guard(startDownloadJob(id), 'Failed to start')}
                onRetry={(url) => void submit([url])}
              />
            ))
          ) : (
            <p className="py-3 text-center text-[0.8rem] text-[var(--text-tertiary)]">
              No active downloads
            </p>
          )}
        </div>

        <div className="space-y-3">
          <h3 className="text-[0.92rem] font-semibold">History</h3>
          {history.length > 0 ? (
            history.map((job) => (
              <DownloadJobCard
                key={job.job_id}
                job={job}
                showTranscodeStage={false}
                onCancel={(id) => void guard(cancelDownloadJob(id), 'Failed to cancel')}
                onDelete={(id) => void guard(deleteDownloadJob(id), 'Failed to delete')}
                onStart={(id) => void guard(startDownloadJob(id), 'Failed to start')}
                onRetry={(url) => void submit([url])}
              />
            ))
          ) : (
            <p className="py-4 text-center text-[0.82rem] text-[var(--text-tertiary)]">
              No recent downloads yet.
            </p>
          )}
        </div>

        <div className="flex items-center justify-center">
          <div className="inline-flex items-center gap-3 rounded-full border border-white/6 bg-white/[0.03] px-5 py-2 text-[0.72rem] text-[var(--text-tertiary)]">
            <span>yt-dlp {status?.yt_dlp_version ?? '...'}</span>
            <span className="text-white/10">·</span>
            <span>{connected ? 'Live' : 'Reconnecting...'}</span>
            <span className="text-white/10">·</span>
            <span>Queue: {status?.queue_depth ?? 0}</span>
          </div>
        </div>
      </div>
    </PanelLayout>
  )
}
```

- [ ] **Step 4: Update the call site**

`DownloaderPanel` no longer takes `onLog` or `log`. In `frontend/src/App.tsx`, find where `DownloaderPanel` is rendered and remove those two props, keeping `onError`, `onBack`, `error`, and `showBaseLabel`.

- [ ] **Step 5: Add a test for URL parsing**

Create `frontend/src/__tests__/downloaderPanel.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { parseUrls } from '@/components/DownloaderPanel'

describe('parseUrls', () => {
  it('returns a single url unchanged', () => {
    expect(parseUrls('https://example.com/a')).toEqual(['https://example.com/a'])
  })

  it('splits one url per line and drops blanks', () => {
    expect(parseUrls('https://a\n\n  https://b  \n')).toEqual(['https://a', 'https://b'])
  })

  it('returns an empty array for empty input', () => {
    expect(parseUrls('   \n  ')).toEqual([])
  })
})
```

- [ ] **Step 6: Run the full frontend suite and build**

Run: `cd frontend && npm run test && npm run build`
Expected: all tests PASS and the build succeeds. Fix any TypeScript errors from removed props or the `DirectorySelect` colour union.

- [ ] **Step 7: Format**

Run: `cd frontend && npm run format`

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/DownloaderPanel.tsx frontend/src/components/downloader frontend/src/App.tsx frontend/src/__tests__/downloaderPanel.test.ts
git commit -m "feat(downloader): rebuild panel on stream hook with stage display and inline bulk"
```

---

### Task 13: Documentation and branch cleanup

**Files:**

- Modify: `CLAUDE.md`, `README.md`, `backend/dependencies/.env.example`, `docker-compose.yml`

- [ ] **Step 1: Update the environment example**

In `backend/dependencies/.env.example`, replace any `DOWNLOADER_JOBS_DIR` entry with:

```bash
# Downloader
DOWNLOADS_DIR=/downloads
DOWNLOADER_DATA_DIR=/data/downloader
DOWNLOADER_WORKERS=3
DOWNLOADER_JOB_TTL=604800
# YT_DLP_COOKIES=/data/downloader/cookies.txt
```

- [ ] **Step 2: Update docker-compose**

In `docker-compose.yml`, replace the `DOWNLOADER_JOBS_DIR` environment entry with `DOWNLOADER_DATA_DIR=/data/downloader` and confirm the `/data` volume mount still covers it.

- [ ] **Step 3: Update CLAUDE.md**

In the root `CLAUDE.md`, under the Jellyfin_Media-Renamer entry, and in the repo's own `CLAUDE.md` if it documents endpoints, replace the downloader description with:

```markdown
- **Downloader**: queue-backed yt-dlp downloads. Worker pool (`DOWNLOADER_WORKERS`,
  default 3) drains a SQLite-persisted queue; jobs survive restarts. A job holds
  N output items, so playlists report per-item progress. Optional ffmpeg
  re-encode runs as a separate cancellable stage. State streams to the client
  over `GET /download/events`.
```

- [ ] **Step 4: Update README**

Replace the downloader section's endpoint list with the routes from Task 7, and note that bulk downloads are submitted as one request.

- [ ] **Step 5: Run the full suite one last time**

Run: `cd backend && python -m pytest && cd ../frontend && npm run test && npm run build`
Expected: everything passes.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md backend/dependencies/.env.example docker-compose.yml
git commit -m "docs(downloader): document the queue-backed rewrite and new settings"
```

- [ ] **Step 7: Delete the abandoned branch**

Only after everything above passes:

```bash
git branch -D feature/downloader
git push origin --delete feature/downloader
```

---

## Verification Checklist

Run before considering the rewrite complete:

- [ ] `cd backend && python -m pytest` — all green
- [ ] `cd frontend && npm run test` — all green
- [ ] `cd frontend && npm run build` — no TypeScript errors
- [ ] `docker compose up --build` — the downloader panel loads and the footer shows "Live"
- [ ] Paste 10 URLs at once — all 10 appear as jobs, none fails with 429
- [ ] Start a download, reload the page mid-transfer — progress resumes without duplication
- [ ] Cancel a download mid-transfer — job shows `cancelled`, not `error`
- [ ] Request an H.265 re-encode — the transcode stage shows moving progress and can be cancelled
- [ ] Download a playlist URL — each entry appears as its own item with its own progress
- [ ] Restart the backend with jobs queued — they resume
- [ ] No amber (`--accent-5`) references remain in downloader code
