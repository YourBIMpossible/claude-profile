"""Sandboxed install tests for bootstrap.py (the POSIX bootstrap).

Runs the real script with HOME pointed at a temp directory, so nothing on the
machine is touched. Needs a committed working tree for hooks/ and vendor/ (C1),
exactly like a real run; pass nothing else.

Run: python3 hooks/tests/test_bootstrap_py.py   (or pytest)
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
BOOTSTRAP = REPO / "bootstrap.py"


@unittest.skipIf(os.name == "nt", "bootstrap.py is the POSIX bootstrap")
class BootstrapPy(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="bootstrap-py-test-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.claude = self.home / ".claude"
        self.hooks_dir = self.claude / "hooks"

    def run_bootstrap(self, *args: str, expect_ok: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home)}
        proc = subprocess.run([sys.executable, str(BOOTSTRAP), "--allow-uncommitted-hooks", *args],
                              capture_output=True, text=True, env=env, cwd=str(self.home))
        if expect_ok:
            self.assertEqual(proc.returncode, 0, f"bootstrap failed:\n{proc.stdout}\n{proc.stderr}")
        else:
            self.assertNotEqual(proc.returncode, 0, f"bootstrap should have refused:\n{proc.stdout}")
        return proc

    def settings(self) -> dict:
        return json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))

    def handlers(self) -> list[tuple[str, dict]]:
        return [(event, h) for event, groups in self.settings()["hooks"].items()
                for g in groups for h in g["hooks"]]

    def test_clean_install(self) -> None:
        self.run_bootstrap()
        self.assertEqual((self.claude / "CLAUDE.md").read_text(encoding="utf-8"),
                         f"@{REPO.as_posix()}/CLAUDE.md")
        self.assertTrue((self.claude / "skills").is_symlink())
        self.assertEqual((self.claude / "skills").resolve(), (REPO / "skills").resolve())
        for src in (REPO / "hooks").iterdir():
            if src.is_file() and (src.suffix in {".py", ".ps1", ".sh"} or src.name == "closure_audit_repos.json"):
                installed = self.hooks_dir / src.name
                self.assertTrue(installed.is_file(), installed)
                if src.name != "session-start-pull.sh":
                    self.assertEqual(installed.read_bytes(), src.read_bytes(), src.name)
        pinned = (self.hooks_dir / "session-start-pull.sh").read_text(encoding="utf-8")
        self.assertIn(f"REPO_ROOT='{REPO}'", pinned)
        self.assertTrue(os.access(self.hooks_dir / "session-start-pull.sh", os.X_OK))
        for unit in (REPO / "vendor").iterdir():
            if unit.is_dir():
                for f in unit.rglob("*"):
                    if f.is_file() and "__pycache__" not in f.parts:
                        self.assertEqual((self.hooks_dir / unit.name / f.relative_to(unit)).read_bytes(), f.read_bytes())

    def test_registrations_are_posix_and_point_at_installed_copies(self) -> None:
        self.run_bootstrap()
        handlers = self.handlers()
        self.assertTrue(handlers)
        for event, h in handlers:
            if h["type"] != "command":
                continue
            self.assertIn(h["command"], {"python3", "bash"}, (event, h))
            for a in h["args"]:
                self.assertTrue(a.startswith(str(self.hooks_dir) + "/"), a)
                self.assertTrue(Path(a).is_file(), a)
                self.assertNotIn(str(REPO), a)
        scripts = [h["args"][-1].rsplit("/", 1)[-1] for _, h in handlers if h["type"] == "command"]
        self.assertIn("session-start-pull.sh", scripts)
        self.assertNotIn("session-start-pull.ps1", scripts)
        self.assertNotIn("Show-AuditAlerts.ps1", scripts)
        self.assertIn("protect_paths_gate.py", scripts)
        self.assertIn("evidence-capture/capture_start.py", [h["args"][-1].split("/hooks/", 1)[1] for _, h in handlers if h["type"] == "command"])

    def test_rerun_is_a_no_op(self) -> None:
        self.run_bootstrap()
        before = (self.claude / "settings.json").read_bytes()
        proc = self.run_bootstrap()
        self.assertIn("settings.json already correct", proc.stdout)
        self.assertEqual((self.claude / "settings.json").read_bytes(), before)
        self.assertFalse(list(self.claude.glob("*.pre-profile-backup-*")))

    def test_existing_settings_are_merged_not_replaced(self) -> None:
        self.claude.mkdir()
        mine = {"modelSettings": {"claude-fable-5-1": {"effortLevel": "low"}}, "theme": "light",
                "permissions": {"allow": ["WebSearch"], "additionalDirectories": ["/srv/x"]},
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "python3", "args": ["/opt/mine/hook.py"], "timeout": 5}]}]}}
        (self.claude / "settings.json").write_text(json.dumps(mine), encoding="utf-8")
        self.run_bootstrap()
        s = self.settings()
        self.assertEqual(s["modelSettings"], mine["modelSettings"])
        self.assertEqual(s["theme"], "light")
        self.assertEqual(s["permissions"]["allow"], ["WebSearch"])
        self.assertIn("/srv/x", s["permissions"]["additionalDirectories"])
        kept = [h for e, h in self.handlers() if h.get("args") == ["/opt/mine/hook.py"]]
        self.assertEqual(len(kept), 1)
        self.assertTrue(list(self.claude.glob("settings.json.pre-profile-backup-*")))

    def test_empty_settings_refused_before_any_write(self) -> None:
        self.claude.mkdir()
        (self.claude / "settings.json").write_text("", encoding="utf-8")
        self.run_bootstrap(expect_ok=False)
        self.assertFalse((self.claude / "CLAUDE.md").exists())
        self.assertFalse(self.hooks_dir.exists())

    def test_real_skills_dir_is_moved_aside(self) -> None:
        (self.claude / "skills" / "local-only").mkdir(parents=True)
        (self.claude / "skills" / "local-only" / "SKILL.md").write_text("x", encoding="utf-8")
        self.run_bootstrap()
        backups = list(self.claude.glob("skills.pre-profile-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "local-only" / "SKILL.md").is_file(), "backup is kept intact")
        self.assertTrue((self.claude / "skills").is_symlink())
        self.assertFalse((REPO / "skills" / "local-only").exists(), "skills are replaced, not merged")

    def test_graphify_auto_refresh_is_pinned_off(self) -> None:
        self.run_bootstrap()
        self.assertEqual(self.settings()["env"]["GRAPHIFY_NO_AUTO_REFRESH"], "1")

if __name__ == "__main__":
    unittest.main()
