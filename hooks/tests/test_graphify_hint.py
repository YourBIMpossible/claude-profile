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


def run_hook(payload, env_extra=None) -> tuple[int, str]:
    env = dict(os.environ)
    env.pop("GRAPHIFY_HINT", None)
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
        # 2026-09-29 acceptance run: a session ran `cd F:\...` in Git Bash, the
        # backslashes were eaten and the first query failed; the retry worked.
        _, out = run_hook({"hook_event_name": "SessionStart", "cwd": str(self.web)})
        cmd_line = next(l.strip() for l in self.ctx(out, "SessionStart").splitlines()
                        if l.strip().startswith("python "))
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
        rc, out = run_hook({"hook_event_name": "UserPromptSubmit", "cwd": str(self.web)})
        self.assertEqual((rc, out), (0, ""))

    def test_malformed_stdin_never_fails(self):
        for bad in ("not json", "", "[1,2]"):
            rc, out = run_hook(bad)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
