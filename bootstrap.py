#!/usr/bin/env python3
"""bootstrap.py - install this profile on a Linux/macOS machine.

The POSIX port of bootstrap.ps1, same contract:

  ~/.claude/CLAUDE.md      one-line @import pointer at this repo's CLAUDE.md
  ~/.claude/skills         symlink into this repo's skills/ (a real directory is moved
                           aside as a *.pre-profile-backup-<stamp>; a desktop-synced
                           skills/synced/ inside it is copied back, since it is
                           runtime-managed and git-ignored)
  ~/.claude/hooks/*        pinned copies of hooks/*.py, *.sh, *.ps1 and
                           closure_audit_repos.json, plus every vendor/<unit>/ whole
                           (C1 in hooks/README.md - settings.json never points at
                           this working tree)
  ~/.claude/settings.json  merged with settings.template.json exactly as the
                           PowerShell version merges it (template hooks replace the
                           ones it owns, additionalDirectories is a union, plugin,
                           marketplace and env entries are added when missing, every other
                           existing key is kept; an empty or unparseable file is
                           refused, a changed file is backed up first)

Platform adaptation of the template (the template stays Windows-shaped; nothing
else in the repo forks):
  - command "python"  -> "python3"
  - powershell ... session-start-pull.ps1 -> bash ... session-start-pull.sh
  - Show-AuditAlerts.ps1 is dropped: it reads the Windows audit-watchdog state,
    which has no Linux counterpart.
  - claude_desktop_config.json is not written: Claude Desktop is not available on
    Linux. (On macOS, write it by hand from mcp/ - deliberately out of scope here.)

Everything before "Writes" only reads and computes, so a refusal leaves the
machine untouched. Re-running is safe and is the deliberate update step.

Usage:  python3 bootstrap.py [--allow-uncommitted-hooks]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
HOOK_DATA_FILES = {"closure_audit_repos.json"}
HOOK_SOURCE_SUFFIXES = {".py", ".ps1", ".sh"}
PROFILE_OWNED_KEYS = ("hooks", "permissions", "enabledPlugins", "extraKnownMarketplaces")
PROJECT_DIR_NAMES = ("BIMpossible", "BIMpossible-Workspace", "BIMpossible_Workspace")
# Registrations that ran the same script from its upstream checkout before it was
# pinned here; an existing one is replaced by the template's pinned entry.
SUPERSEDED_HOOK_PATHS = (
    "F:/BIMpossible-Workspace/system/scheduled-tasks/audit-watchdog/Show-AuditAlerts.ps1",
    "F:/Claude-Tools/evidence-capture/capture_start.py",
    "F:/Claude-Tools/evidence-capture/capture_end.py",
)
HOOK_TOKEN = "__CLAUDE_HOOKS_DIR__"
DIRS_TOKEN = '"__ADDITIONAL_DIRECTORIES__"'


class Refuse(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"bootstrap refused: {message}")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def say(msg: str) -> None:
    print(msg, flush=True)


# ---- settings merge (port of Merge-ProfileSettings & co.) -------------------------

def is_obj(v: object) -> bool:
    return isinstance(v, dict)


def merge_profile_settings(existing: dict | None, generated: dict) -> dict:
    """Existing settings with only the profile-owned parts updated: hooks replaced,
    permissions.additionalDirectories unioned, plugin/marketplace maps gain missing
    entries, every other template key is a default used only when absent."""
    out: dict = dict(existing or {})
    for name, value in generated.items():
        current = out.get(name)
        if name == "hooks":
            out[name] = value
        elif name == "permissions":
            perm = dict(current) if is_obj(current) else {}
            for k, v in value.items():
                if k != "additionalDirectories" and k not in perm:
                    perm[k] = v
            candidates = list(perm.get("additionalDirectories") or []) + list(value.get("additionalDirectories") or [])
            seen: set[str] = set()
            dirs: list[str] = []
            for d in candidates:
                if not isinstance(d, str) or not d.strip():
                    continue
                key = d.replace("\\", "/").rstrip("/").lower()
                if key not in seen:
                    seen.add(key)
                    dirs.append(d)
            perm["additionalDirectories"] = dirs
            out[name] = perm
        elif name in ("enabledPlugins", "extraKnownMarketplaces", "env"):
            m = dict(current) if is_obj(current) else {}
            for k, v in value.items():
                m.setdefault(k, v)
            out[name] = m
        elif name not in out:
            out[name] = value
    return out


def leaf_paths(node: object, path: str, into: dict[str, str]) -> None:
    if is_obj(node):
        if not node:
            into[path] = "{}"
        for k, v in node.items():
            leaf_paths(v, f"{path}/{k}", into)
    else:
        into[path] = json.dumps(node, sort_keys=True, separators=(",", ":"))


def settings_changes(before: dict | None, after: dict) -> list[tuple[str, str]]:
    b: dict[str, str] = {}
    a: dict[str, str] = {}
    if before is not None:
        leaf_paths(before, "", b)
    leaf_paths(after, "", a)
    changes = [("removed", k) for k in b if k not in a]
    changes += [("changed", k) for k in b if k in a and a[k] != b[k]]
    changes += [("added", k) for k in a if k not in b]
    return changes


def print_plan(changes: list[tuple[str, str]]) -> None:
    counts = {"added": 0, "removed": 0, "changed": 0}
    lines = set()
    for kind, path in changes:
        counts[kind] += 1
        lines.add(f"  {kind} {re.sub(r'^/env/.*', '/env/*', path)}")
    say(f"settings.json plan: {counts['added']} added, {counts['removed']} removed, {counts['changed']} changed")
    for line in sorted(lines):
        say(line)


def unmanaged_hook_groups(existing_hooks: dict, generated_hooks: dict, managed_roots: list[str],
                          managed_names: list[str], superseded: tuple[str, ...]) -> list[tuple[str, dict]]:
    """Hook groups in the existing settings that still hold handlers the template
    does not own (see Get-UnmanagedHookGroups in bootstrap.ps1)."""
    template_prompts = {h.get("prompt") for groups in generated_hooks.values() for g in groups
                        for h in g.get("hooks", []) if h.get("type") == "prompt"}
    roots = [r.replace("\\", "/").rstrip("/").lower() + "/" for r in managed_roots]
    pinned = ["/.claude/hooks/" + n.lower() for n in managed_names]
    sup = [p.replace("\\", "/").lower() for p in superseded]
    result = []
    for event, groups in existing_hooks.items():
        for group in groups or []:
            kept = []
            for h in group.get("hooks", []):
                if h.get("type") == "prompt":
                    if h.get("prompt") not in template_prompts:
                        kept.append(h)
                    continue
                text = " ".join([str(h.get("command", ""))] + [str(a) for a in (h.get("args") or [])])
                text = text.replace("\\", "/").lower()
                owned = any(r in text for r in roots) or any(n in text for n in pinned) or any(s in text for s in sup)
                if not owned:
                    kept.append(h)
            if kept:
                copy = {}
                if "matcher" in group:
                    copy["matcher"] = group["matcher"]
                copy["hooks"] = kept
                result.append((event, copy))
    return result


# ---- platform adaptation of the template --------------------------------------------

def adapt_handler(h: dict) -> dict | None:
    """Windows-shaped command handler -> POSIX; None drops a Windows-only hook."""
    if h.get("type") != "command":
        return h
    cmd = h.get("command")
    args = list(h.get("args") or [])
    if cmd == "python":
        return {**h, "command": "python3"}
    if cmd == "powershell":
        script = args[-1] if args else ""
        if script.endswith("/session-start-pull.ps1"):
            return {**h, "command": "bash", "args": [script[:-len(".ps1")] + ".sh"]}
        return None
    return h


def adapt_hooks(hooks: dict) -> dict:
    out: dict = {}
    for event, groups in hooks.items():
        kept_groups = []
        for group in groups:
            handlers = [a for a in (adapt_handler(h) for h in group.get("hooks", [])) if a is not None]
            if handlers:
                kept_groups.append({**group, "hooks": handlers})
        if kept_groups:
            out[event] = kept_groups
    return out


def registered_hook_files(hooks: dict, hooks_dir: str) -> list[str]:
    prefix = hooks_dir.rstrip("/") + "/"
    found = set()
    for groups in hooks.values():
        for g in groups:
            for h in g.get("hooks", []):
                for a in [h.get("command", "")] + list(h.get("args") or []):
                    if isinstance(a, str) and a.startswith(prefix):
                        found.add(a[len(prefix):])
    return sorted(found)


# ---- file helpers ---------------------------------------------------------------------

def backup_if_real_file(path: Path, expected: str) -> None:
    if not path.exists():
        return
    if path.read_text(encoding="utf-8").strip() == expected.strip():
        return
    dest = path.with_name(f"{path.name}.pre-profile-backup-{stamp()}")
    shutil.copy2(path, dest)
    say(f"Backed up existing file to {dest}")


def backup_if_real_directory(path: Path, expected_target: Path) -> None:
    if path.is_symlink():
        try:
            current = path.resolve()
        except OSError:
            current = None
        if current == expected_target.resolve():
            return
        path.unlink()  # removes only the link, never the target's contents
        say(f"Removed stale skills symlink (was pointing at {current}) - its old target is untouched")
        return
    if not path.exists():
        return
    dest = path.with_name(f"{path.name}.pre-profile-backup-{stamp()}")
    path.rename(dest)
    say(f"Backed up existing directory to {dest}")
    synced = dest / "synced"
    if synced.is_dir() and not (expected_target / "synced").exists():
        shutil.copytree(synced, expected_target / "synced")
        say("Copied runtime-managed skills/synced back into the repo skills dir (git-ignored)")


def unit_files(unit: Path) -> list[Path]:
    return sorted(p for p in unit.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def unit_identical(unit: Path, target: Path) -> bool:
    if not target.is_dir():
        return False
    src = unit_files(unit)
    inst = unit_files(target)
    if len(src) != len(inst):
        return False
    for f in src:
        t = target / f.relative_to(unit)
        if not t.is_file() or t.read_bytes() != f.read_bytes():
            return False
    return True


# ---- main -----------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-uncommitted-hooks", action="store_true",
                    help="install hook sources even when they have uncommitted changes (C1 override)")
    opts = ap.parse_args(argv)

    if os.name == "nt":
        raise Refuse("this is the POSIX bootstrap; on Windows run bootstrap.ps1")
    if " " in str(REPO_ROOT):
        raise Refuse(f"repo path contains a space: {REPO_ROOT}\nClaude Code's @import silently fails on "
                     "such paths (anthropics/claude-code#56927). Move or re-clone this repo to a path "
                     "without spaces, e.g. ~/workspace/claude-profile, then run bootstrap again.")

    # ---- Reads and checks; nothing is written until every check passes. ----
    hooks_src_dir = REPO_ROOT / "hooks"
    hook_sources = sorted(p for p in hooks_src_dir.iterdir()
                          if p.is_file() and (p.suffix in HOOK_SOURCE_SUFFIXES or p.name in HOOK_DATA_FILES))
    vendor_root = REPO_ROOT / "vendor"
    vendor_units = sorted(p for p in vendor_root.iterdir() if p.is_dir()) if vendor_root.is_dir() else []

    template_raw = (REPO_ROOT / "settings.template.json").read_text(encoding="utf-8")

    hooks_revision = "unknown (no git)"
    if shutil.which("git") and (REPO_ROOT / ".git").exists():
        paths = [f"hooks/{p.name}" for p in hook_sources] + [f"vendor/{u.name}" for u in vendor_units]
        proc = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", *paths],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise Refuse(f"git status failed in {REPO_ROOT} (exit {proc.returncode})")
        dirty = [l for l in proc.stdout.splitlines() if l.strip()]
        if dirty and not opts.allow_uncommitted_hooks:
            raise Refuse("hook sources have uncommitted changes:\n  " + "\n  ".join(dirty) +
                         "\n\nBootstrap installs them as pinned copies under ~/.claude/hooks (C1), which must "
                         "be reviewed code. Commit (or discard) these changes and run bootstrap again, or "
                         "pass --allow-uncommitted-hooks to install them anyway.")
        sha = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        hooks_revision = sha + (" (with uncommitted hook changes)" if dirty else "")

    claude_dir = Path.home() / ".claude"
    claude_md = claude_dir / "CLAUDE.md"
    settings_path = claude_dir / "settings.json"
    skills_path = claude_dir / "skills"
    skills_target = REPO_ROOT / "skills"
    hooks_dir = claude_dir / "hooks"
    if '"' in str(hooks_dir):
        raise Refuse(f"hooks directory path contains a double quote: {hooks_dir}")
    say(f"Repo root: {REPO_ROOT}")
    say(f"Claude dir: {claude_dir}")

    existing: dict | None = None
    if settings_path.exists():
        text = settings_path.read_text(encoding="utf-8")
        if not text.strip():
            raise Refuse("existing settings.json is empty; refusing to replace it. Restore it from a "
                         "backup or delete it, then run bootstrap again.")
        try:
            existing = json.loads(text)
        except ValueError as exc:
            raise Refuse(f"existing settings.json is not valid JSON; refusing to replace it: {exc}")
        if existing is None:
            raise Refuse("existing settings.json is JSON null, not a settings object; refusing to replace it.")
        if not is_obj(existing):
            raise Refuse("existing settings.json is not a JSON object; refusing to replace it.")
        for name in existing:
            lowered = [k for k in PROFILE_OWNED_KEYS if k.lower() == name.lower()]
            if lowered and name not in PROFILE_OWNED_KEYS:
                raise Refuse(f"existing settings.json has a '{name}' key; Claude Code only reads "
                             f"'{lowered[0]}' (case-sensitive). Rename the key, then run bootstrap again.")
        for key in PROFILE_OWNED_KEYS:
            if key in existing and existing[key] is not None and not is_obj(existing[key]):
                raise Refuse(f"existing settings.json has a '{key}' value that is not a JSON object.")

    # Known project directories: the Windows version scans drive roots; here the home
    # directory and ~/workspace.
    additional_dirs: list[str] = []
    for root in (Path.home(), Path.home() / "workspace"):
        for name in PROJECT_DIR_NAMES:
            cand = root / name
            if cand.is_dir() and str(cand) not in additional_dirs:
                additional_dirs.append(str(cand))
                say(f"Project directory detected: {cand}")
    if not additional_dirs:
        say("No known project directories found - additionalDirectories will be empty")

    dirs_json = ",\n      ".join(json.dumps(d) for d in additional_dirs)
    settings_raw = template_raw.replace(DIRS_TOKEN, dirs_json).replace(HOOK_TOKEN, str(hooks_dir))
    leftover = sorted(set(re.findall(r"__[A-Z_]+__", settings_raw)))
    if leftover:
        raise Refuse(f"generated settings.json still contains unresolved tokens: {', '.join(leftover)}")
    try:
        generated = json.loads(settings_raw)
    except ValueError as exc:
        raise Refuse(f"generated settings.json is not valid JSON: {exc}")
    generated["hooks"] = adapt_hooks(generated.get("hooks", {}))
    # The skills symlink means `graphify` (the graphifyy CLI) would rewrite this repo's
    # pinned skills/graphify on every run; its own opt-out is this variable.
    generated.setdefault("env", {}).setdefault("GRAPHIFY_NO_AUTO_REFRESH", "1")

    # Every hook the (adapted) template registers must exist in hooks/ or vendor/.
    registered = registered_hook_files(generated["hooks"], str(hooks_dir))
    source_names = {p.name for p in hook_sources}
    missing = [f for f in registered
               if ("/" in f and not (vendor_root / f).is_file()) or ("/" not in f and f not in source_names)]
    if missing:
        raise Refuse(f"settings.template.json registers hooks missing from hooks/ or vendor/: {', '.join(missing)}")

    preserved: list[tuple[str, dict]] = []
    if existing and is_obj(existing.get("hooks")):
        preserved = unmanaged_hook_groups(existing["hooks"], generated["hooks"],
                                          [str(hooks_dir), str(REPO_ROOT / "hooks")],
                                          [p.name for p in hook_sources], SUPERSEDED_HOOK_PATHS)
    for event, group in preserved:
        generated["hooks"].setdefault(event, []).append(group)

    merged = merge_profile_settings(existing, generated)
    if existing is not None:
        lost = [k for k in existing if k not in merged]
        if lost:
            raise Refuse(f"settings merge would drop top-level keys: {', '.join(lost)} - refusing to write.")
    merged_raw = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
    changes = settings_changes(existing, merged)

    # ---- Writes. Every check has passed. ----
    claude_dir.mkdir(parents=True, exist_ok=True)

    # 1. CLAUDE.md stub
    stub = f"@{REPO_ROOT.as_posix()}/CLAUDE.md"
    backup_if_real_file(claude_md, stub)
    claude_md.write_text(stub, encoding="utf-8")
    say(f"Wrote CLAUDE.md stub: {stub}")

    # 2. skills symlink
    backup_if_real_directory(skills_path, skills_target)
    if not skills_path.exists() and not skills_path.is_symlink():
        skills_path.symlink_to(skills_target, target_is_directory=True)
        say(f"Created skills symlink -> {skills_target}")
    else:
        say("skills symlink already present")

    # 3. pinned hook copies (C1)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    say(f"Installing pinned hooks from revision {hooks_revision} -> {hooks_dir}")
    for src in hook_sources:
        data = src.read_bytes()
        if src.name == "session-start-pull.sh":
            text = data.decode("utf-8")
            derived = 'REPO_ROOT="$(cd "$HOOK_DIR/.." && pwd)"'
            if derived not in text:
                raise Refuse("session-start-pull.sh no longer derives REPO_ROOT as bootstrap expects; update the pinning step.")
            quoted = "'" + str(REPO_ROOT).replace("'", "'\\''") + "'"
            data = text.replace(derived, f"REPO_ROOT={quoted}  # pinned by bootstrap (C1): this copy runs outside the repo").encode("utf-8")
        target = hooks_dir / src.name
        if target.exists():
            if target.read_bytes() == data:
                continue
            dest = target.with_name(f"{target.name}.pre-profile-backup-{stamp()}")
            shutil.copy2(target, dest)
            say(f"Backed up installed hook to {dest}")
        target.write_bytes(data)
        if src.suffix == ".sh":
            target.chmod(target.stat().st_mode | 0o111)
        say(f"Installed hook: {target}")

    for unit in vendor_units:
        target = hooks_dir / unit.name
        if unit_identical(unit, target):
            continue
        staging = target.with_name(target.name + ".installing")
        if staging.exists():
            shutil.rmtree(staging)
        for f in unit_files(unit):
            dest = staging / f.relative_to(unit)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        if target.exists():
            backup = target.with_name(f"{target.name}.pre-profile-backup-{stamp()}")
            target.rename(backup)
            say(f"Backed up installed unit to {backup}")
        staging.rename(target)
        say(f"Installed unit: {target} ({len(unit_files(unit))} files)")

    for f in registered:
        if not (hooks_dir / f).exists():
            raise Refuse(f"template registers a hook that was not installed: {hooks_dir / f}")

    # 4. settings.json
    for event, group in preserved:
        say(f"Kept {len(group['hooks'])} machine-specific {event} hook(s) not owned by the template")
    if existing is not None and not changes:
        say("settings.json already correct - left untouched")
    else:
        print_plan(changes)
        backup_if_real_file(settings_path, merged_raw)
        settings_path.write_text(merged_raw, encoding="utf-8")
        say("Wrote settings.json")

    # 5. Claude Desktop config: not on Linux.
    say("claude_desktop_config.json: skipped (Claude Desktop is not available on this platform)")

    say("")
    say("Bootstrap complete.")
    say("Verify with: /context (check CLAUDE.md and skills load) and /plugin list")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refuse as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
