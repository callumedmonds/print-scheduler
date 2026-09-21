"""Schedule maths.

Pure functions: given a schedule spec and a reference time, work out when the
job should next print. Kept free of database and printer concerns so the rules
can be tested directly.

All times are naive local time. A job set for 09:00 prints at 09:00 on the wall
clock, including across a daylight-saving change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

ONCE = "once"
DAILY = "daily"
WEEKLY = "weekly"
INTERVAL = "interval"
KINDS = (ONCE, DAILY, WEEKLY, INTERVAL)

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ScheduleError(ValueError):
    """The schedule spec does not describe a runnable schedule."""


@dataclass(frozen=True)
class Schedule:
    kind: str
    run_at: datetime | None = None          # ONCE
    time_of_day: str | None = None          # DAILY / WEEKLY, "HH:MM"
    weekdays: tuple[int, ...] = ()           # WEEKLY, 0=Monday
    interval_minutes: int | None = None      # INTERVAL

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ScheduleError(f"unknown schedule kind {self.kind!r}")
        if self.kind == ONCE and self.run_at is None:
            raise ScheduleError("a one-off schedule needs a date and time")
        if self.kind in (DAILY, WEEKLY):
            parse_time_of_day(self.time_of_day)
        if self.kind == WEEKLY and not self.weekdays:
            raise ScheduleError("a weekly schedule needs at least one weekday")
        if self.kind == WEEKLY and any(d < 0 or d > 6 for d in self.weekdays):
            raise ScheduleError("weekdays must be 0 (Monday) to 6 (Sunday)")
        if self.kind == INTERVAL and (self.interval_minutes or 0) < 1:
            raise ScheduleError("an interval schedule needs at least 1 minute")

    def describe(self) -> str:
        if self.kind == ONCE:
            return f"once on {self.run_at:%a %d %b %Y at %H:%M}"
        if self.kind == DAILY:
            return f"every day at {self.time_of_day}"
        if self.kind == WEEKLY:
            days = ", ".join(WEEKDAY_NAMES[d] for d in sorted(self.weekdays))
            return f"every {days} at {self.time_of_day}"
        mins = self.interval_minutes or 0
        if mins % 60 == 0:
            hours = mins // 60
            return f"every {hours} hour{'s' if hours != 1 else ''}"
        return f"every {mins} minute{'s' if mins != 1 else ''}"


def parse_time_of_day(value: str | None) -> tuple[int, int]:
    """Parse "HH:MM" into (hour, minute), raising ScheduleError if malformed."""
    if not value:
        raise ScheduleError("a time of day is required, as HH:MM")
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise ScheduleError(f"{value!r} is not a time of day like 09:00") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"{value!r} is not a valid time of day")
    return hour, minute


def next_run(schedule: Schedule, after: datetime) -> datetime | None:
    """First moment strictly after `after` that the schedule fires.

    Returns None when the schedule has no future occurrence, which only happens
    for a one-off whose moment has already passed.
    """
    if schedule.kind == ONCE:
        assert schedule.run_at is not None
        return schedule.run_at if schedule.run_at > after else None

    if schedule.kind == INTERVAL:
        assert schedule.interval_minutes is not None
        return after + timedelta(minutes=schedule.interval_minutes)

    hour, minute = parse_time_of_day(schedule.time_of_day)
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if schedule.kind == DAILY:
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate

    # WEEKLY: walk forward to the next allowed weekday, today included if the
    # time has not yet passed.
    allowed = set(schedule.weekdays)
    for offset in range(8):
        day = candidate + timedelta(days=offset)
        if day.weekday() in allowed and day > after:
            return day
    return None  # unreachable while weekdays is non-empty


def first_run(schedule: Schedule, now: datetime) -> datetime | None:
    """When a newly created job should first print.

    A one-off scheduled for the past is still honoured -- you asked for that
    document, so it prints as soon as the scheduler notices.
    """
    if schedule.kind == ONCE:
        assert schedule.run_at is not None
        return schedule.run_at
    if schedule.kind == INTERVAL:
        assert schedule.interval_minutes is not None
        return now + timedelta(minutes=schedule.interval_minutes)
    return next_run(schedule, now)


def is_missed(schedule: Schedule, due: datetime, now: datetime, grace_minutes: int) -> bool:
    """True when a recurring slot is so far in the past it should be skipped.

    Stops a laptop that was shut for the weekend from spitting out every daily
    sheet it slept through. One-off jobs are never treated as missed.
    """
    if schedule.kind == ONCE:
        return False
    return now - due > timedelta(minutes=grace_minutes)
