---
name: revit-viewport-standardization
description: >-
  Align, standardize, or repair Revit plan viewports against a per-sector template
  sheet via mcp-server-for-revit: box centers, grid bubbles, annotation crops, and
  viewport titles ("AlignSectorViewports"-style batches) — even if the user doesn't
  name this skill. For general sheet/view management NOT about matching a template
  (creating sheets/views, scope boxes, print sets, renames), use
  revit-sheet-view-manager instead.
---

# Revit Viewport Standardization

Match every plan view in a sheet set to a per-sector **template view** in four respects:
**box-center alignment · grid-bubble extents · annotation crop · viewport title.**

This skill exists because doing it naively fails in expensive, non-obvious ways: the
operations are coupled (fixing one silently breaks another), the MCP host has sharp
edges, and the model is large enough that batches time out. Follow the order and the
guards below and it goes smoothly.

## When to use / not use

Use it for batch-standardizing plan viewports to a reference view across a set of
sheets (typically one template per zone/sector). It generalizes to any Revit model
reached through `mcp-server-for-revit` — only the **Config** block is project-specific.

Not for: single-viewport tweaks (just do them directly), creating sheets/views, or
non-plan views. If the user only wants one property fixed (say just titles), run only
that phase — but still respect the order rule if you touch box centers.

## Config (edit per project)

Read these from the user or the model; everything else is model-agnostic.

```
DOC_TITLE        = "ACCC_D_EL_SL"          // guard: abort if this isn't the active doc
SCOPE_BOX_PARAM  = "Scope Box"
TARGET_TYPES     = FloorPlan, CeilingPlan
REFERENCE_SHEET_MAP = {                     // sector (scope-box name) -> template sheet number
  "SECTOR A":"E300A.A", "SECTOR B":"E300A.B", "SECTOR C":"E300A.C",
  "SECTOR D":"E300A.D", "SECTOR E":"E300A.E", "SECTOR F":"E300A.F" }
```

Each sector's template is resolved **live** from its sheet (the single target-type
viewport on it whose Scope Box matches the sector). Never hardcode reference geometry —
every sector has different anno offsets and a different grid set.

## The order is non-negotiable

> **grid extents → annotation crop → box-center alignment → titles LAST**

The properties are coupled, so order is correctness, not preference:

- The viewport's **box center includes the annotation crop**. Changing the crop shifts
  the box center — so any box alignment done *before* a crop change is silently undone.
  Crop first, align after.
- **`SetBoxCenter` perturbs the title's `LabelOffset.Y`** (~0.05 ft); setting the title
  does **not** move the box (one-way coupling — verified `dX=dY=0`). So titles come last,
  after alignment, or alignment knocks them off again.
- Within one viewport, set `LabelLineLength` **first**, `LabelOffset` **last** (line
  length re-anchors the offset).
- If you fix a stray box *after* titles are done, **re-set that viewport's title in the
  same commit** (box move perturbs it).

## Workflow

Always **discover → present to the user → execute → verify**. Discovery is read-only;
never write to a production model without showing the mismatch list and getting a
go-ahead first.

0. **Connect & guard.** Confirm the MCP link (`say_hello` or a trivial `return`) and that
   `DOC_TITLE` is the active document. Put `if(document.Title!="<DOC_TITLE>") return "WRONG DOC: "+document.Title;`
   at the top of **every** script — the active doc can switch mid-session and `document`
   always points at whatever is frontmost.

1. **Discover (read-only).** Scan all target views, compare each property to its sector
   template, log mismatches. Present counts per sector + the notable outliers. Get approval.

2. **Fix grids + annotation crop.** Set each grid's view-specific curve to the template's
   crop-relative position; set the 4 crop offsets to the template's.

3. **Align box centers.** `SetBoxCenter` each target viewport to its sector template's
   box center.

4. **Fix titles (LAST).** Set `LabelLineLength`, rotation, then `LabelOffset` to template.

5. **Converge (verify-and-fix loop).** Sweep every view with the Phase-5 verify-and-fix
   block: re-read all four properties post-commit and inline-fix any straggler (a box fix
   always re-sets that viewport's title in the same commit — the box move perturbs it).
   One sweep is rarely enough: silent partial commits and the box→title coupling mean
   fixes can surface new mismatches. So **loop full sweeps until a sweep reports 0 fixes
   and 0 anno/grid-bad**, then run one pure read-only pass as the measured certification.
   Track a running total per sweep so termination is unambiguous. Expect convergence in
   1–2 sweeps; if it hasn't converged by ~3, stop and investigate (active doc switched?
   template resolved wrong? a coupling you haven't accounted for?) rather than looping.

   Why the loop must span calls: the host commits only after a script returns, and
   **in-call reads always show what you just set** — verification is only meaningful
   across the commit boundary. Drive the loop call-by-call; never trust a `while` loop
   inside one script to verify its own writes.

The full, ready-to-paste C# for each phase is in
[references/csharp-templates.md](references/csharp-templates.md). Read it when you start a
phase; each block is parameterized by `VIEW_SKIP` / `VIEW_TAKE` chunk constants.

## Chunking & reliability (why batches fail, and how not to)

`GetBoxCenter` / `SetBoxCenter` / `GetCurvesInView` / `LabelOffset` are **geometry-heavy
(~0.5–1 s each)** on a large model. Consequences:

- **Chunk ≤ ~60 views per `send_code_to_revit` call.** Larger reliably times out. Process
  a stable ordered list with `Skip(VIEW_SKIP).Take(VIEW_TAKE)`; carry the offset forward.
- **Verify post-commit, in a separate call.** Under load the host can report a write
  "done" without committing (silent partial commit). Never trust the moved/fixed count —
  read it back next call. This is the single most important habit; skipping it is what
  turned a clean job into a multi-hour cleanup.
- **Idempotent by construction.** Every fix sets a value to the template's, so re-running
  any range is safe. Use this: on any doubt, re-run.
- **Timeout / desync recovery:** a client `-32001` timeout doesn't stop the server — it
  keeps running and the result surfaces on a later call. Send a trivial ping to drain it,
  then a sentinel (`return "RESYNC-1";`) and confirm you get *that* back before continuing.
  A clean server-side "代码执行超时" means it was killed — just retry smaller.

See [references/troubleshooting.md](references/troubleshooting.md) for the `send_code_to_revit`
C# template quirks (they cause compile errors and silent no-ops if unknown) and the full
timeout/doc-switch playbook. Read it the first time a script won't compile or behaves oddly.

## Metrics (what "matches" means)

- **Box center:** `Viewport.GetBoxCenter()` within 1e-3 ft of the sector template's.
- **Grid bubble:** per grid, the view-specific curve endpoint's gap to the crop edge
  (`cropMax − endHi`, `endLo − cropMin` along the grid axis) within 5e-3 ft of the
  template's. This is the "bubble vs crop boundary" the user cares about.
- **Annotation crop:** the 4 `CropRegionShapeManager` offsets within 5e-4 ft.
- **Title:** `LabelOffset` (X,Y), `LabelLineLength` within 1e-3, and matching rotation.

## Data-quality checks worth surfacing

While scanning, watch for and report (don't silently "fix"): duplicate sheet numbers
(two `ViewSheet` elements sharing a number), views tagged to a sector but with no
viewport (unplaced — they get grid/anno but no title), and rotated viewports
(`SetBoxCenter` semantics differ — skip and flag).
