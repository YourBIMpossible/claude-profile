---
name: schedule-run
description: "Use whenever the user asks to schedule/start a Claude session, set up a routine, cron, weekly job, one-off run, or \"run this in N minutes / at <time> / every <day>\" — or types \"/schedule-run\". Routes to the RIGHT mechanism: LOCAL scheduled-tasks (runs on the user's PC, sees F:\\ drives, appears in the session list) vs CLOUD routines (isolated Linux, GitHub-only, no local drives). Owner has been burned by picking cloud for a local job; this skill removes the guess."
---

# /schedule-run — schedule a Claude session (local vs cloud, decided for you)

The owner uses the word "routine" for BOTH mechanisms. Do not let the word decide.
**The target decides.** Pick the mechanism from WHERE the work must run, then build it.

## The one rule (memorize this)

| If the job needs… | Use | Tool namespace |
|---|---|---|
| a local path (`F:\...`, `C:\...`), the owner's live working tree, local Revit/desktop, "**on my PC**", "shows up here", or matches an existing weekly task | **LOCAL scheduled-task** | `mcp__scheduled-tasks__*` |
| ONLY a GitHub repo (fresh clone is fine), no local files, explicitly "in the cloud / claude.ai" | **CLOUD routine** | `/schedule` skill → `RemoteTrigger` |

**Default to LOCAL.** Cloud runs in an isolated Linux container: **no F: drive, no C: drive, no local working tree, no desktop Revit.** A cloud routine pointed at `F:\anything` will fail every time and stop — this is the exact mistake to never repeat. Only go cloud when the owner is explicit AND the target is reachable as a GitHub URL.

If genuinely ambiguous after applying the table, ask ONE question: *"On your PC (local, sees F:\\) or in the cloud (GitHub-only)?"* — nothing else.

## LOCAL scheduled-task — the primary path

Runs a fresh Claude Code session **on this PC**, in the owner's session list, with full F:\ access. This is the mechanism behind the owner's weekly jobs (slop-audit, weekly-full-audit, next-workspace-sync, context-weight, nl-filter-drift, graphify).

Steps:
1. **Re-check current time first** — `date "+%Y-%m-%dT%H:%M:%S%z"` (Bash). Never infer the date/time from context.
2. Resolve the request against that fresh time. Echo back the resolved **local** time before creating.
3. Create with `mcp__scheduled-tasks__create_scheduled_task`:
   - `fireAt` = one-time, ISO 8601 **with local offset** (e.g. `2026-09-16T15:07:00-07:00`). Auto-disables after firing.
   - `cronExpression` = recurring, evaluated in **LOCAL time** (not UTC). e.g. `0 9 * * 1` = 9am Mon local.
   - Pick at most one; omit both for manual-only ("Run now").
   - `taskId` kebab-case; `title` in the owner's words; `description` one line.
   - `prompt` MUST be self-contained: name the absolute repo path (quote paths with spaces, e.g. `F:\Evidence Compiler`), the exact command/skill to run, read-only vs write, and the "if path unreachable, say so and stop" guard for read-only drills.
4. Report: task id, resolved local fire time, "appears in your Scheduled/session list."
5. To run immediately instead of waiting: `mcp__scheduled-tasks__run_scheduled_task`.
6. Manage/inspect: `list_scheduled_tasks`, `list_task_runs`, `update_scheduled_task`, `delete_scheduled_task`.

Note: scheduled tasks fire while the app is open; if closed at fire time, they run on next launch — say so for time-critical jobs.

## CLOUD routine — only when explicitly cloud + GitHub-only

Invoke the bundled `/schedule` skill (it drives `RemoteTrigger`). Requirements:
- Target must be a GitHub repo attached as a source (`sources: [{git_repository:{url}}]`); the container clones it fresh and sees ONLY pushed `origin/main`, never uncommitted local work.
- `cron_expression` / `run_once_at` are **UTC** (convert from local, confirm the conversion).
- Never point a cloud routine at a local path. If the owner wants their live/local tree, it is LOCAL — do not use cloud.

## Anti-patterns (do not repeat)

- ❌ Mapping the word "routine" → `/schedule` cloud. The owner says "routine" for local jobs too.
- ❌ Cloud routine with an `F:\...` prompt. It cannot see the drive; it will stop with "not reachable."
- ❌ Claiming this session can't see `F:\` repos. It can — this session runs on the PC. Verify with a quick `ls` before ever saying otherwise.
- ❌ Computing `fireAt`/cron from remembered time. Always re-fetch `date` first.

## Reference
- Memory: `scheduled-tasks-not-cloud-routines` (same rule, condensed).
