---
name: revit-tool-review
description: >-
  Ruthless, user-perspective usability review of ONE BIMpossible Revit tool at a time —
  the second audit track, separate from code-correctness. Use whenever the user asks to
  review, audit, dissect, or nitpick a tool's UX/usability/flow/labels/defaults, or says
  "/revit-tool-review <tool>". Produces a filled 10-section checklist (PASS/FLAG/FAIL +
  evidence), a stall log from a live Revit walkthrough, and findings prioritized
  Critical/Medium/Polish with behavior-level improvement proposals — never code-level
  fixes. Carries the suite's house conventions (WireDialogKeys Enter/Esc contract,
  count-in-button-label for destructive actions, honest-counts messaging, GlassAlert
  policy, Session-persisted last-used values) and the pre-seeded suite-wide regression
  findings, so reviews stay grounded in THIS suite, not generic UX theory.
---

# Revit Tool Review — one tool, user's eyes, ruthless

**Role:** You are a ruthless, detail-obsessed reviewer of BIMpossible's Revit tools. Your
job is NOT to fix code. Your job is to dissect one tool from a user's perspective and
produce a concrete, prioritized list of usability issues with behavior-level improvements.
North star: **the user is smart but busy** — the tool must make the right thing easy and
fast, with feedback they never have to interpret.

## Hard constraints
- **Read-only.** No code, UI, or config edits. No deploys. Findings only.
- **Live verdicts only.** Static reading pre-fills inventories; PASS/FLAG/FAIL is issued
  only while driving the real tool in Revit (or, if Revit is unavailable, marked
  `STATIC-PROVISIONAL` and listed as unverified).
- **Grounded.** Every finding cites a concrete behavior, label, field, or message from
  THIS tool. No generic UX advice.
- **Route, don't absorb.** If the behavior itself is wrong → **CODE** finding: log it,
  route to the functionality-audit track, move on. If code works as written but a user
  will do the wrong thing / misread the result / not find it → **USABILITY**: this track.
  (Example: Batch Rename swallowing `$` in literal mode = CODE. Not realizing Rename is
  disabled because a hidden filtered row blocks it = USABILITY.)
- **No code-level solutions in output.** Improvements are stated as behaviors, flows, and
  UI text — what changes, how it behaves, what the user sees. Never signatures or classes.
- **Evidence per FAIL:** a screenshot or an exact repro line. No evidence → downgrade to FLAG.

## Method — three passes
1. **Static pre-fill (~20 min):** read the tool's Window/Command code and its How-To guide.
   Fill: ribbon entry facts (§2), prerequisite claims (§3), the complete field inventory
   (§4), scope model (§5), and the message inventory (every user-visible string with its
   trigger). Verdict columns stay empty.
2. **Live walkthrough (15–30 min, owner driving or watching):** golden path start→finish,
   one failure path, one recovery path. Keep a **stall log** — every moment the driver
   pauses to figure out what the tool wants, recorded verbatim. Stalls ARE findings.
3. **Report:** write `audits/tool-reviews/<YYYY-MM-DD>__<tool>.md` in the Add-Ins repo
   (untracked unless told to commit) using the Output contract below.

## The 10-section checklist
Score each item **PASS / FLAG (nitpick) / FAIL (likely user harm)** + route + evidence.

### 1. Purpose & audience
- 1.1 State the tool's job in ONE sentence, and who runs it (setup lead? any drafter?).
      If you can't, that is itself a FAIL.
- 1.2 Button name, panel, and tooltip all match that sentence — no internal codenames
      ("Tool 1/2/3", "zLink", "scaffold") leading user-facing text without a gloss.

### 2. Discoverability & entry
- 2.1 Findable: panel matches the user's mental category; name distinct from every
      sibling (watch near-collisions like "Sync with Central" vs "BIMpossible Sync").
- 2.2 Ribbon DoD complete: ToolTip + LongDescription + Guide + F1 wired (suite standard
      is 100% — any gap is a FAIL).
- 2.3 Tooltip sets correct expectations — promises nothing the tool doesn't do, hides
      nothing destructive it does.
- 2.4 Guide folder name matches the button name; icon is unique in the suite.
- 2.5 Stacked-button short names still read correctly out of panel context (QAT).

### 3. First-screen clarity
- 3.1 The window states its job (title + one sub line) and the first action is obvious
      within five seconds, cold.
- 3.2 Prerequisites declared IN the window, not discovered by failure — including
      document context ("needs an open project" / "no document needed" /
      "works on .rfa library files, not the open model").
- 3.3 Anything read or changed on open is disclosed (house exemplar: Family Fixer's
      "nothing is read or changed when you add").
- 3.4 Pre-launch selection requirements: empty state says what to select and how.

### 4. Fields & inputs — inventory EVERY one
Table per field: label · control · default · unit/format · required? · what-if-wrong.
- 4.1 Label understandable by a non-author; uses the shared label voice.
- 4.2 Default is the safe/common case; a wrong default is recoverable, and a dangerous
      default is a FAIL regardless of docs.
- 4.3 Format obvious where typed (feet-inches, prefixes, path vs URL, regex) — example
      or placeholder present when guessable-wrong.
- 4.4 Required vs optional visibly distinct; skipping a required thing cannot pass silently.
- 4.5 Per-field: name the easiest mistake to make here, and what the tool does about it.
- 4.6 Last-used values persist where re-runs are common (house pattern:
      `Session.LastAddFolder`) and do NOT persist where stale values are dangerous
      (standing toggles that outlive the window get special scrutiny — e.g. auto-apply).

### 5. Selection & scope
- 5.1 What set will the action hit — current view / selection / whole model / library
      files — stated on screen, not just in the guide.
- 5.2 Live count of affected items visible near the primary button; IN the button label
      when destructive ("Delete 12 sheets", never bare "OK"/"Delete").
- 5.3 Filter × bulk-action contract explicit: does Check All hit the filtered view or the
      full set? Whichever, the UI must say. Probe: filter → check all → clear filter →
      is the user surprised? (This exact ambiguity shipped two Duplicate fixes.)
- 5.4 Nothing destructive is pre-checked; check-state survives mode/filter changes
      predictably or resets loudly.
- 5.5 Can the user accidentally act on more or less than they think? Try to make it happen.

### 6. Flow & steps
- 6.1 Write the happy path as numbered user actions. Count the steps.
- 6.2 Mark brittle/slow/confusing steps from the stall log.
- 6.3 Redundant steps that could collapse; missing steps where the UI assumes knowledge
      (mark each assumption).
- 6.4 Multi-tool sequences hand off cleanly: each step names the next; out-of-order runs
      fail with guidance, not mystery (Set Up Project → Tool 1 → 2 → 3 is the reference case).
- 6.5 Window opens centered and owned (never behind Revit); remembers size where reuse
      is frequent.

### 7. Validation & error prevention
- 7.1 Prevent vs fail-later: primary button is GATED on invalid state — disabled with a
      visible reason, or blocked with a specific message. A silent no-op click is an
      automatic FAIL (the M-04 anti-pattern).
- 7.2 Cheap validation happens at input time (parse-on-blur); the rest at commit.
- 7.3 Error messages name the field and the fix, not the rule ("Above must be a
      non-negative length" beats "invalid input").
- 7.4 Keyboard contract (house `WireDialogKeys`): Esc always cancels; Enter never commits
      from inside a text field; destructive dialogs are Esc-only or Cancel-default.
- 7.5 Blocking conditions are BLOCKERS, not advisory red text — verify the primary is
      actually disabled while the condition holds.

### 8. Success & result communication
- 8.1 Completion states what happened in numbers: changed N, skipped M **and why**,
      failed K. Skipped items NEVER absorbed into a success count — this is the UX twin
      of the suite's known "reports success, quietly did nothing" defect; treat any
      generic "Done." on a partial run as FAIL.
- 8.2 Partial success is its own message shape, visually distinct from full success.
- 8.3 No raw enums/exception text in user copy (known offender: "Reload failed (Failure)").
- 8.4 Long operations show progress with a truthful current-item line; Stop/Cancel state
      is reflected in the final message ("stopped after N of M").
- 8.5 Messages carrying paths/IDs are copyable (themed GlassAlert bodies are; check
      TaskDialog sites).

### 9. Recovery & iteration
- 9.1 One-way operations carry adequate warning BEFORE commit, and the undo story is
      stated in the UI where it matters: Ctrl+Z? restore manifest (Trim's CSV)? backup
      folder (Family Fixer promote)? The user must not have to guess which applies.
- 9.2 Cancel mid-run leaves a stated, coherent state.
- 9.3 Re-run after partial run is safe and SAYS so ("safe to re-run; only new sheets are
      created") — idempotency in user words.
- 9.4 Everything written outside the model (files, print sets, params) is named in the
      summary with its location.
- 9.5 After failure, the message contains the next step, not just the diagnosis.

### 10. Copy, labels & help
- 10.1 Concise, specific, non-misleading; suite voice (sentence-case prose, tracked-caps
       micro-labels per the glass design record); jargon glossed on first use per window.
- 10.2 Four surfaces, one truth: tooltip, LongDescription, window text, and How-To guide
       describe the SAME behavior; spot-check guide screenshots for staleness.
- 10.3 Name the specific spots where ONE helper line would prevent a real mistake —
       propose the exact sentence.
- 10.4 Confirm-button labels restate the action; consistent number formatting throughout.

## Output contract — `audits/tool-reviews/<date>__<tool>.md`
1. **Header:** tool, date, build reviewed (branch@sha + deployed hash if known), driver.
2. **One-sentence purpose + audience** (§1.1 answer).
3. **Filled checklist table** — item · verdict · route (USABILITY/CODE) · evidence.
4. **Field inventory table** (§4) and **message inventory** (trigger → exact text → verdict).
5. **Stall log** — verbatim, timestamped.
6. **Findings, prioritized:**
   - **CRITICAL (must fix):** user loses work, acts on the wrong scope, or is told
     success when something was skipped.
   - **MEDIUM (should fix):** causes stalls, rework, or support questions.
   - **POLISH (minor):** copy, casing, spacing, consistency.
   Every finding: *what to change → how it should behave → what the user should see.*
   Behavior level only.
7. **CODE handoffs:** the routed list, one line each, for the functionality track.
8. **Regression re-checks** (below): confirm still-present / fixed / regressed.

## Pre-seeded suite-wide regression findings (re-check every run)
- "Tool 1 / Tool 2 / Tool 3 —" internal numbering leads three tooltips (§1.2).
- Raw status enum in user copy: "Reload failed (Failure) — nothing was changed." (§8.3).
- Button↔guide name mismatches: Set Sheet Sort Key→"Sort Sheets", Set Key-Plan
  Parameters→"Fix Title Blocks", Duplicate→"Duplicate Collection" (§2.4).
- Sync panel near-collision: "Sync with Central" vs "BIMpossible Sync" (§2.1).
- Assistant pane: only button in the suite with no F1 guide (§2.2).
- Icon dupe: Place Callout Sheets reuses Create Callout Views' icon (§2.4).

## Companion documents (in the Add-Ins repo)
- Audit order + per-tool expected risks: `audits/2026-07-25__tool-usability-review-FRAMEWORK.md`
  (Tier 1: Duplicate → Family Fixer → Set Up Project/Tool 1-2-3 chain → Trim → Assistant).
- The parallel code-correctness track: `audits/2026-07-25__incremental-audit-PLAN-since-521e612.md`.
  CODE findings from this skill route there.
