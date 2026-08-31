# Crosswalk: `revit-name-normalization` skill ↔ Batch Rename add-in (Tool 15)

Two implementations of the same idea — rule-driven bulk rename — built for different
contexts. This file keeps them honest with each other.

- **Skill** (this directory): ad-hoc, MCP-driven, runs against whatever model is open,
  arbitrary C#, no UI. Fast to adapt, nothing reusable by the user without Claude.
- **Add-in**: `F:\BIMpossible-AddIns\BIMpossible.RevitLink\Commands\BatchRenameCommand.cs`
  + `BatchRenameWindow.cs`, engine in `Shared\Views\ViewRenamePreview.cs`, rules in
  `Shared\Views\ViewRenameRule.cs`. Shipped, previewed, transactional, user-operable.

The skill is where new normalization rules get **proven on a live model**. The add-in is
where proven rules get **productized**. Traffic flows both ways.

---

## Capability matrix

| Capability | Add-in (Tool 15) | Skill | Note |
|---|---|---|---|
| Find/replace: Contains / StartsWith / EndsWith / Exact | ✅ `MatchMode` | ⚠️ ad-hoc regex | Adopt the add-in's vocabulary in skill prose |
| Regex + capture-group substitution | ✅ | ✅ | Add-in escapes `$`→`$$` in literal modes |
| Case-sensitivity toggle | ✅ `CaseSensitive` | ⚠️ ad-hoc | |
| **Strip trailing suffix token** (`- 2.HR`, `- 0.D`, `- D.0`) | ❌ | ✅ | **Gap → tool** |
| **Strip `Copy N`** | ❌ | ✅ | **Gap → tool** |
| **Remove a token anywhere** (`T.I.` / `TI`) | ❌ | ✅ | **Gap → tool** |
| **Normalize hyphen spacing** (` - ` each side) | ❌ | ✅ | **Gap → tool** |
| **Collapse multiple spaces** | ❌ | ✅ | **Gap → tool** |
| **Collapse duplicate adjacent words** | ❌ | ✅ | **Gap → tool** |
| **Ordered multi-step rule pipeline** | ❌ one rule per field | ✅ 7 ordered steps | **Gap → tool.** Order is load-bearing |
| **Derive value from another property** (View Name → Title on Sheet) | ❌ | ✅ | **Gap → tool.** Adjacent to backlog B20 |
| **Target `Title on Sheet`** (`VIEW_DESCRIPTION`) | ❌ not a scope | ✅ | **Gap → tool.** This is the *printed* title |
| **Skip multi-viewport sheets** | ❌ | ✅ pre-pass | **Gap → tool** |
| **Empty-result guard** | ⚠️ `Invalid` status | ✅ explicit skip+count | Confirm add-in blocks blank results |
| Prefix / suffix add | ❌ (backlog B19) | ❌ | Neither |
| Case conversion (Title Case, UPPER) | ❌ | ❌ | Neither |
| Sequential numbering | ❌ | ❌ | Neither |
| Scopes: Views/Sheets/Collections/Schedules/Legends/Drafting/Templates | ✅ | ⚠️ views-on-sheets only | **Gap → skill** |
| Sheet Number vs Sheet Name as separate rules | ✅ `SheetField` | ⚠️ ad-hoc | **Gap → skill** |
| Source filter: All / Collection / Selection | ✅ `SheetSourceScope` | ❌ | **Gap → skill** |
| Live preflight preview, per-row include/exclude | ✅ | ⚠️ audit query | **Gap → skill** |
| Per-row status taxonomy | ✅ 6 statuses | ⚠️ 5 counters | **Gap → skill** — adopt the taxonomy |
| Conflict detection (name collisions) | ✅ blocks | ❌ | **Gap → skill** |
| Swap/cycle handling | ⚠️ blocks `DependentSwap` | ❌ | See divergence below |
| Re-collect + re-evaluate immediately before apply | ✅ | ❌ | **Gap → skill** — good discipline |
| CSV manifest of every run | ✅ `ReportHelper.WriteCsv` | ❌ | **Gap → skill** |
| Atomic all-or-nothing rollback | ✅ `TransactionGuard.TryCommit` | ❌ host-managed | See divergence below |

---

## Skill → Tool: proposed enhancements

Ranked by what the ACCC_D_EL_SL run (896 sheets, 306 corrections) actually needed.

1. **New scope: `TitleOnSheet`** — write `BuiltInParameter.VIEW_DESCRIPTION` on views placed
   on sheets. This is what actually prints in the titleblock; when blank, Revit falls back
   to the raw View Name, suffix and all. Today the tool cannot touch it, which means the
   single most visible "wrong sheet title" defect is out of reach.
2. **Derive-from-source mode** — instead of find/replace on the target's own value, set
   `target = pipeline(source)` where source is another property (View Name → Title on
   Sheet). Generalizes backlog **B20 (Copy Parameters Between Elements)**.
3. **Named normalization operations** as first-class rule types, not hand-written regex.
   Users will not author `\b(\w+)(\s+\1\b)+`. Ship these as checkboxes:
   - Strip trailing token (configurable pattern; default `N.XX` / `XX.N`)
   - Strip `Copy N`
   - Remove token (literal or regex, anywhere in string)
   - Normalize hyphen spacing
   - Collapse whitespace
   - Collapse duplicate adjacent words
   - Trim orphaned leading/trailing hyphens
4. **Ordered rule pipeline per field** — multiple rules applied in sequence with visible,
   reorderable order. **Order is load-bearing**, not cosmetic: suffix-strip must precede
   hyphen-normalization (the `$`-anchored pattern needs the raw tail), and duplicate-word
   collapse must follow token-removal (deleting `T.I.` is what *creates* some adjacent
   duplicates). A single find/replace per field cannot express this.
5. **Exclusion filter: sheets with more than one viewport** — a sheet hosting several
   distinctly-named views has no single source name; a blind pass corrupts it. 60 of 896
   sheets on ACCC.
6. **Guard against empty results** — any rule chain reducing a value to empty/whitespace
   must surface as blocking `Invalid`, never as a silent blank write.

Suggested backlog framing: extend Tool 15 rather than a new tool. Items 1–2 are the
product-visible win; 3–4 are the engine change that makes 1–2 usable.

---

## Tool → Skill: adopt these

1. **Status taxonomy over ad-hoc counters.** Replace `changed/unchanged/skipped/
   emptyResult/setErrors` with the add-in's `WillRename | Unchanged | Conflict | Invalid |
   NotEditable | DependentSwap`. Same vocabulary in both places = comparable reports.
2. **Re-evaluate immediately before applying.** The add-in re-collects fresh model state
   and re-runs the preflight at click time, aborting if anything became blocking. The skill
   currently audits, then writes in a separate call — the model can change in between.
3. **Emit a CSV manifest.** Every add-in run writes one. Skill runs should too, so a bulk
   change is reviewable and reversible after the fact.
4. **Detect name conflicts before writing**, not after a failed `Set`.
5. **Match-mode vocabulary.** Use Contains / Starts with / Ends with / Exact / Regex when
   describing rules, so a user moving between the two doesn't relearn terms.

---

## Divergences to preserve (do NOT cross-copy)

| Concern | Add-in | Skill (MCP) | Why |
|---|---|---|---|
| Transaction | **Creates its own** — `using (var tx = new Transaction(doc, "Batch Rename"))`, `TransactionMode.Manual` | **Must NOT create one** — MCP already wraps; `new Transaction(...)` throws `InvalidOperationException` | Different execution contexts. Copying the add-in's transaction code into an MCP call is a guaranteed failure |
| Swap/cycle renames | **Blocks** `DependentSwap` | Sibling skill `revit-sheet-view-manager` §I does a **two-pass temp rename** | Add-in chooses safety+explicitness; scripted context can do the temp dance. Don't "fix" either to match |
| Scope of a run | One scope per commit, user-chosen | Whatever the C# collects | |

---

## Known model defects this crosswalk came from

From ACCC_D_EL_SL, still open (deferred to a later Sheet Number/Name pass):

- Duplicate `SheetNumber` `HR-T201i` across two distinct `ViewSheet` elements
  (ids 3601126, 4402521). The add-in's conflict detection would flag this; worth a
  dedicated "duplicate sheet number" audit in either surface.
- Malformed sheet numbers: `T3-E203MD` / `ME` / `MF` (missing `.` before sector letter),
  `T3-3303M` (likely `T303M`), `zD3-E133` / `xT501` / `zT500` prefixes.
- ~55 sheets where the placed view is a different discipline than the sheet name claims
  (Security sheet holding a Power Plan, etc.) — content mismatch, not a formatting defect.
  No rename engine can fix these; they need human adjudication of which side is wrong.
