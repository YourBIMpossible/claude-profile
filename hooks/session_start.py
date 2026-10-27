"""SessionStart: keep this machine on the merged profile, then tell the session its roots.

Runs from the clone (wherever it lives - the path is derived from this file), on every
platform, for startup|resume|clear|compact:

  1. (not on compact) `git pull --ff-only` in this clone, under a non-blocking lock:
     Claude starts many processes at once and all of them run this hook against the
     same checkout, so the first one pulls and the rest log "pull skipped (in flight)".
  2. (same lock holder) re-apply the settings merge (tools/profile_setup.py) to
     ~/.claude/settings.json, so a merged change to settings.template.json reaches this
     machine without re-running bootstrap. Claude Code reads hook registrations when a
     session starts, so a template change applies from the next session on; changed
     hook *code* applies at once, since hooks run from this clone. The previous file is
     kept as settings.json.pre-profile-sync. An unusable settings.json is left alone.
  3. Print the machine roots ({profile}, {repos}, {tools}, {reports}, {mailbox}) as
     session context, plus a warning when this clone is not on its default branch or
     has uncommitted hook code - hooks run from here, so that state runs on every
     prompt.

Best effort, never blocks: every path exits 0. Record: ~/.claude/hooks/last-run.log
(same vocabulary as before: "pull ok", "pull skipped (in flight)", "pull failed (exit
N)", "git missing", "pull threw"; plus "settings synced" / "settings sync skipped"). A
failed pull or settings sync is also named under "Profile warnings" in the session.
Opt-outs: CLAUDE_PROFILE_PULL=0 (no pull), CLAUDE_PROFILE_SYNC=0 (no settings sync).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROFILE_ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_S = 10


def state_dir() -> Path:
    return Path.home() / ".claude" / "hooks"


def _lock(f, blocking: bool) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def log(message: str) -> None:
    """One line under a short lock; logging must never be what fails this hook."""
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} - {message}\n".encode("utf-8")
        with open(d / "last-run.log.log-lock", "a+b") as lk:
            deadline = time.monotonic() + 2
            while not _lock(lk, blocking=False):
                if time.monotonic() > deadline:
                    return
                time.sleep(0.02)
            try:
                with open(d / "last-run.log", "ab") as f:
                    f.write(line)
            finally:
                _unlock(lk)
    except Exception:
        pass


def git(*args: str, timeout: float = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(PROFILE_ROOT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


PULL_SEE_LOG = "this clone may be out of date - see ~/.claude/hooks/last-run.log"


def pull() -> str | None:
    """`git pull --ff-only` in this clone; returns a warning for the session, or None."""
    try:
        proc = git("pull", "--ff-only")
    except FileNotFoundError:
        log(f"git missing: git not found on PATH [{PROFILE_ROOT}]")
        return f"the profile was not pulled: git was not found on PATH; {PULL_SEE_LOG}"
    except subprocess.TimeoutExpired:
        log(f"pull failed (exit 124): timed out after {GIT_TIMEOUT_S}s [{PROFILE_ROOT}]")
        return f"the profile pull failed (exit 124): timed out after {GIT_TIMEOUT_S}s; {PULL_SEE_LOG}"
    out = " | ".join(l for l in (proc.stdout + proc.stderr).splitlines() if l.strip())
    if proc.returncode == 0:
        log(f"pull ok: {out}")
        return None
    log(f"pull failed (exit {proc.returncode}): {out} [{PROFILE_ROOT}]")
    return f"the profile pull failed (exit {proc.returncode}); {PULL_SEE_LOG}"


def sync_settings() -> str | None:
    """Re-apply the settings merge; returns a warning for the session, or None."""
    sys.path.insert(0, str(PROFILE_ROOT / "tools"))
    import machine_config  # imported after the pull, so the pulled code runs
    import profile_setup as ps
    try:
        cfg = machine_config.load(PROFILE_ROOT)
        plan = ps.plan_settings(cfg, machine_config.claude_dir() / "settings.json", PROFILE_ROOT)
    except (ps.Refuse, machine_config.ConfigError) as exc:
        log(f"settings sync skipped: {exc}")
        return f"Profile settings were not synced: {exc}"
    if not plan.needed:
        return None
    ps.write_settings(plan, rolling_backup=True)
    summary = ps.plan_lines(plan.changes)[0]
    log(f"settings synced: {summary}")
    return None


def clone_warnings() -> list[str]:
    """Hooks run from this clone: say so when it is not the merged default branch."""
    out = []
    try:
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD", timeout=5).stdout.strip()
        default = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", timeout=5).stdout.strip()
        default = default.split("/", 1)[1] if "/" in default else default
        if default and branch != default:
            out.append(f"the profile clone {PROFILE_ROOT} is on '{branch or 'a detached HEAD'}', not '{default}': "
                       "its hooks run on every prompt - do feature work in a worktree")
        dirty = git("status", "--porcelain", "--", "hooks", "vendor", "tools", "settings.template.json",
                    timeout=5).stdout.strip()
        if dirty:
            out.append(f"the profile clone {PROFILE_ROOT} has uncommitted hook/tool changes, which run on every prompt")
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    source = payload.get("source") if isinstance(payload, dict) else None
    notes: list[str] = []
    if source != "compact":
        lock_path = state_dir() / "last-run.log.pull-lock"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "a+b") as lk:
                if not _lock(lk, blocking=False):
                    log(f"pull skipped (in flight) [{PROFILE_ROOT}]")
                else:
                    try:
                        if os.environ.get("CLAUDE_PROFILE_PULL", "1") != "0":
                            warning = pull()
                            if warning:
                                notes.append(warning)
                        if os.environ.get("CLAUDE_PROFILE_SYNC", "1") != "0":
                            try:
                                warning = sync_settings()
                            except Exception as exc:
                                log(f"settings sync failed: {type(exc).__name__}: {exc}")
                                warning = f"Profile settings sync failed ({type(exc).__name__}); see ~/.claude/hooks/last-run.log"
                            if warning:
                                notes.append(warning)
                    finally:
                        _unlock(lk)
        except Exception as exc:  # never block session start
            log(f"pull threw: {type(exc).__name__}: {exc} [{PROFILE_ROOT}]")
    notes += clone_warnings()

    try:
        sys.path.insert(0, str(PROFILE_ROOT / "tools"))
        import machine_config
        cfg_file = machine_config.config_path()
        roots = machine_config.describe(machine_config.load(PROFILE_ROOT), PROFILE_ROOT)
        text = f"Machine roots for this profile (from {cfg_file.as_posix()}): {roots}"
        if not cfg_file.exists():
            notes.append(f"{cfg_file.as_posix()} is missing; roots above are auto-detected - run bootstrap once")
    except Exception as exc:
        text = f"Machine roots unavailable ({type(exc).__name__}: {exc}); see ~/.claude/profile-machine.json"
    if notes:
        text += "\nProfile warnings: " + " | ".join(notes)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
