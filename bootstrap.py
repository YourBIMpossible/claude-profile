#!/usr/bin/env python3
"""bootstrap.py - install this profile for the current user, on Windows, Linux or macOS.

Run it from a clone anywhere (any folder, any drive): nothing here names a machine
path. Windows users can also run bootstrap.ps1, which calls this and then installs REA.

What it writes (each replaced file is backed up first as
<name>.pre-profile-backup-<stamp>; nothing is deleted):

  ~/.claude/profile-machine.json  the machine config: roots this clone cannot derive
                                  (tools/machine_config.py). Detected on first run;
                                  stored values are kept on re-runs, flags override.
  ~/.claude/CLAUDE.md             one-line @import of this clone's CLAUDE.md
  ~/.claude/skills                a junction (Windows) / symlink into this clone's skills/
  ~/.claude/settings.json         merged with settings.template.json (tools/profile_setup.py);
                                  hooks run straight from this clone
  Claude Desktop config           Windows only: mcpServers replaced, every other byte kept
  core.hooksPath = .githooks      in this clone and the claude-tools clone, so their versioned
                                  git hooks run (pre-commit secret scan, pre-push path scan).
                                  A value someone else set is left alone. --no-git-hooks skips it.

After this one run, a merged change to the hooks, the settings template or the text
reaches the machine through the session-start hook (hooks/session_start.py): it
fast-forwards this clone and re-applies the settings merge. Re-run bootstrap only to
move the clone, change a root, or repair the skills link / CLAUDE.md stub.

Everything up to "Writes" only reads and checks, so a refusal leaves the machine as
it was. --dry-run stops there and prints what would be written.

Usage:
  python bootstrap.py [--dry-run] [--repos-root DIR] [--tools-root DIR]
                      [--collab-mailbox DIR | --no-collab-mailbox] [--no-git-hooks] [--yes]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
import machine_config  # noqa: E402
import profile_setup as ps  # noqa: E402
from profile_setup import Refuse  # noqa: E402


def say(msg: str = "") -> None:
    print(msg, flush=True)


def is_link(path: Path) -> bool:
    """A symlink or a Windows junction (any reparse point), never followed."""
    if path.is_symlink():
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def link_target(path: Path) -> Path | None:
    try:
        return Path(os.readlink(path))
    except OSError:
        return None


def same_path(a: Path | None, b: Path) -> bool:
    if a is None:
        return False
    norm = lambda p: os.path.normcase(str(p)).replace("\\\\?\\", "").rstrip("\\/")  # noqa: E731
    return norm(a) == norm(b) or norm(a.resolve()) == norm(b.resolve())


def make_dir_link(link: Path, target: Path) -> str:
    if os.name == "nt":
        import _winapi  # junctions need no privilege, unlike symlinks
        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    link.symlink_to(target, target_is_directory=True)
    return "symlink"


HOOKS_DIR = ".githooks"


@dataclass
class HooksPlan:
    """core.hooksPath in one clone: "set" (unset now), "already" (.githooks) or "foreign" (left alone)."""
    clone: Path
    action: str
    current: str


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


def plan_git_hooks(clone: Path) -> HooksPlan | None:
    """None when `clone` is not the top of a git work tree that ships .githooks/pre-push, or git is missing."""
    if not (clone / HOOKS_DIR / "pre-push").is_file() or shutil.which("git") is None:
        return None
    try:
        top = _git(clone, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or not same_path(Path(top.stdout.strip()), clone):
            return None
        current = _git(clone, "config", "--local", "--get", "core.hooksPath").stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not current:
        return HooksPlan(clone, "set", "")
    resolved = Path(current) if Path(current).is_absolute() else clone / current
    return HooksPlan(clone, "already" if same_path(resolved, clone / HOOKS_DIR) else "foreign", current)


def apply_git_hooks(plan: HooksPlan) -> str:
    if plan.action == "already":
        return f"git hooks already on in {plan.clone} (core.hooksPath = {plan.current})"
    if plan.action == "foreign":
        return (f"Note: {plan.clone} has core.hooksPath = {plan.current} - left alone, so its {HOOKS_DIR} "
                f"hooks do not run. To use them: git -C \"{plan.clone}\" config core.hooksPath {HOOKS_DIR}")
    proc = _git(plan.clone, "config", "--local", "core.hooksPath", HOOKS_DIR)
    if proc.returncode != 0:
        raise Refuse(f"git config core.hooksPath failed in {plan.clone}:\n{proc.stderr.strip()}")
    return f"Turned on git hooks in {plan.clone} (core.hooksPath = {HOOKS_DIR})"


def given_folder(flag: str, value: str) -> str:
    """An explicitly passed root, resolved; a typo must not be stored as a root."""
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise Refuse(f"{flag} is not a folder: {path}\nCheck the path, then run bootstrap again.")
    return str(path)


def build_config(opts: argparse.Namespace, stored: dict) -> dict:
    given = dict(stored)
    if opts.repos_root:
        new_root = given_folder("--repos-root", opts.repos_root)
        if stored.get("repos_root") and os.path.normcase(stored["repos_root"]) != os.path.normcase(new_root):
            for key in ("tools_root", "reports_dir", "evidence_archive", "additional_directories"):
                given.pop(key, None)  # re-detect under the new repos root
        given["repos_root"] = new_root
    if opts.tools_root:
        given["tools_root"] = given_folder("--tools-root", opts.tools_root)
    if opts.no_collab_mailbox:
        given["collab_mailbox"] = ""
    elif opts.collab_mailbox is not None:
        given["collab_mailbox"] = str(Path(opts.collab_mailbox).expanduser()) if opts.collab_mailbox else ""
    elif "collab_mailbox" not in stored and not opts.yes and not opts.dry_run and sys.stdin.isatty():
        answer = input("Claude Collab mailbox folder for this machine (empty = not used): ").strip()
        given["collab_mailbox"] = answer.strip('"')
    cfg = machine_config.detect(given, REPO_ROOT)
    cfg = {"schema": machine_config.SCHEMA, **cfg}
    return cfg


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="check and print the plan; write nothing")
    ap.add_argument("--repos-root", help="folder holding the repo clones (default: detected)")
    ap.add_argument("--tools-root", help="the claude-tools clone (default: <repos-root>/claude-tools if present)")
    ap.add_argument("--collab-mailbox", help="this machine's Claude Collab mailbox folder ('' = not used)")
    ap.add_argument("--no-collab-mailbox", action="store_true", help="clear the Collab mailbox setting")
    ap.add_argument("--no-git-hooks", action="store_true", help="leave core.hooksPath unchanged in every clone")
    ap.add_argument("--yes", action="store_true", help="never prompt")
    opts = ap.parse_args(argv)

    # The CLAUDE.md stub is an @import, which fails SILENTLY on a path containing a
    # space (anthropics/claude-code#56927; no quoting exists): the machine would look
    # configured while no instruction loads. Refuse before writing anything.
    if " " in str(REPO_ROOT):
        raise Refuse(f"Repo path contains a space:\n  {REPO_ROOT}\n\nClaude Code's @import silently fails on "
                     "paths containing spaces, so ~/.claude/CLAUDE.md would never load. Move or re-clone this "
                     "repo to a folder without spaces, then run bootstrap again.")

    claude_dir = machine_config.claude_dir()
    config_path = machine_config.config_path()
    say(f"Repo root: {REPO_ROOT}")
    say(f"Claude dir: {claude_dir}")

    # ---- Reads and checks; nothing is written until every check passes. ----
    try:
        stored = machine_config.read_config(config_path)
    except machine_config.ConfigError as exc:
        raise Refuse(f"{exc}; refusing to replace it. Fix or move it, then run bootstrap again.") from exc
    cfg = build_config(opts, stored)
    cfg_text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    for key in ("repos_root", "tools_root", "reports_dir", "evidence_archive", "collab_mailbox"):
        say(f"  {key}: {cfg[key] or '(not set)'}")
    say(f"  additional_directories: {', '.join(cfg['additional_directories']) or '(none)'}")
    if not cfg["tools_root"]:
        say("  note: no claude-tools clone found; pass --repos-root or --tools-root if it lives elsewhere")

    settings_plan = ps.plan_settings(cfg, claude_dir / "settings.json", REPO_ROOT)
    appdata = os.environ.get("APPDATA")
    desktop_plan = ps.plan_desktop_config(Path(appdata), REPO_ROOT) if os.name == "nt" and appdata else None

    claude_md = claude_dir / "CLAUDE.md"
    stub = f"@{REPO_ROOT.as_posix()}/CLAUDE.md"
    skills_path = claude_dir / "skills"
    skills_target = REPO_ROOT / "skills"

    hook_plans: list[HooksPlan] = []
    if not opts.no_git_hooks:
        clones = [REPO_ROOT] + ([Path(cfg["tools_root"])] if cfg["tools_root"] else [])
        hook_plans = [plan for plan in map(plan_git_hooks, clones) if plan is not None]
    gitleaks_missing = not (os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks"))

    if opts.dry_run:
        say("")
        say("DRY RUN - nothing written. Would write:")
        say(f"  {config_path}{' (unchanged)' if stored and json.dumps(stored, sort_keys=True) == json.dumps(cfg, sort_keys=True) else ''}")
        say(f"  {claude_md}: {stub}")
        say(f"  {skills_path} -> {skills_target}")
        if settings_plan.needed:
            for line in ps.plan_lines(settings_plan.changes):
                say(line)
        else:
            say("  settings.json already correct")
        if desktop_plan is not None:
            say(f"  {desktop_plan.path}: {'update mcpServers' if desktop_plan.changed else 'already correct'}")
        for plan in hook_plans:
            say(f"  {plan.clone}: core.hooksPath " + {"set": f"-> {HOOKS_DIR}", "already": "already correct",
                                                       "foreign": f"= {plan.current} (left alone)"}[plan.action])
        return 0

    # ---- Writes. Every check has passed. ----
    claude_dir.mkdir(parents=True, exist_ok=True)

    # 1. Machine config
    if config_path.exists() and ps.read_text(config_path).strip() == cfg_text.strip():
        say(f"Machine config already correct: {config_path}")
    else:
        backup = ps.backup_file(config_path, cfg_text)
        if backup:
            say(f"Backed up existing file to {backup}")
        ps.atomic_write(config_path, cfg_text)
        say(f"Wrote machine config: {config_path}")

    # 2. CLAUDE.md stub
    backup = ps.backup_file(claude_md, stub)
    if backup:
        say(f"Backed up existing file to {backup}")
    ps.atomic_write(claude_md, stub)
    say(f"Wrote CLAUDE.md stub: {stub}")

    # 3. skills link
    if is_link(skills_path):
        current = link_target(skills_path)
        if not same_path(current, skills_target):
            # Remove only the link itself, never its target's contents.
            if os.name == "nt":
                os.rmdir(skills_path)
            else:
                skills_path.unlink()
            say(f"Removed stale skills link (was pointing at {current}) - its old target is untouched")
    elif skills_path.exists():
        dest = skills_path.with_name(f"skills.pre-profile-backup-{ps.stamp()}")
        skills_path.rename(dest)
        say(f"Backed up existing directory to {dest}")
        synced = dest / "synced"
        if synced.is_dir() and not (skills_target / "synced").exists():
            # Desktop-synced skills are runtime-managed and git-ignored; keep them live.
            shutil.copytree(synced, skills_target / "synced")
            say("Copied runtime-managed skills/synced back into the repo skills dir (git-ignored)")
    if not is_link(skills_path) and not skills_path.exists():
        kind = make_dir_link(skills_path, skills_target)
        say(f"Created skills {kind} -> {skills_target}")
    else:
        say("skills link already present")

    # 4. settings.json
    for event, group in settings_plan.preserved:
        say(f"Kept {len(group['hooks'])} {event} hook(s) not owned by the profile")
    if not settings_plan.needed:
        say("settings.json already correct - left untouched")
    else:
        for line in ps.plan_lines(settings_plan.changes):
            say(line)
        backup = ps.write_settings(settings_plan)
        if backup:
            say(f"Backed up existing file to {backup}")
        say("Wrote settings.json")
    legacy = claude_dir / "hooks"
    if legacy.is_dir() and any(p.suffix in (".py", ".ps1") for p in legacy.iterdir()):
        say(f"Note: hooks now run from {REPO_ROOT.as_posix()}/hooks; the copies earlier bootstraps "
            f"pinned under {legacy} are no longer registered (left in place, safe to delete).")

    # 5. Claude Desktop config (Windows)
    if desktop_plan is None:
        say("claude_desktop_config.json: skipped (Claude Desktop config is written on Windows only)")
    elif not desktop_plan.changed:
        say("claude_desktop_config.json already correct - left untouched")
    else:
        backup = ps.backup_file(desktop_plan.path, desktop_plan.text) if desktop_plan.existed else None
        if backup:
            say(f"Backed up existing file to {backup}")
        ps.atomic_write(desktop_plan.path, desktop_plan.text)
        say("Updated mcpServers in claude_desktop_config.json" + (
            f" (preserved {desktop_plan.kept_keys} top-level keys)" if desktop_plan.existed else " (new file)"))

    # 6. Versioned git hooks (core.hooksPath)
    if opts.no_git_hooks:
        say("git hooks: skipped (--no-git-hooks)")
    for plan in hook_plans:
        say(apply_git_hooks(plan))
    if gitleaks_missing and any(plan.action != "foreign" for plan in hook_plans):
        say(f"Note: gitleaks is not on PATH (or GITLEAKS_BIN). The {HOOKS_DIR}/pre-commit secret scan needs it; "
            "this profile's pre-commit blocks every commit until it is installed.")

    say("")
    say("Bootstrap complete.")
    say("Restart Claude Code; verify with /context (CLAUDE.md and skills load) and /plugin list.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refuse as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        sys.exit(1)
