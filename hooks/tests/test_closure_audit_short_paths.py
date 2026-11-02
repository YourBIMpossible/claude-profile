"""session_closure_audit.py's worktree-convention check across two spellings of one Windows folder.

`git worktree list` reports a checkout's long path; the configured repo root and %TEMP% can be the 8.3 short spelling
(GitHub's Windows runners put `RUNNER~1` in TEMP). A string-only comparison then flagged a repository's own
primary checkout as an out-of-convention worktree. These checks build a long-named temp folder, take its short name and
compare the two spellings. Windows only; skipped where the volume does not generate 8.3 names.

Writes only an empty temp folder, removed afterwards. No git call, no repo touched.

Run: python hooks/tests/test_closure_audit_short_paths.py  (or: python -m pytest hooks/tests -q)
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_closure_audit.py"

spec = importlib.util.spec_from_file_location("session_closure_audit", HOOK_PATH)
sca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sca)

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def short_path(path: str) -> str:
    """The 8.3 short spelling of an existing Windows path, or `path` itself when the volume generates none."""
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    if n == 0 or n > len(buf):
        raise OSError(f"GetShortPathNameW failed for {path!r} (returned {n})")
    return buf.value


def run_checks() -> "list | None":
    """Returns the (name, ok, detail) result tuples, or None when this machine cannot produce a short spelling."""
    results.clear()
    with tempfile.TemporaryDirectory(prefix="closure-audit-long-folder-name-") as tmp:
        long_dir = os.path.realpath(tmp)
        repo_long = os.path.join(long_dir, "BIMpossible-repository")
        os.mkdir(repo_long)
        repo_short = short_path(repo_long)
        if os.path.normcase(repo_short) == os.path.normcase(repo_long):
            return None

        check("primary checkout: long path from git, short configured root -> None",
              sca.classify_worktree_location(repo_long, repo_short) is None,
              f"{repo_long} vs {repo_short}")
        check("primary checkout: short path from git, long configured root -> None",
              sca.classify_worktree_location(repo_short, repo_long) is None)

        lane = os.path.join(repo_long, ".claude", "worktrees", "lane")
        os.makedirs(lane)
        check("lane under .claude/worktrees, short configured root -> None",
              sca.classify_worktree_location(lane, repo_short) is None)

        stray = os.path.join(long_dir, "main-check")
        os.mkdir(stray)
        saved = {var: os.environ.get(var) for var in ("TEMP", "TMP")}
        try:
            os.environ["TEMP"] = os.environ["TMP"] = short_path(long_dir)
            loc = sca.classify_worktree_location(stray, repo_long)
        finally:
            for var, value in saved.items():
                if value is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = value
        check("stray worktree under a short-spelled %TEMP% -> flagged under_temp",
              loc is not None and loc.get("under_temp") is True, repr(loc))
    return list(results)


def test_closure_audit_short_paths() -> None:
    """pytest entry point - same checks as running this file directly."""
    import pytest

    if os.name != "nt":
        pytest.skip("8.3 short names exist only on Windows")
    res = run_checks()
    if res is None:
        pytest.skip("this volume generates no 8.3 short names")
    failed = [(n, d) for n, ok, d in res if not ok]
    assert res, "suite ran zero checks"
    assert not failed, "failing checks:\n  " + "\n  ".join(
        n + (f" ({d})" if d else "") for n, d in failed)


def main() -> int:
    if os.name != "nt":
        print("SKIP: 8.3 short names exist only on Windows")
        return 0
    res = run_checks()
    if res is None:
        print("SKIP: this volume generates no 8.3 short names")
        return 0
    failed = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(failed)}/{len(res)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
