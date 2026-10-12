"""Tests for decay_reminder.py: payload content and fail-open exit.

Pins the 2026-10-11 removal of the file-link copy: CLAUDE.md owns that rule,
and a second wording here competed with it.

Run: python hooks/tests/test_decay_reminder.py  (from the repo root or anywhere)
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "decay_reminder.py"


class DecayReminder(unittest.TestCase):
    def test_payload_keeps_report_rules_without_file_link_copy(self) -> None:
        proc = subprocess.run([sys.executable, str(HOOK)], input=b"{}", capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.startswith("<decay-reminder>") and out.rstrip().endswith("</decay-reminder>"))
        self.assertIn("Done / Assumed / Left-Flags", out)
        self.assertIn("never re-litigate", out)
        for gone in ("File links", "SendUserFile", "markdown link", "cwd"):
            self.assertNotIn(gone, out)


if __name__ == "__main__":
    unittest.main()
