---
name: revit-room-tag-phasing
description: >-
  Diagnose and fix runaway/blank Revit room tags, and tag rooms in a loaded
  architectural link — via mcp-server-for-revit. Use when a model is bloated or slow
  from tens of thousands of room tags, when "Tag All Rooms" produces blank tags, when
  tags reference rooms in a link, or building/repairing a "Retag All Rooms" tool.
  Triggers on "why are there so many room tags", "my tags are blank", "clean up room
  tags", "tag rooms in the link". Covers the LinkElementId overload
  and why phase is NOT a reliable blank-tag predictor for explicit-target tagging (an
  unloaded link is).
---

# Revit Room-Tag Phasing & Linked-Room Tagging

Placing and cleaning room tags looks trivial and is not. Two things bite hard on real
multi-phase projects with a linked architectural model — one is a genuine Revit rule,
the other looks like one but was disproven by direct live testing. Get the second one
wrong and you rebuild a bug that already shipped and got reverted once here.

1. **"Tag All Rooms" is placement-blind, not just phase-blind.** It tags every room
   (including every room in every loaded link) in the active view regardless of whether
   that room is actually placed/enclosed (`Area > 0`). An unplaced/unenclosed room's tag
   renders **blank ("?")** — this part is real and load-bearing.
2. **An UNLOADED link is a primary, easily-missed blank-tag cause.** A tag whose
   `TaggedRoomId` resolves through a `RevitLinkInstance` whose `GetLinkDocument()` is
   `null` cannot resolve its room at all and renders blank. On a real 208k-element model
   this was the dominant cause: **all 8,395 blank tags found (of 136,918 total) traced to
   ONE unloaded link — zero traced to a resolved, placed room of any phase relationship.**

**Do NOT gate room-tag eligibility on phase.** An earlier version of this skill (and two
successive implementations built from it, 2026-08-16/17) taught that a linked room whose
phase differs from the view's phase renders a blank tag via
`doc.Create.NewRoomTag(LinkElementId, ...)`, and that a name-equality or phase-sequence
check was therefore a required precondition. **Direct, repeated live testing (rollback-only
`SubTransaction` probes against a loaded link, `doc.Regenerate()`'d before inspection)
disproved this**: explicit-target `NewRoomTag(LinkElementId, ...)` renders a populated,
non-blank tag for a placed, resolvable linked room **regardless of phase — earlier, later,
or mismatched.** `Room.GetPhaseStatus()` was also confirmed unusable for this: it returns
`PhaseStatus.None` for every `Room`, since Rooms never populate
`PHASE_CREATED`/`PHASE_DEMOLISHED`. The **correct** eligibility gate is: **placed
(`Area > 0`) + on the view's level.** Nothing about phase.

Blanks are not "orphaned" and must not be diagnosed as such — see the trap below.

## The #1 trap: `RoomTag.Room` is blind to linked rooms

`RoomTag.Room` resolves **local** rooms only. For a tag pointing at a room in a **link**,
it returns `null` — even when the tag is perfectly valid. So `Room == null` is NOT an
orphan test; on a linked-arch project it misclassifies nearly every good tag as junk.
**Never** mass-delete on `Room == null`.

The authoritative reference is `RoomTag.TaggedRoomId`, a `LinkElementId`:

- **Local room:** `lid.LinkInstanceId == ElementId.InvalidElementId`; the room is
  `doc.GetElement(lid.HostElementId)`.
- **Linked room:** `lid.LinkInstanceId` is the `RevitLinkInstance` id; resolve the room
  through the link:
  ```csharp
  var rli   = doc.GetElement(lid.LinkInstanceId) as RevitLinkInstance;
  var ldoc  = rli.GetLinkDocument();                 // null if the link is unloaded
  var room  = ldoc.GetElement(lid.LinkedElementId) as Autodesk.Revit.DB.Architecture.Room;
  ```

## The eligibility rule your tagging logic must implement

A room is taggable — and won't produce a blank "?" — when BOTH:

1. **It is placed & enclosed:** `room.Area > 0`. (`Area == 0` means not-placed or
   not-enclosed; tagging it yields a blank. This is the dominant real cause together
   with an unloaded link — see above.)
2. **It's on the view's level** (the level your tool actually cares about — this is a
   tool requirement, not a Revit rendering rule).

**Do not add a phase check here.** It was tried twice (name-equality, then
phase-sequence) and both were reverted after live evidence — see the correction at the
top of this skill. If you're tempted to add one because it "seems like it should matter
for a linked model," that's the same reasoning that produced the bug; run the decisive
test below on YOUR model/version before writing any phase-based condition, don't assume
prior modeling intuition transfers.

For a **linked** room, resolvability is the thing to guarantee upstream (see the trap
below) — a room read successfully off a **loaded** link with a valid location and level
already satisfies "can be tagged"; don't re-derive phase compatibility on top of it.

Optionally also require the room's location point (transformed by `rli.GetTotalTransform()`)
to fall inside the view crop, if you only want tags within the drawn area.

## Placing a tag on a LINKED room

The plain `NewRoomTag(ElementId, UV, ElementId)` overload works for **local** rooms only.
For a linked room use the **`LinkElementId` overload** — this is the non-obvious piece:

```csharp
Transform xf = rli.GetTotalTransform();
XYZ p        = ((LocationPoint)room.Location).Point;   // link coords
XYZ h        = xf.OfPoint(p);                           // host coords
UV  uv       = new UV(h.X, h.Y);
doc.Create.NewRoomTag(new LinkElementId(rli.Id, room.Id), uv, view.Id);   // inside a Transaction
```

Enumerate link rooms filtered by the two rules above, place one tag each, and **no blank
tag is ever created** — the over-tagging is eliminated at the source rather than cleaned up
after.

### The decisive test — run this on YOUR model before trusting any phase claim

`doc.Create.NewRoomTag(new LinkElementId(linkId, roomId), uv, viewId)` does not throw and
does not return null for a cross-phase room — that part is real and worth knowing. What
it does with the *content* of that tag is the part to verify directly rather than assume,
because two independent, plausible-sounding phase rules were both wrong here:

- ~~"cross-phase renders blank"~~ — disproven: creating an explicit-target tag for an
  Existing-phase room in a Highrise-phase view yielded a populated `TagText`, not blank,
  after `doc.Regenerate()`.
- ~~"only later-phase (created-after-view) renders blank, sequence matters"~~ — also
  disproven on further testing: rooms both earlier AND later than the view's phase tagged
  correctly via the same explicit-target call.

**Run this before writing any phase condition:** via a rollback-only `SubTransaction`
(never mutate the live model to test), place an explicit-target tag on a **placed room in
a LOADED link** whose phase differs from the view's, `doc.Regenerate()`, then read the
tag's `TagText`/room-name back. If it's populated, phase is not your blank-tag cause —
look at link-load state and `Area` instead. Don't generalize this skill's finding to a
different Revit version or model without re-running this test; do state clearly when you
haven't. (When probing at all, resolve the linked room's phase in the LINK document if
you need it for anything else — reading `ROOM_PHASE` against the host doc yields a bogus
`'?'` and looks like a mismatch that isn't one.)

## Revit 2027 API gotcha

`ElementId.IntegerValue` is **removed** in Revit 2027 — use `ElementId.Value` (a `long`).
Dictionary keys off element ids must be `long`, not `int`. Code that compiles on 2024 fails
here. (Conversely 2024's own compiler flags `IntegerValue`/`ElementId(int)` as the
deprecated members — this is a genuine cross-version split.)

## Running code through mcp-server-for-revit

`send_code_to_revit` wraps your snippet in `AIGeneratedCode.CodeExecutor.Execute(Document
document, Object[] parameters)`. The injected document variable is **`document`** (not
`doc`, `uiapp`, `uidoc`, or `app`). Start snippets with `var doc = document;`. There is no
`UIApplication` in scope — you get the active `Document` only. If unsure what's in scope,
reflect the enclosing method:
`new System.Diagnostics.StackTrace().GetFrame(0).GetMethod()` and read its parameters.

## Workflow

### 1. Diagnose (read-only) — classify the tags truthfully, by RESOLVABILITY not phase
Resolve every `RoomTag` via `TaggedRoomId` and bucket:
`local+placed (KEEP)` · `linked+placed, link LOADED (KEEP)` · `linked, link UNLOADED
(blank junk — the dominant real cause)` · `linked+unplaced, area 0 (junk)` · `linked room
gone` (deleted from the link since the tag was made). Report per-view counts and, for the
unloaded-link bucket, which link(s) — that's the evidence the user needs before any delete
(often: "just load this link and re-run" fixes most of it). Do NOT bucket by phase
match/mismatch — sample a few "mismatched" tags first and confirm they're actually blank
before trusting phase as the classifier; the evidence above says they usually aren't.

### 2. Decide the cleanup path (with the user)
- **Surgical:** delete only the confirmed-blank buckets (unloaded-link, unplaced, room-gone);
  keep everything else. Less rebuild.
- **Clean slate (best when building/validating a Retag tool):** delete ALL room tags, then
  re-tag with the placed+level-eligible enumerator. "All room tags" is a clean, well-defined
  deletion and the retag becomes the tool's own test harness.

### 3. Safety — this touches model data
Room-tag purge/retag is a **write** (badge: caution). On a **workshared / client** model,
confirm the exact target set with the user first, then apply and let the human sync. Batch
with per-element error capture so one grouped/pinned/workset-locked tag can't abort the whole
run, and report a kept/deleted/skipped tally. Never sync to central on the user's behalf.

**Two hard-won gotchas when purging through `send_code_to_revit` (both cost a full retry):**

1. **Do NOT open your own `Transaction`.** The MCP harness runs your snippet *inside an
   already-open transaction* — check `doc.IsModifiable` and you'll see `True`. Calling
   `new Transaction(doc,…).Start()` then throws on **every** element ("can't start a
   transaction while one is active"), so a batch that looks correct fails 100% with nothing
   deleted. Call **`doc.Delete(...)` directly**; the harness commits when your script returns.
   Chunk the ids (~5k) and wrap each `doc.Delete(chunk)` in `try/catch` with a per-element
   fallback — a throw doesn't kill the harness transaction, so surviving elements still delete.

2. **A large delete overruns the client timeout but the server keeps going.** Purging
   >100k tags returns `执行代码失败: 代码执行超时` (client gave up) while the server *finishes the
   commit*. **Do not retry** — that would double-work or fight a running commit. Send a trivial
   sentinel (`return "RESYNC-1";`); the previous call's real result surfaces on that next call.
   Then run a fresh read-only recount to confirm the true post-state before doing anything else.

## When to use / not use

Use for: room-tag bloat or slowdown, blank/empty tags, "why so many tags", linked-arch room
tagging, and designing or repairing a Retag-All-Rooms tool. It generalizes to any model
reached through `mcp-server-for-revit`.

Not for: schedules/keynotes/other tag families (the resolvability rule here is room/link
specific), or non-linked models where tag-all is already fine. See also
[[revit-explicit-target-linked-room-tag-eligibility]] for the general verification method
behind the phase-is-not-the-cause finding — use that skill when the question is "how do I
verify a claim about what makes a Revit tag render blank," not just for rooms.
