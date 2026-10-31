"""Fixture tests for protect_paths_gate.py — run the real script as a subprocess
with real stdin payloads, assert exit codes. Exit 0 = allow, 2 = block. Live
north-star files are a third class: exit 0 plus a permissionDecision "ask" JSON on
stdout (owner approval prompt), never a silent allow and never a hard block.

Run: python hooks/tests/test_protect_paths_gate.py  (from the repo root or anywhere)
"""
import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "protect_paths_gate.py"


def run_gate_full(stdin_text: str) -> "tuple[int, str]":
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=stdin_text, capture_output=True, text=True, timeout=15,
    )
    return proc.returncode, proc.stdout


def run_gate(stdin_text: str) -> int:
    return run_gate_full(stdin_text)[0]


def payload(file_path, mode="default") -> str:
    body = {"tool_name": "Write", "tool_input": {"file_path": file_path}}
    if mode is not None:
        body["permission_mode"] = mode
    return json.dumps(body)


BLOCKED = [
    payload("X:/fixture/BIMpossible/.env"),
    payload("X:\\fixture\\BIMpossible\\.env"),                     # backslashes
    payload(".env.production"),
    payload("X:/fixture/BIMpossible/backend/guard.py"),
    payload("X:\\fixture\\BIMpossible\\backend\\guard.py"),
    payload("X:/Users/someone/.ssh/id_rsa"),
    payload("X:/Users/someone/.ssh/id_ed25519.pub"),
    payload("certs/server.pem"),
    payload("certs/private.KEY"),                                  # case
    payload("deploy/bundle.pfx"),
    payload("X:/Users/someone/.aws/credentials.json"),
    payload("app/google-credentials-prod.json"),                   # substring
    payload("X:/Users/someone/.netrc"),
    payload("X:/Users/someone/.pgpass"),
    # fail-closed cases
    "not json at all",
    "",
    json.dumps(["a", "list"]),
    json.dumps({"tool_name": "Write"}),                            # no tool_input
    json.dumps({"tool_name": "Write", "tool_input": {}}),          # no file_path
    json.dumps({"tool_name": "Write", "tool_input": {"file_path": "   "}}),
    json.dumps({"tool_name": "Write", "tool_input": {"file_path": 42}}),
]

# Live north-star files: owner approval prompt (exit 0 + "ask"), not a block.
ASKED = [
    payload("X:/fixture/BIMpossible/NORTHSTAR.md"),
    payload("X:\\fixture\\BIMpossible\\NORTHSTAR.md"),                     # backslashes
    payload("proj/northstar.md"),                                  # case
    payload("proj/NORTHSTAR.share-link.md"),
    payload("proj/NORTHSTAR.v2.final.md"),                         # slug with dots
]

# Live north-star files where a prompt cannot be guaranteed: hard block, never a silent allow.
NS = "X:/fixture/BIMpossible/NORTHSTAR.md"
BLOCKED += [
    payload(NS, "bypassPermissions"),
    payload(NS, "dontAsk"),
    payload(NS, None),                                             # mode missing
    payload(NS, "somethingNew"),                                   # unknown mode
]
ASKED += [payload(NS, "acceptEdits"), payload(NS, "plan"), payload(NS, "auto")]


# MultiEdit and NotebookEdit write files the same way Edit does (CP-10): same verdicts.
def tool_payload(tool: str, key: str, path: str, mode: "str | None" = "default") -> str:
    body = {"tool_name": tool, "tool_input": {key: path}}
    if mode is not None:
        body["permission_mode"] = mode
    return json.dumps(body)


BLOCKED += [
    tool_payload("MultiEdit", "file_path", "X:/fixture/BIMpossible/.env"),
    tool_payload("MultiEdit", "file_path", "X:/fixture/BIMpossible/backend/guard.py"),
    tool_payload("MultiEdit", "file_path", NS, "bypassPermissions"),
    tool_payload("NotebookEdit", "notebook_path", "secrets/google-credentials.ipynb"),
    tool_payload("NotebookEdit", "notebook_path", "X:/fixture/BIMpossible/.env.local"),
    json.dumps({"tool_name": "MultiEdit", "tool_input": {"edits": []}}),          # no file_path
    json.dumps({"tool_name": "NotebookEdit", "tool_input": {"new_source": "x"}}),  # no notebook_path
]
ASKED += [tool_payload("MultiEdit", "file_path", NS)]

ALLOWED = [
    payload("proj/NORTHSTAR.draft.md"),                            # drafts stay free
    payload("proj/NORTHSTAR.draft.md", "bypassPermissions"),       # ... in every mode
    payload("proj/NORTHSTAR.draft.md", None),
    payload("proj/NORTHSTAR.share-link.draft.md"),
    payload("templates/NORTHSTAR.template.md"),
    payload("proj/NORTHSTAR-notes.md"),                            # not a north-star file
    payload("proj/northstar_inject.py"),
    payload("X:/fixture/BIMpossible/backend/main.py"),
    payload("X:/fixture/BIMpossible/.env.example"),
    payload(".env.template"),
    payload("docs/environment.md"),                                # 'env' substring
    payload("X:/fixture/BIMpossible/backend/guardrails.py"),        # not guard.py
    payload("src/monkey.py"),                                      # .py not .key
    payload("notes/keys-to-success.md"),
    payload("X:/fixture/claude-profile/hooks/README.md"),
    json.dumps({"tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": "analysis/run.ipynb"}}),
    json.dumps({"tool_name": "MultiEdit",
                "tool_input": {"file_path": "X:/fixture/BIMpossible/backend/main.py", "edits": []}}),
]


def run_checks() -> list[str]:
    """Run every fixture through the gate; return one line per mismatch."""
    failures = []
    for p in BLOCKED:
        rc = run_gate(p)
        if rc != 2:
            failures.append(f"expected BLOCK(2), got {rc}: {p[:90]}")
    for p in ALLOWED:
        rc, out = run_gate_full(p)
        if rc != 0:
            failures.append(f"expected ALLOW(0), got {rc}: {p[:90]}")
        elif out.strip():
            failures.append(f"expected silent ALLOW, got stdout {out[:60]!r}: {p[:90]}")
    for p in ASKED:
        rc, out = run_gate_full(p)
        try:
            decision = json.loads(out)["hookSpecificOutput"]
            ok = (decision["hookEventName"] == "PreToolUse"
                  and decision["permissionDecision"] == "ask"
                  and bool(decision["permissionDecisionReason"]))
        except Exception:
            ok = False
        if rc != 0 or not ok:
            failures.append(f"expected ASK (rc 0 + ask JSON), got rc {rc} stdout {out[:60]!r}: {p[:90]}")
    return failures


def test_protect_paths_gate() -> None:
    """pytest entry point - same fixtures as running this file directly."""
    failures = run_checks()
    assert not failures, "failing fixtures:\n  " + "\n  ".join(failures)


def main() -> int:
    failures = run_checks()
    total = len(BLOCKED) + len(ALLOWED) + len(ASKED)
    if failures:
        print(f"FAIL {len(failures)}/{total}:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"OK - {total} fixtures ({len(BLOCKED)} blocked, {len(ASKED)} ask, {len(ALLOWED)} allowed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
