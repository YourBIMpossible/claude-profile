"""SessionStart + SubagentStart + UserPromptSubmit hook: point the model at the graphify graph for this cwd.

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

  UserPromptSubmit is a fallback. The harness does not always fire
  SessionStart: a desktop fork (2026-10-05 funnel, session 07542a44) fired
  none, and 15 compactions on 2026-09-29/30 left no SessionStart:compact
  record. On each prompt, after a graph resolves (never before, so no-graph
  repos never touch the transcript), it scans transcript_path back to the
  last compact_boundary for a delivered hint (a hook_additional_context
  attachment carrying <graphify-graph>) and injects the session hint only
  if none is there. Its own injection is that marker for the next prompt.

DIAGNOSTIC LOG
  One JSONL record per invocation in ~/.claude/hooks/logs/graphify_hint.jsonl
  (override: GRAPHIFY_HINT_LOG_DIR), rotated to a single .1 at 1 MiB. Fields:
  ts, event, source, agent_type, session_id, cwd, resolved, graph,
  transcript_read, suppressed_prior_hint, emitted, skipped, error. Never the
  prompt, transcript content, or exception messages (type + errno/char
  offset only). Separate from the graphify query log. Best-effort: written
  after the hint is emitted, and any logging failure is swallowed.

SCOPE HONESTY
  ADVISORY. It informs tool choice; it cannot make the model use graphify,
  and it does not check graph freshness beyond printing the build date.

PINNED-COPY RULES (hooks/README.md, C1)
  Installed as a copied file under ~/.claude/hooks. It must not import code
  from the skills junction, so the resolver below is a verbatim-behaviour
  copy of resolve_graph.resolve(); hooks/tests/test_graphify_hint.py runs
  both over the same fixtures and fails if they ever disagree. WRAPPER is a
  hardcoded path the MODEL runs (not code this hook executes).

  Never fails a session: any error -> exit 0, no output (the error type goes
  to the diagnostic log). Opt-out: GRAPHIFY_HINT=0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The reranked-query wrapper in the claude-profile checkout. GRAPHIFY_WRAPPER
# overrides; otherwise the house location per platform (Platform map in CLAUDE.md).
WRAPPER = os.environ.get("GRAPHIFY_WRAPPER") or (
    r"F:\Claude-Profile\skills\graphify\scripts\query_reranked.py" if os.name == "nt"
    else str(Path.home() / "workspace" / "claude-profile" / "skills" / "graphify"
             / "scripts" / "query_reranked.py"))
PYTHON_CMD = "python" if os.name == "nt" else "python3"

# Subagent types that cannot run shell commands gain nothing from the hint.
NO_SHELL_AGENTS = {"claude-code-guide", "statusline-setup"}
HANDLED_EVENTS = ("SessionStart", "SubagentStart", "UserPromptSubmit")

HINT_TAG = "<graphify-graph>"
SCAN_CHUNK = 1 << 20
SCAN_MAX_BYTES = 64 << 20  # live context is far smaller; past this, inject rather than keep reading

LOG_DIR_ENV = "GRAPHIFY_HINT_LOG_DIR"
LOG_NAME = "graphify_hint.jsonl"
LOG_MAX_BYTES = 1 << 20  # one rotation: at most ~2 MiB on disk
MAX_FIELD = 260
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

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
    return f'{PYTHON_CMD} "{Path(WRAPPER).as_posix()}" "<question>" --graph "{Path(graph).as_posix()}"'


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


def _attachment_has_hint(attachment: object) -> bool:
    if not isinstance(attachment, dict) or attachment.get("type") != "hook_additional_context":
        return False
    content = attachment.get("content")
    parts = content if isinstance(content, list) else [content]
    return any(isinstance(p, str) and HINT_TAG in p for p in parts)


def hint_in_live_context(transcript_path: str | None) -> bool:
    """True if a main-thread graphify hook hint follows the last compact_boundary.

    Reads the transcript backward and stops at the first compaction, so the
    cost tracks the live context, not the session's history; SCAN_MAX_BYTES
    caps it outright. Only hook_additional_context attachments count: a hint
    pasted by the user or echoed in a tool result was never delivered by the
    harness. No transcript_path -> False (inject); an unreadable one raises
    OSError, which the caller logs and treats as False.
    """
    if not transcript_path:
        return False
    with open(transcript_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        scanned = 0
        tail = b""
        while pos > 0 and scanned < SCAN_MAX_BYTES:
            size = min(SCAN_CHUNK, pos)
            pos -= size
            f.seek(pos)
            block = f.read(size) + tail
            scanned += size
            lines = block.split(b"\n")
            # lines[0] may be cut mid-record unless the file start was reached.
            tail = lines.pop(0) if pos > 0 else b""
            for line in reversed(lines):
                verdict = _classify(line)
                if verdict is not None:
                    return verdict
        return False


def _classify(line: bytes) -> bool | None:
    """True = delivered hint, False = compaction boundary (stop), None = keep going."""
    boundary = b"compact_boundary" in line
    if not boundary and not (b"graphify-graph" in line and b"hook_additional_context" in line):
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("isSidechain"):
        return None
    if boundary and rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
        return False
    return True if _attachment_has_hint(rec.get("attachment")) else None


def handle(payload: dict, rec: dict) -> tuple[str, str] | None:
    """Decide what to inject for one hook payload; record the outcome in rec."""
    event = payload.get("hook_event_name") or "SessionStart"
    rec["event"] = str(event)[:40]
    if event not in HANDLED_EVENTS:
        rec["skipped"] = "event_ignored"
        return None
    if event == "SubagentStart" and payload.get("agent_type") in NO_SHELL_AGENTS:
        rec["skipped"] = "shell_less_agent"
        return None
    cwd = payload.get("cwd") or os.getcwd()
    rec["cwd"] = str(cwd)[:MAX_FIELD]
    r = resolve(cwd)
    rec["resolved"] = r is not None
    if r is None:
        rec["skipped"] = "no_graph"
        return None
    rec["graph"] = r["graph"]
    if event == "UserPromptSubmit":
        # Fallback for paths where the harness fires no SessionStart (desktop
        # forks, some compactions): inject only if the live context lacks it.
        rec["transcript_read"] = True
        try:
            if hint_in_live_context(payload.get("transcript_path")):
                rec["suppressed_prior_hint"] = True
                return None
        except OSError as e:
            rec["error"] = sanitize_error(e)
    return event, build_hint(r, subagent=(event == "SubagentStart"))


# ---- diagnostic log (best-effort; never affects what is emitted) ----
def log_dir() -> Path:
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    return Path(override) if override else Path.home() / ".claude" / "hooks" / "logs"


def sanitize_error(e: BaseException) -> str:
    """Exception type plus a numeric locator only: messages can carry paths or content."""
    name = type(e).__name__
    if isinstance(e, json.JSONDecodeError):
        return f"{name} at char {e.pos}"
    if isinstance(e, OSError) and e.errno is not None:
        return f"{name} errno={e.errno}"
    return name


def write_log(rec: dict) -> None:
    """Append one JSONL record; rotate to a single .1 backup past LOG_MAX_BYTES."""
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / LOG_NAME
        try:
            if path.stat().st_size >= LOG_MAX_BYTES:
                os.replace(path, d / (LOG_NAME + ".1"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> None:
    rec: dict = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": None, "source": None, "agent_type": None, "session_id": None, "cwd": None,
        "resolved": None, "graph": None, "transcript_read": False,
        "suppressed_prior_hint": False, "emitted": False, "skipped": None, "error": None,
    }
    try:
        if os.environ.get("GRAPHIFY_HINT", "").strip() == "0":
            rec["skipped"] = "opt_out"
            return
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        for key in ("source", "agent_type"):
            if isinstance(payload.get(key), str):
                rec[key] = payload[key][:40]
        sid = payload.get("session_id")
        if isinstance(sid, str) and SESSION_ID_RE.fullmatch(sid):
            rec["session_id"] = sid
        out = handle(payload, rec)
        if out is not None:
            emit(*out)
            rec["emitted"] = True
    except Exception as e:
        rec["error"] = sanitize_error(e)
    finally:
        write_log(rec)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
