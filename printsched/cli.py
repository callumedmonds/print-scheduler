"""Command line front end."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from . import config, printing, store
from .schedule import (DAILY, INTERVAL, ONCE, WEEKDAY_NAMES, WEEKLY, Schedule,
                       ScheduleError)
from .scheduler import setup_logging, tick

DAY_LOOKUP = {name.lower(): index for index, name in enumerate(WEEKDAY_NAMES)}
DAY_LOOKUP.update({"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                   "friday": 4, "saturday": 5, "sunday": 6})
DURATION = re.compile(r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?$", re.I)


def parse_when(text: str) -> datetime:
    """Accept '2026-09-22 09:00', '2026-09-22T09:00' or a bare '09:00' for today."""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        hour, minute = text.split(":")
        return datetime.now().replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    except ValueError:
        raise ScheduleError(f"{text!r} is not a date and time (try '2026-09-22 09:00')") from None


def parse_weekly(text: str) -> tuple[tuple[int, ...], str]:
    """Parse 'mon,wed,fri@09:00' into ((0,2,4), '09:00')."""
    if "@" not in text:
        raise ScheduleError("weekly needs days and a time, like 'mon,wed,fri@09:00'")
    days_part, time_part = text.rsplit("@", 1)
    days = []
    for token in days_part.split(","):
        key = token.strip().lower()
        if key not in DAY_LOOKUP:
            raise ScheduleError(f"{token!r} is not a weekday (use mon..sun)")
        days.append(DAY_LOOKUP[key])
    return tuple(sorted(set(days))), time_part.strip()


def parse_duration(text: str) -> int:
    match = DURATION.match(text.strip())
    if not match:
        raise ScheduleError(f"{text!r} is not a duration (try '30m' or '2h')")
    amount, unit = int(match.group(1)), (match.group(2) or "m").lower()
    return amount * 60 if unit.startswith("h") else amount


def schedule_from_args(args: argparse.Namespace) -> Schedule:
    if args.at:
        return Schedule(kind=ONCE, run_at=parse_when(args.at))
    if args.daily:
        return Schedule(kind=DAILY, time_of_day=args.daily.strip())
    if args.weekly:
        days, time_of_day = parse_weekly(args.weekly)
        return Schedule(kind=WEEKLY, time_of_day=time_of_day, weekdays=days)
    if args.every:
        return Schedule(kind=INTERVAL, interval_minutes=parse_duration(args.every))
    raise ScheduleError("choose one of --at, --daily, --weekly or --every")


def cmd_add(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser().resolve()
    if not source.is_file():
        print(f"error: no such file: {source}", file=sys.stderr)
        return 1
    schedule = schedule_from_args(args)
    mode = store.LIVE if args.live else store.SNAPSHOT
    path = source if args.live else store.spool(source)

    conn = store.connect()
    try:
        job_id = store.create_job(
            conn, name=args.name or source.stem, source_path=path, schedule=schedule,
            source_mode=mode, printer=args.printer, copies=args.copies,
            options=" ".join(args.option or []),
        )
        row = store.get_job(conn, job_id)
        print(f"Job {job_id}: {row['name']} -- {schedule.describe()}")
        print(f"  next print: {row['next_run'] or 'never'}")
        if mode == store.LIVE:
            print(f"  live file:  {path} (re-read each time)")
    finally:
        conn.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = store.connect()
    try:
        rows = store.list_jobs(conn)
        if not rows:
            print("No scheduled prints yet. Add one with: printsched add FILE --daily 09:00")
            return 0
        print(f"{'ID':>3}  {'STATUS':<8} {'NEXT PRINT':<17} {'NAME':<24} SCHEDULE")
        for row in rows:
            status = "paused" if not row["enabled"] else (row["last_status"] or "waiting")
            nxt = (row["next_run"] or "-").replace("T", " ")[:16]
            flag = " [file missing]" if not Path(row["source_path"]).exists() else ""
            print(f"{row['id']:>3}  {status:<8} {nxt:<17} {row['name'][:24]:<24} "
                  f"{store.schedule_of(row).describe()}{flag}")
    finally:
        conn.close()
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    conn = store.connect()
    try:
        rows = store.recent_runs(conn, limit=args.limit, job_id=args.job)
        if not rows:
            print("Nothing has printed yet.")
            return 0
        for row in rows:
            when = row["ran_at"].replace("T", " ")[:16]
            print(f"{when}  {row['status']:<8} {row['job_name'][:24]:<24} {row['message']}")
    finally:
        conn.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .scheduler import run_job

    conn = store.connect()
    try:
        row = store.get_job(conn, args.id)
        if row is None:
            print(f"error: no job {args.id}", file=sys.stderr)
            return 1
        status, message = run_job(conn, row, force=True)
        print(f"{status}: {message}")
        return 0 if status != "error" else 1
    finally:
        conn.close()


def cmd_rm(args: argparse.Namespace) -> int:
    conn = store.connect()
    try:
        if store.delete_job(conn, args.id):
            print(f"Deleted job {args.id}.")
            return 0
        print(f"error: no job {args.id}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_toggle(args: argparse.Namespace, enable: bool) -> int:
    conn = store.connect()
    try:
        store.set_enabled(conn, args.id, enable)
        row = store.get_job(conn, args.id)
        print(f"Job {args.id} {'resumed' if enable else 'paused'}."
              + (f" Next print: {row['next_run']}" if enable else ""))
        return 0
    except KeyError:
        print(f"error: no job {args.id}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_printers(args: argparse.Namespace) -> int:
    printers = printing.list_printers()
    if not printers:
        print("No printers found. Is CUPS running?")
        return 1
    for printer in printers:
        print(f"{'*' if printer.is_default else ' '} {printer.name:<28} {printer.state}")
    print("\n* = system default")
    queue = printing.queue_status()
    if queue:
        print("\nWaiting in the queue:")
        for line in queue:
            print(f"  {line}")
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    """One pass over due jobs, for anyone who would rather drive this from cron."""
    setup_logging(verbose=True)
    conn = store.connect()
    try:
        print(f"Handled {tick(conn)} due job(s).")
        return 0
    finally:
        conn.close()


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="printsched",
        description="Schedule documents to print at a time you choose.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  printsched serve                                   open the web app
  printsched add invoice.pdf --at "2026-09-22 09:00"
  printsched add jobsheet.pdf --daily 08:30 --copies 2
  printsched add rota.pdf --weekly mon,fri@07:45 --live
  printsched add label.pdf --every 2h -o sides=two-sided-long-edge
  printsched list""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the web app (and the scheduler)")
    p_serve.add_argument("--host", default=config.HOST)
    p_serve.add_argument("--port", type=int, default=config.PORT)
    p_serve.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p_serve.set_defaults(func=cmd_serve)

    p_add = sub.add_parser("add", help="schedule a document")
    p_add.add_argument("file")
    when = p_add.add_mutually_exclusive_group(required=True)
    when.add_argument("--at", metavar="'YYYY-MM-DD HH:MM'", help="print once, then stop")
    when.add_argument("--daily", metavar="HH:MM", help="print every day at this time")
    when.add_argument("--weekly", metavar="mon,wed@09:00", help="print on these weekdays")
    when.add_argument("--every", metavar="30m", help="print on a repeating interval")
    p_add.add_argument("--name", help="label for the job (defaults to the file name)")
    p_add.add_argument("--printer", help="queue name (defaults to the system default)")
    p_add.add_argument("--copies", type=int, default=1)
    p_add.add_argument("-o", "--option", action="append", metavar="KEY=VALUE",
                       help="lp option, e.g. -o sides=two-sided-long-edge")
    p_add.add_argument("--live", action="store_true",
                       help="re-read the file at print time instead of printing a snapshot")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="show scheduled prints")
    p_list.set_defaults(func=cmd_list)

    p_runs = sub.add_parser("runs", help="show recent print history")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.add_argument("--job", type=int, help="only this job id")
    p_runs.set_defaults(func=cmd_runs)

    p_run = sub.add_parser("run", help="print a job right now")
    p_run.add_argument("id", type=int)
    p_run.set_defaults(func=cmd_run)

    p_rm = sub.add_parser("rm", help="delete a job")
    p_rm.add_argument("id", type=int)
    p_rm.set_defaults(func=cmd_rm)

    p_pause = sub.add_parser("pause", help="stop a job printing, keeping it in the list")
    p_pause.add_argument("id", type=int)
    p_pause.set_defaults(func=lambda a: cmd_toggle(a, False))

    p_resume = sub.add_parser("resume", help="restart a paused job")
    p_resume.add_argument("id", type=int)
    p_resume.set_defaults(func=lambda a: cmd_toggle(a, True))

    p_printers = sub.add_parser("printers", help="list printers CUPS knows about")
    p_printers.set_defaults(func=cmd_printers)

    p_tick = sub.add_parser("tick", help="process due jobs once and exit")
    p_tick.set_defaults(func=cmd_tick)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
