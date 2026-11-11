"""SA02-9: session_closure_audit.py must not turn a config error into a silent, empty run.

Drives the real `__main__` stdin path in a subprocess, against a temp copy of the hook
(the hook reads `closure_audit_repos.json` from its own folder, so the copy gets its own).

Run: python hooks/tests/test_closure_audit_main_config.py  (or: python -m pytest hooks/tests -q)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "session_closure_audit.py"
SHIPPED_CONFIG = REPO / "hooks" / "closure_audit_repos.json"
MACHINE_CONFIG = REPO / "tools" / "machine_config.py"
CONFIG_NAME = "closure_audit_repos.json"


def git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, timeout=60, env=env, check=True)


class ClosureAuditMainConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "hooks").mkdir()
        (root / "tools").mkdir()
        self.hook = root / "hooks" / HOOK.name
        shutil.copy(HOOK, self.hook)
        shutil.copy(MACHINE_CONFIG, root / "tools" / MACHINE_CONFIG.name)
        self.config = root / "hooks" / CONFIG_NAME
        self.repo = root / "good-repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "main.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def write_config(self, repos: object) -> None:
        cfg = json.loads(SHIPPED_CONFIG.read_text(encoding="utf-8-sig"))
        cfg["repos"] = repos
        self.config.write_text(json.dumps(cfg), encoding="utf-8")

    def good(self) -> dict:
        return {"name": "good-repo", "path": str(self.repo)}

    def run_hook(self, event: str = "SessionStart") -> tuple[int, str]:
        proc = subprocess.run([sys.executable, str(self.hook)], input=json.dumps({"hook_event_name": event}).encode(),
                              capture_output=True, timeout=60)
        return proc.returncode, proc.stdout.decode("utf-8")

    def assert_attention(self, out: str, *causes: str) -> None:
        self.assertIn("<closure-audit>", out)
        self.assertIn("ATTENTION REQUIRED", out)
        self.assertIn("not confirmed clean", out)
        for cause in causes:
            self.assertIn(cause, out)

    # --- controls: these already worked ----------------------------------------------
    def test_one_valid_repo_gives_a_verdict(self) -> None:
        self.write_config([self.good()])
        code, out = self.run_hook()
        self.assertEqual(code, 0)
        self.assertIn("## good-repo", out)
        self.assertIn("Verdict: 0 ATTENTION REQUIRED", out)

    def test_other_events_stay_silent(self) -> None:
        self.assertEqual(self.run_hook("Stop"), (0, ""))  # no config written: must not matter

    # --- SA02-9 ------------------------------------------------------------------------
    def test_missing_config_is_reported(self) -> None:
        code, out = self.run_hook()
        self.assertEqual(code, 0)
        self.assert_attention(out, CONFIG_NAME, "FileNotFoundError")

    def test_malformed_config_is_reported(self) -> None:
        self.config.write_text('{"repos": [', encoding="utf-8")
        code, out = self.run_hook()
        self.assertEqual(code, 0)
        self.assert_attention(out, CONFIG_NAME, "JSONDecodeError")

    def test_config_of_the_wrong_shape_is_reported(self) -> None:
        for label, text in (("top level is a list", "[]"), ("repos is a string", '{"repos": "x"}')):
            with self.subTest(label):
                self.config.write_text(text, encoding="utf-8")
                code, out = self.run_hook()
                self.assertEqual(code, 0)
                self.assert_attention(out, CONFIG_NAME)

    def test_entry_without_path_is_named_and_the_valid_entry_is_still_audited(self) -> None:
        self.write_config([{"name": "no-path-repo"}, self.good()])
        code, out = self.run_hook()
        self.assertEqual(code, 0)
        self.assert_attention(out, CONFIG_NAME, "no-path-repo", '"path"')
        self.assertIn("## good-repo", out)
        self.assertIn("Verdict: 1 ATTENTION REQUIRED", out)

    def test_unreadable_entries_are_named_by_position(self) -> None:
        self.write_config([self.good(), "just-a-string", {"path": str(self.repo)}])
        code, out = self.run_hook()
        self.assertEqual(code, 0)
        self.assert_attention(out, CONFIG_NAME, "entry 2", "entry 3", '"name"')
        self.assertIn("## good-repo", out)

    def test_unexpected_error_is_reported_not_swallowed(self) -> None:
        self.write_config([self.good()])
        src = self.hook.read_text(encoding="utf-8")
        marker = "def build_report(cfg: dict) -> str:\n"
        self.assertIn(marker, src)
        self.hook.write_text(src.replace(marker, marker + '    raise RuntimeError("boom")\n'), encoding="utf-8")
        code, out = self.run_hook()
        self.assertEqual(code, 0)
        self.assert_attention(out, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
