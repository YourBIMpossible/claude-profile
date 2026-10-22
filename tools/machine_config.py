"""Per-machine roots for this profile: one local file, never committed.

Nothing in this repository names a machine path. Every path is derived at runtime
from one of three sources:

  1. this clone's own location        -> {profile}  (PROFILE_ROOT below)
  2. the home folder                   -> ~ and ~/.claude
  3. the machine config, written by bootstrap with auto-detected defaults:
       ~/.claude/profile-machine.json   (CLAUDE_PROFILE_MACHINE_CONFIG overrides the path)

Keys of the machine config (all optional; a missing key falls back to detection):

  repos_root              {repos}    folder holding the repo clones
  tools_root              {tools}    the claude-tools clone ("" when not cloned)
  reports_dir             {reports}  transient deliverables (default {tools}/reports,
                                     or ~/.claude/reports without a claude-tools clone)
  evidence_archive                   Evidence-Archive root ("" when absent)
  collab_mailbox          {mailbox}  this machine's Claude Collab mailbox; user-defined,
                                     empty means the Collab routing rule does not apply
  python                             interpreter name hooks are registered with
  additional_directories             extra folders granted to every session

CLAUDE.md and skill text refer to the braced names; the session-start hook prints
the resolved values into each session. Run ``python tools/machine_config.py`` to see
them, ``--json`` for machine-readable output.

Standard library only; imported by hooks, so it must stay fast and never raise on a
missing file (a malformed one raises ConfigError, which callers surface).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG_NAME = "profile-machine.json"
CONFIG_ENV = "CLAUDE_PROFILE_MACHINE_CONFIG"
SCHEMA = 1
PROFILE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR_NAME = "claude-tools"
EVIDENCE_DIR_NAME = "Evidence-Archive"
# Project folders granted to every session when found under {repos}.
PROJECT_DIR_NAMES = ("BIMpossible", "BIMpossible-Workspace", "BIMpossible_Workspace")
KEYS = ("repos_root", "tools_root", "reports_dir", "evidence_archive", "collab_mailbox",
        "python", "additional_directories")
# Braced root names used in CLAUDE.md, skills and data files -> config key.
TOKENS = {"{profile}": None, "{repos}": "repos_root", "{tools}": "tools_root",
          "{reports}": "reports_dir", "{mailbox}": "collab_mailbox"}


class ConfigError(Exception):
    """The machine config exists but cannot be used."""


def claude_dir() -> Path:
    return Path.home() / ".claude"


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    return Path(override) if override else claude_dir() / CONFIG_NAME


def read_config(path: Path | None = None) -> dict:
    """The stored config: {} when the file does not exist."""
    path = path or config_path()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"{path}: unreadable ({exc.strerror})") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a JSON object")
    for key in KEYS:
        value = data.get(key)
        if key == "additional_directories":
            if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                raise ConfigError(f"{path}: '{key}' must be a list of strings")
        elif value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}: '{key}' must be a string")
    return data


def _child(parent: Path, name: str) -> Path | None:
    """parent/name as spelled on disk, matching the name case-insensitively (clones
    differ in case: claude-tools, Claude-Tools)."""
    try:
        entries = [e for e in parent.iterdir() if e.name.lower() == name.lower() and e.is_dir()]
    except OSError:
        return None
    entries.sort(key=lambda e: e.name != name)  # an exact spelling wins
    return entries[0] if entries else None


def detect_repos_root(profile_root: Path = PROFILE_ROOT) -> Path:
    """The folder holding the repo clones: the first candidate that holds a
    claude-tools clone, else the folder holding this clone."""
    home = Path.home()
    base = profile_root
    if base.parent.name == "worktrees" and base.parent.parent.name == ".claude":
        base = base.parents[2]  # a worktree lane: the repos sit beside its main checkout
    candidates = [base.parent, home / "src", home / "workspace", home / "repos", home]
    for cand in candidates:
        if _child(cand, TOOLS_DIR_NAME) is not None:
            return cand
    return base.parent


def detect(given: dict | None = None, profile_root: Path = PROFILE_ROOT) -> dict:
    """Every key, from `given` where set, else auto-detected. Never writes."""
    given = dict(given or {})
    out: dict = {}
    repos = Path(given["repos_root"]) if given.get("repos_root") else detect_repos_root(profile_root)
    out["repos_root"] = str(repos)
    if given.get("tools_root") is not None:
        out["tools_root"] = given["tools_root"]
    else:
        tools = _child(repos, TOOLS_DIR_NAME)
        out["tools_root"] = str(tools) if tools else ""
    if given.get("reports_dir"):
        out["reports_dir"] = given["reports_dir"]
    else:
        out["reports_dir"] = str(Path(out["tools_root"]) / "reports") if out["tools_root"] else str(claude_dir() / "reports")
    if given.get("evidence_archive") is not None:
        out["evidence_archive"] = given["evidence_archive"]
    else:
        found = _child(repos, EVIDENCE_DIR_NAME) or _child(Path.home(), EVIDENCE_DIR_NAME)
        out["evidence_archive"] = str(found) if found else ""
    out["collab_mailbox"] = given.get("collab_mailbox") or ""
    out["python"] = given.get("python") or ("python" if os.name == "nt" else "python3")
    if given.get("additional_directories") is not None:
        out["additional_directories"] = list(given["additional_directories"])
    else:
        dirs = []
        for name in PROJECT_DIR_NAMES:
            found = _child(repos, name)
            if found is not None and found.as_posix() not in dirs:
                dirs.append(found.as_posix())
        out["additional_directories"] = dirs
    for key, value in given.items():
        out.setdefault(key, value)  # unknown keys are the user's; keep them
    return out


def load(profile_root: Path = PROFILE_ROOT) -> dict:
    """The effective config: stored values, detection for anything missing."""
    return detect(read_config(), profile_root)


def roots(cfg: dict | None = None, profile_root: Path = PROFILE_ROOT) -> dict[str, str]:
    """Braced token -> resolved value ("" when unset)."""
    cfg = load(profile_root) if cfg is None else cfg
    return {tok: (str(profile_root) if key is None else str(cfg.get(key) or "")) for tok, key in TOKENS.items()}


def expand(text: str, cfg: dict | None = None, profile_root: Path = PROFILE_ROOT) -> str:
    """Replace braced root tokens and a leading ~ in a configured path string."""
    values = roots(cfg, profile_root)
    for tok, value in values.items():
        if tok in text:
            if not value:
                raise ConfigError(f"{tok} is not set in {config_path()}")
            text = text.replace(tok, value.replace("\\", "/"))
    return os.path.expanduser(text)


def describe(cfg: dict | None = None, profile_root: Path = PROFILE_ROOT) -> str:
    values = roots(cfg, profile_root)
    return "; ".join(f"{tok} = {val or '(not set)'}" for tok, val in values.items())


def main(argv: list[str]) -> int:
    try:
        cfg = load()
    except ConfigError as exc:
        print(f"machine config error: {exc}", file=sys.stderr)
        return 1
    if "--json" in argv:
        print(json.dumps({"config_path": str(config_path()), "profile_root": str(PROFILE_ROOT), **cfg}, indent=2))
    else:
        print(f"config: {config_path()}{'' if config_path().exists() else ' (missing - run bootstrap)'}")
        print(describe(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
