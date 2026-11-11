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
     ruff, ...: see CHECK_RE). A command line is read one simple command at a time; a
     reader or editor that only names a check tool or a test file (grep, cat, sed, ...:
     see NOT_A_RUNNER) is not a check, and neither is an install of a check tool, a
     comment, a no-op mode (--version, --collect-only), a check sent to the background
     with `&`, or a check whose tool result came back as an error;
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


def tool_uses(turn: list[dict[str, Any]]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """(tool_use id, tool name, input) for every tool call of the turn, in order."""
    for e in turn:
        if e.get("type") != "assistant":
            continue
        for b in blocks(e):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                inp = b.get("input")
                yield str(b.get("id") or ""), str(b.get("name") or ""), inp if isinstance(inp, dict) else {}


def failed_tool_ids(turn: list[dict[str, Any]]) -> frozenset[str]:
    """Ids of the tool calls whose result came back as an error (`is_error`).

    A shell command that exits non-zero is reported that way, so a failed test run is
    in this set. A call with no recorded result is not: it is treated as before.
    """
    failed = set()
    for e in turn:
        if e.get("type") != "user":
            continue
        for b in blocks(e):
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error") is True:
                tid = b.get("tool_use_id")
                if isinstance(tid, str) and tid:
                    failed.add(tid)
    return frozenset(failed)


# Commands that read, list, print or edit files. One of these naming a check tool or a
# test file (`grep pytest pyproject.toml`, `cat pytest.ini`, `sed -i ... test_app.py`)
# ran no check.
NOT_A_RUNNER = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "cat", "bat", "type", "head", "tail", "less", "more",
    "sed", "awk", "gawk", "cut", "sort", "uniq", "wc", "tr", "tee", "echo", "printf", "ls", "dir", "find", "fd",
    "tree", "stat", "file", "diff", "cmp", "git", "gh", "cp", "mv", "rm", "touch", "mkdir", "ln", "chmod", "cd",
    "pushd", "popd", "export", "set", "which", "where", "whereis", "curl", "wget", "jq", "xxd", "sha256sum",
    "get-content", "gc", "select-string", "sls", "get-childitem", "gci", "get-item", "test-path", "get-command",
    "write-output", "write-host", "set-content", "add-content", "out-file", "new-item", "copy-item", "move-item",
    "remove-item", "rename-item", "set-location", "measure-object", "select-object", "where-object",
})
# A check tool run in a mode that checks nothing (`pytest --version`, `--collect-only`).
NO_OP_RE = re.compile(r"(?:^|\s)(?:--version|--help|--collect-only|--co)(?=\s|$)")
# Installing a check tool (`pip install pytest`, `npm install --save-dev jest`) runs no check.
INSTALL_RE = re.compile(
    r"""(?ix)
    \b(?:pip3?|uv\s+pip|conda|mamba|pipx)\s+install\b
    | \b(?:npm|pnpm|yarn|bun)\s+(?:install|i|add)\b
    | \buv\s+add\b | \bInstall-(?:Module|Package)\b
    """
)
ENV_ASSIGN_RE = re.compile(r"\s*(?:[A-Za-z_]\w*=\S*\s+)*")
VARIABLE_RE = re.compile(r"\$[A-Za-z_{(]")  # `$f`, `${f}`, `$(...)`; not a regex `$` anchor


def split_pipelines(cmd: str) -> list[list[str]]:
    """Split a shell command line into pipelines, and each pipeline into its `|` pieces.

    Pipelines are separated by `&&`, `||`, `;`, newlines and a single `&`, outside
    quotes. A pipeline that a single `&` sent to the background keeps that `&` at the
    end of its last piece (see `in_background`).
    """
    pipelines: list[list[str]] = [[]]
    buf: list[str] = []
    quote, i = "", 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(cmd):
                buf.append(cmd[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ""
            buf.append(ch)
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "\\" and i + 1 < len(cmd):
            buf.append(cmd[i:i + 2])
            i += 1
        elif ch in ";\n|" or cmd.startswith("&&", i):
            pipelines[-1].append("".join(buf))
            buf = []
            if cmd.startswith(("&&", "||"), i):
                i += 1
                pipelines.append([])
            elif ch != "|":
                pipelines.append([])
        elif ch == "&" and ends_command(cmd, i, buf):
            pipelines[-1].append("".join(buf) + "&")
            buf = []
            pipelines.append([])
        else:
            buf.append(ch)
        i += 1
    pipelines[-1].append("".join(buf))
    cleaned = ([s for s in (piece.strip() for piece in pipeline) if s] for pipeline in pipelines)
    return [pipeline for pipeline in cleaned if pipeline]


def ends_command(cmd: str, i: int, buf: list[str]) -> bool:
    """True when the single `&` at cmd[i] ends a command (bash: run it in the background).

    Not when it is part of a redirect (`2>&1`, `>&2`, `&>`), follows a pipe (`|&`), or is
    PowerShell's call operator (`& cmd` at the start of a command, `$r = & cmd`).
    """
    before = "".join(buf).rstrip()
    return bool(before) and before[-1] not in "<>|&=(" and not cmd.startswith(">", i + 1)


def in_background(pipeline: list[str]) -> bool:
    return pipeline[-1].endswith("&") and not pipeline[-1].endswith("&&")


def split_segments(cmd: str) -> list[str]:
    """Split a shell command line on `&&`, `||`, `;`, `|` and newlines, outside quotes."""
    return [piece for pipeline in split_pipelines(cmd) for piece in pipeline]


def runs_check(segment: str) -> bool:
    """True when one simple command (no `&&`, `|`, ...) plausibly ran a test/build/lint."""
    rest = segment[ENV_ASSIGN_RE.match(segment).end():].lstrip("({& ")
    words = rest.split(None, 1)
    if not words or rest.startswith("#") or NO_OP_RE.search(rest) or INSTALL_RE.search(rest):
        return False
    head = re.split(r"[/\\]", words[0].strip("'\""))[-1].lower()
    if head.endswith(".exe"):
        head = head[:-4]
    return head not in NOT_A_RUNNER and bool(CHECK_RE.search(rest))


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
    failed = failed_tool_ids(turn)
    for tid, name, inp in tool_uses(turn):
        if name in EDIT_TOOLS:
            if not is_doc(str(inp.get("file_path") or inp.get("notebook_path") or "")):
                changed, checked_since_change = True, False
        elif name in SHELL_TOOLS:
            # Each simple command in order: `fix && test` ends checked, `test; fix` does not.
            pipelines = split_pipelines(str(inp.get("command") or ""))
            # A write through a variable (`f=app.py; sed -i ... "$f"`) names its file in an
            # earlier command, so the non-check pieces of the whole line are read for it.
            line_names_code = any(
                names_code_file(m.group(0))
                for pipeline in pipelines for piece in pipeline if not runs_check(piece)
                for m in FILE_TOKEN_RE.finditer(piece))
            for pipeline in pipelines:
                # A write fed through a pipe (`cat fix.patch | git apply`) names its file in
                # another piece, so the pieces that are not checks are also read together.
                others = " | ".join(piece for piece in pipeline if not runs_check(piece))
                background = in_background(pipeline)
                for segment in pipeline:
                    if runs_check(segment):
                        # A check that errored, or that was left running, verified nothing.
                        if tid not in failed and not background:
                            checked_since_change = True
                    elif shell_changes_code(segment) or (WRITE_RE.search(segment) and (
                            shell_changes_code(others)
                            or (line_names_code and VARIABLE_RE.search(segment)))):
                        changed, checked_since_change = True, False
    if not changed or checked_since_change:
        return False
    return bool(CLAIM_RE.search(final_text)) and not DISCLOSE_RE.search(final_text)


def main() -> int:
    if os.environ.get("VERIFY_STOP_HOOK", "1") == "0":
        return 0
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        raise ValueError("empty hook input")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("hook input is not a JSON object")
    if payload.get("stop_hook_active"):
        return 0
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        raise FileNotFoundError("no readable transcript_path")
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
