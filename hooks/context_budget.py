#!/usr/bin/env python3
"""
Context Intake Budget - PostToolUse hook.

Large tool outputs are spilled to a file and replaced, in context, by a compact
record plus a pointer. Small outputs pass through untouched.

WHY PostToolUse RATHER THAN PreToolUse
--------------------------------------
At PostToolUse the output size is a *fact*. At PreToolUse it can only be
estimated, and estimation fails in both directions: it truncates results that
mattered, and it waves through the 60k-token blobs it failed to predict.
PreToolUse remains useful for refusing known-catastrophic commands outright;
it is the wrong place to decide "inline vs spill".

WHY THIS EXISTS (measured, not assumed)
---------------------------------------
Across the owner's 25 largest sessions (51,569 messages):
  - the top 1% of tool results (93 of 9,327 calls) drove 64.6% of all context growth
  - cumulative cache_read was 7.46B tokens - anything admitted to context is
    re-billed on every subsequent turn of the session
  - only 8 compactions occurred, so *intake* is the bottleneck, not summarization

RELATIONSHIP TO THE HARNESS'S OWN SPILL (measured 2026-08-04, v2.1.178)
----------------------------------------------------------------------
Claude Code already persists *very* large tool results itself: it writes them to
<project>/tool-results/<id>.txt and shows a <persisted-output> wrapper with a 2KB
preview. Measured behaviour of that native path:

    293 KB  -> persisted natively
     40 KB  -> persisted natively
     11 KB  -> NOT persisted; entered context in full

So the native floor sits somewhere between 11 KB and 40 KB. This hook exists to
cover the band beneath it. An 11 KB result is ~2,700 tokens that the native path
lets through and that `cache_read` then re-bills on every subsequent turn.

That also makes the two mechanisms compose safely rather than fight: if the
harness has already replaced a huge result with its 2KB preview, that preview is
under this hook's threshold and passes straight through - no double spill.

Contract with the harness (verified against Claude Code 2.1.178, not docs):
  - stdin  : JSON payload; the result field is `tool_output` in the published
             schema, but the binary also contains `tool_response`. Both are read.
  - stdout : {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                     "updatedToolOutput": "..."}}
  - exit 0 with empty stdout == pass through unchanged.

TOOL RESULT SHAPES (measured 2026-08-07, live payload dumps across Bash,
PowerShell, Read, Grep, Glob). `tool_response` is not one shape - it is a
different dict per tool, so _resolve_text() dispatches structurally (by which
keys are present) rather than assuming one field name applies everywhere:

  Bash / PowerShell : {stdout, stderr, interrupted, isImage, ...}. stdout is
                       hard-truncated to 30000 chars by the harness regardless
                       of true size; when truncated, persistedOutputPath (+
                       persistedOutputSize) names a file under this machine's
                       own ~/.claude/projects/**/tool-results/ holding the
                       full text. The rendered "Output too large... Full
                       output saved to:" notice seen in transcripts is a
                       display-layer artifact - it never reaches this hook as
                       text; the pointer arrives as a plain dict field.
  Read              : {file: {filePath, content, numLines, startLine,
                       totalLines}, type}. content held the FULL file in
                       every case measured (up to ~50KB) - no persistence
                       field observed. Read also has its own hard client-side
                       cap (256KB) that errors before dispatch, so no
                       PostToolUse hook fires at all above that size.
  Grep              : {content, filenames, mode, numFiles, numLines,
                       totalLines}. No persistence field. Grep applies its
                       own truncation to individual over-long lines (replaced
                       in content with the literal "[Omitted long matching
                       line]") - unrelated to the harness spill mechanism and
                       not recoverable from the payload; content is already
                       the best available text.
  Glob              : {filenames, numFiles, totalMatches, truncated,
                       countIsComplete, durationMs}. No single text field;
                       filenames (one path per line) is the real payload.
  ToolSearch, WebFetch, WebSearch, TaskOutput, mcp__* : not yet confirmed
                       against a live large payload. _resolve_text() falls
                       back to a json.dumps() of the whole dict for any shape
                       it does not recognise, rather than guessing a field
                       name - worst case is the pre-fix behaviour, not a
                       crash.

Failure policy: this hook must never break a session. Any unexpected error is
logged to context_budget.error.log and the output passes through UNCHANGED.
It fails open and loudly, never silently.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# --- configuration -----------------------------------------------------------
# Thresholds are in characters. ~4 chars per token is a rough but adequate proxy;
# the point is to catch the right tail, not to bill accurately.

DEFAULT_LIMIT = int(os.environ.get("CLAUDE_CONTEXT_BUDGET_CHARS", "8000"))

# Per-tool overrides. Read gets more room because file contents are usually the
# actual deliverable; ToolSearch gets less because MCP schema dumps averaged
# ~61k tokens per call and are the single worst offender per call.
PER_TOOL_LIMIT = {
    "Read": 12000,
    "ToolSearch": 6000,
    "Grep": 10000,
}

# Allowlist, not denylist. An allowlist is the safe default for an unattended
# change: a tool whose result is a control signal (task tools, plan mode,
# permission prompts) must never have its output rewritten. Everything measured
# as a real offender is here.
SPILL_TOOLS = {
    "Bash", "PowerShell", "Read", "Grep", "Glob",
    "ToolSearch", "WebFetch", "WebSearch", "TaskOutput",
}

# MCP tools are included by prefix (mcp__server__tool).
SPILL_MCP_PREFIX = "mcp__"

HEAD_LINES = 25
TAIL_LINES = 12
PRUNE_AFTER_DAYS = 7

# Hard character ceilings on the excerpt. Line counts alone do NOT bound size:
# 40 lines of minified JS or a single-line JSON dump would produce an excerpt
# larger than the output it replaced. These caps are the actual guarantee.
#
# Sized deliberately small. A generous excerpt defeats the purpose near the
# threshold: with a 3.7k-char excerpt, an 11k-char output compressed only 3x,
# which does not repay the round trip needed to recover the rest. At ~2.3k the
# same output compresses ~8x, and the excerpt is still enough to decide whether
# the full file is worth opening - which is all it needs to do.
HEAD_CHARS = 1400
TAIL_CHARS = 700
MAX_LINE_CHARS = 400

MARKER = "[context-budget]"


def _log_error(msg: str) -> None:
    """Record a failure without ever interrupting the session."""
    try:
        p = Path(__file__).with_name("context_budget.error.log")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _passthrough() -> None:
    """Leave the tool output exactly as it was."""
    sys.exit(0)


def _as_text(value) -> str:
    """Tool results arrive as str, or as structured content blocks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


_TOOL_RESULTS_ROOT = Path.home() / ".claude" / "projects"


def _read_if_safe(path_str) -> str | None:
    """Read a harness-persisted-output file, but only when it resolves under
    this machine's own tool-results tree. The path traces back to harness
    data, but a command's own stdout is attacker-influenceable in principle,
    so this containment check is what stops a forged path from becoming an
    arbitrary local file read. Ported from ctxdex_autoindex.py's identical
    helper - keep the two in sync if this check ever changes."""
    try:
        candidate = Path(str(path_str).strip()).resolve()
        safe_prefix = str(_TOOL_RESULTS_ROOT.resolve()).lower() + os.sep
        if not str(candidate).lower().startswith(safe_prefix):
            return None
        if candidate.parent.name != "tool-results" or not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve_text(output) -> str:
    """Resolve the true output text from a `tool_output`/`tool_response`
    value. See the module docstring's TOOL RESULT SHAPES section - the dict
    shape differs per tool, and there is no single field that is always
    "the text": a blind json.dumps() of the whole dict (the old behaviour)
    can return a persistedOutputPath pointer instead of content, and even
    when a dict already holds full text inline, JSON-escaping mangles the
    head/tail line-splitting downstream. Detection is structural (which keys
    are present), not by tool name, so it generalises to any tool sharing a
    shape without needing to enumerate every tool.
    """
    if not isinstance(output, dict):
        return _as_text(output)

    # Bash / PowerShell, and anything else the harness truncated: the pointer
    # is a plain dict field, never rendered text, and is only ever present
    # once stdout was actually cut - safe to check unconditionally.
    persisted_path = output.get("persistedOutputPath")
    if persisted_path:
        resolved = _read_if_safe(persisted_path)
        if resolved is not None:
            return resolved

    # Bash / PowerShell shape: {stdout, stderr, ...}.
    if isinstance(output.get("stdout"), str):
        parts = [output.get("stdout") or ""]
        if output.get("stderr"):
            parts.append(output["stderr"])
        return "\n".join(p for p in parts if p)

    # Read shape: {file: {filePath, content, numLines, startLine, totalLines}, type}.
    file_field = output.get("file")
    if isinstance(file_field, dict) and isinstance(file_field.get("content"), str):
        return file_field["content"]

    # Glob shape: {filenames: [...], numFiles, totalMatches, truncated, ...} -
    # the path list, one per line, is the real payload; the rest is counts.
    filenames = output.get("filenames")
    if isinstance(filenames, list) and filenames and all(isinstance(f, str) for f in filenames):
        return "\n".join(filenames)

    # Grep shape (and anything else with a plain text `content` field):
    # {content, filenames, mode, numFiles, numLines, totalLines}.
    content_field = output.get("content")
    if isinstance(content_field, str):
        return content_field

    # Unrecognised dict shape (ToolSearch, WebFetch, WebSearch, TaskOutput,
    # mcp__* - not yet confirmed against a live large payload): fall back to
    # the previous behaviour rather than guessing at a field name.
    return _as_text(output)


def _limit_for(tool_name: str) -> int:
    return PER_TOOL_LIMIT.get(tool_name, DEFAULT_LIMIT)


def _should_consider(tool_name: str) -> bool:
    return tool_name in SPILL_TOOLS or tool_name.startswith(SPILL_MCP_PREFIX)


def _scratch_dir(cwd: str) -> Path:
    d = Path(cwd) / ".claude" / "scratch"
    d.mkdir(parents=True, exist_ok=True)
    # Self-ignoring: keeps spill files out of git without touching the repo's
    # own .gitignore. Written once; cheap to re-check.
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    return d


def _prune(d: Path) -> None:
    """Spill files are disposable. Do not grow a second _backups/ pile."""
    cutoff = time.time() - PRUNE_AFTER_DAYS * 86400
    try:
        for f in d.glob("tool_*.log"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except Exception as exc:
        _log_error(f"prune failed: {exc!r}")


def _clip_lines(lines: list[str], budget: int) -> str:
    """Join lines under a hard character budget, clipping over-long lines.

    Both bounds matter: many lines of normal width, or few lines of absurd
    width, must produce the same order-of-magnitude excerpt.
    """
    out: list[str] = []
    used = 0
    for ln in lines:
        if len(ln) > MAX_LINE_CHARS:
            ln = ln[:MAX_LINE_CHARS] + f" ...[+{len(ln) - MAX_LINE_CHARS:,} chars]"
        if used + len(ln) + 1 > budget:
            out.append("...[excerpt truncated - see FULL OUTPUT]")
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def _slug(tool_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", tool_name)[:40]


def _describe_input(tool_input) -> str:
    """One short line naming what produced this, so the record is self-explaining."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "pattern", "query", "url", "path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            val = val.strip().replace("\n", " ")
            return f"{key}: {val[:200]}"
    return ""


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        _passthrough()

    payload = json.loads(raw)

    tool_name = payload.get("tool_name") or ""
    if not _should_consider(tool_name):
        _passthrough()

    # The published schema says `tool_output`; the binary also carries
    # `tool_response`. Read both rather than betting on one - a wrong field name
    # here would make this hook silently do nothing, which is the exact failure
    # mode it exists to prevent.
    output = payload.get("tool_output")
    if output in (None, ""):
        output = payload.get("tool_response")
    text = _resolve_text(output)

    if not text:
        _passthrough()

    # Idempotence: never spill a spill record.
    if text.lstrip().startswith(MARKER):
        _passthrough()

    limit = _limit_for(tool_name)
    if len(text) <= limit:
        _passthrough()

    cwd = payload.get("cwd") or os.getcwd()
    d = _scratch_dir(cwd)
    _prune(d)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = d / f"tool_{stamp}_{_slug(tool_name)}.log"
    path.write_text(text, encoding="utf-8", errors="replace")

    lines = text.splitlines()
    approx_tokens = len(text) // 4
    head = _clip_lines(lines[:HEAD_LINES], HEAD_CHARS)
    tail = (
        _clip_lines(lines[-TAIL_LINES:], TAIL_CHARS)
        if len(lines) > HEAD_LINES + TAIL_LINES
        else ""
    )

    is_error = bool(payload.get("tool_output_is_error"))
    origin = _describe_input(payload.get("tool_input"))

    parts = [
        f"{MARKER} Output was ~{approx_tokens:,} tokens "
        f"({len(text):,} chars, {len(lines):,} lines) and was kept out of context.",
    ]
    if is_error:
        parts.append("This tool reported an ERROR. The full text is preserved in the file below.")
    if origin:
        parts.append(f"Produced by -> {origin}")
    parts.append(f"FULL OUTPUT: {path}")
    parts.append("")
    parts.append(f"--- first {min(HEAD_LINES, len(lines))} lines ---")
    parts.append(head)
    if tail:
        parts.append(f"--- last {TAIL_LINES} lines ---")
        parts.append(tail)
    # Deliberately terse: this trailer is paid on every single spill, and the
    # context-budget skill already explains the rule at length. A 250-char
    # reminder repeated across a session is the very cost this hook exists to cut.
    parts.append("")
    parts.append("Search this file, do not re-read it whole (Grep, or Read with offset/limit).")

    replacement = "\n".join(parts)

    hook_out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": replacement,
        }
    }
    # The binary contains a distinct `updatedMCPToolOutput` alongside
    # `updatedToolOutput`. The published docs say MCP uses the same field. Emit
    # both for MCP tools; an unrecognised key is ignored, a missing one is not.
    if tool_name.startswith(SPILL_MCP_PREFIX):
        hook_out["hookSpecificOutput"]["updatedMCPToolOutput"] = replacement

    sys.stdout.write(json.dumps(hook_out))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail open, never break the session
        _log_error(f"unhandled: {exc!r}")
        sys.exit(0)
