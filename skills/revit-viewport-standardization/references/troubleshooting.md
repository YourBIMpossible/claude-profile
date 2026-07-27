# Troubleshooting — `send_code_to_revit` quirks & failure recovery

Read this the first time a script won't compile, a write "succeeds" but nothing changed,
or a call times out. Each item below cost real time to discover.

## `send_code_to_revit` C# template quirks

The host inserts your code into a method body and compiles it. That context has sharp edges:

| Symptom | Cause | Fix |
|---|---|---|
| `The name 'doc' does not exist` | The injected Document variable is **`document`**, not `doc` | Add `Document doc = document;` at the top |
| `'System' is a namespace but is used like a type`, cascading errors | You put **top-level `using` directives** — invalid inside a method body | Delete them. `System`, `System.Collections.Generic`, `System.Linq`, `Autodesk.Revit.DB` are already in scope |
| `StringBuilder could not be found` | `System.Text` is the one namespace **not** implicitly in scope | Write `System.Text.StringBuilder` (full name) |
| `Starting a new transaction is not permitted` | You opened your own `Transaction`; the host already wraps your code in one | Remove it — just write directly; the host commits |
| `ElementId does not contain 'IntegerValue'` | Removed in the Revit 2026 API | Use `ElementId.Value`; construct with `new ElementId((long)id)` |

## The Scope Box trap (silent zero-match)

`view.LookupParameter("Scope Box")` is **ElementId storage type**, so `AsString()` returns
`""` — a helper that only calls `AsString()` matches **zero** views and fails silently.
Resolve the id through a name dictionary, or use `AsValueString()`:

```csharp
var scopeBoxNames=new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_VolumeOfInterest)
  .WhereElementIsNotElementType().ToDictionary(e=>e.Id,e=>e.Name);
// then p.AsElementId() -> scopeBoxNames[id]
```

## Post-commit verification is mandatory

**In-call reads always show the value you just set** (the change is staged but not
committed). The commit happens after your code returns, and under load the host can report
a write "done" **without persisting it** (silent partial commit — worst during/after
timeouts). So a phase's "moved=N / fixed=N" count is **not** proof.

Always verify in a **separate** `send_code_to_revit` call that re-reads the property. The
whole multi-hour cleanup that motivated this skill came from trusting in-call success and
skipping the post-commit read.

## Timeouts and desync

Two distinct failures — treat them differently:

- **Client `-32001` "Request timed out"**: your client gave up, but **the server kept
  running** and its result surfaces on a *later* call (you'll see a stale chunk's output
  come back from a trivial ping). Recovery: send a trivial ping (`return "PING";`) to drain
  it, then a sentinel (`return "RESYNC-1";`) and confirm you get **that exact string** back
  before continuing. Because all writes are idempotent, if you can't tell whether a chunk
  committed, just re-run that `VIEW_SKIP` range.
- **Server-side `代码执行超时` ("code execution timeout")**: the host **killed** the run and
  returned the error to you. Nothing pending, nothing to drain — just retry with a smaller
  `VIEW_TAKE`.

If even a trivial ping keeps timing out, the server is still churning a prior op (or a modal
dialog is blocking the add-in). Wait (a background sleep, then one ping) rather than piling
on more calls — each new call queues behind the stuck one and makes it worse.

If `say_hello` and a trivial `return` both fail with **"connect to revit client failed"**,
the add-in listener is down (not just busy). Ask the user to toggle the Revit MCP server
add-in off/on, or restart Revit; nothing on your side fixes it.

## The active document can switch under you

`document` always points at whatever is **frontmost** in Revit. Mid-session it silently
became a *different* model (an architectural file), and a batch nearly ran against the wrong
document — caught only because the target list came back empty. Put this first line in
**every** script and it aborts safely:

```csharp
if(document.Title!="ACCC_D_EL_SL") return "WRONG DOC: "+document.Title;
```

## Chunk sizing

Geometry-touching calls (`GetBoxCenter`/`SetBoxCenter`/`GetCurvesInView`/`LabelOffset`) run
~0.5–1 s each on a large (100k+ element) model, and the host's ceiling is variable. Keep
`VIEW_TAKE ≤ ~60`; if a range times out, halve it. A warm geometry cache can make a re-run
of the same range 100× faster, so a retry that "flew" isn't a red flag.

## Reading a plotted PDF to review results (no poppler needed)

If asked to review an exported sheet PDF and `pdftoppm`/poppler isn't installed, render with
**PyMuPDF** (`pip install PyMuPDF`; `import fitz`) — it has no external deps. Build a
page→sheet-number index from the title-block text region, then render only the sheets you
need to PNG for visual inspection. Extract element positions from the vector layer for exact
measurements. Check the PDF's plot timestamp (`doc.metadata['creationDate']`) against your
edit times to tell a stale export from a current one.
