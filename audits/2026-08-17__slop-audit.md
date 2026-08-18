# Slop audit — claude-profile

- **Date:** 2026-08-17
- **Window:** INCREMENTAL — since `audits/2026-08-10__slop-audit.md` (window end `679e06f`). Commits `679e06f..7759f5c` (2 commits: `0017f5a` scope-governor v3 multi north-star, `7759f5c` docs generalization).
- **Scope:** silent-catch census, counter-integrity, tested-but-dead. Read-only, findings only.
- **In-window executable code:** `hooks/northstar_inject.py` (+218/-81, rewritten to v3), `hooks/protect_paths_gate.py` (+14/-?). Everything else in the window is markdown (`CLAUDE.md`, `LONG-TASK-HARNESS.md`, `docs/northstar-multi-design.md`, `hooks/README.md`, `templates/*`) — excluded from the three checks.
- **Install drift:** none. `hooks/northstar_inject.py` and `hooks/protect_paths_gate.py` are byte-identical to the pinned copies in `C:\Users\Zeria\.claude\hooks` (SHA256 `16FD2297…` / `679A2051…`), and this session's own `SessionStart` output carried the v3 `<northstar-setup>` text — v3 is live.

## Verdict

Two MEDIUM, two LOW. No CRITICAL/HIGH.

v3's fail-open polarity is still right for an advisory hook and still documented inline. What changed is the *consequence* of failing open: v2 had exactly one mission, so a read failure meant **no** `<northstar>` block at all — loud by absence. v3 injects the survivors and states a confident count, so a dropped mission now looks like a complete mission set. That is the house defect (reports success, quietly did nothing) landing inside the control that exists to prevent scope runaway.

### Check 1 — silent-catch census

See appendix. Four in-window swallow sites. Two are *justified-and-documented* fail-open (advisory hook must never block a prompt). Two hide a real failure behind a confident success message — MED-1 and LOW-1.

### Check 2 — counter-integrity

The injected header at `hooks/northstar_inject.py:196` is a counter: `"{len(blocks)} active north star file(s)"`. It counts what *survived* the read loop, not what was found active on disk. Every drop path (`:178-179`) is silent — no skip counter, no note in the emitted block. This is the exact "success total that silently excludes failures" shape check 2 targets. Confirmed live (MED-1).

`find_stars()` collects `inactive` honestly across the whole walk, but the emitted `paused_note` (`:186`) filters it to the winning directory only — a second, quieter count mismatch (LOW-2).

### Check 3 — tested-but-dead

No test changed in the window, so nothing is tested-but-dead. `hooks/tests/` contains only `test_ctxdex_hook.py` and `test_protect_paths_gate.py` — **there is no `test_northstar_inject.py`**, so v3's new status parser, multi-star selection, and closest-active-level walk shipped with zero coverage. That is *untested*, not tested-but-dead; noted, not scored.

## Findings

### MED-1 — an unreadable ACTIVE north star is silently dropped and the header still reports a confident count — VERIFIED

- **Where:** `hooks/northstar_inject.py:177-181` (`except Exception: continue`) and `hooks/northstar_inject.py:196` (`{len(blocks)} active north star file{plural}`).
- **What:** `star_status()` (`:79-89`) reads with `errors="replace"`, so a mission file with an invalid byte is still classified **active**. `main()` then re-reads it with `read_text(encoding="utf-8-sig")` — no `errors=` — which raises `UnicodeDecodeError`, is caught, and `continue`s. The star vanishes from `blocks`, and the header counts only survivors. Nothing in the emitted block says a mission was dropped.
- **Why it matters:** the model is told these are *the* locked missions and works toward them. A mission that is active on disk but unreadable is a mission the model does not know exists — the same silent-plan-drift failure the scope governor was built to stop, arriving through the governor itself.
- **Verification step (RUN, result below):** create two active stars in a scratch dir, one valid (`NORTHSTAR.alpha.md`), one with a raw `0x80` byte in the body (`NORTHSTAR.bravo.md`); run the hook with `{"hook_event_name":"UserPromptSubmit"}` on stdin.
- **Result — CONFIRMED.** Output: `LOCKED MISSION - 1 active north star file, re-read from disk every turn.` followed by `----- NORTHSTAR.alpha.md (active) -----` only. `NORTHSTAR.bravo.md` is absent, unmentioned, exit 0.
- **Shape of a fix (not applied):** either read with the same `errors="replace"` `star_status()` already uses, or keep the drop and append a line to the emitted block naming the unreadable file — a fail-open control may drop a mission, but it must not claim the remainder is complete.

### MED-2 — the injected text overclaims the gate: it is registered on `Write|Edit` only, so a Bash write is not intercepted — VERIFIED

- **Where:** `hooks/northstar_inject.py:198-199` ("You cannot edit these files (a gate blocks Write/Edit on NORTHSTAR*.md; only *.draft.md is writable)") and `:202-204` ("Never abandon, merge, pause, or close a mission on your own - pausing/closing/switching is the human editing that file's 'status:' line"). Also the `<northstar-status>` branch at `:182-183` ("you cannot - a gate blocks NORTHSTAR*.md").
- **What:** `C:\Users\Zeria\.claude\settings.json` registers `protect_paths_gate.py` under `PreToolUse` with `"matcher": "Write|Edit"` (verified by reading lines 100-111). Bash is not gated. A `Set-Content` / `sed` / `mv` through the Bash or PowerShell tool reaches any `NORTHSTAR*.md` untouched.
- **Why it matters in v3 specifically:** this bypass is pre-existing, but v2's mission lifecycle required renaming/replacing a whole file. v3 moved the lifecycle onto a **one-line `status:` field inside the file**, so a single one-line write now pauses or closes a mission — and the in-window text newly asserts the human is the only one who can do that. `northstar_inject.py`'s own docstring says "Never overclaim what this hook does" (SCOPE HONESTY, the C3 lesson); this is that overclaim.
- **Verification step (RUN):** read the `PreToolUse` matcher in `settings.json` — done, it is `Write|Edit`. Not exercised further: writing to a real `NORTHSTAR*.md` to prove the bypass would mutate a locked mission file, which is out of bounds for a read-only audit.
- **Shape of a fix (not applied):** either widen the gate to cover Bash/PowerShell writes touching protected paths, or soften the claim to what the control actually does ("a gate blocks the Write and Edit tools on these files") so the honesty note in the docstring stays true.

### LOW-1 — `stars_in()` swallows `OSError` mid-iteration and returns a partial star list — HYPOTHESIS

- **Where:** `hooks/northstar_inject.py:95-104`.
- **What:** the `try` wraps the whole `for p in d.iterdir()` loop. An `OSError` raised part-way through enumeration (permission change, transient network/junction failure — this profile lives behind a junction into a repo) exits the loop with whatever was collected so far and returns it as if complete. `find_stars()` then treats a partial list as the directory's full star set, and if the surviving entries are all inactive the walk continues **upward** and injects a different level's mission.
- **Verification step:** point `stars_in()` at a directory whose `iterdir()` raises after the first entry (monkeypatch `Path.iterdir` to a generator that yields once then raises `OSError`) and assert the returned list length vs. the real file count.
- **Severity note:** LOW because it needs an enumeration failure, and the outcome (injecting a broader mission) is the safer direction. It is listed because it shares MED-1's shape: partial result presented as complete.

### LOW-2 — a directory whose only stars are paused/closed silently inherits the parent's mission — HYPOTHESIS

- **Where:** `hooks/northstar_inject.py:143-150` (`find_stars` continues the walk when a level has no active star) and `:186` (`local_inactive` filters `inactive` to `blocks[0][0].parent`).
- **What:** if a worktree lane's own `NORTHSTAR.<lane>.md` is `status: paused`, the walk moves up and injects the portfolio-level mission instead. The `paused_note` only lists inactive stars sharing the **winning** directory, so the lane's own paused star — the one whose absence explains the switch — is filtered out and never mentioned. The session silently changes which mission it is working toward, with nothing in the injected block saying so.
- **Verification step:** nested dirs `parent/NORTHSTAR.md` (active) and `parent/lane/NORTHSTAR.lane.md` (`status: paused`); run the hook from `parent/lane` and check whether the emitted block names `NORTHSTAR.lane.md`. Expected per the code: it does not.
- **Shape of a fix (not applied):** include inactive stars from directories *closer* than the winner in `paused_note`, since those are exactly the ones that explain the inherited mission.

## Appendix — silent-catch census (in-window)

| Site | Pattern | Classification | Note |
|---|---|---|---|
| `northstar_inject.py:70` | `except Exception: pass` in `emit()` | justified-and-documented | advisory hook must never break a prompt on a console/encoding fault; raw-UTF-8 write is the documented cp1252 workaround |
| `northstar_inject.py:88` | `except Exception: pass` in `star_status()` → returns `"active"` | justified-and-documented | fails toward over-injecting; explicitly reasoned in the docstring ("over-injecting a mission is safer than silently dropping one") — correct polarity |
| `northstar_inject.py:103` | `except OSError: pass` in `stars_in()` → partial list | justified-but-silent | LOW-1: partial enumeration is indistinguishable from a complete one |
| `northstar_inject.py:136` | `except Exception` in `read_event()` | justified-and-documented | unparseable event payload → treat as non-SessionStart; advisory |
| `northstar_inject.py:178-179` | `except Exception: continue` per-star | **swallows-a-real-failure** | MED-1: dropped mission + confident survivor count |
| `protect_paths_gate.py` (in-window hunk) | no new catch | n/a | the v3 hunk is pure matcher logic (`northstar.*.md` minus `.draft.md`/`.template.md`); fail-closed polarity unchanged and still correct for a guard |

**Gate matcher cross-check (not a finding):** the hook's `STAR_NAME_RE` + `NON_STAR_SUFFIXES` and the gate's `is_protected()` exemptions agree exactly — `.draft.md` and `.template.md` are writable and are also not injected as missions. Near-miss names behave: `NORTHSTAR.mydraft.md` and `NORTHSTAR.draft-review.md` are both **blocked** (neither ends with `.draft.md`), which is the safe direction.
