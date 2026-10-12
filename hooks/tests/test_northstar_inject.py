"""Tests for northstar_inject.py output: no-star, inactive-star, and active-star paths.

Pins the 2026-10-11 alignment with CLAUDE.md: with no active north star the hook
says "state the assumed milestone and proceed" and never asks the owner to draft
a mission; with an active star it still injects the mission text and the doors.

Run: python hooks/tests/test_northstar_inject.py  (from the repo root or anywhere)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "northstar_inject.py"


def run_hook(cwd: Path, event: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=cwd,
        input=json.dumps({"hook_event_name": event}).encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.decode("utf-8")


class NorthstarInject(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # Deeper than MAX_WALK_UP so the walk never leaves the temp tree.
        self.proj = Path(self._tmp.name).joinpath(*"abcdefgh")
        self.proj.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_star_session_start_proceeds_on_assumed_milestone(self) -> None:
        out = run_hook(self.proj, "SessionStart")
        self.assertIn("<northstar-setup>", out)
        self.assertIn("state the milestone you are assuming", out)
        self.assertIn("grants no authority beyond the request", out)
        self.assertIn("Do not stop to ask for or draft", out)
        self.assertNotIn("ask the user what we are building", out)

    def test_no_star_prompt_is_silent(self) -> None:
        self.assertEqual(run_hook(self.proj, "UserPromptSubmit"), "")

    def test_inactive_star_does_not_ask_to_resume(self) -> None:
        (self.proj / "NORTHSTAR.old.md").write_text("status: paused\nOld mission.\n", encoding="utf-8")
        out = run_hook(self.proj, "SessionStart")
        self.assertIn("<northstar-status>", out)
        self.assertIn("NORTHSTAR.old.md (paused)", out)
        self.assertIn("state the milestone you are assuming", out)
        self.assertNotIn("ask the user", out)
        self.assertIn("never\nyou, by any tool", out)
        self.assertNotIn("Old mission.", out)

    def test_active_star_injects_text_and_doors(self) -> None:
        (self.proj / "NORTHSTAR.md").write_text("Ship the widget.\n", encoding="utf-8")
        out = run_hook(self.proj, "UserPromptSubmit")
        self.assertIn("<northstar>", out)
        self.assertIn("Ship the widget.", out)
        self.assertIn("Needs your call", out)
        self.assertIn("a gate blocks Write/Edit on NORTHSTAR*.md", out)
        # The gate covers Write/Edit only; this rule covers every tool.
        self.assertEqual(out.count("Never edit a north-star file yourself by any tool (Bash included)."), 1)


if __name__ == "__main__":
    unittest.main()
