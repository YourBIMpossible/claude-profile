"""The machine-paths guard's holes from the 2026-10-26 slop audit, and the paths it must keep catching.

- CP-5: an allowlist entry's `match` used to cover every hit on the line, so one allowlisted fixture hid a real path
  written next to it. An entry now covers only the hits in the path token its `match` is found in.
- CP-6: a file with a NUL byte was taken as binary, so UTF-16 text (PowerShell 5.1's default `Out-File` encoding)
  was never scanned. A byte-order mark now selects the codec before the binary check.

Every case runs the shipped scanner exactly as CI runs it, against a throwaway git repository. Fixture paths are
assembled from pieces so that this file holds no literal machine path (it is scanned too).

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_machine_paths_guard_holes.py)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / ".github" / "scripts" / "check_machine_paths.py"

BS = "\\"
REAL_UNC = BS + BS + "fileserver" + BS + "projects" + BS + "x.txt"
REAL_USER_PATH = "C" + ":" + BS + "Users" + BS + "someone" + BS + "proj" + BS + "x.py"
FAKE_FIXTURE = "X" + ":" + "/fake"
FIXTURE_ENTRY = {"path": "*.py", "match": "X:[\\\\/]", "reason": "synthetic fixture drive X:"}


def git(root: Path, *args: str, stdin: bytes = None) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, check=True).stdout


def scan_repo(root: Path, entries: list):
    allow = root / "allow.json"
    allow.write_text(json.dumps(entries), encoding="utf-8")
    git(root, "add", "-A")
    proc = subprocess.run([sys.executable, str(SCANNER), "--root", str(root), "--allow", str(allow)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout


def scan(files: dict, entries: list):
    """Scan a throwaway repository holding `files` (str = UTF-8 text, bytes = raw); (exit code, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        for name, content in files.items():
            if isinstance(content, bytes):
                (root / name).write_bytes(content)
            else:
                (root / name).write_text(content, encoding="utf-8")
        return scan_repo(root, entries)


def findings(out: str) -> list:
    return [line for line in out.split("machine-paths:")[0].splitlines() if line.strip()]


def test_the_built_in_self_test_passes():
    proc = subprocess.run([sys.executable, str(SCANNER), "--self-test"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout


# --- real machine paths are still caught -------------------------------------------------------------------------

def test_a_real_unc_path_is_flagged():
    code, out = scan({"notes.md": f"copy it from {REAL_UNC}\n"}, [])
    assert code == 1 and "notes.md:1: unc-path" in out, out


def test_a_real_drive_letter_user_path_is_flagged():
    code, out = scan({"notes.md": f"open {REAL_USER_PATH}\n"}, [])
    assert code == 1 and "notes.md:1: drive-path" in out, out


# --- CP-5: an allowlist entry covers only its own path token ------------------------------------------------------

def test_cp5_an_allowlisted_fixture_does_not_hide_a_real_path_on_the_same_line():
    line = f'P = "{FAKE_FIXTURE}"; Q = "{REAL_USER_PATH}"\n'
    code, out = scan({"t.py": line}, [FIXTURE_ENTRY])
    assert code == 1, out
    hits = findings(out)
    assert len(hits) == 1 and hits[0].startswith("t.py:1: drive-path ('C:"), out


def test_cp5_an_allowlisted_fixture_does_not_hide_a_real_unc_path_on_the_same_line():
    line = f'P = "{FAKE_FIXTURE}"  # mirror of {REAL_UNC}\n'
    code, out = scan({"t.py": line}, [FIXTURE_ENTRY])
    assert code == 1 and "t.py:1: unc-path" in out and "('X:" not in out, out


def test_cp5_the_fixture_alone_on_a_line_is_still_covered():
    code, out = scan({"t.py": f'P = "{FAKE_FIXTURE}"\n'}, [FIXTURE_ENTRY])
    assert code == 0, out


def test_cp5_every_hit_inside_the_matched_token_is_covered():
    # A drive path under a Users folder is two hits (drive-path and home-path) in one token.
    token = "X" + ":" + "/" + "Users" + "/someone/.ssh/id_rsa"
    code, out = scan({"t.py": f'payload("{token}")\n'}, [FIXTURE_ENTRY])
    assert code == 0, out


def test_cp5_a_match_found_later_in_the_same_token_covers_the_hit():
    # The repository's own Show-AuditAlerts entry: `match` names the file at the end of the path.
    token = "F" + ":" + BS + "Workspace" + BS + "Ack-AuditAlert.ps1"
    entry = {"path": "a.ps1", "match": "Ack-AuditAlert\\.ps1", "reason": "pinned upstream text"}
    code, out = scan({"a.ps1": f"$ack = '{token}'\n"}, [entry])
    assert code == 0, out
    code, out = scan({"a.ps1": f"$ack = '{token}'; $src = '{REAL_USER_PATH}'\n"}, [entry])
    assert code == 1 and len(findings(out)) == 1 and "('C:" in out, out


# --- CP-6: UTF-16 text is decoded and scanned, not skipped as binary ----------------------------------------------

def _text(body: str) -> str:
    return "# header\r\n" + body + "\r\n"


def test_cp6_utf16le_with_bom_is_scanned():
    code, out = scan({"a.ps1": b"\xff\xfe" + _text(f"$p = '{REAL_USER_PATH}'").encode("utf-16-le")}, [])
    assert code == 1 and "a.ps1:2: drive-path" in out and "0 binary skipped" in out, out


def test_cp6_utf16be_with_bom_is_scanned():
    code, out = scan({"a.ps1": b"\xfe\xff" + _text(f"$p = '{REAL_UNC}'").encode("utf-16-be")}, [])
    assert code == 1 and "a.ps1:2: unc-path" in out, out


def test_cp6_utf8_with_bom_is_scanned():
    code, out = scan({"a.ps1": b"\xef\xbb\xbf" + _text(f"$p = '{REAL_USER_PATH}'").encode("utf-8")}, [])
    assert code == 1 and "a.ps1:2: drive-path" in out, out


def test_cp6_a_clean_utf16_file_passes_and_is_counted_as_text():
    code, out = scan({"a.ps1": b"\xff\xfe" + _text("Write-Output 'hello'").encode("utf-16-le")}, [])
    assert code == 0 and "0 binary skipped" in out, out


def test_cp6_a_real_binary_is_still_skipped():
    code, out = scan({"b.bin": b"\x89PNG\x00\x00" + REAL_USER_PATH.encode()}, [])
    assert code == 0 and "1 binary skipped" in out, out


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS:", name)
            except AssertionError as exc:
                failed += 1
                print("FAIL:", name, "--", str(exc)[:300])
    sys.exit(1 if failed else 0)
