"""Shared library for the weekly-full-audit and slop-audit-sweep skills.

Stdlib only. Every location is an argument; nothing here names a machine path.
Implements the anchor contract rules: identities (5), run record and state
separation (6, 7), revision baselines (3), slop reuse (4), rollback (8),
protected paths, and exclusive-create artifact names.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "audit-run/1"
TASK_IDS: tuple[str, ...] = ("weekly-full-audit", "slop-audit-sweep")
# Weekly coverage floor (SA02-1): `start` registers these, so skipping `record --require` cannot empty it.
WEEKLY_REQUIRED: tuple[str, ...] = ("sec", "re", "arch", "cq", "fe", "perf", "hyg", "slop", "ws")

# Activation table (anchor "What each decision blocks").
# A mode set False may not run: preflight refuses it with an explicit failure.
#   autofix               -> owner decision D1 (keep or drop the Auto-Fix Pass)
#   deliver:gmail-draft   -> owner decision D2 (delivery channel)
#   deliver:file          -> owner decision D2 (delivery channel)
# Flip an entry only on a recorded owner decision.
# D1 owner decision 2026-10-30: the Auto-Fix Pass is dropped; autofix stays off.
ACTIVATION: dict[str, bool] = {
    "autofix": False,
    "deliver:gmail-draft": False,
    "deliver:file": True,  # D2 owner decision 2026-10-30, file only
}
ACTIVATION_DECISION: dict[str, str] = {
    "autofix": "D1",
    "deliver:gmail-draft": "D2",
    "deliver:file": "D2",
}
DELIVER_MODES: tuple[str, ...] = ("gmail-draft", "file", "none")

RUN_ID_RE = re.compile(
    r"^(?P<task>[a-z][a-z0-9-]*?)-(?P<ts>\d{8}T\d{6}Z)-(?P<hex>[0-9a-f]{6})$"
)

# Coverage statuses. "lost" and "handoff-pending" degrade the run; the
# not-run-* statuses are declared gaps that leave the run ok (anchor rule 2); not-run-approval is a
# check that needs an owner approval the run was not given (a build, a live measurement).
COVERAGE_STATUSES: tuple[str, ...] = (
    "complete",
    "lost",
    "not-run-optional",
    "not-run-flag-off",
    "not-run-approval",
    "handoff-pending",
    "unchanged",
)
DEGRADING: frozenset[str] = frozenset({"lost", "handoff-pending"})
RUN_SEVERITY: dict[str, int] = {"ok": 0, "degraded": 1, "failed": 2}


class AuditError(Exception):
    """A contract violation or unusable input. Callers map it to a failure."""


# --------------------------------------------------------------------------- time / ids


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso_utc(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def mint_run_id(task_id: str, now: _dt.datetime | None = None) -> str:
    if task_id not in TASK_IDS:
        raise AuditError(f"unknown task_id {task_id!r}; expected one of {TASK_IDS}")
    moment = (now or utcnow()).astimezone(_dt.timezone.utc)
    return f"{task_id}-{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


@dataclass(frozen=True)
class RunId:
    task_id: str
    stamp: _dt.datetime
    short: str
    text: str


def parse_run_id(text: str) -> RunId:
    m = RUN_ID_RE.match(text or "")
    if not m or m.group("task") not in TASK_IDS:
        raise AuditError(f"malformed run_id {text!r}")
    stamp = _dt.datetime.strptime(m.group("ts"), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=_dt.timezone.utc
    )
    return RunId(m.group("task"), stamp, m.group("hex"), text)


# Date policy. A run has ONE date: the calendar date of its run_id stamp (a UTC instant) in the
# local zone of the host that minted it, recorded with its UTC offset in the run record
# (`date_policy`). Every name this package builds (GRRP slug, report stem) uses that date, so a
# run that crosses midnight keeps one date throughout. Files GRRP names itself (`init` prefixes
# its local date at creation, `batch-init` its UTC date) are never reconstructed from a date:
# their paths are taken from GRRP's own `created:` / `existing:` line (parse_grrp_path).


def _local(run_id: str, tz: _dt.tzinfo | None) -> _dt.datetime:
    stamp = parse_run_id(run_id).stamp
    return stamp.astimezone(tz) if tz else stamp.astimezone()


def run_date(run_id: str, tz: _dt.tzinfo | None = None) -> str:
    """Calendar date of the run start in `tz` (default: host local zone)."""
    return _local(run_id, tz).strftime("%Y-%m-%d")


def date_policy(run_id: str, tz: _dt.tzinfo | None = None) -> dict[str, str]:
    local = _local(run_id, tz)
    off = local.utcoffset() or _dt.timedelta(0)
    sign = "-" if off < _dt.timedelta(0) else "+"
    mins = int(abs(off).total_seconds() // 60)
    return {
        "basis": "run_id UTC stamp in the minting host's local zone",
        "run_start_utc": parse_run_id(run_id).stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc_offset": f"{sign}{mins // 60:02d}:{mins % 60:02d}",
        "run_date": local.strftime("%Y-%m-%d"),
    }


GRRP_PATH_RE = re.compile(r"^(?:created|existing): (?P<path>.+?)[ \t]*$", re.M)


def parse_grrp_path(stdout: str) -> str:
    """The ledger/manifest path from `grrp.py init|batch-init` output; never rebuilt from a date."""
    found = GRRP_PATH_RE.findall(stdout or "")
    if len(found) != 1:
        raise AuditError(f"expected one 'created:'/'existing:' line from grrp, found {len(found)}")
    return found[0]


def grrp_slug(run_id: str, prefix: str | None = None) -> str:
    """Rule 5 slug: <prefix or task_id>-<date>-<run_id short>.

    A resumed run reuses its run_id and therefore its slug, so `grrp init`
    resumes the same ledger instead of opening a second one.
    """
    rid = parse_run_id(run_id)
    head = prefix or rid.task_id
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", head):
        raise AuditError(f"bad slug prefix {head!r}")
    return f"{head}-{run_date(run_id)}-{rid.short}"


# --------------------------------------------------------------------------- git


def git(root: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise AuditError(f"git {' '.join(args)} failed in {root}: {proc.stderr.strip()}")
    return proc


def git_available() -> bool:
    return shutil.which("git") is not None


def is_git_checkout(path: Path | str) -> bool:
    p = Path(path)
    if not p.is_dir():
        return False
    proc = git(p, "rev-parse", "--is-inside-work-tree", check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def head_rev(root: Path | str) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def commit_known(root: Path | str, rev: str) -> bool:
    return git(root, "cat-file", "-e", f"{rev}^{{commit}}", check=False).returncode == 0


def is_ancestor(root: Path | str, older: str, newer: str) -> bool | None:
    """True/False from merge-base --is-ancestor; None if either rev is unknown."""
    proc = git(root, "merge-base", "--is-ancestor", older, newer, check=False)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def normalize_remote(url: str) -> str:
    """host/owner/repo, lower-case host, no scheme, credentials or .git suffix."""
    u = url.strip()
    m = re.match(r"^[\w.-]+@([\w.-]+):(.+)$", u)  # scp-like git@host:owner/repo
    if m:
        u = f"{m.group(1)}/{m.group(2)}"
    u = re.sub(r"^[a-z+]+://", "", u, flags=re.I)
    u = re.sub(r"^[^@/]+@", "", u)
    u = u.rstrip("/")
    if u.lower().endswith(".git"):
        u = u[:-4]
    host, _, rest = u.partition("/")
    return f"{host.lower()}/{rest}"


@dataclass(frozen=True)
class RootInfo:
    name: str
    path: Path
    toplevel: Path
    identity: str
    path_arg: str
    head_rev: str


def root_info(name: str, path: Path | str) -> RootInfo:
    """Machine-independent identity of a target root.

    identity = normalized origin URL, else 'local:<top-level folder name>'.
    path_arg = the root's path relative to the repository top level ('.' = whole repo).
    """
    p = Path(path).resolve()
    if not is_git_checkout(p):
        raise AuditError(f"root {name!r} is not a git checkout: {p}")
    top = Path(git(p, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    origin = git(p, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    identity = normalize_remote(origin) if origin else f"local:{top.name}"
    rel = os.path.relpath(p, top).replace(os.sep, "/")
    return RootInfo(name, p, top, identity, "." if rel in ("", ".") else rel, head_rev(p))


def dirty(root: Path | str) -> list[str]:
    out = git(root, "status", "--porcelain", "--untracked-files=normal").stdout
    return [ln for ln in out.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- blobs / paths


def blob_sha_bytes(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def blob_sha(path: Path) -> str | None:
    """git blob SHA of the file's raw bytes; None if the file does not exist."""
    if not path.exists():
        return None
    if not path.is_file():
        raise AuditError(f"not a regular file: {path}")
    return blob_sha_bytes(path.read_bytes())


_PROTECTED_SEGMENTS = frozenset({"99_archive", "_backups"})


def protected_reason(rel_posix: str) -> str | None:
    """Why a root-relative path is protected (never written), or None."""
    parts = [s for s in rel_posix.replace("\\", "/").split("/") if s not in ("", ".")]
    low = [s.lower() for s in parts]
    if not parts:
        return "empty path"
    name = low[-1]
    if name == ".env" or name.startswith(".env."):
        return ".env files are protected"
    if low[-2:] == ["backend", "guard.py"]:
        return "backend/guard.py is protected"
    for seg in low:
        if seg in _PROTECTED_SEGMENTS:
            return f"{seg} is protected"
    for i in range(len(low) - 1):
        if low[i] == "alembic" and low[i + 1] == "versions":
            return "alembic versions are protected"
    return None


def resolve_inside(root: Path, rel: str) -> Path:
    """Resolve a root-relative path; refuse escapes and protected paths."""
    root_r = root.resolve()
    target = (root_r / rel).resolve()
    if target != root_r and root_r not in target.parents:
        raise AuditError(f"path escapes root: {rel}")
    reason = protected_reason(os.path.relpath(target, root_r).replace(os.sep, "/"))
    if reason:
        raise AuditError(f"refused: {rel}: {reason}")
    return target


# --------------------------------------------------------------------------- atomic io


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8 without BOM, LF, replaced atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


_BAD_ESCAPE = re.compile(r'(?<!\\)(?:\\\\)*\\(?![\\"/bfnrtu])')


# Text decoded with the wrong codec (UTF-8 bytes read as cp1252/latin-1): "Â·", "â€”", "Ã©".
_MOJIBAKE = re.compile("\u00c2[\u00a0-\u00bf]|\u00e2\u20ac|\u00c3[\u0080-\u00bf]")


def refuse_mojibake(what: str, text: str) -> str:
    """Stored lines are copied verbatim into reports; refuse text that was already mis-decoded."""
    m = _MOJIBAKE.search(text)
    if m:
        raise AuditError(f"{what}: mis-decoded text {m.group(0)!r} (UTF-8 read with a legacy codec); "
                         "re-produce it as UTF-8")
    return text


def decode_text(data: bytes, what: str) -> str:
    """Strict decode: UTF-32/UTF-16 by BOM, else UTF-8 (BOM optional). Never a locale codec."""
    for bom, codec in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                       (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            try:
                return data.decode(codec)
            except UnicodeDecodeError as exc:
                raise AuditError(f"{what}: invalid {codec}: {exc}") from exc
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AuditError(f"{what}: not UTF-8 ({exc}); write it as UTF-8") from exc


def read_json(path: Path) -> Any:
    """Parse JSON as written. Invalid input is refused with a diagnostic, never repaired: child
    results must be produced by a serializer (json.dump / `RUN check` args), not hand-typed."""
    text = decode_text(path.read_bytes(), str(path))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        hint = ""
        if _BAD_ESCAPE.search(text):
            hint = (" (invalid backslash escape, e.g. an unescaped Windows path: write the file with a"
                    " JSON serializer or pass the values as `RUN check` arguments)")
        raise AuditError(f"{path}: invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg}{hint}") from exc


def create_exclusive(directory: Path, stem: str, suffix: str, content: str) -> Path:
    """Create <stem><suffix>, else <stem>-2<suffix>, -3 ... via O_EXCL; never overwrite."""
    directory.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for n in range(1, 1000):
        name = f"{stem}{suffix}" if n == 1 else f"{stem}-{n}{suffix}"
        target = directory / name
        try:
            fd = os.open(target, flags)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return target
    raise AuditError(f"no free -N name for {stem}{suffix} in {directory}")


# --------------------------------------------------------------------------- front matter

FRONT_KEYS: tuple[str, ...] = ("task_id", "run_id", "roots")


def render_front_matter(task_id: str, run_id: str, roots: Mapping[str, Mapping[str, Any]]) -> str:
    """Five lines, so the live header lines still fall inside the first 15 lines."""
    return (
        "---\n"
        f"task_id: {task_id}\n"
        f"run_id: {run_id}\n"
        f"roots: {json.dumps(roots, separators=(',', ':'), sort_keys=True)}\n"
        "---\n"
    )


def parse_front_matter(text: str) -> dict[str, Any] | None:
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    out: dict[str, Any] = {}
    closed = False
    for ln in lines[1:40]:
        if ln.strip() == "---":
            closed = True
            break
        key, sep, val = ln.partition(":")
        if not sep:
            continue
        key, val = key.strip(), val.strip()
        if key == "roots":
            try:
                out[key] = json.loads(val)
            except json.JSONDecodeError:
                return None
        else:
            out[key] = val
    if not closed or any(k not in out for k in FRONT_KEYS) or not isinstance(out.get("roots"), dict):
        return None
    try:
        parse_run_id(str(out["run_id"]))
    except AuditError:
        return None
    return out


def root_entry(info: RootInfo, baseline_rev: str | None, window: str) -> dict[str, Any]:
    return {
        "identity": info.identity,
        "path_arg": info.path_arg,
        "baseline_rev": baseline_rev,
        "head_rev": info.head_rev,
        "window": window,
    }


# --------------------------------------------------------------------------- baselines (rule 3)


@dataclass
class Baseline:
    root: str
    head_rev: str
    baseline_rev: str | None
    window: str  # "<base>..<head>", "unchanged" or "full"
    source: str | None  # report path or "--baseline"
    notes: list[str] = field(default_factory=list)
    trust: str = "recorded"  # recorded | override | bootstrap

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "head_rev": self.head_rev,
            "baseline_rev": self.baseline_rev,
            "window": self.window,
            "source": self.source,
            "notes": self.notes,
            "trust": self.trust,
        }


def _candidate_reports(report_dirs: Sequence[Path], pattern: str) -> list[Path]:
    seen: dict[str, Path] = {}
    for d in report_dirs:
        if d.is_dir():
            for p in d.glob(pattern):
                if p.is_file():
                    seen[str(p.resolve())] = p
    return sorted(seen.values())


def select_baseline(
    info: RootInfo,
    task_id: str,
    report_dirs: Sequence[Path],
    pattern: str,
    override: str | None = None,
    state_dir: Path | None = None,
) -> Baseline:
    """Newest prior report for this root by revision ancestry, never by date.

    Candidates: reports whose front matter names this task_id and this root with
    the same identity and path_arg. Unknown commits are skipped with a note.
    Trust rule: a baseline is established only by a SUCCESSFUL run, so a candidate
    counts only when its run record exists under `state_dir` with run ok|degraded.
    A legacy report (no front-matter head_rev), a missing/failed/unfinished record,
    or no state_dir -> bootstrap: window `full`, trust `bootstrap`; this run
    inspects full scope and becomes a baseline only if it succeeds. Nothing is
    inferred from a report's date. An operator override is trust `override`,
    never an established baseline.
    Maximal = no other candidate head descends from it; ties or divergence ->
    most recent run_id timestamp.
    """
    notes: list[str] = []
    if override:
        if not commit_known(info.path, override):
            raise AuditError(f"--baseline {override} is not a commit in {info.name}")
        base = git(info.path, "rev-parse", f"{override}^{{commit}}").stdout.strip()
        notes.append("operator override: not established by a successful run record")
        bl = _finish_baseline(info, base, "--baseline", notes)
        if bl.trust != "bootstrap":
            bl.trust = "override"
        return bl

    cands: list[tuple[str, _dt.datetime, Path]] = []
    for path in _candidate_reports(report_dirs, pattern):
        try:
            fm = parse_front_matter(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError as exc:
            notes.append(f"unreadable report skipped: {path.name} ({exc})")
            continue
        if not fm or fm.get("task_id") != task_id:
            continue
        entry = fm["roots"].get(info.name)
        if not isinstance(entry, dict):
            continue
        if entry.get("identity") != info.identity or entry.get("path_arg") != info.path_arg:
            continue
        rev = entry.get("head_rev")
        if not isinstance(rev, str) or not commit_known(info.path, rev):
            notes.append(f"baseline candidate skipped, commit unknown: {path.name}")
            continue
        if state_dir is None:
            notes.append(f"baseline candidate skipped, no state dir to confirm its run: {path.name}")
            continue
        rec_path = record_path(state_dir, task_id, str(fm["run_id"]))
        if not rec_path.exists():
            notes.append(f"baseline candidate skipped, no run record (not established by a successful run): {path.name}")
            continue
        try:
            outcome = read_json(rec_path).get("run")
        except (OSError, json.JSONDecodeError):
            notes.append(f"baseline candidate skipped, run record unreadable: {path.name}")
            continue
        if outcome not in ("ok", "degraded"):
            why = "run failed" if outcome == "failed" else f"run not successful ({outcome})"
            notes.append(f"baseline candidate skipped, {why}: {path.name}")
            continue
        cands.append((rev, parse_run_id(str(fm["run_id"])).stamp, path))

    if not cands:
        return Baseline(info.name, info.head_rev, None, "full", None, notes + ["no baseline", "bootstrap: full-scope inspection; baseline only if this run succeeds"], "bootstrap")

    def descends(a: str, b: str) -> bool:
        return a != b and bool(is_ancestor(info.path, a, b))

    maximal = [c for c in cands if not any(descends(c[0], d[0]) for d in cands)]
    if len({c[0] for c in maximal}) > 1:
        notes.append("divergent baseline candidates; most recent run_id chosen")
    best = max(maximal, key=lambda c: c[1])
    return _finish_baseline(info, best[0], str(best[2]), notes)


def _finish_baseline(info: RootInfo, base: str, source: str, notes: list[str]) -> Baseline:
    head = info.head_rev
    if is_ancestor(info.path, base, head) is False:
        mb = git(info.path, "merge-base", base, head, check=False).stdout.strip()
        if not mb:
            notes.append(f"baseline {base[:12]} shares no history with HEAD; full scope")
            return Baseline(info.name, head, None, "full", source, notes + ["no baseline", "bootstrap: full-scope inspection; baseline only if this run succeeds"], "bootstrap")
        notes.append(f"baseline {base[:12]} is not an ancestor of HEAD; window starts at merge-base {mb[:12]}")
        base = mb
    window = "unchanged" if base == head else f"{base}..{head}"
    return Baseline(info.name, head, base, window, source, notes)


# --------------------------------------------------------------------------- run record (rules 6, 7)

INSPECTION = ("pending", "running", "complete", "failed")
PUBLICATION = ("pending", "published", "not-published")
DELIVERY = ("pending", "delivered", "skipped", "failed")
REMEDIATION = ("not-run", "running", "ok", "failed", "reverted")


def record_path(state_dir: Path, task_id: str, run_id: str) -> Path:
    return state_dir / task_id / f"{run_id}.json"


def evidence_dir(state_dir: Path, task_id: str, run_id: str) -> Path:
    """The one declared per-run evidence location: <sd>/<task_id>/<run_id>_evidence/."""
    return state_dir / task_id / f"{run_id}_evidence"


FINDING_LABELS = ("HYPOTHESIS", "CONFIRMED", "REFUTED", "INTENDED")
FINDING_KEYS = ("id", "label", "evidence")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def require_artifact(path_s: str, root: Path | None, what: str) -> Path:
    """A claimed artifact must exist, be non-empty and (when a root is declared) lie under it."""
    path = Path(path_s)
    if not path_s or not path.is_file():
        raise AuditError(f"{what} missing ({path_s or 'none given'})")
    if path.stat().st_size == 0:
        raise AuditError(f"{what} is empty ({path_s})")
    if root is not None and not _inside(path, root):
        raise AuditError(f"{what} {path_s} is outside the per-run evidence dir {root}")
    return path


def validate_findings(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    if not isinstance(data, list):
        raise AuditError(f"{path}: findings must be a JSON list")
    seen: set[str] = set()
    for i, f in enumerate(data):
        if not isinstance(f, dict):
            raise AuditError(f"{path}: finding {i} is not an object")
        miss = [k for k in FINDING_KEYS if not str(f.get(k, "")).strip()]
        if miss:
            raise AuditError(f"{path}: finding {f.get('id', i)} lacks {', '.join(miss)}")
        if f["label"] not in FINDING_LABELS:
            raise AuditError(f"{path}: finding {f['id']} label {f['label']!r} not in {FINDING_LABELS}")
        if f["id"] in seen:
            raise AuditError(f"{path}: duplicate finding id {f['id']}")
        seen.add(f["id"])
    return data


def ctxcheck_line(summary: Mapping[str, Any], verdicts: Sequence[Mapping[str, Any]]) -> str:
    """Header line from ctxcheck's own summary plus one serialized verdict per WARN."""
    try:
        counts = {k: int(summary[k]) for k in ("PASS", "WARN", "FAIL")}
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"ctxcheck summary lacks PASS/WARN/FAIL counts: {exc}") from exc
    if len(verdicts) != counts["WARN"]:
        raise AuditError(f"ctxcheck: {counts['WARN']} WARN but {len(verdicts)} verdict(s); every WARN needs one")
    bad = [v for v in verdicts if not isinstance(v, dict) or v.get("verdict") not in ("CONFIRMED", "REFUTED")]
    if bad:
        raise AuditError(f"ctxcheck: WARN verdict must be CONFIRMED|REFUTED: {bad[0]}")
    confirmed = sum(1 for v in verdicts if v["verdict"] == "CONFIRMED")
    return (f"**ctxcheck:** PASS {counts['PASS']} · WARN {counts['WARN']} · FAIL {counts['FAIL']}"
            f" (WARN-confirmed {confirmed})")


def new_record(task_id: str, run_id: str, flags: Mapping[str, Any]) -> dict[str, Any]:
    now = iso_utc(utcnow())
    return {
        "schema": SCHEMA,
        "task_id": task_id,
        "run_id": run_id,
        "run_date": run_date(run_id),
        "date_policy": date_policy(run_id),
        "started_at": now,
        "finished_at": None,
        "flags": dict(flags),
        "states": {
            "inspection": "pending",
            "publication": "pending",
            "delivery": {"mode": flags.get("deliver"), "state": "pending", "ref": None},
            "remediation": "not-run",
            "remediation_reason": None,
        },
        "run": None,
        "roots": {},
        "coverage": {},
        "artifacts": [],
        "ledgers": [],
        "gate": None,
        "remediation_log": [],
        "events": [{"at": now, "event": "record-created", "detail": None}],
    }


def load_record(path: Path) -> dict[str, Any]:
    rec = read_json(path)
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        raise AuditError(f"not an audit run record: {path}")
    return rec


def save_record(path: Path, rec: Mapping[str, Any]) -> None:
    atomic_write_json(path, rec)


def add_event(rec: dict[str, Any], event: str, detail: Any = None) -> None:
    rec["events"].append({"at": iso_utc(utcnow()), "event": event, "detail": detail})


def set_inspection(rec: dict[str, Any], state: str) -> None:
    cur = rec["states"]["inspection"]
    allowed = {"running": ("pending",), "complete": ("running",), "failed": ("pending", "running")}
    if state not in allowed or cur not in allowed[state]:
        raise AuditError(f"inspection {cur} -> {state} not allowed")
    if state == "complete":
        barrier(rec, "inspection-complete")
    rec["states"]["inspection"] = state
    add_event(rec, f"inspection-{state}")


def set_coverage(rec: dict[str, Any], key: str, status: str, note: str = "") -> None:
    if status not in COVERAGE_STATUSES:
        raise AuditError(f"unknown coverage status {status!r}")
    if rec["states"]["inspection"] != "running":
        raise AuditError("coverage changes only while inspection is running")
    chk = rec.get("checks", {}).get(key)
    if chk is None and status == "complete":
        # `complete` is earned by a registered check's return, never asserted for a free-form key.
        raise AuditError(f"coverage {key} is not a required check; register it with `record --require` "
                         "and report it with `check`")
    if chk is not None and status == "complete" and not (chk["state"] == "returned" and not chk["remaining"]):
        # A required check is complete only through its own return with nothing remaining.
        raise AuditError(f"coverage {key} is a required check ({chk['state']}); report it with `check`")
    prior = rec["coverage"].get(key)
    if prior and prior["status"] in DEGRADING and status not in DEGRADING:
        # Never softer (rule 7): a recorded loss or pending handoff cannot be cleared mid-run.
        raise AuditError(f"coverage {key} is {prior['status']}; it cannot be softened to {status}")
    rec["coverage"][key] = {"status": status, "note": note}


def derive_run(rec: Mapping[str, Any]) -> str:
    if rec["states"]["inspection"] == "failed":
        return "failed"
    if rec.get("undeclared_outputs"):
        return "degraded"  # outputs-audit defect: something wrote outside the declared outputs
    if rec.get("modified_after_collect"):
        return "degraded"  # a collected output changed before finalize
    return "degraded" if any(c["status"] in DEGRADING for c in rec["coverage"].values()) else "ok"


def set_run(rec: dict[str, Any], run: str) -> None:
    """Declare run completion (rule 7). Never softer than coverage implies."""
    if run not in RUN_SEVERITY:
        raise AuditError(f"unknown run outcome {run!r}")
    if rec["states"]["inspection"] not in ("complete", "failed"):
        raise AuditError("run outcome is declared only after inspection ends")
    if rec["run"] is not None:
        raise AuditError(f"run outcome already declared: {rec['run']}")
    verify_collected_outputs(rec)
    floor = derive_run(rec)
    if RUN_SEVERITY[run] < RUN_SEVERITY[floor]:
        raise AuditError(f"run {run} is softer than coverage implies ({floor})")
    rec["run"] = run
    add_event(rec, "run-complete", run)


def set_gate(rec: dict[str, Any], gate: Any) -> None:
    if rec["states"]["inspection"] != "complete":
        raise AuditError("gate is recorded only after inspection completes")
    if rec["task_id"] == "slop-audit-sweep":
        # sweep: {"<ledger path>": <gate json>}; {} when every root was unchanged
        if not isinstance(gate, dict):
            raise AuditError("gate must be a JSON object mapping each ledger path to its grrp gate result")
        for ledger, result in gate.items():
            _check_gate_result(result, f"gate[{ledger}]")
    else:
        _check_gate_result(gate, "gate")
    rec["gate"] = gate
    add_event(rec, "gate-recorded")


GATE_VERDICTS = ("complete", "incomplete", "blocked")


def _check_gate_result(result: Any, where: str) -> None:
    """A grrp gate result is a verdict the gate can reach; exit 3 (`invalid`) is a failure, not a verdict."""
    if not isinstance(result, dict):
        raise AuditError(f"{where}: not a grrp gate result (expected a JSON object with a verdict)")
    verdict = result.get("verdict")
    if verdict not in GATE_VERDICTS:
        raise AuditError(f"{where}: verdict {verdict!r} is not one of {', '.join(GATE_VERDICTS)} "
                         "(grrp gate exit 3 is a failure: finalize the run, do not record it)")
    if result.get("validation_errors"):
        raise AuditError(f"{where}: gate result carries validation_errors; fix the ledger and re-run the gate")


def _unreported_roots(rec: Mapping[str, Any]) -> list[str]:
    """Changed roots whose inspection was not lost and that no report artifact names."""
    reported = {n for a in rec["artifacts"] if a.get("kind") == "report" for n in a.get("roots") or []}
    out = []
    for name, root in rec["roots"].items():
        if root.get("window") == "unchanged" or name in reported:
            continue
        cov = rec["coverage"].get(name)
        if cov is None or cov["status"] == "complete":
            out.append(name)
    return out


def publish(rec: dict[str, Any]) -> None:
    """Rule 6: publication follows report, GRRP apply and gate."""
    if rec["states"]["publication"] != "pending":
        raise AuditError(f"publication already {rec['states']['publication']}")
    if rec["run"] not in ("ok", "degraded"):
        raise AuditError(f"publish requires run ok|degraded, not {rec['run']}")
    if rec["gate"] is None:
        raise AuditError("publish requires the GRRP gate result (rule 6)")
    if not rec["roots"]:
        raise AuditError("publish requires baselined roots (run baseline first; rule 6)")
    if rec["task_id"] == "weekly-full-audit":
        if not any(a.get("kind") == "report" for a in rec["artifacts"]):
            raise AuditError("publish requires the report (run write-report first; rule 6)")
    else:
        missing = _unreported_roots(rec)
        if missing:
            raise AuditError(f"publish requires a report for each changed root; none for: {', '.join(missing)} "
                             "(run write-report --roots <name>; rule 6)")
    rec["states"]["publication"] = "published"
    rec["finished_at"] = iso_utc(utcnow())
    add_event(rec, "published", rec["run"])


def set_delivery(rec: dict[str, Any], state: str, ref: str | None = None) -> None:
    if state not in DELIVERY[1:]:
        raise AuditError(f"unknown delivery state {state!r}")
    if rec["states"]["publication"] != "published":
        raise AuditError("delivery follows publication")
    cur = rec["states"]["delivery"]
    if cur["state"] != "pending":
        raise AuditError(f"delivery already {cur['state']}")
    mode = cur["mode"]
    if state == "delivered" and (mode == "none" or not ACTIVATION.get(f"deliver:{mode}", False)):
        raise AuditError(f"delivery mode {mode} is not activated")
    cur["state"], cur["ref"] = state, ref
    add_event(rec, f"delivery-{state}", ref)


def start_remediation(rec: dict[str, Any], dirty_lines: Sequence[str]) -> bool:
    """True if remediation may proceed; otherwise records the not-run reason."""
    st = rec["states"]
    if st["remediation"] != "not-run" or st["remediation_reason"] is not None:
        raise AuditError("remediation already decided for this run")
    if not rec["flags"].get("autofix"):
        st["remediation_reason"] = "flag off"
        add_event(rec, "remediation-not-run", "flag off")
        return False
    if not ACTIVATION["autofix"]:
        raise AuditError("autofix requested but not activated (D1)")
    if rec["run"] not in ("ok", "degraded"):
        raise AuditError("remediation needs a completed inspection run (ok|degraded)")
    if st["publication"] != "published" or st["delivery"]["state"] == "pending":
        raise AuditError("remediation starts only after publication and delivery")
    if dirty_lines:
        st["remediation_reason"] = "dirty tree"
        add_event(rec, "remediation-not-run", "dirty tree")
        return False
    st["remediation"] = "running"
    add_event(rec, "remediation-running")
    return True


def remediation_outcome(log: Iterable[Mapping[str, Any]]) -> str:
    statuses = [str(e["status"]) for e in log]
    if any(s.startswith("rollback-refused") or s in ("rollback-failed", "failed") for s in statuses):
        return "failed"
    if any(s == "reverted" for s in statuses):
        return "reverted"
    return "ok"


def finish_remediation(rec: dict[str, Any]) -> str:
    st = rec["states"]
    if st["remediation"] != "running":
        raise AuditError("remediation is not running")
    st["remediation"] = remediation_outcome(rec["remediation_log"])
    add_event(rec, "remediation-finished", st["remediation"])
    return st["remediation"]


def finalize_failed(rec: dict[str, Any], reason: str) -> None:
    """Failure finalization from any stage; completed states are kept as they are.

    Before run completion: inspection failed, run failed, nothing published.
    After publication (remediation stage): run outcome and report stay as
    published; a running remediation becomes failed.
    """
    st = rec["states"]
    if st["inspection"] in ("pending", "running"):
        st["inspection"] = "failed"
    if rec["run"] is None:
        rec["run"] = "failed"
    if st["publication"] == "pending":
        st["publication"] = "not-published"
    if st["delivery"]["state"] == "pending":
        st["delivery"]["state"] = "skipped"
    if st["remediation"] == "running":
        rec["remediation_log"].append({"fix_id": None, "status": "failed", "files": [], "detail": reason})
        st["remediation"] = "failed"
    rec["failure_reason"] = reason
    rec["finished_at"] = rec["finished_at"] or iso_utc(utcnow())
    add_event(rec, "finalized-failed", reason)


# --------------------------------------------------------------------------- required checks (lens barrier)
#
# A required check is a child lens or check the parent must wait for. It is registered `pending`
# before dispatch and leaves `pending` only through `report_check`, which carries its completion
# plan: what it inspected, what remains and why. Convergence, refutation, proving tests, the report
# and inspection close are all barred while any required check is still pending. A check that
# returns with work remaining is recorded `lost` (partial coverage is never complete).

CHECK_STATES = ("pending", "returned", "failed")
BARRIER_PHASES = ("convergence", "refutation", "proving", "report", "inspection-complete")
_CHECK_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def require_checks(rec: dict[str, Any], keys: Iterable[str]) -> list[str]:
    if rec["states"]["inspection"] != "running":
        raise AuditError("required checks are registered only while inspection is running")
    checks = rec.setdefault("checks", {})
    added = []
    for key in keys:
        if not _CHECK_KEY_RE.fullmatch(key):
            raise AuditError(f"check key {key!r}: 1-64 of [A-Za-z0-9._-]")
        if key in checks:
            continue  # idempotent re-registration
        checks[key] = {"state": "pending", "inspected": [], "remaining": [], "why": "", "evidence": None,
                       "registered_at": iso_utc(utcnow())}
        added.append(key)
    if added:
        add_event(rec, "checks-required", added)
    return added


def _str_list(v: Any, what: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise AuditError(f"completion plan: {what} must be a list of non-empty strings")
    return [x.strip() for x in v]


_ATTEST = re.compile(r"^method-sha256: (?P<path>.+) (?P<sha>[0-9a-f]{64})\s*$", re.M)
# Attestation is hash-only; a child that also quotes the line must quote line k exactly (a quoted
# neighbour line with the right hash is how 1a4-5's addins child misattested 106 as 104).
_ATTEST_LINE = re.compile(
    r"^method-line: (?P<path>.+?) (?P<k>\d+) (?P<sha>[0-9a-f]{64})(?:[ \t]+(?P<quote>.*?))?[ \t\r]*$", re.M)

# Brief fields that must be filled before any child is dispatched (GRRP brief, `ENROLL brief`).
BRIEF_REQUIRED: tuple[str, ...] = ("Scope", "Boundaries", "Done means", "Authority")
_BRIEF_PLACEHOLDERS = frozenset({"unknown", "?", "tbd"})


def method_lines(path: Path) -> list[str]:
    """A method file's lines as the child sees them: UTF-8, newline-split, CR dropped."""
    return [ln.rstrip("\r") for ln in decode_text(path.read_bytes(), str(path)).split("\n")]


def line_sha256(line: str) -> str:
    """Hash of one method line's stripped text: what a child attests after reading line k."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


def brief_done(rec: dict[str, Any], ledger: str) -> dict[str, Any]:
    """Record that a recorded ledger's brief is filled: Scope, Boundaries, Done means and Authority
    must each be present and not a placeholder. Dispatch is refused until this has run."""
    if _norm(ledger) not in {_norm(x) for x in rec["ledgers"]}:
        raise AuditError(f"brief-done: ledger {ledger} is not recorded (run `RUN record --ledger` first)")
    lp = Path(ledger)
    if not lp.is_file():
        raise AuditError(f"brief-done: ledger {ledger} missing")
    text = decode_text(lp.read_bytes(), ledger)
    fields: dict[str, str] = {}
    bad: list[str] = []
    for key in BRIEF_REQUIRED:
        m = re.search(rf"^- {re.escape(key)}:[ \t]*(.*)$", text, re.M)
        val = m.group(1).strip() if m else ""
        if not val or val.lower() in _BRIEF_PLACEHOLDERS:
            bad.append(key)
        fields[key] = val
    if bad:
        raise AuditError(f"brief-done: {ledger} brief field(s) empty: {', '.join(bad)} (fill with `ENROLL brief`)")
    entry = {"ledger": lp.resolve().as_posix(), "fields": fields, "at": iso_utc(utcnow())}
    briefs = [b for b in rec.setdefault("briefs", []) if _norm(b["ledger"]) != _norm(ledger)]
    rec["briefs"] = [*briefs, entry]
    add_event(rec, "brief-done", entry["ledger"])
    return entry


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(path_s: str) -> str:
    return Path(path_s).resolve().as_posix().lower()


def dispatch_shard(rec: dict[str, Any], key: str, shard: str, methods: Sequence[str], outputs: Sequence[str],
                   evidence_root: Path) -> dict[str, Any]:
    """Declare one child before it runs: the method files it must load (hashed now) and the exact
    outputs it must write. Outputs must lie under the per-run evidence dir and not exist yet, so a
    child can neither pick its own location nor be credited with a file written before dispatch."""
    if rec["states"]["inspection"] != "running":
        raise AuditError("dispatch only while inspection is running")
    if not rec.get("briefs"):
        raise AuditError("dispatch: no filled brief recorded; run `RUN brief-done --ledger <path>` first")
    entry = rec.get("checks", {}).get(key)
    if entry is None:
        raise AuditError(f"dispatch {shard!r}: check {key!r} was never required")
    if entry["state"] != "pending":
        raise AuditError(f"dispatch {shard!r}: check {key!r} already {entry['state']}")
    if not shard.strip():
        raise AuditError("dispatch: a shard id is required")
    table = rec.setdefault("dispatch", {})
    if shard in table:
        raise AuditError(f"dispatch: shard {shard!r} already declared")
    if not methods:
        raise AuditError(f"dispatch {shard!r}: at least one --method is required")
    if not outputs:
        raise AuditError(f"dispatch {shard!r}: at least one --output is required")
    meths = []
    for m in methods:
        mp = Path(m)
        if not mp.is_file() or mp.stat().st_size == 0:
            raise AuditError(f"dispatch {shard!r}: method file missing or empty ({m})")
        candidates = [i for i, ln in enumerate(method_lines(mp), start=1) if ln.strip()]
        if not candidates:
            raise AuditError(f"dispatch {shard!r}: method file has no non-blank line ({m})")
        meths.append({"path": mp.resolve().as_posix(), "sha256": file_sha256(mp),
                      "line": secrets.choice(candidates)})
    outs = []
    for o in outputs:
        op = Path(o)
        if not _inside(op, evidence_root):
            raise AuditError(f"dispatch {shard!r}: output {o} is outside the per-run evidence dir {evidence_root}")
        if op.exists():
            raise AuditError(f"dispatch {shard!r}: output {o} already exists (outputs are create-only)")
        outs.append(op.resolve().as_posix())
    table[shard] = {"key": key, "methods": meths, "outputs": outs, "dispatched_at": iso_utc(utcnow())}
    add_event(rec, "dispatch", f"{key}:{shard}")
    return table[shard]


def verify_dispatch(rec: Mapping[str, Any], key: str) -> list[str]:
    """Every declared output of every shard for `key` exists and is non-empty; every Markdown output
    attests each declared method with `method-sha256: <path> <sha>`, and the hash matches both the
    dispatch-time hash and the file now. Each method also carries a dispatch-time random line k: the
    output must attest `method-line: <path> <k> <sha256 of line k stripped>`, which a child can only
    produce by reading the file. Returns the declared outputs."""
    shards = {k: v for k, v in rec.get("dispatch", {}).items() if v["key"] == key}
    if not shards:
        raise AuditError(f"check {key!r}: no dispatched shard; declare each child with `RUN dispatch` first")
    declared: list[str] = []
    for shard, d in shards.items():
        for o in d["outputs"]:
            op = Path(o)
            if not op.is_file() or op.stat().st_size == 0:
                raise AuditError(f"check {key!r}: shard {shard!r} declared output missing or empty ({o})")
            declared.append(_norm(o))
            if op.suffix.lower() != ".md":
                continue
            body = decode_text(op.read_bytes(), o)
            attested = {_norm(m.group("path").strip()): m.group("sha") for m in _ATTEST.finditer(body)}
            lines_att = {(_norm(m.group("path").strip()), int(m.group("k"))): (m.group("sha"), m.group("quote"))
                         for m in _ATTEST_LINE.finditer(body)}
            for meth in d["methods"]:
                got = attested.get(_norm(meth["path"]))
                if got is None:
                    raise AuditError(f"check {key!r}: shard {shard!r} output {o} does not attest method {meth['path']}")
                if got != meth["sha256"]:
                    raise AuditError(f"check {key!r}: shard {shard!r} attests {meth['path']} with a wrong sha256")
                if file_sha256(Path(meth["path"])) != meth["sha256"]:
                    raise AuditError(f"check {key!r}: method {meth['path']} changed after dispatch")
                k = meth.get("line")
                if k is None:
                    continue  # record from before line attestation
                got_line = lines_att.get((_norm(meth["path"]), k))
                if got_line is None:
                    raise AuditError(f"check {key!r}: shard {shard!r} output {o} does not attest line {k} "
                                     f"of method {meth['path']} (`method-line: <path> {k} <sha256>`)")
                line_k = method_lines(Path(meth["path"]))[k - 1]
                sha_k, quote = got_line
                if sha_k != line_sha256(line_k):
                    raise AuditError(f"check {key!r}: shard {shard!r} attests line {k} of {meth['path']} "
                                     "with a wrong line hash")
                if quote and quote.strip().strip("\"'`").strip() != line_k.strip():
                    raise AuditError(f"check {key!r}: shard {shard!r} quoted text does not match line {k} "
                                     f"of {meth['path']} (attestation is hash-only; drop the quote or quote line {k})")
    return declared


def verify_collected_outputs(rec: dict[str, Any]) -> list[str]:
    """Outputs whose sha256 changed (or that vanished) since `RUN check` collected them. Each new
    hit is recorded `defect:output-modified-after-collect`; the run can then only be degraded.
    A child's edits before collect are invisible here: hashing starts at collect."""
    hits: list[str] = []
    for c in rec.get("checks", {}).values():
        for p, sha in (c.get("output_sha256") or {}).items():
            fp = Path(p)
            if not fp.is_file() or file_sha256(fp) != sha:
                hits.append(p)
    new = sorted(set(hits) - set(rec.get("modified_after_collect", [])))
    if new:
        rec["modified_after_collect"] = sorted(set(rec.get("modified_after_collect", [])) | set(new))
        add_event(rec, "defect:output-modified-after-collect", new)
    return sorted(set(hits))


def status_snapshot(toplevel: Path) -> dict[str, dict[str, str | None]]:
    """`git status --porcelain=v1 -z --untracked-files=all` of a live checkout: {rel path: {xy, blob}}.
    blob = git blob SHA of the bytes now (None when deleted or not a regular file)."""
    raw = git(toplevel, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    toks = raw.split("\0")
    out: dict[str, dict[str, str | None]] = {}
    i = 0
    while i < len(toks):
        tok = toks[i]
        i += 1
        if len(tok) < 4:
            continue
        xy, rel = tok[:2], tok[3:]
        if xy[0] in "RC" or xy[1] in "RC":
            i += 1  # the rename/copy source path follows as its own token
        fp = toplevel / rel
        blob = blob_sha_bytes(fp.read_bytes()) if fp.is_file() else None
        out[rel] = {"xy": xy, "blob": blob}
    return out


def _under(n: str, prefix: str) -> bool:
    return n == prefix or n.startswith(prefix.rstrip("/") + "/")


def outputs_audit(rec: dict[str, Any], scan_dirs: Sequence[Path], evidence_root: Path,
                  exclude: Sequence[Path] = (), exclude_prefixes: Sequence[str] = ()) -> dict[str, Any]:
    """Attribute every write the run can see, in two strictly separate scopes.

    Run-owned (defects): files created or modified since run start under `scan_dirs` (declared
    output dirs) that the run never declared — not a dispatched output, artifact, ledger or the
    checklist, not under the evidence dir, not in `exclude` — plus anything `git status --ignored`
    shows in an isolated checkout (`RUN isolate`). A nested git checkout inside a scan dir belongs to
    another session: it is skipped and noted, never walked. Any hit is `defect:undeclared-outputs`.

    Live target roots (observed only): the start-time `git status` snapshot of each root is
    compared with now (status code + blob). Differences not declared by the run are
    `observed:unattributed-target-changes` — listed, never a defect, because concurrent sessions
    and hooks write there. A scan dir equal to or containing a live root is refused (it would
    blame those writers). Also re-verifies collected outputs (`verify_collected_outputs`)."""
    started = _dt.datetime.strptime(rec["started_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=_dt.timezone.utc).timestamp()
    snaps = rec.get("target_snapshots", {})
    live_tops = {_norm(s["toplevel"]) for s in snaps.values()}
    for d in scan_dirs:
        if not d.is_dir():
            raise AuditError(f"outputs-audit: scan dir {d} is not a directory")
        nd = _norm(str(d))
        for top in sorted(live_tops):
            if _under(top, nd):
                raise AuditError(f"outputs-audit: scan dir {d} contains live target root {top}; scan only "
                                 "run-owned dirs (declared output dirs); live roots are compared by snapshot")
    declared = {_norm(o) for d in rec.get("dispatch", {}).values() for o in d["outputs"]}
    declared |= {_norm(a["path"]) for a in rec["artifacts"]}
    declared |= {_norm(x) for x in rec["ledgers"]}
    checklist = rec.get("preflight", {}).get("tools", {}).get("checklist")
    if checklist:
        declared.add(_norm(checklist))
    declared |= {_norm(str(x)) for x in exclude}
    ev = _norm(str(evidence_root))
    prefixes = [_norm(x) for x in exclude_prefixes]  # the run's own state files: <sd>/<task>/<run_id>*
    found: list[str] = []
    nested: list[str] = []
    for d in scan_dirs:
        for cur, dirs, files in os.walk(d):
            keep = []
            for x in dirs:
                nx = _norm(os.path.join(cur, x))
                if x == ".git" or nx == ev or any(_under(nx, p) for p in prefixes):
                    continue
                if (Path(cur) / x / ".git").exists():
                    nested.append((Path(cur) / x).resolve().as_posix())
                    continue
                keep.append(x)
            dirs[:] = keep
            for f in files:
                fp = Path(cur) / f
                n = _norm(str(fp))
                if n in declared or n.startswith(ev + "/") or any(n.startswith(x) for x in prefixes):
                    continue
                try:
                    if fp.stat().st_mtime < started:
                        continue
                except OSError:
                    continue
                found.append(fp.resolve().as_posix())
    for iso in rec.get("isolated", {}).values():
        if iso.get("released"):
            continue
        dest = Path(iso["dest"])
        raw = git(dest, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored").stdout
        toks = raw.split("\0")
        i = 0
        while i < len(toks):
            tok = toks[i]
            i += 1
            if len(tok) < 4:
                continue
            if tok[0] in "RC" or tok[1] in "RC":
                i += 1
            fp = (dest / tok[3:]).resolve()
            if _norm(str(fp)) not in declared:
                found.append(fp.as_posix())
        iso["audited"] = True
    found = sorted(set(found))
    nested = sorted(set(nested))
    modified = verify_collected_outputs(rec)
    scan_norm = [_norm(str(x)) for x in scan_dirs]
    unattributed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, snap in snaps.items():
        top = Path(snap["toplevel"])
        if _norm(str(top)) in seen:
            continue
        seen.add(_norm(str(top)))
        before, after = snap["status"], status_snapshot(top)
        for rel in sorted(set(before) | set(after)):
            if before.get(rel) == after.get(rel):
                continue
            ap = (top / rel).resolve().as_posix()
            n = _norm(ap)
            if n in declared or any(_under(n, s) for s in scan_norm):
                continue
            unattributed.append({"root": name, "path": ap, "before": before.get(rel), "after": after.get(rel)})
    rec.setdefault("outputs_audit", []).append(
        {"at": iso_utc(utcnow()), "scanned": [Path(x).resolve().as_posix() for x in scan_dirs], "undeclared": found,
         "skipped_nested": nested, "unattributed": len(unattributed)})
    rec["unattributed_target_changes"] = unattributed
    if nested:
        add_event(rec, "outputs-audit-skipped-nested", nested)
    if unattributed:
        add_event(rec, "observed:unattributed-target-changes", [u["path"] for u in unattributed])
    if found:
        rec.setdefault("undeclared_outputs", [])
        rec["undeclared_outputs"] = sorted(set(rec["undeclared_outputs"]) | set(found))
        add_event(rec, "defect:undeclared-outputs", found)
    elif not modified:
        add_event(rec, "outputs-audit-clean", len(scan_dirs))
    return {"undeclared": found, "defect": bool(found or modified), "unattributed": unattributed,
            "skipped_nested": nested, "modified_after_collect": modified}


def results_markdown(rec: Mapping[str, Any], run_log: str | None) -> str:
    """RESULTS.md for `--deliver file`: the run's records trail, with the gate verdict verbatim."""
    reports = [a["path"] for a in rec["artifacts"] if a.get("kind") == "report"]
    if run_log is None:
        logs = [a["path"] for a in rec["artifacts"] if a.get("kind") == "run-log"]
        run_log = logs[-1] if logs else None
    lines = [
        f"# Results: {rec['run_id']}",
        "",
        f"- run_id: {rec['run_id']}",
        f"- task: {rec['task_id']}",
        f"- run outcome: {rec['run']}",
        f"- finished_at: {rec['finished_at']}",
        f"- run log: {run_log or 'not recorded'}",
        "",
        "## Published reports",
        "",
        *([f"- {r}" for r in reports] or ["- none"]),
        "",
        "## Ledgers",
        "",
        *([f"- {x}" for x in rec["ledgers"]] or ["- none"]),
        "",
        *(["## Outputs modified after collect (defect)", "",
           *[f"- {x}" for x in rec["modified_after_collect"]], ""] if rec.get("modified_after_collect") else []),
        "## Unattributed target changes (informational)",
        "",
        "Live target-root changes since run start that this run did not declare (concurrent sessions,",
        "hooks); not a defect.",
        "",
        *([f"- {u['root']}: {u['path']}" for u in rec.get("unattributed_target_changes", [])] or ["- none"]),
        "",
        "## Gate verdict (verbatim)",
        "",
        "```json",
        json.dumps(rec["gate"], indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    return "\n".join(lines)


def checkout_revision(repo: Path, rev: str, dest: Path) -> None:
    """A detached checkout of `rev` at `dest` that leaves `repo` untouched: `git clone --shared
    --no-checkout` (objects borrowed via alternates), the source's `refs/remotes/origin/*` copied in
    so base-ref lookups resolve as they would in place, then `checkout --detach rev`. A real git
    repository, because checkers such as G4 measure through `git ls-files`."""

    def git(*args: str, cwd: Path | None = None) -> None:
        cp = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if cp.returncode != 0:
            raise AuditError(f"git {' '.join(args[:2])} for {repo}@{rev[:12]}: {cp.stderr.strip()}")

    git("clone", "-q", "--shared", "--no-checkout", str(repo), str(dest))
    git("fetch", "-q", "--no-tags", str(repo), "+refs/remotes/origin/*:refs/remotes/origin/*", cwd=dest)
    git("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", rev, cwd=dest)


def report_check(rec: dict[str, Any], key: str, state: str, completion: Mapping[str, Any] | None = None,
                 reason: str = "", evidence_root: Path | None = None) -> dict[str, Any]:
    """A child's return. Refuses a return for an unregistered key, a second return, an empty plan,
    unexplained remaining work, or missing evidence."""
    if rec["states"]["inspection"] != "running":
        raise AuditError("checks report only while inspection is running")
    entry = rec.get("checks", {}).get(key)
    if entry is None:
        raise AuditError(f"check {key!r} was never required; register it before dispatch")
    if entry["state"] != "pending":
        raise AuditError(f"check {key!r} already {entry['state']}")
    if state == "failed":
        if not reason.strip():
            raise AuditError(f"check {key!r} failed: a reason is required")
        entry.update(state="failed", why=reason.strip(), returned_at=iso_utc(utcnow()))
        set_coverage(rec, key, "lost", f"failed: {reason.strip()}")
    elif state == "returned":
        plan = completion or {}
        inspected = _str_list(plan.get("inspected"), "inspected")
        if not inspected:
            raise AuditError(f"check {key!r} returned with nothing inspected (premature return)")
        remaining = _str_list(plan.get("remaining", []), "remaining")
        why = str(plan.get("why", "")).strip()
        if remaining and not why:
            raise AuditError(f"check {key!r}: {len(remaining)} item(s) remain with no why")
        declared = verify_dispatch(rec, key)
        evidence = str(plan.get("evidence", "")).strip()
        require_artifact(evidence, evidence_root, f"check {key!r}: evidence file")
        artifacts = _str_list(plan.get("artifacts", []), "artifacts")
        for art in artifacts:
            require_artifact(art, evidence_root, f"check {key!r}: declared artifact")
        findings = str(plan.get("findings", "") or "").strip()
        for claimed in [evidence, *artifacts, *([findings] if findings else [])]:
            if _norm(claimed) not in declared:
                raise AuditError(f"check {key!r}: {claimed} is not a declared output of any dispatched shard")
        n_findings = None
        if findings:
            n_findings = len(validate_findings(require_artifact(findings, evidence_root, f"check {key!r}: findings file")))
        entry.update(state="returned", inspected=inspected, remaining=remaining, why=why,
                     evidence=evidence, artifacts=artifacts, findings=findings or None,
                     findings_count=n_findings, returned_at=iso_utc(utcnow()),
                     output_sha256={o: file_sha256(Path(o)) for d in rec.get("dispatch", {}).values()
                                    if d["key"] == key for o in d["outputs"]})
        if remaining:
            set_coverage(rec, key, "lost", f"partial: {len(remaining)} remaining ({why})")
        else:
            set_coverage(rec, key, "complete", f"{len(inspected)} inspected")
    else:
        raise AuditError(f"check state must be returned|failed, not {state!r}")
    add_event(rec, f"check-{state}", key)
    return entry


def pending_checks(rec: Mapping[str, Any]) -> list[str]:
    return [k for k, v in rec.get("checks", {}).items() if v["state"] == "pending"]


def barrier(rec: Mapping[str, Any], phase: str) -> None:
    if phase not in BARRIER_PHASES:
        raise AuditError(f"unknown phase {phase!r}")
    pending = pending_checks(rec)
    if pending:
        raise AuditError(f"{phase} blocked: required checks not returned: {', '.join(pending)}")


# --------------------------------------------------------------------------- slop reuse (rule 4)


def reuse_check(
    sweep_report: Path,
    state_dir: Path,
    info: RootInfo,
    weekly_baseline_rev: str | None,
) -> tuple[bool, list[str]]:
    """Rule 4. Returns (reuse, reasons). Any reason means: run /slop-audit nested.

    Window rule: a full-scope sweep (baseline_rev null) covers any weekly window;
    an incremental sweep covers the weekly window only if its baseline is at or
    before the weekly baseline, and never covers a full-scope weekly.
    """
    reasons: list[str] = []
    try:
        fm = parse_front_matter(sweep_report.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError as exc:
        return False, [f"report unreadable: {exc}"]
    if not fm:
        return False, ["report has no parseable front matter"]
    if fm["task_id"] != "slop-audit-sweep":
        reasons.append(f"report task_id is {fm['task_id']}, not slop-audit-sweep")
    entries: dict[str, Any] = fm["roots"]
    if list(entries) != [info.name]:
        reasons.append(f"scope differs: report roots {sorted(entries)} != ['{info.name}']")
    entry = entries.get(info.name) or {}
    if entry.get("identity") != info.identity:
        reasons.append(f"scope differs: identity {entry.get('identity')} != {info.identity}")
    if entry.get("path_arg") != info.path_arg:
        reasons.append(f"scope differs: path_arg {entry.get('path_arg')} != {info.path_arg}")
    if entry.get("head_rev") != info.head_rev:
        reasons.append("head_rev differs from current HEAD")
    sweep_base = entry.get("baseline_rev")
    if sweep_base is not None:
        if weekly_baseline_rev is None:
            reasons.append("window narrower: sweep is incremental, weekly is full-scope")
        elif sweep_base != weekly_baseline_rev and not is_ancestor(info.path, sweep_base, weekly_baseline_rev):
            reasons.append("window narrower: sweep baseline is not at or before the weekly baseline")
    rec_path = record_path(state_dir, "slop-audit-sweep", str(fm["run_id"]))
    if not rec_path.exists():
        reasons.append("run record missing")
    else:
        try:
            rec = load_record(rec_path)
        except (OSError, json.JSONDecodeError, AuditError) as exc:
            reasons.append(f"run record unreadable: {exc}")
        else:
            if rec.get("run") != "ok":
                reasons.append(f"run record says run: {rec.get('run')}")
            cov = rec.get("coverage", {}).get(info.name)
            if not cov or cov.get("status") != "complete":
                reasons.append(f"root coverage not complete: {cov}")
    return (not reasons), reasons


# --------------------------------------------------------------------------- autofix fixes (rule 8)


def fixes_dir(state_dir: Path, task_id: str, run_id: str) -> Path:
    return state_dir / task_id / run_id / "fixes"


def _check_fix_id(fix_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", fix_id):
        raise AuditError(f"bad fix_id {fix_id!r}")


def fix_snapshot(root: Path, fdir: Path, fix_id: str, rel_files: Sequence[str]) -> dict[str, Any]:
    """Record pre-fix blobs (and a copy) of every file the fix will touch."""
    _check_fix_id(fix_id)
    base = fdir / fix_id
    if base.exists():
        raise AuditError(f"fix {fix_id} already snapshotted")
    if not rel_files:
        raise AuditError("a fix must name the files it touches")
    targets = [(rel, resolve_inside(root, rel)) for rel in rel_files]  # refuse before writing
    files = []
    (base / "pre").mkdir(parents=True)
    for i, (rel, target) in enumerate(targets):
        sha = blob_sha(target)
        copy = None
        if sha is not None:
            copy = f"pre/{i}.bin"
            shutil.copyfile(target, base / copy)
        files.append({"path": rel.replace("\\", "/"), "pre_sha": sha, "post_sha": None, "pre_copy": copy})
    manifest = {"fix_id": fix_id, "state": "snapshotted", "files": files}
    atomic_write_json(base / "manifest.json", manifest)
    return manifest


def fix_post(root: Path, fdir: Path, fix_id: str) -> dict[str, Any]:
    """Record post-fix blobs after the fix is applied."""
    base = fdir / fix_id
    manifest = read_json(base / "manifest.json")
    if manifest["state"] != "snapshotted":
        raise AuditError(f"fix {fix_id} is {manifest['state']}, not snapshotted")
    for f in manifest["files"]:
        f["post_sha"] = blob_sha(resolve_inside(root, f["path"]))
    manifest["state"] = "applied"
    atomic_write_json(base / "manifest.json", manifest)
    return manifest


def fix_rollback(root: Path, fdir: Path, fix_id: str) -> dict[str, Any]:
    """Restore pre-fix content only if every file still equals its post-fix blob.

    All-or-nothing: one concurrent change refuses the whole fix and every file
    is left as found. Returns the remediation_log entry; a refusal is a
    remediation failure, never a success.
    """
    base = fdir / fix_id
    manifest = read_json(base / "manifest.json")
    if manifest["state"] != "applied":
        raise AuditError(f"fix {fix_id} is {manifest['state']}; rollback needs applied")
    targets = [(f, resolve_inside(root, f["path"])) for f in manifest["files"]]
    changed = [f["path"] for f, t in targets if blob_sha(t) != f["post_sha"]]
    if changed:
        manifest["state"] = "rollback-refused"
        atomic_write_json(base / "manifest.json", manifest)
        return {
            "fix_id": fix_id,
            "status": "rollback-refused (concurrent change)",
            "files": changed,
            "detail": "current blob differs from recorded post-fix blob; left as found",
        }
    mismatched: list[str] = []
    for f, t in targets:
        if f["pre_sha"] is None:
            if t.exists():
                t.unlink()
        else:
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / f["pre_copy"], t)
        if blob_sha(t) != f["pre_sha"]:
            mismatched.append(f["path"])
    manifest["state"] = "rollback-failed" if mismatched else "reverted"
    atomic_write_json(base / "manifest.json", manifest)
    if mismatched:
        return {"fix_id": fix_id, "status": "rollback-failed", "files": mismatched,
                "detail": "restored blob does not equal pre-fix blob"}
    return {"fix_id": fix_id, "status": "reverted", "files": [f["path"] for f in manifest["files"]],
            "detail": None}
