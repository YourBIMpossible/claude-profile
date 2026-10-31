#!/usr/bin/env python3
"""context-weight — scenario-aware standing-context budget & coverage report.

Phase 1 of the context-budget audit improvement program
(notes/2026-09-21-context-audit-review.md). This is a MEASUREMENT tool: it
reports what is actually loading and what remains unmeasured; it never
edits, trims, or otherwise touches any content source. Deterministic, no
LLM, no network.

Schema v2 supersedes the v1 single-total model from
notes/2026-08-08-context-audit.md / commit 96393be. Three defects drove the
rewrite (see the review note for the full account):

  1. MEMORY.md was reported only as "the worst of all projects, by
     subtraction from a combined total" — never measured directly.
  2. The BIMpossible project's own CLAUDE.md was summed into every
     session's number, though it only loads inside that project.
  3. "CLEAN" covered only ~15 personal skills. Everything else that loads
     every session — enabled-plugin skills, the ~/.claude/CLAUDE.md
     wrapper, hosted/runtime-injected skills, MCP instructions, the
     deferred-tool catalog, agent descriptions, the SessionStart hook's
     injected preview — was invisible to the tool and to "CLEAN".

Design (see also notes/2026-09-21-context-audit-review.md §4):

  - Every context source has one of four states: MEASURED, UNMEASURED,
    NOT_APPLICABLE, MISSING. A source marked `required` gates its
    scenario(s): MISSING or UNMEASURED on a required source makes that
    scenario's total incomplete (reported, never silently zeroed) and the
    run cannot report clean. Optional/discovered sources that are
    UNMEASURED are visible in the coverage inventory but do not block a
    verdict — that is the honest state for "hosted, not locally
    inspectable," not a scan failure.
  - Two independent scenario totals: global_session (loads in every
    session everywhere) and bimpossible_session (global_session + what
    additionally loads inside {repos}\\BIMpossible). BIMpossible-only content
    is never charged to global_session.
  - Scenario totals are TELEMETRY, not a gate: the 32 KB design target and
    the 45 KB / 56 KB reference lines are this tool's own policy numbers,
    not a Claude Code limit, so crossing one is reported (INFO) but never
    warns or fails the run. Real limits still fail per source — above all
    Claude Code's MEMORY.md startup truncation (25 KB / 200 lines).
  - Runs append one record to tools/context_weight.history.jsonl (gitignored,
    machine-local) so growth is visible as a trend, not just a snapshot.

Exit codes (2026-09-30 contract):
  0 = measurement complete, no hard failure. Advisory warnings (per-source
      WARN/NEAR-LIMIT, per-skill description WARN, unlisted plugin, trend
      growth) are still printed and the VERDICT line says ADVISORY, but
      they do not fail the run.
  2 = a hard failure (a per-source fail_at such as MEMORY.md 25 KB / 200
      lines, a per-skill description over DESC_FAIL) OR a required input
      that could not be measured (never reported as clean, never silently
      read as zero).
  1 is no longer emitted.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 2

HOME = Path.home()

# ---------------------------------------------------------------- defaults
# Real-world paths. Every function that reads one of these takes it as a
# parameter with this module constant as the default, so tests pass throwaway
# fixture paths and never touch the real ~/.claude or repo files.
SKILLS_DIR = HOME / ".claude" / "skills"
WRAPPER_CLAUDE_MD = HOME / ".claude" / "CLAUDE.md"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import machine_config  # noqa: E402

GLOBAL_CLAUDE_MD = machine_config.PROFILE_ROOT / "CLAUDE.md"


def _bimpossible_claude_md() -> Path:
    try:
        repos = machine_config.load()["repos_root"]
    except machine_config.ConfigError:
        repos = str(machine_config.detect_repos_root())
    return Path(repos) / "BIMpossible" / "CLAUDE.md"


BIMPOSSIBLE_CLAUDE_MD = _bimpossible_claude_md()
PROJECTS_DIR = HOME / ".claude" / "projects"
# Claude Code keys each project's memory by an encoded form of its working
# directory. These two are the fixed anchors this tool tracks: the "home"
# profile project (stand-in for any session not inside a named project) and
# BIMpossible (the one project with its own CLAUDE.md tracked here). If
# either directory is ever renamed, this becomes MISSING and says so by
# path — it is never silently read as "no memory".
GLOBAL_MEMORY_MD = PROJECTS_DIR / "F--Claude-Profile" / "memory" / "MEMORY.md"
BIMPOSSIBLE_MEMORY_MD = PROJECTS_DIR / "F--BIMpossible" / "memory" / "MEMORY.md"
SETTINGS = HOME / ".claude" / "settings.json"
PLUGINS_DIR = HOME / ".claude" / "plugins"
HISTORY_PATH = Path(__file__).resolve().parent / "context_weight.history.jsonl"

GLOBAL = "global_session"
BIMPOSSIBLE = "bimpossible_session"
SCENARIOS = (GLOBAL, BIMPOSSIBLE)

# ------------------------------------------------------------- thresholds
# Rationale: notes/2026-08-08-context-audit.md + notes/2026-09-21-context-
# audit-review.md. 4 chars ~ 1 token.
DESIGN_TARGET_BYTES = 32_000   # aspirational; informational only, NEVER gates,
                                # NEVER a deletion quota (see module docstring)
SCENARIO_WARN_BYTES = 45_000   # per-scenario reference line; INFO only, never gates
SCENARIO_FAIL_BYTES = 56_000   # per-scenario reference line; INFO only, never gates
NEAR_LIMIT_RATIO = 0.90        # warn at 90% of whichever ceiling applies
TREND_GROWTH_WARN_RATIO = 0.10  # warn when a comparable prior run grew >10%

DESC_WARN = 600                 # per-skill description (unchanged from v1)
DESC_FAIL = 900
DESC_TOTAL_WARN = 10_000        # personal skill descriptions, combined
PLUGIN_DESC_TOTAL_WARN = 6_000  # enabled-plugin skill descriptions, combined
CLAUDEMD_WARN = 20_000          # per CLAUDE.md aggregate (wrapper+profile, or a project file)
# Claude Code loads "the first 200 lines of MEMORY.md, or the first 25KB,
# whichever comes first" (https://code.claude.com/docs/en/memory, verified
# 2026-10-13). Both limits are what still loads, so FAIL only when a count
# exceeds them: line 201+ or byte 25_001+ is lost. "25KB" is read as 25_000
# bytes, the conservative reading (25_600 if Claude Code means KiB).
MEMORY_WARN_BYTES = 20_000
MEMORY_FAIL_BYTES = 25_000      # max bytes that load; FAIL when size > this
MEMORY_FAIL_LINES = 200         # max lines that load; FAIL when count > this

# Plugins expected enabled. Anything enabled but not listed => WARN (new
# context source appeared without an audit). Update deliberately when
# adopting one.
PLUGIN_ALLOWLIST = {
    "typescript-lsp@claude-plugins-official",
    "pyright-lsp@claude-plugins-official",
    "csharp-lsp@claude-plugins-official",
    "context7@claude-plugins-official",
    "claude-md-management@claude-plugins-official",
    "security-guidance@claude-code-plugins",
    "hookify@claude-plugins-official",
    "plugin-dev@claude-code-plugins",
    "pr-review-toolkit@claude-code-plugins",
    "roadmap-band@owner-local",
}

# Context sources known — from direct observation of live sessions — to load
# every session but that are shipped by the client/harness itself, not by any
# file under ~/.claude. A deterministic, offline, repo-local tool has no file
# to read for these, so they are inventoried by category rather than
# fabricated a byte count. Never counted in any scenario total.
HOSTED_UNMEASURABLE = [
    ("plugin_or_hosted_skill_catalog_beyond_local",
     "Skills that appear in every session's available-skills list but have no "
     "SKILL.md under ~/.claude/skills or a locally cached, enabled plugin "
     "(observed: the anthropic-skills bundle — docs, docx, pptx, xlsx, pdf, "
     "code-review, simplify, dataviz, claude-api, artifact-design and others — "
     "plus cowork-plugin-management). Shipped by the client/harness itself."),
    ("mcp_server_instructions",
     "Per-server instruction blocks injected for whichever MCP servers are "
     "connected this session. Size and presence depend on session-local "
     "connector state, not on any file this tool can read."),
    ("deferred_tool_catalog",
     "The deferred-tool name list (order of ~200-250 names in a typical "
     "session) and the full schemas later fetched via ToolSearch. "
     "Harness-injected at runtime."),
    ("agent_type_descriptions",
     "The Agent tool's available-agent-types listing. Harness-injected, not "
     "backed by a local file."),
]


# =========================================================================
# data model
# =========================================================================

@dataclass
class Source:
    key: str
    label: str
    bytes: Optional[int]
    state: str  # MEASURED | UNMEASURED | NOT_APPLICABLE | MISSING
    scenarios: tuple
    required: bool = False
    reason: Optional[str] = None
    detail: Optional[str] = None
    warn_at: Optional[int] = None
    fail_at: Optional[int] = None
    ceiling_kind: Optional[str] = None  # label for what warn_at/fail_at mean
    # False: FAIL at n >= fail_at (default). True: fail_at is the largest
    # value that still fits (MEMORY.md load limits), so FAIL only at n > it.
    fail_when_over: bool = False

    @property
    def ceiling(self) -> Optional[int]:
        return self.fail_at if self.fail_at is not None else self.warn_at


# =========================================================================
# low-level readers — never turn "could not read" into 0
# =========================================================================

def _read_bytes(path: Path) -> tuple:
    """-> (byte_count | None, error | None). None count means NOT measured,
    never 0. Distinguishes "nothing at this path" (MISSING) from "something
    is there but isn't a readable file, e.g. a directory" (UNMEASURED) —
    callers key off whether the error string starts with "not found"."""
    if not path.exists():
        return None, f"not found: {path}"
    if not path.is_file():
        return None, f"could not read {path} (not a regular file)"
    try:
        return len(path.read_bytes()), None
    except OSError as e:
        return None, f"could not read {path} ({e})"


def _line_count(text: str) -> int:
    """LF-separated line count: a trailing LF ends the last line rather than
    opening an empty one, "" is 0 lines, CRLF counts once, and a lone CR is
    not a line break."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _read_text_and_lines(path: Path) -> tuple:
    """-> (text | None, byte_count | None, line_count | None, error | None).
    Same MISSING-vs-UNMEASURED distinction as _read_bytes()."""
    if not path.exists():
        return None, None, None, f"not found: {path}"
    if not path.is_file():
        return None, None, None, f"could not read {path} (not a regular file)"
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        return text, len(raw), _line_count(text), None
    except OSError as e:
        return None, None, None, f"could not read {path} ({e})"


def skill_descriptions(skills_dir: Path) -> tuple:
    """-> (descriptions:[(name, chars)], unmeasured:[str]).

    An absent skills directory - or one holding no SKILL.md at all - means
    the largest standing-context source was not measured. Reported by path,
    never returned as "0 skills, 0 chars".
    """
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


def plugin_skill_descriptions(enabled_plugins: set, plugins_dir: Path) -> tuple:
    """-> (descriptions:[(plugin, skill, chars)], per_plugin_status:{plugin: 'found'|'not_applicable'}, unmeasured:[str]).

    Only plugins in `enabled_plugins` are searched. A plugin with no
    SKILL.md anywhere under it (e.g. an LSP/MCP-only plugin) is
    NOT_APPLICABLE, not unmeasured — its skill-description cost is
    genuinely zero, which is different from "could not check".
    """
    if not enabled_plugins:
        return [], {}, []
    if not plugins_dir.is_dir():
        return [], {}, [f"plugins directory not found: {plugins_dir}"]
    out = []
    status = {}
    unmeasured = []
    for key in sorted(enabled_plugins):
        name = key.split("@", 1)[0]
        found = sorted(plugins_dir.glob(f"cache/*/{name}/*/skills/*/SKILL.md"))
        if not found:
            found = sorted(plugins_dir.glob(f"marketplaces/*/plugins/{name}/skills/*/SKILL.md"))
        if not found:
            status[key] = "not_applicable"
            continue
        any_ok = False
        for f in found:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                unmeasured.append(f"could not read {f} ({e})")
                continue
            m = re.match(r"---\n(.*?)\n---", text, re.S)
            fm = m.group(1) if m else ""
            dm = re.search(r"^description:\s*(.*?)(?=^\w+:|\Z)", fm, re.S | re.M)
            desc = dm.group(1).strip() if dm else ""
            out.append((key, f.parent.name, len(desc)))
            any_ok = True
        status[key] = "found" if any_ok else "not_applicable"
    return out, status, unmeasured


def _enabled_plugins(settings_path: Path) -> tuple:
    """-> (enabled:set|None, error:str|None)."""
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, f"could not read {settings_path} ({e})"
    enabled = {k for k, v in (settings.get("enabledPlugins") or {}).items() if v}
    return enabled, None


def _git_sha(repo_dir: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except Exception:
        pass
    return None


# =========================================================================
# near-limit / warn / fail evaluation for one source
# =========================================================================

def _evaluate_ceiling(n: int, warn_at: Optional[int], fail_at: Optional[int],
                      fail_when_over: bool = False) -> Optional[str]:
    """-> 'FAIL' | 'WARN' | 'NEAR-LIMIT' | None.

    fail_when_over=True treats fail_at as the largest allowed value (FAIL
    only when n > fail_at); the default fails at n >= fail_at."""
    ceiling = fail_at if fail_at is not None else warn_at
    if fail_at is not None and (n > fail_at if fail_when_over else n >= fail_at):
        return "FAIL"
    if warn_at is not None and n >= warn_at:
        return "WARN"
    if ceiling is not None and n >= ceiling * NEAR_LIMIT_RATIO:
        return "NEAR-LIMIT"
    return None


# =========================================================================
# main scan
# =========================================================================

def run_scan(*,
             skills_dir: Optional[Path] = None,
             wrapper_claude_md: Optional[Path] = None,
             global_claude_md: Optional[Path] = None,
             bimpossible_claude_md: Optional[Path] = None,
             global_memory_md: Optional[Path] = None,
             bimpossible_memory_md: Optional[Path] = None,
             settings_path: Optional[Path] = None,
             plugins_dir: Optional[Path] = None,
             history_path: Optional[Path] = None,
             repo_dir: Optional[Path] = None,
             now: Optional[datetime] = None,
             write_history: bool = True) -> dict:
    """Pure(ish) scan: reads local files, never mutates anything but (when
    write_history) appending one line to history_path. Returns a structured
    result; render_report() turns it into the printed text."""
    skills_dir = SKILLS_DIR if skills_dir is None else skills_dir
    wrapper_claude_md = WRAPPER_CLAUDE_MD if wrapper_claude_md is None else wrapper_claude_md
    global_claude_md = GLOBAL_CLAUDE_MD if global_claude_md is None else global_claude_md
    bimpossible_claude_md = BIMPOSSIBLE_CLAUDE_MD if bimpossible_claude_md is None else bimpossible_claude_md
    global_memory_md = GLOBAL_MEMORY_MD if global_memory_md is None else global_memory_md
    bimpossible_memory_md = BIMPOSSIBLE_MEMORY_MD if bimpossible_memory_md is None else bimpossible_memory_md
    settings_path = SETTINGS if settings_path is None else settings_path
    plugins_dir = PLUGINS_DIR if plugins_dir is None else plugins_dir
    history_path = HISTORY_PATH if history_path is None else history_path
    repo_dir = Path(__file__).resolve().parent.parent if repo_dir is None else repo_dir
    now = now or datetime.now(timezone.utc)

    sources: list = []
    per_skill_issues: list = []  # (name, chars, 'WARN'|'FAIL')
    plugin_allowlist_warns: list = []

    # ---- personal skill descriptions (global_session + bimpossible_session)
    descs, missing = skill_descriptions(skills_dir)
    if missing:
        sources.append(Source(
            key="personal_skill_description_bytes",
            label=f"personal skill descriptions ({skills_dir})",
            bytes=None, state="MISSING", scenarios=SCENARIOS, required=True,
            reason=missing[0],
        ))
    else:
        total = sum(n for _, n in descs)
        for name, n in descs:
            if n >= DESC_FAIL:
                per_skill_issues.append((name, n, "FAIL"))
            elif n >= DESC_WARN:
                per_skill_issues.append((name, n, "WARN"))
        sources.append(Source(
            key="personal_skill_description_bytes",
            label=f"personal skill descriptions ({len(descs)} skills)",
            bytes=total, state="MEASURED", scenarios=SCENARIOS, required=True,
            warn_at=DESC_TOTAL_WARN, ceiling_kind="combined personal descriptions",
        ))

    # ---- global CLAUDE.md = ~/.claude/CLAUDE.md wrapper + the @-imported
    # {profile}\CLAUDE.md it pulls in. Both load in every session.
    wb, werr = _read_bytes(wrapper_claude_md)
    pb, perr = _read_bytes(global_claude_md)
    if werr or perr:
        errs = [e for e in (werr, perr) if e]
        state = "MISSING" if any(e.startswith("not found") for e in errs) else "UNMEASURED"
        sources.append(Source(
            key="global_claude_md_bytes",
            label="global CLAUDE.md (wrapper + Claude-Profile)",
            bytes=None, state=state, scenarios=SCENARIOS, required=True,
            reason="; ".join(errs),
        ))
    else:
        sources.append(Source(
            key="global_claude_md_bytes",
            label="global CLAUDE.md (wrapper + Claude-Profile)",
            bytes=wb + pb, state="MEASURED", scenarios=SCENARIOS, required=True,
            detail=f"wrapper {wb}B ({wrapper_claude_md}) + profile {pb}B ({global_claude_md})",
            warn_at=CLAUDEMD_WARN, ceiling_kind="global CLAUDE.md combined",
        ))

    # ---- BIMpossible project CLAUDE.md (bimpossible_session only)
    bb, berr = _read_bytes(bimpossible_claude_md)
    if berr:
        state = "MISSING" if berr.startswith("not found") else "UNMEASURED"
        sources.append(Source(
            key="project_claude_md_bytes",
            label=f"project CLAUDE.md ({bimpossible_claude_md})",
            bytes=None, state=state, scenarios=(BIMPOSSIBLE,), required=True,
            reason=berr,
        ))
    else:
        sources.append(Source(
            key="project_claude_md_bytes",
            label=f"project CLAUDE.md ({bimpossible_claude_md})",
            bytes=bb, state="MEASURED", scenarios=(BIMPOSSIBLE,), required=True,
            warn_at=CLAUDEMD_WARN, ceiling_kind="project CLAUDE.md",
        ))

    # ---- MEMORY.md, measured directly per project — never by subtraction
    #
    # global_memory_bytes (Claude-Profile's MEMORY.md) is scoped to GLOBAL
    # only, not SCENARIOS. Verified 2026-09-21 (pre-Phase-2 load-model
    # check): Claude Code's memory system is keyed per encoded session cwd
    # (~/.claude/projects/<encoded-cwd>/memory/MEMORY.md) — a session
    # rooted at {repos}\BIMpossible reads that folder's own MEMORY.md, never
    # Claude-Profile's. No @import, hook, or settings.json entry injects
    # Claude-Profile's MEMORY.md content into a BIMpossible session; see
    # notes/2026-09-21-context-audit-phase-1-baseline.md §12 for the
    # evidence trail. Do not restore SCENARIOS here without new evidence
    # meeting that section's proof bar.
    for key, label, path, scenarios in (
        ("global_memory_bytes", "global memory index (Claude-Profile project)", global_memory_md, (GLOBAL,)),
        ("project_memory_bytes", "BIMpossible project memory index", bimpossible_memory_md, (BIMPOSSIBLE,)),
    ):
        text, nbytes, nlines, err = _read_text_and_lines(path)
        if err:
            state = "MISSING" if "not found" in err else "UNMEASURED"
            sources.append(Source(
                key=key, label=f"{label} ({path})", bytes=None, state=state,
                scenarios=scenarios, required=True, reason=err,
            ))
            continue
        s = Source(
            key=key, label=f"{label} ({path})", bytes=nbytes, state="MEASURED",
            scenarios=scenarios, required=True,
            detail=f"{nlines} lines",
            warn_at=MEMORY_WARN_BYTES, fail_at=MEMORY_FAIL_BYTES,
            ceiling_kind="MEMORY.md bytes", fail_when_over=True,
        )
        sources.append(s)
        if nlines > MEMORY_FAIL_LINES:
            # Line-count ceiling is independent of the byte ceiling; surfaced
            # as its own fail note rather than folded into byte-based state.
            s.reason = (s.reason or "")
            per_skill_issues.append((f"{key} line count", nlines, "FAIL-LINES"))

    # ---- enabled plugins: allowlist + their skill descriptions (global_session + bimpossible_session)
    enabled, senerr = _enabled_plugins(settings_path)
    if senerr:
        sources.append(Source(
            key="plugin_skill_description_bytes",
            label="enabled-plugin skill descriptions",
            bytes=None, state="UNMEASURED", scenarios=SCENARIOS, required=True,
            reason=senerr,
        ))
    else:
        for extra in sorted(enabled - PLUGIN_ALLOWLIST):
            plugin_allowlist_warns.append(extra)
        pdescs, pstatus, punmeasured = plugin_skill_descriptions(enabled, plugins_dir)
        if punmeasured and not pdescs and not pstatus:
            sources.append(Source(
                key="plugin_skill_description_bytes",
                label="enabled-plugin skill descriptions",
                bytes=None, state="UNMEASURED", scenarios=SCENARIOS, required=True,
                reason="; ".join(punmeasured),
            ))
        else:
            ptotal = sum(n for _, _, n in pdescs)
            found_plugins = sorted({p for p, _, _ in pdescs})
            na_plugins = sorted(p for p, st in pstatus.items() if st == "not_applicable")
            detail_bits = []
            if found_plugins:
                detail_bits.append("skills found in: " + ", ".join(found_plugins))
            if na_plugins:
                detail_bits.append("no skill (tool/LSP-only): " + ", ".join(na_plugins))
            sources.append(Source(
                key="plugin_skill_description_bytes",
                label=f"enabled-plugin skill descriptions ({len(pdescs)} skills across {len(enabled)} enabled plugins)",
                bytes=ptotal, state="MEASURED", scenarios=SCENARIOS, required=True,
                detail="; ".join(detail_bits) if detail_bits else None,
                warn_at=PLUGIN_DESC_TOTAL_WARN, ceiling_kind="combined enabled-plugin descriptions",
            ))
            for name, n in [(f"{plugin}:{skill}", n) for plugin, skill, n in pdescs]:
                if n >= DESC_FAIL:
                    per_skill_issues.append((name, n, "FAIL"))
                elif n >= DESC_WARN:
                    per_skill_issues.append((name, n, "WARN"))

    # ---- hosted/runtime-only sources: always UNMEASURED, never gate, never counted
    for hkey, hreason in HOSTED_UNMEASURABLE:
        sources.append(Source(
            key=hkey, label=hkey.replace("_", " "), bytes=None, state="UNMEASURED",
            scenarios=SCENARIOS, required=False, reason=hreason,
        ))

    # ---- SessionStart hook injected preview vs backing artifact
    sources.append(Source(
        key="session_start_injected_preview_bytes",
        label="SessionStart closure-audit: injected preview",
        bytes=None, state="UNMEASURED", scenarios=SCENARIOS, required=False,
        reason=("the hook itself (session_closure_audit.py) emits raw, "
                "untruncated text; truncation to a preview happens in the "
                "client/harness after the hook returns, which is not local "
                "repo configuration. Empirically, across observed sessions "
                "the injected preview has consistently been the first ~2KB "
                "of a much larger artifact (e.g. 46.7KB, 50.7KB, 50KB source "
                "reports each truncated to a ~2KB preview) — a pattern, not "
                "a locally-configured contract, so it is reported as "
                "UNMEASURED rather than asserted as a fixed byte count."),
    ))
    sources.append(Source(
        key="session_start_backing_artifact_bytes",
        label="SessionStart closure-audit: full backing artifact",
        bytes=None, state="UNMEASURED", scenarios=(), required=False,
        reason=("written to an ephemeral per-session tool-results path "
                "outside repo control; not a stable local artifact, and — "
                "per the injected-preview finding above — NOT what actually "
                "loads into standing context, so it is excluded from every "
                "scenario total even where its size is known for one run."),
    ))

    # ---- scenario totals
    def scenario_total(scenario: str):
        relevant = [s for s in sources if scenario in s.scenarios and s.required]
        incomplete = [s for s in relevant if s.state in ("MISSING", "UNMEASURED")]
        if incomplete:
            return None, incomplete
        return sum(s.bytes for s in relevant if s.state == "MEASURED"), []

    scenario_results = {}
    for scen in SCENARIOS:
        total, incomplete = scenario_total(scen)
        if total is None:
            scenario_results[scen] = {"total": None, "status": "incomplete", "incomplete": incomplete}
        else:
            # Aggregate size is telemetry: the reference lines are local
            # policy, not a runtime limit, so they label but never gate.
            verdict = _evaluate_ceiling(total, SCENARIO_WARN_BYTES, SCENARIO_FAIL_BYTES)
            status = "info" if verdict else "clean"
            scenario_results[scen] = {
                "total": total, "status": status, "verdict": verdict, "incomplete": [],
            }

    # ---- coverage counts (all sources, required and optional alike — this
    # is the honest inventory, not the gating signal)
    measured_n = sum(1 for s in sources if s.state == "MEASURED")
    unmeasured_n = sum(1 for s in sources if s.state == "UNMEASURED")
    missing_n = sum(1 for s in sources if s.state == "MISSING")
    na_n = sum(1 for s in sources if s.state == "NOT_APPLICABLE")

    # Gating counts: only a REQUIRED source in this state means "no verdict
    # possible". An optional/hosted source being UNMEASURED (e.g. the
    # anthropic-skills bundle, MCP instructions) is a real, permanent
    # coverage gap that belongs in the inventory — but it does not, by
    # itself, block a clean verdict on what IS measured.
    required_missing_n = sum(1 for s in sources if s.required and s.state == "MISSING")
    required_unmeasured_n = sum(1 for s in sources if s.required and s.state == "UNMEASURED")

    # ---- top contributors (measured only)
    top3 = sorted((s for s in sources if s.state == "MEASURED" and s.bytes), key=lambda s: -s.bytes)[:3]

    # ---- overall exit status
    # A FAIL-level breach — whether a whole scenario total, a single
    # per-skill description, a MEMORY_FAIL_LINES trip, or any other
    # measured source crossing its own fail_at — must escalate to exit 2,
    # not just the generic "something warned" bucket.
    per_skill_fail = any(k in ("FAIL", "FAIL-LINES") for _, _, k in per_skill_issues)
    per_skill_warn = any(k == "WARN" for _, _, k in per_skill_issues)
    source_ceiling_verdicts = [
        _evaluate_ceiling(s.bytes, s.warn_at, s.fail_at, s.fail_when_over)
        for s in sources if s.state == "MEASURED" and (s.warn_at or s.fail_at)
    ]
    any_source_fail = any(v == "FAIL" for v in source_ceiling_verdicts)
    any_source_warn = any(v in ("WARN", "NEAR-LIMIT") for v in source_ceiling_verdicts)

    any_fail = (
        required_missing_n > 0
        or per_skill_fail
        or any_source_fail
    )
    any_warn = (
        bool(plugin_allowlist_warns)
        or per_skill_warn
        or any_source_warn
    )
    # Advisory warnings are displayed but never fail a complete measurement.
    exit_code = 2 if any_fail else 0
    # A REQUIRED source that is MISSING/UNMEASURED means "no verdict
    # possible" even if nothing else looks wrong — preserve the v1 contract
    # that this is never indistinguishable from clean. Optional/hosted gaps
    # do not trigger this (see docstring): they are visible, not gating.
    if required_missing_n or required_unmeasured_n:
        exit_code = max(exit_code, 2)

    # ---- history / trend
    history_records = _read_history(history_path)
    prior = history_records[-1] if history_records else None
    trend_lines, trend_reset = _compare_trend(prior, scenario_results, sources)

    complete_enough_for_history = not any(
        s.required and s.state in ("MISSING", "UNMEASURED") for s in sources
    )
    advisory = any_warn or any(t.startswith("WARN") for t in trend_lines)
    record = None
    if write_history and complete_enough_for_history:
        record = _build_history_record(now, repo_dir, scenario_results, sources, exit_code, advisory)
        _append_history(history_path, record)

    return dict(
        sources=sources,
        per_skill_issues=per_skill_issues,
        plugin_allowlist_warns=plugin_allowlist_warns,
        scenario_results=scenario_results,
        measured_n=measured_n, unmeasured_n=unmeasured_n,
        missing_n=missing_n, na_n=na_n,
        top3=top3,
        exit_code=exit_code,
        advisory=advisory,
        trend_lines=trend_lines,
        trend_reset=trend_reset,
        history_recorded=record is not None,
        now=now,
    )


# =========================================================================
# history (JSONL trend)
# =========================================================================

def _read_history(history_path: Path) -> list:
    if not history_path.is_file():
        return []
    out = []
    try:
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a corrupt line is skipped, never crashes the scan
    except OSError:
        return []
    return out


def _append_history(history_path: Path, record: dict) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _source_bytes(sources: list, key: str):
    for s in sources:
        if s.key == key:
            return s.bytes if s.state == "MEASURED" else None
    return None


PRINCIPAL_SOURCE_KEYS = (
    "personal_skill_description_bytes",
    "plugin_skill_description_bytes",
    "global_claude_md_bytes",
    "project_claude_md_bytes",
    "global_memory_bytes",
    "project_memory_bytes",
)


def _build_history_record(now: datetime, repo_dir: Path, scenario_results: dict,
                           sources: list, exit_code: int, advisory: bool) -> dict:
    # Same verdict the report prints: exit 0 with any WARN is ADVISORY, not clean.
    status = {0: "advisory" if advisory else "clean", 2: "fail"}[exit_code]
    rec = {
        "timestamp": now.isoformat(),
        "tool_version_or_git_sha": _git_sha(repo_dir),
        "schema_version": SCHEMA_VERSION,
        "global_session_bytes": scenario_results[GLOBAL]["total"],
        "bimpossible_session_bytes": scenario_results[BIMPOSSIBLE]["total"],
        "measured_source_count": sum(1 for s in sources if s.state == "MEASURED"),
        "unmeasured_source_count": sum(1 for s in sources if s.state in ("UNMEASURED", "MISSING")),
        "status": status,
    }
    for k in PRINCIPAL_SOURCE_KEYS:
        rec[k] = _source_bytes(sources, k)
    return rec


def _compare_trend(prior: Optional[dict], scenario_results: dict, sources: list) -> tuple:
    """-> (lines:[str], reset:bool)."""
    if prior is None:
        return ["TREND: no prior history — this run establishes the baseline."], False
    if prior.get("schema_version") != SCHEMA_VERSION:
        return ([f"TREND BASELINE RESET — prior history is not comparable under "
                 f"schema/version {SCHEMA_VERSION} (prior was "
                 f"{prior.get('schema_version')!r})."], True)

    lines = []
    scen_field = {GLOBAL: "global_session_bytes", BIMPOSSIBLE: "bimpossible_session_bytes"}
    for scen in SCENARIOS:
        cur = scenario_results[scen]["total"]
        prev = prior.get(scen_field[scen])
        if cur is None or prev is None:
            continue
        if prev == 0:
            continue
        growth = (cur - prev) / prev
        if growth > TREND_GROWTH_WARN_RATIO:
            lines.append(
                f"WARN: {scen} grew {growth:+.1%} vs last comparable run "
                f"({prev} -> {cur} bytes, recorded {prior.get('timestamp')})"
            )
    for k in PRINCIPAL_SOURCE_KEYS:
        cur = _source_bytes(sources, k)
        prev = prior.get(k)
        if cur is None or prev is None or prev == 0:
            continue
        growth = (cur - prev) / prev
        if growth > TREND_GROWTH_WARN_RATIO:
            lines.append(
                f"WARN: {k} grew {growth:+.1%} vs last comparable run "
                f"({prev} -> {cur} bytes, recorded {prior.get('timestamp')})"
            )
    if not lines:
        lines.append(f"TREND: stable vs last comparable run (recorded {prior.get('timestamp')}).")
    return lines, False


# =========================================================================
# rendering
# =========================================================================

def _fmt_bytes_tokens(n: int) -> str:
    return f"{n} B (~{n // 4} tok, approx.)"


def render_report(result: dict) -> str:
    lines = []
    sources = result["sources"]
    scen = result["scenario_results"]

    lines.append(
        f"context-weight v{SCHEMA_VERSION} | design target {DESIGN_TARGET_BYTES}B "
        f"| reference lines {SCENARIO_WARN_BYTES}B / {SCENARIO_FAIL_BYTES}B "
        f"(all informational — scenario totals never gate)"
    )
    for name, key in ((GLOBAL, "global_session"), (BIMPOSSIBLE, "bimpossible_session")):
        r = scen[name]
        if r["total"] is None:
            lines.append(f"  {name}: INCOMPLETE — {len(r['incomplete'])} required source(s) not measured")
        else:
            pct = r["total"] / SCENARIO_FAIL_BYTES * 100
            label = f" (past {r['verdict']} reference line)" if r["verdict"] else ""
            lines.append(
                f"  {name}: {_fmt_bytes_tokens(r['total'])} | status={r['status'].upper()}{label} "
                f"| {pct:.0f}% of {SCENARIO_FAIL_BYTES}B reference line"
            )

    lines.append("")
    lines.append("Measured sources:")
    any_measured = False
    for s in sources:
        if s.state != "MEASURED":
            continue
        any_measured = True
        scen_str = ",".join(s.scenarios) if s.scenarios else "-"
        ceil_str = ""
        if s.ceiling:
            v = _evaluate_ceiling(s.bytes, s.warn_at, s.fail_at, s.fail_when_over)
            pct = s.bytes / s.ceiling * 100
            ceil_str = f" | {pct:.0f}% of {s.ceiling}B {s.ceiling_kind or 'ceiling'}"
            if v:
                ceil_str += f" [{v}]"
        detail_str = f" ({s.detail})" if s.detail else ""
        lines.append(f"  - {s.label}: {s.bytes}B{detail_str} | scenarios: {scen_str}{ceil_str}")
    if not any_measured:
        lines.append("  (none)")

    lines.append("")
    lines.append("Unmeasured/discovered sources:")
    any_unmeasured = False
    for s in sources:
        if s.state not in ("UNMEASURED", "MISSING"):
            continue
        any_unmeasured = True
        scen_str = ",".join(s.scenarios) if s.scenarios else "n/a"
        req_str = "required" if s.required else "optional"
        lines.append(f"  - [{s.state}/{req_str}] {s.label} | scenarios: {scen_str}")
        if s.reason:
            lines.append(f"      reason: {s.reason}")
    if not any_unmeasured:
        lines.append("  (none)")

    lines.append("")
    lines.append("Excluded / not applicable:")
    any_na = False
    for s in sources:
        if s.state != "NOT_APPLICABLE":
            continue
        any_na = True
        lines.append(f"  - {s.label}: {s.reason or 'not applicable'}")
    if not any_na:
        lines.append("  (none)")

    if result["per_skill_issues"]:
        lines.append("")
        lines.append("Per-source ceiling issues:")
        for name, n, kind in result["per_skill_issues"]:
            lines.append(f"  {kind}: '{name}' {n} chars/lines")

    if result["plugin_allowlist_warns"]:
        lines.append("")
        lines.append("Plugin allowlist:")
        for p in result["plugin_allowlist_warns"]:
            lines.append(f"  WARN: plugin '{p}' enabled but not in the audited allowlist")

    lines.append("")
    lines.append("Top three measured contributors:")
    if result["top3"]:
        for i, s in enumerate(result["top3"], 1):
            scen_str = ",".join(s.scenarios) if s.scenarios else "-"
            pct = f"{s.bytes / s.ceiling * 100:.0f}% of ceiling" if s.ceiling else "no ceiling configured"
            lines.append(f"  {i}. {s.label} — {s.bytes}B — ~{s.bytes // 4} tok — {pct} — {scen_str}")
    else:
        lines.append("  (no measured sources)")

    lines.append("")
    lines.append(
        f"Coverage: measured={result['measured_n']} unmeasured={result['unmeasured_n']} "
        f"missing={result['missing_n']} not_applicable={result['na_n']}"
    )

    lines.append("")
    for t in result["trend_lines"]:
        lines.append(t)
    lines.append(f"history: {'recorded' if result['history_recorded'] else 'skipped (measurement incomplete)'}")

    lines.append("")
    if result["exit_code"] == 0 and result["advisory"]:
        lines.append("VERDICT: ADVISORY (exit 0) — measurement complete, no hard failure; see WARN/NEAR-LIMIT lines above.")
    elif result["exit_code"] == 0:
        lines.append("VERDICT: CLEAN on measured sources — see Unmeasured/discovered above for coverage gaps.")
    else:
        lines.append("VERDICT: FAIL or NOT MEASURED — a required source is missing/unmeasured, or a hard ceiling was hit.")

    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================

def main(**overrides) -> int:
    result = run_scan(**overrides)
    print(render_report(result))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
