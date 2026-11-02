"""PreToolUse ship guard: from a Claude session, red code does not merge and ungated code does not push (2026-11-02).

GitHub branch protection is not used in these repositories (owner ruling, 2026-11-02: the fix happens on our side, not in
GitHub settings), so nothing on GitHub stops a red PR from merging; claude-profile #53 and #55 merged with a failing check.
This hook replaces required checks for every merge and push a Claude session can start.

MERGES. A merge is allowed only when every check on the PR head is green:

  - `gh pr merge [<number> | <url> | <branch>] [--repo OWNER/REPO] ...`, including `--auto` and `--admin`
    (`--disable-auto` only turns auto-merge off and is allowed);
  - the REST merge endpoint, `.../repos/OWNER/REPO/pulls/N/merge`, through `gh api`, curl, Invoke-RestMethod or anything
    else that names it;
  - GraphQL `mergePullRequest` / `enablePullRequestAutoMerge`: always refused, because the guard cannot tell which PR a
    node id is; `gh pr merge` is the route it can check;
  - an MCP tool named `*merge_pull_request` (owner, repo and pull number in its input);
  - the app's auto-merge switch (`mcp__ccd_pr__set_auto_merge`) when it turns auto-merge on: with no required checks,
    auto-merge has nothing to wait for.

Green means: the head commit reports at least one check, and every check run has completed with SUCCESS, NEUTRAL or
SKIPPED and every commit status is SUCCESS. Pending, failed, cancelled, timed out, action required, stale or missing all
refuse the merge. The state comes from `gh pr view --json statusCheckRollup` at the moment of the merge.

PUSHES AND COMMITS. A repository that ships the gate (its `.githooks/pre-push` carries the marker `ship-guard: gate`)
runs its whole local gate, CI's tests included, on every push. This hook makes sure a session cannot step around it:

  - `git push` in such a repository is refused unless its `core.hooksPath` resolves to the repository's `.githooks`
    (turn it on with `python <profile>/tools/git_gate.py`, which bootstrap runs);
  - `git push --no-verify` and `git commit --no-verify` / `-n` are refused everywhere;
  - `git -c core.hooksPath=...`, `--config-env` for it, `GIT_CONFIG_*` carrying it, and `git config` writing any value
    other than `.githooks` (or unsetting it) are refused;
  - the audited bypass variables (BIMP_PREPUSH_BYPASS, BIMP_SECRETS_BYPASS) are refused in a command and while they are
    set in the session's environment: they are for the owner, typed in the owner's own terminal;
  - an MCP tool that writes files straight to GitHub (`*push_files`, `*create_or_update_file`) is refused: it skips every
    local gate. Push from a clone instead.

The directory a push runs in is followed through `cd`, `pushd`, `Set-Location` / `sl` / `Push-Location`, and `git -C`, and
into `bash -c` / `sh -c` / `powershell -Command` / `pwsh -c` / `cmd /c` arguments. A directory it cannot know (a variable,
`popd`) refuses the push with the literal form to use instead.

FAIL-CLOSED CONTRACT (as hooks/protect_paths_gate.py): a payload it cannot read, a merge or push command it cannot parse,
or a `gh` call that fails all block. Exit 0 = allow, exit 2 = block (stderr is shown to Claude). A command that mentions no
merge, push, commit, hooks path or bypass exits 0 after one regex test, so ordinary Bash and PowerShell calls cost nothing;
any other tool (Write, Read, ...) is allowed untouched.

SCOPE HONESTY: this binds Claude sessions on machines that run this profile. The owner merging in the GitHub web UI or
pushing from a terminal is not affected, by design. A command that hides the git or gh call from the text (an alias, a
script file, an encoded command) is not seen; the hooks themselves, the gate they run and CI are the layers behind it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

BLOCK_EXIT = 2
GATE_MARKER = "ship-guard: gate"
BYPASS_VARS = ("BIMP_PREPUSH_BYPASS", "BIMP_SECRETS_BYPASS")
SHELL_TOOLS = {"Bash", "PowerShell"}

# Cheap prefilters: anything that could be a merge, or a push / commit / hooks-path / bypass. Everything else exits 0.
MAYBE_MERGE = re.compile(r"\bmerge\b|/merge\b|mergePullRequest|enablePullRequestAutoMerge", re.IGNORECASE)
MAYBE_SHIP = re.compile(r"\bpush\b|\bcommit\b|hookspath|BIMP_\w*BYPASS", re.IGNORECASE)
REST_MERGE = re.compile(r"repos/([^/\s'\"`]+)/([^/\s'\"`]+)/pulls/(\d+)/merge\b")
GRAPHQL_MERGE = re.compile(r"\b(mergePullRequest|enablePullRequestAutoMerge)\b")
GIT_CONFIG_ENV = re.compile(r"GIT_CONFIG_(PARAMETERS|KEY_\d+|COUNT)")
GIT_SHIP_LINE = re.compile(r"\bgit(\.exe)?\b.*\b(push|commit|config)\b", re.IGNORECASE)
OPERATORS = {";", "&", "&&", "|", "||", "(", ")", "\n"}
# `gh pr merge` flags that take a value (gh 2.x); every other flag is a switch.
VALUE_FLAGS = {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject", "-A", "--author-email",
               "--match-head-commit"}
GREEN_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
# git global options that take a value as the next token; the subcommand is the first token that is not one of these.
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path"}
# `git commit` short options that take a value: in a bundle (`-am msg`) the letters after one of these are the value.
COMMIT_VALUE_LETTERS = set("mFCctSu")
CD_COMMANDS = {"cd", "chdir", "pushd", "set-location", "sl", "push-location"}
SHELLS_C = {"bash", "sh", "zsh", "dash", "bash.exe", "sh.exe"}
POWERSHELLS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
DIRECT_WRITE_TOOLS = re.compile(r"(push_files|create_or_update_file)$")

Runner = Callable[[list, Optional[str]], "subprocess.CompletedProcess[str]"]


class Refuse(Exception):
    """The call is refused; the message says why and what to do."""


@dataclass(frozen=True)
class Target:
    """One PR a command would merge: a selector `gh pr view` accepts, and the repository when it is not the cwd's."""
    selector: Optional[str]
    repo: Optional[str]

    def describe(self) -> str:
        what = self.selector or "the current branch's PR"
        return f"{what} in {self.repo}" if self.repo else what


def _tokens(command: str, powershell: bool = False) -> list:
    lexer = shlex.shlex(command.replace("\r\n", "\n"), posix=True, punctuation_chars=";&|()")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    if powershell:
        lexer.escape = ""  # backslashes are path separators in PowerShell; its escape is the backtick
    return list(lexer)


def _segments(tokens: list) -> Iterable[list]:
    """The token stream split at shell operators: one list per simple command."""
    current: list = []
    for tok in tokens:
        if tok in OPERATORS or set(tok) <= set(";&|()\n"):
            if current:
                yield current
            current = []
        else:
            current.append(tok)
    if current:
        yield current


# ----------------------------------------------------------------------------------------------------------- merges --

def _gh_pr_merge_args(tokens: list) -> Iterable[list]:
    """The argument list after every `gh pr merge` in the token stream, up to the next shell operator."""
    for i, tok in enumerate(tokens):
        name = _base(tok)
        if name in ("gh", "gh.exe") and tokens[i + 1:i + 3] == ["pr", "merge"]:
            args = []
            for arg in tokens[i + 3:]:
                if arg in OPERATORS or set(arg) <= set(";&|()"):
                    break
                args.append(arg)
            yield args


def _target_from_args(args: list) -> Optional[Target]:
    """The PR `gh pr merge <args>` merges, or None when the call only turns auto-merge off."""
    selector, repo, i = None, None, 0
    if "--disable-auto" in args:
        return None
    while i < len(args):
        arg = args[i]
        flag, eq, value = arg.partition("=")
        if arg.startswith("-") and flag in VALUE_FLAGS:
            if not eq:
                i += 1
                value = args[i] if i < len(args) else ""
            if flag in ("-R", "--repo"):
                repo = value or None
        elif not arg.startswith("-") and selector is None:
            selector = arg
        i += 1
    return Target(selector, repo)


def targets_in_command(command: str, powershell: bool = False) -> list:
    """Every PR this shell command would merge. Raises Refuse for a merge the guard cannot check."""
    if not MAYBE_MERGE.search(command):
        return []
    if GRAPHQL_MERGE.search(command):
        raise Refuse("a GraphQL merge (mergePullRequest / enablePullRequestAutoMerge) names the PR by node id, which "
                     "this guard cannot check. Use `gh pr merge <number> --repo OWNER/REPO` once every check is green.")
    found = []
    for owner, name, number in REST_MERGE.findall(command):
        repo = None if (owner, name) == ("{owner}", "{repo}") else f"{owner}/{name}"
        found.append(Target(number, repo))
    if re.search(r"\bpr\b", command) and re.search(r"\bmerge\b", command):
        try:
            tokens = _tokens(command, powershell)
        except ValueError as exc:  # unbalanced quotes: the arguments cannot be trusted
            raise Refuse(f"could not parse the command to find which PR it merges ({exc}).") from exc
        for args in _gh_pr_merge_args(tokens):
            target = _target_from_args(args)
            if target is not None:
                found.append(target)
    return found


def targets_in_mcp(tool_name: str, tool_input: dict) -> list:
    if tool_name.endswith("set_auto_merge"):
        flags = [tool_input.get(k) for k in ("enabled", "enable", "auto_merge", "on") if k in tool_input]
        if flags and not any(flags):
            return []  # turning auto-merge off
        raise Refuse("auto-merge has no required checks to wait for in these repositories, so it can merge a red PR. "
                     "Merge with `gh pr merge` once every check on the head is green.")
    owner = tool_input.get("owner")
    repo = tool_input.get("repo")
    number = tool_input.get("pullNumber", tool_input.get("pull_number", tool_input.get("number")))
    if not (owner and repo and number):
        raise Refuse(f"{tool_name}: the input does not name owner, repo and pull number, so the PR cannot be checked.")
    return [Target(str(number), f"{owner}/{repo}")]


def _run(argv: list, cwd: Optional[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, cwd=cwd or None, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=25)


def not_green(rollup: list) -> list:
    """Why the head is not green: one line per check that is not, or the empty list when every check is green."""
    if not rollup:
        return ["no checks reported on the head commit"]
    problems = []
    for item in rollup:
        if item.get("__typename") == "StatusContext" or "context" in item:
            name, state = item.get("context") or "status", (item.get("state") or "").upper()
            if state != "SUCCESS":
                problems.append(f"{name}: {state.lower() or 'unknown'}")
            continue
        name = item.get("name") or item.get("workflowName") or "check"
        status, conclusion = (item.get("status") or "").upper(), (item.get("conclusion") or "").upper()
        if status != "COMPLETED":
            problems.append(f"{name}: {status.lower() or 'unknown'}")
        elif conclusion not in GREEN_CONCLUSIONS:
            problems.append(f"{name}: {conclusion.lower() or 'no conclusion'}")
    return problems


def check_target(target: Target, cwd: Optional[str], run: Runner = _run) -> None:
    """Return when the PR's head is green; raise Refuse otherwise."""
    argv = ["gh", "pr", "view"] + ([target.selector] if target.selector else [])
    argv += ["--repo", target.repo] if target.repo else []
    argv += ["--json", "number,url,state,headRefOid,statusCheckRollup"]
    try:
        proc = run(argv, cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refuse(f"could not read the checks of {target.describe()} ({exc}); a merge that cannot be checked is "
                     "not allowed.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        raise Refuse(f"could not read the checks of {target.describe()}: {detail[0]}")
    try:
        pr = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Refuse(f"`gh pr view` returned unreadable output for {target.describe()}.") from exc
    problems = not_green(pr.get("statusCheckRollup") or [])
    if problems:
        head = (pr.get("headRefOid") or "")[:12]
        listed = "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else "")
        raise Refuse(f"{pr.get('url') or target.describe()} is not green at head {head}: {listed}. Fix the code and "
                     "push again; a known or flaky failure is fixed too, not merged over.")


# ------------------------------------------------------------------------------------------------- pushes, commits --

@dataclass(frozen=True)
class Push:
    """One `git push` a command would run, and the directory it runs in (None when the guard cannot know it)."""
    directory: Optional[str]
    why_unknown: str = ""


def _base(word: str) -> str:
    """The command name of a word, without its directory (either separator, on any OS), lower case."""
    return re.split(r"[\\/]", word)[-1].lower()


def _native(path: str) -> str:
    """A Git Bash drive path (`/f/x`) as the Windows path it names; `~` expanded. Other paths unchanged."""
    path = os.path.expanduser(path)
    if os.name == "nt":
        m = re.match(r"^/([a-zA-Z])(/.*)?$", path)
        if m:
            path = f"{m.group(1).upper()}:{m.group(2) or '/'}"
    return path


def _join(base: Optional[str], target: str) -> Optional[str]:
    if any(ch in target for ch in "$`%"):
        return None
    target = _native(target)
    if os.path.isabs(target):
        return os.path.normpath(target)
    return None if base is None else os.path.normpath(os.path.join(base, target))


def _long_flag(arg: str, name: str) -> bool:
    """True when `arg` is `name` or an abbreviation git would accept for it (`--no-ver` for `--no-verify`)."""
    flag = arg.partition("=")[0]
    return flag.startswith("--") and len(flag) > 5 and name.startswith(flag)


def _commit_skips_hooks(args: list) -> bool:
    for arg in args:
        if arg == "--":
            return False
        if _long_flag(arg, "--no-verify"):
            return True
        if arg.startswith("-") and not arg.startswith("--"):
            for letter in arg[1:]:
                if letter == "n":
                    return True
                if letter in COMMIT_VALUE_LETTERS:
                    break
    return False


def _config_writes_hooks_path(args: list) -> bool:
    """True when `git config <args>` would set core.hooksPath to anything but `.githooks`, or unset it."""
    low = [a.lower() for a in args]
    if "core.hookspath" not in low:
        return False
    k = low.index("core.hookspath")
    flags = {a.split("=")[0] for a in low if a.startswith("-")}
    if "unset" in low[:k] or flags & {"--unset", "--unset-all"}:  # `unset` is git 2.46+ `git config unset <key>`
        return True
    if "get" in low[:k] or flags & {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin"}:
        return False
    values = [a for a in args[k + 1:] if not a.startswith("-")]
    return bool(values) and values[0] != ".githooks"


def _git_call(words: list, cwd: Optional[str], why: str, pushes: list) -> None:
    """Check one `git ...` simple command: refuse hook-skipping forms; record a push with the directory it runs in."""
    i, directory, unknown = 1, cwd, why
    while i < len(words) and words[i].startswith("-"):
        opt, eq, value = words[i].partition("=")
        if opt in GIT_VALUE_OPTS and not eq:
            i += 1
            value = words[i] if i < len(words) else ""
        if opt == "-c" and value.split("=")[0].lower() == "core.hookspath":
            raise Refuse("`git -c core.hooksPath=...` replaces the repository's gate hooks. Run git without it.")
        if opt == "--config-env" and value.split("=")[0].lower() == "core.hookspath":
            raise Refuse("`git --config-env core.hooksPath=...` replaces the repository's gate hooks. Run git without it.")
        if opt == "-C":
            joined = _join(directory, value)
            if joined is None:
                unknown = unknown or f"`git -C {value}` names a directory the guard cannot resolve"
            directory = joined
        if opt in ("--git-dir", "--work-tree"):
            directory, unknown = None, f"`git {opt}` points git somewhere the guard does not follow"
        i += 1
    if i >= len(words):
        return
    sub, args = words[i].lower(), words[i + 1:]
    if sub == "push":
        if any(_long_flag(a, "--no-verify") for a in args):
            raise Refuse("`git push --no-verify` skips the pre-push gate. Push without it; if the gate fails, fix the code.")
        pushes.append(Push(directory, "" if directory else unknown or "the directory of this push is unknown"))
    elif sub == "commit":
        if _commit_skips_hooks(args):
            raise Refuse("`git commit --no-verify` / `-n` skips the pre-commit secret scan. Commit without it.")
    elif sub == "config":
        if _config_writes_hooks_path(args):
            raise Refuse("core.hooksPath is the gate's switch: only `python <profile>/tools/git_gate.py` (or "
                         "`git config core.hooksPath .githooks`) sets it, and a session never unsets or redirects it.")


def _strip_env(words: list) -> list:
    """The command words with leading `NAME=value` assignments and an `env` / `&` / `command` prefix removed."""
    while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0].lower() in ("env", "command", "exec")):
        words = words[1:]
    return words


def scan_shell(command: str, cwd: Optional[str], powershell: bool, pushes: list, depth: int = 0) -> None:
    """Walk one shell command for pushes and hook-skipping forms; nested `bash -c` / `pwsh -c` strings too."""
    if depth > 4:
        raise Refuse("the command nests shells too deeply for the guard to follow.")
    try:
        tokens = _tokens(command, powershell)
    except ValueError as exc:
        if any(GIT_SHIP_LINE.search(line) for line in command.splitlines()):
            raise Refuse(f"could not parse the command to check its git push / commit ({exc}). Run that git command "
                         "on its own.") from exc
        return
    here, why = cwd, ""
    for words in _segments(tokens):
        words = _strip_env(words)
        if not words:
            continue
        name = _base(words[0])
        if name in CD_COMMANDS:
            rest = [w for w in words[1:] if not w.startswith("-")]
            path_flag = [words[k + 1] for k, w in enumerate(words[:-1]) if w.lower() in ("-path", "-literalpath")]
            target = (path_flag or rest or ["~"])[0]
            joined = _join(here, target)
            if joined is None:
                why = f"`{words[0]} {target}` changes to a directory the guard cannot resolve"
            here = joined
        elif name in ("popd", "pop-location"):
            here, why = None, f"`{words[0]}` returns to a directory the guard does not track"
        elif name in ("git", "git.exe"):
            _git_call(words, here, why, pushes)
        elif name in SHELLS_C or name in POWERSHELLS or name in ("cmd", "cmd.exe"):
            nested_ps = name in POWERSHELLS
            for k, w in enumerate(words[1:], 1):
                low = w.lower()
                if (name in SHELLS_C and low.startswith("-") and not low.startswith("--") and "c" in low) or \
                   (nested_ps and low in ("-c", "-command")) or (name.startswith("cmd") and low in ("/c", "/k")):
                    inner = words[k + 1] if name in SHELLS_C else " ".join(words[k + 1:])
                    if inner:
                        scan_shell(inner, here, nested_ps, pushes, depth + 1)
                    break


def gate_is_on(directory: str, run: Runner = _run) -> Optional[str]:
    """None when a push from `directory` runs the repository's gate (or the repository ships none); else why not."""
    if not os.path.isdir(directory):
        return f"the push runs in {directory}, which does not exist"
    try:
        top = run(["git", "-C", directory, "rev-parse", "--show-toplevel"], None)
    except (OSError, subprocess.SubprocessError):
        return None  # no git here: there is no push either
    if top.returncode != 0:
        return None  # not a work tree: nothing of a gated repository to push
    root = Path(top.stdout.strip())
    hook = root / ".githooks" / "pre-push"
    try:
        if GATE_MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
            return None
    except OSError:
        return None  # this repository ships no gate
    proc = run(["git", "-C", str(root), "config", "--get", "core.hooksPath"], None)
    value = proc.stdout.strip() if proc.returncode == 0 else ""
    if value:
        resolved = Path(_native(value))
        resolved = resolved if resolved.is_absolute() else root / resolved
        try:
            if resolved.resolve() == (root / ".githooks").resolve():
                return None
        except OSError:
            pass
    shown = f"is {value!r}" if value else "is not set"
    return (f"{root} ships the pre-push gate, but its core.hooksPath {shown}, so a push would skip CI's tests. "
            f"Turn the gate on first: python <profile>/tools/git_gate.py (then `--check` prints `git gate: ON`).")


def check_ship(command: str, cwd: Optional[str], powershell: bool, environ: Mapping[str, str],
               run: Runner = _run) -> None:
    if not MAYBE_SHIP.search(command):
        return
    for var in BYPASS_VARS:
        if var in command:
            raise Refuse(f"{var} is the owner's audited bypass, typed in the owner's own terminal; a session does not "
                         "set it. Fix what the gate reports instead.")
    if GIT_CONFIG_ENV.search(command) and "hookspath" in command.lower():
        raise Refuse("GIT_CONFIG_* carrying core.hooksPath replaces the repository's gate hooks. Run git without it.")
    pushes: list = []
    scan_shell(command, cwd, powershell, pushes)
    if not pushes:
        return
    for var in BYPASS_VARS:
        if environ.get(var):
            raise Refuse(f"{var} is set in this session's environment, so the gate would be skipped. It is the owner's "
                         "bypass for the owner's terminal; unset it in the session before pushing.")
    for push in pushes:
        if push.directory is None:
            raise Refuse(f"{push.why_unknown}, so the guard cannot check that the gate runs. Use a literal directory: "
                         "git -C <dir> push ...")
        reason = gate_is_on(push.directory, run)
        if reason:
            raise Refuse(reason)


# ------------------------------------------------------------------------------------------------------------ entry --

def decide(payload: dict, run: Runner = _run, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """None to allow; otherwise the reason the call is refused."""
    environ = os.environ if environ is None else environ
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return "the hook payload has no tool_input, so the call cannot be checked."
    cwd = payload.get("cwd") or None
    try:
        if tool_name.startswith("mcp__"):
            if DIRECT_WRITE_TOOLS.search(tool_name):
                raise Refuse(f"{tool_name} writes files straight to GitHub and skips every local gate. Commit in a "
                             "clone and `git push` instead.")
            if not (tool_name.endswith("merge_pull_request") or tool_name.endswith("set_auto_merge")):
                return None
            targets = targets_in_mcp(tool_name, tool_input)
        elif tool_name in SHELL_TOOLS:
            command = tool_input.get("command")
            if not isinstance(command, str):
                return f"{tool_name}: the payload has no command string, so it cannot be checked."
            powershell = tool_name == "PowerShell"
            check_ship(command, cwd, powershell, environ, run)
            targets = targets_in_command(command, powershell)
        else:
            return None
        for target in targets:
            check_target(target, cwd, run)
    except Refuse as exc:
        return str(exc)
    return None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
        reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
        reason = f"ship guard could not evaluate the call ({type(exc).__name__}: {exc})."
    if reason:
        sys.stderr.write(f"BLOCKED by ship guard: {reason}\n")
        sys.exit(BLOCK_EXIT)
    sys.exit(0)


if __name__ == "__main__":
    main()
