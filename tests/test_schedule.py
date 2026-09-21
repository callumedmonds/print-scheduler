"""Rules that decide when something prints."""

import unittest
from datetime import datetime, timedelta

from printsched.schedule import (DAILY, INTERVAL, ONCE, WEEKLY, Schedule,
                                 ScheduleError, first_run, is_missed, next_run,
                                 parse_time_of_day)

MON_9AM = datetime(2026, 9, 21, 9, 0)  # a Monday


class ParseTimeOfDay(unittest.TestCase):
    def test_accepts_valid_times(self):
        self.assertEqual(parse_time_of_day("09:30"), (9, 30))
        self.assertEqual(parse_time_of_day("00:00"), (0, 0))
        self.assertEqual(parse_time_of_day("23:59"), (23, 59))

    def test_rejects_nonsense(self):
        for bad in ("", None, "9", "25:00", "09:60", "nine o'clock"):
            with self.subTest(bad=bad), self.assertRaises(ScheduleError):
                parse_time_of_day(bad)


class Validation(unittest.TestCase):
    def test_once_needs_a_moment(self):
        with self.assertRaises(ScheduleError):
            Schedule(kind=ONCE)

    def test_weekly_needs_days(self):
        with self.assertRaises(ScheduleError):
            Schedule(kind=WEEKLY, time_of_day="09:00", weekdays=())

    def test_weekly_rejects_out_of_range_days(self):
        with self.assertRaises(ScheduleError):
            Schedule(kind=WEEKLY, time_of_day="09:00", weekdays=(7,))

    def test_interval_needs_a_positive_gap(self):
        with self.assertRaises(ScheduleError):
            Schedule(kind=INTERVAL, interval_minutes=0)

    def test_unknown_kind(self):
        with self.assertRaises(ScheduleError):
            Schedule(kind="fortnightly", time_of_day="09:00")


class OnceSchedules(unittest.TestCase):
    def test_future_moment_is_returned(self):
        moment = MON_9AM + timedelta(hours=2)
        schedule = Schedule(kind=ONCE, run_at=moment)
        self.assertEqual(next_run(schedule, MON_9AM), moment)

    def test_past_moment_does_not_repeat(self):
        schedule = Schedule(kind=ONCE, run_at=MON_9AM - timedelta(hours=1))
        self.assertIsNone(next_run(schedule, MON_9AM))

    def test_backdated_once_still_prints_when_created(self):
        """You asked for this document; a late start should not lose it."""
        past = MON_9AM - timedelta(days=3)
        schedule = Schedule(kind=ONCE, run_at=past)
        self.assertEqual(first_run(schedule, MON_9AM), past)


class DailySchedules(unittest.TestCase):
    def test_later_today(self):
        schedule = Schedule(kind=DAILY, time_of_day="17:00")
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 21, 17, 0))

    def test_rolls_to_tomorrow_once_passed(self):
        schedule = Schedule(kind=DAILY, time_of_day="08:00")
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 22, 8, 0))

    def test_exact_moment_rolls_forward_so_it_cannot_double_print(self):
        schedule = Schedule(kind=DAILY, time_of_day="09:00")
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 22, 9, 0))


class WeeklySchedules(unittest.TestCase):
    def test_same_day_when_time_still_ahead(self):
        schedule = Schedule(kind=WEEKLY, time_of_day="17:00", weekdays=(0,))
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 21, 17, 0))

    def test_jumps_a_week_when_only_day_has_passed(self):
        schedule = Schedule(kind=WEEKLY, time_of_day="08:00", weekdays=(0,))
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 28, 8, 0))

    def test_picks_the_nearest_selected_day(self):
        schedule = Schedule(kind=WEEKLY, time_of_day="09:00", weekdays=(0, 2, 4))
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 23, 9, 0))

    def test_wraps_over_the_weekend(self):
        friday = datetime(2026, 9, 25, 18, 0)
        schedule = Schedule(kind=WEEKLY, time_of_day="09:00", weekdays=(0, 4))
        self.assertEqual(next_run(schedule, friday), datetime(2026, 9, 28, 9, 0))

    def test_every_day_selected_behaves_daily(self):
        schedule = Schedule(kind=WEEKLY, time_of_day="08:00", weekdays=tuple(range(7)))
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 22, 8, 0))


class IntervalSchedules(unittest.TestCase):
    def test_counts_forward_from_now(self):
        schedule = Schedule(kind=INTERVAL, interval_minutes=90)
        self.assertEqual(next_run(schedule, MON_9AM), datetime(2026, 9, 21, 10, 30))

    def test_first_run_waits_one_interval(self):
        schedule = Schedule(kind=INTERVAL, interval_minutes=30)
        self.assertEqual(first_run(schedule, MON_9AM), datetime(2026, 9, 21, 9, 30))


class MissedSlots(unittest.TestCase):
    def test_recurring_slot_within_grace_still_prints(self):
        schedule = Schedule(kind=DAILY, time_of_day="09:00")
        self.assertFalse(is_missed(schedule, MON_9AM, MON_9AM + timedelta(minutes=30), 60))

    def test_recurring_slot_long_past_is_skipped(self):
        schedule = Schedule(kind=DAILY, time_of_day="09:00")
        self.assertTrue(is_missed(schedule, MON_9AM, MON_9AM + timedelta(days=2), 60))

    def test_one_off_is_never_treated_as_missed(self):
        schedule = Schedule(kind=ONCE, run_at=MON_9AM)
        self.assertFalse(is_missed(schedule, MON_9AM, MON_9AM + timedelta(days=30), 60))


class Descriptions(unittest.TestCase):
    def test_reads_as_plain_english(self):
        cases = [
            (Schedule(kind=DAILY, time_of_day="08:30"), "every day at 08:30"),
            (Schedule(kind=WEEKLY, time_of_day="07:45", weekdays=(0, 4)), "every Mon, Fri at 07:45"),
            (Schedule(kind=INTERVAL, interval_minutes=120), "every 2 hours"),
            (Schedule(kind=INTERVAL, interval_minutes=60), "every 1 hour"),
            (Schedule(kind=INTERVAL, interval_minutes=45), "every 45 minutes"),
        ]
        for schedule, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(schedule.describe(), expected)


if __name__ == "__main__":
    unittest.main()
