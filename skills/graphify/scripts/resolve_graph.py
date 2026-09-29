"""Resolve which graphify graph (if any) serves a working directory.

graphify's own CLI only looks at `<cwd>/graphify-out/graph.json`. Sessions
rarely start there: they start at a repo root whose graph lives in a subfolder
(`F:\\BIMpossible` -> `backend\\graphify-out`), or in a worktree under
`.claude\\worktrees\\<lane>`, where `graphify-out/` (gitignored) never exists.
This resolves the canonical graph instead of assuming it sits beside cwd.

Search order (first hit wins):
  1. cwd and its parents, up to the git toplevel of cwd's checkout.
  2. `<toplevel>/<sub>/graphify-out/graph.json` for each sub in GRAPH_SUBDIRS.
  3. If that checkout is a linked worktree: steps 1-2 again in the MAIN
     checkout, starting from cwd's equivalent path there.
Outside any git checkout, only step 1 runs, capped at MAX_NON_GIT_LEVELS.

The walk never climbs past the checkout's own toplevel, so a worktree of
repo A that happens to sit inside repo B's folder never picks up B's graph.
The graph is never copied: the main checkout's graph stays the only source.

A pinned copy of this logic lives in hooks/graphify_hint.py (hooks may not
execute code behind the skills junction); hooks/tests/test_graphify_hint.py
asserts the two stay in agreement.

CLI: python resolve_graph.py [--cwd DIR] [--json]
Prints the graph path (or JSON) and exits 0; exits 1 when no graph resolves.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

GRAPH_REL = Path("graphify-out") / "graph.json"
# Repo subfolders that hold a repo's canonical graph when the root does not.
GRAPH_SUBDIRS: tuple[str, ...] = ("backend",)
MAX_NON_GIT_LEVELS = 6


@dataclass(frozen=True)
class ResolvedGraph:
    graph: str        # absolute path to graph.json
    graph_root: str   # directory the graph was built from (parent of graphify-out)
    source: str       # "cwd-walk" | "repo-subdir" | "main-checkout"
    local_root: str   # graph_root's equivalent inside cwd's own checkout


def _git_toplevel(start: Path) -> Path | None:
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


def _main_checkout(toplevel: Path) -> Path | None:
    """Main checkout root for a linked worktree; None for a main checkout."""
    dotgit = toplevel / ".git"
    if not dotgit.is_file():
        return None
    try:
        line = dotgit.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not line.lower().startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (toplevel / gitdir).resolve()
    try:
        common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    common_dir = Path(common)
    if not common_dir.is_absolute():
        common_dir = (gitdir / common_dir).resolve()
    if common_dir.name.lower() != ".git":
        return None  # bare repo or unusual layout: no main working tree to use
    return common_dir.parent


def _search(start: Path, root: Path) -> tuple[Path, str] | None:
    d = start
    while True:
        if (d / GRAPH_REL).is_file():
            return d / GRAPH_REL, "cwd-walk"
        if d == root or d.parent == d:
            break
        d = d.parent
    for sub in GRAPH_SUBDIRS:
        cand = root / sub / GRAPH_REL
        if cand.is_file():
            return cand, "repo-subdir"
    return None


def resolve(cwd: str | Path) -> ResolvedGraph | None:
    try:
        here = Path(cwd).resolve()
    except OSError:
        return None
    top = _git_toplevel(here)

    if top is None:
        for level, d in enumerate((here, *here.parents)):
            if level > MAX_NON_GIT_LEVELS:
                break
            if (d / GRAPH_REL).is_file():
                g = d / GRAPH_REL
                return ResolvedGraph(str(g), str(d), "cwd-walk", str(d))
        return None

    hit = _search(here, top)
    if hit:
        g, source = hit
        root = g.parent.parent
        return ResolvedGraph(str(g), str(root), source, str(root))

    main = _main_checkout(top)
    if main is None or main == top:
        return None
    rel = here.relative_to(top)
    start = main / rel
    while not start.exists() and start != main:
        start = start.parent
    hit = _search(start, main)
    if not hit:
        return None
    g, _ = hit
    root = g.parent.parent
    local = top / root.relative_to(main)
    return ResolvedGraph(str(g), str(root), "main-checkout", str(local))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = resolve(args.cwd)
    if r is None:
        return 1
    print(json.dumps(asdict(r)) if args.json else r.graph)
    return 0


if __name__ == "__main__":
    sys.exit(main())
