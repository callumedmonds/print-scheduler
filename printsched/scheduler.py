"""The loop that notices due jobs and prints them."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config, store
from .printing import PrintError, send_to_printer
from .schedule import is_missed, next_run

log = logging.getLogger("printsched")


class RunOutcome:
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


def run_job(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[str, str]:
    """Print one job now and move its schedule forward.

    `force` is the Print now button: it ignores the missed-slot grace period and
    leaves the job's own next_run untouched.
    """
    now = now or datetime.now()
    schedule = store.schedule_of(row)
    job_id = int(row["id"])
    due = store.parse(row["next_run"]) or now

    if not force and is_missed(schedule, due, now, config.CATCH_UP_GRACE_MINUTES):
        upcoming = next_run(schedule, now)
        store.reschedule(conn, job_id, upcoming)
        message = f"skipped the {due:%a %d %b %H:%M} slot -- computer was not awake for it"
        log.info("job %s: %s", job_id, message)
        return RunOutcome.SKIPPED, message

    path = Path(row["source_path"])
    try:
        cups_job = send_to_printer(
            path,
            printer=row["printer"],
            copies=int(row["copies"]),
            title=row["name"],
            options=row["options"],
        )
        status, message = RunOutcome.OK, f"sent to {row['printer'] or 'default printer'} as {cups_job}"
    except PrintError as exc:
        cups_job, status, message = None, RunOutcome.ERROR, str(exc)
        log.warning("job %s failed: %s", job_id, exc)

    if force:
        # A manual print must not consume the scheduled slot.
        store.update_after_run(
            conn, job_id, ran_at=now, status=status, message=message,
            cups_job=cups_job, next_due=store.parse(row["next_run"]),
        )
    else:
        store.update_after_run(
            conn, job_id, ran_at=now, status=status, message=message,
            cups_job=cups_job, next_due=next_run(schedule, now),
        )
    if status == RunOutcome.OK:
        log.info("job %s (%s): %s", job_id, row["name"], message)
    return status, message


def tick(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Process every job that has come due. Returns how many were handled."""
    now = now or datetime.now()
    handled = 0
    for row in store.due_jobs(conn, now):
        try:
            run_job(conn, row, now=now)
        except Exception:  # one bad job must not stop the others
            log.exception("unexpected error running job %s", row["id"])
            store.update_after_run(
                conn, int(row["id"]), ran_at=now, status=RunOutcome.ERROR,
                message="internal error -- see printsched.log",
                cups_job=None, next_due=next_run(store.schedule_of(row), now),
            )
        handled += 1
    return handled


class SchedulerThread(threading.Thread):
    """Background ticker. Its own connection, since SQLite objects are per-thread."""

    def __init__(self, interval: int | None = None) -> None:
        super().__init__(name="printsched-scheduler", daemon=True)
        self.interval = interval or config.TICK_SECONDS
        self._stop = threading.Event()

    def run(self) -> None:
        conn = store.connect()
        log.info("scheduler started, checking every %ss", self.interval)
        try:
            while not self._stop.is_set():
                try:
                    tick(conn)
                except Exception:
                    log.exception("scheduler tick failed")
                self._stop.wait(self.interval)
        finally:
            conn.close()
            log.info("scheduler stopped")

    def stop(self) -> None:
        self._stop.set()


def setup_logging(verbose: bool = False) -> None:
    config.ensure_dirs()
    handlers: list[logging.Handler] = [logging.FileHandler(config.LOG_PATH)]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
