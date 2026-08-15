"""SQLite persistence for presets, rules, jobs and retention.

Mirrors ``downloader/store.py``: one connection shared across threads under a
lock, schema applied on open, ``sqlite3.Row`` factory.

State survives restart deliberately. An encode is minutes to hours of GPU time
against a file the user cares about; losing the queue on a container restart
matters far more here than for a download that can simply be re-fetched.
"""

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.encoder.presets import NamedPreset
from app.encoder.rules import Condition, Rule

TERMINAL_STAGES = ("done", "failed", "skipped", "cancelled")

# Stages a restart must requeue. `pending` (awaiting review) and `blocked`
# (awaiting a human decision) are excluded on purpose: both mean "a person has
# not answered yet", and resuming them would answer for them.
_RESUMABLE_STAGES = ("queued", "encoding", "swapping")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                   TEXT PRIMARY KEY,
    source_path          TEXT NOT NULL,
    preset_name          TEXT,
    rule_id              TEXT,
    stage                TEXT NOT NULL,
    progress             REAL NOT NULL DEFAULT 0.0,
    remote_job_id        TEXT,
    output_path          TEXT,
    error                TEXT,
    error_code           TEXT,
    facts                TEXT,
    original_size        INTEGER,
    encoded_size         INTEGER,
    original_kept_path   TEXT,
    original_expires_at  REAL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
-- One live job per file. The watcher can see the same path twice (a second
-- filesystem event, a rescan after restart); two jobs encoding one file would
-- race for the same output and one would swap over the other's result.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_source
    ON jobs(source_path)
    WHERE stage NOT IN ('done', 'failed', 'skipped', 'cancelled');
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS presets (
    name          TEXT PRIMARY KEY,
    encoder       TEXT NOT NULL,
    video_preset  TEXT NOT NULL DEFAULT '',
    file_format   TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rules (
    id          TEXT PRIMARY KEY,
    position    INTEGER NOT NULL,
    conditions  TEXT NOT NULL,
    target      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_position ON rules(position);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


@dataclass
class Job:
    id: str
    source_path: str
    stage: str
    progress: float = 0.0
    preset_name: str | None = None
    rule_id: str | None = None
    remote_job_id: str | None = None
    output_path: str | None = None
    error: str | None = None
    error_code: str | None = None
    facts: dict = field(default_factory=dict)
    original_size: int | None = None
    encoded_size: int | None = None
    original_kept_path: str | None = None
    original_expires_at: float | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StoredPreset:
    name: str
    encoder: str
    video_preset: str
    file_format: str
    body: dict


class EncoderStore:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- jobs ------------------------------------------------------------

    def create_job(self, source_path: str) -> Job:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, source_path, stage) VALUES (?, ?, 'settling')",
                (job_id, source_path),
            )
            self._conn.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _to_job(row) if row else None

    def list_jobs(self, limit: int = 200) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_to_job(r) for r in rows]

    def set_stage(self, job_id: str, stage: str, error: str | None = None,
                  error_code: str | None = None) -> None:
        self._update(job_id, stage=stage, error=error, error_code=error_code)

    def set_progress(self, job_id: str, pct: float) -> None:
        self._update(job_id, progress=float(pct))

    def set_remote_job(self, job_id: str, remote_job_id: str) -> None:
        self._update(job_id, remote_job_id=remote_job_id)

    def set_result(self, job_id: str, output_path: str, encoded_size: int) -> None:
        self._update(job_id, output_path=output_path, encoded_size=encoded_size)

    def set_plan(self, job_id: str, preset_name: str | None, rule_id: str | None,
                 facts: dict, original_size: int) -> None:
        self._update(job_id, preset_name=preset_name, rule_id=rule_id,
                     facts=json.dumps(facts), original_size=original_size)

    def set_retention(self, job_id: str, kept_path: str, expires_at: float) -> None:
        self._update(job_id, original_kept_path=kept_path,
                     original_expires_at=expires_at)

    def clear_retention(self, job_id: str) -> None:
        self._update(job_id, original_kept_path=None, original_expires_at=None)

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def active_source_paths(self) -> set[str]:
        placeholders = ",".join("?" * len(TERMINAL_STAGES))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT source_path FROM jobs WHERE stage NOT IN ({placeholders})",
                TERMINAL_STAGES,
            ).fetchall()
        return {r["source_path"] for r in rows}

    def reset_active_for_recovery(self) -> list[Job]:
        """Requeue jobs a restart interrupted, and return them."""
        placeholders = ",".join("?" * len(_RESUMABLE_STAGES))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE stage IN ({placeholders})",
                _RESUMABLE_STAGES,
            ).fetchall()
            self._conn.execute(
                f"UPDATE jobs SET stage = 'queued', updated_at = datetime('now') "
                f"WHERE stage IN ({placeholders})",
                _RESUMABLE_STAGES,
            )
            self._conn.commit()
        return [_to_job(r) for r in rows]

    def purge_expired(self, ttl_seconds: int) -> int:
        placeholders = ",".join("?" * len(TERMINAL_STAGES))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM jobs WHERE stage IN ({placeholders}) "
                f"AND updated_at <= datetime('now', ?)",
                (*TERMINAL_STAGES, f"-{int(ttl_seconds)} seconds"),
            )
            self._conn.commit()
            return cur.rowcount

    def due_retentions(self, now: float) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE original_kept_path IS NOT NULL "
                "AND original_expires_at IS NOT NULL AND original_expires_at <= ?",
                (now,),
            ).fetchall()
        return [_to_job(r) for r in rows]

    def _update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {assignments}, updated_at = datetime('now') "
                f"WHERE id = ?",
                (*fields.values(), job_id),
            )
            self._conn.commit()

    # ---- presets ---------------------------------------------------------

    def replace_presets(self, presets: list[NamedPreset]) -> None:
        """Upsert by name. Additive across imports: a second preset file must
        not discard the first, but re-importing a name updates it."""
        with self._lock:
            self._conn.executemany(
                "INSERT INTO presets (name, encoder, video_preset, file_format, body) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "encoder = excluded.encoder, video_preset = excluded.video_preset, "
                "file_format = excluded.file_format, body = excluded.body",
                [(p.name, p.encoder, p.video_preset, p.file_format,
                  json.dumps(p.body)) for p in presets],
            )
            self._conn.commit()

    def list_presets(self) -> list[StoredPreset]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM presets ORDER BY name").fetchall()
        return [
            StoredPreset(name=r["name"], encoder=r["encoder"],
                         video_preset=r["video_preset"], file_format=r["file_format"],
                         body=json.loads(r["body"]))
            for r in rows
        ]

    def delete_preset(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM presets WHERE name = ?", (name,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---- rules -----------------------------------------------------------

    def replace_rules(self, rules: list[Rule]) -> None:
        """Full replacement, in one transaction.

        Rule order *is* the semantics -- first match wins -- so merging would
        make the effective order depend on insertion history rather than on
        what the user arranged.
        """
        with self._lock:
            self._conn.execute("DELETE FROM rules")
            self._conn.executemany(
                "INSERT INTO rules (id, position, conditions, target) "
                "VALUES (?, ?, ?, ?)",
                [
                    (r.id, i,
                     json.dumps([{"field": c.field, "op": c.op, "value": c.value}
                                 for c in r.conditions]),
                     r.target)
                    for i, r in enumerate(rules)
                ],
            )
            self._conn.commit()

    def list_rules(self) -> list[Rule]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM rules ORDER BY position"
            ).fetchall()
        return [
            Rule(
                id=r["id"],
                conditions=[Condition(field=c["field"], op=c["op"], value=c["value"])
                            for c in json.loads(r["conditions"])],
                target=r["target"],
            )
            for r in rows
        ]

    # ---- settings --------------------------------------------------------

    def get_setting(self, key: str, default: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()


def _to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], source_path=row["source_path"], stage=row["stage"],
        progress=row["progress"], preset_name=row["preset_name"],
        rule_id=row["rule_id"], remote_job_id=row["remote_job_id"],
        output_path=row["output_path"], error=row["error"],
        error_code=row["error_code"],
        facts=json.loads(row["facts"]) if row["facts"] else {},
        original_size=row["original_size"], encoded_size=row["encoded_size"],
        original_kept_path=row["original_kept_path"],
        original_expires_at=row["original_expires_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
