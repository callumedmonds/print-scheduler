"""Talking to CUPS through the lp/lpstat command line tools."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REQUEST_ID = re.compile(r"request id is (\S+)")


class PrintError(RuntimeError):
    """A print could not be handed to CUPS."""


@dataclass(frozen=True)
class Printer:
    name: str
    state: str
    is_default: bool


def _run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise PrintError(
            f"{argv[0]} not found. Install the CUPS client tools: sudo apt install cups-client"
        ) from None
    except subprocess.TimeoutExpired:
        raise PrintError(f"{argv[0]} did not respond within {timeout}s") from None


def list_printers() -> list[Printer]:
    """Every queue CUPS knows about, default first."""
    if not shutil.which("lpstat"):
        return []
    default = ""
    result = _run(["lpstat", "-d"])
    match = re.search(r"destination:\s*(\S+)", result.stdout)
    if match:
        default = match.group(1)

    printers: list[Printer] = []
    for line in _run(["lpstat", "-p"]).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "printer":
            name = parts[1]
            state = "idle" if " is idle" in line else ("disabled" if "disabled" in line else "busy")
            printers.append(Printer(name=name, state=state, is_default=name == default))
    printers.sort(key=lambda p: (not p.is_default, p.name))
    return printers


def default_printer() -> str | None:
    for printer in list_printers():
        if printer.is_default:
            return printer.name
    return None


def build_command(
    path: Path,
    *,
    printer: str | None = None,
    copies: int = 1,
    title: str | None = None,
    options: str = "",
) -> list[str]:
    """Assemble the lp invocation for a job.

    Split out from send_to_printer so the exact command can be shown in the UI
    and asserted in tests without printing anything.
    """
    argv = ["lp"]
    if printer:
        argv += ["-d", printer]
    if copies and copies > 1:
        argv += ["-n", str(copies)]
    if title:
        argv += ["-t", title[:255]]
    for option in shlex.split(options or ""):
        argv += ["-o", option]
    argv.append(str(path))
    return argv


def send_to_printer(
    path: Path,
    *,
    printer: str | None = None,
    copies: int = 1,
    title: str | None = None,
    options: str = "",
) -> str:
    """Hand a file to CUPS. Returns the CUPS job id, or raises PrintError.

    Success here means CUPS accepted the job, not that ink hit paper -- an
    offline printer will hold the job in its queue.
    """
    if not path.exists():
        raise PrintError(f"file no longer exists: {path}")
    if path.is_dir():
        raise PrintError(f"{path} is a folder, not a document")
    if path.stat().st_size == 0:
        raise PrintError(f"{path} is empty")

    argv = build_command(path, printer=printer, copies=copies, title=title, options=options)
    result = _run(argv, timeout=60)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"lp exited {result.returncode}"
        raise PrintError(detail)

    match = REQUEST_ID.search(result.stdout)
    return match.group(1) if match else "(accepted)"


def queue_status(printer: str | None = None) -> list[str]:
    """Lines describing what is currently waiting in the CUPS queue."""
    argv = ["lpstat", "-o"] + ([printer] if printer else [])
    result = _run(argv)
    return [line for line in result.stdout.splitlines() if line.strip()]


def cancel(cups_job: str) -> None:
    result = _run(["cancel", cups_job])
    if result.returncode != 0:
        raise PrintError((result.stderr or "could not cancel").strip())
