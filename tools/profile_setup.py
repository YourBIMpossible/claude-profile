"""Render and merge this profile's Claude Code settings; shared by bootstrap.py and
the session-start hook, so installing and keeping current are one code path.

settings.template.json is machine-agnostic. Rendering fills three tokens:

  __PROFILE_ROOT__              this clone (hooks run from it: hooks/, vendor/)
  __PYTHON__                    the interpreter name from the machine config
  "__ADDITIONAL_DIRECTORIES__"  the machine config's additional_directories

A command handler may carry "platforms": ["win32", ...] (sys.platform prefixes); it
is dropped on other platforms and the key itself is never written.

Merging into ~/.claude/settings.json updates only the profile-owned parts:
  hooks                 template hooks replace the ones the profile owns; every
                        other registration (another tool's hook) is kept
  permissions           additionalDirectories is the union (case/slash-insensitive);
                        other template permission keys only fill gaps
  enabledPlugins, extraKnownMarketplaces, env
                        gain the template's missing entries; existing ones win
  anything else         the template value is a default, used only when absent
An existing settings.json that is empty, not JSON, not an object, repeats a key or
spells a profile-owned key in the wrong case is refused, never replaced.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import machine_config  # noqa: E402

PROFILE_ROOT = machine_config.PROFILE_ROOT
PROFILE_OWNED_KEYS = ("hooks", "permissions", "enabledPlugins", "extraKnownMarketplaces", "env")
MAP_KEYS = ("enabledPlugins", "extraKnownMarketplaces", "env")
DIRS_TOKEN = '"__ADDITIONAL_DIRECTORIES__"'
# Hook files this profile used to register that no longer ship. An existing
# registration of one is the profile's (dropped), never someone else's hook.
RETIRED_HOOK_NAMES = ("session-start-pull.ps1", "session-start-pull.sh")
# Registrations that ran an upstream-owned script from its own checkout before the
# profile took it over; matched by path suffix, so no machine path is named.
SUPERSEDED_SUFFIXES = (
    "/audit-watchdog/show-auditalerts.ps1",
    "/evidence-capture/capture_start.py",
    "/evidence-capture/capture_end.py",
)
VENDOR_UNIT_DIRS = ("evidence-capture", "evidence-footprints")
REVIT_MCP_EXE = Path("Autodesk") / "Revit 2027 MCP Server Read-Tools Technical Preview" / "Autodesk.RevitMcpServer.Stdio.exe"


class Refuse(Exception):
    """A condition under which nothing may be written."""


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ---- reading ------------------------------------------------------------------------

def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"key '{k}' appears twice")
        out[k] = v
    return out


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def read_settings(path: Path) -> dict | None:
    """The existing settings object, None when the file does not exist; Refuse when
    it exists but cannot be merged safely."""
    if not path.exists():
        return None
    text = read_text(path)
    if not text.strip():
        raise Refuse("Existing settings.json is empty; refusing to replace it. Restore it from a backup "
                     "or delete it, then run bootstrap again.")
    try:
        existing = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ValueError as exc:
        raise Refuse(f"Existing settings.json is not valid JSON (or repeats a key); refusing to replace it. "
                     f"Fix or move it, then run bootstrap again: {exc}") from exc
    if existing is None:
        raise Refuse("Existing settings.json is JSON null, not a settings object; refusing to replace it.")
    if not isinstance(existing, dict):
        raise Refuse("Existing settings.json is not a JSON object; refusing to replace it.")
    # Claude Code reads these keys case-sensitively: a 'Hooks' key would be merged
    # into and written back as 'Hooks', which Claude Code ignores.
    for name in existing:
        expected = [k for k in PROFILE_OWNED_KEYS if k.lower() == name.lower()]
        if expected and name != expected[0]:
            raise Refuse(f"Existing settings.json has a '{name}' key; Claude Code only reads '{expected[0]}' "
                         f"(case-sensitive); refusing to replace it. Rename the key, then run bootstrap again.")
    for key in PROFILE_OWNED_KEYS:
        if existing.get(key) is not None and not isinstance(existing[key], dict):
            raise Refuse(f"Existing settings.json has a '{key}' value that is not a JSON object; refusing to replace it.")
    return existing


# ---- rendering ----------------------------------------------------------------------

def _platform_ok(handler: dict) -> bool:
    plats = handler.get("platforms")
    return not plats or any(sys.platform.startswith(p) for p in plats)


def render_settings(template_raw: str, cfg: dict, profile_root: Path = PROFILE_ROOT) -> dict:
    root = profile_root.as_posix()
    if '"' in root or "\\" in root:
        raise Refuse(f"Profile path cannot be written into settings.json: {profile_root}")
    dirs = cfg.get("additional_directories") or []
    raw = template_raw.replace(DIRS_TOKEN, ", ".join(json.dumps(d) for d in dirs))
    raw = raw.replace("__PROFILE_ROOT__", root).replace("__PYTHON__", cfg.get("python") or "python")
    leftover = sorted(set(re.findall(r"__[A-Z_]+__", raw)))
    if leftover:
        raise Refuse(f"Generated settings.json still contains unresolved tokens: {', '.join(leftover)}")
    try:
        generated = json.loads(raw)
    except ValueError as exc:
        raise Refuse(f"Generated settings.json is not valid JSON: {exc}") from exc
    hooks: dict = {}
    for event, groups in (generated.get("hooks") or {}).items():
        kept_groups = []
        for group in groups:
            handlers = [{k: v for k, v in h.items() if k != "platforms"}
                        for h in group.get("hooks", []) if _platform_ok(h)]
            if handlers:
                kept_groups.append({**group, "hooks": handlers})
        if kept_groups:
            hooks[event] = kept_groups
    generated["hooks"] = hooks
    return generated


def handler_paths(hooks: dict, profile_root: Path = PROFILE_ROOT) -> list[str]:
    """Repo-relative paths of every file a rendered command handler runs from this clone."""
    prefix = profile_root.as_posix().rstrip("/") + "/"
    found = set()
    for groups in hooks.values():
        for g in groups:
            for h in g.get("hooks", []):
                for a in [h.get("command", "")] + list(h.get("args") or []):
                    if isinstance(a, str) and a.startswith(prefix):
                        found.add(a[len(prefix):])
    return sorted(found)


def owned_hook_names(profile_root: Path = PROFILE_ROOT) -> list[str]:
    names = [p.name for p in (profile_root / "hooks").iterdir()
             if p.is_file() and p.suffix in (".py", ".ps1", ".sh")]
    return sorted(set(names) | set(RETIRED_HOOK_NAMES))


# ---- merging ------------------------------------------------------------------------

def _norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def is_owned_handler(handler: dict, template_prompts: set, profile_root: Path, owned_names: list[str]) -> bool:
    if handler.get("type") == "prompt":
        return handler.get("prompt") in template_prompts
    text = _norm(" ".join([str(handler.get("command", ""))] + [str(a) for a in (handler.get("args") or [])]))
    root = _norm(profile_root.as_posix()).rstrip("/") + "/"
    if root + "hooks/" in text or root + "vendor/" in text:
        return True
    # A copy pinned by an earlier bootstrap under ~/.claude/hooks, or a registration
    # from another clone of this profile (the clone moved): the template's now.
    for name in owned_names:
        if re.search(r"(^|[/ ])" + re.escape("hooks/" + name.lower()) + r"($|[ \"])", text):
            return True
    for unit in VENDOR_UNIT_DIRS:
        if f"/.claude/hooks/{unit}/" in text or f"/vendor/{unit}/" in text:
            return True
    return any(text.endswith(sfx) or (sfx + " ") in text for sfx in SUPERSEDED_SUFFIXES)


def unmanaged_hook_groups(existing_hooks: dict, generated_hooks: dict,
                          profile_root: Path = PROFILE_ROOT) -> list[tuple[str, dict]]:
    """(event, group) for each existing group that still holds handlers the profile does not own."""
    template_prompts = {h.get("prompt") for groups in generated_hooks.values() for g in groups
                        for h in g.get("hooks", []) if h.get("type") == "prompt"}
    names = owned_hook_names(profile_root)
    result = []
    for event, groups in existing_hooks.items():
        for group in groups or []:
            if not isinstance(group, dict):
                continue
            kept = [h for h in group.get("hooks") or []
                    if isinstance(h, dict) and not is_owned_handler(h, template_prompts, profile_root, names)]
            if kept:
                copy = {"matcher": group["matcher"]} if "matcher" in group else {}
                copy["hooks"] = kept
                result.append((event, copy))
    return result


def merge_profile_settings(existing: dict | None, generated: dict) -> dict:
    out: dict = dict(existing or {})
    for name, value in generated.items():
        current = out.get(name)
        if name == "hooks":
            out[name] = value
        elif name == "permissions":
            perm = dict(current) if isinstance(current, dict) else {}
            for k, v in value.items():
                if k != "additionalDirectories" and k not in perm:
                    perm[k] = v
            candidates = list(perm.get("additionalDirectories") or []) + list(value.get("additionalDirectories") or [])
            seen: set[str] = set()
            dirs: list = []
            for d in candidates:
                if not isinstance(d, str) or not d.strip():
                    continue
                key = d.replace("\\", "/").rstrip("/").lower()
                if key not in seen:
                    seen.add(key)
                    dirs.append(d)
            perm["additionalDirectories"] = dirs
            out[name] = perm
        elif name in MAP_KEYS:
            m = dict(current) if isinstance(current, dict) else {}
            for k, v in value.items():
                m.setdefault(k, v)
            out[name] = m
        elif name not in out:
            out[name] = value
    return out


def _leaves(node: object, path: str, into: dict[str, str]) -> None:
    if isinstance(node, dict):
        if not node:
            into[path] = "{}"
        for k, v in node.items():
            _leaves(v, f"{path}/{k}", into)
    else:
        into[path] = json.dumps(node, ensure_ascii=False, separators=(",", ":"))


def settings_changes(before: dict | None, after: dict) -> list[tuple[str, str]]:
    """(kind, path) per JSON leaf path whose value differs; formatting and key order do not count."""
    b: dict[str, str] = {}
    a: dict[str, str] = {}
    if before is not None:
        _leaves(before, "", b)
    _leaves(after, "", a)
    changes = [("removed", k) for k in b if k not in a]
    changes += [("changed", k) for k in b if k in a and a[k] != b[k]]
    changes += [("added", k) for k in a if k not in b]
    return changes


def plan_lines(changes: list[tuple[str, str]]) -> list[str]:
    """Change counts and changed paths, never values; names under /env are masked."""
    counts = {"added": 0, "removed": 0, "changed": 0}
    lines = set()
    for kind, path in changes:
        counts[kind] += 1
        lines.add(f"  {kind} {re.sub(r'^/env/.*', '/env/*', path)}")
    return [f"settings.json plan: {counts['added']} added, {counts['removed']} removed, {counts['changed']} changed",
            *sorted(lines)]


@dataclass
class SettingsPlan:
    path: Path
    existing: dict | None
    merged: dict
    changes: list[tuple[str, str]]
    preserved: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return json.dumps(self.merged, indent=2, ensure_ascii=False) + "\n"

    @property
    def needed(self) -> bool:
        return self.existing is None or bool(self.changes)


def plan_settings(cfg: dict, settings_path: Path, profile_root: Path = PROFILE_ROOT) -> SettingsPlan:
    """Read, render and merge; writes nothing. Refuse on anything unsafe."""
    existing = read_settings(settings_path)
    generated = render_settings(read_text(profile_root / "settings.template.json"), cfg, profile_root)
    missing = [p for p in handler_paths(generated["hooks"], profile_root) if not (profile_root / p).is_file()]
    if missing:
        raise Refuse(f"settings.template.json registers hooks missing from this clone: {', '.join(missing)}")
    preserved: list[tuple[str, dict]] = []
    if existing and isinstance(existing.get("hooks"), dict):
        preserved = unmanaged_hook_groups(existing["hooks"], generated["hooks"], profile_root)
    for event, group in preserved:
        generated["hooks"].setdefault(event, []).append(group)
    merged = merge_profile_settings(existing, generated)
    if existing is not None:
        lost = [k for k in existing if k not in merged]
        if lost:
            raise Refuse(f"Settings merge would drop top-level keys: {', '.join(lost)} - refusing to write.")
    return SettingsPlan(settings_path, existing, merged, settings_changes(existing, merged), preserved)


# ---- writing ------------------------------------------------------------------------

def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same folder, then replace. Falls back to a plain
    write when the replace is refused (Windows: another process holds the file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.replace(tmp, path)
        except PermissionError:
            path.write_bytes(data)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def backup_file(path: Path, expected_text: str, rolling: bool = False) -> Path | None:
    """Copy `path` aside unless it already holds `expected_text`. rolling=True keeps
    one `<name>.pre-profile-sync` (session-start updates); otherwise a timestamped
    `<name>.pre-profile-backup-<stamp>` that is never touched again."""
    if not path.exists():
        return None
    current = path.read_bytes()
    if current.decode("utf-8-sig", errors="replace").strip() == expected_text.strip():
        return None
    dest = path.with_name(path.name + (".pre-profile-sync" if rolling else f".pre-profile-backup-{stamp()}"))
    dest.write_bytes(current)
    return dest


def write_settings(plan: SettingsPlan, rolling_backup: bool = False) -> Path | None:
    backup = backup_file(plan.path, plan.text, rolling=rolling_backup)
    atomic_write(plan.path, plan.text)
    return backup


# ---- Claude Desktop config (Windows) -------------------------------------------------

def find_object_span(text: str, key: str) -> tuple[int, int] | None:
    """(index of '{', index of its matching '}') for a top-level key's object value."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*\{', text)
    if not m:
        return None
    start = m.end() - 1
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, i
    return None


@dataclass
class DesktopPlan:
    path: Path
    text: str
    existed: bool
    changed: bool
    kept_keys: int


def revit_mcp_exe() -> Path | None:
    base = os.environ.get("ProgramFiles")
    if not base:
        return None
    exe = Path(base) / REVIT_MCP_EXE
    return exe if exe.is_file() else None


def plan_desktop_config(appdata: Path, profile_root: Path = PROFILE_ROOT) -> DesktopPlan:
    """Replace only mcpServers in claude_desktop_config.json; every other byte stays.
    The file also holds the Desktop app's own preferences (pairing, grants, flags)."""
    raw = read_text(profile_root / "mcp" / "claude_desktop_config.template.json")
    exe = revit_mcp_exe()
    entry = ""
    if exe is not None:
        entry = (',\n    "autodesk-revit-2027": {\n      "command": ' + json.dumps(str(exe)) + ',\n      "args": []\n    }')
    raw = raw.replace("__REVIT_MCP_ENTRY__", entry)
    try:
        json.loads(raw)
    except ValueError as exc:
        raise Refuse(f"Generated claude_desktop_config.json is not valid JSON: {exc}") from exc
    path = appdata / "Claude" / "claude_desktop_config.json"
    if not path.exists():
        return DesktopPlan(path, raw, False, True, 0)
    existing_raw = read_text(path)
    try:
        existing = json.loads(existing_raw)
    except ValueError as exc:
        raise Refuse(f"Existing claude_desktop_config.json is not valid JSON; refusing to edit it: {exc}") from exc
    if not isinstance(existing, dict):
        raise Refuse("Existing claude_desktop_config.json is not a JSON object; refusing to edit it.")
    src = find_object_span(raw, "mcpServers")
    if not src:
        raise Refuse("Template is missing an mcpServers object")
    block = raw[src[0]:src[1] + 1]
    dst = find_object_span(existing_raw, "mcpServers")
    if dst:
        merged = existing_raw[:dst[0]] + block + existing_raw[dst[1] + 1:]
    else:
        brace = existing_raw.find("{")
        merged = existing_raw[:brace + 1] + '\n  "mcpServers": ' + block + "," + existing_raw[brace + 1:]
    try:
        merged_obj = json.loads(merged)
    except ValueError as exc:
        raise Refuse(f"Merged claude_desktop_config.json is not valid JSON - refusing to write: {exc}") from exc
    lost = [k for k in existing if k not in merged_obj]
    if lost:
        raise Refuse(f"Merge would drop top-level keys: {', '.join(lost)} - refusing to write.")
    if isinstance(existing.get("preferences"), dict):
        if len(merged_obj.get("preferences") or {}) != len(existing["preferences"]):
            raise Refuse("Merge would change preferences sub-key count - refusing to write.")
    return DesktopPlan(path, merged, True, merged != existing_raw, len(existing))
