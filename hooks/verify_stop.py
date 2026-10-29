"""Stop hook: flag a completion claim that no verification backs (CP-VERIFY-STOP-HOOK).

ADVISORY only. It never blocks a stop (no exit 2, no "decision": "block"): blocking
risks stop loops and fights act-then-report. When it fires it prints one
`systemMessage` the owner sees; otherwise it prints nothing.

Fires only when ALL hold for the current turn (entries after the last genuine user
prompt, subagent sidechains excluded):
  1. the turn changed non-doc files: a Write/Edit/MultiEdit/NotebookEdit call, or a
     Bash / PowerShell command that writes (sed -i, a redirect to a file, tee,
     Set-Content, ...) and names a file outside DOC_EXTS (.md, .txt, .log, ...).
     Reports, notes and logs alone need no test run;
  2. no check command ran after the last change (pytest, npm test, dotnet build, tsc,
     ruff, ...: see CHECK_RE);
  3. the final assistant message claims completion (done / fixed / complete / ...);
  4. that message does not already disclose the gap ("not verified", "untested", ...).

Proportional by design (owner: "proportional verification, no receipt system"):
Q&A turns, read-only turns, and honest "not verified" reports stay silent.
Pattern matching is a heuristic; a miss or a false alarm costs one line, never a stop.

Fail-open: any error -> exit 0, no stdout, one `verify_stop:` line on stderr.
Opt-out: VERIFY_STOP_HOOK=0. `stop_hook_active` -> silent (no re-entry).
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Iterator

TAIL_BYTES = 4 * 1024 * 1024  # a turn longer than this is judged on its tail

EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})

CHECK_RE = re.compile(
    r"""(?ix)
    \b(?:
        pytest | unittest | py_compile | compileall | tox | nox
      | (?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|check|lint|build|typecheck)
      | npx\s+(?:jest|vitest|playwright|tsc|eslint)
      | jest | vitest | tsc | eslint | ruff | mypy | pyright | flake8 | pylint
      | dotnet\s+(?:test|build) | msbuild
      | go\s+(?:test|vet|build) | cargo\s+(?:test|check|build|clippy)
      | mvn | gradlew? | ctest | make\s+(?:test|check)
      | Invoke-Pester | pester | bats | shellcheck
      | test_\w+\.(?:py|ps1|sh) | \w+_test\.(?:py|go)
    )\b
    """
)

# `cmd > file` / `cmd >> file`: group 1 is the target. Only the target matters, not
# the command producing the output (running `python x.py > out.log` changes no code).
REDIRECT_RE = re.compile(r"""(?:^|[\s(])>>?(?![>&=])\s*["']?([^\s"';|&)<>]+)""")
NULL_SINKS = frozenset({"/dev/null", "$null", "nul"})

# Commands that change files they name anywhere in their arguments.
WRITE_RE = re.compile(
    r"""(?ix)
      \bsed\s+(?:-\w*\s+)*-i
    | \btee\b
    | \b(?:Set-Content|Add-Content|Out-File|New-Item|Copy-Item|Move-Item|Remove-Item|Rename-Item)\b
    | (?:^|[;&|]\s*)(?:cp|mv|rm|touch|mkdir)\s
    | \bgit\s+(?:apply|am|checkout\s+--|restore)\b
    | \bpatch\s
    """
)

# Prose/data files: changing only these needs no test run (reports, notes, logs,
# queue/state YAML). Files under a session scratchpad are throwaway helpers.
DOC_EXTS = frozenset({"md", "markdown", "txt", "rst", "log", "csv", "tsv", "jsonl", "yaml", "yml"})
FILE_TOKEN_RE = re.compile(r"[\w.~/\\:$-]*[\w$]\.([A-Za-z][A-Za-z0-9]{0,7})\b")
SCRATCH_RE = re.compile(r"[/\\]scratchpad[/\\]", re.IGNORECASE)
# A shell command's arguments are full of dotted names that are not files
# (sys.path, $b.Trim, s.replace), so a shell write counts only when it names a file
# with one of these extensions. Edit-tool paths are real paths and need no allowlist.
CODE_EXTS = frozenset({
    "py", "pyi", "ps1", "psm1", "psd1", "sh", "bash", "bat", "cmd",
    "cs", "csproj", "sln", "props", "targets", "xaml", "resx", "vb", "fs", "fsproj",
    "ts", "tsx", "js", "jsx", "mjs", "cjs", "vue", "svelte", "css", "scss", "html",
    "go", "rs", "java", "kt", "kts", "gradle", "c", "h", "cc", "cpp", "hpp", "swift",
    "rb", "php", "lua", "dart", "sql", "json", "toml", "ini", "cfg", "xml", "patch", "diff",
})


def names_code_file(token: str) -> bool:
    m = FILE_TOKEN_RE.fullmatch(token)
    return bool(m) and not token.startswith("$") and not SCRATCH_RE.search(token) \
        and m.group(1).lower() in CODE_EXTS


def is_doc(path: str) -> bool:
    if SCRATCH_RE.search(path):
        return True
    return "." in path and path.rsplit(".", 1)[-1].lower() in DOC_EXTS


def shell_changes_code(cmd: str) -> bool:
    """True when a shell command plausibly changed a non-doc file.

    A write command (sed -i, tee, Set-Content, cp, git apply, ...) counts when it names
    a code file (CODE_EXTS) anywhere in its arguments. A bare redirect counts only when
    its own target is a code file. Variable-rooted ($X/...) and scratchpad paths are
    unknown or throwaway and never count.
    """
    if WRITE_RE.search(cmd):
        return any(names_code_file(m.group(0)) for m in FILE_TOKEN_RE.finditer(cmd))
    return any(m.group(1).lower() not in NULL_SINKS and names_code_file(m.group(1))
               for m in REDIRECT_RE.finditer(cmd))


CLAIM_RE = re.compile(
    r"""(?ix)
    \b(?:
        done | complete(?:d)? | finished | fixed | implemented | resolved | shipped
      | all\s+(?:tests|checks)\s+pass(?:ed|ing)? | tests?\s+pass(?:ed|es|ing)?
      | ready\s+(?:to|for)\s+(?:merge|review) | works\s+now
    )\b
    """
)

DISCLOSE_RE = re.compile(
    r"""(?ix)
      \bnot\s+(?:re-?)?(?:verified|tested|run|checked)\b
    | \bun(?:verified|tested)\b
    | \b(?:could|did)\s*(?:n['’]?t|\s+not)\s+(?:verify|test|run)\b
    | \bno\s+(?:tests?|verification)\b
    """
)

MESSAGE = (
    "verify-stop: this turn changed files and the last message claims completion, "
    "but no test/build/check ran after the last change. Verify it, or report it as not verified."
)


def read_tail(path: str) -> list[dict[str, Any]]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        data = fh.read()
    if size > TAIL_BYTES:
        data = data.split(b"\n", 1)[-1]  # drop the partial first line
    entries: list[dict[str, Any]] = []
    for line in data.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def blocks(entry: dict[str, Any]) -> list[Any]:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def is_prompt(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks(entry))


def current_turn(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    main = [e for e in entries if not e.get("isSidechain") and e.get("type") in ("user", "assistant")]
    start = 0
    for i, e in enumerate(main):
        if is_prompt(e):
            start = i + 1
    return main[start:]


def tool_uses(turn: list[dict[str, Any]]) -> Iterator[tuple[str, dict[str, Any]]]:
    for e in turn:
        if e.get("type") != "assistant":
            continue
        for b in blocks(e):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                inp = b.get("input")
                yield str(b.get("name") or ""), inp if isinstance(inp, dict) else {}


def last_text(turn: list[dict[str, Any]]) -> str:
    for e in reversed(turn):
        if e.get("type") != "assistant":
            continue
        texts = [b.get("text", "") for b in blocks(e) if isinstance(b, dict) and b.get("type") == "text"]
        if any(t.strip() for t in texts):
            return "\n".join(texts)
    return ""


def needs_warning(turn: list[dict[str, Any]], final_text: str) -> bool:
    changed = False
    checked_since_change = False
    for name, inp in tool_uses(turn):
        if name in EDIT_TOOLS:
            if not is_doc(str(inp.get("file_path") or inp.get("notebook_path") or "")):
                changed, checked_since_change = True, False
        elif name in SHELL_TOOLS:
            cmd = str(inp.get("command") or "")
            if CHECK_RE.search(cmd):
                checked_since_change = True
            elif shell_changes_code(cmd):
                changed, checked_since_change = True, False
    if not changed or checked_since_change:
        return False
    return bool(CLAIM_RE.search(final_text)) and not DISCLOSE_RE.search(final_text)


def main() -> int:
    if os.environ.get("VERIFY_STOP_HOOK", "1") == "0":
        return 0
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return 0
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return 0
    turn = current_turn(read_tail(path))
    final_text = payload.get("last_assistant_message")
    if not isinstance(final_text, str) or not final_text.strip():
        final_text = last_text(turn)
    if needs_warning(turn, final_text):
        sys.stdout.write(json.dumps({"systemMessage": MESSAGE}))
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # advisory: never let a failure touch the session
        try:
            sys.stderr.write(f"verify_stop: {type(exc).__name__}\n")
        except Exception:
            pass
    sys.exit(0)
