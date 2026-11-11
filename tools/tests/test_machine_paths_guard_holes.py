"""The machine-paths guard's holes from the 2026-10-26 slop audit, and the paths it must keep catching.

- CP-5: an allowlist entry's `match` used to cover every hit on the line, so one allowlisted fixture hid a real path
  written next to it. An entry now covers only the hits in the path token its `match` is found in.
- CP-6: a file with a NUL byte was taken as binary, so UTF-16 text (PowerShell 5.1's default `Out-File` encoding)
  was never scanned. A byte-order mark now selects the codec before the binary check.
- CP-21: a tracked symlink was read through the working tree, so a dangling one (or one git stores only in the index,
  as on Windows without symlink rights) took the "deleted" branch and was neither scanned nor counted. A symlink's
  target text is now read from the index and scanned, and a tracked file missing from the working tree is counted.

Every case runs the shipped scanner exactly as CI runs it, against a throwaway git repository. Fixture paths are
assembled from pieces so that this file holds no literal machine path (it is scanned too).

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_machine_paths_guard_holes.py)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / ".github" / "scripts" / "check_machine_paths.py"

BS = "\\"
REAL_UNC = BS + BS + "fileserver" + BS + "projects" + BS + "x.txt"
REAL_USER_PATH = "C" + ":" + BS + "Users" + BS + "someone" + BS + "proj" + BS + "x.py"
FAKE_FIXTURE = "X" + ":" + "/fake"
FIXTURE_ENTRY = {"path": "*.py", "match": "X:[\\\\/]", "reason": "synthetic fixture drive X:"}


def git(root: Path, *args: str, stdin: bytes = None) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, check=True).stdout


def scan_repo(root: Path, entries: list, after_add=None):
    allow = root / "allow.json"
    allow.write_text(json.dumps(entries), encoding="utf-8")
    git(root, "add", "-A")
    if after_add is not None:
        after_add(root)
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


# --- SA02-13: a second path joined into the fixture's token is its own path ---------------------------------------

REAL_USER_TAIL = "C" + ":" + "/" + "Users" + "/someone/x"
REAL_USER_TAIL_BS = "C" + ":" + BS + "Users" + BS + "someone" + BS + "x"
JOINED_TO_THE_FIXTURE = {
    "PATH-style colon join": (FAKE_FIXTURE + ":" + REAL_USER_TAIL, ["drive-path", "home-path"]),
    "parent-directory hop": (FAKE_FIXTURE + "/../" + REAL_USER_TAIL, ["drive-path", "home-path"]),
    "backslash parent-directory hop": (FAKE_FIXTURE + BS + ".." + BS + REAL_USER_TAIL_BS, ["drive-path"]),
    "question-mark join": (FAKE_FIXTURE + "?" + REAL_USER_TAIL_BS, ["drive-path"]),
    "PATH-style colon join to a home path": (FAKE_FIXTURE + ":" + "/" + "home" + "/someone/y", ["home-path"]),
}


@pytest.mark.parametrize("case", sorted(JOINED_TO_THE_FIXTURE))
def test_sa02_13_a_real_path_joined_to_an_allowlisted_fixture_in_one_token_is_flagged(case):
    token, rules = JOINED_TO_THE_FIXTURE[case]
    code, out = scan({"t.py": f'P = "{token}"\n'}, [FIXTURE_ENTRY])
    assert code == 1, out
    assert [hit.split(" ")[1] for hit in findings(out)] == rules, out
    assert "('X:" not in out, "the fixture's own drive stays covered"


def test_sa02_13_two_fixtures_joined_in_one_token_are_each_covered():
    code, out = scan({"t.py": f'P = "{FAKE_FIXTURE}:{FAKE_FIXTURE}/other"\n'}, [FIXTURE_ENTRY])
    assert code == 0, out


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


# --- CP-21: a tracked symlink is scanned by its target text; a missing file is counted ----------------------------

HOME_TARGET = "/" + "home" + "/someone/y"


def _index_only_symlink(name: str, target: str):
    """Stage a symlink the way git stores it (mode 120000, blob = target text) with nothing on disk: the state a
    dangling link, or a link checked out without symlink rights, leaves the scanner to read."""
    def add(root: Path) -> None:
        sha = git(root, "hash-object", "-w", "--stdin", stdin=target.encode()).decode().strip()
        git(root, "update-index", "--add", "--cacheinfo", f"120000,{sha},{name}")
    return add


def _repo(files: dict) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    git(Path(tmp.name), "init", "-q")
    for name, text in files.items():
        (Path(tmp.name) / name).write_text(text, encoding="utf-8")
    return tmp


def test_cp21_a_symlink_to_a_machine_path_is_flagged_even_with_nothing_on_disk():
    with _repo({"readme.md": "clean\n"}) as tmp:
        code, out = scan_repo(Path(tmp), [], _index_only_symlink("link", HOME_TARGET))
    assert code == 1 and "link:1: home-path" in out, out


def test_cp21_a_symlink_to_a_relative_path_passes_and_is_counted():
    with _repo({"readme.md": "clean\n"}) as tmp:
        code, out = scan_repo(Path(tmp), [], _index_only_symlink("link", "docs/readme.md"))
    assert code == 0 and "3 text files scanned" in out, out


def test_cp21_a_real_dangling_symlink_is_flagged():
    with _repo({"readme.md": "clean\n"}) as tmp:
        try:
            os.symlink(HOME_TARGET, Path(tmp) / "link")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable here ({exc}); the index-only case covers the same branch")
        code, out = scan_repo(Path(tmp), [])
    assert code == 1 and "link:1: home-path" in out, out


def test_cp21_a_tracked_file_missing_from_the_working_tree_is_counted():
    def delete(root: Path) -> None:
        (root / "gone.md").unlink()
    with _repo({"readme.md": "clean\n", "gone.md": "clean\n"}) as tmp:
        code, out = scan_repo(Path(tmp), [], delete)
    assert code == 0 and "1 missing from the working tree" in out, out


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS:", name)
            except pytest.skip.Exception as exc:
                print("SKIP:", name, "--", exc)
            except AssertionError as exc:
                failed += 1
                print("FAIL:", name, "--", str(exc)[:300])
    sys.exit(1 if failed else 0)
