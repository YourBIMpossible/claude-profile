"""PreToolUse fail-closed protected-path gate for Write|Edit (C3, 2026-08-08).

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

SCOPE HONESTY: this covers only the Write and Edit tools. The Bash channel
(sed -i, redirection, tee, Set-Content, python -c ...) is NOT covered and is
documented as advisory-only in hooks/README.md. Do not extend claims beyond
that.

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


def is_protected(raw_path: str) -> "str | None":
    """Return the reason a path is protected, or None if allowed."""
    norm = raw_path.replace("\\", "/").strip().rstrip("/")
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
    name = raw_path.replace("\\", "/").strip().rstrip("/").rsplit("/", 1)[-1].lower()
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
