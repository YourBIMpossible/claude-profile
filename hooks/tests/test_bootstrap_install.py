"""Clean-install and reinstall tests for bootstrap.ps1's hook installation.

Runs the real bootstrap under Windows PowerShell 5.1 (its declared floor) with
USERPROFILE and APPDATA pointed at a throwaway sandbox, so nothing on the live
machine is read or written. Guards the regression where settings.template.json
registered only session-start-pull, so any reinstall silently dropped every
other hook -- including the fail-closed protect_paths_gate control.

Run: python hooks/tests/test_bootstrap_install.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK_SOURCES = sorted(
    p for p in (REPO / "hooks").iterdir()
    if p.is_file() and (p.suffix in {".py", ".ps1"} or p.name == "closure_audit_repos.json")
)
EXECUTABLE_HOOKS = {p.name for p in HOOK_SOURCES if p.suffix in {".py", ".ps1"}}
# session-start-pull runs `git pull` in the repo; it is parse-checked, never executed here.
NOT_EXECUTED = {"session-start-pull.ps1"}


def remove_sandbox(root: Path) -> None:
    # Bootstrap junctions <home>/.claude/skills into the repo. Unlink the junction
    # itself before rmtree so cleanup can never recurse into the repo's skills.
    skills = root / "home" / ".claude" / "skills"
    if os.path.isjunction(skills) or skills.is_symlink():
        os.rmdir(skills)
    shutil.rmtree(root, ignore_errors=True)


def handlers(settings: dict):
    for event, groups in settings["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, group.get("matcher"), handler


class BootstrapSandbox(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bootstrap-test-"))
        self.home = self.root / "home"
        self.appdata = self.root / "appdata"
        self.home.mkdir()
        self.appdata.mkdir()
        self.claude = self.home / ".claude"
        self.hooks_dir = self.claude / "hooks"
        self.addCleanup(remove_sandbox, self.root)

    def run_bootstrap(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "USERPROFILE": str(self.home), "APPDATA": str(self.appdata)}
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / "bootstrap.ps1")],
            env=env, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(proc.returncode, 0, f"bootstrap failed:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def settings(self) -> dict:
        return json.loads((self.claude / "settings.json").read_text(encoding="utf-8-sig"))

    def hooks_prefix(self) -> str:
        return self.hooks_dir.as_posix().lower() + "/"


class CleanInstall(BootstrapSandbox):
    def setUp(self) -> None:
        super().setUp()
        self.run_bootstrap()

    def test_every_hook_source_is_installed_as_a_pinned_copy(self) -> None:
        for source in HOOK_SOURCES:
            installed = self.hooks_dir / source.name
            self.assertTrue(installed.is_file(), f"{source.name} not installed")
            if source.name == "session-start-pull.ps1":
                text = installed.read_text(encoding="utf-8")
                self.assertNotIn("Split-Path -Parent $PSScriptRoot", text)
                self.assertIn(f"$repoRoot = '{REPO}'", text)
            else:
                self.assertEqual(installed.read_bytes(), source.read_bytes(), source.name)

    def test_every_registration_points_at_an_installed_copy_never_the_repo(self) -> None:
        prefix = self.hooks_prefix()
        for event, _, handler in handlers(self.settings()):
            if handler["type"] != "command":
                continue
            args = " ".join([handler["command"], *handler.get("args", [])]).replace("\\", "/").lower()
            self.assertIn(prefix, args, f"{event} handler not pinned: {args}")
            self.assertNotIn(REPO.as_posix().lower() + "/", args)
            target = args.split(prefix, 1)[1].split()[0]
            self.assertTrue((self.hooks_dir / target).is_file(), f"{event} registers missing {target}")

    def test_every_executable_hook_is_registered(self) -> None:
        text = json.dumps(self.settings()["hooks"])
        for name in EXECUTABLE_HOOKS:
            self.assertIn(f"/{name}", text, f"{name} ships in hooks/ but the template never registers it")

    def test_control_and_graph_hooks_keep_their_events(self) -> None:
        seen = {(event, matcher, Path(h["args"][-1]).name) for event, matcher, h in handlers(self.settings())
                if h["type"] == "command"}
        self.assertIn(("PreToolUse", "Write|Edit", "protect_paths_gate.py"), seen)
        self.assertIn(("SessionStart", "startup|resume|clear|compact", "graphify_hint.py"), seen)
        self.assertIn(("SubagentStart", None, "graphify_hint.py"), seen)
        prompts = [h for _, m, h in handlers(self.settings()) if h["type"] == "prompt"]
        self.assertEqual(len(prompts), 1, "skill-detection prompt hook missing or duplicated")

    def test_registered_hooks_execute_from_the_install(self) -> None:
        work = self.root / "work"
        work.mkdir()
        env = {**os.environ, "USERPROFILE": str(self.home),
               "EVIDENCE_EVACUATE_LOG": str(self.root / "evacuate.log")}
        payloads = {
            "SessionStart": {"hook_event_name": "SessionStart", "source": "startup"},
            "SessionEnd": {"hook_event_name": "SessionEnd", "reason": "other"},
            "UserPromptSubmit": {"hook_event_name": "UserPromptSubmit", "prompt": "hello"},
            "PostToolUse": {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                            "tool_input": {"command": "echo hi"}, "tool_response": {"stdout": "hi"}},
            "PreToolUse": {"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {"file_path": str(work / "notes.txt"), "content": "x"}},
            "Stop": {"hook_event_name": "Stop"},
            "SubagentStart": {"hook_event_name": "SubagentStart", "agent_type": "general-purpose"},
        }
        ran = set()
        for event, _, handler in handlers(self.settings()):
            if handler["type"] != "command":
                continue
            name = Path(handler["args"][-1]).name
            if name in NOT_EXECUTED:
                continue
            payload = {**payloads[event], "cwd": str(work), "session_id": "bootstrap-test"}
            proc = subprocess.run([handler["command"], *handler["args"]], input=json.dumps(payload),
                                  env=env, cwd=work, capture_output=True, text=True,
                                  timeout=max(handler.get("timeout", 30), 60))
            self.assertEqual(proc.returncode, 0, f"{event} {name} exit {proc.returncode}: {proc.stderr}")
            ran.add(name)
        self.assertEqual(ran, EXECUTABLE_HOOKS - NOT_EXECUTED)

    def test_installed_gate_still_fails_closed(self) -> None:
        gate = self.hooks_dir / "protect_paths_gate.py"
        self.assertTrue(gate.is_file(), "gate not installed")
        locked = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                  "tool_input": {"file_path": str(self.root / "NORTHSTAR.md"), "content": "x"}}
        for stdin in (json.dumps(locked), "not json"):
            proc = subprocess.run(["python", str(gate)], input=stdin, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 2, f"gate did not block: {stdin!r}")

    def test_installed_session_start_pull_parses(self) -> None:
        script = self.hooks_dir / "session-start-pull.ps1"
        check = (f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{script}', [ref]$null, [ref]$e); if ($e) {{ $e | Out-String; exit 1 }}")
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", check],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class Reinstall(BootstrapSandbox):
    def test_keeps_unowned_hooks_replaces_owned_and_backs_up(self) -> None:
        self.hooks_dir.mkdir(parents=True)
        (self.hooks_dir / "decay_reminder.py").write_text("# stale installed copy\n", encoding="utf-8")
        legacy = (REPO / "hooks" / "session-start-pull.ps1").as_posix()
        unowned = {"type": "command", "command": "python", "args": ["elsewhere/capture_start.py"], "timeout": 5}
        existing = {"hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [
                {"type": "command", "command": "powershell", "args": ["-File", legacy], "timeout": 15}]}],
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": "python",
                 "args": [(self.hooks_dir / "decay_reminder.py").as_posix()], "timeout": 10},
                unowned]}],
            "Notification": [{"hooks": [unowned]}],
        }}
        # A registration carried over from a different profile path is still the template's.
        existing["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "python",
                                                 "args": ["D:/Users/old/.claude/hooks/evidence_evacuate.py"]}]}]
        (self.claude / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        self.run_bootstrap()
        settings = self.settings()
        all_handlers = list(handlers(settings))

        self.assertEqual(sum(1 for e, _, h in all_handlers if h == unowned), 2)
        self.assertIn(("UserPromptSubmit", None, unowned), all_handlers)
        self.assertIn(("Notification", None, unowned), all_handlers)
        self.assertNotIn(legacy.lower(), json.dumps(settings).lower(), "repo-path registration survived")
        self.assertNotIn("d:/users/old/", json.dumps(settings).lower(), "foreign-profile registration survived")
        decay = [h for e, _, h in all_handlers if h.get("args", [""])[-1].endswith("/decay_reminder.py")]
        self.assertEqual(len(decay), 1, "owned hook duplicated instead of replaced")
        self.assertEqual((self.hooks_dir / "decay_reminder.py").read_bytes(),
                         (REPO / "hooks" / "decay_reminder.py").read_bytes())
        self.assertTrue(list(self.hooks_dir.glob("decay_reminder.py.pre-profile-backup-*")))
        self.assertTrue(list(self.claude.glob("settings.json.pre-profile-backup-*")))

        # Idempotent: a second run changes nothing and makes no new backups.
        before = (self.claude / "settings.json").read_text(encoding="utf-8")
        backups = sorted(p.name for p in self.claude.rglob("*.pre-profile-backup-*"))
        self.run_bootstrap()
        self.assertEqual((self.claude / "settings.json").read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(p.name for p in self.claude.rglob("*.pre-profile-backup-*")), backups)


if __name__ == "__main__":
    unittest.main()
