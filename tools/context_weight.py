#!/usr/bin/env python3
"""context-weight — standing-context budget check for the Claude Code harness.

Measures what loads into EVERY session (skill descriptions, CLAUDE.md files,
MEMORY.md index, enabled plugins) and compares against declared thresholds.
Deterministic, no LLM, no network. Counterpart of the manual audit in
notes/2026-08-08-context-audit.md — run weekly so drift is caught, not re-audited.

Exit codes: 0 = clean, 1 = warnings, 2 = failures.

Thresholds (chars unless noted). Rationale: a description is trigger + sibling
routing only; bodies are free (load on invoke). 4 chars ~ 1 token.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HOME = Path.home()
SKILLS_DIR = HOME / ".claude" / "skills"
SETTINGS = HOME / ".claude" / "settings.json"
MEMORY_MD = HOME / ".claude" / "projects" / "F--AI-Dev" / "memory" / "MEMORY.md"
CLAUDE_MDS = [
    Path("F:/AI-Dev/claude-profile/CLAUDE.md"),
    Path("F:/AI-Dev/CLAUDE.md"),
]

DESC_WARN = 600          # per-skill description
DESC_FAIL = 900
DESC_TOTAL_WARN = 10_000 # all personal descriptions combined
MEMORY_WARN_BYTES = 20_000
MEMORY_FAIL_BYTES = 25_000   # harness guidance ceiling
MEMORY_FAIL_LINES = 200      # harness guidance ceiling
CLAUDEMD_WARN = 8_000        # both files combined
TOTAL_WARN = 32_000          # everything standing, combined

# Plugins expected enabled. Anything enabled but not listed => WARN (new context
# source appeared without an audit). Update this list deliberately when adopting one.
PLUGIN_ALLOWLIST = {
    "typescript-lsp@claude-plugins-official",
    "pyright-lsp@claude-plugins-official",
    "csharp-lsp@claude-plugins-official",
    "context7@claude-plugins-official",
    "claude-md-management@claude-plugins-official",
    "security-guidance@claude-code-plugins",
}


def skill_descriptions() -> list[tuple[str, int]]:
    out = []
    if not SKILLS_DIR.is_dir():
        return out
    for d in sorted(SKILLS_DIR.iterdir()):
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"---\n(.*?)\n---", text, re.S)
        fm = m.group(1) if m else ""
        dm = re.search(r"^description:\s*(.*?)(?=^\w+:|\Z)", fm, re.S | re.M)
        desc = dm.group(1).strip() if dm else ""
        out.append((d.name, len(desc)))
    return out


def main() -> int:
    warns: list[str] = []
    fails: list[str] = []
    total = 0

    descs = skill_descriptions()
    desc_total = sum(n for _, n in descs)
    total += desc_total
    for name, n in descs:
        if n >= DESC_FAIL:
            fails.append(f"skill '{name}' description {n} chars (>= {DESC_FAIL})")
        elif n >= DESC_WARN:
            warns.append(f"skill '{name}' description {n} chars (>= {DESC_WARN})")
    if desc_total >= DESC_TOTAL_WARN:
        warns.append(f"all skill descriptions {desc_total} chars (>= {DESC_TOTAL_WARN})")

    cm_total = 0
    for p in CLAUDE_MDS:
        if p.is_file():
            cm_total += len(p.read_bytes())
    total += cm_total
    if cm_total >= CLAUDEMD_WARN:
        warns.append(f"CLAUDE.md files {cm_total} bytes combined (>= {CLAUDEMD_WARN})")

    if MEMORY_MD.is_file():
        mem_bytes = len(MEMORY_MD.read_bytes())
        mem_lines = MEMORY_MD.read_text(encoding="utf-8", errors="replace").count("\n") + 1
        total += mem_bytes
        if mem_bytes >= MEMORY_FAIL_BYTES or mem_lines >= MEMORY_FAIL_LINES:
            fails.append(f"MEMORY.md {mem_bytes} bytes / {mem_lines} lines (ceiling {MEMORY_FAIL_BYTES}B / {MEMORY_FAIL_LINES} lines)")
        elif mem_bytes >= MEMORY_WARN_BYTES:
            warns.append(f"MEMORY.md {mem_bytes} bytes (>= {MEMORY_WARN_BYTES})")

    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        enabled = {k for k, v in (settings.get("enabledPlugins") or {}).items() if v}
        for extra in sorted(enabled - PLUGIN_ALLOWLIST):
            warns.append(f"plugin '{extra}' enabled but not in the audited allowlist")
    except (OSError, json.JSONDecodeError) as e:
        warns.append(f"could not read settings.json ({e})")

    if total >= TOTAL_WARN:
        warns.append(f"total standing context {total} bytes (>= {TOTAL_WARN})")

    print(f"standing context: {total} bytes (~{total // 4} tokens) | "
          f"descriptions {desc_total} | CLAUDE.md {cm_total} | skills {len(descs)}")
    for f_ in fails:
        print(f"FAIL: {f_}")
    for w in warns:
        print(f"WARN: {w}")
    if not fails and not warns:
        print("CLEAN — within all budgets")
    return 2 if fails else (1 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
