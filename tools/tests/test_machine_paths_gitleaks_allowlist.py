"""The machine-paths guard and its one allowlisted false positive: the gitleaks allowlist regex.

`.gitleaks.toml` carries a regex for a truncated PEM stub whose text contains a regex-escaped backslash, an `n`, a word
and an escaped dot. The guard's UNC rule (two backslashes, a server, a backslash, a share) matches that text, so every
pull request failed "no machine-specific paths", the default branch included.

The rule cannot tell a regex escape from a real share by shape (a real share can start with `n` and be followed by any
character), so the fix is a justified allowlist entry for that one line, not a looser rule. These tests pin both sides:
the line passes through the repository's own allowlist entry, and a real UNC path still fails, in another file and in
the same file on another line. Throwaway git repositories only; the scanner is run exactly as CI runs it.

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_machine_paths_gitleaks_allowlist.py)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / ".github" / "scripts" / "check_machine_paths.py"
ALLOW = REPO / ".machine-paths-allow.json"
GITLEAKS = ".gitleaks.toml"
MARKER = "BEGIN RSA PRIVATE KEY"  # the text that identifies the flagged line, whatever its line number

# A real UNC path, assembled so that this file holds no literal machine path (it is scanned too).
REAL_UNC = "\\" + "\\" + "fileserver" + "\\" + "projects" + "\\" + "x.txt"


def gitleaks_line() -> str:
    for line in (REPO / GITLEAKS).read_text(encoding="utf-8").splitlines():
        if MARKER in line:
            return line
    raise AssertionError(f"{GITLEAKS} no longer has a line containing {MARKER!r}; update this test")


def repo_entries() -> list:
    return [e for e in json.loads(ALLOW.read_text(encoding="utf-8")) if e["path"] == GITLEAKS]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


def scan(files: dict, entries: list):
    """Scan a throwaway repository holding `files` with an allowlist of `entries`; (exit code, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8")
        allow = root / "allow.json"
        allow.write_text(json.dumps(entries), encoding="utf-8")
        git(root, "add", "-A")
        proc = subprocess.run([sys.executable, str(SCANNER), "--root", str(root), "--allow", str(allow)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout


def test_the_repository_scans_clean_as_ci_runs_it():
    if subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"], capture_output=True).returncode != 0:
        return  # an extracted copy without .git: the scanner needs `git ls-files`
    proc = subprocess.run([sys.executable, str(SCANNER), "--root", str(REPO)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout


def test_the_gitleaks_line_is_a_false_positive_for_the_unc_rule():
    code, out = scan({GITLEAKS: gitleaks_line() + "\n"}, [])
    assert code == 1 and "unc-path" in out and f"{GITLEAKS}:1:" in out, out


def test_the_repository_allowlist_covers_exactly_that_line():
    entries = repo_entries()
    assert len(entries) == 1, entries
    assert entries[0].get("match"), "the entry must be pinned to the line, not to the whole file"
    assert entries[0].get("reason", "").strip()
    code, out = scan({GITLEAKS: gitleaks_line() + "\n"}, entries)
    assert code == 0, out


def test_a_real_unc_path_still_fails_in_another_file():
    code, out = scan({GITLEAKS: gitleaks_line() + "\n", "notes.md": f"copy it from {REAL_UNC}\n"}, repo_entries())
    assert code == 1, out
    assert "notes.md:1: unc-path" in out and f"{GITLEAKS}:" not in out.split("machine-paths:")[0], out


def test_a_real_unc_path_still_fails_in_the_allowlisted_file():
    text = gitleaks_line() + "\n" + f"# source: {REAL_UNC}\n"
    code, out = scan({GITLEAKS: text}, repo_entries())
    assert code == 1 and f"{GITLEAKS}:2: unc-path" in out and f"{GITLEAKS}:1:" not in out, out


def test_the_entry_goes_stale_when_the_line_goes_away():
    code, out = scan({GITLEAKS: "[allowlist]\n"}, repo_entries())
    assert code == 1 and "covers nothing" in out, out


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
