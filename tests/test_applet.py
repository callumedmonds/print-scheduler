"""The tray applet's view of the world.

Only the state layer is covered: it is the part with logic. Building the GTK
menu needs a display, so it is left to manual checking.
"""

import json
import unittest
import urllib.error
from io import BytesIO
from unittest import mock

from printsched.applet import AppletState

STATE = {
    "jobs": [
        {"id": 1, "name": "Job sheet", "enabled": True, "next_run": "2026-09-22T08:30:00", "copies": 2},
        {"id": 2, "name": "Rota", "enabled": True, "next_run": "2026-09-21T17:00:00", "copies": 1},
        {"id": 3, "name": "Paused one", "enabled": False, "next_run": "2026-09-21T09:00:00", "copies": 1},
        {"id": 4, "name": "Finished one", "enabled": True, "next_run": None, "copies": 1},
    ],
    "queue": ["HP-1 callum 1024 Mon 21 Sep 2026"],
}


def fake_response(payload):
    return mock.MagicMock(
        __enter__=lambda self: BytesIO(json.dumps(payload).encode()),
        __exit__=lambda *a: None,
    )


class Upcoming(unittest.TestCase):
    def setUp(self):
        self.state = AppletState()
        with mock.patch("urllib.request.urlopen", return_value=fake_response(STATE)):
            self.state.refresh()

    def test_reaching_the_server_is_recorded(self):
        self.assertTrue(self.state.reachable)
        self.assertEqual(self.state.error, "")

    def test_soonest_job_comes_first(self):
        self.assertEqual([j["name"] for j in self.state.upcoming()], ["Rota", "Job sheet"])

    def test_paused_and_finished_jobs_are_left_out(self):
        names = [j["name"] for j in self.state.upcoming()]
        self.assertNotIn("Paused one", names)
        self.assertNotIn("Finished one", names)

    def test_the_list_is_capped(self):
        self.assertEqual(len(self.state.upcoming(limit=1)), 1)

    def test_tooltip_counts_active_jobs_and_names_the_next(self):
        self.assertEqual(self.state.summary(), "Print Scheduler - 3 jobs, next 2026-09-21 17:00")


class Unreachable(unittest.TestCase):
    def test_a_down_server_is_reported_not_raised(self):
        state = AppletState()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            state.refresh()
        self.assertFalse(state.reachable)
        self.assertEqual(state.upcoming(), [])
        self.assertEqual(state.summary(), "Print Scheduler - not running")

    def test_malformed_json_is_treated_as_unreachable(self):
        state = AppletState()
        broken = mock.MagicMock(__enter__=lambda self: BytesIO(b"not json"), __exit__=lambda *a: None)
        with mock.patch("urllib.request.urlopen", return_value=broken):
            state.refresh()
        self.assertFalse(state.reachable)


class EmptySchedule(unittest.TestCase):
    def test_tooltip_when_nothing_is_scheduled(self):
        state = AppletState()
        with mock.patch("urllib.request.urlopen", return_value=fake_response({"jobs": [], "queue": []})):
            state.refresh()
        self.assertEqual(state.summary(), "Print Scheduler - nothing scheduled")


if __name__ == "__main__":
    unittest.main()
