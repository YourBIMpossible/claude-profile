"""SessionStart/SessionEnd hook: local-first, read-only closure audit.

WHY THIS EXISTS
  Design docs and decision reasoning were repeatedly landing as uncommitted,
  unpushed local files (see 2026-08-19 session: four Workspace strategy docs
  sat local-only with no commit path). A worktree deleted in that state loses
  the work permanently. This hook makes that state visible every session
  instead of relying on remembering to ask "did everything get committed?"

CONTRACT (non-negotiable, per owner spec 2026-08-19)
  - Local-first only: git plumbing + filesystem reads. No git push, no git
    commit, no `gh`, no GitHub API, no network call of any kind.
  - Never mutates repo state. Never auto-commits, auto-pushes, deletes, or
    renames anything.
  - Never blocks session start or end. Any parse/read failure is swallowed
    and the hook exits 0 emitting nothing (fail open, same convention as
    northstar_inject.py in this same hooks directory).
  - No hard-inescapable loop: this hook cannot set a blocking decision even
    in principle — it never emits a "decision" field, only advisory text.

VISIBILITY DESIGN NOTE
  SessionEnd hook output visibility is not reliably documented (the harness
  may already be tearing down the UI). SessionStart output visibility IS
  proven in this exact hooks directory (northstar_inject.py's <northstar-*>
  blocks reliably surface as system-reminders). So this hook is registered
  on BOTH events but treats SessionStart as the guaranteed-visible surface:
  git state is current regardless of when it's checked, so "audit at the
  start of the next session" catches everything a same-session SessionEnd
  audit would, with a delivery mechanism that's actually proven to work.
  SessionEnd registration is kept as a harmless best-effort second chance.

OUTPUT
  Raw UTF-8 text wrapped in <closure-audit>...</closure-audit>, emitted the
  same way northstar_inject.py emits — not JSON, not a blocking decision.
"""
import fnmatch
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
CONFIG_PATH = HOOKS_DIR / "closure_audit_repos.json"
GIT_TIMEOUT = 8  # seconds, per git call — this hook must never hang a session
OVERALL_GIT_BUDGET = 10.0  # seconds, wall-clock ceiling for ALL git calls combined.
# The outer harness (settings.json hook "timeout": 15) kills this whole process
# and discards its output SILENTLY if it runs past 15s — with ~15+ live worktrees,
# sequential per-worktree `git status` calls can add up past that ceiling even
# though each individual call is well inside GIT_TIMEOUT. Running calls
# concurrently keeps total wall time bounded near this budget regardless of
# worktree count; anything that still doesn't finish in time is reported as
# ATTENTION REQUIRED (see _run_concurrently / build_report), never silently
# dropped — a truncated run must never look identical to a clean one.
MAX_CONCURRENCY = 24  # generous: these are I/O-bound subprocess calls, not CPU work


def emit(text: str) -> None:
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        pass


def read_event() -> str:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if isinstance(payload, dict):
            return str(payload.get("hook_event_name") or "")
    except Exception:
        pass
    return ""


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def run_git(repo: str, *args: str) -> "list[str] | None":
    """Run one git command. Returns stdout lines on success, None on ANY
    failure (non-zero exit, GIT_TIMEOUT, git missing, any exception).

    None vs [] is load-bearing: callers must be able to tell "git said the
    answer is empty" from "git never answered". Collapsing both to [] made a
    failed `git status` render as "clean. No action needed." — the exact
    silent-loss this hook exists to prevent (2026-08-24 slop-audit HIGH)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.splitlines()
    except Exception:
        return None


def list_worktrees(repo_path: str) -> "list[dict]":
    """Parse `git worktree list --porcelain`. Falls back to [repo_path] alone
    if the repo can't be read (e.g. path doesn't exist) — reported as a
    single unreadable entry rather than silently vanishing from the audit."""
    lines = run_git(repo_path, "worktree", "list", "--porcelain")
    if not lines:
        return [{"path": repo_path, "branch": None, "detached": False, "unreadable": True}]
    out, cur = [], {}
    for line in lines:
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):].strip(), "branch": None, "detached": False}
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref.replace("refs/heads/", "")
        elif line.strip() == "detached":
            cur["detached"] = True
    if cur:
        out.append(cur)
    return out or [{"path": repo_path, "branch": None, "detached": False, "unreadable": True}]


def parse_status(wt_path: str) -> dict:
    """Parse `git status --porcelain=v2 --branch` for one worktree."""
    # --untracked-files=all: without it, a brand-new untracked directory (e.g. a
    # fresh wiki/ folder) collapses to one "wiki/" line instead of listing the
    # files inside it, which would silently drop every design doc in that
    # directory from the audit — the exact "no silent loss" guarantee this
    # hook exists to uphold (caught 2026-08-19 dogfooding against wiki/ itself).
    lines = run_git(wt_path, "status", "--porcelain=v2", "--branch", "--untracked-files=all")
    info = {
        "branch": None, "detached": False, "upstream": None,
        "ahead": 0, "behind": 0, "tracked_changed": [], "untracked": [],
        # True when the status call itself failed (timeout, index.lock
        # contention from a concurrent session, non-zero exit). The defaults
        # above then describe NOTHING — audit_worktree must report the
        # failure, never read them as "clean".
        "status_failed": lines is None,
    }
    if lines is None:
        return info
    for line in lines:
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            info["detached"] = (head == "(detached)")
            info["branch"] = None if info["detached"] else head
        elif line.startswith("# branch.upstream "):
            info["upstream"] = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            try:
                parts = line[len("# branch.ab "):].split()
                info["ahead"] = int(parts[0].lstrip("+"))
                info["behind"] = int(parts[1].lstrip("-"))
            except Exception:
                pass
        elif line.startswith("1 ") or line.startswith("u "):
            path = line.split(" ", 8)[-1]
            info["tracked_changed"].append(path)
        elif line.startswith("2 "):
            path = line.split(" ", 9)[-1].split("\t")[0]
            info["tracked_changed"].append(path)
        elif line.startswith("? "):
            info["untracked"].append(line.split(" ", 1)[-1])
    return info


def is_design_doc(path: str, globs: "list[str]") -> bool:
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, g) for g in globs)


def is_draft(path: str, markers: "list[str]") -> bool:
    lower = path.lower()
    return any(lower.endswith(m) for m in markers)


def classify_worktree_location(wt_path: str, repo_root: str) -> "dict | None":
    """Return None when `wt_path` is canonical for `repo_root`: either the
    repo's own primary checkout, or a lane under its `.claude/worktrees/`
    convention (WORKFLOW.md / claude-profile CLAUDE.md). Otherwise return a
    dict flagging it as out-of-convention, with `under_temp` telling whether
    it sits inside the process's TEMP/TMP directory — the specific pattern
    seen from a peer session's ad hoc `git worktree add` (2026-08-21
    concurrent-mutation investigation: worktrees named `main-check` /
    `t6-rebase` registered under %TEMP%, invisible to this convention).

    Read-only: string/path comparison only, no filesystem or git call.
    """
    norm_path = os.path.normcase(os.path.normpath(wt_path))
    norm_root = os.path.normcase(os.path.normpath(repo_root))
    if norm_path == norm_root:
        return None

    worktrees_root = os.path.normcase(os.path.normpath(os.path.join(repo_root, ".claude", "worktrees")))
    if norm_path == worktrees_root or norm_path.startswith(worktrees_root + os.sep):
        return None

    under_temp = False
    for var in ("TEMP", "TMP"):
        temp_dir = os.environ.get(var)
        if not temp_dir:
            continue
        norm_temp = os.path.normcase(os.path.normpath(temp_dir))
        if norm_path == norm_temp or norm_path.startswith(norm_temp + os.sep):
            under_temp = True
            break

    return {"under_temp": under_temp}


def _run_concurrently(jobs: "list", deadline: float) -> "dict[int, object]":
    """Run zero-arg callables `jobs` on daemon threads, stopping new
    submissions once `deadline` (a time.monotonic() timestamp) has passed.
    Returns {index: result} only for jobs that completed in time — a missing
    index means "not checked" and the caller must surface that as ATTENTION
    REQUIRED, never treat it as clean.

    Daemon threads (not concurrent.futures.ThreadPoolExecutor) are deliberate:
    ThreadPoolExecutor registers an atexit hook that blocks process exit until
    every worker thread finishes, which would reintroduce the exact silent
    over-15s hang this function exists to remove. Daemon threads are simply
    abandoned at process exit — harmless here since each is just waiting on a
    subprocess that has its own GIT_TIMEOUT ceiling.
    """
    results: "dict[int, object]" = {}
    lock = threading.Lock()
    sem = threading.Semaphore(MAX_CONCURRENCY)
    threads = []

    def runner(i, fn):
        try:
            r = fn()
        except Exception:
            r = None
        with lock:
            results[i] = r
        sem.release()

    for i, fn in enumerate(jobs):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not sem.acquire(timeout=max(0.0, remaining)):
            break
        t = threading.Thread(target=runner, args=(i, fn), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(timeout=remaining)

    return results


def audit_worktree(
    name: str,
    wt: dict,
    cfg: dict,
    st: "dict | None" = None,
    truncated: bool = False,
    repo_root: "str | None" = None,
) -> "list[dict]":
    """Return a list of finding dicts for one worktree/repo. Empty = clean.

    `st` is a pre-fetched parse_status() result (fetched concurrently by
    build_report to stay inside OVERALL_GIT_BUDGET). `truncated=True` means
    the audit ran out of its time budget before this worktree's status could
    be checked — that must surface as an explicit finding, not silence.
    `repo_root` is the configured repo path, used only for the out-of-
    convention location check below (registry-only, no status needed).
    """
    findings = []
    path = wt["path"]
    if wt.get("unreadable"):
        findings.append({
            "severity": "ATTENTION REQUIRED",
            "text": f"{path} — repo path configured but not readable by git.",
            "action": "Confirm the path in closure_audit_repos.json is correct.",
        })
        return findings

    # Convention check runs off `git worktree list` alone (already fetched in
    # stage 1), so it still fires even when this worktree's `git status` was
    # truncated by the time budget below. WARNING only, per owner spec — this
    # flags visibility, it never blocks and never touches another session's
    # worktree.
    if repo_root is not None:
        loc = classify_worktree_location(path, repo_root)
        if loc is not None:
            where = "under %TEMP%" if loc["under_temp"] else "outside the repo's worktree convention"
            head_state = "detached HEAD" if wt.get("detached") else (wt.get("branch") or "(unknown branch)")
            findings.append({
                "severity": "WARNING",
                "text": f"{path} [{head_state}] — out-of-convention worktree ({where}).",
                "action": "Out-of-convention worktree. Its owning session should use "
                          "`.claude/worktrees/<lane>` or clean it up after completion. "
                          "Do not modify another session's worktree.",
            })

    if truncated:
        findings.append({
            "severity": "ATTENTION REQUIRED",
            "text": f"{path} — status check skipped: audit exceeded its {OVERALL_GIT_BUDGET:.0f}s "
                    "time budget (too many worktrees or slow git). State unknown, not confirmed clean.",
            "action": "Re-run the audit, or check this worktree manually with `git status`.",
        })
        return findings

    # `git status` itself failed (timeout, index.lock held by a concurrent
    # session, transient git error). Same contract as truncation: an
    # unanswered check must surface, never render as clean (2026-08-24
    # slop-audit HIGH — a failed status previously printed "clean. No
    # action needed.", byte-identical to a genuinely clean worktree).
    if st.get("status_failed"):
        findings.append({
            "severity": "ATTENTION REQUIRED",
            "text": f"{path} — `git status` failed (timeout, lock contention, or git error). "
                    "State unknown, not confirmed clean.",
            "action": "Re-run the audit, or check this worktree manually with `git status`.",
        })
        return findings

    globs = cfg.get("design_doc_globs", ["*.md"])
    markers = cfg.get("draft_markers", [".draft.md"])

    if st["detached"]:
        findings.append({
            "severity": "ATTENTION REQUIRED",
            "text": f"{path} — detached HEAD.",
            "action": "Check out a branch before continuing work here.",
        })

    if st["upstream"] and st["ahead"] > 0:
        findings.append({
            "severity": "ATTENTION REQUIRED",
            "text": f"{path} [{st['branch']}] — {st['ahead']} unpushed commit(s) ahead of {st['upstream']}.",
            "action": "Push intentionally (or confirm this branch is deliberately parked).",
        })
    elif not st["upstream"] and st["branch"] not in (None, "main", "master"):
        findings.append({
            "severity": "WARNING",
            "text": f"{path} [{st['branch']}] — local branch has no upstream tracking.",
            "action": "Push with -u once ready, or confirm it's deliberately local-only.",
        })

    # Tracks whether THIS worktree's file-level findings (below) already
    # covered its edits, so the catch-all a few lines down knows whether it
    # would be reporting something new — independent of any earlier finding
    # (detached HEAD, ahead-of-upstream) already in `findings`. Scanning the
    # cumulative `findings` list for that check was the bug: an unrelated
    # ATTENTION REQUIRED entry (e.g. detached HEAD) would silently suppress
    # the "has local edits" WARNING even when no file-level finding actually
    # covered those edits.
    file_level_attention = False

    for p in st["untracked"]:
        if is_draft(p, markers):
            findings.append({
                "severity": "WARNING",
                "text": f"{path} — untracked draft: {p}",
                "action": "Keep as an explicitly marked draft (intentionally local-only).",
            })
        elif is_design_doc(p, globs):
            findings.append({
                "severity": "ATTENTION REQUIRED",
                "text": f"{path} — untracked design/decision doc: {p}",
                "action": "Commit and push intentionally, or rename to *.draft.md if not ready.",
            })
            file_level_attention = True
        # non-design untracked files (build output, scratch, etc.) are not
        # reported — the "no silent loss" guarantee is about durable
        # knowledge, not every stray artifact.

    for p in st["tracked_changed"]:
        if is_design_doc(p, globs) and not is_draft(p, markers):
            findings.append({
                "severity": "ATTENTION REQUIRED",
                "text": f"{path} — modified design/decision doc not committed: {p}",
                "action": "Commit and push intentionally.",
            })
            file_level_attention = True

    if (st["tracked_changed"] or st["untracked"]) and not file_level_attention:
        findings.append({
            "severity": "WARNING",
            "text": f"{path} [{st['branch']}] — has local edits (non-design or already-drafted).",
            "action": "Commit when ready; no design knowledge currently at risk here.",
        })

    return findings


def build_report(cfg: dict) -> str:
    deadline = time.monotonic() + OVERALL_GIT_BUDGET
    repos = cfg.get("repos", [])

    valid_repos = []  # (name, path)
    missing_sections = []
    for r in repos:
        name, path = r["name"], r["path"]
        if not os.path.isdir(path):
            missing_sections.append(f"## {name}\n  ATTENTION REQUIRED: configured path not found: {path} — Fix closure_audit_repos.json.")
        else:
            valid_repos.append((name, path))

    # Stage 1: `git worktree list` for every valid repo, concurrently.
    wt_lists = _run_concurrently(
        [(lambda p=path: list_worktrees(p)) for _, path in valid_repos], deadline
    )

    flat = []  # (repo_name, wt_dict) — every worktree across every repo, flattened
    repo_ranges = {}  # repo_name -> (start, end) slice into `flat`, or None if not listed in time
    for i, (name, _) in enumerate(valid_repos):
        wts = wt_lists.get(i)
        if wts is None:
            repo_ranges[name] = None
            continue
        start_idx = len(flat)
        flat.extend((name, wt) for wt in wts)
        repo_ranges[name] = (start_idx, len(flat))

    # Stage 2: `git status` for every worktree, concurrently, sharing the SAME
    # deadline as stage 1 (not a fresh budget) so a slow stage 1 eats into
    # stage 2's time instead of the two stacking to roughly 2x the ceiling.
    statuses = _run_concurrently(
        [(lambda wt=wt: parse_status(wt["path"])) for _, wt in flat], deadline
    )

    sections = list(missing_sections)
    total_attn = len(missing_sections)
    total_warn = 0
    truncated_repos = 0
    truncated_worktrees = 0

    repo_paths = dict(valid_repos)

    for name, _ in valid_repos:
        rng = repo_ranges[name]
        if rng is None:
            sections.append(
                f"## {name}\n  ATTENTION REQUIRED: worktree listing skipped — audit exceeded its "
                f"{OVERALL_GIT_BUDGET:.0f}s time budget. State unknown, not confirmed clean."
            )
            total_attn += 1
            truncated_repos += 1
            continue
        s_idx, e_idx = rng
        lines = [f"## {name}"]
        for idx in range(s_idx, e_idx):
            _, wt = flat[idx]
            st = statuses.get(idx)
            wt_truncated = st is None and not wt.get("unreadable")
            if wt_truncated:
                truncated_worktrees += 1
            wt_findings = audit_worktree(
                name, wt, cfg, st=st, truncated=wt_truncated, repo_root=repo_paths.get(name)
            )
            if not wt_findings:
                lines.append(f"  - {wt['path']} [{wt.get('branch') or '(detached)'}] — clean. No action needed.")
                continue
            for f in wt_findings:
                if f["severity"] == "ATTENTION REQUIRED":
                    total_attn += 1
                else:
                    total_warn += 1
                lines.append(f"  [{f['severity']}] {f['text']}\n      -> {f['action']}")
        sections.append("\n".join(lines))

    verdict = (
        f"Verdict: {total_attn} ATTENTION REQUIRED, {total_warn} WARNING across "
        f"{len(repos)} configured repo(s). Nothing here blocks closing this session — "
        "items above are what to externalize before deleting any worktree that holds them."
    )
    if truncated_repos or truncated_worktrees:
        verdict += (
            f" TRUNCATED: {truncated_repos} repo(s) and {truncated_worktrees} worktree(s) "
            "were not checked in time — treat this run as incomplete, not clean."
        )
    header = (
        "Local-first closure audit (read-only: git status/worktree list + filesystem "
        "only — no commit, no push, no GitHub calls). Config: "
        f"{CONFIG_PATH.name}."
    )
    return header + "\n\n" + "\n\n".join(sections) + "\n\n" + verdict


def main() -> None:
    event = read_event()
    if event not in ("SessionStart", "SessionEnd"):
        sys.exit(0)
    cfg = load_config()
    report = build_report(cfg)
    emit(f"<closure-audit>\n{report}\n</closure-audit>")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory hook: fail open, never break session start/end
