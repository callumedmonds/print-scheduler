"""SQLite persistence for jobs and their run history."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config
from .schedule import DAILY, INTERVAL, ONCE, WEEKLY, Schedule, ScheduleError, first_run

SNAPSHOT = "snapshot"
LIVE = "live"
SOURCE_MODES = (SNAPSHOT, LIVE)

ISO = "%Y-%m-%dT%H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    source_path      TEXT    NOT NULL,
    source_mode      TEXT    NOT NULL DEFAULT 'snapshot',
    printer          TEXT,
    copies           INTEGER NOT NULL DEFAULT 1,
    options          TEXT    NOT NULL DEFAULT '',
    kind             TEXT    NOT NULL,
    run_at           TEXT,
    time_of_day      TEXT,
    weekdays         TEXT    NOT NULL DEFAULT '',
    interval_minutes INTEGER,
    enabled          INTEGER NOT NULL DEFAULT 1,
    next_run         TEXT,
    created_at       TEXT    NOT NULL,
    last_run_at      TEXT,
    last_status      TEXT,
    last_message     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ran_at    TEXT    NOT NULL,
    status    TEXT    NOT NULL,
    message   TEXT    NOT NULL DEFAULT '',
    cups_job  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_due  ON jobs(enabled, next_run);
CREATE INDEX IF NOT EXISTS idx_runs_job  ON runs(job_id, ran_at DESC);
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def fmt(moment: datetime | None) -> str | None:
    return moment.strftime(ISO) if moment else None


def parse(text: str | None) -> datetime | None:
    return datetime.strptime(text, ISO) if text else None


def schedule_of(row: sqlite3.Row | dict) -> Schedule:
    """Rebuild the Schedule value object from a stored row."""
    weekdays = tuple(int(d) for d in str(row["weekdays"] or "").split(",") if d != "")
    return Schedule(
        kind=row["kind"],
        run_at=parse(row["run_at"]),
        time_of_day=row["time_of_day"],
        weekdays=weekdays,
        interval_minutes=row["interval_minutes"],
    )


def spool(source: Path) -> Path:
    """Copy a document into our own spool so later edits cannot change it."""
    config.ensure_dirs()
    target = config.SPOOL_DIR / f"{uuid.uuid4().hex[:12]}{source.suffix}"
    shutil.copy2(source, target)
    return target


def save_upload(filename: str, data: bytes) -> Path:
    """Persist bytes that arrived through the web form."""
    config.ensure_dirs()
    suffix = Path(filename).suffix
    target = config.SPOOL_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    target.write_bytes(data)
    return target


def create_job(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_path: Path,
    schedule: Schedule,
    source_mode: str = SNAPSHOT,
    printer: str | None = None,
    copies: int = 1,
    options: str = "",
    now: datetime | None = None,
) -> int:
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"source_mode must be one of {SOURCE_MODES}")
    now = now or datetime.now()
    due = first_run(schedule, now)
    cursor = conn.execute(
        """INSERT INTO jobs (name, source_path, source_mode, printer, copies, options,
                             kind, run_at, time_of_day, weekdays, interval_minutes,
                             enabled, next_run, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
        (
            name,
            str(source_path),
            source_mode,
            printer,
            max(1, int(copies)),
            options or "",
            schedule.kind,
            fmt(schedule.run_at),
            schedule.time_of_day,
            ",".join(str(d) for d in sorted(schedule.weekdays)),
            schedule.interval_minutes,
            fmt(due),
            fmt(now),
        ),
    )
    return int(cursor.lastrowid)


def list_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT * FROM jobs
               ORDER BY enabled DESC,
                        CASE WHEN next_run IS NULL THEN 1 ELSE 0 END,
                        next_run ASC, id ASC"""
        )
    )


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def due_jobs(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT * FROM jobs
               WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?
               ORDER BY next_run ASC""",
            (fmt(now),),
        )
    )


def set_enabled(conn: sqlite3.Connection, job_id: int, enabled: bool, now: datetime | None = None) -> None:
    """Pause or resume a job, recomputing the next slot when resuming."""
    row = get_job(conn, job_id)
    if row is None:
        raise KeyError(job_id)
    if not enabled:
        conn.execute("UPDATE jobs SET enabled = 0 WHERE id = ?", (job_id,))
        return
    now = now or datetime.now()
    due = first_run(schedule_of(row), now)
    conn.execute("UPDATE jobs SET enabled = 1, next_run = ? WHERE id = ?", (fmt(due), job_id))


def update_after_run(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    ran_at: datetime,
    status: str,
    message: str,
    cups_job: str | None,
    next_due: datetime | None,
) -> None:
    conn.execute(
        "INSERT INTO runs (job_id, ran_at, status, message, cups_job) VALUES (?,?,?,?,?)",
        (job_id, fmt(ran_at), status, message[:2000], cups_job),
    )
    conn.execute(
        """UPDATE jobs
           SET last_run_at = ?, last_status = ?, last_message = ?,
               next_run = ?, enabled = CASE WHEN ? IS NULL THEN 0 ELSE enabled END
           WHERE id = ?""",
        (fmt(ran_at), status, message[:2000], fmt(next_due), fmt(next_due), job_id),
    )


def reschedule(conn: sqlite3.Connection, job_id: int, next_due: datetime | None) -> None:
    conn.execute("UPDATE jobs SET next_run = ? WHERE id = ?", (fmt(next_due), job_id))


def delete_job(conn: sqlite3.Connection, job_id: int) -> bool:
    row = get_job(conn, job_id)
    if row is None:
        return False
    # Only remove the spooled copy; a 'live' job points at the user's own file.
    if row["source_mode"] == SNAPSHOT:
        spooled = Path(row["source_path"])
        if spooled.is_relative_to(config.SPOOL_DIR) and spooled.exists():
            spooled.unlink(missing_ok=True)
    conn.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return True


def recent_runs(conn: sqlite3.Connection, limit: int = 50, job_id: int | None = None) -> list[sqlite3.Row]:
    if job_id is not None:
        return list(
            conn.execute(
                """SELECT runs.*, jobs.name AS job_name FROM runs
                   JOIN jobs ON jobs.id = runs.job_id
                   WHERE job_id = ? ORDER BY ran_at DESC LIMIT ?""",
                (job_id, limit),
            )
        )
    return list(
        conn.execute(
            """SELECT runs.*, jobs.name AS job_name FROM runs
               JOIN jobs ON jobs.id = runs.job_id
               ORDER BY ran_at DESC LIMIT ?""",
            (limit,),
        )
    )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = {key: row[key] for key in row.keys()}
    data["enabled"] = bool(data.get("enabled"))
    return data
