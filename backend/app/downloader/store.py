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

    def _read_job_locked(self, job_id: str) -> Job | None:
        """Read job while already holding self._lock. Do not call without holding lock."""
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        item_rows = self._conn.execute(
            "SELECT * FROM items WHERE job_id = ? ORDER BY idx", (job_id,)
        ).fetchall()
        return _to_job(row, item_rows)

    def create_job(
        self, url: str, options: dict[str, Any], stage: str = "queued"
    ) -> str:
        job_id = str(uuid.uuid4())
        job = None
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, url, options, stage) VALUES (?, ?, ?, ?)",
                (job_id, url, json.dumps(options), stage),
            )
            self._conn.commit()
            job = self._read_job_locked(job_id)
        if job is not None and self._on_change is not None:
            self._on_change(job)
        return job_id

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._read_job_locked(job_id)

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
        job = None
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET stage = ?, error = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (stage, error, job_id),
            )
            self._conn.commit()
            job = self._read_job_locked(job_id)
        if job is not None and self._on_change is not None:
            self._on_change(job)

    def upsert_item(self, job_id: str, index: int, **fields: Any) -> None:
        unknown = set(fields) - set(_ITEM_FIELDS)
        if unknown:
            raise ValueError(f"Unknown item fields: {sorted(unknown)}")
        job = None
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
            job = self._read_job_locked(job_id)
        if job is not None and self._on_change is not None:
            self._on_change(job)

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
