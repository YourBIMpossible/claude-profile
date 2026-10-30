"""Thin, portable wrapper around graphify_funnel.py for the weekly adoption-funnel check.

Adds only guards; the funnel logic, verdicts and thresholds live in graphify_funnel.py.
  * --out and --json are required and must not land where git would track them: a path
    inside a git working tree is refused unless that tree's .gitignore ignores it.
  * The script location comes from --script, else $GRAPHIFY_FUNNEL_SCRIPT.
  * The child's exit code is passed through (0 ok, 2 bad config / bad --until).
Wrapper exit codes: 3 = refused output path, 4 = funnel script not found.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

EXIT_REFUSED = 3
EXIT_NO_SCRIPT = 4


def git_worktree_of(path: Path) -> Path | None:
    """Return the nearest ancestor of *path* that holds a .git entry, else None."""
    p = path.resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def is_git_ignored(repo: Path, target: Path) -> bool:
    """True when *target* is ignored by *repo*'s ignore rules (git check-ignore exit 0)."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--no-index",
                            str(target.resolve())], capture_output=True, check=False)
    except OSError:
        return False
    return r.returncode == 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--script", type=Path, default=None,
                    help="path to graphify_funnel.py (default: $GRAPHIFY_FUNNEL_SCRIPT)")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--projects", type=Path, default=None)
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--json", type=Path, required=True)
    ap.add_argument("--allow-in-repo", action="store_true",
                    help="skip the tracked-in-a-git-tree refusal (not for scheduled use)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    script = args.script or (Path(os.environ["GRAPHIFY_FUNNEL_SCRIPT"])
                             if os.environ.get("GRAPHIFY_FUNNEL_SCRIPT") else None)
    if script is None or not script.is_file():
        print(f"audit tooling FAIL: funnel script not found: {script} "
              "(pass --script or set GRAPHIFY_FUNNEL_SCRIPT)", file=sys.stderr)
        return EXIT_NO_SCRIPT

    if not args.allow_in_repo:
        for name, target in (("--out", args.out), ("--json", args.json)):
            repo = git_worktree_of(target.parent)
            if repo is not None and not is_git_ignored(repo, target):
                print(f"refused: {name} {target} is inside git tree {repo} and not git-ignored",
                      file=sys.stderr)
                return EXIT_REFUSED

    cmd = [sys.executable, str(script), "--days", str(args.days),
           "--out", str(args.out), "--json", str(args.json)]
    for flag, val in (("--config", args.config), ("--projects", args.projects),
                      ("--log", args.log), ("--until", args.until)):
        if val is not None:
            cmd += [flag, str(val)]
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
