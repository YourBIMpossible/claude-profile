"""Focused tests for session_closure_audit.py's out-of-convention worktree
warning (added 2026-08-21, following the concurrent-worktree-mutation
investigation: peer sessions registering ad hoc `git worktree add` checkouts
under %TEMP%, named e.g. `main-check` / `t6-rebase`, invisible to the repo's
`.claude/worktrees/<lane>` convention).

Imports the hook module directly and calls its pure functions — no subprocess,
no git, no filesystem writes. Nothing here mutates any repo/worktree state.

Run: python hooks/tests/test_closure_audit_worktree_convention.py
"""
import importlib.util
import os
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


REPO_ROOT = "F:/BIMpossible"

# ---------------------------------------------------------------------------
# 1. classify_worktree_location — direct unit coverage of the four scenarios
# ---------------------------------------------------------------------------

check(
    "canonical primary checkout -> None (no warning)",
    sca.classify_worktree_location(REPO_ROOT, REPO_ROOT) is None,
)

check(
    "canonical primary checkout, backslash form -> None",
    sca.classify_worktree_location("F:\\BIMpossible", REPO_ROOT) is None,
)

check(
    "canonical .claude/worktrees/<lane> -> None (no warning)",
    sca.classify_worktree_location(
        "F:/BIMpossible/.claude/worktrees/some-lane-abcdef", REPO_ROOT
    ) is None,
)

check(
    "canonical .claude/worktrees root itself -> None",
    sca.classify_worktree_location("F:/BIMpossible/.claude/worktrees", REPO_ROOT) is None,
)

_orig_temp = os.environ.get("TEMP")
_orig_tmp = os.environ.get("TMP")
try:
    os.environ["TEMP"] = "C:\\Users\\Zeria\\AppData\\Local\\Temp"
    os.environ["TMP"] = "C:\\Users\\Zeria\\AppData\\Local\\Temp"

    temp_result = sca.classify_worktree_location(
        "C:/Users/Zeria/AppData/Local/Temp/main-check", REPO_ROOT
    )
    check(
        "%TEMP% detached verification worktree -> flagged, under_temp=True",
        temp_result is not None and temp_result["under_temp"] is True,
        repr(temp_result),
    )

    external_result = sca.classify_worktree_location(
        "D:/scratch/some-other-checkout", REPO_ROOT
    )
    check(
        "arbitrary external-path worktree -> flagged, under_temp=False",
        external_result is not None and external_result["under_temp"] is False,
        repr(external_result),
    )

    # Lookalike path that merely starts with the repo root string but isn't
    # actually inside it (e.g. a sibling dir "BIMpossible2") must not be
    # treated as canonical.
    lookalike_result = sca.classify_worktree_location("F:/BIMpossible2", REPO_ROOT)
    check(
        "sibling dir with repo-root-prefixed name is NOT canonical",
        lookalike_result is not None,
        repr(lookalike_result),
    )
finally:
    if _orig_temp is None:
        os.environ.pop("TEMP", None)
    else:
        os.environ["TEMP"] = _orig_temp
    if _orig_tmp is None:
        os.environ.pop("TMP", None)
    else:
        os.environ["TMP"] = _orig_tmp

# ---------------------------------------------------------------------------
# 2. audit_worktree — end-to-end finding shape, severity, and message content
# ---------------------------------------------------------------------------

CFG = {"design_doc_globs": ["*.md"], "draft_markers": [".draft.md"]}
CLEAN_STATUS = {
    "branch": "main", "detached": False, "upstream": "origin/main",
    "ahead": 0, "behind": 0, "tracked_changed": [], "untracked": [],
}

primary_findings = sca.audit_worktree(
    "BIMpossible",
    {"path": REPO_ROOT, "branch": "main", "detached": False},
    CFG, st=CLEAN_STATUS, truncated=False, repo_root=REPO_ROOT,
)
check("primary checkout, clean status -> zero findings", primary_findings == [], repr(primary_findings))

lane_findings = sca.audit_worktree(
    "BIMpossible",
    {"path": "F:/BIMpossible/.claude/worktrees/some-lane-abcdef",
     "branch": "claude/some-lane-abcdef", "detached": False},
    CFG,
    st={**CLEAN_STATUS, "branch": "claude/some-lane-abcdef", "upstream": "origin/claude/some-lane-abcdef"},
    truncated=False, repo_root=REPO_ROOT,
)
check("canonical lane worktree, clean status -> zero findings", lane_findings == [], repr(lane_findings))

os.environ["TEMP"] = "C:\\Users\\Zeria\\AppData\\Local\\Temp"
os.environ["TMP"] = "C:\\Users\\Zeria\\AppData\\Local\\Temp"
try:
    temp_findings = sca.audit_worktree(
        "BIMpossible",
        {"path": "C:/Users/Zeria/AppData/Local/Temp/main-check", "branch": None, "detached": True},
        CFG,
        st={**CLEAN_STATUS, "branch": None, "detached": True, "upstream": None},
        truncated=False, repo_root=REPO_ROOT,
    )
    # A detached-HEAD worktree also trips the pre-existing "detached HEAD"
    # ATTENTION REQUIRED finding (unrelated to location) — both are expected
    # to coexist; assert the convention finding specifically.
    convention_findings = [f for f in temp_findings if "out-of-convention" in f["text"]]
    check(
        "%TEMP% detached worktree -> exactly one convention finding, severity WARNING",
        len(convention_findings) == 1 and convention_findings[0]["severity"] == "WARNING",
        repr(temp_findings),
    )
    if convention_findings:
        f = convention_findings[0]
        check("WARNING never escalated to ATTENTION REQUIRED", f["severity"] == "WARNING")
        check("finding names the full path", "C:/Users/Zeria/AppData/Local/Temp/main-check" in f["text"])
        check("finding reports detached HEAD state", "detached HEAD" in f["text"])
        check("finding notes %TEMP%", "%TEMP%" in f["text"])
        check(
            "action text matches the owner-oriented recommendation",
            f["action"] == (
                "Out-of-convention worktree. Its owning session should use "
                "`.claude/worktrees/<lane>` or clean it up after completion. "
                "Do not modify another session's worktree."
            ),
            f["action"],
        )

    external_findings = sca.audit_worktree(
        "BIMpossible",
        {"path": "D:/scratch/some-other-checkout", "branch": "wip", "detached": False},
        CFG,
        st={**CLEAN_STATUS, "branch": "wip", "upstream": None},
        truncated=False, repo_root=REPO_ROOT,
    )
    # branch "wip" with no upstream also trips the existing no-upstream WARNING —
    # both are expected to coexist; assert the convention one is present.
    check(
        "arbitrary external-path worktree -> convention WARNING present",
        any("out-of-convention worktree" in f["text"] and "outside the repo's worktree convention" in f["text"]
            for f in external_findings),
        repr(external_findings),
    )
    check(
        "arbitrary external-path worktree -> no ATTENTION REQUIRED from convention check",
        all(f["severity"] == "WARNING" for f in external_findings if "out-of-convention" in f["text"]),
        repr(external_findings),
    )
finally:
    if _orig_temp is None:
        os.environ.pop("TEMP", None)
    else:
        os.environ["TEMP"] = _orig_temp
    if _orig_tmp is None:
        os.environ.pop("TMP", None)
    else:
        os.environ["TMP"] = _orig_tmp

# ---------------------------------------------------------------------------
# 3. truncated worktrees still get the convention check (registry-only, no
#    git status needed) — must not be silently skipped by the truncation path
# ---------------------------------------------------------------------------

os.environ["TEMP"] = "C:\\Users\\Zeria\\AppData\\Local\\Temp"
os.environ["TMP"] = "C:\\Users\\Zeria\\AppData\\Local\\Temp"
try:
    truncated_findings = sca.audit_worktree(
        "BIMpossible",
        {"path": "C:/Users/Zeria/AppData/Local/Temp/t6-rebase", "branch": "claude/t6-reason-tag", "detached": False},
        CFG, st=None, truncated=True, repo_root=REPO_ROOT,
    )
    check(
        "truncated + out-of-convention -> both the convention WARNING and the truncation ATTENTION REQUIRED appear",
        any(f["severity"] == "WARNING" and "out-of-convention" in f["text"] for f in truncated_findings)
        and any(f["severity"] == "ATTENTION REQUIRED" and "time budget" in f["text"] for f in truncated_findings),
        repr(truncated_findings),
    )
finally:
    if _orig_temp is None:
        os.environ.pop("TEMP", None)
    else:
        os.environ["TEMP"] = _orig_temp
    if _orig_tmp is None:
        os.environ.pop("TMP", None)
    else:
        os.environ["TMP"] = _orig_tmp

# ---------------------------------------------------------------------------
# 4. no-mutation guarantee: importing/exercising the module must not shell
#    out to any git subcommand beyond read-only ones already used elsewhere
#    in the file (defensive static check, not a live git call).
# ---------------------------------------------------------------------------

import inspect  # noqa: E402

source = inspect.getsource(sca.classify_worktree_location)
check(
    "classify_worktree_location contains no subprocess/git call",
    "subprocess" not in source and "run_git" not in source,
    source,
)

# ---------------------------------------------------------------------------

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILURES:")
    for name, _, detail in failed:
        print(f"  - {name}" + (f" ({detail})" if detail else ""))
    sys.exit(1)
sys.exit(0)
