"""PreToolUse fail-closed protected-path gate for Write|Edit (C3, 2026-08-08).

Converts the protected-file rule from instruction to control. Reads the hook
payload as JSON on STDIN (never an env var - the fail-open lesson from the
operating-canon review: an absent env var silently allowed everything).

FAIL-CLOSED CONTRACT: any condition this script cannot positively evaluate -
malformed JSON, missing tool_input, missing/empty file_path, any unexpected
exception - exits 2 (block). A gate that can silently no-op is worse than no
gate. Exit 0 = allow, exit 2 = block (stderr is shown to Claude).

SCOPE HONESTY: this covers only the Write and Edit tools. The Bash channel
(sed -i, redirection, tee, Set-Content, python -c ...) is NOT covered and is
documented as advisory-only in hooks/README.md. Do not extend claims beyond
that.

Protected classes (mirrors the standing rules in CLAUDE.md / memory):
  - .env and every .env.* variant EXCEPT .env.example / .env.template /
    .env.sample (documented placeholders, routinely edited)
  - any path ending backend/guard.py (BIMpossible human-only file)
  - key material: *.pem, *.key, *.pfx, *.p12, id_rsa*, id_ed25519*, id_ecdsa*
  - credential stores: credentials.json, .netrc, _netrc, .pgpass, .npmrc,
    .git-credentials, and any filename containing "credential"
"""
import json
import sys

BLOCK_EXIT = 2
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
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # absolute backstop: never fail open
        block(f"unexpected error ({exc}); failing closed")
