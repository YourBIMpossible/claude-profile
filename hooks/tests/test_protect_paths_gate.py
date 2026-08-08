"""Fixture tests for protect_paths_gate.py — run the real script as a subprocess
with real stdin payloads, assert exit codes. Exit 0 = allow, 2 = block.

Run: python hooks/tests/test_protect_paths_gate.py  (from the repo root or anywhere)
"""
import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "protect_paths_gate.py"


def run_gate(stdin_text: str) -> int:
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=stdin_text, capture_output=True, text=True, timeout=15,
    )
    return proc.returncode


def payload(file_path) -> str:
    return json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path}})


BLOCKED = [
    payload("F:/AI-Dev/BIMpossible/.env"),
    payload("F:\\AI-Dev\\BIMpossible\\.env"),                     # backslashes
    payload(".env.production"),
    payload("F:/AI-Dev/BIMpossible/backend/guard.py"),
    payload("F:\\AI-Dev\\BIMpossible\\backend\\guard.py"),
    payload("C:/Users/Zeria/.ssh/id_rsa"),
    payload("C:/Users/Zeria/.ssh/id_ed25519.pub"),
    payload("certs/server.pem"),
    payload("certs/private.KEY"),                                  # case
    payload("deploy/bundle.pfx"),
    payload("C:/Users/Zeria/.aws/credentials.json"),
    payload("app/google-credentials-prod.json"),                   # substring
    payload("C:/Users/Zeria/.netrc"),
    payload("C:/Users/Zeria/.pgpass"),
    # fail-closed cases
    "not json at all",
    "",
    json.dumps(["a", "list"]),
    json.dumps({"tool_name": "Write"}),                            # no tool_input
    json.dumps({"tool_name": "Write", "tool_input": {}}),          # no file_path
    json.dumps({"tool_name": "Write", "tool_input": {"file_path": "   "}}),
    json.dumps({"tool_name": "Write", "tool_input": {"file_path": 42}}),
]

ALLOWED = [
    payload("F:/AI-Dev/BIMpossible/backend/main.py"),
    payload("F:/AI-Dev/BIMpossible/.env.example"),
    payload(".env.template"),
    payload("docs/environment.md"),                                # 'env' substring
    payload("F:/AI-Dev/BIMpossible/backend/guardrails.py"),        # not guard.py
    payload("src/monkey.py"),                                      # .py not .key
    payload("notes/keys-to-success.md"),
    payload("F:/AI-Dev/claude-profile/hooks/README.md"),
    json.dumps({"tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": "analysis/run.ipynb"}}),
]


def main() -> None:
    failures = []
    for p in BLOCKED:
        rc = run_gate(p)
        if rc != 2:
            failures.append(f"expected BLOCK(2), got {rc}: {p[:90]}")
    for p in ALLOWED:
        rc = run_gate(p)
        if rc != 0:
            failures.append(f"expected ALLOW(0), got {rc}: {p[:90]}")
    total = len(BLOCKED) + len(ALLOWED)
    if failures:
        print(f"FAIL {len(failures)}/{total}:")
        for f in failures:
            print("  " + f)
        sys.exit(1)
    print(f"OK - {total} fixtures ({len(BLOCKED)} blocked, {len(ALLOWED)} allowed)")


if __name__ == "__main__":
    main()
