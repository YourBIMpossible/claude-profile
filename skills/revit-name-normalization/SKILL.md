---
name: revit-name-normalization
description: >
  Rule-driven bulk text cleanup or derivation across many Revit views/sheets via
  mcp-server-for-revit: stripping name suffixes (e.g. "- 2.HR"), removing
  firm/phase tokens, fixing spacing and hyphens, syncing one property into
  another (View Name -> "Title on Sheet"). Use for "make the titles match",
  "clean up the naming", or any rename across hundreds of elements. For
  viewports, scope boxes, grids, or print sets use revit-sheet-view-manager.
---

# Revit Name Normalization

Rule-driven bulk text cleanup across views and sheets. The shape is always the same:
**audit read-only → agree the rule set → apply in one pass → verify by re-counting.**

Proven on ACCC_D_EL_SL (896 sheets, 877 placed views, 306 corrected in a single pass,
zero write errors).

---

## The Iron Rules

| Rule | Detail |
|---|---|
| Document variable | Lowercase `document`. Not `doc`, not `Document` (capital resolves to the *type* — "an object reference is required" on `.Title`) |
| **No `new Transaction(...)`** | MCP already wraps your code in a transaction. Creating one throws `InvalidOperationException: Starting a new transaction is not permitted`. Just call `p.Set(...)` directly — it commits with the host transaction |
| Revit 2026 ElementId | `id.Value` (a `long`). `.IntegerValue` no longer exists |
| Never dump raw rows | Aggregate/filter **inside** the C#. Returning per-element rows for 900 elements overflows the tool result (~357K chars) and gets rejected |
| Dedupe by view id | A view can be placed on more than one sheet. Track a `HashSet<long>` of processed view ids or you double-process |
| Schedules aren't viewports | `sheet.GetAllViewports()` misses schedules. Those are `ScheduleSheetInstance` → `document.GetElement(ssi.ScheduleId)`. A "no views on this sheet" bucket is usually schedule-only sheets, not empty ones |

### Surfacing the real error

MCP reports *any* server-side throw as an opaque:

```
执行失败: Exception has been thrown by the target of an invocation.
```

That message tells you nothing. **Wrap suspect work in try/catch and return the exception
text yourself**, walking `InnerException`, or you will burn calls guessing:

```csharp
catch (Exception ex)
{
    fatal = ex.GetType().FullName + ": " + ex.Message;
    var inner = ex.InnerException; int d = 0;
    while (inner != null && d < 5) {
        fatal += " | INNER[" + d + "]=" + inner.GetType().FullName + ": " + inner.Message;
        inner = inner.InnerException; d++;
    }
}
```

This is what turned a dead-end invocation error into the actual "nested transaction" cause.

### Revit busy ≠ your query is too heavy

When Revit's UI thread is tied up in another interactive tool, **every** MCP call times out
uniformly — a trivial one-element read fails identically to a 900-element scan. Don't
binary-search your query size chasing a phantom scale limit. Confirm with a cheap ping
before blaming the workload:

```csharp
return "ping " + document.Title;
```

Also use that ping to confirm **which document you're actually attached to** — the active
doc can change between calls if the user switches windows. Verify the title before a write.

---

## The Clean Pipeline

Order matters. Each step assumes the previous one ran.

```csharp
var suffixRx    = new Regex(@"\s*-?\s*(?:\d+\.[A-Za-z]{1,4}|[A-Za-z]{1,4}\.\d+)(?:\s*Copy\s*\d+)?\s*$", RegexOptions.IgnoreCase);
var copyRx      = new Regex(@"\s*Copy\s*\d+\s*$", RegexOptions.IgnoreCase);
var tokenRx     = new Regex(@"\bT\.?I\.?\b\.?", RegexOptions.IgnoreCase);   // firm/phase token
var hyphenRx    = new Regex(@"\s*-\s*");
var multiSpaceRx= new Regex(@"\s{2,}");
var dupWordRx   = new Regex(@"\b(\w+)(\s+\1\b)+", RegexOptions.IgnoreCase);

Func<string,string> clean = (raw) =>
{
    string s = (raw ?? "").Trim();
    s = suffixRx.Replace(s, "");        // 1. trailing "- 2.HR" / "- 0.D" / "- D.0 Copy 1"
    s = copyRx.Replace(s, "");          // 2. bare "Copy 1" left behind
    s = tokenRx.Replace(s, "");         // 3. "T.I." / "TI" tokens
    s = hyphenRx.Replace(s, " - ");     // 4. single space each side of every hyphen
    s = dupWordRx.Replace(s, "$1");     // 5. "ENLARGED ENLARGED X" -> "ENLARGED X"
    s = multiSpaceRx.Replace(s, " ");   // 6. collapse whitespace
    return s.Trim().Trim('-').Trim();   // 7. strip orphaned leading/trailing hyphen
};
```

**Why this order:**
- Suffix strip runs **first** — its `$` anchor needs the raw tail. Normalize hyphens first
  and the `-?` alternation stops matching cleanly.
- Duplicate-word collapse runs **after** token removal — deleting `T.I.` from
  `T.I. TECHNOLOGY TECHNOLOGY PLAN` is what *creates* some adjacent duplicates.
- Whitespace collapse runs **after** hyphen normalization, since ` - ` insertion can
  produce doubles.
- Final `Trim('-')` catches names that were *only* a token plus a hyphen.

Guard the output: **never write an empty result.** If `clean()` returns blank, skip and
count it — a rule bug that blanks 800 titles is far worse than 800 messy ones.

---

## Deriving "Title on Sheet" from View Name

`Title on Sheet` is a per-**view** parameter (`BuiltInParameter.VIEW_DESCRIPTION`), not a
sheet parameter. When it's blank the sheet falls back to displaying the raw View Name —
suffix and all. That's why blank fields still count as broken.

This decouples the two concerns cleanly, and is usually what a user means by "the sheet
title is wrong": the *printed* title is this parameter, while `ViewSheet.Name` /
`ViewSheet.SheetNumber` are a separate problem with their own numbering standards. **Ask
which layer they mean before touching anything.**

```csharp
string paramName = "Title on Sheet";
Parameter p = null;
foreach (Parameter prm in view.Parameters)
    if (prm.Definition != null && prm.Definition.Name == paramName) { p = prm; break; }
// LookupParameter(paramName) also works; iterating lets you inspect StorageType/IsReadOnly
```

### Exclude multi-view sheets

A sheet holding several distinctly-named views has no single view name to sync to, and a
blind pass makes it worse. Collect those view ids in a **pre-pass**, then skip them:

```csharp
var multiViewSheetViewIds = new HashSet<long>();
foreach (var sheet in sheets)
{
    var vpIds = sheet.GetAllViewports();
    if (vpIds.Count > 1)
        foreach (var vid in vpIds) {
            var vpx = document.GetElement(vid) as Viewport;
            if (vpx != null) multiViewSheetViewIds.Add(vpx.ViewId.Value);
        }
}
```

### The write pass

```csharp
var seen = new HashSet<long>();
int changed = 0, unchanged = 0, skipped = 0, emptyResult = 0, setErrors = 0;
var sampleChanged = new List<string>();

foreach (var sheet in sheets)
foreach (var vpId in sheet.GetAllViewports())
{
    var vp = document.GetElement(vpId) as Viewport; if (vp == null) continue;
    var view = document.GetElement(vp.ViewId) as View; if (view == null) continue;
    if (!seen.Add(view.Id.Value)) continue;
    if (multiViewSheetViewIds.Contains(view.Id.Value)) { skipped++; continue; }

    Parameter p = null;
    foreach (Parameter prm in view.Parameters)
        if (prm.Definition != null && prm.Definition.Name == paramName) { p = prm; break; }
    if (p == null) continue;

    string newVal = clean(view.Name);
    if (string.IsNullOrWhiteSpace(newVal)) { emptyResult++; continue; }

    string curVal = p.AsString() ?? "";
    if (string.Equals(curVal.Trim(), newVal, StringComparison.OrdinalIgnoreCase)) { unchanged++; continue; }

    try {
        p.Set(newVal);
        changed++;
        if (sampleChanged.Count < 25)
            sampleChanged.Add(view.Id.Value + "|VN=" + view.Name + "|OLD=" + curVal + "|NEW=" + newVal);
    } catch (Exception ex) {
        setErrors++;
    }
}
```

Return **counts plus a capped sample** (~25 rows). Never the full set.

---

## Workflow

1. **Ping** — confirm connection *and* that `document.Title` is the model you think it is.
2. **Audit, read-only** — count total / already-correct / blank / mismatched, plus
   per-defect counts (contains token, double space, bad hyphen spacing, duplicate word).
   Return counts + ≤20 samples.
3. **Classify** — separate *format-only* noise from *content* mismatches by normalizing
   both strings (`ToUpperInvariant`, `Regex.Replace(s, "[^A-Z0-9]+", " ").Trim()`) and
   comparing. Format-only is mechanically safe; content differences mean the wrong view is
   on the sheet, which is a human call.
4. **Present the rule set and get explicit sign-off** — including what's excluded.
5. **Apply** in one pass with the counters above.
6. **Verify** — re-run the audit query. Populated/blank counts must move by exactly the
   number you changed. Spot-check one known-bad element id by name.

Steps 1–3 are read-only and safe to run unprompted. Step 5 is not.

### Matching heuristic for the audit

For "does the sheet name match its view", a case-insensitive
`view.Name.Trim().StartsWith(sheet.Name.Trim())` is far more robust than modelling every
suffix variant — real models contain transposed suffixes (`- D.0`), stray `Copy N`,
embedded sheet numbers, and missing dashes. `StartsWith` tolerates all of it while still
catching genuine wrong-discipline mismatches.

---

## Pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `执行失败: Exception has been thrown by the target of an invocation` | Any server-side throw, message swallowed | try/catch and return `ex.Message` + inner chain yourself |
| `Starting a new transaction is not permitted` | `new Transaction(...)` in your code | Delete it — MCP owns the transaction |
| `'ElementId' does not contain a definition for 'IntegerValue'` | Revit 2026 API | Use `.Value` |
| `The name 'doc' does not exist` | Wrong variable | Lowercase `document` |
| Tool result rejected as too large | Returned per-element rows | Aggregate in C#, return counts + capped sample |
| Every call times out, even trivial ones | Revit UI thread busy in another tool | Wait; re-ping. Not a query-size problem |
| Changes appear to apply but revert | Wrote to the wrong open document | Ping `document.Title` before writing |
| A view gets processed twice | Placed on multiple sheets | Dedupe on `view.Id.Value` |
| Generic sheets get nonsense titles | Multi-view sheets not excluded | Pre-pass collect + skip |
| Titles blanked | `clean()` returned empty | Guard `IsNullOrWhiteSpace` before `Set` |

---

## Relationship to the Batch Rename add-in

See `references/batch-rename-crosswalk.md` — the normalization rules here and the
BIMpossible Batch Rename tool's transform rules should stay in sync. Rules proven on a live
model here are candidates to become first-class options in the tool; options the tool
already exposes should be reused as the vocabulary here rather than reinvented ad hoc.
