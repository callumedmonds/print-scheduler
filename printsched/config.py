"""Filesystem locations and tunables."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


DATA_DIR = Path(os.environ.get("PRINTSCHED_HOME") or _xdg("XDG_DATA_HOME", ".local/share") / "print-scheduler")
DB_PATH = DATA_DIR / "scheduler.db"
SPOOL_DIR = DATA_DIR / "spool"
LOG_PATH = DATA_DIR / "printsched.log"

HOST = os.environ.get("PRINTSCHED_HOST", "127.0.0.1")
PORT = int(os.environ.get("PRINTSCHED_PORT", "8765"))

# How often the scheduler thread wakes up to look for due jobs.
TICK_SECONDS = int(os.environ.get("PRINTSCHED_TICK", "15"))

# If the machine was asleep and a *recurring* job's slot passed by more than
# this many minutes, skip it rather than printing a stale pile on wake-up.
# One-off jobs always run late, because you explicitly asked for that document.
CATCH_UP_GRACE_MINUTES = int(os.environ.get("PRINTSCHED_GRACE", "60"))

# Largest file accepted through the web upload form.
MAX_UPLOAD_BYTES = int(os.environ.get("PRINTSCHED_MAX_UPLOAD", str(64 * 1024 * 1024)))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
