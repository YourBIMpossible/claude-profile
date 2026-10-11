"""Clean-install and reinstall tests for bootstrap.ps1's hook installation.

Runs the real bootstrap under Windows PowerShell 5.1 (its declared floor) with
USERPROFILE and APPDATA pointed at a throwaway sandbox, so nothing on the live
machine is read or written. Set BOOTSTRAP_TEST_POWERSHELL=pwsh to run the suite
under PowerShell 7 instead; the UTF-8 template test always runs under both.
Guards the regression where settings.template.json registered only
session-start-pull, so any reinstall silently dropped every
other hook -- including the fail-closed protect_paths_gate control.

Run: python hooks/tests/test_bootstrap_install.py
"""
from __future__ import annotations

import datetime
import hashlib
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
# Pinned copies of upstream-owned multi-file units (vendor/README.md), installed whole.
VENDOR_UNITS = sorted(p for p in (REPO / "vendor").iterdir() if p.is_dir())
CAPTURE_TOOL_SHA256 = "ce559bfe38d98a730f0d4195f4349862ec19e253f4acb54d1f0743d3d9bf46e9"
# session-start-pull runs `git pull` in the repo; it is parse-checked, never executed here.
NOT_EXECUTED = {"session-start-pull.ps1"}
POWERSHELL = os.environ.get("BOOTSTRAP_TEST_POWERSHELL", "powershell.exe")


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

    def run_bootstrap(self, repo: Path = REPO, *args: str, expect_ok: bool = True,
                      powershell: str = POWERSHELL) -> subprocess.CompletedProcess:
        # bootstrap writes only under USERPROFILE and APPDATA; both must be this sandbox.
        temp = Path(tempfile.gettempdir()).resolve()
        for sandboxed in (self.home, self.appdata):
            self.assertTrue(sandboxed.resolve().is_relative_to(temp), f"{sandboxed} is not a sandbox path")
            for real in (os.environ.get("USERPROFILE"), os.environ.get("APPDATA")):
                self.assertNotEqual(str(sandboxed).lower(), (real or "").lower())
        env = {**os.environ, "USERPROFILE": str(self.home), "APPDATA": str(self.appdata)}
        proc = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "bootstrap.ps1"), *args],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        if expect_ok:
            self.assertEqual(proc.returncode, 0, f"bootstrap failed:\n{proc.stdout}\n{proc.stderr}")
        else:
            self.assertNotEqual(proc.returncode, 0, f"bootstrap should have refused:\n{proc.stdout}")
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

    def test_every_vendored_unit_is_installed_whole_and_byte_identical(self) -> None:
        for unit in VENDOR_UNITS:
            source = {p.relative_to(unit).as_posix(): p.read_bytes() for p in unit.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts}
            target = self.hooks_dir / unit.name
            installed = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*")
                         if p.is_file() and "__pycache__" not in p.parts}
            self.assertEqual(installed, source, unit.name)

    def test_capture_hooks_are_registered_once_at_the_installed_unit(self) -> None:
        seen = [(event, h["args"][-1]) for event, _, h in handlers(self.settings())
                if h["type"] == "command" and "capture_" in h["args"][-1]]
        prefix = self.hooks_prefix() + "evidence-capture/"
        self.assertEqual(sorted((e, a.lower().startswith(prefix), Path(a).name) for e, a in seen),
                         [("Stop", True, "capture_end.py"), ("UserPromptSubmit", True, "capture_start.py")])

    def test_installed_capture_unit_runs_without_the_upstream_checkout(self) -> None:
        # The tool hashes every *.py beside it: the installed unit must be the pinned code.
        unit = self.hooks_dir / "evidence-capture"
        probe = ("import json, sys; sys.path.insert(0, sys.argv[1]); import pipeline, screen; "
                 "print(json.dumps([pipeline.tool_identity()[1], screen.__file__, sys.modules['shell_paths'].__file__]))")
        proc = subprocess.run(["python", "-I", "-c", probe, str(unit)], capture_output=True, text=True, timeout=60,
                              cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sha, screen_file, shell_paths_file = json.loads(proc.stdout)
        self.assertEqual(sha, CAPTURE_TOOL_SHA256, "vendor/README.md tool_sha256 is stale or the copy differs")
        for loaded in (screen_file, shell_paths_file):
            self.assertTrue(Path(loaded).resolve().is_relative_to(self.hooks_dir.resolve()), loaded)

    def test_installed_capture_hooks_capture_synthetic_episodes(self) -> None:
        # Upstream's own dry mode: the real installed capture_start/capture_end against a
        # synthetic repository and stores inside the sandbox; nothing else is touched.
        out = self.root / "dry"
        out.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("EC_CAPTURE", "EVIDENCE_CAPTURE"))}
        proc = subprocess.run(["python", "-I", str(self.hooks_dir / "evidence-capture" / "capture.py"),
                               "dry", "--out", str(out), "-n", "2"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300, env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["exit_codes"], [0])
        self.assertEqual(result["stdout_bytes"], 0, "a capture hook printed to the agent")
        self.assertEqual(result["manifests_unreadable"], 0)
        self.assertEqual(result["boundary_trusted"], 2)
        self.assertTrue(result["checkout_clean"] and result["index_unchanged"])
        manifests = [json.loads(p.read_text(encoding="utf-8")) for p in out.rglob("*.json")
                     if '"tool_sha256"' in p.read_text(encoding="utf-8", errors="replace")]
        self.assertTrue(manifests, "dry run wrote no manifest")
        self.assertEqual({m["capture"]["tool_sha256"] for m in manifests if "capture" in m}, {CAPTURE_TOOL_SHA256})

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
        # Fallback for harness paths that fire no SessionStart (desktop forks).
        self.assertIn(("UserPromptSubmit", None, "graphify_hint.py"), seen)
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
        # graphify_hint's diagnostic log defaults under the (sandboxed) profile.
        records = (self.hooks_dir / "logs" / "graphify_hint.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual({json.loads(r)["event"] for r in records},
                         {"SessionStart", "SubagentStart", "UserPromptSubmit"})

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
        proc = subprocess.run([POWERSHELL, "-NoProfile", "-Command", check],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class Reinstall(BootstrapSandbox):
    def test_keeps_unowned_hooks_replaces_owned_and_backs_up(self) -> None:
        self.hooks_dir.mkdir(parents=True)
        (self.hooks_dir / "decay_reminder.py").write_text("# stale installed copy\n", encoding="utf-8")
        legacy = (REPO / "hooks" / "session-start-pull.ps1").as_posix()
        unowned = {"type": "command", "command": "python", "args": ["elsewhere/capture_start.py"], "timeout": 5}
        # Lives in this repo but outside hooks/: machine-specific, not the template's.
        in_repo_unowned = {"type": "command", "command": "python",
                           "args": [(REPO / "skills" / "graphify" / "scripts" / "local_hint.py").as_posix()]}
        existing = {"hooks": {
            "SubagentStop": [{"hooks": [in_repo_unowned]}],
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
        self.assertIn(("SubagentStop", None, in_repo_unowned), all_handlers,
                      "machine-specific hook under the repo but outside hooks/ was dropped")
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


    def test_restores_dropped_graphify_prompt_hook_and_missing_installed_copy(self) -> None:
        self.run_bootstrap()
        settings = self.settings()
        settings["hooks"]["UserPromptSubmit"] = [
            {**g, "hooks": [h for h in g["hooks"] if not h.get("args", [""])[-1].endswith("/graphify_hint.py")]}
            for g in settings["hooks"]["UserPromptSubmit"]]
        (self.claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (self.hooks_dir / "graphify_hint.py").unlink()

        self.run_bootstrap()
        prompt_hooks = [h["args"][-1] for e, _, h in handlers(self.settings())
                        if e == "UserPromptSubmit" and h["type"] == "command"]
        graphify = [a for a in prompt_hooks if a.endswith("/graphify_hint.py")]
        self.assertEqual(len(graphify), 1, f"UserPromptSubmit graphify_hint not restored: {prompt_hooks}")
        self.assertEqual(Path(graphify[0]).read_bytes(), (REPO / "hooks" / "graphify_hint.py").read_bytes())

    def test_upstream_checkout_registrations_are_superseded_by_pinned_copies(self) -> None:
        legacy_audit = {"type": "command", "command": "powershell", "timeout": 15, "args": [
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            "F:/BIMpossible-Workspace/system/scheduled-tasks/audit-watchdog/Show-AuditAlerts.ps1"]}
        capture_start = {"type": "command", "command": "python", "timeout": 5,
                         "args": ["F:/Claude-Tools/evidence-capture/capture_start.py"]}
        capture_end = {"type": "command", "command": "python", "timeout": 25,
                       "args": ["F:/Claude-Tools/evidence-capture/capture_end.py"]}
        existing = {"hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [legacy_audit]}],
            "UserPromptSubmit": [{"hooks": [capture_start]}],
            "Stop": [{"hooks": [capture_end]}],
        }}
        self.claude.mkdir(parents=True)
        (self.claude / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        self.run_bootstrap()
        all_handlers = list(handlers(self.settings()))
        audit = [h for _, _, h in all_handlers if h.get("args", [""])[-1].endswith("Show-AuditAlerts.ps1")]
        self.assertEqual(len(audit), 1, f"audit-alert hook must run once, found {audit}")
        self.assertTrue(audit[0]["args"][-1].lower().startswith(self.hooks_prefix()),
                        "the surviving audit-alert registration is not the pinned copy")
        capture = sorted((e, h["args"][-1].lower().startswith(self.hooks_prefix()), Path(h["args"][-1]).name)
                         for e, _, h in all_handlers if h["type"] == "command" and "capture_" in h["args"][-1])
        self.assertEqual(capture, [("Stop", True, "capture_end.py"), ("UserPromptSubmit", True, "capture_start.py")],
                         "each capture hook must run once, from the pinned unit")
        self.assertNotIn("f:/claude-tools/", json.dumps(self.settings()["hooks"]).lower())
        self.assertEqual((self.hooks_dir / "Show-AuditAlerts.ps1").read_bytes(),
                         (REPO / "hooks" / "Show-AuditAlerts.ps1").read_bytes())


class AuditAlertsHook(BootstrapSandbox):
    """The pinned Show-AuditAlerts.ps1, run from its install against a sandboxed watchdog state."""
    def setUp(self) -> None:
        super().setUp()
        self.run_bootstrap()
        self.state = self.home / ".claude" / "scheduled-tasks" / "_watchdog"
        self.state.mkdir(parents=True)

    def run_hook(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "USERPROFILE": str(self.home)}
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(self.hooks_dir / "Show-AuditAlerts.ps1")],
            env=env, input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

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


class Preflight(BootstrapSandbox):
    """Refusals, run against a committed throwaway copy of the installer."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.root / "repo"
        (self.repo / "hooks").mkdir(parents=True)
        (self.repo / "skills").mkdir()
        for name in ("bootstrap.ps1", "settings.template.json"):
            shutil.copy2(REPO / name, self.repo / name)
        shutil.copytree(REPO / "mcp", self.repo / "mcp")
        for source in HOOK_SOURCES:
            shutil.copy2(source, self.repo / "hooks" / source.name)
        shutil.copytree(REPO / "vendor", self.repo / "vendor", ignore=shutil.ignore_patterns("__pycache__"))
        self.git("init", "-q")
        self.commit()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        *args], check=True, capture_output=True)

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")

    def edit_template(self, old: str, new: str) -> None:
        template = self.repo / "settings.template.json"
        text = template.read_text(encoding="utf-8")
        self.assertIn(old, text)
        template.write_text(text.replace(old, new), encoding="utf-8")
        self.commit()

    def test_uncommitted_hook_source_is_refused_before_any_write(self) -> None:
        with open(self.repo / "hooks" / "decay_reminder.py", "a", encoding="utf-8") as f:
            f.write("# unreviewed edit\n")
        proc = self.run_bootstrap(self.repo, expect_ok=False)
        self.assertIn("uncommitted", proc.stdout + proc.stderr)
        self.assertFalse(self.claude.exists(), "bootstrap wrote to the profile before refusing")

        self.run_bootstrap(self.repo, "-AllowUncommittedHooks")
        installed = (self.hooks_dir / "decay_reminder.py").read_text(encoding="utf-8")
        self.assertTrue(installed.endswith("# unreviewed edit\n"))

    def test_template_hook_missing_from_hooks_is_refused_before_any_write(self) -> None:
        self.edit_template("__CLAUDE_HOOKS_DIR__/decay_reminder.py", "__CLAUDE_HOOKS_DIR__/not_shipped.py")
        proc = self.run_bootstrap(self.repo, expect_ok=False)
        self.assertIn("not_shipped.py", proc.stdout + proc.stderr)
        self.assertFalse(self.claude.exists())

    def test_unresolved_template_token_never_reaches_settings(self) -> None:
        self.edit_template('"theme": "dark"', '"theme": "__UNKNOWN_TOKEN__"')
        proc = self.run_bootstrap(self.repo, expect_ok=False)
        self.assertIn("__UNKNOWN_TOKEN__", proc.stdout + proc.stderr)
        self.assertFalse(self.claude.exists(), "bootstrap wrote to the profile before refusing")

    def test_non_ascii_template_text_round_trips(self) -> None:
        # Windows PowerShell 5.1 decodes a BOM-less file with the ANSI code page unless
        # told otherwise, which turned an em dash into mojibake in settings.json.
        marker = "café — ‐"
        self.edit_template("You decide whether", f"You decide ({marker}) whether")
        mcp = self.repo / "mcp" / "claude_desktop_config.template.json"
        text = mcp.read_text(encoding="utf-8")
        mcp.write_text(text.replace('"command": "cmd",', f'"command": "cmd", "x-note": "{marker}",'),
                       encoding="utf-8")
        self.commit()
        shells = ["powershell.exe"] + (["pwsh"] if shutil.which("pwsh") else [])
        for shell in shells:
            with self.subTest(powershell=shell):
                # Fresh-install outputs each time, so each shell renders both templates.
                shutil.rmtree(self.appdata / "Claude", ignore_errors=True)
                (self.claude / "settings.json").unlink(missing_ok=True)
                self.run_bootstrap(self.repo, powershell=shell)
                prompts = [h["prompt"] for _, _, h in handlers(self.settings()) if h["type"] == "prompt"]
                self.assertEqual(len(prompts), 1)
                self.assertIn(f"You decide ({marker}) whether", prompts[0])
                desktop = json.loads((self.appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
                self.assertEqual(desktop["mcpServers"]["mcp-server-for-revit"]["x-note"], marker)


TEMPLATE = json.loads((REPO / "settings.template.json").read_text(encoding="utf-8"))
SECRET = "sandbox-secret-value-not-real"
HOOK_ARG = "sandbox-hook-arg-not-for-output"


class SettingsPreservation(BootstrapSandbox):
    """Bootstrap updates only the Profile-owned parts of an existing settings.json."""

    def existing(self) -> dict:
        plugin = next(iter(TEMPLATE["enabledPlugins"]))
        return {
            "autoMode": {"allow": ["$defaults", "example exception \u2014 non-ASCII kept"]},
            "env": {"EXAMPLE_TOKEN": SECRET},
            "cleanupPeriodDays": 45,
            "statusLine": {"type": "command", "command": "echo status"},
            "model": "user-chosen-model",
            "permissions": {"allow": ["Bash(gh pr ready:*)"], "deny": ["Read(**/.env)"], "ask": [],
                            "defaultMode": "default", "additionalDirectories": ["F:/Extra-Dir"]},
            "enabledPlugins": {plugin: not TEMPLATE["enabledPlugins"][plugin], "user-only@local": True},
            "hooks": {"Notification": [{"hooks": [{"type": "command", "command": "python",
                                                   "args": ["elsewhere/notify.py", HOOK_ARG]}]}]},
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
        for key in ("autoMode", "env", "cleanupPeriodDays", "statusLine", "model"):
            self.assertEqual(s[key], existing[key], f"{key} was not preserved")
        for key in ("allow", "deny", "ask", "defaultMode"):
            self.assertEqual(s["permissions"][key], existing["permissions"][key], f"permissions.{key}")
        self.assertEqual(s["permissions"]["additionalDirectories"][0], "F:/Extra-Dir")
        for plugin, value in existing["enabledPlugins"].items():
            self.assertEqual(s["enabledPlugins"][plugin], value, "an existing plugin choice was overridden")
        self.assertTrue(set(TEMPLATE["enabledPlugins"]) <= set(s["enabledPlugins"]), "template plugin not seeded")
        self.assertIn(("Notification", None, existing["hooks"]["Notification"][0]["hooks"][0]), list(handlers(s)))
        self.assertTrue(any(h.get("args", [""])[-1].lower().startswith(self.hooks_prefix())
                            for _, _, h in handlers(s)), "template hooks missing")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), original, "backup is not the exact prior file")

        before = (self.claude / "settings.json").read_bytes()
        proc = self.run_bootstrap()
        self.assertEqual((self.claude / "settings.json").read_bytes(), before, "second run changed settings.json")
        self.assertEqual(len(self.backups()), 1, "second run made a backup")
        self.assertIn("settings.json already correct", proc.stdout)

    def test_fresh_install_takes_template_values_and_second_run_is_identical(self) -> None:
        self.run_bootstrap()
        s = self.settings()
        self.assertEqual(s["model"], TEMPLATE["model"])
        self.assertEqual(s["enabledPlugins"], TEMPLATE["enabledPlugins"])
        self.assertNotIn("autoMode", s)
        before = (self.claude / "settings.json").read_bytes()
        self.run_bootstrap()
        self.assertEqual((self.claude / "settings.json").read_bytes(), before)
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
        prefix = self.hooks_dir.as_posix()
        self.assertIn(prefix, text)
        stale = text.replace(prefix, prefix.upper())
        self.assertNotEqual(stale, text, "sandbox path has no letters to change case")
        path.write_text(stale, encoding="utf-8")
        proc = self.run_bootstrap()
        self.assertEqual(path.read_text(encoding="utf-8"), text, "case-only difference was not rewritten")
        self.assertRegex(proc.stdout, r"(?m)^  changed /hooks/")
        self.assertEqual(len(self.backups()), 1, "the case-only rewrite made no backup")
        self.assertEqual(self.backups()[0].read_text(encoding="utf-8"), stale)

    def test_formatting_only_difference_is_left_untouched(self) -> None:
        self.write_existing(self.existing())
        self.run_bootstrap()
        path = self.claude / "settings.json"
        reformatted = json.dumps(json.loads(path.read_text(encoding="utf-8-sig")), indent=2, ensure_ascii=False) + "\n"
        path.write_bytes(reformatted.encode("utf-8"))
        backups = self.backups()
        proc = self.run_bootstrap()
        self.assertEqual(path.read_bytes(), reformatted.encode("utf-8"), "a formatting-only difference was rewritten")
        self.assertEqual(self.backups(), backups, "a formatting-only difference made a backup")
        self.assertIn("settings.json already correct", proc.stdout)

    def test_plan_masks_changed_env_names_and_values(self) -> None:
        # The merge itself never changes env, so drive the plan function directly with a
        # before/after pair that adds, removes and changes env entries.
        before = {"env": {"KEEP_NAME_HIDDEN": "old-secret-value", "REMOVED_NAME_HIDDEN": "gone-secret-value"}, "theme": "dark"}
        after = {"env": {"KEEP_NAME_HIDDEN": "new-secret-value", "ADDED_NAME_HIDDEN": "added-secret-value"}, "theme": "Dark"}
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:BOOTSTRAP_PATH, [ref]$null, [ref]$null)\n"
            "$wanted = 'Test-JsonObject', 'Get-JsonLeafPaths', 'Get-SettingsChanges', 'Write-SettingsPlan'\n"
            "foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {\n"
            "    if ($wanted -contains $f.Name) { . ([scriptblock]::Create($f.Extent.Text)) }\n"
            "}\n"
            "$before = $env:PLAN_BEFORE | ConvertFrom-Json\n"
            "$after = $env:PLAN_AFTER | ConvertFrom-Json\n"
            "Write-SettingsPlan -Changes (Get-SettingsChanges -Before $before -After $after)\n"
        )
        env = {**os.environ, "BOOTSTRAP_PATH": str(REPO / "bootstrap.ps1"),
               "PLAN_BEFORE": json.dumps(before), "PLAN_AFTER": json.dumps(after)}
        proc = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                              env=env, capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("settings.json plan: 1 added, 1 removed, 2 changed", out)
        for line in ("  added /env/*", "  removed /env/*", "  changed /env/*", "  changed /theme"):
            self.assertIn(line, out)
        for leaked in ("KEEP_NAME_HIDDEN", "REMOVED_NAME_HIDDEN", "ADDED_NAME_HIDDEN", "secret-value", "dark", "Dark"):
            self.assertNotIn(leaked, out, f"plan output leaked {leaked!r}")

    def test_directory_entries_deduplicate_across_case_and_slashes(self) -> None:
        detected = Path("F:/BIMpossible")
        if not detected.exists():
            self.skipTest("needs a detectable F:/BIMpossible")
        self.write_existing({"permissions": {"additionalDirectories": ["f:\\bimpossible\\", "F:/Extra-Dir"]}})
        self.run_bootstrap()
        dirs = self.settings()["permissions"]["additionalDirectories"]
        self.assertEqual(dirs[:2], ["f:\\bimpossible\\", "F:/Extra-Dir"], "existing spelling and order kept")
        norm = [d.replace("\\", "/").rstrip("/").lower() for d in dirs]
        self.assertEqual(len(norm), len(set(norm)), f"duplicate directories: {dirs}")

    def test_plan_lists_changed_paths_only_never_values(self) -> None:
        self.write_existing(self.existing())
        proc = self.run_bootstrap()
        out = proc.stdout + proc.stderr
        self.assertIn("settings.json plan:", out)
        self.assertRegex(out, r"(?m)^  (added|changed|removed) /")
        for leaked in (SECRET, HOOK_ARG, "user-chosen-model", "echo status", "EXAMPLE_TOKEN"):
            self.assertNotIn(leaked, out, f"plan output leaked {leaked!r}")


def tree_snapshot(root: Path) -> dict[str, str]:
    """relative path -> sha256 (file), 'dir', or 'link -> target'. Never follows a
    junction or symlink, so the repo's skills tree is never walked."""
    snap: dict[str, str] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            rel = entry.relative_to(root).as_posix()
            if os.path.isjunction(entry) or entry.is_symlink():
                snap[rel] = f"link -> {os.readlink(entry)}"
            elif entry.is_dir():
                snap[rel] = "dir"
                stack.append(entry)
            else:
                snap[rel] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return snap


class RefusalLeavesProfileUntouched(BootstrapSandbox):
    """An existing settings.json bootstrap cannot safely merge is refused before ANY
    write: the CLAUDE.md stub, the skills directory, every installed hook and the
    desktop config stay byte-identical, and no backup is made (L1, 2026-10-05)."""

    BAD_SETTINGS = {
        "malformed": "{not json",
        "empty": "",
        "whitespace-only": "  \n\t\n",
        "null": "null",
        "array": '["an", "array"]',
        "wrong-case Hooks": '{"Hooks": {}}',
        "wrong-case Permissions": '{"Permissions": {"allow": []}}',
        "case-variant hooks collision": '{"hooks": {}, "Hooks": {}}',
        "hooks not an object": '{"hooks": []}',
        "permissions not an object": '{"permissions": "not-an-object"}',
    }

    def plant_profile(self, settings: str, desktop: str = '{"other": 1}') -> None:
        # Every artefact a successful run would rewrite: a foreign CLAUDE.md, a real
        # skills directory (would be moved to a backup), a stale pinned hook (would be
        # replaced), and a desktop config whose mcpServers would be inserted.
        self.hooks_dir.mkdir(parents=True)
        (self.claude / "CLAUDE.md").write_bytes(b"# hand-written, not the stub\n")
        (self.claude / "skills").mkdir()
        (self.claude / "skills" / "mine.md").write_bytes(b"user skill\n")
        (self.hooks_dir / "decay_reminder.py").write_bytes(b"# stale installed copy\n")
        (self.claude / "settings.json").write_bytes(settings.encode("utf-8"))
        (self.appdata / "Claude").mkdir()
        (self.appdata / "Claude" / "claude_desktop_config.json").write_bytes(desktop.encode("utf-8"))

    def snapshot(self) -> dict:
        # bootstrap's two write roots. The PowerShell host itself may create
        # AppData folders under the sandboxed USERPROFILE; that is not bootstrap.
        return {"claude": tree_snapshot(self.claude), "desktop": tree_snapshot(self.appdata / "Claude")}

    def assert_refused_untouched(self, expect_in_output: str) -> None:
        before = self.snapshot()
        proc = self.run_bootstrap(expect_ok=False)
        out = proc.stdout + proc.stderr
        self.assertIn(expect_in_output, out)
        self.assertIn("refusing", out)
        after = self.snapshot()
        self.assertEqual(after, before, f"a refused run changed the profile:\n{proc.stdout}")
        self.assertNotIn("pre-profile-backup", json.dumps(after))

    def test_unusable_settings_is_refused_before_any_write(self) -> None:
        for label, raw in self.BAD_SETTINGS.items():
            with self.subTest(settings=label):
                remove_sandbox(self.root)
                self.home.mkdir(parents=True)
                self.appdata.mkdir()
                self.plant_profile(raw)
                self.assert_refused_untouched("settings.json")

    def test_wrong_case_key_names_the_expected_key(self) -> None:
        self.plant_profile('{"Hooks": {}}')
        proc = self.run_bootstrap(expect_ok=False)
        self.assertIn("'Hooks' key; Claude Code only reads 'hooks'", proc.stdout + proc.stderr)

    def test_unusable_desktop_config_is_refused_before_any_write(self) -> None:
        self.plant_profile("{}", desktop="{not json")
        self.assert_refused_untouched("claude_desktop_config.json")


if __name__ == "__main__":
    unittest.main()
