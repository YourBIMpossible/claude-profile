"""Regression tests for slop-audit 2026-10-26 CP-11 and CP-26: session_start.py must tell the session
when it could not pull or could not check the profile clone, instead of logging only or reading a
failure as "clean".

The end-to-end cases (a real failed pull, origin/HEAD unset, a corrupt index) run the hook in a
sandbox in test_bootstrap_install.py. This file covers the paths a real git cannot be made to hit
on demand - a timeout and a missing git - by importing the hook and replacing its `git` and `log`
helpers. Nothing here runs git. The N-1 timeout bound runs a fake git (a Python script) in a temp dir.

Run: python hooks/tests/test_session_start_warnings.py   (or pytest hooks/tests)
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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
        self.assertIn("pull timed out (exit 124)", self.logged[-1])
        self.assertNotIn("failed", self.logged[-1])
        self.assertIsNotNone(warning)
        self.assertIn("timed out", warning)
        self.assertIn("last-run.log", warning)
        # A timed-out pull is not a failed one: killing `git pull` leaves its child processes running, and the
        # pull can still finish after the hook returns (PR #62 triage probe), so the session must not hear "failed".
        self.assertNotIn("failed", warning)
        self.assertIn("may still complete", warning)

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


class CloneWarnings(HookWithFakeGit):
    """CP-26: clone_warnings() swallowed exceptions and never read the `git status` exit code."""

    @staticmethod
    def answers(branch: str = "main", default: str = "origin/main", status: subprocess.CompletedProcess | None = None):
        def handler(args):
            if args[:1] == ("symbolic-ref",):
                return done(0, branch + "\n") if args[-1] == "HEAD" else (done(0, default + "\n") if default else done(1))
            if args[:1] == ("status",):
                return status if status is not None else done(0)
            raise AssertionError(f"unexpected git call {args}")
        return handler

    def test_clean_default_branch_has_no_warning(self) -> None:
        self.fake_git(self.answers())
        self.assertEqual(self.ss.clone_warnings(), [])

    def test_unset_origin_head_is_reported(self) -> None:
        self.fake_git(self.answers(branch="feature", default=""))
        warnings = self.ss.clone_warnings()
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("origin/HEAD is not set", warnings[0])
        self.assertIn("remote set-head origin -a", warnings[0])

    def test_nonzero_status_is_not_clean(self) -> None:
        self.fake_git(self.answers(status=done(128, stderr="fatal: index file corrupt")))
        warnings = self.ss.clone_warnings()
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("could not check the profile clone", warnings[0])
        self.assertIn("git status exit 128", warnings[0])

    def test_a_timeout_is_reported_not_swallowed(self) -> None:
        def handler(args):
            raise subprocess.TimeoutExpired(["git", *args], 5)
        self.fake_git(handler)
        warnings = self.ss.clone_warnings()
        # Both checks must report on their own: reverting either `except` to `pass` drops one line.
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(all("could not check the profile clone" in w and "TimeoutExpired" in w for w in warnings),
                        warnings)
        self.assertEqual(sum("for its default branch" in w for w in warnings), 1, warnings)
        self.assertEqual(sum("for uncommitted hook/tool changes" in w for w in warnings), 1, warnings)

    def test_branch_warning_still_fires(self) -> None:
        self.fake_git(self.answers(branch="feature"))
        warnings = self.ss.clone_warnings()
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("is on 'feature', not 'main'", warnings[0])


# Stands in for `git pull`: it starts a process that inherits its stdout/stderr (as git's fetch and
# remote helpers do), then hangs. Killing it on the timeout leaves that child holding the output open.
FAKE_GIT = """import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep({hold})"])
with open({pidfile!r}, "w") as f:
    f.write(str(child.pid))
print("fetching", flush=True)
time.sleep(120)
"""


class GitTimeoutIsBounded(unittest.TestCase):
    """N-1: the timeout must bound the whole call. subprocess.run(capture_output=True) kills git on the
    timeout, then keeps reading its pipes until git's surviving child closes them: a 3 s timeout returned
    after 20.5 s, near the hook's 25 s SessionStart limit."""

    TIMEOUT_S = 2.0
    HOLD_S = 12
    SLACK_S = 3.0

    def setUp(self) -> None:
        self.ss = load_hook()
        self.tmp = Path(tempfile.mkdtemp(prefix="ss-n1-"))
        self.pidfile = self.tmp / "child.pid"
        self.fake = self.tmp / "fake_git.py"
        self.fake.write_text(FAKE_GIT.format(hold=self.HOLD_S, pidfile=str(self.pidfile)), encoding="utf-8")

    def tearDown(self) -> None:
        try:
            os.kill(int(self.pidfile.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_at_the_deadline_while_a_child_holds_the_output(self) -> None:
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.ss.run_bounded([sys.executable, str(self.fake)], timeout=self.TIMEOUT_S)
        elapsed = time.monotonic() - start
        self.assertTrue(self.pidfile.exists(), "the fake git never started its child; the probe proved nothing")
        self.assertLess(elapsed, self.TIMEOUT_S + self.SLACK_S,
                        f"a {self.TIMEOUT_S}s timeout returned after {elapsed:.1f}s")

    def test_output_and_exit_code_are_returned(self) -> None:
        proc = self.ss.run_bounded(
            [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], 30)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (3, "out\n", "err\n"))


if __name__ == "__main__":
    unittest.main()
