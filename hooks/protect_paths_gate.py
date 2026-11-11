"""PreToolUse fail-closed protected-path gate for Write|Edit|MultiEdit|NotebookEdit (C3, 2026-08-08).

Converts the protected-file rule from instruction to control. Reads the hook
payload as JSON on STDIN (never an env var - the fail-open lesson from the
operating-canon review: an absent env var silently allowed everything).

FAIL-CLOSED CONTRACT: any condition this script cannot positively evaluate -
malformed JSON, missing tool_input, missing/empty file_path, any unexpected
exception - exits 2 (block). A gate that can silently no-op is worse than no
gate. Exit 0 = allow, exit 2 = block (stderr is shown to Claude). The one
exception to "exit 0 = plain allow" is the approval class below: exit 0 with a
permissionDecision "ask" JSON on stdout, which makes the host stop and prompt
the owner. Only a positively matched north-star path ever takes that route.

SCOPE HONESTY: this covers only the file-writing tools Write, Edit, MultiEdit and
NotebookEdit (MultiEdit sends file_path, NotebookEdit sends notebook_path; matcher
added for the last two by CP-10, 2026-10-29). The Bash channel
(sed -i, redirection, tee, Set-Content, python -c ...) is NOT covered and is
documented as advisory-only in hooks/README.md. Do not extend claims beyond
that.

PATH SPELLINGS: the rules read the last path component. NTFS stream suffixes
(`name::$DATA`, `name:s`), drive-relative paths (`C:name`), trailing dots and
spaces and, for a file that exists, 8.3 short names and links are reduced to
the real name first (CP-10 of the 2026-10-26 slop audit). The name as written
is tested as well, so a reduction never removes a block. A path on another
machine (`//host/share/...`) is never resolved, because that would contact the
host before any permission decision; a `~N` short-name-like component on one is
blocked. A device path that names neither a drive nor a host is blocked, and so
is a local path that cannot be resolved. Not covered: whatever the
host does to file_path before or after this hook sees it, hard links, and
links on another machine.

Protected classes (mirrors the standing rules in CLAUDE.md / memory):
  - .env and every .env.* variant EXCEPT .env.example / .env.template /
    .env.sample (documented placeholders, routinely edited)
  - north-star mission files: NORTHSTAR.md and NORTHSTAR.<slug>.md are NOT
    blocked since 2026-10-10 - they are OWNER-APPROVED. Claude may draft and
    edit freely in NORTHSTAR.draft.md / NORTHSTAR.<slug>.draft.md (and
    *.template.md, which is documentation scaffolding), but any Write/Edit to a
    live NORTHSTAR*.md - promoting a draft, changing the mission text, or
    editing its status: line (pause/close/reopen) - returns an "ask": the owner
    sees one approval prompt and clicks yes or no (only in permission modes where
    a prompt is reliable - ASK_MODES; elsewhere the edit is blocked). The scope-governor still
    depends on the model being unable to rewrite its own orders SILENTLY; the
    approval prompt, not a human doing the edit by hand, is that control.
  - any path ending backend/guard.py (BIMpossible human-only file)
  - key material: *.pem, *.key, *.pfx, *.p12, id_rsa*, id_ed25519*, id_ecdsa*
  - credential stores: credentials.json, .netrc, _netrc, .pgpass, .npmrc,
    .git-credentials, and any filename containing "credential"
"""
import json
import os
import re
import sys

BLOCK_EXIT = 2
# Permission modes in which a hook "ask" is known to stop and prompt the owner.
# Anything else (bypassPermissions, dontAsk, a missing or unrecognised value) could
# turn the ask into a silent allow, so a live north-star edit is hard-blocked there
# instead. acceptEdits is listed on the documented ask-always precedence but has
# not been live-tested; pull it from this set if it ever auto-accepts.
ASK_MODES = frozenset({"default", "acceptEdits", "plan", "auto"})
ALLOWED_ENV_SUFFIXES = (".example", ".template", ".sample")
KEY_SUFFIXES = (".pem", ".key", ".pfx", ".p12")
KEYFILE_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa")
CRED_NAMES = {"credentials.json", ".netrc", "_netrc", ".pgpass", ".npmrc", ".git-credentials"}


def block(msg: str) -> None:
    sys.stderr.write(f"protect_paths_gate: BLOCKED - {msg}\n")
    sys.exit(BLOCK_EXIT)


class CannotCheck(Exception):
    """The path could not be compared with the protected names; the caller blocks."""


def as_written(raw_path: str) -> str:
    """The path with forward slashes and nothing else changed in its last component."""
    return raw_path.replace("\\", "/").strip().rstrip("/")


def plain_spelling(raw_path: str) -> str:
    """The path with forward slashes and no Windows decoration on its last component.

    `name::$DATA` and `name:stream` write to the file `name`, and a drive-relative `C:name`
    is `name` in that drive's current folder (slop audit 2026-10-26, CP-10). Both are cut back
    to `name`. Win32 also drops trailing dots and spaces, so `x.pem.` creates `x.pem`; they are
    stripped. A rule that reads the last component then sees the file Windows would write.

    Off NTFS a colon, a trailing dot and a trailing space are ordinary filename characters, and
    there `old:credentials.json` is a file of that name. This reading alone would lose it, so
    `spellings` always tests the path as written (`as_written`) beside this one.
    """
    norm = as_written(raw_path)
    head, sep, name = norm.rpartition("/")
    if not sep and len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        name = name[2:]                       # drive-relative: "C:name"
    name = name.split(":", 1)[0].rstrip(". ")
    return f"{head}{sep}{name}"


LOCAL = "local"      # this machine: safe to hand to the filesystem
REMOTE = "remote"    # another machine: tested as written, never resolved
DEVICE = "device"    # a device path with no drive or host in it: cannot be checked


def locate(plain: str) -> "tuple[str, str]":
    """Where a path points, and the spelling to hand to the filesystem when that is safe.

    Resolving a path on another machine (`//host/share/...`, also `//?/UNC/host/...`) makes
    this machine contact `host` and offer it the user's sign-in. This hook runs before any
    permission decision, so such a path is never resolved. The device forms of a drive path
    (`//?/`, `//./` or the NT-namespace `/??/` followed by a drive letter) and a share on this
    machine itself (`localhost`, `127.0.0.1`, this computer's name) are local and resolved
    like any other path. Every other device path (a volume id, GLOBALROOT, a pipe) is
    reported as DEVICE.
    """
    if plain.startswith("/??/"):
        plain = "/" + plain               # the NT-namespace prefix: same meaning as `//?/`
    if not plain.startswith("//"):
        return LOCAL, plain
    rest = plain[2:]
    if rest[:2] in ("?/", "./") or rest[:3] == "??/":
        rest = rest[3:] if rest[:3] == "??/" else rest[2:]
        if len(rest) >= 2 and rest[0].isalpha() and rest[1] == ":":
            return LOCAL, rest
        if rest[:4].lower() != "unc/":
            return DEVICE, plain
        rest = rest[4:]
    host = rest.split("/", 1)[0].lower()
    own_name = os.environ.get("COMPUTERNAME", "").lower()
    if host and host in ("localhost", "127.0.0.1", own_name):
        return LOCAL, f"//{rest}"
    return REMOTE, plain


SHORT_NAME_RE = re.compile(r"~\d")


def spellings(raw_path: str) -> "list[str]":
    """Every spelling of the path to test: reduced, as written, and as the filesystem resolves it.

    The path as written is always tested too, so the reductions in `plain_spelling` can add a
    block or an ask but never remove one. realpath turns an 8.3 short name (`NORTHS~1.MD`) or
    a link into the real long path when the file exists; a short name that names no existing
    file is just a new file with that name. A local path realpath cannot resolve raises
    CannotCheck: its short names and links are unknown, so the caller blocks.
    """
    plain = plain_spelling(raw_path)
    out = [plain]
    written = as_written(raw_path)
    if written != plain:
        out.append(written)
    where, target = locate(plain)
    if where != LOCAL:
        return out
    try:
        real = os.path.realpath(target).replace("\\", "/")
    except (OSError, ValueError) as exc:
        raise CannotCheck(f"the path could not be resolved ({type(exc).__name__}), so it cannot "
                          "be checked against the protected names") from exc
    if real.lower() != plain.lower():
        out.append(real)
    return out


def is_protected(raw_path: str) -> "str | None":
    """Return the reason a path is protected, or None if allowed."""
    plain = plain_spelling(raw_path)
    where, _ = locate(plain)
    if where == DEVICE:
        return "a device path with no drive or host in it cannot be checked against the protected names"
    if where == REMOTE and SHORT_NAME_RE.search(plain):
        return ("a short-name-like component (~N) on a network path cannot be checked "
                "against the protected names")
    try:
        candidates = spellings(raw_path)
    except CannotCheck as exc:
        return str(exc)
    for norm in candidates:
        reason = _protected_reason(norm)
        if reason:
            return reason
    return None


def _protected_reason(norm: str) -> "str | None":
    name = norm.rsplit("/", 1)[-1].lower()
    if not name:
        return "empty filename"
    if name == ".env" or name.startswith(".env."):
        if not name.endswith(ALLOWED_ENV_SUFFIXES):
            return f"{name} is a protected env file (human-only)"
    if norm.lower().endswith("backend/guard.py"):
        return "backend/guard.py is human-only"
    if name.endswith(KEY_SUFFIXES):
        return f"{name} is key material"
    if name.startswith(KEYFILE_PREFIXES):
        return f"{name} is an SSH key file"
    if name in CRED_NAMES or "credential" in name:
        return f"{name} is a credential store"
    return None


def needs_approval(raw_path: str) -> "str | None":
    """Return the owner-facing prompt text if the path is a live north-star file."""
    for norm in spellings(raw_path):
        name = norm.rsplit("/", 1)[-1].lower()
        if name == "northstar.md" or (name.startswith("northstar.") and name.endswith(".md")):
            if not name.endswith((".draft.md", ".template.md")):
                return (f"{name} is a live north-star mission file. Approve this change only if "
                        "the new text and the reason Claude gave match what you want; "
                        "deny to keep the mission as it is.")
    return None


def ask(msg: str) -> None:
    """Exit 0 with a permissionDecision "ask": the host prompts the owner."""
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": msg,
    }}))
    sys.exit(0)


def main() -> None:
    try:
        # utf-8-sig: tolerate a BOM (PowerShell pipes prepend one); harness sends plain UTF-8
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception as exc:  # fail-closed: unreadable payload blocks
        block(f"could not parse hook payload from stdin ({exc}); failing closed")
    if not isinstance(payload, dict):
        block("hook payload is not a JSON object; failing closed")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        block("payload has no tool_input object; failing closed")
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(file_path, str) or not file_path.strip():
        block("no file_path in tool_input; failing closed")
    reason = is_protected(file_path)
    if reason:
        block(f"{file_path}: {reason}. This path is protected by standing rule; "
              "if the human wants it changed, they do it themselves.")
    approval = needs_approval(file_path)
    if approval:
        mode = payload.get("permission_mode")
        if mode in ASK_MODES:
            ask(approval)
        block(f"{file_path}: live north-star edits need an owner approval prompt, which "
              f"cannot be guaranteed in permission mode {mode!r}. Switch the session to a "
              "prompting mode, or have the owner make the edit.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # absolute backstop: never fail open
        block(f"unexpected error ({exc}); failing closed")
