---
name: revit-sheet-view-manager
description: >
  Use this skill for any bulk view or sheet management task in Revit via the revit-mcp
  connection. Triggers include: assigning or reassigning scope boxes to views, trimming
  grid extents, centering viewports on sheets, setting sheet parameters in bulk (phase,
  category, sort order, title block parameters), adding or reordering sheets in print sets,
  renaming views or sheets, creating dependent views, placing views on sheets, and any
  task involving organizing or configuring multiple Revit views or sheets at once. Use
  this skill even when the user phrases it casually — "fix the grids", "add these to the
  print set", "set the sheet parameters", "the views are already there just wire them up".
  Always prefer this skill over ad-hoc code when working with revit-mcp on view/sheet tasks.
---

# Revit Sheet & View Manager

Handles bulk configuration, organization, and management of views and sheets in workshared
Revit models via the revit-mcp MCP connection. Covers the full lifecycle from view setup
through sheet publishing.

---

## Critical API Rules

These apply to every `send_code_to_revit` call. Violating them causes silent failures or
exceptions.

| Rule | Detail |
|---|---|
| Document variable | Always `document` — not `doc`, `ActiveDocument`, `RevitDoc` |
| No nested transactions | MCP wraps all code in a transaction. Never use `new Transaction(...)` |
| Workshared checkout | Call `WorksharingUtils.CheckoutElements(document, ids)` before modifying elements not owned by you |
| View duplication method | `view.CanViewBeDuplicated(option)` not `view.CanBeDuplicated(option)` |
| Grid curve Z | Always preserve Z from original curve when calling `SetCurveInView`. Z=0 throws `ArgumentException` |
| Sheet rename conflicts | Use temp names when swapping — Revit won't allow two sheets with the same number simultaneously |
| Viewport IDs after sync | IDs can change after syncing to central. Re-query by name or sheet number, not stored IDs |
| Parameter lookup | Use `element.LookupParameter("Name")` for shared/project params; `get_Parameter(BuiltInParameter.X)` for built-ins |

---

## Reusable Code Patterns

### Find Views by Name

```csharp
var targetNames = new HashSet<string> { "VIEW NAME 1", "VIEW NAME 2" };
var results = new FilteredElementCollector(document)
    .OfClass(typeof(View))
    .Cast<View>()
    .Where(v => targetNames.Contains(v.Name) && !v.IsTemplate)
    .ToList();
```

### Find Sheets by Number

```csharp
var sheetsByNum = new Dictionary<string, ViewSheet>();
foreach (ViewSheet s in new FilteredElementCollector(document).OfClass(typeof(ViewSheet)))
    sheetsByNum[s.SheetNumber] = s;
```

### Find Scope Boxes

```csharp
var scopeBoxes = new Dictionary<string, Element>();
foreach (var e in new FilteredElementCollector(document)
    .OfCategory(BuiltInCategory.OST_VolumeOfInterest))
    scopeBoxes[e.Name] = e;
```

### Find Title Block by Family Name

```csharp
var tb = new FilteredElementCollector(document)
    .OfCategory(BuiltInCategory.OST_TitleBlocks)
    .OfClass(typeof(FamilySymbol))
    .Cast<FamilySymbol>()
    .FirstOrDefault(t => t.FamilyName == "YOUR FAMILY NAME");
```

### Checkout Before Modifying

```csharp
WorksharingUtils.CheckoutElements(document,
    new List<ElementId> { element.Id });
```

---

## Core Operations

### A. Assign Scope Box to View

```csharp
var param = view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP);
if (param != null && !param.IsReadOnly)
    param.Set(new ElementId(scopeBoxId));
```

### B. Trim Grid Extents to Crop Box (+ Optional Offset)

Run this after assigning scope boxes. The grid bubbles extend model-wide by default
and prevent proper viewport centering on sheets.

```csharp
double offset = 2.0; // feet — extend beyond crop for bubble visibility (0 = flush)

var crop = view.CropBox;
double xMin = crop.Min.X - offset, xMax = crop.Max.X + offset;
double yMin = crop.Min.Y - offset, yMax = crop.Max.Y + offset;

var grids = new FilteredElementCollector(document, view.Id)
    .OfCategory(BuiltInCategory.OST_Grids)
    .OfClass(typeof(Grid)).ToElements();

foreach (Grid g in grids) {
    // Switch to ViewSpecific extents
    g.SetDatumExtentType(DatumEnds.End0, view, DatumExtentType.ViewSpecific);
    g.SetDatumExtentType(DatumEnds.End1, view, DatumExtentType.ViewSpecific);

    var curves = g.GetCurvesInView(DatumExtentType.ViewSpecific, view);
    if (curves == null || curves.Count == 0) continue;
    var line = curves[0] as Line;
    if (line == null) continue;

    var p0 = line.GetEndPoint(0);
    var p1 = line.GetEndPoint(1);
    double z = p0.Z; // MUST preserve — do not use 0

    bool isHoriz = Math.Abs(p1.Y - p0.Y) < 0.001;
    bool isVert  = Math.Abs(p1.X - p0.X) < 0.001;

    XYZ newP0, newP1;
    if (isHoriz) {
        double y = p0.Y;
        double xa = Math.Max(Math.Min(p0.X, p1.X), xMin);
        double xb = Math.Min(Math.Max(p0.X, p1.X), xMax);
        if (xb - xa < 0.01) continue;
        newP0 = p0.X < p1.X ? new XYZ(xa,y,z) : new XYZ(xb,y,z);
        newP1 = p0.X < p1.X ? new XYZ(xb,y,z) : new XYZ(xa,y,z);
    } else if (isVert) {
        double x = p0.X;
        double ya = Math.Max(Math.Min(p0.Y, p1.Y), yMin);
        double yb = Math.Min(Math.Max(p0.Y, p1.Y), yMax);
        if (yb - ya < 0.01) continue;
        newP0 = p0.Y < p1.Y ? new XYZ(x,ya,z) : new XYZ(x,yb,z);
        newP1 = p0.Y < p1.Y ? new XYZ(x,yb,z) : new XYZ(x,ya,z);
    } else continue;

    g.SetCurveInView(DatumExtentType.ViewSpecific, view, Line.CreateBound(newP0, newP1));
}
```

### C. Create Dependent View

```csharp
WorksharingUtils.CheckoutElements(document, new List<ElementId>{ parentView.Id });
var depId = parentView.Duplicate(ViewDuplicateOption.AsDependent);
var depView = document.GetElement(depId) as View;
depView.Name = "NEW VIEW NAME";
```

### D. Create Sheet

```csharp
var sheet = ViewSheet.Create(document, titleBlockSymbolId);
sheet.SheetNumber = "E201A";
sheet.Name = "LIGHTING PLAN - LEVEL 1 - SECTOR A";
sheet.LookupParameter("S&L_Sheet Phase")?.Set("01 POWER & LIGHTING");
sheet.LookupParameter("S&L_Sheet Category")?.Set("200 CEILING PLANS");
sheet.LookupParameter("S&L_Sheet Sort Order")?.Set("2");
```

### E. Place / Reposition Viewport on Sheet

**Finding the right center position:**
1. Check an equivalent parent/reference sheet for its plan viewport `BoxOutline`
2. Compute usable area center: midpoint of that outline's X and Y ranges
3. Offset Y up by half the label height so the box+label block is vertically centered

```csharp
// Get reference position from existing sheet
var refVp = document.GetElement(refVpId) as Viewport;
var outline = refVp.GetBoxOutline();
double centerX = (outline.MinimumPoint.X + outline.MaximumPoint.X) / 2.0;
double centerY = (outline.MinimumPoint.Y + outline.MaximumPoint.Y) / 2.0;

// Place new viewport
var vp = Viewport.Create(document, sheet.Id, viewId, new XYZ(centerX, centerY, 0));

// Adjust for label height after placement
var label = vp.GetLabelOutline();
double labelH = label.MaximumPoint.Y - label.MinimumPoint.Y;
vp.SetBoxCenter(new XYZ(centerX, centerY + labelH / 2.0, 0));
```

**Title line too long**: Delete and re-place the viewport. Revit resets the title line
width to match the viewport on fresh placement.

```csharp
document.Delete(existingViewportId);
Viewport.Create(document, sheet.Id, viewId, new XYZ(centerX, centerY, 0));
```

### F. Set Title Block Parameters

```csharp
var tbInstances = new FilteredElementCollector(document, sheet.Id)
    .OfCategory(BuiltInCategory.OST_TitleBlocks)
    .OfClass(typeof(FamilyInstance));

foreach (FamilyInstance tb in tbInstances) {
    var p = tb.LookupParameter("PARAM NAME");
    if (p != null && !p.IsReadOnly) p.Set(value); // string or int
}
```

### G. Bulk Set Sheet Parameters

```csharp
foreach (ViewSheet sheet in targetSheets) {
    WorksharingUtils.CheckoutElements(document, new List<ElementId>{ sheet.Id });
    sheet.LookupParameter("S&L_Sheet Phase")?.Set("01 POWER & LIGHTING");
    sheet.LookupParameter("S&L_Sheet Category")?.Set("300 POWER PLANS");
    sheet.LookupParameter("S&L_Sheet Sort Order")?.Set("3");
}
```

### H. Add Sheets to Print Set

```csharp
var printSet = document.GetElement(printSetId) as ViewSheetSet;
var viewSet = new ViewSet();
foreach (View v in printSet.Views) viewSet.Insert(v); // preserve existing
foreach (ViewSheet s in newSheets) viewSet.Insert(s);

WorksharingUtils.CheckoutElements(document, new List<ElementId>{ printSet.Id });
printSet.Views = viewSet;
```

### I. Rename Views or Sheets (Conflict-Safe)

When swapping names (A→B, B→C, C→A), always use a two-pass temp rename:

```csharp
// Pass 1: to temps
foreach (var item in items) item.Name = item.Name.Replace("SECTOR A", "SECTOR TMPA")
                                                   .Replace("SECTOR B", "SECTOR TMPB")
                                                   .Replace("SECTOR C", "SECTOR TMPC");
// Pass 2: to finals
foreach (var item in items) item.Name = item.Name.Replace("SECTOR TMPA", "SECTOR B")
                                                   .Replace("SECTOR TMPB", "SECTOR C")
                                                   .Replace("SECTOR TMPC", "SECTOR A");
```

Apply same pattern to `sheet.SheetNumber` and `sheet.Name` separately.

---

## Workflow: Views & Sheets Already Exist

Most common scenario. Steps:

1. **Audit** — query existing views/sheets to confirm names, IDs, current scope boxes,
   current parameters, what's already on sheets
2. **Assign scope boxes** — operation C above
3. **Trim grids** — operation B above
4. **Set sheet parameters** — operation G above
5. **Set title block params** — operation F above
6. **Place/reposition viewports** — operation E above
7. **Add to print set** — operation H above
8. **Verify** — re-query after user syncs to confirm

## Workflow: Views & Sheets Need Creating

1. **Audit** — confirm nothing already exists to avoid duplicates
2. **Create dependent views** — operation C above
3. **Assign scope boxes** — operation A above
4. **Trim grids** — operation B above
5. **Create sheets** — operation D above
6. **Place viewports** — operation E above
7. **Set title block params** — operation F above
8. **Add to print set** — operation H above

---

## Audit Queries (Run Before Any Changes)

```csharp
// What dependent views exist under a parent?
foreach (View v in allViews)
    if (v.GetPrimaryViewId().IntegerValue == parentId)
        // log name, id, scope box param

// What viewports are on a sheet?
foreach (var vpId in sheet.GetAllViewports()) {
    var vp = document.GetElement(vpId) as Viewport;
    var view = document.GetElement(vp.ViewId) as View;
    var center = vp.GetBoxCenter();
    var outline = vp.GetBoxOutline();
    // log view name, center, box dimensions
}

// What's in the print set?
var printSet = document.GetElement(printSetId) as ViewSheetSet;
foreach (View v in printSet.Views)
    if (v is ViewSheet s) // log sheet number and name

// What print sets exist?
new FilteredElementCollector(document).OfClass(typeof(ViewSheetSet));
```

---

## Common Pitfalls & Fixes

| Symptom | Cause | Fix |
|---|---|---|
| `document does not exist` | Wrong variable name | Use `document` |
| `Starting a new transaction is not permitted` | Nested transaction | Remove all `using (var tx...)` blocks |
| `curve not coincident` on SetCurveInView | Z=0 passed instead of original | Preserve `p0.Z` from existing curve |
| Viewport off-center on sheet | Grids extending beyond crop | Trim grids before placing viewport |
| Viewport off-center after grid trim | Label height not accounted for | Add `labelH / 2.0` to Y center |
| Title line too long | Viewport placed before trim, or moved | Delete and re-place viewport |
| Element not found by ID after sync | Revit reassigns IDs on sync | Re-query by name/sheet number |
| Sheet rename fails | Duplicate sheet number collision | Use two-pass temp rename |
| Scope box param read-only | View template controlling it | Detach template, set, reattach — or set via template |
| Print order wrong | Manual order set controls UI order | User must drag in print dialog — cannot be set via API in manual mode |
| `CheckoutElements` silent fail | Element owned by another user | Notify user to relinquish or sync first |

---

## Print Set Notes

- **Finding print sets**: `FilteredElementCollector(document).OfClass(typeof(ViewSheetSet))`
- **Adding sheets**: rebuild the `ViewSet` from scratch including existing + new, then set `printSet.Views`
- **Order**: `OrderedViewList` only affects browser-organization mode. Manual order must be set by the user via drag-and-drop in the Print dialog. Tell the user upfront if this will be needed.
- **Sort parameters**: `S&L_Sheet Sort Order` + `Sheet Number` (ascending) is the typical sort key for schedule-based sheet lists. Match sector sheets to their parent's sort order value.
