---
name: revit-project-audit
description: >
  Use this skill at the start of any Revit session before making any changes to views,
  sheets, or parameters. Triggers include: "scan the model", "audit the project",
  "what exists", "what's missing", "check the scope boxes", "gap analysis", "before
  we start", "what disciplines need work", "check the arch link", or any request to
  understand the current state of the model. Also use proactively whenever starting
  view or sheet management work — always audit before modifying. Runs a 13-step guided
  wizard that scans and verifies the model at each step before collecting user confirmation.
  Works on any project regardless of discipline count, sector count, or naming convention.
---

# Revit Project Audit Skill

Runs a guided 13-step intake wizard, scanning the model at each step and presenting
findings for user confirmation before proceeding. Discovers everything at runtime —
no hardcoded project data.

**Always run before touching views, sheets, or parameters.**

---

## Critical API Rules

- Document variable: `document` (host), `link.GetLinkDocument()` (linked)
- No nested transactions — audit is read-only
- **Timeout risk**: Models with 500+ views will timeout on broad collectors. Always
  filter by name keyword: `.Where(v => v.Name.Contains("KEYWORD"))`. Run in
  discipline-specific batches, never collect all views at once.
- Linked doc returns `null` if not loaded — always null-check before querying

---

## Guided Wizard — 13 Steps

Each step scans the model, presents findings, and asks the user to confirm or correct
before moving on. Never skip a step. Never proceed past a blocker without user action.

---

### Step 1 — Worksharing

**Scan:**
```csharp
bool isWorkshared = document.IsWorkshared;
var currentUser = document.Application.Username;
// Check last sync time if available
```

**Present:** Is the model workshared? Who is the current owner?

**Ask:** "Has the model been synced recently and is it ready to proceed?"

**Choices:**
- Yes — synced and ready → continue
- I need to sync first → stop, remind user to sync and re-run

**Blocker:** If user needs to sync, stop here. Do not proceed.

---

### Step 2 — Arch Link

**Scan:**
```csharp
var links = new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfClass(typeof(Autodesk.Revit.DB.RevitLinkInstance));
foreach (Autodesk.Revit.DB.RevitLinkInstance link in links) {
    bool isLoaded = Autodesk.Revit.DB.RevitLinkType.IsLoaded(document, link.GetTypeId());
    var linkedDoc = link.GetLinkDocument();
    // log: link.Name, isLoaded, linkedDoc?.Title
}
```

**Present:** List all links with loaded status.

**Ask:** "Which link is the architectural model? Is it loaded?"

**Choices:**
- Arch link loaded → continue to Step 3 (auto-scan scope boxes)
- Can load it → stop, user loads link and re-runs
- Working without it → skip Steps 3–4, go to Step 5 (manual sector entry)

**Blocker:** If arch link needs loading, stop here.

---

### Step 3 — Scope Boxes (Arch → Host)

*Skip if no arch link. Go to Step 4 for manual entry.*

**Scan arch:**
```csharp
// From arch linked doc
foreach (var e in new Autodesk.Revit.DB.FilteredElementCollector(archDoc)
    .OfCategory(Autodesk.Revit.DB.BuiltInCategory.OST_VolumeOfInterest)) {
    var bb = e.get_BoundingBox(null);
    // log: e.Name, bb.Min.X/Y, bb.Max.X/Y (round to 1 decimal)
}
```

**Scan host:**
```csharp
foreach (var e in new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfCategory(Autodesk.Revit.DB.BuiltInCategory.OST_VolumeOfInterest)) {
    var bb = e.get_BoundingBox(null);
    // Compare to arch extents within ±2ft tolerance
}
```

**Exclude** catch-all boxes: names containing "Overall", "Site", "S&L", or extents
spanning the full building footprint.

**Present mapping:**
```
Arch: Sector 01  →  Host: SECTOR A  ✓ matched
Arch: Sector 02  →  Host: SECTOR B  ⚠ name mismatch — rename host
Arch: Sector 03  →  Host: —         ✗ missing — create manually
                     Extents: X=79 to 207, Y=-56 to 24
```

**Ask:** "Are scope box names correct and all sectors present?"

**Choices:**
- All correct → continue
- Names need updating → we rename to match arch (two-pass temp rename)
- Some missing → stop, user creates missing scope boxes manually and re-runs

**Blocker:** Missing scope boxes. Scope boxes cannot be created via API.

**Naming rule:** Host scope box names must exactly match arch. All view and sheet
names follow: `[DISCIPLINE] - [LEVEL] - [ARCH SECTOR NAME]`

---

### Step 4 — Sector Names (Manual — no arch link only)

*Skip if arch link is loaded — names auto-discovered in Step 3.*

**Ask:** "Enter the sector names exactly as they appear in the arch model."

```
e.g. Sector 01, Sector 02, Sector 03, Sector 04
     North Wing, South Wing
     Zone A, Zone B, Zone C
```

These drive all downstream view and sheet naming.

**0 or 1 sector** → no sector treatment needed, overall views only. Confirm with user.

---

### Step 5 — Title Block & Room Tag

**Scan:**
```csharp
// Title block family types
var tbTypes = new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfCategory(Autodesk.Revit.DB.BuiltInCategory.OST_TitleBlocks)
    .OfClass(typeof(Autodesk.Revit.DB.FamilySymbol))
    .Cast<Autodesk.Revit.DB.FamilySymbol>()
    .Select(tb => tb.FamilyName + " : " + tb.Name)
    .Distinct().OrderBy(s => s).ToList();

// Room tag families
var rtFamilies = new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfCategory(Autodesk.Revit.DB.BuiltInCategory.OST_RoomTags)
    .OfClass(typeof(Autodesk.Revit.DB.FamilySymbol))
    .Cast<Autodesk.Revit.DB.FamilySymbol>()
    .Select(rt => rt.FamilyName + " : " + rt.Name)
    .Distinct().OrderBy(s => s).ToList();
```

**Present both lists.**

**Ask:** "Which title block and room tag should be used?"

- Title block → applied to all new sheets created
- Room tag → applied via Tag All to every sector view created or modified

Store confirmed selections. These are passed to the sheet-view-manager skill at execution.

---

### Step 6 — S&L Sheet Parameters

**Scan existing sheets for parameter values in use:**
```csharp
// Sample from first sheet of each existing discipline
var sheet = ...; // first sheet per prefix
var phase = sheet.LookupParameter("S&L_Sheet Phase")?.AsString();
var category = sheet.LookupParameter("S&L_Sheet Category")?.AsString();
var sort = sheet.LookupParameter("S&L_Sheet Sort Order")?.AsString();
```

**Present findings:**
```
S&L_Sheet Phase:       01 POWER & LIGHTING (found on existing sheets)
200 series Sort Order: 2
300 series Sort Order: 3
Lighting Category:     200 CEILING PLANS
Power Category:        300 POWER PLANS
Other disciplines:     ⚠ Phase/Category not yet set — confirm values
```

**Ask:** "Are these parameter values correct? Any corrections for new disciplines?"

These values are applied to all new and existing sector sheets at execution.

---

### Step 7 — Disciplines in Scope

**Scan print sets and browser groups to show what's already set up:**
```csharp
foreach (Autodesk.Revit.DB.ViewSheetSet ps in new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfClass(typeof(Autodesk.Revit.DB.ViewSheetSet)))
    // log: ps.Name, ps.Views.Size
```

**Present discovered discipline groups.**

**Ask:** "Which disciplines are in scope for this project? Select all that apply."

Standard groups (present as multi-select):
- 01 Power & Lighting
- 02 Technology
- 03 Security
- 04 Fire Alarm
- 05 AV
- 06 Nurse Call

Never assume. Most projects start with Power & Lighting only — others added as project grows.
Confirmed list defines what gets configured AND what is eligible for cleanup.

---

### Step 8 — View Templates

**Scan per confirmed discipline (batched):**
```csharp
var templates = new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfClass(typeof(Autodesk.Revit.DB.View))
    .Cast<Autodesk.Revit.DB.View>()
    .Where(v => v.IsTemplate)
    .OrderBy(v => v.Name)
    .ToList();
// Filter to templates relevant to confirmed disciplines
```

**Expected per discipline (4 templates):**
- `[CODE] OVERALL RCP` → 200 series parent views
- `[CODE] SECTOR RCP` → 200 series dependent views
- `[CODE] OVERALL FP` → 300 series parent views
- `[CODE] SECTOR FP` → 300 series dependent views

**Present:** Which templates exist, which are missing.

**Ask:** "Are all required templates present? Confirm names."

**Blocker:** Missing templates — views for that discipline cannot be created until
templates exist. Flag and skip that discipline's view creation at execution.

---

### Step 9 — Parent Views

**Scan per confirmed discipline (batched by keyword):**
```csharp
var keyword = "FIRE ALARM"; // one discipline at a time
var views = new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfClass(typeof(Autodesk.Revit.DB.View))
    .Cast<Autodesk.Revit.DB.View>()
    .Where(v => !v.IsTemplate &&
               (v.ViewType == Autodesk.Revit.DB.ViewType.CeilingPlan ||
                v.ViewType == Autodesk.Revit.DB.ViewType.FloorPlan) &&
                v.Name.ToUpper().Contains(keyword.ToUpper()))
    .OrderBy(v => v.Name).ToList();

foreach (var v in views) {
    bool isDependent = v.GetPrimaryViewId().IntegerValue != -1;
    var sp = v.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP);
    int sbId = sp != null ? sp.AsElementId().IntegerValue : -1;
    // log: v.ViewType, v.Name, v.Id, isDependent, sbId
}
```

**Classify:** OVERALL / PARENT / SECTOR / OTHER (skip)

**Assign scenario per discipline × series × level:**

| Scenario | Condition | Action at Execution |
|---|---|---|
| A | PARENT + sectors exist, scope boxes assigned | None |
| B | Views + sectors exist, SB = -1 | Assign scope boxes + trim grids |
| C | PARENT exists, no dependents | Create dependents |
| D | OVERALL exists, no PARENT | Duplicate OVERALL → PARENT → dependents |
| E | No views at all | Create from template → PARENT → dependents |

**Present per discipline:** which scenario applies to each series and level.

**Ask:** "Is this correct? Any adjustments?"

Only Level 1 and Level 2 get sector treatment unless user specifies otherwise.

---

### Step 10 — Sheets

**Scan per discipline prefix:**
```csharp
var sheets = new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfClass(typeof(Autodesk.Revit.DB.ViewSheet))
    .Cast<Autodesk.Revit.DB.ViewSheet>()
    .Where(s => s.SheetNumber.StartsWith(prefix))
    .OrderBy(s => s.SheetNumber).ToList();

foreach (var s in sheets) {
    int vpCount = s.GetAllViewports().Count;
    var sort = s.LookupParameter("S&L_Sheet Sort Order")?.AsString();
    var phase = s.LookupParameter("S&L_Sheet Phase")?.AsString();
    var cat = s.LookupParameter("S&L_Sheet Category")?.AsString();
    // log: s.SheetNumber, s.Name, vpCount, sort, phase, cat
}
```

**For each sector sheet check:**
- Exists with correct number and name (matching arch sector naming)
- Plan viewport placed (vpCount > 1)
- S&L parameters set
- Title block sector checkbox matches sheet sector

**Present:** existing sheets summary, missing sheets to be created.

**Ask:** "Is this correct?"

---

### Step 11 — Print Sets

**Scan:**
```csharp
foreach (Autodesk.Revit.DB.ViewSheetSet ps in new Autodesk.Revit.DB.FilteredElementCollector(document)
    .OfClass(typeof(Autodesk.Revit.DB.ViewSheetSet))) {
    var inSet = new System.Collections.Generic.HashSet<string>();
    foreach (Autodesk.Revit.DB.View v in ps.Views)
        if (v is Autodesk.Revit.DB.ViewSheet s) inSet.Add(s.SheetNumber);
    // log: ps.Name, ps.Views.Size, which sector sheets are/aren't included
}
```

**Present:** each print set with sector sheet inclusion status.

**Note:** Print order cannot be set programmatically in Manual mode. After execution,
user must drag sector sheets into correct position in the print dialog.

**Ask:** "Are the print sets correct?"

---

### Step 12 — Cleanup

**Ask:** "The following disciplines are not in scope. Remove their views, sheets,
and print sets to clean up the model?"

For each out-of-scope discipline, show counts only (not individual names):
```
  Technology  — 18 views, 10 sheets, 1 print set
  Security    — 18 views, 10 sheets, 1 print set
```

**Choices:** Delete all / select specific disciplines / skip cleanup

**Safety rules:**
- Summarize at discipline level only — never list individual elements
- Require explicit confirmation before any deletion
- Deletion order: print sets → sheets → views
- Checkout before deleting; skip and report elements owned by other users
- Sync to central after cleanup before any further work

---

### Step 13 — Sync Confirmation & Run

**Ask:** "Sync to central now before we proceed. This ensures clean element ownership
and up-to-date IDs."

**Choices:**
- Synced and ready → generate full action plan and run
- Not yet → stop, remind user to sync and re-run

**On confirmation — produce action plan:**
```
=== ACTION PLAN ===
Host: [model name]
Arch Link: [status]

CONFIRMED SETTINGS
  Sectors:      [list]
  Title block:  [family : type]
  Room tag:     [family : type]
  Disciplines:  [list]
  Levels:       1 & 2

ACTIONS (in order)
  1. Rename scope boxes to match arch (if needed)
  2. Create missing views by discipline and scenario
  3. Assign scope boxes to existing unassigned views
  4. Trim grids on all sector views (crop ± 2ft offset)
  5. Tag All rooms in every sector view using confirmed room tag
  6. Create missing sheets with confirmed title block
  7. Place and center viewports on sheets
  8. Set S&L sheet parameters
  9. Set title block sector checkboxes
  10. Add sheets to print sets
  11. Run cleanup (if confirmed)

BLOCKED ITEMS (resolve before running)
  [list any missing templates, scope boxes, etc.]

Proceed with execution?
```

---

## Workflow Decision Tree

```
START
  Step 1: Workshared + synced? → NO: stop and sync
  Step 2: Arch link loaded?
    YES → Step 3: scan + match scope boxes
    NO  → Step 4: manual sector name entry
  Step 5: Confirm title block + room tag
  Step 6: Confirm S&L parameters
  Step 7: Confirm disciplines in scope
  Step 8: Verify view templates per discipline
  Step 9: Classify parent views by scenario (A–E)
  Step 10: Audit sheets per discipline
  Step 11: Audit print sets
  Step 12: Identify cleanup candidates
  Step 13: Sync + confirm → generate action plan → execute
```

---

## Scope Box Creation Note

Scope boxes cannot be created or transferred via the Revit API. If scope boxes are
missing from the host:

1. Report each missing box name and exact extents (X/Y min/max in feet)
2. Stop — user creates them manually in the host and syncs
3. Re-run the audit from Step 3 to confirm before proceeding

---

## Cleanup Module — Execution Details

Only runs after Step 12 confirmation. Deletion order is mandatory:

```csharp
// 1. Print sets first
//    If dedicated to one discipline → delete
//    If mixed → rebuild ViewSet without discipline's sheets

// 2. Sheets (viewports auto-delete with sheet)
foreach (var sheet in sheetsToDelete) {
    WorksharingUtils.CheckoutElements(document, new List<ElementId>{ sheet.Id });
    document.Delete(sheet.Id);
    // catch: log "could not delete [sheetNumber] — owned by another user"
}

// 3. Views last
foreach (var view in viewsToDelete) {
    WorksharingUtils.CheckoutElements(document, new List<ElementId>{ view.Id });
    document.Delete(view.Id);
}
```

**Post-cleanup report:**
```
=== CLEANUP COMPLETE ===
  Technology  — deleted 18 views, 10 sheets, 1 print set ✓
  ⚠ Security  — 2 views not deleted (owned by another user)
                Relinquish and re-run cleanup.

Sync to central before proceeding.
```
