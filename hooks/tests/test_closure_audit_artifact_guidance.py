"""Pins the action guidance for an untracked design/decision artifact in
session_closure_audit.py: governing -> commit/push; transient -> reports dir;
not ready -> *.draft.md. Pure-function call, no git, no filesystem writes.

Run: python -m pytest hooks/tests -q
"""
import importlib.util
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_closure_audit.py"

spec = importlib.util.spec_from_file_location("session_closure_audit", HOOK_PATH)
sca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sca)

EXPECTED = (
    "Commit and push if it governs future work; if it is transient output, "
    f"move it to {sca.reports_dir()}; or rename to *.draft.md if not ready."
)


def test_untracked_design_doc_action_guidance_verbatim() -> None:
    repo = "X:/fixture/BIMpossible"
    wt = {"path": repo, "branch": "main", "detached": False}
    st = {
        "branch": "main", "upstream": "origin/main", "detached": False,
        "ahead": 0, "behind": 0, "tracked_changed": [],
        "untracked": ["decisions/new-decision.md", "notes.draft.md"],
        "status_failed": False,
    }
    findings = sca.audit_worktree("X", wt, {}, st=st, truncated=False, repo_root=repo)
    doc = [f for f in findings if "untracked design/decision doc" in f["text"]]
    assert len(doc) == 1
    assert doc[0]["severity"] == "ATTENTION REQUIRED"
    assert doc[0]["action"] == EXPECTED
    # renders with single backslashes, no doubled escapes, no control chars
    assert sca.reports_dir() + ";" in doc[0]["action"]  # this machine's reports dir, from its config
    assert chr(92) * 2 not in doc[0]["action"]
    assert "\r" not in doc[0]["action"]
    # draft marker handling unchanged
    draft = [f for f in findings if "untracked draft" in f["text"]]
    assert len(draft) == 1 and draft[0]["severity"] == "WARNING"


TRACKED_EXPECTED = (
    "Commit and push the change if it governs future work; "
    "otherwise revert it or finish it."
)


def test_tracked_modified_design_doc_action_guidance_verbatim() -> None:
    repo = "X:/fixture/BIMpossible"
    wt = {"path": repo, "branch": "main", "detached": False}
    st = {
        "branch": "main", "upstream": "origin/main", "detached": False,
        "ahead": 0, "behind": 0, "tracked_changed": ["decisions/existing-decision.md"],
        "untracked": [], "status_failed": False,
    }
    findings = sca.audit_worktree("X", wt, {}, st=st, truncated=False, repo_root=repo)
    doc = [f for f in findings if "modified design/decision doc" in f["text"]]
    assert len(doc) == 1
    assert doc[0]["severity"] == "ATTENTION REQUIRED"
    assert doc[0]["action"] == TRACKED_EXPECTED
    assert "Claude-Tools" not in doc[0]["action"]
    assert "*.draft.md" not in doc[0]["action"]
