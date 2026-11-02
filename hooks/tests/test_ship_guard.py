"""Tests for hooks/ship_guard.py: red PRs do not merge and ungated code does not push from a Claude session.

Merges are checked against a fake `gh` (the runner is injected); pushes against real git repositories in a temporary
folder, so the gate-on check is the real one. The last tests run the script as Claude Code does (stdin JSON, exit code).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))
import ship_guard  # noqa: E402

SCRIPT = HOOKS / "ship_guard.py"
GREEN = [{"__typename": "CheckRun", "name": "tests (ubuntu)", "status": "COMPLETED", "conclusion": "SUCCESS"},
         {"__typename": "CheckRun", "name": "tests (windows)", "status": "COMPLETED", "conclusion": "SKIPPED"},
         {"__typename": "StatusContext", "context": "ci/legacy", "state": "SUCCESS"}]
RED = GREEN[:1] + [{"__typename": "CheckRun", "name": "tests (windows)", "status": "COMPLETED", "conclusion": "FAILURE"}]
PENDING = GREEN[:1] + [{"__typename": "CheckRun", "name": "tests (windows)", "status": "IN_PROGRESS", "conclusion": ""}]
HEAD = "abc123def4567890"


class FakeGh:
    """A runner whose `gh pr view` answers with a fixed rollup; git calls go to the real git."""

    def __init__(self, rollup=None, returncode: int = 0, stdout: str | None = None):
        self.rollup, self.returncode, self.stdout, self.calls, self.cwds = rollup, returncode, stdout, [], []

    def __call__(self, argv, cwd):
        if argv[0] == "git":
            return ship_guard._run(argv, cwd)
        self.calls.append(argv)
        self.cwds.append(cwd)
        out = self.stdout if self.stdout is not None else json.dumps(
            {"number": 7, "url": "https://github.com/o/r/pull/7", "headRefOid": HEAD, "statusCheckRollup": self.rollup})
        return subprocess.CompletedProcess(argv, self.returncode, out, "boom" if self.returncode else "")


def bash(command: str, cwd: str | None = None) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


def pwsh(command: str, cwd: str | None = None) -> dict:
    return {"tool_name": "PowerShell", "tool_input": {"command": command}, "cwd": cwd}


def git(*args, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def make_repo(path: Path, gated: bool = True) -> Path:
    path.mkdir(parents=True)
    git("init", "-q", cwd=path)
    hooks = path / ".githooks"
    hooks.mkdir()
    marker = f"# {ship_guard.GATE_MARKER}\n" if gated else ""
    (hooks / "pre-push").write_text(f"#!/bin/sh\n{marker}exit 0\n", encoding="utf-8", newline="\n")
    return path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "gated")


def decide(payload: dict, run=None, environ=None):
    return ship_guard.decide(payload, run=run or FakeGh(GREEN), environ=environ or {})


# --- merges ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rollup", [RED, PENDING, []], ids=["red", "pending", "no-checks"])
def test_gh_pr_merge_is_refused_unless_every_check_is_green(rollup):
    reason = decide(bash("gh pr merge 7 --repo o/r --squash"), run=FakeGh(rollup))
    assert reason and "not green" in reason


def test_gh_pr_merge_on_a_green_pinned_head_is_allowed():
    fake = FakeGh(GREEN)
    assert decide(bash(f"gh pr merge 7 --repo o/r --squash --delete-branch --match-head-commit {HEAD}"), run=fake) is None
    assert fake.calls[0][:5] == ["gh", "pr", "view", "7", "--repo"]


def test_a_merge_must_pin_the_exact_head_it_was_checked_at():
    reason = decide(bash("gh pr merge 7 --repo o/r --squash"))
    assert reason and f"--match-head-commit {HEAD}" in reason
    assert "the head the checks ran on" in decide(bash("gh pr merge 7 --repo o/r --match-head-commit 1234567abc"))
    assert decide(bash(f"gh api -X PUT repos/o/r/pulls/7/merge -f sha={HEAD}")) is None
    assert "does not pin it" in decide(bash("gh api -X PUT repos/o/r/pulls/7/merge"))


def test_a_merge_runs_its_check_in_the_directory_the_merge_runs_in(tmp_path):
    lane = tmp_path / "lane"
    lane.mkdir()
    fake = FakeGh(GREEN)
    assert decide(bash(f'cd "{lane.as_posix()}" && gh pr merge 7 --match-head-commit {HEAD}', cwd=str(tmp_path)),
                  run=fake) is None
    assert os.path.normcase(fake.cwds[0]) == os.path.normcase(os.path.normpath(str(lane)))
    assert "--repo OWNER/REPO" in decide(bash(f"cd $X && gh pr merge 7 --match-head-commit {HEAD}"))
    assert decide(bash(f"cd $X && gh pr merge 7 --repo o/r --match-head-commit {HEAD}")) is None
    assert "GH_REPO" in decide(bash(f"GH_REPO=o/r gh pr merge 7 --match-head-commit {HEAD}"))


@pytest.mark.parametrize("command", [
    "gh pr merge --auto --squash",
    "cd x && gh pr merge https://github.com/o/r/pull/7 --admin",
    "gh api -X PUT repos/o/r/pulls/7/merge",
    "curl -X PUT https://api.github.com/repos/o/r/pulls/7/merge",
])
def test_every_merge_route_is_checked(command):
    assert "not green" in decide(bash(command), run=FakeGh(RED))


def test_graphql_merge_is_always_refused():
    assert "GraphQL" in decide(bash("gh api graphql -f query='mutation { mergePullRequest(input: {}) { x } }'"))


def test_turning_auto_merge_off_is_allowed():
    fake = FakeGh(RED)
    assert decide(bash("gh pr merge 7 --disable-auto"), run=fake) is None
    assert fake.calls == []


def test_a_gh_failure_refuses_the_merge():
    assert "could not read the checks" in decide(bash("gh pr merge 7"), run=FakeGh(returncode=1))
    assert "unreadable" in decide(bash("gh pr merge 7"), run=FakeGh(stdout="not json"))


def test_mcp_merge_and_auto_merge():
    merge = {"tool_name": "mcp__github__merge_pull_request", "tool_input": {"owner": "o", "repo": "r", "pullNumber": 7}}
    assert "not green" in decide(merge, run=FakeGh(RED))
    assert "does not pin it" in decide(merge, run=FakeGh(GREEN))
    merge["tool_input"]["sha"] = HEAD
    assert decide(merge, run=FakeGh(GREEN)) is None
    assert "owner, repo and pull number" in decide({"tool_name": "mcp__github__merge_pull_request", "tool_input": {}})
    assert "auto-merge" in decide({"tool_name": "mcp__ccd_pr__set_auto_merge", "tool_input": {"enabled": True}})
    assert decide({"tool_name": "mcp__ccd_pr__set_auto_merge", "tool_input": {"enabled": False}}) is None


def test_direct_github_file_writes_are_refused():
    for name in ("mcp__github__push_files", "mcp__github__create_or_update_file"):
        assert "skips every local gate" in decide({"tool_name": name, "tool_input": {}})


def test_unrelated_calls_are_allowed_untouched():
    fake = FakeGh(RED)
    assert decide(bash("ls -la && echo merged"), run=fake) is None
    assert decide({"tool_name": "Write", "tool_input": {"file_path": "x", "content": "push"}}, run=fake) is None
    assert decide({"tool_name": "mcp__other__thing", "tool_input": {}}, run=fake) is None
    assert decide(bash('git commit -m "push the gate; merge later"'), run=fake) is None
    assert fake.calls == []


# --- pushes ----------------------------------------------------------------------------------------------------------

def test_push_from_a_gated_repo_with_the_gate_off_is_refused(repo):
    reason = decide(bash("git push origin HEAD", cwd=str(repo)))
    assert reason and "core.hooksPath is not set" in reason and "tools/git_gate.py" in reason
    git("config", "core.hooksPath", "/elsewhere", cwd=repo)
    assert "core.hooksPath is '/elsewhere'" in decide(bash("git push", cwd=str(repo)))


def test_push_with_the_gate_on_is_allowed(repo):
    git("config", "core.hooksPath", ".githooks", cwd=repo)
    assert decide(bash("git push -u origin feature", cwd=str(repo))) is None
    sub = repo / "src"
    sub.mkdir()
    assert decide(pwsh("git push", cwd=str(sub))) is None


def test_push_from_a_linked_worktree_follows_the_shared_config(repo):
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    tree = repo / ".claude" / "worktrees" / "lane"
    git("worktree", "add", "-q", str(tree), "-b", "lane", cwd=repo)
    assert "is not set" in decide(bash("git push", cwd=str(tree)))
    git("config", "core.hooksPath", ".githooks", cwd=repo)
    assert decide(bash("git push", cwd=str(tree))) is None


def test_push_from_a_repo_without_the_gate_is_allowed(tmp_path):
    plain = make_repo(tmp_path / "plain", gated=False)
    assert decide(bash("git push", cwd=str(plain))) is None
    assert decide(bash("git push", cwd=str(tmp_path))) is None  # not a repository at all


def test_the_push_directory_is_followed(tmp_path, repo):
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    rel = Path(os.path.relpath(repo, elsewhere)).as_posix()
    for payload in (bash(f'cd "{repo.as_posix()}" && git push', cwd=str(elsewhere)),
                    bash(f"git -C {rel} push", cwd=str(elsewhere)),
                    pwsh(f"Set-Location -Path '{repo}'; git push", cwd=str(elsewhere)),
                    pwsh(f"& git.exe -C '{repo}' push", cwd=str(elsewhere)),
                    bash(f"bash -lc 'cd {repo.as_posix()} && git push'", cwd=str(elsewhere)),
                    pwsh(f"powershell -NoProfile -Command \"git -C '{repo}' push\"", cwd=str(elsewhere))):
        assert decide(payload) and "core.hooksPath is not set" in decide(payload), payload


@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive paths exist only on Windows")
def test_git_bash_drive_paths_are_followed(tmp_path, repo):
    drive, rest = os.path.splitdrive(str(repo))
    posix = "/" + drive[0].lower() + rest.replace("\\", "/")
    assert "is not set" in decide(bash(f"cd {posix} && git push", cwd=str(tmp_path)))


@pytest.mark.parametrize("command", ["cd $REPO && git push", "pushd x; popd; git push", "git -C \"$d\" push",
                                     "git --git-dir=x/.git push", "cd - && git push", "GIT_DIR=x/.git git push",
                                     "export GIT_WORK_TREE=x; git push", "env -C x git push"])
def test_a_push_directory_the_guard_cannot_know_is_refused(command, tmp_path):
    assert "git -C <dir> push" in decide(bash(command, cwd=str(tmp_path)))


def test_git_redirected_by_the_session_environment_is_refused(repo):
    git("config", "core.hooksPath", ".githooks", cwd=repo)
    assert "GIT_DIR" in decide(bash("git push", cwd=str(repo)), environ={"GIT_DIR": "x"})


@pytest.mark.parametrize("command", [
    "timeout 60 git push", "nice -n 5 git push", "nohup git push", "time git push", "sudo -u me git push",
    "stdbuf -o0 git push", "env -i PATH=x git push", "command git push", "exec git push", "env -u X git push",
    'env -S "git push"', "X=1 Y=2 git push", "if true; then git push; fi",
])
def test_wrappers_are_looked_through(command, repo):
    assert "is not set" in (decide(bash(command, cwd=str(repo))) or ""), command


def test_cmd_cd_switches_drive_only_with_d(tmp_path, repo):
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    assert "is not set" in decide(pwsh(f'cmd /c "cd /d {repo} && git push"', cwd=str(elsewhere)))


@pytest.mark.parametrize("command", [
    "echo --no-verify | xargs git push", 'iex "git push"', "find . -exec git push ;",
    "Start-Process git -ArgumentList push", 'eval "git push"', "echo `git push --no-verify`",
    'echo "$(git push --no-verify)"', 'echo "git push" | bash', 'git -c "$k=$v" push',
])
def test_git_run_where_the_guard_cannot_follow_is_refused(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


@pytest.mark.parametrize("command", [
    'echo "remember to git push"', 'grep -r "git push" docs', 'gh pr create --body "then git push"',
    "# git push --no-verify later\nls", "git commit -F - <<'EOF'\nsubject\n\ngit push --no-verify is refused\nEOF",
    "if git diff --quiet; then git commit -m x; fi",
])
def test_text_that_only_mentions_git_is_allowed(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))) is None, command


def test_a_here_document_a_shell_reads_is_checked(repo):
    assert "is not set" in decide(bash("bash <<'EOF'\ngit push\nEOF", cwd=str(repo)))


@pytest.mark.parametrize("command", [
    "git push --no-verify", "git push origin main --no-ver", "git commit --no-verify -m x", "git commit -n -m x",
    "git commit -anm x", "git -c core.hooksPath=/dev/null push", "git -c core.hooksPath= commit -m x",
    "git --config-env=core.hooksPath=X push", "git config --unset core.hooksPath", "git config core.hooksPath hooks",
    "git config --global core.hooksPath /tmp/h", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath git push",
    "BIMP_PREPUSH_BYPASS=why git push", "export BIMP_SECRETS_BYPASS=x", "sh -c 'git push --no-verify'",
])
def test_hook_skipping_forms_are_refused_everywhere(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


@pytest.mark.parametrize("command", [
    "git commit -m 'ship it'", "git commit -am 'no verify needed'","git commit -m x -- -n",
    "git config core.hooksPath .githooks", "git config --get core.hooksPath", "git push --dry-run",
])
def test_ordinary_commits_and_config_reads_are_allowed(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))) is None, command


def test_a_bypass_in_the_session_environment_refuses_pushes(repo):
    git("config", "core.hooksPath", ".githooks", cwd=repo)
    reason = decide(bash("git push", cwd=str(repo)), environ={"BIMP_PREPUSH_BYPASS": "x"})
    assert reason and "session's environment" in reason


def test_an_unparseable_git_push_is_refused(tmp_path):
    assert "could not parse" in decide(bash("git push 'unbalanced", cwd=str(tmp_path)))
    assert decide(bash("echo 'unbalanced", cwd=str(tmp_path))) is None


# --- the script, as Claude Code runs it ------------------------------------------------------------------------------

def run_script(stdin: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in ship_guard.BYPASS_VARS}
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, timeout=60,
                          env={**full, **(env or {})})


def test_script_blocks_with_exit_2_and_fails_closed(tmp_path):
    proc = run_script(json.dumps(bash("git push --no-verify", cwd=str(tmp_path))))
    assert proc.returncode == 2 and "BLOCKED by ship guard" in proc.stderr
    for stdin in ("not json", "[]", json.dumps({"tool_name": "Bash"})):
        assert run_script(stdin).returncode == 2, stdin


def test_script_allows_ordinary_calls(tmp_path):
    proc = run_script(json.dumps(bash("echo hi", cwd=str(tmp_path))))
    assert proc.returncode == 0 and proc.stderr == ""
