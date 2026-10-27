"""Regression tests for slop-audit 2026-10-26 CP-11: session_start.py must tell the session when it
could not pull, instead of writing the failure to last-run.log only.

The end-to-end cases (a real failed pull) run the hook in a
sandbox in test_bootstrap_install.py. This file covers the paths a real git cannot be made to hit
on demand - a timeout and a missing git - by importing the hook and replacing its `git` and `log`
helpers. Nothing here runs git or writes outside memory.

Run: python hooks/tests/test_session_start_warnings.py   (or pytest hooks/tests)
"""
from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_start.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("session_start_under_test", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def done(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["git"], rc, stdout, stderr)


class HookWithFakeGit(unittest.TestCase):
    def setUp(self) -> None:
        self.ss = load_hook()
        self.logged: list[str] = []
        self.ss.log = self.logged.append  # never touch ~/.claude/hooks/last-run.log

    def fake_git(self, handler) -> None:
        self.ss.git = lambda *args, timeout=None: handler(args)


class PullWarnings(HookWithFakeGit):
    """CP-11: pull() reported failures to last-run.log only."""

    def test_timeout_returns_a_session_warning(self) -> None:
        def handler(args):
            raise subprocess.TimeoutExpired(["git", *args], self.ss.GIT_TIMEOUT_S)
        self.fake_git(handler)
        warning = self.ss.pull()
        self.assertIn("pull failed (exit 124)", self.logged[-1])
        self.assertIsNotNone(warning)
        self.assertIn("timed out", warning)
        self.assertIn("last-run.log", warning)

    def test_missing_git_returns_a_session_warning(self) -> None:
        def handler(args):
            raise FileNotFoundError("git")
        self.fake_git(handler)
        warning = self.ss.pull()
        self.assertIn("git missing", self.logged[-1])
        self.assertIsNotNone(warning)
        self.assertIn("git was not found", warning)

    def test_nonzero_exit_returns_a_session_warning(self) -> None:
        self.fake_git(lambda args: done(1, stderr="fatal: no such remote"))
        warning = self.ss.pull()
        self.assertIsNotNone(warning)
        self.assertIn("the profile pull failed (exit 1)", warning)

    def test_success_returns_no_warning(self) -> None:
        self.fake_git(lambda args: done(0, stdout="Already up to date."))
        self.assertIsNone(self.ss.pull())
        self.assertIn("pull ok", self.logged[-1])


if __name__ == "__main__":
    unittest.main()
