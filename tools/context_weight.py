#!/usr/bin/env python3
"""context-weight — standing-context budget check for the Claude Code harness.

Measures what loads into EVERY session (skill descriptions, CLAUDE.md files,
MEMORY.md index, enabled plugins) and compares against declared thresholds.
Deterministic, no LLM, no network. Counterpart of the manual audit in
notes/2026-08-08-context-audit.md — run weekly so drift is caught, not re-audited.

Exit codes: 0 = clean, 1 = warnings, 2 = failures OR an input that could not
be measured. A missing input is NOT an empty one: with every path absent the
totals all come out 0, which is under every threshold, and the tool used to
print "CLEAN - within all budgets" and exit 0 having measured nothing. That is
not hypothetical - b640675 records MEMORY_MD pointing at a path that had not
existed for an unknown period while the tool kept reporting clean (2026-08-31
slop audit, MEDIUM-1). Unmeasured inputs are now listed by path and fail.

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
PROJECTS_DIR = HOME / ".claude" / "projects"
CLAUDE_MDS = [
    Path("F:/Claude-Profile/CLAUDE.md"),
    Path("F:/AI-Dev/CLAUDE.md"),
    Path("F:/BIMpossible/CLAUDE.md"),
]

DESC_WARN = 600          # per-skill description
DESC_FAIL = 900
DESC_TOTAL_WARN = 10_000 # all personal descriptions combined
MEMORY_WARN_BYTES = 20_000
MEMORY_FAIL_BYTES = 25_000   # harness guidance ceiling
MEMORY_FAIL_LINES = 200      # harness guidance ceiling
CLAUDEMD_WARN = 20_000       # all CLAUDE_MDS combined (recalibrated 2026-08-24, owner-approved)
TOTAL_WARN = 56_000          # everything standing, combined (recalibrated 2026-08-24)

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


def skill_descriptions(skills_dir: Path | None = None) -> tuple[list[tuple[str, int]], list[str]]:
    """-> (descriptions, unmeasured).

    An absent skills directory - or one holding no SKILL.md at all - means the
    largest standing-context source was not measured. It is reported by path,
    never returned as "0 skills, 0 chars".
    """
    skills_dir = SKILLS_DIR if skills_dir is None else skills_dir
    if not skills_dir.is_dir():
        return [], [f"skills directory not found: {skills_dir}"]
    out = []
    for d in sorted(skills_dir.iterdir()):
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return out, [f"could not read {f} ({e})"]
        m = re.match(r"---\n(.*?)\n---", text, re.S)
        fm = m.group(1) if m else ""
        dm = re.search(r"^description:\s*(.*?)(?=^\w+:|\Z)", fm, re.S | re.M)
        desc = dm.group(1).strip() if dm else ""
        out.append((d.name, len(desc)))
    if not out:
        return out, [f"no SKILL.md found under {skills_dir}"]
    return out, []


def main(skills_dir: Path | None = None, claude_mds: list[Path] | None = None,
         projects_dir: Path | None = None, settings_path: Path | None = None) -> int:
    # Defaults resolve at call time, so the module-level constants stay the
    # single source of truth and tests pass explicit paths instead of patching.
    skills_dir = SKILLS_DIR if skills_dir is None else skills_dir
    claude_mds = CLAUDE_MDS if claude_mds is None else claude_mds
    projects_dir = PROJECTS_DIR if projects_dir is None else projects_dir
    settings_path = SETTINGS if settings_path is None else settings_path

    warns: list[str] = []
    fails: list[str] = []
    # Inputs that could not be read at all. Kept apart from `fails` because the
    # honest verdict is "unknown", not "over budget" - but both exit non-zero,
    # and neither may print CLEAN.
    unmeasured: list[str] = []
    total = 0

    descs, missing = skill_descriptions(skills_dir)
    unmeasured += missing
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
    for p in claude_mds:
        if not p.is_file():
            unmeasured.append(f"CLAUDE.md not found: {p}")
            continue
        try:
            cm_total += len(p.read_bytes())
        except OSError as e:
            unmeasured.append(f"could not read {p} ({e})")
    total += cm_total
    if cm_total >= CLAUDEMD_WARN:
        warns.append(f"CLAUDE.md files {cm_total} bytes combined (>= {CLAUDEMD_WARN})")

    # Each project's index is checked against the ceilings, but only one loads
    # per session — the largest is the worst-case contribution to `total`.
    mem_worst = 0
    if not projects_dir.is_dir():
        unmeasured.append(f"projects directory not found: {projects_dir}")
        mems: list[Path] = []
    else:
        mems = sorted(projects_dir.glob("*/memory/MEMORY.md"))
        if not mems:
            unmeasured.append(f"no */memory/MEMORY.md found under {projects_dir}")
    for mem in mems:
        proj = mem.parent.parent.name
        mem_bytes = len(mem.read_bytes())
        mem_lines = mem.read_text(encoding="utf-8", errors="replace").count("\n") + 1
        mem_worst = max(mem_worst, mem_bytes)
        if mem_bytes >= MEMORY_FAIL_BYTES or mem_lines >= MEMORY_FAIL_LINES:
            fails.append(f"MEMORY.md ({proj}) {mem_bytes} bytes / {mem_lines} lines (ceiling {MEMORY_FAIL_BYTES}B / {MEMORY_FAIL_LINES} lines)")
        elif mem_bytes >= MEMORY_WARN_BYTES:
            warns.append(f"MEMORY.md ({proj}) {mem_bytes} bytes (>= {MEMORY_WARN_BYTES})")
    total += mem_worst

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        enabled = {k for k, v in (settings.get("enabledPlugins") or {}).items() if v}
        for extra in sorted(enabled - PLUGIN_ALLOWLIST):
            warns.append(f"plugin '{extra}' enabled but not in the audited allowlist")
    except (OSError, json.JSONDecodeError) as e:
        # The plugin-allowlist check did not run at all, so this is unmeasured
        # rather than a mere warning.
        unmeasured.append(f"could not read {settings_path} ({e})")

    if total >= TOTAL_WARN:
        warns.append(f"total standing context {total} bytes (>= {TOTAL_WARN})")

    print(f"standing context: {total} bytes (~{total // 4} tokens) | "
          f"descriptions {desc_total} | CLAUDE.md {cm_total} | skills {len(descs)}")
    for u in unmeasured:
        print(f"UNMEASURED: {u}")
    for f_ in fails:
        print(f"FAIL: {f_}")
    for w in warns:
        print(f"WARN: {w}")
    if unmeasured:
        print(f"NOT MEASURED — {len(unmeasured)} input(s) missing or unreadable; "
              "the totals above are incomplete and no budget verdict is possible")
    elif not fails and not warns:
        print("CLEAN — within all budgets")
    return 2 if (fails or unmeasured) else (1 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
