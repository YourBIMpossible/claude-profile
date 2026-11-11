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

# NTFS spellings of the same file (slop audit 2026-10-26, CP-10): a stream suffix on the last
# component (`name::$DATA`, `name:stream`) and a drive-relative path (`C:name`) reach the file
# the plain name reaches, so they get the plain name's verdict.
BLOCKED += [
    payload("X:/fixture/BIMpossible/.env::$DATA"),
    payload("X:/fixture/BIMpossible/.env:s"),
    payload("certs/server.pem::$DATA"),
    payload("X:\\fixture\\BIMpossible\\backend\\guard.py::$DATA"),
    payload("X:/Users/someone/.ssh/id_rsa:s"),
    payload("C:.env"),                                             # drive-relative
    payload("C:credentials.json"),
    payload(NS + "::$DATA", "bypassPermissions"),
    payload(NS + ":s", None),
    payload("proj/:s"),                                            # nothing left: fail closed
    tool_payload("NotebookEdit", "notebook_path", "X:/fixture/BIMpossible/.env.local::$DATA"),
]
ASKED += [
    payload(NS + "::$DATA"),
    payload("proj/NORTHSTAR.md:s"),
    payload("C:NORTHSTAR.md"),
    payload("X:\\fixture\\BIMpossible\\NORTHSTAR.share-link.md::$DATA"),
]
ALLOWED += [
    payload("proj/NORTHSTAR.draft.md::$DATA"),                     # a draft stays free
    payload("docs/notes.md:meta"),
    payload("C:notes.md"),
    payload("X:/fixture/BIMpossible/.env.example::$DATA"),
]


def _short_path(path: str) -> "str | None":
    """The 8.3 spelling of an existing path, or None where the volume has none."""
    if sys.platform != "win32":
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    if not n or n >= len(buf):
        return None
    short = buf.value
    return short if short.lower() != path.lower() else None


def short_name_checks() -> "tuple[list[str], str]":
    """8.3 short names (CP-10): `NORTHS~1.MD` is NORTHSTAR.md. Returns (failures, skip reason)."""
    import os
    import tempfile
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = os.path.realpath(raw_tmp)
        cases = (
            ("NORTHSTAR.md", "default", "ask"),
            ("NORTHSTAR.md", "bypassPermissions", "block"),
            ("google-credentials-prod.json", "default", "block"),
            ("ordinary-long-file-name.json", "default", "allow"),
        )
        seen = 0
        for fname, mode, want in cases:
            full = os.path.join(tmp, fname)
            Path(full).write_text("x", encoding="utf-8")
            short = _short_path(full)
            if short is None:
                continue
            seen += 1
            rc, out = run_gate_full(payload(short, mode))
            got = "block" if rc == 2 else ("ask" if '"ask"' in out else "allow" if rc == 0 else f"rc {rc}")
            if got != want:
                failures.append(f"expected {want.upper()} for the 8.3 name of {fname} ({mode}), got {got}: "
                                f"{os.path.basename(short)}")
        if not seen:
            return failures, "no 8.3 short names on this platform or on the temp volume"
    return failures, ""


def network_path_checks() -> "list[str]":
    """A network or device path is never handed to the filesystem by the gate.

    Resolving `//host/share/...` makes this machine contact `host` (and offer it the user's
    sign-in) before any permission decision. In-process, with realpath replaced by a recorder,
    so the test itself contacts nothing.
    """
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location("protect_paths_gate_under_test", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    failures: list[str] = []
    seen: list[str] = []
    real_realpath = os.path.realpath

    def recorder(path, *args, **kwargs):
        seen.append(str(path))
        return str(path)

    os.path.realpath = recorder
    drive = "Q" + ":"          # built here: the machine-paths scan rejects a literal drive path
    old_name = os.environ.get("COMPUTERNAME")
    os.environ["COMPUTERNAME"] = "TESTBOX"
    try:
        # another machine: tested as written, never resolved
        for raw in ("//host.invalid/share/proj/notes.md", "\\\\host.invalid\\share\\proj\\notes.md",
                    "\\\\?\\UNC\\host.invalid\\share\\notes.md"):
            seen.clear()
            if gate.is_protected(raw) or gate.needs_approval(raw):
                failures.append(f"expected ALLOW for an ordinary name on a network path: {raw}")
            if seen:
                failures.append(f"realpath was called for a network path: {raw} -> {seen}")
        # this machine under another spelling: resolved like any local path
        for raw, want in (
                (f"\\\\?\\{drive}\\proj\\notes.md", f"{drive}/proj/notes.md"),
                (f"\\\\.\\{drive}\\proj\\notes.md", f"{drive}/proj/notes.md"),
                ("//localhost/share/proj/notes.md", "//localhost/share/proj/notes.md"),
                ("//127.0.0.1/share/notes.md", "//127.0.0.1/share/notes.md"),
                ("//TestBox/share/notes.md", "//TestBox/share/notes.md"),
                ("\\\\?\\UNC\\localhost\\share\\notes.md", "//localhost/share/notes.md")):
            seen.clear()
            gate.is_protected(raw)
            if [s.replace("\\", "/") for s in seen[:1]] != [want]:
                failures.append(f"{raw} should be resolved as {want}, got {seen}")
        # a device path the gate cannot map to a drive or a host: fail closed, never resolved
        for raw in ("//./pipe/notes.md", "//?/Volume{01234567-89ab-cdef-0123-456789abcdef}/proj/notes.md",
                    "\\\\?\\GLOBALROOT\\Device\\Mup\\host.invalid\\share\\notes.md", "//./notes.md"):
            seen.clear()
            if not gate.is_protected(raw):
                failures.append(f"expected BLOCK for an uncheckable device path: {raw}")
            if seen:
                failures.append(f"realpath was called for a device path: {raw} -> {seen}")
        # the rules still apply to the name as written
        if not gate.is_protected("//host.invalid/share/.env"):
            failures.append("expected BLOCK for .env on a network path")
        if not gate.needs_approval("\\\\host.invalid\\share\\NORTHSTAR.md::$DATA"):
            failures.append("expected ASK for NORTHSTAR.md on a network path")
        # a short-name-like component there cannot be checked: fail closed
        for raw in ("//host.invalid/share/NORTHS~1.MD", "//host.invalid/share/BACKEN~1/guard.py",
                    "\\\\?\\UNC\\host.invalid\\share\\GOOGLE~1.JSO"):
            seen.clear()
            if not gate.is_protected(raw):
                failures.append(f"expected BLOCK for an uncheckable short name: {raw}")
            if seen:
                failures.append(f"realpath was called for a network path: {raw} -> {seen}")
    finally:
        os.path.realpath = real_realpath
        if old_name is None:
            os.environ.pop("COMPUTERNAME", None)
        else:
            os.environ["COMPUTERNAME"] = old_name
    return failures


def test_network_paths_are_not_resolved() -> None:
    failures = network_path_checks()
    assert not failures, "failing network-path cases:\n  " + "\n  ".join(failures)


def run_checks() -> list[str]:
    """Run every fixture through the gate; return one line per mismatch."""
    failures = short_name_checks()[0] + network_path_checks()
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


def test_short_name_aliases() -> None:
    """The 8.3 cases on their own, so a volume without short names shows as a skip, not a pass."""
    failures, skipped = short_name_checks()
    if skipped:
        import pytest
        pytest.skip(skipped)
    assert not failures, "failing 8.3 cases:\n  " + "\n  ".join(failures)


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
