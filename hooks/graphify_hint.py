"""SessionStart + SubagentStart hook: point the model at the graphify graph for this cwd.

WHY THIS EXISTS
  A 2026-09-29 transcript scan found 1 of 304 sessions since the reranker
  cutover started in a folder holding `graphify-out/graph.json`, and the
  graphify skill was invoked once. The graphs exist (BIMpossible's in
  `backend/`, AddIns' at its root), but sessions start at repo roots or in
  `.claude/worktrees/<lane>`, where graphify's cwd-relative default finds
  nothing, and exploration subagents never hear about graphify at all.

WHAT IT DOES
  Resolves the canonical graph for the payload's `cwd` (same rules as
  skills/graphify/scripts/resolve_graph.py) and, only when one resolves,
  injects a short block via hookSpecificOutput.additionalContext naming the
  graph, the exact logged query command, and when to prefer it over
  Grep/Read. SessionStart gets the parent-session version (including "paste
  this into exploration subagent prompts"); SubagentStart gets a
  self-contained version so the subagent route works even when the parent
  forgets. No graph -> no output: projects without a graph are never nudged.

SCOPE HONESTY
  ADVISORY. It informs tool choice; it cannot make the model use graphify,
  and it does not check graph freshness beyond printing the build date.

PINNED-COPY RULES (hooks/README.md, C1)
  Installed as a copied file under ~/.claude/hooks. It must not import code
  from the skills junction, so the resolver below is a verbatim-behaviour
  copy of resolve_graph.resolve(); hooks/tests/test_graphify_hint.py runs
  both over the same fixtures and fails if they ever disagree. WRAPPER is a
  hardcoded path the MODEL runs (not code this hook executes).

  Never fails a session: any error -> exit 0, no output. Opt-out:
  GRAPHIFY_HINT=0.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

WRAPPER = r"F:\Claude-Profile\skills\graphify\scripts\query_reranked.py"

# Subagent types that cannot run shell commands gain nothing from the hint.
NO_SHELL_AGENTS = {"claude-code-guide", "statusline-setup"}

# ---- resolver (keep behaviour identical to skills/graphify/scripts/resolve_graph.py) ----
GRAPH_REL = Path("graphify-out") / "graph.json"
GRAPH_SUBDIRS: tuple[str, ...] = ("backend",)
MAX_NON_GIT_LEVELS = 6


def _git_toplevel(start: Path) -> Path | None:
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


def _main_checkout(toplevel: Path) -> Path | None:
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
        return None
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


def resolve(cwd: str | Path) -> dict | None:
    """Same contract as resolve_graph.resolve(), returned as a plain dict."""
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
                return {"graph": str(g), "graph_root": str(d), "source": "cwd-walk", "local_root": str(d)}
        return None

    hit = _search(here, top)
    if hit:
        g, source = hit
        root = g.parent.parent
        return {"graph": str(g), "graph_root": str(root), "source": source, "local_root": str(root)}

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
    return {"graph": str(g), "graph_root": str(root), "source": "main-checkout", "local_root": str(local)}
# ---- end resolver ----


def query_command(graph: str) -> str:
    """The wrapper command the model should run, shell-neutral.

    Forward slashes and double quotes survive both Git Bash and PowerShell; a
    backslash path passed through bash `cd` loses its separators (2026-09-29
    acceptance run: the first query failed that way, the retry succeeded).
    The wrapper takes --graph, so it runs correctly from any cwd.
    """
    return f'python "{Path(WRAPPER).as_posix()}" "<question>" --graph "{Path(graph).as_posix()}"'


def build_hint(r: dict, *, subagent: bool) -> str:
    graph = r["graph"]
    try:
        built = datetime.fromtimestamp(Path(graph).stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        built = "unknown"
    lines = [
        "<graphify-graph>",
        f"A graphify knowledge graph covers the code under {r['graph_root']} (built {built}).",
        f"  graph: {graph}",
    ]
    if r["local_root"] != r["graph_root"]:
        lines.append(
            f"  This checkout is a worktree: result paths are relative to {r['graph_root']}; "
            f"read the files under {r['local_root']} instead (your branch may differ from the graph)."
        )
    else:
        lines.append(f"  Result paths (src=...) are relative to {r['graph_root']}.")
    lines += [
        "Use it FIRST for cross-file code questions: how a feature/flow works, what calls or uses X,",
        "where data flows, what a change would affect, how an area is structured. Run it exactly as",
        "written, from any directory -- no `cd` first (forward slashes work in bash and PowerShell):",
        f'  {query_command(graph)}',
        "  (add --dfs to trace one specific path; --budget N caps output, default 2000). Then read the",
        "  cited source before making claims. If it errors or returns 0 nodes, fall back to Grep/Read.",
        "Skip it and use Grep/Read directly for: a known file, an exact symbol/string/error lookup,",
        "or a narrow one-file change.",
    ]
    if not subagent:
        lines.append(
            "When you delegate code exploration to a subagent, include the graph path and the command "
            "above in its prompt."
        )
    lines.append("</graphify-graph>")
    return "\n".join(lines)


def emit(event: str, text: str) -> None:
    out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> None:
    if os.environ.get("GRAPHIFY_HINT", "").strip() == "0":
        return
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    event = payload.get("hook_event_name") or "SessionStart"
    if event not in ("SessionStart", "SubagentStart"):
        return
    if event == "SubagentStart" and payload.get("agent_type") in NO_SHELL_AGENTS:
        return
    cwd = payload.get("cwd") or os.getcwd()
    r = resolve(cwd)
    if r is None:
        return
    emit(event, build_hint(r, subagent=(event == "SubagentStart")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
