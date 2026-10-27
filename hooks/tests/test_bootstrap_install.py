"""Install, re-install and session-start-sync tests for bootstrap.py / bootstrap.ps1.

Every test runs the real installer or hook with HOME, USERPROFILE and APPDATA pointed at
a throwaway sandbox under the OS temp folder, so nothing on the live machine is read or
written. The sandbox also holds a synthetic "repos" folder (a claude-tools clone and a
BIMpossible folder) so root detection is exercised without depending on this machine.
Tests that must edit the profile itself (template, hooks) run against a committed copy
of this clone in the sandbox - which also proves a clone works from any folder.

Run: python hooks/tests/test_bootstrap_install.py   (or pytest hooks/tests)
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import profile_setup as ps  # noqa: E402

HOOK_SOURCES = sorted(p for p in (REPO / "hooks").iterdir() if p.is_file() and p.suffix in {".py", ".ps1"})
EXECUTABLE_HOOKS = {p.name for p in HOOK_SOURCES}
WINDOWS_ONLY_HOOKS = {"Show-AuditAlerts.ps1"}
CAPTURE_TOOL_SHA256 = "ce559bfe38d98a730f0d4195f4349862ec19e253f4acb54d1f0743d3d9bf46e9"
TEMPLATE = json.loads((REPO / "settings.template.json").read_text(encoding="utf-8"))
POWERSHELL = os.environ.get("BOOTSTRAP_TEST_POWERSHELL", "powershell.exe")
PY_NAME = "python" if os.name == "nt" else "python3"


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def remove_sandbox(root: Path) -> None:
    # Bootstrap links <home>/.claude/skills into a clone. Unlink the link itself before
    # rmtree so cleanup can never recurse into the clone's skills.
    skills = root / "home" / ".claude" / "skills"
    if is_link(skills):
        os.rmdir(skills) if os.name == "nt" else skills.unlink()
    shutil.rmtree(root, ignore_errors=True)


def handlers(settings: dict):
    for event, groups in settings["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, group.get("matcher"), handler


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "init.defaultBranch=main", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def copy_profile(dest: Path) -> Path:
    """A committed copy of this clone's install-relevant files (skills/ left empty)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("bootstrap.py", "bootstrap.ps1", "settings.template.json", "CLAUDE.md", ".gitattributes"):
        shutil.copy2(REPO / name, dest / name)
    ignore = shutil.ignore_patterns("__pycache__", "tests", "*.log*")
    for name in ("hooks", "tools", "vendor", "mcp"):
        shutil.copytree(REPO / name, dest / name, ignore=ignore)
    (dest / "skills").mkdir()
    (dest / "skills" / "README.md").write_text("fixture\n", encoding="utf-8")
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "fixture")
    return dest


class Sandbox(unittest.TestCase):
    repo: Path = REPO

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bootstrap-test-")).resolve()
        self.addCleanup(remove_sandbox, self.root)
        self.home = self.root / "home"
        self.appdata = self.root / "appdata"
        self.repos = self.root / "repos"
        for d in (self.home, self.appdata, self.repos / "Claude-Tools" / "reports", self.repos / "BIMpossible"):
            d.mkdir(parents=True)
        self.claude = self.home / ".claude"

    def env(self, **extra: str) -> dict:
        temp = Path(tempfile.gettempdir()).resolve()
        for sandboxed in (self.home, self.appdata):
            self.assertTrue(sandboxed.is_relative_to(temp), f"{sandboxed} is not a sandbox path")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROFILE_MACHINE_CONFIG"}
        env.update({"HOME": str(self.home), "USERPROFILE": str(self.home), "APPDATA": str(self.appdata),
                    "CLAUDE_PROFILE_PULL": "0"})
        env.update(extra)
        return env

    def run_bootstrap(self, *args: str, repo: Path | None = None, expect_ok: bool = True,
                      default_roots: bool = True) -> subprocess.CompletedProcess:
        repo = repo or self.repo
        extra = ["--repos-root", str(self.repos)] if default_roots and "--repos-root" not in args else []
        proc = subprocess.run([sys.executable, "-I", str(repo / "bootstrap.py"), "--yes", *extra, *args],
                              env=self.env(), cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        if expect_ok:
            self.assertEqual(proc.returncode, 0, f"bootstrap failed:\n{proc.stdout}\n{proc.stderr}")
        else:
            self.assertNotEqual(proc.returncode, 0, f"bootstrap should have refused:\n{proc.stdout}")
        return proc

    def run_session_start(self, repo: Path | None = None, source: str = "startup", **env: str):
        repo = repo or self.repo
        proc = subprocess.run([sys.executable, str(repo / "hooks" / "session_start.py")],
                              input=json.dumps({"hook_event_name": "SessionStart", "source": source}),
                              env=self.env(**env), cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        return out["hookSpecificOutput"]["additionalContext"]

    def settings(self) -> dict:
        return json.loads((self.claude / "settings.json").read_text(encoding="utf-8-sig"))

    def machine_config(self) -> dict:
        return json.loads((self.claude / "profile-machine.json").read_text(encoding="utf-8"))

    def log(self) -> str:
        p = self.claude / "hooks" / "last-run.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""


class CleanInstall(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.proc = self.run_bootstrap()

    def test_machine_config_holds_detected_roots_and_no_mailbox(self) -> None:
        cfg = self.machine_config()
        self.assertEqual(Path(cfg["repos_root"]), self.repos)
        self.assertEqual(Path(cfg["tools_root"]), self.repos / "Claude-Tools", "case-variant clone not found")
        self.assertEqual(Path(cfg["reports_dir"]), self.repos / "Claude-Tools" / "reports")
        self.assertEqual(cfg["collab_mailbox"], "")
        self.assertEqual(cfg["additional_directories"], [(self.repos / "BIMpossible").as_posix()])
        self.assertEqual(cfg["python"], PY_NAME)

    def test_stub_and_skills_link_point_at_this_clone(self) -> None:
        self.assertEqual((self.claude / "CLAUDE.md").read_text(encoding="utf-8"), f"@{REPO.as_posix()}/CLAUDE.md")
        self.assertTrue(is_link(self.claude / "skills"))
        self.assertEqual(os.path.realpath(self.claude / "skills"), os.path.realpath(REPO / "skills"))

    def test_every_registration_runs_from_this_clone(self) -> None:
        prefix = REPO.as_posix() + "/"
        text = json.dumps(self.settings())
        self.assertNotRegex(text, r"__[A-Z_]+__", "a template token survived")
        self.assertNotIn('"platforms"', text)
        for event, _, h in handlers(self.settings()):
            if h["type"] != "command":
                continue
            script = h["args"][-1]
            self.assertTrue(script.startswith(prefix), f"{event} handler not in this clone: {script}")
            self.assertTrue(Path(script).is_file(), f"{event} registers missing {script}")
            if h["command"] != "powershell":
                self.assertEqual(h["command"], PY_NAME)
        self.assertFalse((self.claude / "hooks").exists() and any((self.claude / "hooks").glob("*.py")),
                         "bootstrap copied hook code into ~/.claude/hooks")

    def test_every_executable_hook_is_registered(self) -> None:
        text = json.dumps(self.settings()["hooks"])
        for name in EXECUTABLE_HOOKS:
            if name in WINDOWS_ONLY_HOOKS and os.name != "nt":
                self.assertNotIn(f"/{name}", text, f"{name} is Windows-only")
                continue
            self.assertIn(f"/hooks/{name}", text, f"{name} ships in hooks/ but the template never registers it")

    def test_control_session_and_graph_hooks_keep_their_events(self) -> None:
        seen = {(event, matcher, Path(h["args"][-1]).name) for event, matcher, h in handlers(self.settings())
                if h["type"] == "command"}
        self.assertIn(("PreToolUse", "Write|Edit", "protect_paths_gate.py"), seen)
        self.assertIn(("SessionStart", "startup|resume|clear|compact", "session_start.py"), seen)
        self.assertIn(("SessionStart", "startup|resume|clear|compact", "graphify_hint.py"), seen)
        self.assertIn(("SubagentStart", None, "graphify_hint.py"), seen)
        self.assertIn(("UserPromptSubmit", None, "graphify_hint.py"), seen)
        first = self.settings()["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertTrue(first["args"][-1].endswith("/session_start.py"), "session_start must run first")
        prompts = [h for _, _, h in handlers(self.settings()) if h["type"] == "prompt"]
        self.assertEqual(len(prompts), 1, "skill-detection prompt hook missing or duplicated")
        self.assertEqual(self.settings()["env"]["GRAPHIFY_NO_AUTO_REFRESH"], "1")

    def test_capture_hooks_are_registered_once_at_the_vendored_unit(self) -> None:
        seen = [(e, h["args"][-1]) for e, _, h in handlers(self.settings())
                if h["type"] == "command" and "capture_" in h["args"][-1]]
        unit = (REPO / "vendor" / "evidence-capture").as_posix() + "/"
        self.assertEqual(sorted((e, a.startswith(unit), Path(a).name) for e, a in seen),
                         [("Stop", True, "capture_end.py"), ("UserPromptSubmit", True, "capture_start.py")])

    def test_vendored_capture_unit_is_the_pinned_code(self) -> None:
        unit = REPO / "vendor" / "evidence-capture"
        probe = ("import json, sys; sys.path.insert(0, sys.argv[1]); import pipeline, screen; "
                 "print(json.dumps([pipeline.tool_identity()[1], screen.__file__, sys.modules['shell_paths'].__file__]))")
        proc = subprocess.run([sys.executable, "-I", "-c", probe, str(unit)], capture_output=True, text=True,
                              timeout=60, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sha, screen_file, shell_paths_file = json.loads(proc.stdout)
        self.assertEqual(sha, CAPTURE_TOOL_SHA256, "vendor/README.md tool_sha256 is stale or the unit changed")
        for loaded in (screen_file, shell_paths_file):
            self.assertTrue(Path(loaded).resolve().is_relative_to((REPO / "vendor").resolve()), loaded)

    def test_vendored_capture_hooks_capture_synthetic_episodes(self) -> None:
        out = self.root / "dry"
        out.mkdir()
        env = {k: v for k, v in self.env().items() if not k.startswith(("EC_CAPTURE", "EVIDENCE_CAPTURE"))}
        proc = subprocess.run([sys.executable, "-I", str(REPO / "vendor" / "evidence-capture" / "capture.py"),
                               "dry", "--out", str(out), "-n", "2"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300, env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["exit_codes"], [0])
        self.assertEqual(result["stdout_bytes"], 0, "a capture hook printed to the agent")
        self.assertEqual(result["manifests_unreadable"], [])
        self.assertEqual(result["boundary_trusted"], 2)
        self.assertTrue(result["checkout_clean"] and result["index_unchanged"])
        manifests = [json.loads(p.read_text(encoding="utf-8")) for p in out.rglob("*.json")
                     if '"tool_sha256"' in p.read_text(encoding="utf-8", errors="replace")]
        self.assertTrue(manifests, "dry run wrote no manifest")
        self.assertEqual({m["capture"]["tool_sha256"] for m in manifests if "capture" in m}, {CAPTURE_TOOL_SHA256})

    def test_registered_hooks_execute_from_the_clone(self) -> None:
        work = self.root / "work"
        work.mkdir()
        env = self.env(EVIDENCE_EVACUATE_LOG=str(self.root / "evacuate.log"), CLAUDE_PROFILE_SYNC="0")
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
            payload = {**payloads[event], "cwd": str(work), "session_id": "bootstrap-test"}
            proc = subprocess.run([handler["command"], *handler["args"]], input=json.dumps(payload),
                                  env=env, cwd=work, capture_output=True, text=True,
                                  timeout=max(handler.get("timeout", 30), 60))
            self.assertEqual(proc.returncode, 0, f"{event} {name} exit {proc.returncode}: {proc.stderr}")
            ran.add(name)
        expected = {n for n in EXECUTABLE_HOOKS if os.name == "nt" or n not in WINDOWS_ONLY_HOOKS}
        self.assertEqual(ran, expected | {"capture_start.py", "capture_end.py"})
        # Runtime state lands in the (sandboxed) profile, never in the clone.
        records = (self.claude / "hooks" / "logs" / "graphify_hint.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual({json.loads(r)["event"] for r in records}, {"SessionStart", "SubagentStart", "UserPromptSubmit"})

    def test_gate_still_fails_closed(self) -> None:
        gate = REPO / "hooks" / "protect_paths_gate.py"
        locked = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                  "tool_input": {"file_path": str(self.root / "NORTHSTAR.md"), "content": "x"}}
        for stdin in (json.dumps(locked), "not json"):
            proc = subprocess.run([sys.executable, str(gate)], input=stdin, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 2, f"gate did not block: {stdin!r}")

    def test_second_run_changes_nothing(self) -> None:
        before = {p.name: p.read_bytes() for p in self.claude.iterdir() if p.is_file()}
        proc = self.run_bootstrap()
        after = {p.name: p.read_bytes() for p in self.claude.iterdir() if p.is_file()}
        self.assertEqual(after, before)
        self.assertIn("settings.json already correct", proc.stdout)
        self.assertIn("Machine config already correct", proc.stdout)


class MachineConfigReruns(Sandbox):
    def test_stored_values_win_and_flags_override(self) -> None:
        self.run_bootstrap("--collab-mailbox", str(self.root / "mailbox"))
        self.assertEqual(Path(self.machine_config()["collab_mailbox"]), self.root / "mailbox")
        cfg = self.machine_config()
        cfg["reports_dir"] = str(self.root / "my-reports")
        (self.claude / "profile-machine.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.run_bootstrap()
        cfg = self.machine_config()
        self.assertEqual(Path(cfg["reports_dir"]), self.root / "my-reports", "a hand-edited value was replaced")
        self.assertEqual(Path(cfg["collab_mailbox"]), self.root / "mailbox", "the mailbox was dropped on re-run")
        self.run_bootstrap("--no-collab-mailbox")
        self.assertEqual(self.machine_config()["collab_mailbox"], "")

    def test_detects_repos_beside_the_clone_without_flags(self) -> None:
        clone = copy_profile(Path(tempfile.mkdtemp(dir=self.repos, prefix="claude-profile-")))
        self.run_bootstrap(repo=clone, default_roots=False)
        cfg = self.machine_config()
        self.assertEqual(Path(cfg["repos_root"]), self.repos)
        self.assertEqual(Path(cfg["tools_root"]), self.repos / "Claude-Tools")

    def test_unreadable_machine_config_is_refused_before_any_write(self) -> None:
        self.claude.mkdir()
        (self.claude / "profile-machine.json").write_text("{not json", encoding="utf-8")
        proc = self.run_bootstrap(expect_ok=False)
        self.assertIn("profile-machine.json", proc.stdout + proc.stderr)
        self.assertEqual(sorted(p.name for p in self.claude.iterdir()), ["profile-machine.json"])

    def test_dry_run_writes_nothing(self) -> None:
        proc = self.run_bootstrap("--dry-run")
        self.assertIn("DRY RUN - nothing written", proc.stdout)
        self.assertFalse(self.claude.exists())
        self.assertEqual(list(self.appdata.iterdir()), [])


class Reinstall(Sandbox):
    def test_keeps_unowned_hooks_replaces_owned_and_backs_up(self) -> None:
        pinned = self.claude / "hooks"
        pinned.mkdir(parents=True)
        (pinned / "decay_reminder.py").write_text("# copy pinned by an earlier bootstrap\n", encoding="utf-8")
        unowned = {"type": "command", "command": "python", "args": ["elsewhere/capture_start.py"], "timeout": 5}
        in_repo_unowned = {"type": "command", "command": "python",
                           "args": [(REPO / "skills" / "graphify" / "scripts" / "local_hint.py").as_posix()]}
        existing = {"hooks": {
            "SubagentStop": [{"hooks": [in_repo_unowned]}],
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [
                {"type": "command", "command": "powershell",
                 "args": ["-File", (pinned / "session-start-pull.ps1").as_posix()], "timeout": 15}]}],
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": "python", "args": [(pinned / "decay_reminder.py").as_posix()]},
                unowned]}],
            "Notification": [{"hooks": [unowned]}],
            # A registration from another clone of this profile (the clone moved).
            "Stop": [{"hooks": [{"type": "command", "command": "python",
                                 "args": ["/old/place/claude-profile/hooks/evidence_evacuate.py"]}]}],
        }}
        self.claude.joinpath("settings.json").write_text(json.dumps(existing), encoding="utf-8")

        proc = self.run_bootstrap()
        settings = self.settings()
        all_handlers = list(handlers(settings))
        self.assertEqual(sum(1 for _, _, h in all_handlers if h == unowned), 2)
        self.assertIn(("Notification", None, unowned), all_handlers)
        self.assertIn(("SubagentStop", None, in_repo_unowned), all_handlers,
                      "a hook under the clone but outside hooks/ was dropped")
        text = json.dumps(settings).lower()
        self.assertNotIn("session-start-pull", text, "the retired pinned pull hook survived")
        self.assertNotIn(pinned.as_posix().lower(), text, "a pinned-copy registration survived")
        self.assertNotIn("/old/place/", text, "a moved-clone registration survived")
        decay = [h for _, _, h in all_handlers if h.get("args", [""])[-1].endswith("/decay_reminder.py")]
        self.assertEqual(len(decay), 1, "owned hook duplicated instead of replaced")
        self.assertTrue(list(self.claude.glob("settings.json.pre-profile-backup-*")))
        self.assertIn("no longer registered", proc.stdout, "the pinned copies were not called out")
        self.assertTrue((pinned / "decay_reminder.py").exists(), "bootstrap deleted a pinned copy")

        before = (self.claude / "settings.json").read_bytes()
        backups = sorted(p.name for p in self.claude.rglob("*.pre-profile-backup-*"))
        self.run_bootstrap()
        self.assertEqual((self.claude / "settings.json").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.claude.rglob("*.pre-profile-backup-*")), backups)

    def test_upstream_checkout_registrations_are_superseded(self) -> None:
        legacy_audit = {"type": "command", "command": "powershell", "timeout": 15, "args": [
            "-File", "/fixture/BIMpossible-Workspace/system/scheduled-tasks/audit-watchdog/Show-AuditAlerts.ps1"]}
        capture_start = {"type": "command", "command": "python", "args": ["/fixture/claude-tools/evidence-capture/capture_start.py"]}
        capture_end = {"type": "command", "command": "python", "args": ["/fixture/claude-tools/evidence-capture/capture_end.py"]}
        self.claude.mkdir()
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [legacy_audit]}],
            "UserPromptSubmit": [{"hooks": [capture_start]}], "Stop": [{"hooks": [capture_end]}]}}), encoding="utf-8")
        self.run_bootstrap()
        all_handlers = list(handlers(self.settings()))
        audit = [h for _, _, h in all_handlers if h.get("args", [""])[-1].endswith("Show-AuditAlerts.ps1")]
        self.assertEqual(len(audit), 1 if os.name == "nt" else 0)
        capture = sorted((e, Path(h["args"][-1]).name) for e, _, h in all_handlers
                         if h["type"] == "command" and "capture_" in h["args"][-1])
        self.assertEqual(capture, [("Stop", "capture_end.py"), ("UserPromptSubmit", "capture_start.py")])
        self.assertNotIn("/fixture/", json.dumps(self.settings()["hooks"]))

    def test_existing_skills_directory_is_moved_aside_not_deleted(self) -> None:
        (self.claude / "skills" / "mine").mkdir(parents=True)
        (self.claude / "skills" / "mine" / "SKILL.md").write_text("mine\n", encoding="utf-8")
        self.run_bootstrap()
        backups = list(self.claude.glob("skills.pre-profile-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "mine" / "SKILL.md").read_text(encoding="utf-8"), "mine\n")
        self.assertTrue(is_link(self.claude / "skills"))


class SettingsPreservation(Sandbox):
    SECRET = "sandbox-secret-value-not-real"
    HOOK_ARG = "sandbox-hook-arg-not-for-output"

    def existing(self) -> dict:
        plugin = next(iter(TEMPLATE["enabledPlugins"]))
        return {
            "autoMode": {"allow": ["$defaults", "example exception — non-ASCII kept"]},
            "env": {"EXAMPLE_TOKEN": self.SECRET},
            "cleanupPeriodDays": 45,
            "statusLine": {"type": "command", "command": "echo status"},
            "model": "user-chosen-model",
            "permissions": {"allow": ["Bash(gh pr ready:*)"], "deny": ["Read(**/.env)"], "ask": [],
                            "defaultMode": "default", "additionalDirectories": ["/fixture/Extra-Dir"]},
            "enabledPlugins": {plugin: not TEMPLATE["enabledPlugins"][plugin], "user-only@local": True},
            "hooks": {"Notification": [{"hooks": [{"type": "command", "command": "python",
                                                   "args": ["elsewhere/notify.py", self.HOOK_ARG]}]}]},
        }

    def write_existing(self, data: dict | str) -> bytes:
        self.claude.mkdir(parents=True, exist_ok=True)
        raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
        (self.claude / "settings.json").write_bytes(raw.encode("utf-8"))
        return (self.claude / "settings.json").read_bytes()

    def backups(self) -> list[Path]:
        return sorted(self.claude.glob("settings.json.pre-profile-backup-*"))

    def test_preserves_user_owned_settings_and_second_run_is_identical(self) -> None:
        existing = self.existing()
        original = self.write_existing(existing)
        self.run_bootstrap()
        s = self.settings()
        for key in ("autoMode", "cleanupPeriodDays", "statusLine", "model"):
            self.assertEqual(s[key], existing[key], f"{key} was not preserved")
        self.assertEqual(s["env"]["EXAMPLE_TOKEN"], self.SECRET, "an existing env entry was changed")
        self.assertEqual(s["env"]["GRAPHIFY_NO_AUTO_REFRESH"], "1", "the template env default was not added")
        for key in ("allow", "deny", "ask", "defaultMode"):
            self.assertEqual(s["permissions"][key], existing["permissions"][key], f"permissions.{key}")
        self.assertEqual(s["permissions"]["additionalDirectories"][0], "/fixture/Extra-Dir")
        for plugin, value in existing["enabledPlugins"].items():
            self.assertEqual(s["enabledPlugins"][plugin], value, "an existing plugin choice was overridden")
        self.assertTrue(set(TEMPLATE["enabledPlugins"]) <= set(s["enabledPlugins"]))
        self.assertIn(("Notification", None, existing["hooks"]["Notification"][0]["hooks"][0]), list(handlers(s)))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), original, "backup is not the exact prior file")
        before = (self.claude / "settings.json").read_bytes()
        proc = self.run_bootstrap()
        self.assertEqual((self.claude / "settings.json").read_bytes(), before, "second run changed settings.json")
        self.assertEqual(len(self.backups()), 1, "second run made a backup")
        self.assertIn("settings.json already correct", proc.stdout)

    def test_fresh_install_takes_template_values(self) -> None:
        self.run_bootstrap()
        s = self.settings()
        self.assertEqual(s["model"], TEMPLATE["model"])
        self.assertEqual(s["enabledPlugins"], TEMPLATE["enabledPlugins"])
        self.assertNotIn("autoMode", s)
        self.assertEqual(self.backups(), [])

    def test_null_and_absent_keys_take_template_values(self) -> None:
        self.write_existing({"permissions": None, "enabledPlugins": None, "model": None})
        self.run_bootstrap()
        s = self.settings()
        self.assertEqual(s["enabledPlugins"], TEMPLATE["enabledPlugins"])
        self.assertIsInstance(s["permissions"]["additionalDirectories"], list)
        self.assertIsNone(s["model"], "an explicit null is the user's value and is kept")

    def test_case_only_change_is_written_and_backed_up(self) -> None:
        self.run_bootstrap()
        path = self.claude / "settings.json"
        text = path.read_text(encoding="utf-8")
        stale = text.replace("protect_paths_gate.py", "PROTECT_PATHS_GATE.py")
        self.assertNotEqual(stale, text)
        path.write_text(stale, encoding="utf-8")
        proc = self.run_bootstrap()
        self.assertEqual(path.read_text(encoding="utf-8"), text, "case-only difference was not rewritten")
        self.assertRegex(proc.stdout, r"(?m)^  changed /hooks/")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_text(encoding="utf-8"), stale)

    def test_formatting_only_difference_is_left_untouched(self) -> None:
        self.write_existing(self.existing())
        self.run_bootstrap()
        path = self.claude / "settings.json"
        reformatted = json.dumps(json.loads(path.read_text(encoding="utf-8-sig")), indent=4, ensure_ascii=False) + "\n"
        path.write_bytes(reformatted.encode("utf-8"))
        backups = self.backups()
        proc = self.run_bootstrap()
        self.assertEqual(path.read_bytes(), reformatted.encode("utf-8"), "a formatting-only difference was rewritten")
        self.assertEqual(self.backups(), backups)
        self.assertIn("settings.json already correct", proc.stdout)

    def test_plan_masks_changed_env_names_and_values(self) -> None:
        before = {"env": {"KEEP_NAME_HIDDEN": "old-secret-value", "REMOVED_NAME_HIDDEN": "gone-secret-value"}, "theme": "dark"}
        after = {"env": {"KEEP_NAME_HIDDEN": "new-secret-value", "ADDED_NAME_HIDDEN": "added-secret-value"}, "theme": "Dark"}
        out = "\n".join(ps.plan_lines(ps.settings_changes(before, after)))
        self.assertIn("settings.json plan: 1 added, 1 removed, 2 changed", out)
        for line in ("  added /env/*", "  removed /env/*", "  changed /env/*", "  changed /theme"):
            self.assertIn(line, out)
        for leaked in ("KEEP_NAME_HIDDEN", "REMOVED_NAME_HIDDEN", "ADDED_NAME_HIDDEN", "secret-value", "dark", "Dark"):
            self.assertNotIn(leaked, out)

    def test_directory_entries_deduplicate_across_case_and_slashes(self) -> None:
        detected = (self.repos / "BIMpossible").as_posix()
        variant = detected.upper().replace("/", "\\") + "\\"
        self.write_existing({"permissions": {"additionalDirectories": [variant, "/fixture/Extra-Dir"]}})
        self.run_bootstrap()
        dirs = self.settings()["permissions"]["additionalDirectories"]
        self.assertEqual(dirs, [variant, "/fixture/Extra-Dir"], "existing spelling and order kept, no duplicate")

    def test_plan_lists_changed_paths_only_never_values(self) -> None:
        self.write_existing(self.existing())
        proc = self.run_bootstrap()
        out = proc.stdout + proc.stderr
        self.assertIn("settings.json plan:", out)
        for leaked in (self.SECRET, self.HOOK_ARG, "user-chosen-model", "echo status", "EXAMPLE_TOKEN"):
            self.assertNotIn(leaked, out, f"plan output leaked {leaked!r}")


def tree_snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    if not root.exists():
        return snap
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            rel = entry.relative_to(root).as_posix()
            if is_link(entry):
                snap[rel] = f"link -> {os.readlink(entry)}"
            elif entry.is_dir():
                snap[rel] = "dir"
                stack.append(entry)
            else:
                snap[rel] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return snap


class RefusalLeavesProfileUntouched(Sandbox):
    BAD_SETTINGS = {
        "malformed": "{not json",
        "empty": "",
        "whitespace-only": "  \n\t\n",
        "null": "null",
        "array": '["an", "array"]',
        "wrong-case Hooks": '{"Hooks": {}}',
        "wrong-case Permissions": '{"Permissions": {"allow": []}}',
        "case-variant hooks collision": '{"hooks": {}, "Hooks": {}}',
        "repeated key": '{"hooks": {}, "hooks": {}}',
        "hooks not an object": '{"hooks": []}',
        "permissions not an object": '{"permissions": "not-an-object"}',
    }

    def plant_profile(self, settings: str, desktop: str = '{"other": 1}') -> None:
        self.claude.mkdir(parents=True)
        (self.claude / "CLAUDE.md").write_bytes(b"# hand-written, not the stub\n")
        (self.claude / "skills").mkdir()
        (self.claude / "skills" / "mine.md").write_bytes(b"user skill\n")
        (self.claude / "settings.json").write_bytes(settings.encode("utf-8"))
        (self.appdata / "Claude").mkdir()
        (self.appdata / "Claude" / "claude_desktop_config.json").write_bytes(desktop.encode("utf-8"))

    def snapshot(self) -> dict:
        return {"claude": tree_snapshot(self.claude), "desktop": tree_snapshot(self.appdata / "Claude")}

    def assert_refused_untouched(self, expect_in_output: str) -> None:
        before = self.snapshot()
        proc = self.run_bootstrap(expect_ok=False)
        out = proc.stdout + proc.stderr
        self.assertIn(expect_in_output, out)
        self.assertIn("refus", out)
        after = self.snapshot()
        self.assertEqual(after, before, f"a refused run changed the profile:\n{proc.stdout}")
        self.assertNotIn("pre-profile-backup", json.dumps(after))

    def test_unusable_settings_is_refused_before_any_write(self) -> None:
        for label, raw in self.BAD_SETTINGS.items():
            with self.subTest(settings=label):
                remove_sandbox(self.root)
                for d in (self.home, self.appdata, self.repos):
                    d.mkdir(parents=True)
                self.plant_profile(raw)
                self.assert_refused_untouched("settings.json")

    def test_wrong_case_key_names_the_expected_key(self) -> None:
        self.plant_profile('{"Hooks": {}}')
        proc = self.run_bootstrap(expect_ok=False)
        self.assertIn("'Hooks' key; Claude Code only reads 'hooks'", proc.stdout + proc.stderr)

    @unittest.skipUnless(os.name == "nt", "the Claude Desktop config is written on Windows only")
    def test_unusable_desktop_config_is_refused_before_any_write(self) -> None:
        self.plant_profile("{}", desktop="{not json")
        self.assert_refused_untouched("claude_desktop_config.json")


class DesktopConfigMerge(unittest.TestCase):
    """plan_desktop_config on hand-planted configs; plans only, nothing is written."""

    def setUp(self) -> None:
        self.appdata = Path(tempfile.mkdtemp(prefix="desktop-merge-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.appdata, True)
        (self.appdata / "Claude").mkdir()
        fresh = ps.plan_desktop_config(self.appdata / "empty", REPO)
        self.servers = json.loads(fresh.text)["mcpServers"]

    def plan(self, existing: str) -> dict:
        (self.appdata / "Claude" / "claude_desktop_config.json").write_text(existing, encoding="utf-8")
        plan = ps.plan_desktop_config(self.appdata, REPO)
        self.assertTrue(plan.changed)
        return json.loads(plan.text)

    def test_empty_object_gains_mcp_servers(self) -> None:
        for raw in ("{}", "{ }", "\n{\n}\n"):
            with self.subTest(existing=raw):
                self.assertEqual(self.plan(raw), {"mcpServers": self.servers})


class FixtureClone(Sandbox):
    """A committed copy of the profile in the sandbox: template edits, refusals, sync."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = copy_profile(self.root / "elsewhere" / "clone")

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.repo / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new), encoding="utf-8")
        git(self.repo, "commit", "-qam", f"edit {rel}")


class Preflight(FixtureClone):
    def test_template_hook_missing_from_the_clone_is_refused_before_any_write(self) -> None:
        self.edit("settings.template.json", "__PROFILE_ROOT__/hooks/decay_reminder.py", "__PROFILE_ROOT__/hooks/not_shipped.py")
        proc = self.run_bootstrap(expect_ok=False)
        self.assertIn("not_shipped.py", proc.stdout + proc.stderr)
        self.assertFalse(self.claude.exists())

    def test_unresolved_template_token_never_reaches_settings(self) -> None:
        self.edit("settings.template.json", '"theme": "dark"', '"theme": "__UNKNOWN_TOKEN__"')
        proc = self.run_bootstrap(expect_ok=False)
        self.assertIn("__UNKNOWN_TOKEN__", proc.stdout + proc.stderr)
        self.assertFalse(self.claude.exists(), "bootstrap wrote to the profile before refusing")

    def test_clone_path_with_a_space_is_refused(self) -> None:
        spaced = self.root / "has space"
        shutil.copytree(self.repo, spaced / "clone")
        proc = self.run_bootstrap(repo=spaced / "clone", expect_ok=False)
        self.assertIn("contains a space", proc.stdout + proc.stderr)
        self.assertFalse(self.claude.exists())

    def test_non_ascii_template_text_round_trips(self) -> None:
        marker = "café — ‐"
        self.edit("settings.template.json", "You decide whether", f"You decide ({marker}) whether")
        self.edit("mcp/claude_desktop_config.template.json", '"command": "cmd",', f'"command": "cmd", "x-note": "{marker}",')
        self.run_bootstrap()
        prompts = [h["prompt"] for _, _, h in handlers(self.settings()) if h["type"] == "prompt"]
        self.assertIn(f"You decide ({marker}) whether", prompts[0])
        if os.name == "nt":
            desktop = json.loads((self.appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
            self.assertEqual(desktop["mcpServers"]["mcp-server-for-revit"]["x-note"], marker)

    @unittest.skipUnless(os.name == "nt" and shutil.which(POWERSHELL), "Windows PowerShell wrapper")
    def test_powershell_wrapper_delegates_and_forwards_options(self) -> None:
        proc = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(self.repo / "bootstrap.ps1"), "-Yes", "-SkipRea",
                               "-ReposRoot", str(self.repos), "-CollabMailbox", str(self.root / "mb")],
                              env=self.env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(Path(self.machine_config()["collab_mailbox"]), self.root / "mb")
        self.assertIn("Skipped REA install", proc.stdout)
        self.assertTrue((self.appdata / "Claude" / "claude_desktop_config.json").is_file())


class SessionStartSync(FixtureClone):
    def test_template_change_reaches_settings_at_session_start_without_bootstrap(self) -> None:
        self.run_bootstrap()
        before = self.settings()
        self.edit("settings.template.json", '"timeout": 25', '"timeout": 26')
        context = self.run_session_start()
        after = self.settings()
        self.assertNotEqual(after, before)
        first = after["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(first["timeout"], 26)
        self.assertTrue((self.claude / "settings.json.pre-profile-sync").is_file())
        self.assertIn("settings synced", self.log())
        self.assertIn("Machine roots for this profile", context)
        self.assertIn(f"{{repos}} = {self.repos}", context)
        self.assertIn("{mailbox} = (not set)", context)
        # Nothing to do the next time: no rewrite.
        stamp = (self.claude / "settings.json").stat().st_mtime_ns
        self.run_session_start()
        self.assertEqual((self.claude / "settings.json").stat().st_mtime_ns, stamp)

    def test_a_configured_collab_mailbox_reaches_the_session(self) -> None:
        mailbox = self.root / "collab" / "this-machine"
        self.run_bootstrap("--collab-mailbox", str(mailbox))
        context = self.run_session_start(CLAUDE_PROFILE_SYNC="0")
        self.assertIn(f"{{mailbox}} = {mailbox}", context)

    def test_unusable_settings_are_left_alone_and_reported(self) -> None:
        self.run_bootstrap()
        (self.claude / "settings.json").write_text("{not json", encoding="utf-8")
        context = self.run_session_start()
        self.assertEqual((self.claude / "settings.json").read_text(encoding="utf-8"), "{not json")
        self.assertIn("Profile settings were not synced", context)

    def test_missing_machine_config_is_called_out(self) -> None:
        context = self.run_session_start(CLAUDE_PROFILE_SYNC="0")
        self.assertIn("is missing", context)
        self.assertIn("run bootstrap once", context)

    def test_pull_fast_forwards_a_clone_in_any_folder(self) -> None:
        origin = self.root / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.repo), str(origin)], check=True, capture_output=True)
        clone = self.root / "any" / "folder" / "profile"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
        git(self.repo, "remote", "add", "origin", str(origin))
        self.edit("CLAUDE.md", "# How to work with me", "# How to work with me (updated)")
        git(self.repo, "push", "-q", "origin", "HEAD:" + git(clone, "rev-parse", "--abbrev-ref", "HEAD"))
        self.run_session_start(repo=clone, CLAUDE_PROFILE_PULL="1", CLAUDE_PROFILE_SYNC="0")
        self.assertIn("(updated)", (clone / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn("pull ok", self.log())

    def test_compact_does_not_pull_or_sync(self) -> None:
        context = self.run_session_start(source="compact", CLAUDE_PROFILE_PULL="1")
        self.assertIn("Machine roots", context)
        self.assertEqual(self.log(), "")

    def test_a_held_lock_skips_the_pull(self) -> None:
        state = self.claude / "hooks"
        state.mkdir(parents=True)
        holder = subprocess.Popen([sys.executable, "-c", (
            "import sys, time, os\n"
            "f = open(sys.argv[1], 'a+b')\n"
            "if os.name == 'nt':\n"
            "    import msvcrt; f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
            "else:\n"
            "    import fcntl; fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
            "print('held', flush=True); time.sleep(30)\n"), str(state / "last-run.log.pull-lock")],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        self.run_session_start(CLAUDE_PROFILE_PULL="1")
        self.assertIn("pull skipped (in flight)", self.log())

    def test_warns_when_the_clone_is_not_on_its_default_branch(self) -> None:
        origin = self.root / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.repo), str(origin)], check=True, capture_output=True)
        clone = self.root / "any" / "profile"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
        git(clone, "switch", "-q", "-c", "feature")
        context = self.run_session_start(repo=clone, CLAUDE_PROFILE_SYNC="0")
        self.assertIn("is on 'feature'", context)


@unittest.skipUnless(os.name == "nt" and shutil.which(POWERSHELL), "Show-AuditAlerts.ps1 is Windows-only")
class AuditAlertsHook(Sandbox):
    """The pinned Show-AuditAlerts.ps1, run from the clone against a sandboxed watchdog state."""

    def setUp(self) -> None:
        super().setUp()
        self.state = self.home / ".claude" / "scheduled-tasks" / "_watchdog"
        self.state.mkdir(parents=True)

    def run_hook(self) -> subprocess.CompletedProcess:
        return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(REPO / "hooks" / "Show-AuditAlerts.ps1")],
                              env=self.env(), input="{}", capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)

    def write_status(self, **extra) -> None:
        status = {"evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "audits": [], **extra}
        (self.state / "status.json").write_text(json.dumps(status), encoding="utf-8")

    def test_open_alert_is_surfaced_and_exit_is_zero(self) -> None:
        self.write_status()
        alert = {"id": "a1", "status": "open", "class": "FINDING", "task": "weekly-audit", "kind": "missed",
                 "run": "2026-10-05", "first_seen": "2026-10-06T09:00:00", "message": "no report",
                 "next_action": "run it"}
        (self.state / "alerts.json").write_text(json.dumps({"a1": alert}), encoding="utf-8")
        proc = self.run_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("weekly-audit", out["systemMessage"])
        self.assertIn("ack id: a1", out["systemMessage"])

    def test_nothing_to_report_prints_nothing(self) -> None:
        self.write_status()
        proc = self.run_hook()
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, ""), proc.stderr)

    def test_missing_state_is_reported_not_swallowed(self) -> None:
        proc = self.run_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no status", json.loads(proc.stdout)["systemMessage"])

    def test_corrupt_state_never_blocks_startup(self) -> None:
        self.write_status()
        (self.state / "alerts.json").write_text("{not json", encoding="utf-8")
        proc = self.run_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("could not read state", json.loads(proc.stdout)["systemMessage"])


if __name__ == "__main__":
    unittest.main()
