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


def run_hook_proc(cwd: Path, event: str) -> "subprocess.CompletedProcess[bytes]":
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=cwd,
        input=json.dumps({"hook_event_name": event}).encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def run_hook(cwd: Path, event: str) -> str:
    return run_hook_proc(cwd, event).stdout.decode("utf-8")


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
        self.assertIn("owner approves at the gate's prompt (never via Bash)", out)
        self.assertNotIn("Old mission.", out)

    def test_active_star_injects_text_and_doors(self) -> None:
        (self.proj / "NORTHSTAR.md").write_text("Ship the widget.\n", encoding="utf-8")
        out = run_hook(self.proj, "UserPromptSubmit")
        self.assertIn("<northstar>", out)
        self.assertIn("Ship the widget.", out)
        self.assertIn("Needs your call", out)
        self.assertIn("the gate turns any\nWrite/Edit on NORTHSTAR*.md into an approval prompt", out)
        # The gate covers Write/Edit only; this rule covers every tool.
        self.assertEqual(out.count("never route around the prompt by any tool (Bash included)."), 1)


class FailOpenIsObservable(unittest.TestCase):
    """2026-10-12 slop audit SA-6(b): fail-open sites stay exit 0 but leave a stderr line."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name).joinpath(*"abcdefgh")
        self.proj.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_active_stars_unreadable_is_reported_on_stderr(self) -> None:
        # Invalid UTF-8: the status scan (errors="replace") still says active, the strict read fails.
        (self.proj / "NORTHSTAR.md").write_bytes(b"Ship it \xff\xfe\n")
        proc = run_hook_proc(self.proj, "UserPromptSubmit")
        self.assertEqual(proc.stdout, b"")
        err = proc.stderr.decode("utf-8")
        self.assertIn("northstar_inject: skipped unreadable active north star(s): NORTHSTAR.md", err)
        self.assertIn("nothing injected", err)

    def test_one_unreadable_star_is_reported_and_the_rest_still_inject(self) -> None:
        (self.proj / "NORTHSTAR.a.md").write_text("Ship the widget.\n", encoding="utf-8")
        (self.proj / "NORTHSTAR.b.md").write_bytes(b"bad \xff\n")
        proc = run_hook_proc(self.proj, "UserPromptSubmit")
        self.assertIn("Ship the widget.", proc.stdout.decode("utf-8"))
        err = proc.stderr.decode("utf-8")
        self.assertIn("NORTHSTAR.b.md (UnicodeDecodeError)", err)
        self.assertNotIn("nothing injected", err)

    def test_top_level_fail_open_is_reported_on_stderr(self) -> None:
        import contextlib
        import importlib.util
        import io
        spec = importlib.util.spec_from_file_location("northstar_inject_under_test", HOOK)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        def boom() -> None:
            raise RuntimeError("synthetic")

        mod.main = boom
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            mod.run_fail_open()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("northstar_inject: failed open (RuntimeError); nothing injected", err.getvalue())
        self.assertNotIn("synthetic", err.getvalue())  # type only, never exception text


if __name__ == "__main__":
    unittest.main()
