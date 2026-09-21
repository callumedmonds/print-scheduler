"""How jobs are turned into lp commands, and the CUPS parsing around it."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from printsched import printing
from printsched.printing import PrintError, build_command

LPSTAT_P = """printer HP_OfficeJet_9720e is idle.  enabled since Mon 21 Sep 2026 09:30:27 BST
printer Brother_HL is idle.  enabled since Mon 21 Sep 2026 09:30:27 BST
"""
LPSTAT_D = "system default destination: HP_OfficeJet_9720e\n"


class BuildCommand(unittest.TestCase):
    def test_bare_minimum(self):
        self.assertEqual(build_command(Path("/tmp/a.pdf")), ["lp", "/tmp/a.pdf"])

    def test_printer_copies_and_title(self):
        argv = build_command(Path("/tmp/a.pdf"), printer="HP", copies=3, title="Invoice")
        self.assertEqual(argv, ["lp", "-d", "HP", "-n", "3", "-t", "Invoice", "/tmp/a.pdf"])

    def test_single_copy_is_left_implicit(self):
        self.assertNotIn("-n", build_command(Path("/tmp/a.pdf"), copies=1))

    def test_each_option_gets_its_own_flag(self):
        argv = build_command(Path("/tmp/a.pdf"), options="sides=two-sided-long-edge media=A4")
        self.assertEqual(argv, ["lp", "-o", "sides=two-sided-long-edge", "-o", "media=A4", "/tmp/a.pdf"])

    def test_quoted_option_values_stay_together(self):
        argv = build_command(Path("/tmp/a.pdf"), options="'page-ranges=1-4, 7'")
        self.assertEqual(argv, ["lp", "-o", "page-ranges=1-4, 7", "/tmp/a.pdf"])

    def test_long_titles_are_trimmed_for_cups(self):
        argv = build_command(Path("/tmp/a.pdf"), title="x" * 400)
        self.assertEqual(len(argv[argv.index("-t") + 1]), 255)

    def test_the_path_is_passed_as_one_argument(self):
        """A filename with spaces must not be split into two arguments."""
        argv = build_command(Path("/tmp/my invoice (final).pdf"))
        self.assertEqual(argv[-1], "/tmp/my invoice (final).pdf")


class SendToPrinter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc = Path(self._tmp.name) / "a.pdf"
        self.doc.write_bytes(b"%PDF-1.4\n")

    def test_returns_the_cups_job_id(self):
        result = mock.Mock(returncode=0, stdout="request id is HP_OfficeJet_9720e-42 (1 file(s))", stderr="")
        with mock.patch.object(printing, "_run", return_value=result):
            self.assertEqual(printing.send_to_printer(self.doc), "HP_OfficeJet_9720e-42")

    def test_accepts_a_job_with_no_parsable_id(self):
        result = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(printing, "_run", return_value=result):
            self.assertEqual(printing.send_to_printer(self.doc), "(accepted)")

    def test_surfaces_the_lp_error(self):
        result = mock.Mock(returncode=1, stdout="", stderr="lp: Error - unknown destination\n")
        with mock.patch.object(printing, "_run", return_value=result):
            with self.assertRaises(PrintError) as caught:
                printing.send_to_printer(self.doc, printer="Nope")
        self.assertIn("unknown destination", str(caught.exception))

    def test_missing_file_is_caught_before_reaching_cups(self):
        with self.assertRaises(PrintError):
            printing.send_to_printer(Path("/tmp/definitely-not-here.pdf"))

    def test_empty_file_is_refused(self):
        empty = Path(self._tmp.name) / "empty.pdf"
        empty.touch()
        with self.assertRaises(PrintError):
            printing.send_to_printer(empty)

    def test_folder_is_refused(self):
        with self.assertRaises(PrintError):
            printing.send_to_printer(Path(self._tmp.name))


class ListPrinters(unittest.TestCase):
    def test_parses_queues_and_marks_the_default_first(self):
        def fake_run(argv, timeout=30):
            stdout = LPSTAT_D if argv[1] == "-d" else LPSTAT_P
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        with mock.patch.object(printing.shutil, "which", return_value="/usr/bin/lpstat"), \
             mock.patch.object(printing, "_run", side_effect=fake_run):
            printers = printing.list_printers()

        self.assertEqual([p.name for p in printers], ["HP_OfficeJet_9720e", "Brother_HL"])
        self.assertTrue(printers[0].is_default)
        self.assertFalse(printers[1].is_default)
        self.assertEqual(printers[0].state, "idle")

    def test_no_cups_tools_is_not_a_crash(self):
        with mock.patch.object(printing.shutil, "which", return_value=None):
            self.assertEqual(printing.list_printers(), [])


if __name__ == "__main__":
    unittest.main()
