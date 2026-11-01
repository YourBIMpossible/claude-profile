"""Startup-context telemetry (REPORT-ONLY) for the `## Startup context` section.

Python port of the PowerShell snippet in the live weekly-full-audit prompt
(added 2026-10-05). It measures what auto-loads into a Claude Code session
opened in each --repo. Read-only: it writes nothing, never changes the run
outcome and never triggers a fix.

Derivation, unchanged from the snippet:
  (a) user memory <user-claude>/CLAUDE.md plus @imports (depth 5, code spans/fences ignored)
  (b) <user-claude>/rules/*.md without `paths:` front matter (first 15 lines)
  (c) every CLAUDE.md, .claude/CLAUDE.md, CLAUDE.local.md from the filesystem root down
      to the repo (walk-up order, root first), with @imports
  (d) <repo>/.claude/rules/*.md without `paths:` front matter
  (e) <user-claude>/projects/<slug>/memory/MEMORY.md (slug = repo path, non-alphanumerics -> '-')
  (f) active non-draft NORTHSTAR*.md, only when a SessionStart/UserPromptSubmit hook runs
      northstar_inject.py (closest of 7 walk-up levels with an active star wins)

Classification (informational): memory-index TRUNCATED > 200 lines or > 25600 bytes,
NEAR >= 160 lines or >= 20480 bytes; northstar always OK; other files OVER > 200
lines, NEAR >= 160 lines, else OK.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

IMPORT_RE = re.compile(r"(?<![\w`])@((?:~|[A-Za-z]:|\.{1,2})?[/\\][^\s`)]+|[\w.-]+\.md)")
CODE_SPAN_RE = re.compile(r"`[^`]*`")
FENCE_RE = re.compile(r"^\s*```")
PATHS_RE = re.compile(r"^paths\s*:", re.I)
STAR_RE = re.compile(r"^northstar(\..+)?\.md$", re.I)
STAR_EXCL_RE = re.compile(r"\.(draft|template)\.md$", re.I)
STATUS_RE = re.compile(r"^\s*\**status\**\s*[:=]\s*([A-Za-z-]+)")
INACTIVE = {"paused", "closed", "done", "archived", "inactive"}
CLASS_ORDER = ("TRUNCATED", "OVER", "NEAR", "OK")
MAX_DEPTH = 5
STAR_LEVELS = 7


@dataclass
class Row:
    cls: str
    kind: str
    lines: int
    words: int
    bytes: int
    via: str
    path: str


@dataclass
class Hook:
    settings: str
    event: str
    command: str
    northstar: bool


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def measure(path: Path, kind: str, via: str) -> Row:
    text = _read_text(path)
    size = path.stat().st_size
    words = len(re.findall(r"\S+", text))
    lines = len(text.split("\n"))
    if kind == "memory-index":
        cls = "TRUNCATED" if lines > 200 or size > 25600 else "NEAR" if lines >= 160 or size >= 20480 else "OK"
    elif kind == "northstar":
        cls = "OK"
    else:
        cls = "OVER" if lines > 200 else "NEAR" if lines >= 160 else "OK"
    return Row(cls, kind, lines, words, size, via, str(path))


def _full(path: Path) -> Path:
    return Path(os.path.abspath(path))


def resolve_imports(path: Path, kind: str, via: str, depth: int, seen: set[str]) -> list[Row]:
    full = _full(path)
    key = os.path.normcase(str(full))
    if key in seen or not full.is_file():
        return []
    seen.add(key)
    rows = [measure(full, kind, via)]
    if depth >= MAX_DEPTH:
        return rows
    in_fence = False
    for ln in _read_text(full).replace("\r\n", "\n").split("\n"):
        if FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in IMPORT_RE.finditer(CODE_SPAN_RE.sub("", ln)):
            target = m.group(1)
            if target.startswith("~"):
                target = str(Path.home()) + target[1:]
            p = Path(target)
            if not p.is_absolute():
                p = full.parent / target
            rows += resolve_imports(p, "import", f"@import from {full}", depth + 1, seen)
    return rows


def _unscoped_rules(rules_dir: Path) -> Iterator[Path]:
    if not rules_dir.is_dir():
        return
    for p in sorted(rules_dir.rglob("*.md")):
        head = _read_text(p).replace("\r\n", "\n").split("\n")[:15]
        if not any(PATHS_RE.match(h) for h in head):
            yield p


def star_active(path: Path) -> bool:
    for ln in _read_text(path).replace("\r\n", "\n").split("\n")[:20]:
        m = STATUS_RE.match(ln)
        if m:
            return m.group(1).lower() not in INACTIVE
    return True


def active_stars(start: Path) -> list[Path]:
    d: Path | None = _full(start)
    for _ in range(STAR_LEVELS):
        if d is None:
            break
        if d.is_dir():
            act = [
                f for f in sorted(d.iterdir())
                if f.is_file() and STAR_RE.match(f.name) and not STAR_EXCL_RE.search(f.name) and star_active(f)
            ]
            if act:
                return act
        d = d.parent if d.parent != d else None
    return []


def hooks(settings: Path) -> list[Hook]:
    if not settings.is_file():
        return []
    try:
        data: Any = json.loads(_read_text(settings))
    except json.JSONDecodeError:
        return [Hook(str(settings), "-", "settings unreadable (invalid JSON)", False)]
    out: list[Hook] = []
    for event, matchers in (data.get("hooks") or {}).items() if isinstance(data, dict) else []:
        if event not in ("SessionStart", "UserPromptSubmit"):
            continue
        for m in matchers or []:
            for h in (m or {}).get("hooks") or []:
                cmd = " ".join(str(x) for x in [h.get("command")] + list(h.get("args") or []) if x is not None)
                out.append(Hook(str(settings), event, cmd, bool(re.search(r"northstar_inject\.py", cmd))))
    return out


def walk_up(repo: Path) -> list[Path]:
    chain: list[Path] = []
    d = _full(repo)
    while True:
        chain.insert(0, d)
        if d.parent == d:
            return chain
        d = d.parent


def project_slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(_full(repo)))


def measure_repo(repo: Path, user_claude: Path) -> dict[str, Any]:
    seen: set[str] = set()
    rows = resolve_imports(user_claude / "CLAUDE.md", "user", "user memory ~/.claude/CLAUDE.md", 0, seen)
    for p in _unscoped_rules(user_claude / "rules"):
        rows += resolve_imports(p, "rule", "user rule (unscoped)", 0, seen)
    for d in walk_up(repo):
        for name in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "CLAUDE.local.md"):
            rows += resolve_imports(d / name, "project", f"walk-up {d}", 0, seen)
    for p in _unscoped_rules(repo / ".claude" / "rules"):
        rows += resolve_imports(p, "rule", "project rule (unscoped)", 0, seen)
    slug = project_slug(repo)
    mem = user_claude / "projects" / slug / "memory" / "MEMORY.md"
    limitation = ""
    if mem.is_file():
        rows.append(measure(mem, "memory-index", f"auto-memory index ({slug})"))
    else:
        limitation = f"LIMITATION: auto-memory index not found at {mem} (none loaded, or a different project slug)"
    hk = (hooks(user_claude / "settings.json") + hooks(repo / ".claude" / "settings.json")
          + hooks(repo / ".claude" / "settings.local.json"))
    if any(h.northstar for h in hk):
        rows += [measure(f, "northstar", "northstar_inject.py (active, non-draft)") for f in active_stars(repo)]
    classes = {r.cls for r in rows}
    drafts = sorted(f.name for f in repo.iterdir() if f.is_file() and re.match(r"^northstar.*\.draft\.md$", f.name, re.I)) if repo.is_dir() else []
    return {
        "repo": str(repo),
        "limitation": limitation,
        "rows": [asdict(r) for r in rows],
        "total": {"files": len(rows), "words": sum(r.words for r in rows), "bytes": sum(r.bytes for r in rows),
                  "worst": next((c for c in CLASS_ORDER if c in classes), None)},
        "excluded_drafts": drafts,
        "hooks_not_measured": [f"{h.event}: {h.command}" for h in hk if not h.northstar],
    }


def render(result: dict[str, Any]) -> str:
    out = [f"## {result['repo']}"]
    if result["limitation"]:
        out.append(result["limitation"])
    out += ["", "| Class | Kind | Lines | Words | Bytes | Path |", "|---|---|---|---|---|---|"]
    out += [f"| {r['cls']} | {r['kind']} | {r['lines']} | {r['words']} | {r['bytes']} | {r['path']} |"
            for r in result["rows"]]
    t = result["total"]
    out += ["", f"TOTAL files={t['files']} words={t['words']} bytes={t['bytes']} worst={t['worst'] or ''}",
            "Excluded drafts: " + ", ".join(result["excluded_drafts"]),
            "Hooks not measured (dynamic output, not file-backed): " + " | ".join(result["hooks_not_measured"]), ""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", action="append", type=Path, required=True, help="repeatable")
    ap.add_argument("--user-claude", type=Path, default=Path.home() / ".claude")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    results = [measure_repo(r, a.user_claude) for r in a.repo]
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n".join(render(r) for r in results))
    return 0  # telemetry never fails the run


if __name__ == "__main__":
    sys.exit(main())
