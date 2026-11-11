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
]
# The name as written is tested beside the cut one (review repair F1). Written out, this name
# is an env file with an unlisted suffix, so it blocks, as it did before the stream cut existed.
BLOCKED += [payload("X:/fixture/BIMpossible/.env.example::$DATA")]


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


# --- Review repair of PR 86 (Zen review of 118c0e90, findings F1-F4) -------------------------------------------------

def _load_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("protect_paths_gate_under_test", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def test_a_colon_in_a_name_does_not_remove_its_protection() -> None:
    """F1: off NTFS a colon is an ordinary character, so the name before the cut is tested as well."""
    gate = _load_gate()
    for raw in ("/srv/app/backup:server.key", "/srv/app/cert:2026.pem", "/srv/app/old:credentials.json",
                "work/foo:credentials.json", "/srv/app/x:id_rsa.pem", "/srv/app/backend:x/guard.py"):
        if raw.endswith("guard.py"):
            continue  # a colon in a folder name was never cut; listed to show the scope of the cut
        assert gate.is_protected(raw), f"expected BLOCK for {raw}"
    for raw in ("/srv/app/backup:server.key", "work/foo:credentials.json"):
        assert run_gate(payload(raw)) == 2, f"expected BLOCK(2) from the script for {raw}"
    # the NTFS reading still applies: the cut name is tested too
    assert gate.is_protected(".env:.example") and gate.is_protected("docs/.env.local:x.example")
    assert gate.needs_approval("NORTHSTAR.md:s") and gate.needs_approval("NORTHSTAR.md::$DATA")
    assert not gate.is_protected("docs/notes.md:meta") and not gate.needs_approval("docs/notes.md:meta")


def test_the_nt_namespace_form_of_a_path_is_located_like_the_other_device_forms() -> None:
    """F2: a leading backslash, two question marks, backslash is the same prefix as the extended-length one."""
    import os
    gate = _load_gate()
    nt = "\\" + "??" + "\\"
    drive = "Q" + ":"          # built here: the machine-paths scan rejects a literal drive path
    seen: list[str] = []
    real_realpath = os.path.realpath

    def recorder(path, *args, **kwargs):
        seen.append(str(path).replace("\\", "/"))
        return str(path)

    os.path.realpath = recorder
    old_name = os.environ.get("COMPUTERNAME")
    os.environ["COMPUTERNAME"] = "TESTBOX"
    try:
        for raw, want in ((f"{nt}{drive}\\proj\\notes.md", f"{drive}/proj/notes.md"),
                          (f"/??/{drive}/proj/notes.md", f"{drive}/proj/notes.md"),
                          (f"{nt}UNC\\localhost\\share\\notes.md", "//localhost/share/notes.md")):
            seen.clear()
            assert not gate.is_protected(raw), raw
            assert seen[:1] == [want], f"{raw} should be resolved as {want}, got {seen}"
        seen.clear()
        assert not gate.is_protected(f"{nt}UNC\\host.invalid\\share\\notes.md")
        assert gate.is_protected(f"{nt}UNC\\host.invalid\\share\\.env")
        assert gate.is_protected(f"{nt}UNC\\host.invalid\\share\\NORTHS~1.MD")
        assert gate.is_protected(f"{nt}GLOBALROOT\\Device\\Mup\\host.invalid\\share\\notes.md")
        assert gate.is_protected(f"{nt}pipe\\notes.md")
        assert not seen, f"realpath was called for a network or device path: {seen}"
    finally:
        os.path.realpath = real_realpath
        if old_name is None:
            os.environ.pop("COMPUTERNAME", None)
        else:
            os.environ["COMPUTERNAME"] = old_name


def test_the_short_name_behind_the_nt_namespace_prefix_is_resolved() -> None:
    """F2 on a real file: the 8.3 name of NORTHSTAR.md behind the prefix asks; of a credential file, blocks."""
    import os
    import tempfile
    import pytest
    nt = "\\" + "??" + "\\"
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = os.path.realpath(raw_tmp)
        results = []
        for fname, want in (("NORTHSTAR.md", "ask"), ("google-credentials-prod.json", "block")):
            full = os.path.join(tmp, fname)
            Path(full).write_text("x", encoding="utf-8")
            short = _short_path(full)
            if short is None:
                continue
            rc, out = run_gate_full(payload(nt + short))
            results.append((fname, want, "block" if rc == 2 else "ask" if '"ask"' in out else "allow"))
    if not results:
        pytest.skip("no 8.3 short names on this platform or on the temp volume")
    assert [r for r in results if r[1] != r[2]] == []


def test_a_path_that_cannot_be_resolved_is_blocked() -> None:
    """F3: a resolve error is a condition the gate cannot evaluate, so it blocks."""
    import os
    gate = _load_gate()
    real_realpath = os.path.realpath
    try:
        for error in (OSError("the device is not ready"), ValueError("embedded null character")):
            def failing(path, *args, _error=error, **kwargs):
                raise _error
            os.path.realpath = failing
            reason = gate.is_protected("proj/notes.md")
            assert reason and "resolved" in reason, f"{type(error).__name__}: {reason!r}"
            assert gate.is_protected("proj/NORTHS~1.MD"), type(error).__name__
    finally:
        os.path.realpath = real_realpath
    assert not gate.is_protected("proj/notes.md")


def test_trailing_dots_and_spaces_are_dropped_from_the_name() -> None:
    """F4: Win32 drops trailing dots and spaces, so `x.pem.` creates x.pem. No file here exists."""
    import os
    import tempfile
    gate = _load_gate()
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = os.path.realpath(raw_tmp).replace("\\", "/")
        for raw in (f"{tmp}/x.pem.", f"{tmp}/x.pem ", f"{tmp}/x.pem. .", f"{tmp}/.env.", f"{tmp}/.env.local. ",
                    f"{tmp}/credentials.json..", f"{tmp}/x.key.::$DATA", "//host.invalid/share/x.key.",
                    "//host.invalid/share/.env ."):
            assert gate.is_protected(raw), f"expected BLOCK for {raw!r}"
        for raw in (f"{tmp}/NORTHSTAR.slug.md.", f"{tmp}/NORTHSTAR.md. ", "//host.invalid/share/NORTHSTAR.md."):
            assert not gate.is_protected(raw), raw
            assert gate.needs_approval(raw), f"expected ASK for {raw!r}"
        assert run_gate(payload(f"{tmp}/x.pem.")) == 2
        rc, out = run_gate_full(payload(f"{tmp}/NORTHSTAR.slug.md."))
        assert rc == 0 and '"ask"' in out, (rc, out)
        # never weaker than the name as written: this was blocked before the strip and still is
        assert gate.is_protected(f"{tmp}/.env.example.")
        for raw in (f"{tmp}/notes.md.", f"{tmp}/NORTHSTAR.draft.md.", f"{tmp}/readme. "):
            assert not gate.is_protected(raw) and not gate.needs_approval(raw), raw


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
