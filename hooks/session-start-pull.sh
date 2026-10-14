#!/usr/bin/env bash
# Best-effort refresh of the claude-profile repo at Claude Code session start
# (Linux/macOS port of session-start-pull.ps1; same contract, same log vocabulary).
#
# NOT a guarantee of freshness: offline, a non-fast-forward, another session
# already pulling, or git missing all mean the session proceeds on whatever is
# on disk. Every path exits 0 - this must never block session start. The record
# of what happened is last-run.log beside this script.
#
# Concurrency: Claude starts many processes at once and all of them run this
# hook against the SAME checkout. Two flock(1) locks serialize it, mirroring the
# two named mutexes in the PowerShell version:
#   last-run.log.pull-lock  - serializes the git pull (non-blocking: losers log
#                             "pull skipped (in flight)" and move on)
#   last-run.log.log-lock   - serializes the log append
#
# Log vocabulary (identical to the PowerShell hook, so one reader covers both):
#   pull ok: <git output>
#   pull skipped (in flight) [repo]
#   pull failed (exit N): <git output> [repo]
#   git missing: <detail> [repo]
#   pull threw: <detail> [repo]

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_PATH="$HOOK_DIR/last-run.log"
# bootstrap.py pins the next line to the repo's absolute path (C1): the installed
# copy runs from ~/.claude/hooks, outside the repo, so it must not derive it.
REPO_ROOT="$(cd "$HOOK_DIR/.." && pwd)"

log() {
    # One writer at a time; a 2 s wait is generous for a one-line append.
    {
        if flock -w 2 9; then
            printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_PATH"
        fi
    } 9>>"$LOG_PATH.log-lock" 2>/dev/null || true
}

main() {
    if ! command -v git >/dev/null 2>&1; then
        log "git missing: git not found on PATH [$REPO_ROOT]"
        return 0
    fi
    if [ ! -d "$REPO_ROOT/.git" ] && [ ! -f "$REPO_ROOT/.git" ]; then
        log "pull threw: not a git checkout [$REPO_ROOT]"
        return 0
    fi
    exec 8>>"$LOG_PATH.pull-lock" || { log "pull threw: cannot open pull lock [$REPO_ROOT]"; return 0; }
    if ! flock -n 8; then
        # Benign: another session is pulling right now; its result lands on disk for us too.
        log "pull skipped (in flight) [$REPO_ROOT]"
        return 0
    fi
    local output code
    # No branch named on purpose: --ff-only resolves through the current
    # branch's configured upstream, so this stays correct on any branch.
    # The hook has a 15 s budget in settings; cap git well inside it.
    output="$(cd "$REPO_ROOT" && timeout 10 git pull --ff-only 2>&1)"
    code=$?
    output="$(printf '%s' "$output" | tr '\n' '|' | sed 's/|/ | /g')"
    if [ "$code" -eq 0 ]; then
        log "pull ok: $output"
    elif [ "$code" -eq 124 ]; then
        log "pull failed (exit 124): timed out after 10s [$REPO_ROOT]"
    else
        log "pull failed (exit $code): $output [$REPO_ROOT]"
    fi
    flock -u 8
    return 0
}

main || true
exit 0
