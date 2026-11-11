"""Run-control CLI for /weekly-full-audit and /slop-audit-sweep.

Every location is an argument or an environment variable; nothing names a
machine path. The skill bodies call these subcommands in step order; each one
enforces the anchor contract rule it implements and exits non-zero on a
contract violation, so the model cannot skip a gate silently.

Exit codes: 0 ok · 1 check result is negative (reuse refused, dirty tree,
rollback refused) · 2 contract violation or unusable input.

Subcommands
  preflight     required roots/tools present? optional capabilities -> NOT RUN notes (rule 2)
  start         preflight, mint run_id, create run record + heartbeat; on failure finalize failed
  slug          GRRP slug for a run_id (rule 5)
  baseline      revision baseline per root, recorded into the run record (rule 3)
  write-report  exclusive-create a report with front matter (rule 5)
  record        inspection state, coverage, required checks, artifacts, ledgers, gate, events, run outcome
  brief-done    a recorded ledger's brief (Scope, Boundaries, Done means, Authority) is filled
  dispatch      declare one child: method files (hash + random line k) and create-only outputs
  check         one required check's return (completion plan) or failure
  isolate       detached checkout of one root's exact revision under the run dir; children read it
  outputs-audit undeclared writes in run-owned dirs (defect); live-root changes (observed only)
  release-src   remove the run's isolated checkouts after outputs-audit
  write-run-log create-only run log, recorded as artifact kind run-log
  wait          barrier: exit 1 while any required check is still pending
  hygiene-line  run G3/G4 in the repo and workspace roots; print the HYG-13 header line
  reuse-check   may the weekly reuse a sweep report for a root? (rule 4)
  dirty-check   git status of a root's checkout
  publish       publication after run outcome + gate (rule 6)
  deliver       delivery state; only activated modes may deliver
  deliver-file  --deliver file: create-only <state-dir>/<task>/<run-id>/RESULTS.md, then delivered
  remediation   start / finish the remediation stage (rule 7)
  fix           snapshot / post / rollback for one autofix fix (rule 8)
  finalize      failure finalization from any stage; heartbeat finished failed
  finish        heartbeat finish from the run record
  personas      read-only discovery of persona agents
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib as L  # noqa: E402
import heartbeat as HB  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = SKILL_DIR.parent
PERSONAS: tuple[str, ...] = ("security-engineer", "qa-engineer", "tech-lead", "first-time-user", "bim-manager")
WEEKLY_CHECKERS: tuple[str, ...] = (
    "check-audit-report-header.mjs",
    "check-doc-references.mjs",
    "check-docs-budget.mjs",
)
REQUIRED_SKILLS: dict[str, tuple[str, ...]] = {
    "weekly-full-audit": ("review-all", "review-to-resolution", "ctxcheck", "slop-audit"),
    "slop-audit-sweep": ("slop-audit", "review-to-resolution"),
}


# --------------------------------------------------------------------------- preflight


@dataclass
class Preflight:
    task_id: str
    ok: bool = True
    failures: list[str] = field(default_factory=list)
    roots: dict[str, dict[str, Any]] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    optional: dict[str, dict[str, Any]] = field(default_factory=dict)
    personas: dict[str, str | None] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    access: dict[str, str] = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.failures.append(msg)

    def opt(self, name: str, path: Path | None, coverage_key: str, present: bool | None = None) -> None:
        here = present if present is not None else bool(path and path.exists())
        self.optional[name] = {
            "present": here,
            "path": str(path) if path else None,
            "coverage_key": coverage_key,
            "note": None if here else f"NOT RUN ({name} not present)",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "ok": self.ok,
            "failures": self.failures,
            "roots": self.roots,
            "tools": self.tools,
            "optional": self.optional,
            "personas": self.personas,
            "flags": self.flags,
            "access": self.access,
        }


def parse_roots(pairs: Sequence[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for item in pairs:
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            raise L.AuditError(f"--root expects name=path, got {item!r}")
        if name in out:
            raise L.AuditError(f"root name {name!r} given twice")
        out[name] = Path(path)
    return out


def tools_root(arg: str | None) -> Path | None:
    raw = arg or os.environ.get("CLAUDE_TOOLS_ROOT")
    return Path(raw) if raw else None


def default_grrp() -> Path:
    return SKILLS_DIR / "review-to-resolution" / "scripts" / "grrp.py"


def discover_personas(repo_roots: Sequence[Path], user_home: Path | None = None) -> dict[str, str | None]:
    """Read-only. Project agents win over user agents, as in Claude Code."""
    home = user_home or Path.home()
    found: dict[str, str | None] = {}
    for name in PERSONAS:
        hit: str | None = None
        for base in [r / ".claude" / "agents" for r in repo_roots] + [home / ".claude" / "agents"]:
            if (base / f"{name}.md").is_file():
                hit = str(base / f"{name}.md")
                break
        found[name] = hit
    return found


def run_preflight(a: argparse.Namespace) -> Preflight:
    pf = Preflight(a.task)
    pf.flags = {"deliver": a.deliver, "autofix": bool(getattr(a, "autofix", False)),
                "handoff": bool(getattr(a, "handoff", False))}
    if a.task not in L.TASK_IDS:
        pf.fail(f"unknown task {a.task!r}")
        return pf
    if not L.git_available():
        pf.fail("git not on PATH")
    try:
        roots = parse_roots(a.root)
    except L.AuditError as exc:
        pf.fail(str(exc))
        roots = {}
    need = ("repo", "workspace") if a.task == "weekly-full-audit" else ()
    for n in need:
        if n not in roots:
            pf.fail(f"required root {n!r} missing (--root {n}=<path>)")
    if a.task == "slop-audit-sweep" and not roots:
        pf.fail("at least one --root is required")
    infos: dict[str, L.RootInfo] = {}
    if L.git_available():
        for n, p in roots.items():
            if n == "addins" and a.task == "weekly-full-audit":
                continue  # optional, handled below
            try:
                info = L.root_info(n, p)
            except L.AuditError as exc:
                if a.task == "weekly-full-audit" and n in need:
                    pf.access[n] = str(exc)  # degraded run with an Access note, not a failure
                else:
                    pf.fail(str(exc))
                continue
            infos[n] = info
            pf.roots[n] = {"path": str(info.path), "identity": info.identity, "path_arg": info.path_arg,
                           "head_rev": info.head_rev}

    # --deliver: required, and only activated modes may run (D2)
    if a.deliver is None:
        pf.fail("--deliver is required")
    elif a.deliver not in L.DELIVER_MODES:
        pf.fail(f"--deliver must be one of {L.DELIVER_MODES}")
    elif a.deliver != "none" and not L.ACTIVATION.get(f"deliver:{a.deliver}", False):
        pf.fail(f"--deliver {a.deliver} is built but not activated (owner decision "
                f"{L.ACTIVATION_DECISION[f'deliver:{a.deliver}']}); use --deliver none")
    if a.task == "slop-audit-sweep" and a.deliver == "gmail-draft":
        pf.fail("slop-audit-sweep supports --deliver file|none only")
    if pf.flags["autofix"]:
        if a.task != "weekly-full-audit":
            pf.fail("--autofix exists only in weekly-full-audit")
        elif not L.ACTIVATION["autofix"]:
            pf.fail("--autofix is built but not activated (owner decision D1)")

    tr = tools_root(a.tools_root)
    tool = "ctxcheck/ctxcheck.py" if a.task == "weekly-full-audit" else "local-audit/slop_prepass.py"
    if tr is None:
        pf.fail("CLAUDE_TOOLS_ROOT not set and --tools-root not given")
    elif not (tr / tool).is_file():
        pf.fail(f"{tool} not found under tools root {tr}")
    else:
        pf.tools[Path(tool).stem] = str(tr / tool)
    pf.tools["python"] = sys.executable

    grrp = Path(a.grrp) if a.grrp else default_grrp()
    if grrp.is_file():
        pf.tools["grrp"] = str(grrp)
    else:
        pf.fail(f"grrp.py not found at {grrp}")
    for skill in REQUIRED_SKILLS[a.task]:
        if not (SKILLS_DIR / skill / "SKILL.md").is_file():
            pf.fail(f"required skill {skill!r} not found beside this skill ({SKILLS_DIR})")

    if a.task == "weekly-full-audit" and need and all(n in pf.access for n in need):
        pf.fail("no required root is reachable: " + "; ".join(f"{n}: {e}" for n, e in pf.access.items()))
    if a.task == "weekly-full-audit":
        _weekly_optional(pf, a, roots, infos, tr)
    pf.personas = discover_personas([i.path for i in infos.values()]) if a.task == "weekly-full-audit" else {}
    return pf


CHECKLIST_REL = Path("02_Reference") / "Audit and Scan Info" / "BIMpossible_Verification_Checklist.md"


def _checklist(pf: Preflight, arg: str | None, ws: L.RootInfo | None) -> None:
    """Resolve the step-10 Checklist target. `none` = isolated run, recorded NOT RUN; a missing file fails."""
    if arg == "none":
        pf.opt("checklist", None, "checklist", present=False)
        pf.optional["checklist"]["note"] = "NOT RUN (isolated validation, --checklist none)"
        return
    path = Path(arg) if arg else (ws.path / CHECKLIST_REL if ws else None)
    if path is None:
        return  # workspace root already failed
    if not path.is_file():
        src = "--checklist" if arg else "default"
        pf.fail(f"checklist not found ({src}): {path}")
        return
    if "_backups" in path.parts:
        pf.fail(f"checklist under _backups/ is never written: {path}")
        return
    try:  # proven readable as UTF-8 now, not at step 10
        L.decode_text(path.read_bytes(), str(path))
    except (OSError, L.AuditError) as exc:
        pf.fail(f"checklist unreadable: {path}: {exc}")
        return
    pf.tools["checklist"] = str(path)


def _weekly_optional(pf: Preflight, a: argparse.Namespace, roots: dict[str, Path],
                     infos: dict[str, L.RootInfo], tr: Path | None) -> None:
    ws = infos.get("workspace")
    repo = infos.get("repo")
    _checklist(pf, getattr(a, "checklist", None), ws)
    checker_dir = Path(a.checker_dir) if a.checker_dir else (ws.path / "scripts" if ws else None)
    if checker_dir is None and "workspace" in pf.access:
        pf.opt("hyg13-checkers", None, "hyg13", present=False)
        pf.optional["hyg13-checkers"]["note"] = "Access: workspace root unreachable; checkers not located"
    if checker_dir is not None:
        missing = [c for c in WEEKLY_CHECKERS if not (checker_dir / c).is_file()]
        if missing:
            pf.fail(f"checkers missing in {checker_dir}: {', '.join(missing)}")
        node = shutil.which("node")
        if node is None:
            pf.fail("node not on PATH (needed for the HYG-13 checkers)")
        else:
            pf.tools["node"] = node
        pf.tools["checker_dir"] = str(checker_dir)
    if tr is not None:
        pf.opt("slop_prepass", tr / "local-audit" / "slop_prepass.py", "slop")
    if repo is not None:
        pf.opt("breach-chain", repo.path / ".claude" / "skills" / "bimpossible-breach-chain", "breach-chain")
        pf.opt("security-review-full", repo.path / ".claude" / "commands" / "security-review-full.md", "security-full")
        pf.opt("audit-resolution", repo.path / ".claude" / "commands" / "audit-resolution.md", "handoff")
        if pf.flags["handoff"] and not pf.optional["audit-resolution"]["present"]:
            pf.fail("--handoff given but audit-resolution.md is missing in the repo root")
    if "addins" in roots:
        try:
            info = L.root_info("addins", roots["addins"])
        except L.AuditError as exc:
            pf.opt("addins-root", roots["addins"], "addins", present=False)
            pf.optional["addins-root"]["note"] = f"NOT RUN (addins root unusable: {exc})"
        else:
            infos["addins"] = info
            pf.roots["addins"] = {"path": str(info.path), "identity": info.identity, "path_arg": info.path_arg,
                                  "head_rev": info.head_rev}
            pf.opt("addins-root", info.path, "addins")
            pf.opt("revit-functionality-audit",
                   info.path / ".claude" / "skills" / "revit-functionality-audit", "addins")
    else:
        pf.opt("addins-root", None, "addins", present=False)
    live = Path(a.hyg8_live_root) if a.hyg8_live_root else Path.home() / ".claude" / "scheduled-tasks"
    base = Path(a.hyg8_baseline_root) if a.hyg8_baseline_root else (
        ws.path / "system" / "scheduled-tasks" / "_live" if ws else None)
    pf.opt("hyg8-live", live, "hyg8")
    pf.opt("hyg8-baseline", base, "hyg8")


# --------------------------------------------------------------------------- record helpers


def _rec_path(a: argparse.Namespace) -> Path:
    rid = L.parse_run_id(a.run_id)
    return L.record_path(Path(a.state_dir), rid.task_id, a.run_id)


def _mutate(a: argparse.Namespace, fn: Callable[[dict[str, Any]], Any]) -> Any:
    path = _rec_path(a)
    rec = L.load_record(path)
    result = fn(rec)
    L.save_record(path, rec)
    return result


def _watchdog_dir(a: argparse.Namespace) -> Path:
    raw = getattr(a, "watchdog_dir", None) or os.environ.get("AUDIT_WATCHDOG_DIR")
    return Path(raw) if raw else HB.default_watchdog_dir()


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------- commands


def cmd_preflight(a: argparse.Namespace) -> int:
    pf = run_preflight(a)
    _print(pf.as_dict())
    return 0 if pf.ok else 2


def cmd_start(a: argparse.Namespace) -> int:
    pf = run_preflight(a)
    run_id = L.mint_run_id(a.task) if a.task in L.TASK_IDS else None
    if run_id is None:
        _print(pf.as_dict())
        return 2
    state_dir = Path(a.state_dir)
    rec = L.new_record(a.task, run_id, pf.flags)
    rec["preflight"] = pf.as_dict()
    path = L.record_path(state_dir, a.task, run_id)
    if path.exists():
        raise L.AuditError(f"run record already exists: {path}")
    wd = _watchdog_dir(a)
    # finalize finds the heartbeat from the record, not from its own flags
    rec["watchdog"] = {"dir": str(wd), "task": a.watchdog_task}
    HB.start(wd, a.task, run_id, a.watchdog_task)
    try:
        return _start_body(a, pf, rec, path, wd, run_id)
    except Exception as e:
        # the heartbeat is already open: close it and leave a failed record before the error surfaces
        reason = f"start: {type(e).__name__}: {e}"
        L.finalize_failed(rec, reason)
        try:
            L.save_record(path, rec)
        except OSError as save_err:
            print(f"start: run record not written: {save_err}", file=sys.stderr)
        HB.finish(wd, a.task, run_id, "failed", note=reason, task=a.watchdog_task)
        raise


def _start_body(a: argparse.Namespace, pf: Preflight, rec: dict[str, Any], path: Path, wd: Path,
                run_id: str) -> int:
    if not pf.ok:
        reason = "preflight: " + "; ".join(pf.failures)
        L.finalize_failed(rec, reason)
        L.save_record(path, rec)
        HB.finish(wd, a.task, run_id, "failed", note=reason, task=a.watchdog_task)
        _print({"run_id": run_id, "ok": False, "record": str(path), "failures": pf.failures})
        return 2
    L.set_inspection(rec, "running")
    rec["target_snapshots"] = _target_snapshots(pf)
    for name, err in pf.access.items():
        L.set_coverage(rec, f"root-{name}", "lost", f"Access: {err}")
    for name, opt in pf.optional.items():
        if not opt["present"]:
            key = opt["coverage_key"]
            if key not in rec["coverage"]:
                L.set_coverage(rec, key, "not-run-optional", opt["note"] or "")
    if a.task == "weekly-full-audit":
        if not pf.flags["handoff"]:
            L.set_coverage(rec, "handoff", "handoff-pending", "HANDOFF PENDING: --handoff not given")
        if not pf.flags["autofix"]:
            L.set_coverage(rec, "autofix", "not-run-flag-off", "NOT RUN (flag off: --autofix not activated, owner decision D1)")
        for name, hit in pf.personas.items():
            if hit is None:
                L.add_event(rec, "persona-absent", f"{name}: refute stage uses general-purpose")
    L.save_record(path, rec)
    _print({"run_id": run_id, "ok": True, "record": str(path), "slug": L.grrp_slug(run_id),
            "heartbeat": str(HB.heartbeat_path(wd, a.watchdog_task or a.task, run_id)),
            "coverage": rec["coverage"], "personas": pf.personas})
    return 0


def _target_snapshots(pf: Preflight) -> dict[str, dict[str, Any]]:
    """Start-time `git status` of every reachable live root, for outputs-audit attribution."""
    out: dict[str, dict[str, Any]] = {}
    for name, meta in pf.roots.items():
        if name in pf.access:
            continue
        top = Path(L.git(meta["path"], "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        out[name] = {"toplevel": top.as_posix(), "at": L.iso_utc(L.utcnow()), "status": L.status_snapshot(top)}
    return out


def _iso_root(rec: dict[str, Any], name: str, path: str) -> dict[str, Any]:
    if rec["states"]["inspection"] != "running":
        raise L.AuditError("isolate runs while inspection is running")
    meta = rec["preflight"]["roots"].get(name)
    if meta is None:
        raise L.AuditError(f"--root {name!r} is not a preflighted root")
    if name in rec.get("isolated", {}):
        raise L.AuditError(f"root {name!r} already isolated at {rec['isolated'][name]['sha'][:12]}")
    if name in rec["roots"]:
        raise L.AuditError(f"isolate a root before its baseline ({name!r} already has one)")
    return meta


def cmd_isolate(a: argparse.Namespace) -> int:
    """Detached checkout of the exact revision under <sd>/<task>/<rid>/src/<name>; the live tree,
    its index, worktree list and dirty/untracked files are never touched or copied."""
    name, _, path = a.root.partition("=")
    rid = L.parse_run_id(a.run_id)
    run_dir = (Path(a.state_dir) / rid.task_id / a.run_id).resolve()
    rec = L.load_record(_rec_path(a))
    meta = _iso_root(rec, name, path)
    info = L.root_info(name, path)
    if info.identity != meta["identity"] or info.path_arg != meta["path_arg"]:
        raise L.AuditError(f"--root {name}={path} is not the preflighted root ({meta['identity']} {meta['path_arg']})")
    sha = L.git(info.path, "rev-parse", "--verify", f"{a.rev}^{{commit}}").stdout.strip()
    dest = run_dir / "src" / name
    if dest.exists():
        raise L.AuditError(f"isolate: {dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    L.checkout_revision(info.toplevel, sha, dest)
    origin = L.git(info.toplevel, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    if origin:  # identity of the checkout = identity of the source, not the source's local path
        L.git(dest, "remote", "set-url", "origin", origin)
    checkout = (dest / info.path_arg).resolve() if info.path_arg != "." else dest.resolve()
    entry = {"name": name, "path": info.path.as_posix(), "path_arg": info.path_arg, "sha": sha,
             "checkout": checkout.as_posix(), "dest": dest.resolve().as_posix(),
             "created_at": L.iso_utc(L.utcnow()), "released": False}

    def apply(r: dict[str, Any]) -> None:
        _iso_root(r, name, path)
        r.setdefault("isolated", {})[name] = entry
        L.add_event(r, "isolated", f"{name}@{sha}")

    _mutate(a, apply)
    _print(entry)
    return 0


def cmd_release_src(a: argparse.Namespace) -> int:
    """Remove the isolated checkouts this run created, only after outputs-audit has audited them."""
    rid = L.parse_run_id(a.run_id)
    src_root = (Path(a.state_dir) / rid.task_id / a.run_id / "src").resolve()

    def onexc(fn: Callable[..., Any], p: str, _exc: BaseException) -> None:
        os.chmod(p, 0o700)  # git pack files are read-only on Windows
        fn(p)

    def apply(rec: dict[str, Any]) -> list[str]:
        todo = {n: e for n, e in rec.get("isolated", {}).items() if not e.get("released")}
        for n, e in todo.items():
            if not e.get("audited"):
                raise L.AuditError(f"release-src: {n!r} not yet audited; run `RUN outputs-audit` first")
            dest = Path(e["dest"]).resolve()
            if not L._inside(dest, src_root) or dest == src_root:
                raise L.AuditError(f"release-src: {dest} is outside this run's src dir {src_root}; refused")
        released = []
        for n, e in todo.items():
            dest = Path(e["dest"]).resolve()
            if dest.exists():
                shutil.rmtree(dest, onexc=onexc)
            e["released"] = True
            released.append(n)
        L.add_event(rec, "src-released", released)
        return released

    _print({"released": _mutate(a, apply)})
    return 0


def cmd_write_run_log(a: argparse.Namespace) -> int:
    """Create-only run log (-N on collision), recorded as artifact kind run-log."""
    L.load_record(_rec_path(a))
    body = L.decode_text(Path(a.body_file).read_bytes(), a.body_file)
    path = L.create_exclusive(Path(a.dir), a.stem, ".md", body)

    def apply(rec: dict[str, Any]) -> None:
        rec["artifacts"].append({"kind": "run-log", "path": str(path)})
        L.add_event(rec, "run-log-written", str(path))

    _mutate(a, apply)
    print(path)
    return 0


def cmd_slug(a: argparse.Namespace) -> int:
    print(L.grrp_slug(a.run_id, a.prefix))
    return 0


def cmd_baseline(a: argparse.Namespace) -> int:
    name, _, path = a.root.partition("=")
    info = L.root_info(name, path)
    if a.run_id:  # an isolated root was inspected at its recorded SHA, not the live HEAD
        iso = L.load_record(_rec_path(a)).get("isolated", {}).get(name)
        if iso is not None:
            info = dataclasses.replace(info, head_rev=iso["sha"])
    overrides = dict(o.split("=", 1) for o in a.override or [])
    bl = L.select_baseline(info, a.task_id or L.parse_run_id(a.run_id).task_id,
                           [Path(d) for d in a.report_dir], a.pattern, overrides.get(name),
                           Path(a.state_dir))

    def apply(rec: dict[str, Any]) -> None:
        if rec["states"]["inspection"] != "running":
            raise L.AuditError("baselines are recorded while inspection is running")
        rec["roots"][name] = L.root_entry(info, bl.baseline_rev, bl.window)
        rec["roots"][name]["baseline_source"] = bl.source
        rec["roots"][name]["notes"] = bl.notes
        rec["roots"][name]["baseline_trust"] = bl.trust
        if bl.window == "full" and "no baseline" in bl.notes:
            L.add_event(rec, "no-baseline", name)
        if bl.window == "unchanged":
            L.set_coverage(rec, name, "unchanged", f"unchanged ({info.head_rev[:12]})")

    if a.run_id:
        _mutate(a, apply)
    _print(bl.as_dict())
    return 0


def cmd_write_report(a: argparse.Namespace) -> int:
    L.barrier(L.load_record(_rec_path(a)), "report")
    rec = L.load_record(_rec_path(a))
    if rec["states"]["inspection"] != "running":
        raise L.AuditError("reports are written while inspection is running, before run completion")
    names = a.roots or list(rec["roots"])
    missing = [n for n in names if n not in rec["roots"]]
    if missing:
        raise L.AuditError(f"no recorded baseline for roots {missing}; run `baseline` first")
    roots = {n: {k: rec["roots"][n][k] for k in ("identity", "path_arg", "baseline_rev", "head_rev", "window")}
             for n in names}
    body = Path(a.body_file).read_text(encoding="utf-8-sig")
    required = [x["line"] for x in (rec.get("ctxcheck"), rec.get("hygiene")) if x and x.get("line")]
    absent = [ln for ln in required if ln not in body.splitlines()]
    if absent:
        raise L.AuditError("report body lacks the recorded header line(s) verbatim: " + " | ".join(absent))
    content = L.render_front_matter(rec["task_id"], rec["run_id"], roots) + body
    out = Path(a.dir)
    if a.in_root:  # rule 1: an artifact inside a target root never lands on a protected path
        meta = rec["preflight"]["roots"].get(a.in_root)
        if meta is None:
            raise L.AuditError(f"--in-root {a.in_root!r} is not a preflighted root")
        root_path = Path(meta["path"])
        L.resolve_inside(root_path, os.path.relpath(out.resolve() / f"{a.stem}.md", root_path))
    path = L.create_exclusive(out, a.stem, ".md", content)

    def apply(r: dict[str, Any]) -> None:
        r["artifacts"].append({"kind": "report", "path": str(path), "roots": names})
        L.add_event(r, "report-written", str(path))

    _mutate(a, apply)
    print(path)
    return 0


def cmd_record(a: argparse.Namespace) -> int:
    def apply(rec: dict[str, Any]) -> dict[str, Any]:
        # Order within one call: open inspection, record coverage, then close it.
        if a.inspection == "running":
            L.set_inspection(rec, a.inspection)
        if a.require:
            L.require_checks(rec, a.require)
        for item in a.coverage or []:
            key, _, rest = item.partition("=")
            status, _, note = rest.partition(":")
            L.set_coverage(rec, key, status, note)
        if a.inspection and a.inspection != "running":
            L.set_inspection(rec, a.inspection)
        for art in a.artifact or []:
            kind, _, p = art.partition("=")
            rec["artifacts"].append({"kind": kind, "path": p})
        for led in a.ledger or []:
            if not Path(led).is_file():
                raise L.AuditError(f"--ledger {led}: no such file (pass the path grrp init printed)")
            if led not in rec["ledgers"]:
                rec["ledgers"].append(led)
        if a.gate_file:
            gate = L.read_json(Path(a.gate_file))
            L.set_gate(rec, gate)
        for ev in a.event or []:
            L.add_event(rec, ev)
        if a.run:
            L.set_run(rec, a.run)
        return {"run": rec["run"], "states": rec["states"], "derived": L.derive_run(rec),
                "pending_checks": L.pending_checks(rec)}

    _print(_mutate(a, apply))
    return 0


def _completion(a: argparse.Namespace) -> dict[str, Any] | None:
    """Completion plan from serialized CLI args (preferred) or a --completion-file; never both."""
    args = {"inspected": a.inspected, "remaining": a.remaining, "why": a.why, "evidence": a.evidence,
            "artifacts": a.artifact, "findings": a.findings}
    given = {k: v for k, v in args.items() if v is not None}
    if a.completion_file and given:
        raise L.AuditError("give --completion-file or the plan arguments, not both")
    if a.completion_file:
        return L.read_json(Path(a.completion_file))
    return given or None


def cmd_check(a: argparse.Namespace) -> int:
    completion = _completion(a)
    rid = L.parse_run_id(a.run_id)
    root = L.evidence_dir(Path(a.state_dir), rid.task_id, a.run_id)

    def apply(rec: dict[str, Any]) -> dict[str, Any]:
        entry = L.report_check(rec, a.key, a.state, completion, a.reason or "", evidence_root=root)
        return {"key": a.key, "check": entry, "coverage": rec["coverage"][a.key],
                "pending_checks": L.pending_checks(rec)}

    _print(_mutate(a, apply))
    return 0


def cmd_brief_done(a: argparse.Namespace) -> int:
    _print(_mutate(a, lambda rec: L.brief_done(rec, a.ledger)))
    return 0


def cmd_outputs_audit(a: argparse.Namespace) -> int:
    rid = L.parse_run_id(a.run_id)
    sd = Path(a.state_dir)
    root = L.evidence_dir(sd, rid.task_id, a.run_id)
    rec_path = _rec_path(a)
    prefix = (sd / rid.task_id / a.run_id).as_posix()

    def apply(rec: dict[str, Any]) -> dict[str, Any]:
        return L.outputs_audit(rec, [Path(d) for d in a.scan_dir or []], root,
                               exclude=[rec_path, *(Path(x) for x in a.exclude or [])],
                               exclude_prefixes=[prefix])

    res = _mutate(a, apply)
    _print(res)
    return 1 if res["defect"] else 0


def cmd_dispatch(a: argparse.Namespace) -> int:
    rid = L.parse_run_id(a.run_id)
    root = L.evidence_dir(Path(a.state_dir), rid.task_id, a.run_id)

    def apply(rec: dict[str, Any]) -> dict[str, Any]:
        entry = L.dispatch_shard(rec, a.key, a.shard, a.method, a.output, root)
        attest = [f"method-sha256: {m['path']} <sha256 of the file>" for m in entry["methods"]]
        attest += [f"method-line: {m['path']} {m['line']} <sha256 of line {m['line']}, stripped>"
                   for m in entry["methods"]]
        return {"shard": a.shard, **entry, "child_must_attest": attest}

    _print(_mutate(a, apply))
    return 0


def cmd_evidence_dir(a: argparse.Namespace) -> int:
    rid = L.parse_run_id(a.run_id)
    L.load_record(_rec_path(a))
    d = L.evidence_dir(Path(a.state_dir), rid.task_id, a.run_id)
    d.mkdir(parents=True, exist_ok=True)
    print(d.as_posix())
    return 0


def cmd_ctxcheck_line(a: argparse.Namespace) -> int:
    data = L.read_json(Path(a.ctxcheck_json))
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, dict):
        raise L.AuditError(f"{a.ctxcheck_json}: no summary object")
    verdicts = L.read_json(Path(a.verdicts)) if a.verdicts else []
    if not isinstance(verdicts, list):
        raise L.AuditError(f"{a.verdicts}: verdicts must be a JSON list")
    line = L.ctxcheck_line(summary, verdicts)
    L.refuse_mojibake(a.ctxcheck_json, json.dumps(summary, ensure_ascii=False))
    L.refuse_mojibake(a.verdicts or "verdicts", json.dumps(verdicts, ensure_ascii=False))

    def apply(r: dict[str, Any]) -> None:
        r["ctxcheck"] = {"line": line, "summary": summary, "verdicts": verdicts}
        L.add_event(r, "ctxcheck-line", line)

    _mutate(a, apply)
    print(line)
    return 0


HYG_GATES = (("G3", "check-doc-references.mjs"), ("G4", "check-docs-budget.mjs"))
HYG_LABELS = (("repo", "BIMpossible"), ("workspace", "Workspace"))


# G4 measures documents, so it must see the revision the run inspected, not whatever the working
# tree holds when hygiene-line runs (HEAD may have moved, or the tree may be dirty).
G4_REV_GATES = frozenset({"G4"})


def _run_gate(node: str, path: Path, cwd: Path) -> dict[str, Any]:
    try:
        cp = subprocess.run([node, str(path)], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": False, "pass": False, "detail": f"{type(exc).__name__}: {exc}"}
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-3:]
    return {"ran": cp.returncode in (0, 1), "pass": cp.returncode == 0, "exit": cp.returncode,
            "checker": str(path), "detail": " | ".join(tail)}


def hygiene_results(roots: dict[str, Path], node: str, fallback_dir: Path | None,
                    revs: dict[str, str | None] | None = None) -> dict[str, dict[str, Any]]:
    """Run each gate with cwd = that root (each repo keeps its own config). Exit 0 = ✓, 1 = ✗;
    anything else, or no checker, = the gate did not run (✗ in the line, `ran: false`).
    G4 runs against the root's inspected revision (`revs[name]`): in place when the clean working
    tree is at that revision, else in a temporary detached checkout of it (`L.checkout_revision`,
    read-only on the root); with no recorded revision it measures the working tree and says so in
    `note`."""
    revs = revs or {}
    out: dict[str, dict[str, Any]] = {}
    for name, root in roots.items():
        for gate, script in HYG_GATES:
            path = root / "scripts" / script
            if not path.is_file() and fallback_dir is not None:
                path = fallback_dir / script
            key = f"{name}:{gate}"
            rev = revs.get(name) if gate in G4_REV_GATES else None
            if gate in G4_REV_GATES and rev and (L.head_rev(root) != rev or L.dirty(str(root))):
                with tempfile.TemporaryDirectory(prefix="g4-rev-", ignore_cleanup_errors=True) as td:
                    exp = Path(td) / "tree"
                    L.checkout_revision(root, rev, exp)
                    exported = exp / "scripts" / script
                    checker = exported if exported.is_file() else path
                    if not checker.is_file():
                        out[key] = {"ran": False, "pass": False, "detail": f"{script} not found",
                                    "measured": rev, "note": f"inspected revision {rev[:12]} checkout"}
                        continue
                    res = _run_gate(node, checker, exp)
                if checker == exported:
                    res["checker"] = f"{rev[:12]}:scripts/{script}"
                out[key] = {**res, "measured": rev,
                            "note": f"measured inspected revision {rev[:12]} (working tree differs)"}
                continue
            if not path.is_file():
                out[key] = {"ran": False, "pass": False, "detail": f"{script} not found"}
                continue
            res = _run_gate(node, path, root)
            if gate in G4_REV_GATES:
                res["measured"] = rev or "working-tree"
                res["note"] = (f"working tree is the inspected revision {rev[:12]}" if rev
                               else "no inspected revision recorded; measured working tree")
            out[key] = res
    return out


def _inspected_revs(rec: dict[str, Any], names: Sequence[str]) -> dict[str, str | None]:
    """The revision each root was inspected at: its isolated SHA, else the baseline step's head_rev,
    else preflight's."""
    out: dict[str, str | None] = {}
    for n in names:
        rev = rec.get("isolated", {}).get(n, {}).get("sha") or rec.get("roots", {}).get(n, {}).get("head_rev")
        out[n] = rev or rec.get("preflight", {}).get("roots", {}).get(n, {}).get("head_rev")
    return out


def hygiene_line(results: dict[str, dict[str, Any]]) -> str:
    parts = []
    for name, label in HYG_LABELS:
        marks = " · ".join(f"{g} {'✓' if results.get(f'{name}:{g}', {}).get('pass') else '✗'}" for g, _ in HYG_GATES)
        parts.append(f"{label} {marks}")
    return "**Docs-hygiene (HYG-13):** " + " — ".join(parts)


def cmd_hygiene_line(a: argparse.Namespace) -> int:
    rec_path = _rec_path(a)
    rec = L.load_record(rec_path)
    pre = rec.get("preflight", {})
    roots = {n: Path(pre["roots"][n]["path"]) for n, _ in HYG_LABELS if n in pre.get("roots", {})}
    node = pre.get("tools", {}).get("node") or shutil.which("node")
    if node is None:
        raise L.AuditError("node not on PATH")
    fb = pre.get("tools", {}).get("checker_dir")
    res = hygiene_results(roots, node, Path(fb) if fb else None, _inspected_revs(rec, list(roots)))
    line = hygiene_line(res)
    missing = [n for n, _ in HYG_LABELS if n not in roots]
    not_ran = [k for k, v in res.items() if not v["ran"]] + [f"{n}:*" for n in missing]

    def apply(r: dict[str, Any]) -> None:
        r["hygiene"] = {"line": line, "results": res}
        if not_ran:
            L.set_coverage(r, "hyg13", "lost", "did not run: " + ", ".join(not_ran))
        elif r["coverage"].get("hyg13", {}).get("status") not in L.DEGRADING:
            L.set_coverage(r, "hyg13", "complete", "G3/G4 ran in both roots")
        L.add_event(r, "hygiene-line", line)

    _mutate(a, apply)
    _print({"line": line, "results": res, "not_ran": not_ran})
    return 0


def cmd_wait(a: argparse.Namespace) -> int:
    rec = L.load_record(_rec_path(a))
    pending = L.pending_checks(rec)
    checks = rec.get("checks", {})
    _print({"phase": a.phase, "ready": not pending, "pending": pending,
            "returned": [k for k, v in checks.items() if v["state"] == "returned"],
            "failed": [k for k, v in checks.items() if v["state"] == "failed"],
            "partial": {k: {"remaining": v["remaining"], "why": v["why"]}
                        for k, v in checks.items() if v["remaining"]}})
    return 1 if pending else 0


def cmd_reuse_check(a: argparse.Namespace) -> int:
    name, _, path = a.root.partition("=")
    info = L.root_info(name, path)
    ok, reasons = L.reuse_check(Path(a.sweep_report), Path(a.state_dir), info, a.weekly_baseline or None)
    _print({"reuse": ok, "reasons": reasons, "action": "reuse" if ok else "run /slop-audit nested"})
    return 0 if ok else 1


def cmd_dirty_check(a: argparse.Namespace) -> int:
    lines = L.dirty(a.root)
    _print({"root": a.root, "dirty": bool(lines), "lines": lines})
    return 1 if lines else 0


def cmd_publish(a: argparse.Namespace) -> int:
    _mutate(a, L.publish)
    _print({"published": True})
    return 0


def cmd_deliver(a: argparse.Namespace) -> int:
    def apply(rec: dict[str, Any]) -> None:
        if a.state == "delivered" and rec["states"]["delivery"]["mode"] == "file":
            raise L.AuditError("--deliver file delivers through `RUN deliver-file` (it writes RESULTS.md)")
        L.set_delivery(rec, a.state, a.ref)

    _mutate(a, apply)
    _print({"delivery": a.state, "ref": a.ref})
    return 0


def cmd_deliver_file(a: argparse.Namespace) -> int:
    """D2 (2026-10-30): delivery is a results file. Create-only, after publication."""
    rid = L.parse_run_id(a.run_id)
    rec_path = _rec_path(a)
    rec = L.load_record(rec_path)
    st = rec["states"]
    if st["delivery"]["mode"] != "file":
        raise L.AuditError(f"deliver-file needs --deliver file at start (mode is {st['delivery']['mode']})")
    if st["publication"] != "published":
        raise L.AuditError("delivery follows publication (`RUN publish` first)")
    if st["delivery"]["state"] != "pending":
        raise L.AuditError(f"delivery already {st['delivery']['state']}")
    run_log = a.run_log
    if run_log is not None and not Path(run_log).is_file():
        raise L.AuditError(f"--run-log {run_log}: no such file")
    out_dir = Path(a.state_dir) / rid.task_id / a.run_id
    path = L.create_exclusive(out_dir, "RESULTS", ".md", L.results_markdown(rec, run_log))
    ref = path.resolve().as_posix()
    L.set_delivery(rec, "delivered", ref)
    L.save_record(rec_path, rec)
    _print({"delivery": "delivered", "ref": ref})
    return 0


def cmd_remediation(a: argparse.Namespace) -> int:
    rec_path = _rec_path(a)
    rec = L.load_record(rec_path)
    if a.action == "start":
        repo = rec.get("preflight", {}).get("roots", {}).get("repo", {}).get("path")
        lines = L.dirty(repo) if repo and rec["flags"].get("autofix") else []
        go = L.start_remediation(rec, lines)
        L.save_record(rec_path, rec)
        _print({"proceed": go, "remediation": rec["states"]["remediation"],
                "reason": rec["states"]["remediation_reason"]})
        return 0 if go else 1
    outcome = L.finish_remediation(rec)
    L.save_record(rec_path, rec)
    HB.remediation_update(_watchdog_dir(a), rec["task_id"], rec["run_id"], outcome, task=a.watchdog_task)
    _print({"remediation": outcome, "log": rec["remediation_log"]})
    return 0 if outcome == "ok" else 1


def cmd_fix(a: argparse.Namespace) -> int:
    rec_path = _rec_path(a)
    rec = L.load_record(rec_path)
    if rec["states"]["remediation"] != "running":
        raise L.AuditError("fixes run only inside a running remediation stage (rule 7)")
    repo = Path(rec["preflight"]["roots"]["repo"]["path"])
    fdir = L.fixes_dir(Path(a.state_dir), rec["task_id"], rec["run_id"])
    if a.action == "snapshot":
        out: Any = L.fix_snapshot(repo, fdir, a.fix_id, a.file or [])
    elif a.action == "post":
        out = L.fix_post(repo, fdir, a.fix_id)
        rec["remediation_log"].append({"fix_id": a.fix_id, "status": "applied",
                                       "files": [f["path"] for f in out["files"]], "detail": None})
    else:
        out = L.fix_rollback(repo, fdir, a.fix_id)
        rec["remediation_log"].append(out)
    L.save_record(rec_path, rec)
    _print(out)
    status = out.get("status") if isinstance(out, dict) else None
    return 1 if status and status != "reverted" and a.action == "rollback" else 0


def cmd_finalize(a: argparse.Namespace) -> int:
    rec_path = _rec_path(a)
    rec = L.load_record(rec_path)
    L.finalize_failed(rec, a.reason)
    L.save_record(rec_path, rec)
    # explicit flag, else what start persisted, else env/default (records older than the persisted field)
    stored = rec.get("watchdog") or {}
    explicit_dir = getattr(a, "watchdog_dir", None)
    wd = Path(explicit_dir) if explicit_dir else Path(stored["dir"]) if stored.get("dir") else _watchdog_dir(a)
    task = a.watchdog_task or stored.get("task")
    hb = HB.heartbeat_path(wd, task or rec["task_id"], rec["run_id"])
    hb_found = hb.exists()
    if hb_found:
        hbj = L.read_json(hb)
        if not hbj.get("finished_at"):
            HB.finish(wd, rec["task_id"], rec["run_id"], rec["run"], rec["ledgers"], a.reason,
                      rec["states"]["remediation"], task)
        elif rec["states"]["remediation"] != hbj.get("remediation"):
            HB.remediation_update(wd, rec["task_id"], rec["run_id"], rec["states"]["remediation"], a.reason, task)
    _print({"run": rec["run"], "states": rec["states"], "failure_reason": a.reason,
            "heartbeat": str(hb) if hb_found else None})
    if not hb_found:
        print(f"finalize: heartbeat not found at {hb}; the record is finalized but the watchdog will see this "
              "run as hung. Re-run finalize with the --watchdog-dir/--watchdog-task that start used.",
              file=sys.stderr)
        return 1
    return 0


def cmd_finish(a: argparse.Namespace) -> int:
    rec = L.load_record(_rec_path(a))
    if rec["run"] is None:
        raise L.AuditError("declare the run outcome first (record --run)")
    notes = [f"{k}: {v['note'] or v['status']}" for k, v in rec["coverage"].items() if v["status"] != "complete"]
    p = HB.finish(_watchdog_dir(a), rec["task_id"], rec["run_id"], rec["run"], rec["ledgers"],
                  "; ".join(notes), rec["states"]["remediation"], a.watchdog_task)
    print(p)
    return 0


def cmd_personas(a: argparse.Namespace) -> int:
    _print(discover_personas([Path(r) for r in a.repo or []]))
    return 0


# --------------------------------------------------------------------------- argparse


def _preflight_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--task", required=True, choices=L.TASK_IDS)
    p.add_argument("--root", action="append", default=[], help="name=path; weekly needs repo= and workspace=")
    p.add_argument("--deliver", choices=L.DELIVER_MODES)
    p.add_argument("--autofix", action="store_true")
    p.add_argument("--handoff", action="store_true")
    p.add_argument("--tools-root")
    p.add_argument("--grrp")
    p.add_argument("--checker-dir")
    p.add_argument("--hyg8-live-root")
    p.add_argument("--hyg8-baseline-root")
    p.add_argument("--checklist", help="step-10 Checklist file, or 'none' for an isolated validation run")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-id", required=True)
        p.add_argument("--state-dir", required=True)

    p = sub.add_parser("preflight"); _preflight_args(p); p.set_defaults(fn=cmd_preflight)
    p = sub.add_parser("start"); _preflight_args(p)
    p.add_argument("--state-dir", required=True); p.add_argument("--watchdog-dir")
    p.add_argument("--watchdog-task"); p.set_defaults(fn=cmd_start)
    p = sub.add_parser("slug"); p.add_argument("--run-id", required=True); p.add_argument("--prefix")
    p.set_defaults(fn=cmd_slug)
    p = sub.add_parser("baseline"); p.add_argument("--root", required=True, help="name=path")
    p.add_argument("--report-dir", action="append", default=[]); p.add_argument("--pattern", required=True)
    p.add_argument("--override", action="append", help="name=rev"); p.add_argument("--task-id")
    p.add_argument("--run-id"); p.add_argument("--state-dir", required=True); p.set_defaults(fn=cmd_baseline)
    p = sub.add_parser("write-report"); run_args(p); p.add_argument("--dir", required=True)
    p.add_argument("--stem", required=True); p.add_argument("--body-file", required=True)
    p.add_argument("--roots", nargs="*"); p.add_argument("--in-root", help="root name the --dir lies in")
    p.set_defaults(fn=cmd_write_report)
    p = sub.add_parser("record"); run_args(p)
    p.add_argument("--inspection", choices=("running", "complete", "failed"))
    p.add_argument("--coverage", action="append", help="key=status[:note]")
    p.add_argument("--require", nargs="+", action="extend", help="register required checks (pending) before dispatch")
    p.add_argument("--artifact", action="append", help="kind=path"); p.add_argument("--ledger", action="append")
    p.add_argument("--gate-file"); p.add_argument("--event", action="append")
    p.add_argument("--run", choices=tuple(L.RUN_SEVERITY)); p.set_defaults(fn=cmd_record)
    p = sub.add_parser("check"); run_args(p); p.add_argument("--key", required=True)
    p.add_argument("--state", required=True, choices=("returned", "failed"))
    p.add_argument("--completion-file", help="JSON {inspected:[], remaining:[], why, evidence}")
    p.add_argument("--inspected", nargs="+", action="extend"); p.add_argument("--remaining", nargs="*", action="extend")
    p.add_argument("--why"); p.add_argument("--evidence")
    p.add_argument("--artifact", action="append", help="declared artifact under the evidence dir")
    p.add_argument("--findings", help="findings JSON under the evidence dir (validated)")
    p.add_argument("--reason"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("evidence-dir"); run_args(p); p.set_defaults(fn=cmd_evidence_dir)
    p = sub.add_parser("brief-done"); run_args(p)
    p.add_argument("--ledger", required=True, help="a ledger already recorded with `record --ledger`")
    p.set_defaults(fn=cmd_brief_done)
    p = sub.add_parser("outputs-audit"); run_args(p)
    p.add_argument("--scan-dir", action="append", default=[],
                   help="run-owned dir to scan (declared output dir); never a live target root")
    p.add_argument("--exclude", action="append", help="a file the run legitimately wrote outside the record")
    p.set_defaults(fn=cmd_outputs_audit)
    p = sub.add_parser("isolate"); run_args(p); p.add_argument("--root", required=True, help="name=path")
    p.add_argument("--rev", default="HEAD", help="revision to inspect (default HEAD)")
    p.set_defaults(fn=cmd_isolate)
    p = sub.add_parser("release-src"); run_args(p); p.set_defaults(fn=cmd_release_src)
    p = sub.add_parser("write-run-log"); run_args(p); p.add_argument("--dir", required=True)
    p.add_argument("--stem", required=True); p.add_argument("--body-file", required=True)
    p.set_defaults(fn=cmd_write_run_log)
    p = sub.add_parser("dispatch"); run_args(p); p.add_argument("--key", required=True)
    p.add_argument("--shard", required=True)
    p.add_argument("--method", action="append", required=True, help="method file the child must load")
    p.add_argument("--output", action="append", required=True, help="create-only output under the evidence dir")
    p.set_defaults(fn=cmd_dispatch)
    p = sub.add_parser("ctxcheck-line"); run_args(p); p.add_argument("--ctxcheck-json", required=True)
    p.add_argument("--verdicts", help="JSON list, one {id, verdict: CONFIRMED|REFUTED} per WARN")
    p.set_defaults(fn=cmd_ctxcheck_line)
    p = sub.add_parser("wait"); run_args(p); p.add_argument("--phase", required=True, choices=L.BARRIER_PHASES)
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("hygiene-line"); run_args(p); p.set_defaults(fn=cmd_hygiene_line)
    p = sub.add_parser("reuse-check"); p.add_argument("--sweep-report", required=True)
    p.add_argument("--state-dir", required=True); p.add_argument("--root", required=True)
    p.add_argument("--weekly-baseline"); p.set_defaults(fn=cmd_reuse_check)
    p = sub.add_parser("dirty-check"); p.add_argument("--root", required=True); p.set_defaults(fn=cmd_dirty_check)
    p = sub.add_parser("publish"); run_args(p); p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("deliver"); run_args(p)
    p.add_argument("--state", required=True, choices=L.DELIVERY[1:]); p.add_argument("--ref")
    p.set_defaults(fn=cmd_deliver)
    p = sub.add_parser("deliver-file"); run_args(p)
    p.add_argument("--run-log", help="the run log path, if not recorded as artifact kind run-log")
    p.set_defaults(fn=cmd_deliver_file)
    p = sub.add_parser("remediation"); run_args(p); p.add_argument("action", choices=("start", "finish"))
    p.add_argument("--watchdog-dir"); p.add_argument("--watchdog-task"); p.set_defaults(fn=cmd_remediation)
    p = sub.add_parser("fix"); run_args(p); p.add_argument("action", choices=("snapshot", "post", "rollback"))
    p.add_argument("--fix-id", required=True); p.add_argument("--file", action="append")
    p.set_defaults(fn=cmd_fix)
    p = sub.add_parser("finalize"); run_args(p); p.add_argument("--reason", required=True)
    p.add_argument("--watchdog-dir"); p.add_argument("--watchdog-task"); p.set_defaults(fn=cmd_finalize)
    p = sub.add_parser("finish"); run_args(p); p.add_argument("--watchdog-dir")
    p.add_argument("--watchdog-task"); p.set_defaults(fn=cmd_finish)
    p = sub.add_parser("personas"); p.add_argument("--repo", action="append"); p.set_defaults(fn=cmd_personas)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # JSON output carries ✓/✗; a redirected cp1252 console cannot
    try:
        return int(a.fn(a))
    except (L.AuditError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"audit_run {a.cmd}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
