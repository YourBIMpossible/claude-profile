#!/usr/bin/env python3
"""Fail when a tracked file contains an absolute, machine-specific path.

A committed absolute path (a drive-letter path, a Git-Bash drive path, a user-home
path, a network share) only works on the machine that wrote it; every other clone
gets a broken default, a dead link or a silently wrong instruction. This guard
keeps such paths out of a repository. It is self-contained (Python 3.9+, standard
library only) so any repo can adopt it as-is: copy this file, optionally add an
allowlist, and run it in CI and locally.

Usage:
  python check_machine_paths.py                 # scan the git repo containing the cwd
  python check_machine_paths.py --root DIR      # scan another repo
  python check_machine_paths.py --self-test     # run the built-in rule cases

What is scanned: every file ``git ls-files`` lists (tracked or staged), read from the
working tree; a symlink is scanned as its target text, read from the index. A listed
file absent from the working tree is counted as missing in the summary. A file that starts with a byte-order mark is decoded with that codec
(UTF-8, UTF-16 or UTF-32) and scanned; any other file with a NUL byte in its first
8 KiB is treated as binary and skipped (counted in the summary). Nothing else is
exempt unless the allowlist says so.

Rules (each match is one finding):
  drive-path   a Windows drive-letter path: a letter, a colon, then either separator
  msys-path    an MSYS / Git-Bash / WSL drive path: a one-letter root directory,
               optionally under mnt, followed by more path
  home-path    a POSIX user-home path: a named directory under home or Users
               (the CI runner's own home is exempt)
  unc-path     a UNC network path: two backslashes, a server, a backslash, a share
The self-test below shows concrete examples of each.

Allowlist (optional): ``.machine-paths-allow.json`` at the repo root (``--allow`` to
override), a JSON list of entries::

  {"path": "vendor/**", "match": "optional regex", "reason": "why this is not a machine path"}

``path`` is a glob over repo-relative POSIX paths (``*`` stays within one directory,
``**`` crosses directories). ``match``, when given, must be found in the offending
path token (the run of characters around the hit up to whitespace, a quote, a
bracket or the start of another flagged path) for the entry to apply, so it never
covers another path written on the same line, even one joined to it; without it the entry covers every finding in the matched files.
``reason`` is required. An entry that covers no finding fails the
run, so the list cannot silently outlive what it was written for.

Exit status: 0 clean, 1 findings or a stale allowlist entry, 2 usage/config error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ALLOW_FILE = ".machine-paths-allow.json"
BINARY_SNIFF = 8192
# Longest first: the UTF-32LE mark begins with the UTF-16LE one. Each codec consumes its own mark.
BOMS = [(b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"), (b"\xef\xbb\xbf", "utf-8-sig")]

SEP = r"[\\/]"
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("drive-path",
     re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:" + SEP + r"+(?=[A-Za-z0-9_.$~%-])"),
     "Windows drive-letter path"),
    ("msys-path",
     re.compile(r"(?<![\w.:/~$@-])/(?:mnt/)?[A-Za-z]/(?=[A-Za-z0-9_.~-])"),
     "MSYS / Git-Bash / WSL drive path"),
    ("home-path",
     re.compile(r"(?<![\w.~$-])/(?:home|Users)/(?!runner/)[A-Za-z_][\w.-]*/"),
     "POSIX user-home path"),
    ("unc-path",
     re.compile(r"(?<![\\\w])\\\\[A-Za-z0-9_$-][A-Za-z0-9._$-]*\\[A-Za-z0-9._$-]"),
     "UNC network path"),
]


class ConfigError(Exception):
    pass


def glob_to_regex(glob: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(r".*")
            i += 2
        elif c == "*":
            out.append(r"[^/]*")
            i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def load_allowlist(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path.name}: unreadable or not JSON ({exc})") from exc
    if not isinstance(data, list):
        raise ConfigError(f"{path.name}: must be a JSON list of entries")
    entries = []
    for n, e in enumerate(data, 1):
        if not isinstance(e, dict) or not isinstance(e.get("path"), str) or not e["path"].strip():
            raise ConfigError(f"{path.name} entry {n}: 'path' (non-empty string) is required")
        if not isinstance(e.get("reason"), str) or not e["reason"].strip():
            raise ConfigError(f"{path.name} entry {n} ({e['path']}): 'reason' is required")
        match = e.get("match")
        if match is not None and not isinstance(match, str):
            raise ConfigError(f"{path.name} entry {n} ({e['path']}): 'match' must be a string")
        try:
            match_re = re.compile(match) if match else None
        except re.error as exc:
            raise ConfigError(f"{path.name} entry {n} ({e['path']}): bad 'match' regex: {exc}") from exc
        entries.append({"n": n, "path": e["path"], "path_re": glob_to_regex(e["path"]),
                        "match_re": match_re, "used": 0})
    return entries


REGEX_CLASS_LETTERS = set("dwsbDWSB")
TOKEN_DELIMITERS = frozenset("\"'`,;()[]{}<>|=")


def scan_line(line: str) -> list[tuple[str, str, int]]:
    """Every rule hit on the line: (rule id, matched text, start offset)."""
    hits = []
    for rid, rx, _ in RULES:
        for m in rx.finditer(line):
            # A regex class escape (a backslash, then d/w/s/b) before a colon is not a drive letter.
            if (rid == "drive-path" and m.start() > 0 and line[m.start() - 1] == "\\"
                    and line[m.start()] in REGEX_CLASS_LETTERS):
                continue
            hits.append((rid, m.group(0), m.start()))
    return hits


def path_roots(hits: list[tuple[str, str, int]]) -> list[int]:
    """The offsets on a line where a path begins: each rule hit that starts past everything matched before it. A hit
    that starts inside an earlier match (a home folder directly under a drive root) is that same path seen twice."""
    roots, matched_to = [], 0
    for _, text, pos in sorted(hits, key=lambda hit: hit[2]):
        if pos >= matched_to:
            roots.append(pos)
        matched_to = max(matched_to, pos + len(text))
    return roots


def token_span(line: str, pos: int, roots: list[int] | tuple[int, ...] = ()) -> tuple[int, int]:
    """The path token holding offset ``pos``: up to whitespace, a quote or a bracket on either side, and no further
    than the nearest of ``roots`` (see path_roots) on either side, so that a second path joined to the first without
    a delimiter (a colon as in PATH, a parent-directory hop, a question mark) is a token of its own."""
    def inside(c: str) -> bool:
        return not c.isspace() and c not in TOKEN_DELIMITERS
    start = pos
    while start > 0 and inside(line[start - 1]):
        start -= 1
    end = pos
    while end < len(line) and inside(line[end]):
        end += 1
    start = max([start, *(r for r in roots if r <= pos)])
    end = min([end, *(r for r in roots if r > pos)])
    return start, max(end, pos + 1)


def entry_covers(entry: dict, line: str, span: tuple[int, int]) -> bool:
    if entry["match_re"] is None:
        return True
    start, end = span
    return any(m.start() < end and max(m.end(), m.start() + 1) > start for m in entry["match_re"].finditer(line))


SYMLINK_MODE = "120000"


def tracked_files(root: Path) -> list[tuple[str, str, str]]:
    """Every path in the index, once: (path, mode, blob id)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"],
                             capture_output=True, check=True).stdout
    except FileNotFoundError as exc:
        raise ConfigError("git not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"not a git repository: {root} ({exc.stderr.decode(errors='replace').strip()})") from exc
    entries: dict[str, tuple[str, str]] = {}
    for record in out.decode("utf-8", errors="surrogateescape").split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode, sha, _stage = meta.split(" ")
        entries.setdefault(path, (mode, sha))  # an unmerged path is listed once per stage
    return [(path, mode, sha) for path, (mode, sha) in sorted(entries.items())]


def read_blob(root: Path, sha: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "blob", sha], capture_output=True)
    if proc.returncode != 0:
        raise OSError(proc.stderr.decode(errors="replace").strip() or f"git cat-file blob {sha} failed")
    return proc.stdout


def decode_text(data: bytes) -> str | None:
    """The file's text, or None for binary. A byte-order mark picks the codec, so UTF-16 and UTF-32 text
    (PowerShell 5.1 writes UTF-16LE by default) is scanned rather than mistaken for binary by its NUL bytes."""
    for bom, codec in BOMS:
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    if b"\0" in data[:BINARY_SNIFF]:
        return None
    return data.decode("utf-8", errors="replace")


def check(root: Path, allow_path: Path) -> int:
    allow = load_allowlist(allow_path)
    findings, binary, scanned, missing = [], 0, 0, 0
    for rel, mode, sha in tracked_files(root):
        try:
            if mode == SYMLINK_MODE:
                # A symlink's content is its target text. Read it from the index: following the link would scan
                # another file (or nothing, when it dangles or was checked out without symlink rights).
                data = read_blob(root, sha)
            else:
                data = (root / rel).read_bytes()
        except FileNotFoundError:
            missing += 1  # deleted in the working tree but still in the index
            continue
        except OSError as exc:
            findings.append(f"{rel}: unreadable: {exc.strerror or exc}")
            continue
        text = decode_text(data)
        if text is None:
            binary += 1
            continue
        scanned += 1
        candidates = [e for e in allow if e["path_re"].match(rel)]
        for lineno, line in enumerate(text.splitlines(), 1):
            hits = scan_line(line)
            roots = path_roots(hits)
            for rid, text_hit, pos in hits:
                span = token_span(line, pos, roots)
                covered = next((e for e in candidates if entry_covers(e, line, span)), None)
                if covered is not None:
                    covered["used"] += 1
                    continue
                excerpt = line.strip()
                if len(excerpt) > 160:
                    excerpt = excerpt[:157] + "..."
                findings.append(f"{rel}:{lineno}: {rid} ({text_hit!r}): {excerpt}")
    stale = [e for e in allow if e["used"] == 0]
    for line in findings:
        print(line)
    for e in stale:
        print(f"{allow_path.name}: entry {e['n']} ({e['path']}) covers nothing - remove it")
    print(f"machine-paths: {scanned} text files scanned, {binary} binary skipped, "
          f"{missing} missing from the working tree, {len(allow)} allowlist entries, {len(findings)} findings, {len(stale)} stale entries")
    if findings or stale:
        print("FAIL: replace machine paths with paths derived at runtime (repo root, home folder, "
              "per-machine config) or add a justified allowlist entry.")
        return 1
    print("PASS: no machine-specific absolute paths.")
    return 0


# Fixtures are written with markers so that this file holds no literal machine path
# (it must pass this guard, and any stricter boundary check, without exceptions):
# <D> = a drive letter and colon, <M> = an MSYS drive root, <H> = the POSIX home root,
# <U> = a UNC prefix, <C> = the Claude Code state directory name, <CL> = a colon.
_MARKERS = {"<D>": "C" + ":", "<d>": "f" + ":", "<M>": "/" + "c", "<H>": "/" + "home",
            "<UH>": "/" + "Users", "<U>": "\\" + "\\", "<C>": "." + "claude", "<CL>": ":"}


def _expand(line: str) -> str:
    for k, v in _MARKERS.items():
        line = line.replace(k, v)
    return line


SELF_TEST = [
    # (line, expected rule ids)
    (r"see <D>\src\repo\file.md", ["drive-path"]),
    ("open <d>/Tools/reports", ["drive-path"]),
    (r'"<D>\Users\name\x"', ["drive-path"]),
    ("file:///<D>/dir/y", ["drive-path"]),
    ("https://example.com/a/b", []),
    ("regex [A-Za-z]:[\\/] stays clean", []),
    ("ratio 12:30/45", []),
    ("ls <M>/src/repo", ["msys-path"]),
    ("cd /mnt<M>/work", ["msys-path"]),
    ("a path like and/or x/y/z", []),
    ("~/<C>/hooks/x.py", []),
    ("$HOME/<C>", []),
    ("<H>/alice/.bashrc", ["home-path"]),
    ("<UH>/bob/Library/x", ["home-path"]),
    ("<H>/runner/work/repo", []),
    (r"<U>server\share\x", ["unc-path"]),
    (r"printf 'a\n'", []),
    ("{repos}/claude-tools/reports", []),
    (r"regex T\d\d<CL>\d\d<CL>\d\dZ and \w<CL>\s", []),
    (r"extended \\?\<D>\x", ["drive-path"]),
]


def self_test() -> int:
    bad = 0
    for line, want in SELF_TEST:
        got = [rid for rid, _, _ in scan_line(_expand(line))]
        if got != want:
            bad += 1
            print(f"self-test FAIL: {line!r}: expected {want}, got {got}")
    for glob, path, want in [("vendor/**", "vendor/a/b.py", True), ("*.md", "a/b.md", False),
                             ("**/*.md", "a/b.md", True), ("**/*.md", "b.md", True),
                             ("hooks/tests/*.py", "hooks/tests/x/y.py", False)]:
        if bool(glob_to_regex(glob).match(path)) != want:
            bad += 1
            print(f"self-test FAIL: glob {glob!r} vs {path!r}: expected {want}")
    total = len(SELF_TEST) + 5
    for name, ok in _repo_cases():
        total += 1
        if not ok:
            bad += 1
            print(f"self-test FAIL: repo case: {name}")
    print(f"self-test: {total - bad}/{total} cases pass")
    return 1 if bad else 0


def _repo_cases() -> list[tuple[str, bool]]:
    """End-to-end: a throwaway git repo, scanned with and without an allowlist."""
    import contextlib
    import io
    import tempfile

    def run(root: Path) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                return check(root, root / ALLOW_FILE)
            except ConfigError:
                return 2

    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if subprocess.run(["git", "init", "-q", str(root)], capture_output=True).returncode != 0:
            return [("git init", False)]
        (root / "clean.md").write_text("Reports go to {reports}/x.md and ~/notes.\n", encoding="utf-8")
        (root / "fixture.py").write_text(_expand('P = "<D>/fixture/x"\n'), encoding="utf-8")
        (root / "blob.bin").write_bytes(b"\0" + _expand("<D>/x").encode())
        subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
        cases.append(("finding fails the run", run(root) == 1))
        allow = [{"path": "*.py", "match": "fixture", "reason": "synthetic fixture"}]
        (root / ALLOW_FILE).write_text(json.dumps(allow), encoding="utf-8")
        cases.append(("allowlisted finding passes", run(root) == 0))
        (root / "fixture.py").write_text(_expand('P = "<D>/fixture/x"; Q = "<d>/real/y"\n'), encoding="utf-8")
        cases.append(("allowlist entry does not cover another path on its line", run(root) == 1))
        (root / "fixture.py").write_text(_expand('P = "<D>/fixture/x"\n'), encoding="utf-8")
        allow.append({"path": "docs/**", "reason": "covers nothing"})
        (root / ALLOW_FILE).write_text(json.dumps(allow), encoding="utf-8")
        cases.append(("stale entry fails the run", run(root) == 1))
        (root / ALLOW_FILE).write_text(json.dumps([{"path": "*.py"}]), encoding="utf-8")
        cases.append(("entry without reason is a config error", run(root) == 2))
        (root / ALLOW_FILE).write_text(json.dumps(allow[:1]), encoding="utf-8")
        (root / "out16.ps1").write_bytes(_expand("$p = '<D>/real/y'\r\n").encode("utf-16"))
        subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
        cases.append(("UTF-16 text with a byte-order mark is scanned", run(root) == 1))
        (root / "out16.ps1").unlink()
        subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True)
        target = subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin"], capture_output=True,
                                input=_expand("<H>/someone/y").encode()).stdout.decode().strip()
        subprocess.run(["git", "-C", str(root), "update-index", "--add", "--cacheinfo", f"120000,{target},link"],
                       capture_output=True)
        cases.append(("symlink target text is scanned, even with nothing on disk", run(root) == 1))
    return cases


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, help="repository root (default: the git repo containing the cwd)")
    ap.add_argument("--allow", type=Path, help=f"allowlist file (default: <root>/{ALLOW_FILE})")
    ap.add_argument("--self-test", action="store_true", help="run the built-in rule cases and exit")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # findings quote file text; never die on a console codec
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.self_test:
        return self_test()
    try:
        if args.root is None:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
            if top.returncode != 0:
                raise ConfigError("not inside a git repository; pass --root")
            root = Path(top.stdout.strip())
        else:
            root = args.root.resolve()
        return check(root, args.allow or root / ALLOW_FILE)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
