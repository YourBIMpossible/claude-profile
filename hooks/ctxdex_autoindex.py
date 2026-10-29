#!/usr/bin/env python3
"""ctxdex auto-index — PostToolUse hook, Bash only.

Large (>=100 KiB) Bash output is worth keeping searchable instead of letting
it fall out of context forever. This hook indexes it into ctxdex under
source="bash-output" as a pure side effect. Unlike context_budget.py, it
never rewrites the tool result — it only reads the same payload and writes
to ctxdex's own SQLite DB. Query side: ctxdex search --source bash-output
(see skills/ctxdex/SKILL.md in this repo).

Contract (same PostToolUse payload as context_budget.py — see that script's
header for the schema note, verified against the real binary, not just
docs): tool_name, tool_input.command, tool_output / tool_response fallback,
tool_output_is_error, cwd.

This hook must never alter, block, fail, or add noise to the Bash result it
observes: stdout is always empty, exit is always 0.

Opt-out: set CTXDEX_AUTO_INDEX=0 to disable.

NATIVE PERSISTENCE INTERACTION (discovered 2026-08-07, confirmed via a live
payload dump against the real harness - see ctxdex-tool.md memory for the
full trail). Claude Code's own harness truncates tool_response["stdout"] to a
hard 30000-character cap before any hook runs - confirmed identical across
two live payloads regardless of true output size. Critically, the full
content is *not* embedded anywhere in the raw payload as rendered "Output too
large... Full output saved to: <path>" text - that text is a display-layer
artifact of the assistant's own transcript view only and never reaches hooks.
Instead tool_response is a dict carrying the pointer as a plain structured
field: persistedOutputPath (+ persistedOutputSize). _resolve_persisted_output()
reads that field directly when present. Since the path is harness-supplied,
and a command's own stdout could in principle forge similar-looking text via
the secondary string-fallback path, it is only followed when it resolves
under this machine's own ~/.claude/projects/**/tool-results/ tree; otherwise
the already-available text is used as-is (fail open, conservative).

Secret safety: skipped (not indexed) whenever the command looks like a
credential/env inspection, or the output itself matches a private-key,
cloud-key, token, or connection-string pattern. False positives (skipping
something harmless) are the accepted cost — false negatives are not.

Failure policy: fail open, always. Every error is caught and logged to
ctxdex_autoindex.log (rotating); none of them may propagate or print.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")



def _ctxdex_dir() -> tuple[Path, str]:
    """CTXDEX_DIR overrides; else <tools_root>/ctxdex from the machine config
    (tools/machine_config.py). An unset or unreadable config yields a path that
    does not exist, which the preflight below reports once and skips; the second
    value is the config's error text ("" when it loaded) for that report."""
    if os.environ.get("CTXDEX_DIR"):
        return Path(os.environ["CTXDEX_DIR"]), ""
    tools = ""
    error = ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import machine_config
    except Exception as exc:  # fail-open: runs at import, before main's guard
        error = f"cannot import machine_config: {type(exc).__name__}: {exc}"
    else:
        try:
            tools = machine_config.load().get("tools_root") or ""
        except machine_config.ConfigError as exc:
            error = str(exc)
        except Exception as exc:  # fail-open: any other config failure also falls back
            error = f"{type(exc).__name__}: {exc}"
    path = Path(tools) / "ctxdex" if tools else Path.home() / ".claude" / "no-claude-tools-clone" / "ctxdex"
    return path, error


CTXDEX_DIR, CONFIG_ERROR = _ctxdex_dir()
MIN_BYTES = 100 * 1024
# Runtime state stays under ~/.claude/hooks, never in the clone the hook runs from.
LOG_PATH = Path.home() / ".claude" / "hooks" / "ctxdex_autoindex.log"

# --- secret-safety gates ------------------------------------------------------
# Command gate: "env"/"printenv"/"set" only count as a command TOKEN (start of
# string or after a shell separator), not as a substring — otherwise
# `git ... --set-upstream` would false-positive on every such call. The rest
# match anywhere as whole words; a false positive here just means a skipped
# capture, which is the accepted tradeoff per the conservative-skip policy.
_CMD_TOKEN = re.compile(r"(?:^|[;&|\n]|\s)(env|printenv|set)(?:\s|$)", re.I)
_CMD_SUBSTR = re.compile(
    r"Get-ChildItem\s+Env:|\.env\b|\bcredential|\bsecrets?\b|\btokens?\b|\bpasswords?\b|\bapi[_-]?keys?\b",
    re.I,
)

_SELF_INDEX_PY_RE = re.compile(r"\bctxdex\.py\b", re.I)
_SELF_INDEX_EXE_RE = re.compile(
    r"(?:^|[;&|\n])\s*(?:python3?\s+|py\s+)?ctxdex(?:\s|$)", re.I
)

_PERSISTED_OUTPUT_RE = re.compile(
    r"Output too large \([^)]*\)\.\s*Full output saved to:\s*(\S.*?)(?:\r?\n|$)"
)
_TOOL_RESULTS_ROOT = Path.home() / ".claude" / "projects"

# scheme://user:pass@host. The scheme must start where a word starts: without the lookbehind `\w+` was tried from every position of a
# long run of letters or digits and each try scanned to the end of the run, so the scan was quadratic (CT-SLOP1026 / N-2). Any string
# the old pattern matched still matches: the run it matched in starts at a word start, and the rest of the pattern is unchanged.
_CREDENTIAL_URL_RE = re.compile(r"(?<!\w)\w+://[^\s/:@]+:[^\s/@]+@")

_OUTPUT_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    _CREDENTIAL_URL_RE,
]

# ODBC-style connection string: a `server=` followed, later, by `;password=`. Written as two searches because the one-pattern form
# `server=.*?;password=` rescanned the whole tail for every `server=` it found (quadratic, N-2). The earliest `server=` leaves the
# longest tail, so it is enough to look for `;password=` after the first one.
_ODBC_SERVER_RE = re.compile(r"(?i)\bserver\s*=")
_ODBC_PASSWORD_RE = re.compile(r"(?i);\s*password\s*=")


def _logger() -> logging.Logger:
    log = logging.getLogger("ctxdex_autoindex")
    if not log.handlers:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def _passthrough() -> None:
    sys.exit(0)


def _as_text(value) -> str:
    """Tool results arrive as str, or as structured content blocks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _command_looks_sensitive(command: str) -> bool:
    return bool(_CMD_TOKEN.search(command) or _CMD_SUBSTR.search(command))


def _command_invokes_ctxdex(command: str) -> bool:
    """True only for an actual ctxdex invocation - ctxdex.py at any path, or
    a bare ctxdex/python ctxdex/py ctxdex in command position - not any
    command that merely mentions the word (reading this hook's own source,
    grepping for it, a commit message). Keeps the recursive-self-index guard
    without turning ordinary ctxdex-related investigation into a blind spot."""
    return bool(_SELF_INDEX_PY_RE.search(command) or _SELF_INDEX_EXE_RE.search(command))


def _looks_like_odbc_secret(text: str) -> bool:
    server = _ODBC_SERVER_RE.search(text)
    return bool(server and _ODBC_PASSWORD_RE.search(text, server.end()))


def _output_looks_sensitive(text: str) -> bool:
    return any(p.search(text) for p in _OUTPUT_PATTERNS) or _looks_like_odbc_secret(text)


def _read_if_safe(path_str) -> str | None:
    """Read a persisted-output file, but only when it resolves under this
    machine's own tool-results tree. The path traces back to harness data,
    but a command's own stdout could in principle contain a similar-looking
    string, so this containment check is what stops a forged path from
    becoming an arbitrary local file read."""
    try:
        candidate = Path(str(path_str).strip()).resolve()
        safe_prefix = str(_TOOL_RESULTS_ROOT.resolve()).lower() + os.sep
        if not str(candidate).lower().startswith(safe_prefix):
            return None
        if candidate.parent.name != "tool-results" or not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve_persisted_output(output) -> str:
    """Resolve the real Bash output text.

    Primary path: tool_response arrives as a dict, and whenever stdout was
    truncated by the harness, persistedOutputPath names the file holding the
    full content (confirmed live 2026-08-07 - see module docstring). This is
    read directly as a dict field, not parsed out of rendered text.

    Secondary fallback: a plain string containing a rendered "Full output
    saved to: <path>" pointer, kept in case output ever arrives in a shape
    that isn't the dict above - unconfirmed to occur in practice, cheap to
    keep as a safety net.

    Either way, the file is only read when it resolves under this machine's
    own tool-results tree; anything else falls back to whatever text is
    already available (fail open, conservative)."""
    if isinstance(output, dict):
        persisted_path = output.get("persistedOutputPath")
        if persisted_path:
            resolved = _read_if_safe(persisted_path)
            if resolved is not None:
                return resolved
        return _as_text(output)

    text = _as_text(output)
    m = _PERSISTED_OUTPUT_RE.search(text)
    if m:
        resolved = _read_if_safe(m.group(1))
        if resolved is not None:
            return resolved
    return text


def _ctxdex_preflight(log: logging.Logger) -> bool:
    """Verify the external ctxdex dependency before doing any work.

    ctxdex lives OUTSIDE this repo (CTXDEX_DIR, its own git repo — pinned
    commit documented in skills/ctxdex/SKILL.md). Per the fail-open policy
    an absent or moved ctxdex must never disturb the session, but it also
    must not fail silently forever: this writes one clear diagnostic per
    occurrence to the rotating log so the gap is visible on inspection."""
    script = CTXDEX_DIR / "ctxdex.py"
    if not script.is_file():
        log.error(
            "preflight: ctxdex.py not found at %s - auto-capture disabled until "
            "it is restored (see skills/ctxdex/SKILL.md for the pinned version)%s",
            script,
            f" (machine config: {CONFIG_ERROR})" if CONFIG_ERROR else "",
        )
        return False
    return True


def _is_binary_or_empty(text: str) -> bool:
    if not text or not text.strip():
        return True
    return "\x00" in text[:10000]


def main() -> None:
    if os.environ.get("CTXDEX_AUTO_INDEX") == "0":
        _passthrough()

    raw = sys.stdin.read()
    if not raw.strip():
        _passthrough()

    payload = json.loads(raw)

    if (payload.get("tool_name") or "") != "Bash":
        _passthrough()

    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    command = command or ""

    output = payload.get("tool_output")
    if output in (None, ""):
        output = payload.get("tool_response")
    text = _resolve_persisted_output(output)

    size = len(text.encode("utf-8", errors="replace"))
    if size < MIN_BYTES:
        _passthrough()

    log = _logger()

    if _command_invokes_ctxdex(command):
        log.info("skip: self-index guard (command invokes ctxdex) - %r", command[:200])
        _passthrough()

    if _command_looks_sensitive(command):
        log.info("skip: command looks credential-sensitive - %r", command[:200])
        _passthrough()

    if _is_binary_or_empty(text):
        log.info("skip: binary or empty output - %r", command[:200])
        _passthrough()

    if _output_looks_sensitive(text):
        log.info("skip: output looks credential-sensitive - %r", command[:200])
        _passthrough()

    if not _ctxdex_preflight(log):
        _passthrough()

    cwd = payload.get("cwd") or os.getcwd()
    project = Path(cwd).name or "default"
    is_error = bool(payload.get("tool_output_is_error"))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    ms = int((time.time() % 1) * 1000)
    bash_uri = f"bash://{stamp}.{ms:03d}"

    heading = f"# [auto-captured] bash{' (ERROR)' if is_error else ''}: {command.strip()[:150]}\n\n"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8", errors="replace"
        ) as tmp:
            tmp.write(heading)
            tmp.write(text)
            tmp_path = Path(tmp.name)

        if str(CTXDEX_DIR) not in sys.path:
            sys.path.insert(0, str(CTXDEX_DIR))
        import ctxdex  # noqa: E402  (sys.path must be set first)

        if not hasattr(ctxdex, "cmd_index"):
            log.error(
                "preflight: ctxdex at %s has no cmd_index - incompatible version, "
                "auto-capture skipped (see skills/ctxdex/SKILL.md for the pinned version)",
                CTXDEX_DIR,
            )
            return

        ns = argparse.Namespace(
            target=str(tmp_path), source="bash-output", project=project, path=bash_uri
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ctxdex.cmd_index(ns)
        log.info("captured %d bytes -> project=%s %s | %s", size, project, bash_uri, buf.getvalue().strip())
    except Exception as exc:  # fail open, never break the session
        log.error("index failed: %r", exc)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    _passthrough()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail open, never break the session
        try:
            _logger().error("unhandled: %r", exc)
        except Exception:
            pass
        sys.exit(0)
