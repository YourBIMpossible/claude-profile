## Goal

Measure where time goes for one **cold load** and one **warm reload** of `GET /data/elements?category=Electrical Equipment` on the proving-case model. Produce a time-budget breakdown that drives Session 2 fix selection. **No fixes, no speculation, no architecture changes — diagnostic only.**

---

## Test setup

- **Model:** `VNCB-D-0811-EL-SLX-R24_EXP.rvt`
- **Category:** Electrical Equipment
- **Environment:** `docker-backend-1` container running, live AEC DM access, real Autodesk auth session
- **Browser:** any (Chromium DevTools recommended for Network tab inspection)

---

## Procedure

### Cold load

1. Restart the backend container: `docker compose -f docker/docker-compose.yml restart backend`
2. Wait until `/health` returns 200 in DevTools or curl
3. Open the model fresh in a new browser tab
4. Click **Electrical Equipment** in the category sidebar
5. Capture: every backend log line with timestamps, DevTools Network entries for `/data/elements` and any sibling requests, end-to-end response time, total wall-clock from click to fully-rendered table

### Warm reload

1. Without restarting the backend, click away to another category (e.g., **Lighting Fixtures**)
2. Wait for that category to fully render
3. Click back to **Electrical Equipment**
4. Capture: same instrumentation as cold load

---

## Exact questions to answer

Answer each in a markdown table. Include **actual numbers**, not adjectives like "fast" / "slow."

| # | Question | Answer (cold) | Answer (warm) |
|---|---|---|---|
| 1 | Which layers are touched? (AEC DM remote call / Postgres `element_cache` / in-memory cache / other) | | |
| 2 | If AEC DM is hit, how many round-trips and what is the total network time? | | |
| 3 | If `element_cache` is hit, what is the SQL query time? | | |
| 4 | What is the response payload size (bytes)? | | |
| 5 | What is the response serialization time (backend log)? | | |
| 6 | What HTTP cache headers are present on the response? Check for `Cache-Control`, `ETag`, `Last-Modified`, `Age`. | | |
| 7 | Does the frontend re-request when the user navigates away and back to the same category? Check Network tab during the warm-reload step. | | |
| 8 | Does the frontend make additional requests beyond `/data/elements` during the click? (e.g., separate count probe, category list refresh, version check) List each. | | |
| 9 | Is the AEC DM version probe called per request, or cached? If cached, what is the TTL? | | |
| 10 | Total wall-clock time from click to fully-rendered table? | | |

---

## Time-budget decomposition (required)

For BOTH cold and warm runs, break down total time into this structure. Use real numbers; mark items that don't apply as `n/a` or `0`.

```
Total: X ms
  ├─ Auth check / token refresh:        _ ms
  ├─ AEC DM version probe:               _ ms  (or 0 if cached)
  ├─ AEC DM elements fetch:              _ ms  (or 0 if cached)
  ├─ Postgres element_cache query:       _ ms
  ├─ In-memory cache lookup:             _ ms
  ├─ Response serialization:             _ ms
  ├─ Network return to browser:          _ ms
  ├─ Frontend parse + render:            _ ms
  └─ Element Preview Panel hydrate:      _ ms  (if applicable on first row hover)
```

---

## Constraints (non-negotiable)

- **No fixes.** Do not edit any code. Do not propose architecture changes mid-session. Diagnostic only.
- **Real container.** Run against `docker-backend-1`, not a local Python process running outside Docker.
- **Real model.** Use the proving-case Revit file, not a mocked dataset.
- **Markdown output.** Tables and a time-budget breakdown — no prose-only reports.
- **Stop at the data.** When the questions above are answered with real numbers, the session is done. Save the fixes-discussion for Session 2.

---

## Deliverable

Save a single markdown report to:

```
F:\AI-Dev\BIMpossible_Workspace\01_BuildLog\YYYY-MM-DD__performance-diagnostic-wave-4-6-5-session-1.md
```

Containing:

1. The question table (above) with all cells filled in
2. The time-budget decomposition (cold + warm)
3. **Observations section** — note any surprise findings (unexpected requests, missing cache headers, redundant AEC DM calls, etc.)
4. **Top-3 contributors recommendation** — list the three biggest contributors to total time, ordered by impact, with a one-line proposed Session 2 intervention each

---

## Run grouping / index (do this every run)

So every run of this task is findable as a set (the session browser has no native "groups" — see 2026-06-01 decision), keep a durable file-based index:

1. **Append one line** to `F:\AI-Dev\BIMpossible_Workspace\01_BuildLog\_perf-diagnostic-runs.md` (create it with an `# ⏱ PERF-DIAG run index` heading + a `| Date | Cold (ms) | Warm (ms) | Top contributor | Report |` table header if it does not yet exist). The new row records: run date, end-to-end cold ms, warm ms, the #1 contributor, and a relative link to that run's report under `01_BuildLog/`.
2. **Best-effort session title:** at the start of the run, if a session-title mechanism is available (e.g. the `/title` command), set the title to `PERF-DIAG <YYYY-MM-DD>` so runs cluster when searching the session browser by `PERF-DIAG`. If no title mechanism is available in this run, skip silently — the index file in step 1 is the authoritative grouping.

---

## Out of scope for this session

- Any code change
- Any deployment change
- Speculation about causes when the diagnostic data doesn't yet exist
- Discussion of caching architectures (those happen in Session 2 against real evidence)
- Frontend-only or backend-only fixes (Session 2 picks based on evidence, not preference)

---

## What "done" looks like

- All 10 questions answered with real numbers for both cold and warm
- Time-budget decomposition complete for both
- Top-3 contributors named
- Report saved at the path above
- A row appended to `01_BuildLog/_perf-diagnostic-runs.md` (the run index)