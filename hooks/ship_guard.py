"""PreToolUse ship guard: from a Claude session, red code does not merge and ungated code does not push (2026-11-02).

GitHub branch protection is not used in these repositories (owner ruling, 2026-11-02: the fix happens on our side, not in
GitHub settings), so nothing on GitHub stops a red PR from merging; claude-profile #53 and #55 merged with a failing check.
This hook replaces required checks for every merge and push a Claude session can start.

MERGES. A merge is allowed only when every check on the PR head is green, and the merge pins that head:

  - `gh pr merge [<number> | <url> | <branch>] [--repo OWNER/REPO] --match-head-commit <sha> ...`, including `--auto`
    and `--admin` (`--disable-auto` only turns auto-merge off and is allowed);
  - the REST merge endpoint, `.../repos/OWNER/REPO/pulls/N/merge`, through `gh api`, curl, Invoke-RestMethod or anything
    else that names it; the pin is read only from a `-f` / `-F` / `--field` / `--raw-field` named exactly `sha`, or the
    `sha` key of a JSON body (`-d` / `--data` / `--json` / `-Body`);
  - GraphQL `mergePullRequest` / `enablePullRequestAutoMerge`: always refused, because the guard cannot tell which PR a
    node id is; `gh pr merge` is the route it can check;
  - GitHub's branch merge (REST `repos/OWNER/REPO/merges`, GraphQL `mergeBranch`): always refused, as it merges with no
    PR and no checks;
  - an MCP tool named `*merge_pull_request` (owner, repo, pull number and `sha` in its input);
  - the app's auto-merge switch (`mcp__ccd_pr__set_auto_merge`) when it turns auto-merge on: with no required checks,
    auto-merge has nothing to wait for.

Green means: the head commit reports at least one check that passed (a check run with SUCCESS or a commit status of
SUCCESS), and every check run has completed with SUCCESS, NEUTRAL or SKIPPED and every commit status is SUCCESS. A head
whose checks were all skipped or neutral is not green: nothing ran. Pending, failed, cancelled, timed out, action required, stale or missing all
refuse the merge. The state comes from `gh pr view --json statusCheckRollup` plus the head commit's own check runs
(`gh api repos/OWNER/REPO/commits/<head>/check-runs`: a manually dispatched workflow reports only there), at the moment of
the merge, run in the directory the merge runs in (followed through `cd` like a push, below). The pinned SHA must be the full head the guard
checked, so a commit pushed between the check and the merge makes GitHub refuse the merge. `GH_REPO` / `GH_HOST` in a
merging command are refused: `--repo` is the form the guard reads.

PUSHES AND COMMITS. A repository that ships the gate (its `.githooks/pre-push` carries the marker `ship-guard: gate`)
runs its whole local gate, CI's tests included, on every push. This hook makes sure a session cannot step around it:

  - `git push` in such a repository is refused unless its `core.hooksPath` resolves to the repository's `.githooks`
    (turn it on with `python <profile>/tools/git_gate.py`, which bootstrap runs);
  - `git push --no-verify` and `git commit --no-verify` / `-n` are refused everywhere;
  - `git -c core.hooksPath=...` (or a `-c` key it cannot read), `--config-env` for it, `GIT_CONFIG_*` carrying it or set
    in a pushing command, and `git config` writing any value other than `.githooks` (or unsetting it) are refused;
  - `GIT_DIR` / `GIT_WORK_TREE` / `GIT_COMMON_DIR` in a pushing command or in the session's environment are refused:
    they point git somewhere the guard does not follow;
  - the audited bypass variables (BIMP_PREPUSH_BYPASS, BIMP_SECRETS_BYPASS) are refused in a command and while they are
    set in the session's environment: they are for the owner, typed in the owner's own terminal;
  - `git -c` / `--config-env` / `git config` setting an `alias.*`, `include.*` or `includeIf.*` key is refused: an
    alias can turn any subcommand into a push the guard cannot see;
  - writes that put code on a branch without a push are refused: an MCP tool that writes files straight to GitHub
    (`*push_files`, `*create_or_update_file`, `*delete_file`), the GraphQL `createCommitOnBranch` / `updateRef(s)` /
    `createRef` mutations, and a writing call (a write method, fields or a body) to the REST `contents/`, `git/refs`,
    `git/commits`, `git/trees` or `git/blobs` endpoints. Push from a clone instead.

The directory a push or merge runs in is followed through `cd` (cmd's `cd /d` too), `pushd`, `Set-Location` / `sl` /
`Push-Location`, `git -C`, and into `bash -c` / `sh -c` / `cmd /c` arguments, powershell / pwsh `-Command`,
`-CommandWithArgs`, `-EncodedCommand` (decoded), a positional command and `-WorkingDirectory`, `$(...)` substitutions
(nested parentheses counted) and backticks, and here-documents a shell reads. Here-documents are found the way bash finds
them (not inside quotes, comments or arithmetic); one fed to anything but a shell or a command that only reads it (`cat`,
`tee`, `git commit -F -`, ...) is refused when it holds a git push / commit / merge. Wrappers that run their arguments
(`env`, `timeout`, `nice`, `nohup`, `time`, `sudo`, `stdbuf`, `command`, `exec`, ...) are looked through. A directory it
cannot know (a variable, `cd -`, `popd`, `env -C`, a bare `D:` in cmd or PowerShell) refuses the push with the literal
form to use instead; git or gh run by something the guard does not follow (`xargs` anywhere in a wrapper chain, `eval`,
`iex`, `find -exec`, `Start-Process`, a shell reading its input) is refused.

FAIL-CLOSED CONTRACT (as hooks/protect_paths_gate.py): a payload it cannot read, a merge or push command it cannot parse,
or a `gh` call that fails all block. Exit 0 = allow, exit 2 = block (stderr is shown to Claude). A command that mentions no
merge, push, commit, hooks path or bypass exits 0 after one regex test, so ordinary Bash and PowerShell calls cost nothing;
any other tool (Write, Read, ...) is allowed untouched.

SCOPE HONESTY: this binds Claude sessions on machines that run this profile. The owner merging in the GitHub web UI or
pushing from a terminal is not affected, by design. A command that hides the git or gh call from the text (a git alias already in
the config, a script file, a command decoded at run time other than `-EncodedCommand`) is not seen; the hooks themselves, the gate they run and CI are the layers behind it.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import string
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

BLOCK_EXIT = 2
GATE_MARKER = "ship-guard: gate"
BYPASS_VARS = ("BIMP_PREPUSH_BYPASS", "BIMP_SECRETS_BYPASS")
SHELL_TOOLS = {"Bash", "PowerShell"}

# Cheap prefilters: anything that could be a merge, or a push / commit / hooks-path / bypass. Everything else exits 0.
MAYBE_MERGE = re.compile(r"\bmerge\b|/merge\b|mergePullRequest|enablePullRequestAutoMerge", re.IGNORECASE)
MAYBE_SHIP = re.compile(r"\bpush\b|\bcommit\b|hookspath|\b(alias|include|includeif)\.|BIMP_\w*BYPASS|GIT_CONFIG_|\bconfig\b",
                        re.IGNORECASE)
MAYBE_ENCODED = re.compile(
    r"\b(powershell|pwsh)(\.exe)?\b[^|;&\n]*[\s'\",](-|--|/)e"
    # a launcher, `$variable` or `(expression)` may start a powershell under any name: an exact -EncodedCommand word
    r"|(\b(start-process|saps|start)\b|[$(])[^|;&\n]*[\s'\",](-|--|/)"
    r"(e|ec|en(c(o(d(e(d(c(o(m(m(a(n(d)?)?)?)?)?)?)?)?)?)?)?)?)(?=[\s'\",]|$)", re.IGNORECASE)
PR_URL = re.compile(r"https?://([^/\s]+)/([^/\s]+)/([^/\s]+)/pull/\d+")
REST_MERGE = re.compile(r"repos/([^/\s'\"`]+)/([^/\s'\"`]+)/pulls/(\d+)/merge\b")
GRAPHQL_MERGE = re.compile(r"\b(mergePullRequest|enablePullRequestAutoMerge)\b")
# Writes that put code on a branch with no push, so no local gate runs: GitHub's branch merge (REST and GraphQL), the
# GraphQL commit / ref mutations, and the REST contents and git-data endpoints when the call writes.
MAYBE_GH_WRITE = re.compile(r"/merges\b|\bmergeBranch\b|\bcreateCommitOnBranch\b|\bupdateRefs?\b|\bcreateRef\b|"
                            r"/contents\b|/git/(refs|commits|trees|blobs)\b")
REST_BRANCH_MERGE = re.compile(r"repos/[^/\s'\"`]+/[^/\s'\"`]+/merges\b")
GRAPHQL_WRITE = re.compile(r"\b(mergeBranch|createCommitOnBranch|updateRefs?|createRef)\b")
REST_WRITE_PATH = re.compile(r"repos/[^/\s'\"`]+/[^/\s'\"`]+/(contents\b|git/(refs|commits|trees|blobs)\b)")
# A writing request anywhere in the command (gh api sends POST once any field is given). Checked across the whole
# command, not per piece, so a path and its write flag on different lines or in different pieces still refuse.
WRITE_REQUEST = re.compile(
    r"(^|[\s'\"])(-X|--method|--request|-Method)[\s=:]*['\"]?(PUT|POST|PATCH|DELETE)\b"
    r"|(^|[\s'\"])(--field|--raw-field|--input|--data[\w-]*|--json|--form[\w-]*|--upload-file|-Body|-InFile|-Form)"
    r"(\s|=|$)"
    r"|\.(put|post|patch|delete)\(|\brequest\(\s*(['\"](PUT|POST|PATCH|DELETE)\b|[a-z_])"
    r"|\bmethod(-override)?['\"]?\s*[=:]\s*(['\"]?(PUT|POST|PATCH|DELETE)\b|(?!(GET|HEAD)\b)[a-z_])"
    r"|[(,.]\s*data\s*=(?!=)", re.IGNORECASE)
# urllib's `urlopen(url, body)` / `Request(url, body)`: a second positional argument is the request body.
URLLIB_CALL = re.compile(r"\b(urlopen|Request)\s*\(")
URLLIB_ALIAS = re.compile(r"\b(urlopen|Request)\s+as\b|=\s*[\w.]*\b(urlopen|Request)\b(?!\s*\()"
                          r"|[(,]\s*[\w.]*\b(urlopen|Request)\s*[,)]")
# gh's -f / -F field flags, alone or with the field attached (`-fmessage=x`), curl's -d / -F / -T, attached
# (`-d@f`, `-Tfile`) or after a cluster of curl's flags (`-sd@f`, `-sSLT f`, `-OJd@f`, `-sXPUT`). A cluster ends at
# curl's first value-taking letter, which takes the rest of the word, so every other letter or digit counts as a flag.
# Case-sensitive, so PowerShell's `-Force` / `-TimeoutSec` / `-Debug` / `-Headers` are not read as one.
CURL_VALUE_SHORT = "AbcCDeEHKmoPQrtuUwxyz"
CURL_FLAG = re.escape("".join(c for c in string.ascii_letters + string.digits + "#:"
                              if c not in CURL_VALUE_SHORT + "dFTX"))
SHORT_WRITE_FLAG = re.compile(r"(^|[\s'\"])(-[fFdT](\s|=|$)|-[fF][\w.\[\]-]+=|-d[@'\"{\w]|-T(?!imeoutSec)\S"
                              rf"|-[{CURL_FLAG}]+[dFT]|-[{CURL_FLAG}]*X[\s=:]*(PUT|POST|PATCH|DELETE)\b)")
# What the shell strips before a program sees its words: quotes, `\` and PowerShell's backtick escapes, so `'-X' 'PUT'`
# and `-X P"U"T` read as the `-X PUT` they are.
SHELL_QUOTING = re.compile(r"['\"\\`]")
LINE_CONTINUATION = re.compile(r"[\\`]\r?\n")
# `-c` / `git config` keys that change what a git subcommand means or where config is read from.
CONFIG_REDIRECT_KEY = re.compile(r"(alias|include|includeif)\.", re.IGNORECASE)
CONFIG_REDIRECT_SECTION = re.compile(r"(alias|include|includeif)(\.|$)", re.IGNORECASE)
# `git config`'s long options (git 2.54 `-h`, plus the older single-action forms), so the unique-prefix abbreviations
# git accepts (`--fil` is `--file`, `--ed` is `--edit`) are read as git reads them. A `no-` form is listed only where
# git lists `--[no-]x`.
_CONFIG_NEGATABLE = {"all", "append", "blob", "comment", "default", "file", "fixed-value", "global", "includes",
                     "local", "name-only", "null", "regexp", "show-names", "show-origin", "show-scope", "system", "type",
                     "url", "value", "worktree"}
CONFIG_LONG_OPTS = frozenset(
    {f"--{o}" for o in _CONFIG_NEGATABLE} | {f"--no-{o}" for o in _CONFIG_NEGATABLE}
    | {f"--{o}" for o in ("get", "get-all", "get-regexp", "get-urlmatch", "replace-all", "add", "unset", "unset-all",
                          "rename-section", "remove-section", "list", "edit", "get-color", "get-colorbool", "bool",
                          "int", "bool-or-int", "bool-or-str", "path", "expiry-date")})
# `git config` options that take the next word as their value, so that word is never the key or a subcommand.
CONFIG_VALUE_OPTS = {"--file", "--blob", "--comment", "--type", "--default", "--value", "--url"}
CONFIG_VALUE_SHORT = {"f": "--file", "t": "--type"}  # the rest of the word, or else the next word, is the value
CONFIG_SHORT_FLAGS = {"e": "--edit", "l": "--list", "z": "--null"}
CONFIG_READ_SUBS = {"get", "list", "unset", "remove-section", "get-color", "get-colorbool"}
CONFIG_SUBS = CONFIG_READ_SUBS | {"set", "rename-section", "edit"}
CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "--unset",
                     "--unset-all", "--remove-section", "--get-color", "--get-colorbool"}
GIT_CONFIG_ENV = re.compile(r"GIT_CONFIG_(PARAMETERS|KEY_\d+|VALUE_\d+|COUNT|GLOBAL|SYSTEM)\b", re.IGNORECASE)
# The stem with the rest built at run time (`GIT_CONFIG_${s}`, `GIT_CONFIG_KEY_$i`): treated as naming one.
GIT_CONFIG_FRAGMENT = re.compile(r"GIT_CONFIG(_?\s*[$%{(+]|_(KEY|VALUE)_\s*[$%{(+])", re.IGNORECASE)
# Setting one: `NAME=`, `$env:NAME =`, `"NAME": ` / `"NAME", ` (a mapping or SetEnvironmentVariable), or the env drive.
GIT_CONFIG_SET = re.compile(r"GIT_CONFIG_(PARAMETERS|KEY_\d+|VALUE_\d+|COUNT|GLOBAL|SYSTEM)['\"]?\]?\s*[=:,]"
                            r"|\benv:GIT_CONFIG_(PARAMETERS|KEY_\d+|VALUE_\d+|COUNT|GLOBAL|SYSTEM)\b", re.IGNORECASE)
# `printf -v`, `read`, `declare`, `Set-Item Env:/…` and the like set GIT_CONFIG_* with no `NAME=`, and git may run
# under any path or through another program, so naming one at all is refused unless the command only searches text.
# Programs that only search or page text, so a command made of nothing else names GIT_CONFIG_* without setting it.
SEARCH_PROGRAMS = {"grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sls", "head", "tail", "wc"}
GIT_REDIRECT_ENV = re.compile(r"\bGIT_(DIR|WORK_TREE|COMMON_DIR)\b")
GIT_REDIRECT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")
GH_TARGET_ENV = re.compile(r"\bGH_(REPO|HOST)\b")
GIT_SHIP_LINE = re.compile(r"\bgit(\.exe)?\b.*\b(push|commit|config)\b", re.IGNORECASE)
GIT_OR_GH_WORD = re.compile(r"\b(git|gh)(\.exe)?\b", re.IGNORECASE)
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
BACKTICKS = re.compile(r"`([^`]*)`")
DRIVE_WORD = re.compile(r"[A-Za-z]:")
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
LAUNCHERS = {"start-process", "saps", "start", "start.exe"}  # start a program with arguments the guard reads as text
CMDS = {"cmd", "cmd.exe"}
# Commands that run the rest of their words as a command, each with its own options that take the next word as a value.
WRAPPERS = {
    "command": set(), "exec": set(), "builtin": set(), "nohup": set(), "chronic": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-r", "--role", "-t", "--type",
             "-U", "--other-user", "-C", "--close-from", "-T", "--command-timeout"},
    "doas": {"-u", "-C"},
    "xargs": {"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "-n", "--max-args", "-P", "--max-procs",
              "-s", "--max-chars"},
    "env": {"-u", "--unset"},
}
# Shell keywords that start a simple command (`if git ...; then git commit ...; fi`): the command is the word after.
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{"}
WRAPPER_CHDIR = {"env": {"-C", "--chdir"}, "sudo": {"-D", "--chdir"}}
WRAPPER_SPLIT = {"env": {"-S", "--split-string"}}
# Commands that print or search text without running it: a `git push` among their words is data.
NON_EXEC = {"echo", "printf", "write-host", "write-output", "write-error", "write-verbose", "grep", "egrep", "fgrep",
            "rg", "findstr", "select-string", "sls", "gh", "cat", "type", "get-content", "gc", "head", "tail", "less",
            "more", "man"}
# Commands that read a here-document without running it as commands (a commit message, a file being written).
HEREDOC_READERS = NON_EXEC | {"git", "git.exe", "tee", ":", "set-content", "out-file", "add-content"}
# `gh api` / curl / Invoke-RestMethod options whose value is a `key=value` field, or a JSON request body.
FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field"}
JSON_BODY_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--json", "-body"}
# powershell / pwsh parameters that take the next word as a value (full names match on any 3+ letter prefix).
PS_VALUE_SHORT = {"ep", "ex", "w", "o", "of", "if", "v", "wd"}
PS_VALUE_LONG = ("executionpolicy", "windowstyle", "outputformat", "inputformat", "version", "configurationname",
                 "configurationfile", "settingsfile", "custompipename", "psconsolefile", "workingdirectory")
EXPRESSION_RUNNERS = {"iex", "invoke-expression"}
DIRECT_WRITE_TOOLS = re.compile(r"(push_files|create_or_update_file|delete_file)$")

Runner = Callable[[list, Optional[str]], "subprocess.CompletedProcess[str]"]


class Refuse(Exception):
    """The call is refused; the message says why and what to do."""


@dataclass(frozen=True)
class Target:
    """One PR a call would merge: a selector `gh pr view` accepts, the repository when it is not the directory's, the
    head SHA the call pins (None when it pins none), and the directory the call runs in (`why_unknown` says why the
    guard cannot know it; empty with directory None means the session's own directory)."""
    selector: Optional[str]
    repo: Optional[str]
    head: Optional[str] = None
    directory: Optional[str] = None
    why_unknown: str = ""

    def describe(self) -> str:
        what = self.selector or "the current branch's PR"
        return f"{what} in {self.repo}" if self.repo else what


@dataclass(frozen=True)
class Push:
    """One `git push` a command would run, and the directory it runs in (None when the guard cannot know it)."""
    directory: Optional[str]
    why_unknown: str = ""


@dataclass
class Scan:
    """What one shell command would push and merge."""
    pushes: list = field(default_factory=list)
    merges: list = field(default_factory=list)
    decoded: list = field(default_factory=list)  # command text decoded from -EncodedCommand, checked like the command


def _tokens(command: str, powershell: bool = False) -> list:
    """The command's words and operators; a line break separates commands, as `;` does (a continued line does not)."""
    command = command.replace("\r\n", "\n").replace("`\n" if powershell else "\\\n", " ")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    if powershell:
        lexer.escape = ""  # backslashes are path separators in PowerShell and cmd; PowerShell's escape is the backtick
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


def _base(word: str) -> str:
    """The command name of a word, without its directory (either separator, on any OS), lower case."""
    return re.split(r"[\\/]", word)[-1].lower()


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
    selector, repo, head, i = None, None, None, 0
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
            elif flag == "--match-head-commit":
                head = value or None
        elif not arg.startswith("-") and selector is None:
            selector = arg
        i += 1
    return Target(selector, repo, head)


def _rest_sha(segment: list) -> Optional[str]:
    """The `sha` a REST merge call sends: a `-f sha=...` field, or the `sha` key of a JSON body. None when it sends none,
    or more than one value, or a body the guard cannot read."""
    found = set()
    for k, word in enumerate(segment):
        flag, eq, value = word.partition("=")
        if word[:2] in ("-f", "-F", "-d") and len(word) > 2 and word[2] != "=" and not word.startswith("--"):
            flag, eq, value = word[:2], "=", word[2:]  # `-fsha=...`: the value is attached
        if flag in FIELD_FLAGS or flag in JSON_BODY_FLAGS or flag.lower() in JSON_BODY_FLAGS:
            if not eq:
                value = segment[k + 1] if k + 1 < len(segment) else ""
            if flag in FIELD_FLAGS:
                key, _, field_value = value.partition("=")
                if key == "sha":
                    found.add(field_value)
                continue
            try:
                body = json.loads(value)
            except ValueError:
                continue
            if isinstance(body, dict) and "sha" in body:
                found.add(str(body["sha"]))
    return found.pop() if len(found) == 1 else None


def _record_merges(segment: list, here: Optional[str], why: str, scan: Scan) -> None:
    """Add every merge one simple command would run, with the directory it runs in."""
    text = " ".join(segment)
    for owner, name, number in REST_MERGE.findall(text):
        repo = None if (owner, name) == ("{owner}", "{repo}") else f"{owner}/{name}"
        scan.merges.append(Target(number, repo, _rest_sha(segment), here, why))
    for args in _gh_pr_merge_args(segment):
        target = _target_from_args(args)
        if target is not None:
            scan.merges.append(replace(target, directory=here, why_unknown=why))


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
    return [Target(str(number), f"{owner}/{repo}", tool_input.get("sha") or None)]


def _run(argv: list, cwd: Optional[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, cwd=cwd or None, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=25)


def not_green(rollup: list) -> list:
    """Why the head is not green: one line per check that is not, or the empty list when every check is green."""
    if not rollup:
        return ["no checks reported on the head commit"]
    problems, ran = [], False
    for item in rollup:
        if item.get("__typename") == "StatusContext" or "context" in item:
            name, state = item.get("context") or "status", (item.get("state") or "").upper()
            if state != "SUCCESS":
                problems.append(f"{name}: {state.lower() or 'unknown'}")
            else:
                ran = True
            continue
        name = item.get("name") or item.get("workflowName") or "check"
        status, conclusion = (item.get("status") or "").upper(), (item.get("conclusion") or "").upper()
        if status != "COMPLETED":
            problems.append(f"{name}: {status.lower() or 'unknown'}")
        elif conclusion not in GREEN_CONCLUSIONS:
            problems.append(f"{name}: {conclusion.lower() or 'no conclusion'}")
        elif conclusion == "SUCCESS":
            ran = True
    if not problems and not ran:
        return ["no check ran on the head commit: every check is skipped or neutral"]
    return problems


def head_check_runs(pr_url: str, head: str, cwd: Optional[str], run: Runner) -> list:
    """The head commit's own check runs, shaped like rollup items. A workflow_dispatch run on the head reports here and
    not in the PR's statusCheckRollup: without these, a dispatched gate on the head would not count and a red one would
    not refuse."""
    match = PR_URL.match(pr_url)
    if not match or not re.fullmatch(r"[0-9a-fA-F]{7,64}", head):
        raise Refuse(f"could not read the head's check runs: no repository in {pr_url or 'the PR URL'!r} or no head "
                     f"commit ({head or 'none'}).")
    host, owner, name = match.groups()
    # The host is always named, so a GH_HOST in the session's environment cannot point the read at another server.
    argv = ["gh", "api", f"repos/{owner}/{name}/commits/{head}/check-runs?per_page=100", "--hostname", host]
    try:
        proc = run(argv, cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refuse(f"could not read the check runs of {pr_url} at {head[:12]} ({exc}).") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        raise Refuse(f"could not read the check runs of {pr_url} at {head[:12]}: {detail[0]}")
    try:
        data = json.loads(proc.stdout)
        runs = data.get("check_runs")
        if not isinstance(runs, list) or not all(isinstance(item, dict) for item in runs):
            raise TypeError("check_runs is not a list of objects")
        total = int(data.get("total_count", len(runs)))
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
        raise Refuse(f"the check runs of {pr_url} at {head[:12]} came back unreadable.") from exc
    if total > len(runs):
        raise Refuse(f"{pr_url} has {total} check runs at {head[:12]}, more than the guard reads in one page.")
    return [{"__typename": "CheckRun", "name": item.get("name") or "check", "status": item.get("status") or "",
             "conclusion": item.get("conclusion") or ""} for item in runs]


def check_target(target: Target, cwd: Optional[str], run: Runner = _run) -> None:
    """Return when the PR's head is green and the merge pins that head; raise Refuse otherwise."""
    by_url = bool(target.selector and re.match(r"https?://", target.selector))
    if target.why_unknown and not (target.repo or by_url):
        raise Refuse(f"{target.why_unknown}, so the guard cannot tell which repository's PR this merges. Name it: "
                     "gh pr merge <number> --repo OWNER/REPO --match-head-commit <sha>")
    where = cwd if target.why_unknown or target.directory is None else target.directory
    argv = ["gh", "pr", "view"] + ([target.selector] if target.selector else [])
    argv += ["--repo", target.repo] if target.repo else []
    argv += ["--json", "number,url,state,headRefOid,statusCheckRollup"]
    try:
        proc = run(argv, where)
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
    head = pr.get("headRefOid") or ""
    shown = pr.get("url") or target.describe()
    checks = (pr.get("statusCheckRollup") or []) + head_check_runs(pr.get("url") or "", head, where, run)
    problems = list(dict.fromkeys(not_green(checks)))
    if problems:
        listed = "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else "")
        raise Refuse(f"{shown} is not green at head {head[:12]}: {listed}. Fix the code and push again; a known or "
                     "flaky failure is fixed too, not merged over.")
    if not target.head:
        raise Refuse(f"{shown} is green at head {head}, but the merge does not pin it, so a commit pushed after this "
                     f"check would merge unchecked. Add `--match-head-commit {head}` (the REST call and the MCP tool "
                     f"take `sha={head}`).")
    if target.head.lower() != head.lower():
        raise Refuse(f"{shown}: the merge pins {target.head}, but the head the checks ran on is {head}. Pin the full "
                     f"head: --match-head-commit {head}")


# ------------------------------------------------------------------------------------------------- pushes, commits --

def _native(path: str) -> str:
    """A Git Bash drive path (`/<letter>/x`) as the Windows path it names; `~` expanded. Other paths unchanged."""
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


def _cd(words: list, here: Optional[str], why: str, powershell: bool, cmd: bool) -> tuple:
    """The directory after one `cd`-like command, and why it is unknown (or the old reason, when it is known)."""
    name, args = _base(words[0]), words[1:]
    switch_drive = cmd and any(a.lower() == "/d" for a in args)
    if cmd:
        args = [a for a in args if a.lower() != "/d"]
    if "-" in args:
        return None, f"`{words[0]} -` returns to a directory the guard does not track"
    path_flag = [args[k + 1] for k, a in enumerate(args[:-1]) if a.lower() in ("-path", "-literalpath")]
    rest = [a for a in args if not a.startswith("-")]
    target = (path_flag or rest or [None])[0]
    if target is None:
        if cmd and name in ("cd", "chdir"):
            return here, why  # a bare `cd` in cmd prints the directory
        if powershell or name in ("pushd", "push-location"):
            return None, f"`{words[0]}` with no path goes somewhere the guard does not track"
        target = "~"
    joined = _join(here, target)
    if joined is None:
        return None, f"`{words[0]} {target}` changes to a directory the guard cannot resolve"
    if cmd and name in ("cd", "chdir") and not switch_drive and here and \
            os.path.splitdrive(joined)[0].lower() != os.path.splitdrive(here)[0].lower():
        return here, why  # cmd's `cd` without /d does not change the current drive
    return joined, why


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


def _config_long_opt(word: str) -> Optional[str]:
    """The `git config` long option `word` names, exactly or as git's unique prefix; None if unknown or ambiguous."""
    name = word.split("=")[0].lower()
    if name in CONFIG_LONG_OPTS:
        return name
    hits = [o for o in CONFIG_LONG_OPTS if o.startswith(name)]
    return hits[0] if len(hits) == 1 else None


def _urllib_body(text: str) -> bool:
    """True when a `urlopen(...)` / `Request(...)` call in the text has a second positional argument, its body, or
    when either is renamed (`import ... as`, `x = urlopen`, passed as a value), since the renamed call goes unseen."""
    if URLLIB_ALIAS.search(text):
        return True
    for match in URLLIB_CALL.finditer(text):
        depth, quote, escaped, args, current = 0, "", False, [], ""
        for ch in text[match.end():]:
            if escaped:
                escaped = False
            elif quote:
                escaped = ch == "\\"
                quote = "" if ch == quote else quote
            elif ch in "'\"":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                args.append(current)
                current = ""
                continue
            current += ch
        args.append(current)
        if len(args) > 1 and args[1].strip() and not re.match(r"\s*\w+\s*=(?!=)", args[1]):
            return True
    return False


def _only_searches(command: str) -> bool:
    """True when the command is nothing but text searches piped together, so a GIT_CONFIG_* in it is a pattern.
    Any separator, substitution, redirect, group or variable fails it, and so do the options that make a search run
    another program: rg's `--pre` and git grep's `-O` / `--open-files-in-pager`."""
    if re.search(r"[;&\r\n`<>(){}$]", command):
        return False
    for piece in command.split("|"):
        words = SHELL_QUOTING.sub("", piece).split()
        if not words:
            return False
        first = words[0].lower()
        git_grep = first in ("git", "git.exe") and words[1:2] == ["grep"]
        if not (first in SEARCH_PROGRAMS or git_grep):
            return False
        if first == "rg" and any(w.startswith("--pre") for w in words):
            return False
        if git_grep and any(w.startswith("--op") or re.match(r"-[^-]*O", w) for w in words):
            return False
    return True


def _config_sets_redirect(args: list) -> bool:
    """True when `git config <args>` would set an alias.*, include.* or includeIf.* key, a key the guard cannot read,
    or anything through `edit` / `rename-section` into those sections. Reads and unsets are allowed.

    The arguments are parsed as git does: a long option may be any unique prefix of its name, a value-taking option
    consumes the next word (or the rest of a short-option cluster), and a subcommand is only the first word that is not
    an option, so `git config --fil get alias.x push` and `git config -zt bool alias.x push` are the writes they are.
    An option the guard does not know fails closed when any word could name one of those sections."""
    flags, positional, unknown, i = set(), [], False, 0
    while i < len(args):
        word = args[i]
        if word == "--":
            positional.extend(args[i + 1:])
            break
        if word.startswith("--"):
            opt = _config_long_opt(word)
            if opt is None:
                unknown = True  # git refuses an unknown or ambiguous option, unless it is newer than this table
            else:
                flags.add(opt)
                if opt in CONFIG_VALUE_OPTS and "=" not in word:
                    i += 1
        elif word.startswith("-") and len(word) > 1:
            for k, ch in enumerate(word[1:], 1):
                if ch in CONFIG_VALUE_SHORT:
                    flags.add(CONFIG_VALUE_SHORT[ch])
                    if k == len(word) - 1:
                        i += 1
                    break
                if ch not in CONFIG_SHORT_FLAGS:
                    unknown = True
                flags.add(CONFIG_SHORT_FLAGS.get(ch, f"-{ch}"))
        else:
            positional.append(word)
        i += 1
    if unknown and any(CONFIG_REDIRECT_SECTION.match(a) or any(ch in a for ch in "$`%") for a in positional):
        return True
    sub = positional[0].lower() if positional and positional[0].lower() in CONFIG_SUBS else None
    rest = positional[1:] if sub else positional
    if sub == "edit" or "--edit" in flags:
        return True  # an editor can write any key
    if sub == "rename-section" or "--rename-section" in flags:
        return any(CONFIG_REDIRECT_SECTION.match(a) or any(ch in a for ch in "$`%") for a in rest)
    if sub in CONFIG_READ_SUBS or flags & CONFIG_READ_FLAGS or not rest:
        return False
    key, values = rest[0], rest[1:]
    if not values and sub != "set":
        return False  # `git config alias.x` with no value is a read
    return bool(CONFIG_REDIRECT_KEY.match(key)) or any(ch in key for ch in "$`%")


def _git_call(words: list, cwd: Optional[str], why: str, pushes: list) -> None:
    """Check one `git ...` simple command: refuse hook-skipping forms; record a push with the directory it runs in."""
    i, directory, unknown = 1, cwd, why
    while i < len(words) and words[i].startswith("-"):
        opt, eq, value = words[i].partition("=")
        if opt in GIT_VALUE_OPTS and not eq:
            i += 1
            value = words[i] if i < len(words) else ""
        if opt in ("-c", "--config-env"):
            key = value.split("=")[0]
            if key.lower() == "core.hookspath":
                raise Refuse(f"`git {opt} core.hooksPath=...` replaces the repository's gate hooks. Run git without it.")
            if CONFIG_REDIRECT_KEY.match(key):
                raise Refuse(f"`git {opt} {key}=...` can turn any subcommand into a push the guard cannot see (an alias) "
                             "or read config it cannot check (an include). Run the git command itself.")
            if any(ch in key for ch in "$`%"):
                raise Refuse(f"`git {opt} {value}` sets a key the guard cannot read, which could be core.hooksPath. "
                             "Name the key literally.")
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
        if _config_sets_redirect(args):
            raise Refuse("`git config alias.* / include.* / includeIf.*` (or a key the guard cannot read, `edit`, or a "
                         "`rename-section` into them) can turn any subcommand into a push the guard cannot see, or pull "
                         "in config it cannot check. A session does not set them.")


def _unwrap(words: list, here: Optional[str], why: str) -> tuple:
    """The command a simple command runs, past `NAME=value` assignments, shell KEYWORDS and WRAPPERS.

    Returns (words, directory, why_unknown, the name of every wrapper passed, the `env -S` strings it runs)."""
    via, split = set(), []
    while words:
        if ASSIGNMENT.match(words[0]) or words[0] in KEYWORDS:
            words = words[1:]
            continue
        name = _base(words[0])
        if name not in WRAPPERS:
            break
        via.add(name)
        chdir, splits = WRAPPER_CHDIR.get(name, set()), WRAPPER_SPLIT.get(name, set())
        k = 1
        while k < len(words) and words[k].startswith("-") and words[k] != "-":
            if words[k] == "--":
                k += 1
                break
            opt, eq, value = words[k].partition("=")
            if (opt in WRAPPERS[name] or opt in chdir or opt in splits) and not eq:
                k += 1
                value = words[k] if k < len(words) else ""
            if opt in chdir:
                here, why = None, f"`{name} {opt}` changes to a directory the guard does not follow"
            if opt in splits:
                split.append(value)
            k += 1
        if name == "timeout" and k < len(words):
            k += 1  # the duration
        words = words[k:]
    return words, here, why, via, split


def _mentions_ship(text: str) -> bool:
    """True when the text holds anything the guard checks: a git push / commit / config, or a merge."""
    return any(GIT_SHIP_LINE.search(line) for line in text.splitlines()) or bool(REST_MERGE.search(text)) or \
        bool(GRAPHQL_MERGE.search(text)) or bool(re.search(r"\bpr\b", text) and re.search(r"\bmerge\b", text))


def _substitutions(command: str, powershell: bool) -> list:
    """The text inside each outermost `$(...)`, found by counting parentheses (quoted ones do not count), so a nested
    `$( (...) )` is found whole; an unclosed one yields the rest of the command, which the caller then checks."""
    found, i = [], 0
    while True:
        start = command.find("$(", i)
        if start < 0:
            return found
        depth, k, quote = 1, start + 2, ""
        while k < len(command) and depth:
            ch = command[k]
            if quote:
                if ch == quote:
                    quote = ""
                elif ch == "\\" and quote == '"' and not powershell:
                    k += 1
            elif ch in "'\"":
                quote = ch
            elif ch == "\\" and not powershell:
                k += 1
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            k += 1
        found.append(command[start + 2:k - 1] if depth == 0 else command[start + 2:])
        i = k


def _heredoc_openers(line: str) -> list:
    """Each here-document a bash line opens, as (delimiter, the simple command before its `<<`). Only a `<<` bash reads
    counts: not one inside quotes, a comment or `((...))` arithmetic, and not a `<<<` here-string."""
    found, quote, k, seg = [], "", 0, 0
    while k < len(line):
        ch = line[k]
        if quote:
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"':
                k += 1
        elif ch == "\\":
            k += 1
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (k == 0 or line[k - 1] in " \t;&|()"):
            break
        elif ch in ";&|()":
            if line.startswith("((", k):
                depth, j = 0, k
                while j < len(line):
                    depth += {"(": 1, ")": -1}.get(line[j], 0)
                    if depth == 0:
                        break
                    j += 1
                k = j
            seg = k + 1
        elif line.startswith("<<<", k):
            k += 2
        elif line.startswith("<<", k):
            j = k + 2 + (line[k + 2:k + 3] == "-")
            while j < len(line) and line[j] in " \t":
                j += 1
            word, q = [], ""
            while j < len(line) and (q or line[j] not in " \t;&|()<>"):
                c = line[j]
                if q:
                    if c == q:
                        q = ""
                    else:
                        word.append(c)
                elif c in "'\"":
                    q = c
                elif c == "\\" and j + 1 < len(line):
                    j += 1
                    word.append(line[j])
                else:
                    word.append(c)
                j += 1
            if word:
                found.append(("".join(word), line[seg:k]))
            k = j - 1
        k += 1
    return found


def _heredoc_reader(before: str) -> str:
    """The command name that reads a here-document, from the simple command written before its `<<`."""
    try:
        words = [w for w in _tokens(before) if not (ASSIGNMENT.match(w) or w in KEYWORDS)]
    except ValueError:
        return ""
    while words and _base(words[0]) in WRAPPERS:
        words = words[1:]
    return _base(words[0]) if words else ""


def _split_heredocs(command: str) -> tuple:
    """The command without its here-document bodies; the bodies a shell reads (those run as commands); and the command
    with only those shell-read bodies taken out, the text a command reading its input can still be fed.

    A body fed to a command that is not known to only read it (python, node, a shell found in a variable, ...) and
    that holds a git push / commit / merge is refused: whatever reads it could run it."""
    lines = command.replace("\r\n", "\n").split("\n")
    kept, unread, shell_bodies, i = [], [], [], 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        unread.append(line)
        i += 1
        for delimiter, before in _heredoc_openers(line):
            body = []
            while i < len(lines) and lines[i].strip() != delimiter:
                body.append(lines[i])
                i += 1
            i += 1  # the terminator
            text = "\n".join(body)
            # The body goes to the command before `<<`, and through a `|` on to the commands after it; an opener line
            # ending in `|`, `&&` or `||` continues on the line after the terminator, which reads it too.
            reach = line
            if re.search(r"(\|\|?|&&)\s*$", line) and i < len(lines):
                reach += "\n" + lines[i]
            readers = [_heredoc_reader(before)] + [r for r in (_heredoc_reader(after) for after in
                                                   re.findall(r"\|&?\s*([^|;&()\n]*)", reach)) if r]
            if any(_base(w) in SHELLS_C | POWERSHELLS | CMDS for w in re.split(r"[\s|;&()]+", reach) if w):
                shell_bodies.append(text)
                continue
            unread.append(text)
            if any(r not in HEREDOC_READERS for r in readers) and _mentions_ship(text):
                reader = next((r for r in readers if r not in HEREDOC_READERS), "") or "its reader"
                raise Refuse(f"a here-document fed to `{reader}` holds a git push / commit / merge the guard cannot "
                             "check, and that command could run it. Run git or gh directly.")
    return "\n".join(kept), shell_bodies, "\n".join(unread)


def _ps_option(word: str) -> Optional[str]:
    """The lower-case parameter name of a powershell / pwsh command-line word (`-`, `--` or `/`), None for an operand."""
    if word.startswith("--") and len(word) > 2:
        return word[2:].lower()
    if word.startswith(("-", "/")) and len(word) > 1:
        return word[1:].lower()
    return None


def _is_encoded_option(name: str) -> bool:
    """True for a powershell parameter name (from `_ps_option`) that is -EncodedCommand or one of its abbreviations."""
    return name in ("e", "ec") or (name.startswith("en") and "encodedcommand".startswith(name))


def _check_launched_encoded(words: list) -> None:
    """Refuse an encoded command among `words` that the guard does not otherwise follow, when it cannot be decoded or
    holds a push / commit / merge. Under a launcher (Start-Process, saps, cmd's `start`) every encoded option is
    checked, whatever executable it starts, since a copied or renamed powershell has any name; elsewhere one after a
    powershell word, or after a `$variable`, `(expression)` or `(expression).Member` (left as `.Member` once the
    expression is split out) that may name one. Values are read past the quotes and commas of
    `-ArgumentList '-EncodedCommand','<base64>'`."""
    items = [i for w in words for i in re.split(r"[,\s]+", w) if i.strip("'\"")]
    items = [i.strip("'\"") for i in items]
    launcher = bool(items) and _base(items[0]) in LAUNCHERS
    for j, item in enumerate(items):
        if not ((j == 0 and launcher) or _base(item) in POWERSHELLS or item.startswith(("$", "(", "."))):
            continue
        for k in range(j + 1, len(items)):
            name = _ps_option(items[k])
            if name is None or not _is_encoded_option(name):
                continue
            value = items[k + 1] if k + 1 < len(items) else ""
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-16-le")
            except (ValueError, UnicodeDecodeError) as exc:
                raise Refuse(f"`{words[0]}` starts a powershell with an encoded command the guard cannot decode, so it "
                             "cannot check it. Run the decoded command directly.") from exc
            _check_text(decoded)
            if _mentions_ship(decoded) or MAYBE_ENCODED.search(decoded):
                raise Refuse(f"`{words[0]}` starts a powershell whose encoded command holds a git push / commit / merge "
                             "the guard does not follow. Run git or gh directly.")


def _powershell_command(words: list, here: Optional[str], why: str) -> tuple:
    """What `powershell` / `pwsh <words>` runs: (the command text, or "" for a script file, or None when it reads its
    commands from its input; the directory it runs in; why that is unknown). -Command / -c, -CommandWithArgs / -cwa,
    -EncodedCommand (decoded) and Windows PowerShell's positional command are all read; -WorkingDirectory is followed."""
    k = 1
    while k < len(words):
        name = _ps_option(words[k])
        if name is None:
            return " ".join(words[k:]), here, why  # Windows PowerShell runs a positional operand as -Command
        value = words[k + 1] if k + 1 < len(words) else ""
        if name == "cwa" or (name.startswith("c") and ("command".startswith(name) or "commandwithargs".startswith(name))):
            rest = " ".join(words[k + 1:])
            return (None if rest in ("", "-") else rest), here, why
        if _is_encoded_option(name):
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-16-le")
            except (ValueError, UnicodeDecodeError) as exc:
                raise Refuse(f"`{words[0]} -EncodedCommand` carries a value the guard cannot decode, so it cannot "
                             "check it.") from exc
            _check_text(decoded)
            return decoded, here, why
        if name == "f" or (len(name) >= 2 and "file".startswith(name)):
            return "", here, why
        if name == "wd" or (len(name) >= 2 and "workingdirectory".startswith(name)):
            here = _join(here, value)
            why = why if here else f"`{words[0]} -WorkingDirectory {value}` names a directory the guard cannot resolve"
        if name in PS_VALUE_SHORT or any(len(name) >= 3 and full.startswith(name) for full in PS_VALUE_LONG):
            k += 1
        k += 1
    return None, here, why


def scan_shell(command: str, cwd: Optional[str], powershell: bool, scan: Scan, depth: int = 0,
               cmd: bool = False) -> None:
    """Walk one shell command for pushes, merges and hook-skipping forms; nested shells and substitutions too."""
    if depth > 4:
        raise Refuse("the command nests shells too deeply for the guard to follow.")
    nested = _substitutions(command, powershell)
    # Checks for a command that reads its input look at `original`: here-document bodies stay in it (whatever reads that
    # input still gets them) except those already scanned below as the commands a shell runs.
    original = command
    if not powershell:
        command, shell_bodies, original = _split_heredocs(command)
        nested += shell_bodies + BACKTICKS.findall(command)
    for inner in nested:
        scan_shell(inner, cwd, powershell, scan, depth + 1)
    try:
        tokens = _tokens(command, powershell)
    except ValueError as exc:
        if _mentions_ship(original):
            raise Refuse(f"could not parse the command to check its git push / commit / merge ({exc}). Run that "
                         "command on its own.") from exc
        return
    reads_input = (f"`{{}}` reads its commands from its input here, where the guard cannot check the git push / commit "
                   "in them. Run git directly.")
    here, why = cwd, ""
    for segment in _segments(tokens):
        words, here, why, via, split = _unwrap(segment, here, why)
        for inner in split:
            scan_shell(inner, here, powershell, scan, depth + 1)
        _record_merges(segment, here, why, scan)
        if not words or words[0].startswith("#"):
            continue
        name = _base(words[0])
        # xargs adds words from its input: git / gh under it, or anything that runs them (a shell, python) fed from a
        # command that mentions a push or merge, is out of the guard's sight.
        if "xargs" in via and (GIT_OR_GH_WORD.search(" ".join(words)) or
                               (name not in NON_EXEC and _mentions_ship(original))):
            raise Refuse("`xargs` adds arguments from its input that the guard cannot see (a push, a --no-verify, a PR "
                         "number). Run git or gh directly.")
        if name not in POWERSHELLS | NON_EXEC:
            _check_launched_encoded(words)
        if powershell and len(words) == 1 and DRIVE_WORD.fullmatch(words[0]):
            here, why = None, f"`{words[0]}` switches to that drive's current directory, which the guard does not track"
        elif name in CD_COMMANDS:
            here, why = _cd(words, here, why, powershell, cmd)
        elif name in ("popd", "pop-location"):
            here, why = None, f"`{words[0]}` returns to a directory the guard does not track"
        elif name in ("git", "git.exe"):
            _git_call(words, here, why, scan.pushes)
        elif name in POWERSHELLS:
            inner, ps_here, ps_why = _powershell_command(words, here, why)
            if inner and inner not in " ".join(words):
                scan.decoded.append(inner)
            if inner:
                scan_shell(inner, ps_here, True, scan, depth + 1)
            elif inner is None and GIT_SHIP_LINE.search(original):
                raise Refuse(reads_input.format(words[0]))
        elif name in SHELLS_C or name in CMDS:
            is_cmd, ran = name in CMDS, False
            for k, w in enumerate(words[1:], 1):
                low = w.lower()
                if (not is_cmd and low.startswith("-") and not low.startswith("--") and "c" in low) or \
                   (is_cmd and low in ("/c", "/k")):
                    inner = words[k + 1] if not is_cmd and k + 1 < len(words) else " ".join(words[k + 1:])
                    if inner:
                        scan_shell(inner, here, is_cmd, scan, depth + 1, cmd=is_cmd)
                    ran = True
                    break
            operands = [w for w in words[1:] if not w.startswith(("-", "<", "/"))]
            if not ran and (is_cmd or not operands) and GIT_SHIP_LINE.search(original):
                raise Refuse(reads_input.format(words[0]))
        elif name in EXPRESSION_RUNNERS and GIT_SHIP_LINE.search(original):
            raise Refuse(f"`{words[0]}` runs a string the guard cannot follow, and this command holds a git push / "
                         "commit. Run git itself: git -C <dir> push ...")
        elif name not in NON_EXEC and GIT_SHIP_LINE.search(" ".join(words)):
            raise Refuse(f"`{words[0]}` runs a git push / commit / config the guard cannot follow. Run git itself: "
                         "git -C <dir> push ...")


def gate_is_on(directory: str, run: Runner = _run) -> Optional[str]:
    """None when a push from `directory` runs the repository's gate (or the repository ships none); else why not."""
    if not os.path.isdir(directory):
        return f"the push runs in {directory}, which does not exist"
    try:
        top = run(["git", "-C", directory, "rev-parse", "--show-toplevel"], None)
    except (OSError, subprocess.SubprocessError) as exc:
        return (f"could not ask git which repository {directory} is in ({type(exc).__name__}: {exc}), so the guard "
                "cannot tell whether the push runs the gate. Run the push again.")
    if top.returncode != 0:
        here = Path(directory).absolute()
        if not any((folder / ".git").exists() for folder in (here, *here.parents)):
            return None  # not in a repository: nothing of a gated repository to push
        detail = (top.stderr or top.stdout or "").strip().splitlines()[-1:] or [f"exit {top.returncode}"]
        return (f"{directory} is inside a git repository, but git could not report its top folder ({detail[0]}), so "
                "the guard cannot tell whether the push runs the gate.")
    root = Path(top.stdout.strip())
    hook = root / ".githooks" / "pre-push"
    try:
        if GATE_MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
            return None
    except (FileNotFoundError, NotADirectoryError):
        return None  # this repository ships no gate
    except OSError as exc:  # it is there and unreadable: "unknown", never "no gate"
        return (f"the gate file {hook} could not be read ({type(exc).__name__}: {exc}), so the guard cannot tell "
                "whether this repository ships a gate. Make the file readable, then run the push again.")
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


def scan_command(command: str, cwd: Optional[str], powershell: bool) -> Scan:
    """What a shell command would push and merge. Raises Refuse for a form the guard refuses outright."""
    scan = Scan()
    if not (MAYBE_MERGE.search(command) or MAYBE_SHIP.search(command) or MAYBE_ENCODED.search(command)
            or MAYBE_GH_WRITE.search(command)):
        return scan
    _check_text(command)
    scan_shell(command, cwd, powershell, scan)
    return scan


def _check_text(command: str) -> None:
    """Refuse the forms the command's text alone shows: a GraphQL merge, a bypass, GIT_CONFIG_* carrying hooksPath,
    a branch merge, and a GitHub write that puts code on a branch without a push."""
    if GRAPHQL_MERGE.search(command):
        raise Refuse("a GraphQL merge (mergePullRequest / enablePullRequestAutoMerge) names the PR by node id, which "
                     "this guard cannot check. Use `gh pr merge <number> --repo OWNER/REPO --match-head-commit <sha>` "
                     "once every check is green.")
    for var in BYPASS_VARS:
        if var in command:
            raise Refuse(f"{var} is the owner's audited bypass, typed in the owner's own terminal; a session does not "
                         "set it. Fix what the gate reports instead.")
    named_config_env = any(GIT_CONFIG_ENV.search(t) or GIT_CONFIG_FRAGMENT.search(t)
                           for t in (command, SHELL_QUOTING.sub("", command)))
    if named_config_env and "hookspath" in command.lower():
        raise Refuse("GIT_CONFIG_* carrying core.hooksPath replaces the repository's gate hooks. Run git without it.")
    if GIT_CONFIG_SET.search(command) or named_config_env and not _only_searches(command):
        raise Refuse("setting GIT_CONFIG_* (PARAMETERS / COUNT / KEY_n / VALUE_n / GLOBAL / SYSTEM), or naming one in "
                     "anything but a plain text search, injects git config the guard cannot check: an alias that "
                     "turns any subcommand into a push, an include, or core.hooksPath. Run git without it.")
    if REST_BRANCH_MERGE.search(command):
        raise Refuse("GitHub's branch merge (`repos/OWNER/REPO/merges`) puts commits on a branch with no PR and no "
                     "checks. Open a PR and merge it once every check is green.")
    if GRAPHQL_WRITE.search(command) and re.search(r"graphql", command, re.IGNORECASE):
        raise Refuse("a GraphQL branch merge, commit or ref write (mergeBranch / createCommitOnBranch / updateRef(s) / "
                     "createRef) puts code on a branch without a push, so no local gate runs. Commit in a clone and "
                     "`git push`.")
    joined = LINE_CONTINUATION.sub(" ", command)
    unquoted = SHELL_QUOTING.sub("", joined)
    if any(REST_WRITE_PATH.search(t) and (WRITE_REQUEST.search(t) or SHORT_WRITE_FLAG.search(t) or _urllib_body(t))
           for t in (joined, unquoted)):
        raise Refuse("a write to GitHub's contents or git-data API (`contents/`, `git/refs`, `git/commits`, "
                     "`git/trees`, `git/blobs`) puts code on a branch without a push, so no local gate runs. "
                     "Commit in a clone and `git push`.")


def check_pushes(command: str, pushes: list, environ: Mapping[str, str], run: Runner = _run) -> None:
    if not pushes:
        return
    for var in BYPASS_VARS:
        if environ.get(var):
            raise Refuse(f"{var} is set in this session's environment, so the gate would be skipped. It is the owner's "
                         "bypass for the owner's terminal; unset it in the session before pushing.")
    if GIT_CONFIG_ENV.search(command):
        raise Refuse("GIT_CONFIG_* in a command that pushes can redirect core.hooksPath where the guard cannot see it. "
                     "Push without it.")
    redirect = GIT_REDIRECT_ENV.search(command)
    named = redirect.group(0) if redirect else next((v for v in GIT_REDIRECT_VARS if environ.get(v)), None)
    if named:
        raise Refuse(f"{named} points git somewhere the guard does not follow, so it cannot check that the gate runs. "
                     "Use a literal directory instead: git -C <dir> push ...")
    for push in pushes:
        if push.directory is None:
            raise Refuse(f"{push.why_unknown}, so the guard cannot check that the gate runs. Use a literal directory: "
                         "git -C <dir> push ...")
        reason = gate_is_on(push.directory, run)
        if reason:
            raise Refuse(reason)


def check_merges(command: str, merges: list, cwd: Optional[str], run: Runner = _run) -> None:
    if merges and GH_TARGET_ENV.search(command):
        raise Refuse("GH_REPO / GH_HOST in a merging command choose the repository where the guard does not read it. "
                     "Name it with --repo OWNER/REPO instead.")
    for target in merges:
        check_target(target, cwd, run)


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
            for target in targets_in_mcp(tool_name, tool_input):
                check_target(target, cwd, run)
        elif tool_name in SHELL_TOOLS:
            command = tool_input.get("command")
            if not isinstance(command, str):
                return f"{tool_name}: the payload has no command string, so it cannot be checked."
            scan = scan_command(command, cwd, tool_name == "PowerShell")
            seen = "\n".join([command, *scan.decoded])
            check_pushes(seen, scan.pushes, environ, run)
            check_merges(seen, scan.merges, cwd, run)
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
