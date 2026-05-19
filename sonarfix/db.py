"""SQLite-backed job persistence for SonarFix Agent.

Provides a thin wrapper around a `jobs` table so that job state survives
API server restarts.  All reads/writes are synchronised with a threading
Lock so the module is safe to call from background threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path.home() / ".sonarfix" / "jobs.db"

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT PRIMARY KEY,
            status      TEXT NOT NULL DEFAULT 'queued',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            config      TEXT,        -- JSON: original FixJobRequest
            log         TEXT,        -- JSON array of {ts, msg}
            result      TEXT,        -- JSON
            extra       TEXT         -- JSON: any other fields (fix_branch, repo_dir, error, etc.)
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d: Dict[str, Any] = dict(row)
    d["log"] = json.loads(d["log"] or "[]")
    d["result"] = json.loads(d["result"] or "null")
    d["config"] = json.loads(d["config"] or "null")
    extra = json.loads(d.pop("extra") or "{}")
    d.update(extra)
    # Expose request under the key the rest of the code expects
    if d.get("config") is not None and "request" not in d:
        d["request"] = d["config"]
    return d


def save_job(job: Dict[str, Any]) -> None:
    """Insert or replace a job record."""
    extra_keys = {
        k: v for k, v in job.items()
        if k not in ("id", "status", "created_at", "updated_at", "request", "log", "result")
    }
    conn = _get_conn()
    with _lock:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (id, status, created_at, updated_at, config, log, result, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["id"],
                job.get("status", "queued"),
                job.get("created_at", _now()),
                _now(),
                json.dumps(job.get("request")),
                json.dumps(job.get("log", [])),
                json.dumps(job.get("result")),
                json.dumps(extra_keys),
            ),
        )
        conn.commit()


def load_jobs() -> Dict[str, Dict[str, Any]]:
    """Load all jobs from DB into a dict keyed by job id."""
    conn = _get_conn()
    with _lock:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return {row["id"]: _row_to_dict(row) for row in rows}


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with _lock:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_jobs_paged(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_jobs() -> int:
    conn = _get_conn()
    with _lock:
        row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
    return row[0]
