"""Tests for graphify_hint.py: resolver parity with the skill copy, and hook I/O.

The hook is a pinned copy that may not import skill code, so its resolver is
duplicated; ResolverParity fails if the two copies ever disagree.

Run: python hooks/tests/test_graphify_hint.py  (from the repo root or anywhere)
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "graphify_hint.py"
SKILL_SCRIPTS = REPO / "skills" / "graphify" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import resolve_graph as rg  # noqa: E402
from test_resolve_graph import make_graph, make_repo, make_worktree  # noqa: E402

def git_bash() -> str | None:
    """Git Bash (the shell sessions use), never WSL's System32 bash."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    git = shutil.which("git")
    if git:
        for cand in (Path(git).parents[1] / "bin" / "bash.exe", Path(git).parents[1] / "usr" / "bin" / "bash.exe"):
            if cand.exists():
                return str(cand)
    return None


_spec = importlib.util.spec_from_file_location("graphify_hint", HOOK)
hint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hint)


def build_tree(tmp: Path) -> list[Path]:
    """Every resolver branch in one tree; returns the cwds to compare."""
    web = make_repo(tmp / "web")
    make_graph(web / "backend")
    (web / "frontend" / "src").mkdir(parents=True)
    web_wt = make_worktree(web, web / ".claude" / "worktrees" / "lane", "lane")
    (web_wt / "backend" / "app").mkdir(parents=True)

    addins = make_repo(tmp / "addins")
    make_graph(addins)
    (addins / "src").mkdir()
    addins_wt = make_worktree(addins, tmp / "outside" / "addins-lane", "addins-lane")

    dash = make_repo(tmp / "dash")
    foreign = make_worktree(dash, web / ".claude" / "worktrees" / "dash-lane", "dash-lane")
    dash_wt = make_worktree(dash, dash / ".claude" / "worktrees" / "d", "d")

    loose = tmp / "loose"
    make_graph(loose)
    (loose / "a" / "b").mkdir(parents=True)
    far = tmp.joinpath(*["x"] * (rg.MAX_NON_GIT_LEVELS + 2))
    far.mkdir(parents=True)

    return [web, web / "frontend" / "src", web / "backend", web_wt, web_wt / "backend" / "app",
            addins, addins / "src", addins_wt, dash, foreign, dash_wt, loose / "a" / "b", far]


class ResolverParity(unittest.TestCase):
    def test_hook_copy_matches_skill_resolver(self):
        with tempfile.TemporaryDirectory() as td:
            for cwd in build_tree(Path(td).resolve()):
                skill = rg.resolve(cwd)
                expected = dataclasses.asdict(skill) if skill else None
                self.assertEqual(hint.resolve(cwd), expected, f"diverged at {cwd}")

    def test_constants_match(self):
        self.assertEqual(hint.GRAPH_SUBDIRS, rg.GRAPH_SUBDIRS)
        self.assertEqual(hint.MAX_NON_GIT_LEVELS, rg.MAX_NON_GIT_LEVELS)
        self.assertEqual(hint.GRAPH_REL, rg.GRAPH_REL)

    def test_wrapper_path_points_at_this_repos_wrapper(self):
        self.assertEqual(Path(hint.WRAPPER).name, "query_reranked.py")
        self.assertTrue((SKILL_SCRIPTS / "query_reranked.py").is_file())


_LOGS = tempfile.TemporaryDirectory(prefix="graphify-hint-logs-")


def run_hook(payload, env_extra=None, log_dir=None) -> tuple[int, str]:
    env = dict(os.environ)
    env.pop("GRAPHIFY_HINT", None)
    # Never write the diagnostic log into the real profile from tests.
    env[hint.LOG_DIR_ENV] = str(log_dir or _LOGS.name)
    env.update(env_extra or {})
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run([sys.executable, str(HOOK)], input=stdin.encode("utf-8"),
                          capture_output=True, env=env, timeout=15)
    return proc.returncode, proc.stdout.decode("utf-8")


class HookIO(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tmp = Path(self._td.name).resolve()
        self.web = make_repo(tmp / "web")
        self.graph = make_graph(self.web / "backend")
        self.wt = make_worktree(self.web, self.web / ".claude" / "worktrees" / "lane", "lane")
        self.bare = make_repo(tmp / "dash")

    def ctx(self, out: str, event: str) -> str:
        data = json.loads(out)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], event)
        return data["hookSpecificOutput"]["additionalContext"]

    def test_session_start_injects_graph_command_and_delegation_rule(self):
        rc, out = run_hook({"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.web)})
        self.assertEqual(rc, 0)
        text = self.ctx(out, "SessionStart")
        self.assertIn(str(self.graph), text)
        self.assertIn(hint.query_command(str(self.graph)), text)
        self.assertIn("subagent", text)

    def test_worktree_hint_maps_paths_to_local_checkout(self):
        _, out = run_hook({"hook_event_name": "SessionStart", "cwd": str(self.wt)})
        text = self.ctx(out, "SessionStart")
        self.assertIn(str(self.graph), text)
        self.assertIn(str(self.wt / "backend"), text)

    def test_subagent_start_is_self_contained(self):
        _, out = run_hook({"hook_event_name": "SubagentStart", "agent_type": "Explore", "cwd": str(self.wt)})
        text = self.ctx(out, "SubagentStart")
        self.assertIn(str(self.graph), text)
        self.assertIn(hint.query_command(str(self.graph)), text)
        self.assertNotIn("delegate", text)

    def test_command_is_shell_neutral_and_needs_no_cd(self):
        # 2026-09-29 acceptance run: a session ran `cd <drive>:\...` in Git Bash, the
        # backslashes were eaten and the first query failed; the retry worked.
        _, out = run_hook({"hook_event_name": "SessionStart", "cwd": str(self.web)})
        cmd_line = next(l.strip() for l in self.ctx(out, "SessionStart").splitlines()
                        if l.strip().startswith(hint.PYTHON_CMD + " "))
        self.assertNotIn("\\", cmd_line)
        self.assertNotIn("cd ", cmd_line)
        self.assertIn("no `cd`", self.ctx(out, "SessionStart"))

    @unittest.skipUnless(git_bash(), "Git Bash not found")
    def test_command_runs_from_unrelated_cwd_in_bash(self):
        stub = Path(self._td.name).resolve() / "stub wrapper.py"
        stub.write_text("import sys, json; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
        cmd = hint.query_command(str(self.graph)).replace(Path(hint.WRAPPER).as_posix(), stub.as_posix())
        res = subprocess.run([git_bash(), "-c", cmd], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        argv = json.loads(res.stdout)
        self.assertEqual(Path(argv[argv.index("--graph") + 1]), self.graph)

    def test_no_graph_emits_nothing(self):
        for event in ("SessionStart", "SubagentStart"):
            rc, out = run_hook({"hook_event_name": event, "agent_type": "Explore", "cwd": str(self.bare)})
            self.assertEqual((rc, out), (0, ""))

    def test_shell_less_subagent_emits_nothing(self):
        rc, out = run_hook({"hook_event_name": "SubagentStart", "agent_type": "claude-code-guide",
                            "cwd": str(self.web)})
        self.assertEqual((rc, out), (0, ""))

    def test_opt_out(self):
        rc, out = run_hook({"hook_event_name": "SessionStart", "cwd": str(self.web)}, {"GRAPHIFY_HINT": "0"})
        self.assertEqual((rc, out), (0, ""))

    def test_other_events_ignored(self):
        for event in ("PreToolUse", "Stop", "SessionEnd"):
            rc, out = run_hook({"hook_event_name": event, "cwd": str(self.web)})
            self.assertEqual((rc, out), (0, ""), event)

    def test_malformed_stdin_never_fails(self):
        for bad in ("not json", "", "[1,2]"):
            rc, out = run_hook(bad)
            self.assertEqual(rc, 0)


def write_agent(agents_dir: Path, stem: str, frontmatter: str) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{stem}.md").write_text(f"---\n{frontmatter}---\n\nSystem prompt.\n", encoding="utf-8")


class AgentDefinitions(unittest.TestCase):
    """Custom subagents are judged by their definition's tools (2026-10-26:
    backend-endpoint-reviewer, tools Read/Grep/Glob, was handed the hint)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tmp = Path(self._td.name).resolve()
        self.web = make_repo(tmp / "web")
        make_graph(self.web / "backend")
        self.wt = make_worktree(self.web, self.web / ".claude" / "worktrees" / "lane", "lane")
        self.home = tmp / "home"
        self.home.mkdir()
        # Path.home() reads USERPROFILE on Windows and HOME elsewhere; never the real profile.
        self.env = {"USERPROFILE": str(self.home), "HOME": str(self.home)}
        self.log = tmp / "logs"

    def start(self, agent_type: str, cwd: Path) -> str:
        rc, out = run_hook({"hook_event_name": "SubagentStart", "agent_type": agent_type, "cwd": str(cwd)},
                           self.env, self.log)
        self.assertEqual(rc, 0)
        return out

    def last_log(self) -> dict:
        return json.loads((self.log / hint.LOG_NAME).read_text(encoding="utf-8").splitlines()[-1])

    def test_project_agent_without_shell_gets_nothing(self):
        write_agent(self.web / ".claude" / "agents", "reviewer",
                    "name: backend-endpoint-reviewer\ndescription: >\n  Reviews endpoints.\n"
                    "  tools: Bash\ntools: Read, Grep, Glob\n")
        self.assertEqual(self.start("backend-endpoint-reviewer", self.web / "backend"), "")
        self.assertEqual(self.last_log()["skipped"], "shell_less_agent")

    def test_worktree_copy_of_the_definition_is_found(self):
        write_agent(self.wt / ".claude" / "agents", "reviewer", "name: reviewer\ntools: Read, Grep\n")
        self.assertEqual(self.start("reviewer", self.wt), "")

    def test_user_agent_without_shell_gets_nothing(self):
        write_agent(self.home / ".claude" / "agents", "docs", "name: docs\ntools: [Read, WebFetch]\n")
        self.assertEqual(self.start("docs", self.web), "")

    def test_agents_with_a_shell_get_the_hint(self):
        cases = {
            "bash-csv": "tools: Read, Bash\n",
            "pattern": "tools: Read, Bash(git log:*, git diff:*)\n",
            "ps-list": "tools:\n  - Read\n  - PowerShell\n",
            "all-tools": "description: no tools field means every tool\n",
            "star": "tools: '*'\n",
            "partly-denied": "disallowedTools: Bash(rm:*)\n",
        }
        for name, body in cases.items():
            write_agent(self.web / ".claude" / "agents", name, f"name: {name}\n{body}")
        for name in cases:
            self.assertIn(hint.HINT_TAG, self.start(name, self.web), name)

    def test_disallowed_shell_gets_nothing(self):
        write_agent(self.web / ".claude" / "agents", "nosh",
                    "name: nosh\ndisallowedTools: Bash, PowerShell\n")
        self.assertEqual(self.start("nosh", self.web), "")

    def test_unknown_or_plugin_agent_keeps_the_hint(self):
        for agent in ("Explore", "general-purpose", "pr-review-toolkit:code-reviewer", "no-such-agent"):
            self.assertIn(hint.HINT_TAG, self.start(agent, self.web), agent)

    def test_file_stem_is_the_name_when_frontmatter_has_none(self):
        write_agent(self.web / ".claude" / "agents", "stemmed", "tools: Read\n")
        self.assertEqual(self.start("stemmed", self.web), "")

    def test_nearest_definition_wins(self):
        write_agent(self.web / ".claude" / "agents", "dup", "name: dup\ntools: Read, Bash\n")
        write_agent(self.home / ".claude" / "agents", "dup", "name: dup\ntools: Read\n")
        self.assertIn(hint.HINT_TAG, self.start("dup", self.web))

    def test_no_graph_never_reads_agent_files(self):
        bare = make_repo(Path(self._td.name).resolve() / "bare")
        write_agent(bare / ".claude" / "agents", "x", "name: x\ntools: Read\n")
        self.assertEqual(self.start("x", bare), "")
        self.assertEqual(self.last_log()["skipped"], "no_graph")

    def test_frontmatter_parser(self):
        fm = hint.parse_frontmatter("---\nname: \"a\"\ntools: [Read, 'Bash(git:*, ls)']\n"
                                    "disallowedTools:\n  - Edit\n---\nbody: not frontmatter\n")
        self.assertEqual(fm, {"name": "a", "tools": ["Read", "Bash(git:*, ls)"], "disallowedTools": ["Edit"]})
        self.assertIsNone(hint.parse_frontmatter("no frontmatter"))
        self.assertIsNone(hint.parse_frontmatter("---\nname: unterminated\n"))


# ---- transcript fixtures: the record shapes Claude Code writes ----
def boundary() -> dict:
    return {"parentUuid": None, "logicalParentUuid": "444dc8f7", "isSidechain": False,
            "type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"}


def hook_ctx(text: str, hook: str = "SessionStart:resume", sidechain: bool = False) -> dict:
    return {"isSidechain": sidechain, "type": "attachment",
            "attachment": {"type": "hook_additional_context", "content": [text], "hookName": hook}}


def user(text: str) -> dict:
    return {"isSidechain": False, "type": "user", "message": {"role": "user", "content": text}}


def tool_result(text: str) -> dict:
    return {"isSidechain": False, "type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


# What 07542a44 inherited: a parent compact_boundary, then the parent's turns up
# to a Stop, with other hooks' context but no graphify hint, and no SessionStart.
FORK_PREFIX = [boundary(), user("summary of earlier work"),
               hook_ctx("<context_brief packet_id=\"ep_1\">\nScope confidence: low\n</context_brief>",
                        "UserPromptSubmit"),
               {"isSidechain": False, "type": "assistant", "message": {"content": "done"}},
               {"isSidechain": False, "type": "system", "subtype": "stop_hook_summary"}]


class Transcript:
    def __init__(self, path: Path, records: list[dict]):
        self.path = path
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    def append(self, *records: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in records)


class PromptFallback(unittest.TestCase):
    """UserPromptSubmit injects the hint only when the live context lacks it."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()
        self.web = make_repo(self.tmp / "web")
        self.graph = make_graph(self.web / "backend")
        self.wt = make_worktree(self.web, self.web / ".claude" / "worktrees" / "lane", "lane")
        self.addins = make_repo(self.tmp / "addins")
        self.addins_graph = make_graph(self.addins)
        self.bare = make_repo(self.tmp / "dash")
        self.logs = self.tmp / "logs"

    def transcript(self, records: list[dict], name: str = "t.jsonl") -> Transcript:
        return Transcript(self.tmp / name, records)

    def prompt(self, t: Transcript | str | None, cwd: Path, **extra) -> str:
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "cwd": str(cwd),
                   "prompt": "hello", **extra}
        if t is not None:
            payload["transcript_path"] = str(t.path if isinstance(t, Transcript) else t)
        rc, out = run_hook(payload, log_dir=self.logs)
        self.assertEqual(rc, 0)
        return out

    def deliver(self, t: Transcript, out: str, hook: str) -> str:
        """Record the hook's output the way the harness does; return the injected text."""
        data = json.loads(out)["hookSpecificOutput"]
        t.append(hook_ctx(data["additionalContext"], hook))
        return data["additionalContext"]

    def log(self) -> list[dict]:
        path = self.logs / hint.LOG_NAME
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_fork_without_session_start_injects_exactly_once(self):
        # Regression for 07542a44: forked prefix, no SessionStart of any kind.
        t = self.transcript(FORK_PREFIX)
        injected = []
        for _ in range(4):
            out = self.prompt(t, self.web)
            if out:
                self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
                injected.append(self.deliver(t, out, "UserPromptSubmit"))
            t.append(user("next prompt"), {"isSidechain": False, "type": "assistant", "message": {}})
        self.assertEqual(len(injected), 1)
        self.assertIn(str(self.graph), injected[0])
        self.assertIn(hint.query_command(str(self.graph)), injected[0])
        self.assertIn("subagent", injected[0], "fallback must carry the parent-session (delegation) hint")
        self.assertEqual([r["emitted"] for r in self.log()], [True, False, False, False])
        self.assertEqual([r["suppressed_prior_hint"] for r in self.log()], [False, True, True, True])

    def test_session_start_then_prompts_never_duplicate(self):
        t = self.transcript([user("hi")])
        rc, out = run_hook({"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.web),
                            "transcript_path": str(t.path)}, log_dir=self.logs)
        self.deliver(t, out, "SessionStart:startup")
        for _ in range(3):
            self.assertEqual(self.prompt(t, self.web), "")
            t.append(user("again"))

    def test_hint_only_before_last_compaction_is_reinjected(self):
        # Gap B: a compaction whose SessionStart:compact never fired.
        t = self.transcript([hook_ctx(hint.build_hint(hint.resolve(self.web), subagent=False)),
                             user("work"), boundary(), user("after compaction")])
        self.assertIn(hint.HINT_TAG, self.prompt(t, self.web))

    def test_hint_after_compaction_counts(self):
        t = self.transcript([boundary(), hook_ctx(hint.build_hint(hint.resolve(self.web), subagent=False),
                                                  "SessionStart:compact"), user("go")])
        self.assertEqual(self.prompt(t, self.web), "")

    def test_undelivered_mentions_do_not_count(self):
        tag = hint.HINT_TAG
        for rec in (user(f"pasted {tag} block"), tool_result(f"source of graphify_hint.py: {tag}"),
                    hook_ctx(hint.build_hint(hint.resolve(self.web), subagent=True), "SubagentStart",
                             sidechain=True),
                    {"isSidechain": False, "attachment": {"type": "file", "content": [tag]}}):
            t = self.transcript([boundary(), rec])
            self.assertIn(tag, self.prompt(t, self.web), rec)

    def test_missing_or_unreadable_transcript_injects(self):
        for path in (None, self.tmp / "absent.jsonl", self.tmp):
            self.assertIn(hint.HINT_TAG, self.prompt(path, self.web), path)
        errors = [r["error"] for r in self.log()]
        self.assertIsNone(errors[0])
        self.assertTrue(all(e and e.startswith(("FileNotFoundError", "PermissionError", "IsADirectoryError"))
                            for e in errors[1:]), errors)

    def test_repo_root_worktree_and_addins_root(self):
        for cwd, graph, local in ((self.web, self.graph, self.web / "backend"),
                                  (self.wt, self.graph, self.wt / "backend"),
                                  (self.addins, self.addins_graph, self.addins)):
            t = self.transcript(FORK_PREFIX, name=f"{cwd.name}.jsonl")
            out = self.prompt(t, cwd)
            text = self.deliver(t, out, "UserPromptSubmit")
            self.assertIn(f"graph: {graph}", text, cwd)
            self.assertIn(str(local), text, cwd)
            self.assertEqual(self.prompt(t, cwd), "", f"duplicate at {cwd}")

    def test_no_graph_and_shell_less_subagent_never_read_the_transcript(self):
        reads = []
        real = hint.hint_in_live_context
        hint.hint_in_live_context = lambda p: reads.append(p) or False
        self.addCleanup(setattr, hint, "hint_in_live_context", real)
        t = self.transcript(FORK_PREFIX)
        for payload in ({"hook_event_name": "UserPromptSubmit", "cwd": str(self.bare)},
                        {"hook_event_name": "SubagentStart", "agent_type": "claude-code-guide",
                         "cwd": str(self.web)},
                        {"hook_event_name": "SubagentStart", "agent_type": "Explore", "cwd": str(self.web)},
                        {"hook_event_name": "SessionStart", "source": "resume", "cwd": str(self.web)}):
            rec = {}
            hint.handle({**payload, "transcript_path": str(t.path)}, rec)
            self.assertFalse(rec.get("transcript_read"), payload)
        self.assertEqual(reads, [])
        # And end to end: the no-graph prompt and shell-less subagent are silent.
        self.assertEqual(self.prompt(t, self.bare), "")
        rc, out = run_hook({"hook_event_name": "SubagentStart", "agent_type": "claude-code-guide",
                            "cwd": str(self.web), "transcript_path": str(t.path)}, log_dir=self.logs)
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual([(r["skipped"], r["transcript_read"]) for r in self.log()],
                         [("no_graph", False), ("shell_less_agent", False)])

    def test_scan_cost_is_bounded_by_the_live_context(self):
        filler = json.dumps(tool_result("x" * 4000)) + "\n"
        path = self.tmp / "big.jsonl"
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(hook_ctx(hint.HINT_TAG)) + "\n")
            f.write(filler * (24 * 1024 * 1024 // len(filler)))
            f.write(json.dumps(boundary()) + "\n")
            f.write(filler * 50)
        self.assertGreater(path.stat().st_size, 20 * 1024 * 1024)
        start = time.perf_counter()
        self.assertFalse(hint.hint_in_live_context(str(path)))
        self.assertLess(time.perf_counter() - start, 1.0)


class DiagnosticLog(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()
        self.web = make_repo(self.tmp / "web")
        make_graph(self.web / "backend")
        self.logs = self.tmp / "logs"

    def records(self) -> list[dict]:
        return [json.loads(l) for l in (self.logs / hint.LOG_NAME).read_text(encoding="utf-8").splitlines()]

    def test_one_record_per_invocation_with_outcome_fields(self):
        run_hook({"hook_event_name": "SessionStart", "source": "startup", "session_id": "abc-123",
                  "cwd": str(self.web)}, log_dir=self.logs)
        run_hook({"hook_event_name": "SessionStart", "cwd": str(self.web)}, {"GRAPHIFY_HINT": "0"},
                 log_dir=self.logs)
        run_hook("not json", log_dir=self.logs)
        run_hook({"hook_event_name": "Stop", "session_id": "../../etc", "cwd": str(self.web)}, log_dir=self.logs)
        first, opt_out, bad, stop = self.records()
        self.assertEqual(set(first), {"ts", "event", "source", "agent_type", "session_id", "cwd", "resolved",
                                      "graph", "transcript_read", "suppressed_prior_hint", "emitted",
                                      "skipped", "error"})
        self.assertEqual((first["event"], first["source"], first["session_id"], first["cwd"]),
                         ("SessionStart", "startup", "abc-123", str(self.web)))
        self.assertTrue(first["resolved"] and first["emitted"])
        self.assertIsNone(first["error"])
        self.assertEqual((opt_out["skipped"], opt_out["emitted"]), ("opt_out", False))
        self.assertEqual(bad["error"], "JSONDecodeError at char 0")
        self.assertEqual(stop["skipped"], "event_ignored")
        self.assertIsNone(stop["session_id"], "unsafe session id must not be logged")

    def test_never_records_prompt_or_transcript_content(self):
        marker = "PROMPT-MARKER-7f3a"
        t = Transcript(self.tmp / "t.jsonl", [boundary(), user(f"transcript text {marker}-T")])
        run_hook({"hook_event_name": "UserPromptSubmit", "cwd": str(self.web), "prompt": marker,
                  "transcript_path": str(t.path)}, log_dir=self.logs)
        text = (self.logs / hint.LOG_NAME).read_text(encoding="utf-8")
        self.assertNotIn(marker, text)
        self.assertNotIn(str(t.path), text)
        self.assertTrue(self.records()[0]["emitted"])

    def test_logging_failure_never_blocks_the_hint(self):
        blocker = self.tmp / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        for log_dir in (blocker, blocker / "sub"):
            rc, out = run_hook({"hook_event_name": "UserPromptSubmit", "cwd": str(self.web)}, log_dir=log_dir)
            self.assertEqual(rc, 0)
            self.assertIn(hint.HINT_TAG, json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_size_is_bounded_by_one_rotation(self):
        self.logs.mkdir()
        log = self.logs / hint.LOG_NAME
        for generation in range(3):
            log.write_bytes(b"{}\n" * (hint.LOG_MAX_BYTES // 3 + 1))
            run_hook({"hook_event_name": "Stop", "cwd": str(self.web)}, log_dir=self.logs)
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), [hint.LOG_NAME, hint.LOG_NAME + ".1"])
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)
        self.assertLessEqual((self.logs / (hint.LOG_NAME + ".1")).stat().st_size, hint.LOG_MAX_BYTES + 4)


if __name__ == "__main__":
    unittest.main()
