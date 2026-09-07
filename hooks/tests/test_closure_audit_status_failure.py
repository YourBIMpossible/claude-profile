"""Regression tests for the 2026-08-24 slop-audit HIGH finding: a worktree
whose per-worktree `git status` call FAILS (timeout, non-zero exit, index.lock
contention, any exception in run_git) must surface as an explicit finding —
never render as "clean. No action needed."

Root cause: run_git returned [] for every failure mode, parse_status turned
that into an all-defaults dict (ahead=0, no changes), and audit_worktree found
nothing to report. Failure and cleanliness were byte-identical in the report.

Imports the hook module directly and monkeypatches run_git — no subprocess,
no git, no filesystem writes.

Run: python hooks/tests/test_closure_audit_status_failure.py  (or: python -m pytest hooks/tests -q)
"""
import importlib.util
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_closure_audit.py"

spec = importlib.util.spec_from_file_location("session_closure_audit", HOOK_PATH)
sca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sca)

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def run_checks() -> list:
    """Exercise the status-failure paths. Returns the (name, ok, detail) result tuples."""
    results.clear()
    REPO = "F:/BIMpossible"
    WT = {"path": REPO, "branch": "main", "detached": False}

    # ---------------------------------------------------------------------------
    # 1. run_git distinguishes failure (None) from genuine empty output ([])
    # ---------------------------------------------------------------------------

    check(
        "run_git on a nonexistent repo path -> None (failure), not []",
        sca.run_git("F:/definitely-not-a-repo-xyz", "status") is None,
    )

    # ---------------------------------------------------------------------------
    # 2. parse_status propagates the failure instead of defaulting to clean
    # ---------------------------------------------------------------------------

    orig_run_git = sca.run_git
    try:
        sca.run_git = lambda repo, *a: None  # every git call fails
        st = sca.parse_status(REPO)
        check(
            "parse_status on failed git carries status_failed=True",
            isinstance(st, dict) and st.get("status_failed") is True,
            f"got {st!r}",
        )

        # ---------------------------------------------------------------------
        # 3. audit_worktree turns that into an explicit finding, never clean
        # ---------------------------------------------------------------------
        findings = sca.audit_worktree("X", WT, {}, st=st, truncated=False, repo_root=REPO)
        check(
            "audit_worktree on status_failed -> at least one finding (not clean)",
            len(findings) >= 1,
            f"got {findings!r}",
        )
        check(
            "the finding is ATTENTION REQUIRED",
            any(f["severity"] == "ATTENTION REQUIRED" for f in findings),
            f"got {findings!r}",
        )
        check(
            "the finding says state is unknown, not confirmed clean",
            any("not confirmed clean" in f["text"] for f in findings),
            f"got {findings!r}",
        )
    finally:
        sca.run_git = orig_run_git

    # ---------------------------------------------------------------------------
    # 4. A genuinely clean status still audits clean (no regression)
    # ---------------------------------------------------------------------------

    orig_run_git = sca.run_git
    try:
        sca.run_git = lambda repo, *a: [
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +0 -0",
        ]
        st = sca.parse_status(REPO)
        check(
            "clean status parses without status_failed",
            not st.get("status_failed"),
            f"got {st!r}",
        )
        findings = sca.audit_worktree("X", WT, {}, st=st, truncated=False, repo_root=REPO)
        check(
            "clean status still yields zero findings",
            findings == [],
            f"got {findings!r}",
        )
    finally:
        sca.run_git = orig_run_git

    # ---------------------------------------------------------------------------
    # 5. list_worktrees still treats total git failure as a single unreadable entry
    # ---------------------------------------------------------------------------

    orig_run_git = sca.run_git
    try:
        sca.run_git = lambda repo, *a: None
        wts = sca.list_worktrees(REPO)
        check(
            "list_worktrees on failed git -> one unreadable entry (unchanged contract)",
            len(wts) == 1 and wts[0].get("unreadable") is True,
            f"got {wts!r}",
        )
    finally:
        sca.run_git = orig_run_git
    return list(results)


def test_closure_audit_status_failure() -> None:
    """pytest entry point - same checks as running this file directly."""
    res = run_checks()
    failed = [(n, d) for n, ok, d in res if not ok]
    assert res, "suite ran zero checks"
    assert not failed, "failing checks:\n  " + "\n  ".join(
        n + (f" ({d})" if d else "") for n, d in failed)


def main() -> int:
    res = run_checks()
    failed = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(failed)}/{len(res)} passed")
    if failed:
        print("FAILURES:")
        for name, _, detail in failed:
            print(f"  - {name}" + (f" ({detail})" if detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
