"""Regression test for slop-audit 2026-10-26 CP-1 (HIGH): build_report counted
configured repos with an undefined name (`repos`), so every run raised NameError
and the fail-open `__main__` handler swallowed it — the closure audit emitted
nothing at all, silently.

Imports the hook module directly; no git, no filesystem writes.

Run: python -m pytest hooks/tests/test_closure_audit_build_report.py -q
"""
import importlib.util
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_closure_audit.py"

spec = importlib.util.spec_from_file_location("session_closure_audit", HOOK_PATH)
sca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sca)


def test_build_report_with_no_repos_renders_verdict():
    report = sca.build_report({"repos": []})
    assert "0 configured repo(s)" in report


def test_build_report_counts_configured_repos_including_missing():
    cfg = {"repos": [{"name": "Gone", "path": "X:/definitely-not-a-repo-xyz", "required": True}]}
    report = sca.build_report(cfg)
    assert "1 configured repo(s)" in report
    assert "configured path not found" in report


if __name__ == "__main__":
    test_build_report_with_no_repos_renders_verdict()
    test_build_report_counts_configured_repos_including_missing()
    print("PASS")
