"""Job persistence, run history and the print/reschedule cycle."""

import logging
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from printsched import config, scheduler, store
from printsched.printing import PrintError
from printsched.schedule import DAILY, INTERVAL, ONCE, WEEKLY, Schedule

MON_9AM = datetime(2026, 9, 21, 9, 0)


class StoreTestCase(unittest.TestCase):
    """Points the app's data directory at a throwaway folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        patches = {
            "DATA_DIR": root,
            "DB_PATH": root / "scheduler.db",
            "SPOOL_DIR": root / "spool",
            "LOG_PATH": root / "printsched.log",
        }
        for name, value in patches.items():
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        config.ensure_dirs()
        self.conn = store.connect()
        self.addCleanup(self.conn.close)
        self.addCleanup(self._tmp.cleanup)

        self.doc = root / "invoice.pdf"
        self.doc.write_bytes(b"%PDF-1.4 fake\n")

    def add(self, schedule=None, **kwargs):
        schedule = schedule or Schedule(kind=DAILY, time_of_day="09:00")
        params = dict(name="Invoice", source_path=self.doc, schedule=schedule, now=MON_9AM)
        params.update(kwargs)
        return store.create_job(self.conn, **params)


class CreatingJobs(StoreTestCase):
    def test_round_trips_every_field(self):
        job_id = self.add(
            schedule=Schedule(kind=WEEKLY, time_of_day="07:45", weekdays=(0, 4)),
            printer="HP_OfficeJet_9720e", copies=3, options="sides=two-sided-long-edge",
        )
        row = store.get_job(self.conn, job_id)
        self.assertEqual(row["name"], "Invoice")
        self.assertEqual(row["printer"], "HP_OfficeJet_9720e")
        self.assertEqual(row["copies"], 3)
        self.assertEqual(row["options"], "sides=two-sided-long-edge")
        self.assertEqual(row["weekdays"], "0,4")
        self.assertTrue(row["enabled"])

    def test_schedule_survives_the_round_trip(self):
        job_id = self.add(schedule=Schedule(kind=WEEKLY, time_of_day="07:45", weekdays=(4, 0)))
        restored = store.schedule_of(store.get_job(self.conn, job_id))
        self.assertEqual(restored.kind, WEEKLY)
        self.assertEqual(restored.weekdays, (0, 4))
        self.assertEqual(restored.describe(), "every Mon, Fri at 07:45")

    def test_next_run_is_computed_on_creation(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="17:00"))
        row = store.get_job(self.conn, job_id)
        self.assertEqual(store.parse(row["next_run"]), datetime(2026, 9, 21, 17, 0))

    def test_copies_below_one_are_clamped(self):
        row = store.get_job(self.conn, self.add(copies=0))
        self.assertEqual(row["copies"], 1)

    def test_rejects_unknown_source_mode(self):
        with self.assertRaises(ValueError):
            self.add(source_mode="magic")


class Spooling(StoreTestCase):
    def test_snapshot_copy_is_independent_of_the_original(self):
        copy = store.spool(self.doc)
        self.assertTrue(copy.exists())
        self.assertNotEqual(copy, self.doc)
        self.doc.write_bytes(b"replaced")
        self.assertEqual(copy.read_bytes(), b"%PDF-1.4 fake\n")

    def test_upload_keeps_the_file_extension(self):
        saved = store.save_upload("rota.pdf", b"data")
        self.assertEqual(saved.suffix, ".pdf")
        self.assertEqual(saved.read_bytes(), b"data")

    def test_deleting_removes_the_spooled_copy_only(self):
        copy = store.spool(self.doc)
        job_id = self.add(source_path=copy, source_mode=store.SNAPSHOT)
        self.assertTrue(store.delete_job(self.conn, job_id))
        self.assertFalse(copy.exists())
        self.assertTrue(self.doc.exists())

    def test_deleting_a_live_job_leaves_your_file_alone(self):
        job_id = self.add(source_path=self.doc, source_mode=store.LIVE)
        store.delete_job(self.conn, job_id)
        self.assertTrue(self.doc.exists())

    def test_deleting_an_unknown_job_reports_false(self):
        self.assertFalse(store.delete_job(self.conn, 999))


class DueJobs(StoreTestCase):
    def test_only_returns_jobs_whose_time_has_come(self):
        soon = self.add(schedule=Schedule(kind=DAILY, time_of_day="10:00"))
        later = self.add(schedule=Schedule(kind=DAILY, time_of_day="23:00"))
        due = store.due_jobs(self.conn, datetime(2026, 9, 21, 10, 30))
        self.assertEqual([row["id"] for row in due], [soon])

    def test_paused_jobs_are_never_due(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="10:00"))
        store.set_enabled(self.conn, job_id, False)
        self.assertEqual(store.due_jobs(self.conn, datetime(2026, 9, 21, 10, 30)), [])

    def test_resuming_recomputes_the_next_slot(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="10:00"))
        store.set_enabled(self.conn, job_id, False)
        store.set_enabled(self.conn, job_id, True, now=datetime(2026, 9, 22, 12, 0))
        row = store.get_job(self.conn, job_id)
        self.assertEqual(store.parse(row["next_run"]), datetime(2026, 9, 23, 10, 0))

    def test_resuming_an_unknown_job_raises(self):
        with self.assertRaises(KeyError):
            store.set_enabled(self.conn, 999, True)


class RunningJobs(StoreTestCase):
    def test_successful_print_records_history_and_moves_on(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="09:00"))
        row = store.get_job(self.conn, job_id)
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-42") as send:
            status, message = scheduler.run_job(self.conn, row, now=datetime(2026, 9, 22, 9, 0))

        self.assertEqual(status, "ok")
        self.assertIn("HP-42", message)
        send.assert_called_once()

        row = store.get_job(self.conn, job_id)
        self.assertEqual(row["last_status"], "ok")
        self.assertEqual(store.parse(row["next_run"]), datetime(2026, 9, 23, 9, 0))
        runs = store.recent_runs(self.conn)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["cups_job"], "HP-42")

    def test_printer_options_reach_cups(self):
        job_id = self.add(printer="HP_OfficeJet_9720e", copies=2, options="sides=two-sided-long-edge")
        row = store.get_job(self.conn, job_id)
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-1") as send:
            scheduler.run_job(self.conn, row, now=datetime(2026, 9, 22, 9, 0))
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["printer"], "HP_OfficeJet_9720e")
        self.assertEqual(kwargs["copies"], 2)
        self.assertEqual(kwargs["options"], "sides=two-sided-long-edge")

    def test_a_one_off_disables_itself_after_printing(self):
        job_id = self.add(schedule=Schedule(kind=ONCE, run_at=datetime(2026, 9, 22, 9, 0)))
        row = store.get_job(self.conn, job_id)
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-7"):
            scheduler.run_job(self.conn, row, now=datetime(2026, 9, 22, 9, 0))
        row = store.get_job(self.conn, job_id)
        self.assertIsNone(row["next_run"])
        self.assertFalse(row["enabled"])

    def test_failure_is_recorded_but_the_schedule_continues(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="09:00"))
        row = store.get_job(self.conn, job_id)
        with mock.patch.object(scheduler, "send_to_printer", side_effect=PrintError("printer offline")):
            status, message = scheduler.run_job(self.conn, row, now=datetime(2026, 9, 22, 9, 0))

        self.assertEqual(status, "error")
        self.assertEqual(message, "printer offline")
        row = store.get_job(self.conn, job_id)
        self.assertEqual(row["last_status"], "error")
        self.assertEqual(store.parse(row["next_run"]), datetime(2026, 9, 23, 9, 0),
                         "a failed print must not cancel tomorrow's")

    def test_slot_slept_through_is_skipped_not_printed(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="09:00"))
        row = store.get_job(self.conn, job_id)
        woke_up = datetime(2026, 9, 24, 14, 0)  # three days later
        with mock.patch.object(scheduler, "send_to_printer") as send:
            status, message = scheduler.run_job(self.conn, row, now=woke_up)

        self.assertEqual(status, "skipped")
        send.assert_not_called()
        row = store.get_job(self.conn, job_id)
        self.assertEqual(store.parse(row["next_run"]), datetime(2026, 9, 25, 9, 0))

    def test_print_now_does_not_consume_the_scheduled_slot(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="09:00"))
        before = store.get_job(self.conn, job_id)["next_run"]
        row = store.get_job(self.conn, job_id)
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-9"):
            status, _ = scheduler.run_job(self.conn, row, now=datetime(2026, 9, 21, 15, 0), force=True)
        self.assertEqual(status, "ok")
        self.assertEqual(store.get_job(self.conn, job_id)["next_run"], before)

    def test_print_now_overrides_the_missed_slot_rule(self):
        job_id = self.add(schedule=Schedule(kind=DAILY, time_of_day="09:00"))
        row = store.get_job(self.conn, job_id)
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-3") as send:
            status, _ = scheduler.run_job(self.conn, row, now=datetime(2026, 9, 30, 9, 0), force=True)
        self.assertEqual(status, "ok")
        send.assert_called_once()


class Ticking(StoreTestCase):
    def test_handles_every_due_job(self):
        self.add(schedule=Schedule(kind=DAILY, time_of_day="08:00"))
        self.add(schedule=Schedule(kind=DAILY, time_of_day="08:30"))
        self.add(schedule=Schedule(kind=ONCE, run_at=datetime(2026, 9, 30, 10, 0)))
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-1"):
            handled = scheduler.tick(self.conn, now=datetime(2026, 9, 22, 8, 45))
        self.assertEqual(handled, 2, "the job due next week must be left alone")

    def test_one_broken_job_does_not_stop_the_rest(self):
        self.add(name="broken", schedule=Schedule(kind=DAILY, time_of_day="08:00"))
        good = self.add(name="good", schedule=Schedule(kind=DAILY, time_of_day="08:10"))
        calls = []

        def flaky(path, **kwargs):
            calls.append(kwargs.get("title"))
            if kwargs.get("title") == "broken":
                raise RuntimeError("unexpected explosion")
            return "HP-2"

        with mock.patch.object(scheduler, "send_to_printer", side_effect=flaky):
            scheduler.tick(self.conn, now=datetime(2026, 9, 22, 8, 45))

        self.assertIn("good", calls)
        self.assertEqual(store.get_job(self.conn, good)["last_status"], "ok")

    def test_nothing_due_is_a_no_op(self):
        self.add(schedule=Schedule(kind=DAILY, time_of_day="23:00"))  # next_run: Mon 23:00
        with mock.patch.object(scheduler, "send_to_printer") as send:
            self.assertEqual(scheduler.tick(self.conn, now=datetime(2026, 9, 21, 12, 0)), 0)
        send.assert_not_called()

    def test_an_overdue_job_is_still_picked_up(self):
        """A slot that passed while the app was closed must not be ignored."""
        self.add(schedule=Schedule(kind=DAILY, time_of_day="23:00"))
        with mock.patch.object(scheduler, "send_to_printer", return_value="HP-1"):
            self.assertEqual(scheduler.tick(self.conn, now=datetime(2026, 9, 21, 23, 30)), 1)


class History(StoreTestCase):
    def test_history_is_newest_first_and_can_be_filtered(self):
        first = self.add(name="A")
        second = self.add(name="B")
        store.update_after_run(self.conn, first, ran_at=MON_9AM, status="ok",
                               message="one", cups_job="1", next_due=MON_9AM)
        store.update_after_run(self.conn, second, ran_at=MON_9AM + timedelta(hours=1),
                               status="ok", message="two", cups_job="2", next_due=MON_9AM)
        self.assertEqual([r["job_name"] for r in store.recent_runs(self.conn)], ["B", "A"])
        self.assertEqual(len(store.recent_runs(self.conn, job_id=first)), 1)


if __name__ == "__main__":
    unittest.main()
