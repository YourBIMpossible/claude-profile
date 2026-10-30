"""HYG-8 drift check: live scheduled-task bodies vs the committed baseline snapshots.

Check-only Python port of BIMpossible-Workspace
system/scheduled-tasks/Check-ScheduledTaskDrift.ps1 (same comparison, same exit
codes). The -Refresh mode is deliberately not ported: re-taking baselines is a
Workspace maintenance action, never an audit step.

For every folder <baseline-root>/<taskId>/ the live body
<live-root>/<taskId>/SKILL.md is read, CRLF is normalised to LF, trailing
newlines are trimmed, the baseline's leading <!-- ... --> banner is stripped,
and the two are compared.

Exit 0  every baseline matches its live body
Exit 1  at least one task drifted, or a live body is missing
Exit 2  bad invocation (baseline root missing or empty)

Reads only. Prints the unified diff of drifted files; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass
class TaskResult:
    task_id: str
    status: str  # ok | drift | unreadable
    detail: str = ""
    diff: list[str] = field(default_factory=list)


def read_normalized(path: Path) -> str:
    raw = path.read_bytes().decode("utf-8-sig")
    return raw.replace("\r\n", "\n").rstrip("\n")


def strip_banner(text: str) -> str:
    if text.startswith("<!--"):
        end = text.find("-->")
        if end >= 0:
            return text[end + 3 :].lstrip("\n")
    return text


def check(live_root: Path, baseline_root: Path) -> list[TaskResult]:
    if not baseline_root.is_dir():
        raise FileNotFoundError(f"baseline root not found: {baseline_root}")
    dirs = sorted(d for d in baseline_root.iterdir() if d.is_dir())
    if not dirs:
        raise FileNotFoundError(f"no baseline task folders under {baseline_root}")
    results: list[TaskResult] = []
    for d in dirs:
        live_path = live_root / d.name / "SKILL.md"
        base_path = d / "SKILL.md"
        if not live_path.is_file():
            results.append(TaskResult(d.name, "unreadable", f"live body missing: {live_path}"))
            continue
        try:
            live = read_normalized(live_path)
        except (OSError, UnicodeDecodeError) as exc:
            results.append(TaskResult(d.name, "unreadable", f"live body unreadable: {exc}"))
            continue
        if not base_path.is_file():
            results.append(TaskResult(d.name, "drift", "no baseline SKILL.md"))
            continue
        baseline = strip_banner(read_normalized(base_path))
        if baseline == live:
            results.append(TaskResult(d.name, "ok"))
            continue
        diff = list(
            difflib.unified_diff(
                baseline.split("\n"),
                live.split("\n"),
                f"baseline/{d.name}/SKILL.md",
                f"live/{d.name}/SKILL.md",
                n=2,
                lineterm="",
            )
        )
        results.append(TaskResult(d.name, "drift", diff=diff))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-root", type=Path, default=Path.home() / ".claude" / "scheduled-tasks")
    ap.add_argument("--baseline-root", type=Path, required=True,
                    help="<workspace-root>/system/scheduled-tasks/_live")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        results = check(a.live_root, a.baseline_root)
    except FileNotFoundError as exc:
        print(f"hyg8 error: {exc}", file=sys.stderr)
        return 2
    bad = [r for r in results if r.status != "ok"]
    if a.json:
        print(json.dumps({"results": [asdict(r) for r in results], "drift": len(bad)}, indent=2))
    else:
        for r in results:
            print(f"{r.status.upper():<11} {r.task_id}{'  (' + r.detail + ')' if r.detail else ''}")
            for line in r.diff:
                print(line)
        counts = {s: sum(1 for r in results if r.status == s) for s in ("ok", "drift", "unreadable")}
        print(f"\nSummary: ok={counts['ok']} drift={counts['drift']} unreadable={counts['unreadable']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
