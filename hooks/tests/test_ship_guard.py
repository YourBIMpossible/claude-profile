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
    """A runner whose `gh pr view` answers with a fixed rollup and whose head check-runs call answers with fixed runs
    (`runs_out` replaces that answer's text); git calls go to the real git."""

    def __init__(self, rollup=None, returncode: int = 0, stdout: str | None = None, runs=(), runs_returncode: int = 0,
                 runs_out: str | None = None):
        self.rollup, self.returncode, self.stdout, self.calls, self.cwds = rollup, returncode, stdout, [], []
        self.runs, self.runs_returncode, self.runs_out = list(runs), runs_returncode, runs_out

    def __call__(self, argv, cwd):
        if argv[0] == "git":
            return ship_guard._run(argv, cwd)
        self.calls.append(argv)
        self.cwds.append(cwd)
        if argv[1] == "api" and "/check-runs" in argv[2]:
            out = self.runs_out if self.runs_out is not None else json.dumps(
                {"total_count": len(self.runs), "check_runs": self.runs})
            return subprocess.CompletedProcess(argv, self.runs_returncode, out, "boom" if self.runs_returncode else "")
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


def _done(name: str, conclusion: str) -> dict:
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": conclusion}


@pytest.mark.parametrize("rollup", [[_done("machine-paths", "SKIPPED")], [_done("lint", "NEUTRAL")],
                                    [_done("machine-paths", "SKIPPED"), _done("lint", "NEUTRAL")]],
                         ids=["all-skipped", "all-neutral", "skipped-and-neutral"])
def test_a_head_whose_every_check_was_skipped_or_neutral_is_not_green(rollup):
    # SA09-1: a draft PR's only check run is SKIPPED; "at least one check" must mean one that ran.
    merge = f"gh pr merge 7 --repo o/r --squash --match-head-commit {HEAD}"
    for command in (merge, f"gh pr ready 7 --repo o/r && {merge}"):
        reason = decide(bash(command), run=FakeGh(rollup))
        assert reason and "not green" in reason and "no check ran" in reason
    skipped_run = {"name": "gate (Linux)", "status": "completed", "conclusion": "skipped"}
    reason = decide(bash(merge), run=FakeGh([], runs=[skipped_run]))
    assert reason and "no check ran" in reason


def test_a_skipped_check_beside_one_that_passed_stays_green():
    merge = bash(f"gh pr merge 7 --repo o/r --squash --match-head-commit {HEAD}")
    skipped = [_done("machine-paths", "SKIPPED")]
    assert decide(merge, run=FakeGh(skipped + [_done("tests", "SUCCESS")])) is None
    assert decide(merge, run=FakeGh(skipped + [{"__typename": "StatusContext", "context": "ci", "state": "SUCCESS"}])) is None
    assert decide(merge, run=FakeGh(skipped, runs=[{"name": "gate", "status": "completed", "conclusion": "success"}])) is None


DISPATCHED_GREEN = [{"name": "gate (Linux)", "status": "completed", "conclusion": "success"},
                    {"name": "gate (Windows)", "status": "completed", "conclusion": "success"}]


def test_a_manually_dispatched_run_on_the_head_counts_when_the_pr_rollup_is_empty():
    # A workflow_dispatch run reports on the commit, not in the PR's statusCheckRollup.
    fake = FakeGh([], runs=DISPATCHED_GREEN)
    assert decide(bash(f"gh pr merge 7 --repo o/r --squash --match-head-commit {HEAD}"), run=fake) is None
    assert fake.calls[1] == ["gh", "api", f"repos/o/r/commits/{HEAD}/check-runs?per_page=100", "--hostname", "github.com"]


@pytest.mark.parametrize("run", [{"name": "gate (Windows)", "status": "completed", "conclusion": conclusion}
                                 for conclusion in ("failure", "action_required", "stale", "cancelled", "timed_out",
                                                    None)]
                         + [{"name": "gate (Windows)", "status": "in_progress", "conclusion": None}],
                         ids=["failed", "action-required", "stale", "cancelled", "timed-out", "no-conclusion",
                              "running"])
def test_a_red_or_running_dispatched_run_on_the_head_refuses_even_with_a_green_rollup(run):
    reason = decide(bash(f"gh pr merge 7 --repo o/r --match-head-commit {HEAD}"), run=FakeGh(GREEN, runs=[run]))
    assert reason and "not green" in reason and "gate (Windows)" in reason


def test_head_check_runs_that_cannot_be_read_in_full_refuse_the_merge():
    merge = bash(f"gh pr merge 7 --repo o/r --match-head-commit {HEAD}")
    assert "could not read the check runs" in decide(merge, run=FakeGh(GREEN, runs_returncode=1))
    assert "unreadable" in decide(merge, run=FakeGh(GREEN, runs_out="not json"))
    assert "more than the guard reads" in decide(merge, run=FakeGh(GREEN, runs_out=json.dumps(
        {"total_count": 101, "check_runs": DISPATCHED_GREEN})))
    for malformed in ({"total_count": 1}, {"total_count": 1, "check_runs": {"name": "gate"}},
                      {"total_count": 2, "check_runs": [DISPATCHED_GREEN[0], "gate"]}):
        assert "unreadable" in decide(merge, run=FakeGh(GREEN, runs_out=json.dumps(malformed))), malformed

    class Unstartable(FakeGh):
        def __call__(self, argv, cwd):
            if argv[:2] == ["gh", "api"]:
                raise OSError("gh vanished")
            return super().__call__(argv, cwd)

    assert "could not read the check runs" in decide(merge, run=Unstartable(GREEN))


def test_head_check_runs_are_read_from_the_repository_and_host_the_pr_is_on():
    fake = FakeGh(GREEN, stdout=json.dumps({"number": 7, "url": "https://git.example.com/acme/tool/pull/7",
                                            "headRefOid": HEAD, "statusCheckRollup": GREEN}))
    assert decide(bash(f"gh pr merge 7 --match-head-commit {HEAD}"), run=fake) is None
    assert fake.calls[1] == ["gh", "api", f"repos/acme/tool/commits/{HEAD}/check-runs?per_page=100",
                             "--hostname", "git.example.com"]
    no_head = FakeGh(GREEN, stdout=json.dumps({"number": 7, "url": "https://github.com/o/r/pull/7",
                                               "headRefOid": "", "statusCheckRollup": GREEN}))
    assert "no head commit" in decide(bash("gh pr merge 7 --match-head-commit abc1234"), run=no_head)


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
    for name in ("mcp__github__push_files", "mcp__github__create_or_update_file", "mcp__github__delete_file"):
        assert "skips every local gate" in decide({"tool_name": name, "tool_input": {}})


@pytest.mark.parametrize("command", [
    "gh api repos/o/r/merges -f base=main -f head=x",
    "curl -X POST https://api.github.com/repos/o/r/merges -d '{}'",
    "gh api graphql -f query='mutation { mergeBranch(input: {}) { x } }'",
    "gh api graphql -f query='mutation { createCommitOnBranch(input: {}) { x } }'",
    "gh api graphql -F query=@q.graphql -f n=updateRefs",
    "gh api -X PUT repos/o/r/contents/f -f message=m -f content=eA==",
    "gh api repos/o/r/contents/f --method DELETE -f message=m -f sha=1",
    "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=1",
    "gh api repos/o/r/git/commits --input c.json",
    "curl -X POST https://api.github.com/repos/o/r/git/trees -d @t.json",
    "Invoke-RestMethod -Method Put https://api.github.com/repos/o/r/contents/f -Body $b",
    "gh api repos/o/r/contents/f -fmessage=m -fcontent=eA==",
    "gh api repos/o/r/git/refs/heads/main -Fsha=1",
    "gh api repos/o/r/git/refs -XPATCH",
    "gh api repos/o/r/contents/f \\\n  -X PUT \\\n  -f message=m",
    "gh api repos/o/r/contents/f `\n  -Method PUT",
    "p=repos/o/r/contents/f\ngh api -X PUT $p -f message=m",
    "curl https://api.github.com/repos/o/r/git/blobs -d@blob.json",
    "curl https://api.github.com/repos/o/r/git/blobs --form f=@x",
    "python -c \"import requests; requests.request('PUT', 'https://api.github.com/repos/o/r/contents/f')\"",
    "node -e \"fetch('https://api.github.com/repos/o/r/contents/f', {method: 'PUT'})\"",
    "curl https://api.github.com/repos/o/r/contents/f -sd@c.json",
    "curl -sSLT c.json https://api.github.com/repos/o/r/contents/f",
    "curl -sXPUT https://api.github.com/repos/o/r/contents/f",
    "curl -Tc.json https://api.github.com/repos/o/r/contents/f",
    "curl '-X' 'PUT' https://api.github.com/repos/o/r/contents/f", "gh api repos/o/r/contents/f -X P\"U\"T",
    "python -c \"import urllib.request as u; u.urlopen('https://api.github.com/repos/o/r/git/refs', data=b'{}')\"",
    "curl -OJd@c.json https://api.github.com/repos/o/r/contents/f",
    "curl -aT c.json https://api.github.com/repos/o/r/contents/f",
    "python -c \"import urllib.request as u; u.urlopen('https://api.github.com/repos/o/r/git/refs', b'{}')\"",
    "python -c \"import urllib.request as u; u.urlopen(u.Request('https://api.github.com/repos/o/r/git/refs', b'x'))\"",
    "python -c \"import urllib.request as u; u.urlopen('https://api.github.com/repos/o/r/git/refs\\'', b'x')\"",
    "python -c \"from urllib.request import urlopen as o; o('https://api.github.com/repos/o/r/git/refs', b'x')\"",
    "python -c \"import urllib.request as u; f = u.urlopen; f('https://api.github.com/repos/o/r/git/refs', b'x')\"",
    "python -c \"import urllib.request as u; r = u.Request('https://api.github.com/repos/o/r/git/refs'); "
    "r.data = b'x'; u.urlopen(r)\"",
])
def test_writes_that_put_code_on_a_branch_without_a_push_are_refused(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


@pytest.mark.parametrize("command", [
    "gh api repos/o/r/contents/f", "gh api repos/o/r/git/refs/heads/main --jq .object.sha",
    "curl https://api.github.com/repos/o/r/git/trees/main", "grep -n updateRef hooks/ship_guard.py",
    "gh api repos/o/r/contents/f | jq -r .content",
    "gh api repos/o/r/contents/f \\\n  --jq .sha",
    "gh api repos/o/r/contents/f -X GET", "curl -sSL https://api.github.com/repos/o/r/contents/f",
    "curl -fsSL -H 'Accept: application/vnd.github.raw' https://api.github.com/repos/o/r/contents/f",
    "gh api repos/o/r/contents/f --method=GET",
    "python -c \"import urllib.request as u; u.urlopen('https://api.github.com/repos/o/r/contents/f', timeout=5)\"",
    "python -c \"from urllib.request import urlopen; print(urlopen('https://api.github.com/repos/o/r/contents/f'))\"",
])
def test_reads_of_the_contents_and_git_data_api_are_allowed(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))) is None, command


def test_powershell_reads_of_the_contents_api_are_allowed(tmp_path):
    read = "Invoke-RestMethod https://api.github.com/repos/o/r/contents/f -TimeoutSec 5 | Out-File c.json -Force"
    assert decide(pwsh(read, cwd=str(tmp_path))) is None
    read = "$data = Invoke-RestMethod https://api.github.com/repos/o/r/contents/f -Headers $h -SkipCertificateCheck"
    assert decide(pwsh(read, cwd=str(tmp_path))) is None


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


def test_a_push_whose_gate_state_git_cannot_report_is_refused(repo):
    # SA09-4: a git timeout or a failing rev-parse inside a repository is "unknown", not "gate on".
    def timing_out(argv, cwd):
        raise subprocess.TimeoutExpired(argv, 25)

    def dubious(argv, cwd):
        return subprocess.CompletedProcess(argv, 128, "", "fatal: detected dubious ownership in repository at 'x'\n")

    reason = ship_guard.gate_is_on(str(repo), run=timing_out)
    assert reason and "could not ask git" in reason
    reason = ship_guard.gate_is_on(str(repo), run=dubious)
    assert reason and "dubious ownership" in reason
    sub = repo / "src"
    sub.mkdir()
    assert "dubious ownership" in ship_guard.gate_is_on(str(sub), run=dubious)
    assert "dubious ownership" in decide(bash("git push origin HEAD", cwd=str(repo)), run=dubious)


def test_a_gate_file_that_cannot_be_read_is_refused(repo, monkeypatch):
    # CP#87 review, R1: only a missing gate file means "no gate"; any other read error is "unknown".
    git("config", "core.hooksPath", ".githooks", cwd=repo)
    assert ship_guard.gate_is_on(str(repo)) is None  # readable and switched on
    real_read = Path.read_text

    def denied(self, *args, **kwargs):
        if self.name == "pre-push":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "read_text", denied)
        reason = ship_guard.gate_is_on(str(repo))
        assert reason and "could not be read" in reason and "PermissionError" in reason
        assert "could not be read" in decide(bash("git push origin HEAD", cwd=str(repo)), run=ship_guard._run)


def test_a_gate_path_that_is_a_folder_is_refused(repo):
    hook = repo / ".githooks" / "pre-push"
    hook.unlink()
    hook.mkdir()  # reading it fails with something other than "not found"
    reason = ship_guard.gate_is_on(str(repo))
    assert reason and "could not be read" in reason


def test_a_missing_gate_file_still_means_no_gate(repo):
    (repo / ".githooks" / "pre-push").unlink()
    assert ship_guard.gate_is_on(str(repo)) is None
    (repo / ".githooks").rmdir()
    (repo / ".githooks").write_text("not a folder\n", encoding="utf-8")
    assert ship_guard.gate_is_on(str(repo)) is None


def test_a_push_from_a_bare_repository_is_refused(tmp_path):
    # CP#87 review, R2: a bare repository has no work tree and no .git entry; that is "unknown", not "no repository".
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    reason = ship_guard.gate_is_on(str(bare))
    assert reason and "bare repository" in reason
    assert "bare repository" in (ship_guard.gate_is_on(str(bare / "refs")) or "")
    assert "bare repository" in (decide(bash("git push origin HEAD", cwd=str(bare))) or "")
    assert ship_guard.gate_is_on(str(tmp_path)) is None  # control: not a repository at all is still allowed


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


@pytest.mark.parametrize("command", ["echo hi\ngit push --no-verify", "ls\r\ngit commit -n -m x",
                                     "git push \\\n  --no-verify origin main"])
def test_a_line_break_separates_commands_and_a_continuation_does_not(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command
    assert decide(pwsh(command.replace("\\\n", "`\n"), cwd=str(tmp_path))), command


def test_an_encoded_command_is_checked_like_a_typed_one(tmp_path):
    import base64
    for inner in ("$env:BIMP_PREPUSH_BYPASS='x'; git push", "git -c core.hooksPath=/dev/null push"):
        encoded = base64.b64encode(inner.encode("utf-16-le")).decode()
        assert decide(pwsh(f"powershell -e {encoded}", cwd=str(tmp_path))), inner


def test_a_here_document_a_shell_reads_is_checked(repo):
    assert "is not set" in decide(bash("bash <<'EOF'\ngit push\nEOF", cwd=str(repo)))


def test_here_documents_are_found_the_way_bash_finds_them(repo, tmp_path):
    for command in ('echo "<<EOF"\ngit push\nEOF', "echo '<<EOF' # x\ngit push\nEOF", "(( x << 2 ))\ngit push",
                    "# cat <<EOF\ngit push\nEOF", "cat <<< EOF\ngit push"):
        assert "is not set" in (decide(bash(command, cwd=str(repo))) or ""), command
    assert decide(bash("cat > notes.md <<EOF\nthen git push\nEOF", cwd=str(tmp_path))) is None
    assert decide(bash("tee x <<-EOF\n\tgit push\n\tEOF", cwd=str(tmp_path))) is None


@pytest.mark.parametrize("command", [
    "python - <<'EOF'\nimport os\nos.system('git push --no-verify')\nEOF",
    "node <<EOF\nrequire('child_process').execSync('gh pr merge 7')\nEOF",
    "X=1 ruby <<EOF\nsystem 'git push'\nEOF",
])
def test_a_here_document_fed_to_another_program_that_ships_is_refused(command, tmp_path):
    assert "here-document" in (decide(bash(command, cwd=str(tmp_path))) or ""), command


@pytest.mark.parametrize("command", ["nice xargs git push", "echo x | timeout 5 xargs -n1 git push",
                                     "echo push | xargs -I{} sh -c 'git {}'", "ls | xargs gh pr merge"])
def test_xargs_anywhere_in_a_wrapper_chain_is_refused(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


@pytest.mark.parametrize("command", ["echo 'git push --no-verify' | xargs sh -c",
                                     "printf 'git push --no-verify' | xargs -0 bash -c",
                                     "echo 'gh pr merge 7' | xargs -I{} python -c 'import os; os.system(\"{}\")'"])
def test_xargs_feeding_a_shell_or_runner_a_push_is_refused(command, tmp_path):
    assert "xargs" in (decide(bash(command, cwd=str(tmp_path))) or ""), command


def test_xargs_that_only_reads_text_is_allowed(tmp_path):
    assert decide(bash("git log --oneline | xargs echo commit", cwd=str(tmp_path))) is None
    assert decide(bash("echo 'git push --no-verify' | xargs echo", cwd=str(tmp_path))) is None


@pytest.mark.parametrize("command", [
    "cat <<'EOF' |\ngit push --no-verify\nEOF\nbash",
    "cat <<'EOF' | python3\nimport os; os.system('git push --no-verify')\nEOF",
    "cat <<'EOF' |\nimport os; os.system('git push --no-verify')\nEOF\npython3",
    "cat <<'EOF' | xargs -0 sh -c\ngit push --no-verify\nEOF",
])
def test_a_here_document_piped_on_to_a_runner_is_checked(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


def test_a_here_document_piped_on_to_a_reader_is_allowed(tmp_path):
    assert decide(bash("cat <<'EOF' | grep push\nthen git push\nEOF", cwd=str(tmp_path))) is None
    assert decide(bash("cat <<'EOF' |\nthen git push\nEOF\ntee notes.md", cwd=str(tmp_path))) is None


def test_an_encoded_command_through_a_launcher_is_checked(tmp_path):
    import base64
    ship = base64.b64encode("git push --no-verify".encode("utf-16-le")).decode()
    quiet = base64.b64encode("Get-Date".encode("utf-16-le")).decode()
    for command in (f"Start-Process powershell -ArgumentList '-EncodedCommand','{ship}'",
                    f'Start-Process pwsh -ArgumentList "-NoProfile -enc {ship}"', f"cmd /c start powershell -e {ship}"):
        assert "encoded command" in (decide(pwsh(command, cwd=str(tmp_path))) or ""), command
    assert "decode" in decide(pwsh("Start-Process powershell -ArgumentList '-ec','!!notbase64!!'", cwd=str(tmp_path)))
    assert decide(pwsh(f"Start-Process powershell -ArgumentList '-EncodedCommand','{quiet}'", cwd=str(tmp_path))) is None
    assert decide(pwsh("Start-Process powershell -ExecutionPolicy Bypass -File x.ps1", cwd=str(tmp_path))) is None


def test_an_encoded_command_is_checked_whatever_the_launched_executable(tmp_path):
    import base64
    ship = base64.b64encode("git push --no-verify".encode("utf-16-le")).decode()
    quiet = base64.b64encode("Get-Date".encode("utf-16-le")).decode()
    for command in (f"Start-Process $exe -ArgumentList '-enc','{ship}'",
                    f"saps tools/ps.exe -ArgumentList '-EncodedCommand','{ship}'",
                    f"cmd /c start renamed.exe -ec {ship}", f"$p = 'pwsh'; & $p -enc {ship}",
                    f"& (Get-Command pwsh).Source -e {ship}"):
        assert "encoded command" in (decide(pwsh(command, cwd=str(tmp_path))) or ""), command
    assert "decode" in decide(pwsh("Start-Process $exe -ArgumentList '-enc','!!notbase64!!'", cwd=str(tmp_path)))
    assert decide(pwsh(f"Start-Process $exe -ArgumentList '-enc','{quiet}'", cwd=str(tmp_path))) is None
    assert decide(pwsh("Start-Process notepad.exe -ArgumentList notes.txt", cwd=str(tmp_path))) is None


@pytest.mark.parametrize("command", ["D:; git push", 'cmd /c "D: && git push"', "q: ; git -C x push"])
def test_a_bare_drive_switch_makes_the_directory_unknown(command, tmp_path):
    assert "git -C <dir> push" in (decide(pwsh(command, cwd=str(tmp_path))) or ""), command


@pytest.mark.parametrize("command", ['echo "$(cd x && (git push --no-verify))"', "echo $( (git push --no-verify) )",
                                     'x="$(echo "(" ; git push --no-verify)"', "echo $(git push --no-verify"])
def test_nested_substitutions_are_scanned_whole(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


def test_powershell_command_forms_are_followed(tmp_path, repo):
    import base64
    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    inner = f"git -C '{repo}' push"
    encoded = base64.b64encode(inner.encode("utf-16-le")).decode()
    for command in (f'powershell -NoProfile "{inner}"', f'pwsh -cwa "{inner}"', f"pwsh -NoLogo -ec {encoded}",
                    f"powershell /EncodedCommand {encoded}", f'pwsh -ExecutionPolicy Bypass --command "{inner}"',
                    f"pwsh -wd '{repo}' -c \"git push\""):
        assert "is not set" in (decide(pwsh(command, cwd=str(elsewhere))) or ""), command
    assert "decode" in decide(pwsh("powershell -EncodedCommand !!notbase64!!", cwd=str(elsewhere)))
    assert decide(pwsh("echo 'git push' | powershell -Command -", cwd=str(elsewhere)))
    assert decide(pwsh("'git push' | iex", cwd=str(elsewhere)))
    assert decide(pwsh("powershell -NoProfile -Command Get-Date", cwd=str(elsewhere))) is None


def test_the_rest_merge_pin_is_read_only_from_the_sha_field():
    route = "gh api -X PUT repos/o/r/pulls/7/merge"
    assert "does not pin it" in decide(bash(f'{route} -f commit_message="sha={HEAD}"'))
    assert "does not pin it" in decide(bash(f"{route} -f sha={HEAD} -f sha=1234567abc"))
    assert decide(bash(f"{route} --raw-field sha={HEAD}")) is None
    assert decide(bash(f"{route} -fsha={HEAD}")) is None
    curl = "curl -X PUT https://api.github.com/repos/o/r/pulls/7/merge"
    assert decide(bash(f"{curl} -d '{{\"sha\": \"{HEAD}\", \"merge_method\": \"squash\"}}'")) is None
    assert "does not pin it" in decide(bash(f"{curl} -d '{{\"commit_title\": \"sha={HEAD}\"}}'"))


@pytest.mark.parametrize("command", [
    "git push --no-verify", "git push origin main --no-ver", "git commit --no-verify -m x", "git commit -n -m x",
    "git commit -anm x", "git -c core.hooksPath=/dev/null push", "git -c core.hooksPath= commit -m x",
    "git --config-env=core.hooksPath=X push", "git config --unset core.hooksPath", "git config core.hooksPath hooks",
    "git config --global core.hooksPath /tmp/h", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath git push",
    "BIMP_PREPUSH_BYPASS=why git push", "export BIMP_SECRETS_BYPASS=x", "sh -c 'git push --no-verify'",
    "git -c alias.p='push --no-verify' p", "git -c Alias.st=push st", "git --config-env include.path=X status",
    "git --config-env=includeIf.onbranch:main.path=X log", "git config alias.x 'push --no-verify'",
    "git config --global alias.x '!git push'", "git config set alias.x push", "git config --add include.path h.cfg",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.st GIT_CONFIG_VALUE_0=push git st",
    "GIT_CONFIG_PARAMETERS=\"'alias.st'='push'\" git st", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=$k git st",
    "export GIT_CONFIG_GLOBAL=evil.cfg; git status", "$env:GIT_CONFIG_COUNT = '1'; git st",
    "git config --file get alias.x push", "git config --comment unset alias.x push", "git config -f list include.path h",
    "git config --default list alias.x push", "git config --type=bool alias.x push", "git config $k push",
    "git config rename-section old alias", "git config --rename-section old include", "git config edit",
    "git config --global -e",
    "git config --fil get alias.x push", "git config --typ bool alias.x push", "git config -t bool alias.x push",
    "git config -zf get alias.x push", "git config -zt bool alias.x push", "git config -ze", "git config --ed",
    "git config --rename old alias", "git config --renam old include", "git config --newer-opt x alias.p push",
    "git config -- alias.x push", "printf -v GIT_CONFIG_COUNT 1; git st",
    "Set-Item -Path Env:/GIT_CONFIG_COUNT -Value 1; git st",
    "read GIT_CONFIG_COUNT <<< 1; export GIT_CONFIG_COUNT; git st",
    "declare -x GIT_CONFIG_PARAMETERS; git st",
    "printf -v GIT_CONFIG_COUNT 1; /usr/bin/git st", "printf -v GIT_CONFIG_COUNT 1; \\git st",
    "export GIT_CONFIG_COUNT; make", "printf -v GIT_CONFIG_\"COUNT\" 1; git st",
    "grep x <(GIT_CONFIG_COUNT=1 git st)", "rg --pre sh GIT_CONFIG_COUNT run.sh",
    "git grep -O'sh -c x' GIT_CONFIG_COUNT", "git grep --open-files-in-pager=sh GIT_CONFIG_COUNT",
    "sort --compress-program=sh GIT_CONFIG_COUNT", "git config --url get alias.x push",
    "n=COUNT; export GIT_CONFIG_$n=1; git st", "export GIT_CONFIG_KEY_${i}=alias.st; git st",
    "python -c \"import os; os.environ['GIT_CONFIG_' + 'COUNT'] = '1'\"",
    "rg '--pre' sh GIT_CONFIG_COUNT run.sh", "rg \\--pre=sh GIT_CONFIG_COUNT x", "git grep -\"O\"sh GIT_CONFIG_COUNT",
])
def test_hook_skipping_forms_are_refused_everywhere(command, tmp_path):
    assert decide(bash(command, cwd=str(tmp_path))), command


@pytest.mark.parametrize("command", [
    "git commit -m 'ship it'", "git commit -am 'no verify needed'","git commit -m x -- -n",
    "git config core.hooksPath .githooks", "git config --get core.hooksPath", "git push --dry-run",
    "git config --get alias.x", "git config alias.x", "git config --unset alias.x", "git config --get-regexp alias",
    "git config get include.path", "git config --file x.cfg --get alias.x", "git config --show-origin --get alias.x",
    "git config unset alias.x", "git config --remove-section alias", "git config rename-section old new",
    "git config --file x.cfg user.name me", "grep -n GIT_CONFIG_COUNT hooks/ship_guard.py",
    "git config --get-a alias.x", "git config --show-o --get alias.x", "git config -zl",
    "git config -fx.cfg --get alias.x",
    "git grep -n GIT_CONFIG_COUNT | head -5", "python -m pytest -k GIT_CONFIG hooks/tests",
    "rg -n GIT_CONFIG_COUNT hooks | wc -l", "git config --url https://x/ --get-urlmatch alias.x",
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
