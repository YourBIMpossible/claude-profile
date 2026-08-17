---
name: revit-explicit-target-linked-room-tag-eligibility
description: >-
  Verify — don't assume — what actually makes an explicit-target Revit tag
  (NewRoomTag(LinkElementId, ...) or any other explicit-target placement API) render
  blank versus populated, especially anything involving phase. Use whenever you're about
  to write an eligibility/precondition check for tagging, annotating, or scheduling
  elements resolved through a RevitLinkInstance, or whenever someone proposes a rule
  based on phase names, phase sequence, or Element.GetPhaseStatus for Rooms or other
  spatial elements. Triggers on "why is this tag blank", "should I check phase before
  tagging", "gate this by phase", or when reviewing/building a Retag-style tool. This is
  the general verification method; for the room-tag-specific findings already proven by
  it, see revit-room-tag-phasing.
---

# Explicit-Target Linked-Element Tag Eligibility — Verify, Don't Infer

Two successive, plausible-sounding phase rules were built, tested with unit tests, built,
deployed, and shipped as fixes for Revit room-tag bloat — a phase-name-equality gate, then
a phase-sequence gate — before either was tested against the one thing that actually
mattered: does the *specific API call in use* (`NewRoomTag(LinkElementId, ...)`, an
**explicit-target** placement, not a spatial/visibility-driven one) actually produce a
blank tag under the claimed condition. Neither had been. Both were wrong. The real cause —
an unloaded `RevitLinkInstance` — was sitting in the data the whole time and only surfaced
once someone resolved every blank tag's actual link reference instead of reasoning from
Revit's general phase-visibility rules.

The lesson generalizes beyond rooms: **explicit-target APIs (anything taking a
`LinkElementId`, or otherwise given a specific element reference rather than doing its own
spatial/visibility resolution) do not necessarily honor the visibility rules you'd expect
from normal in-view interaction.** Don't reason from "Revit doesn't show X in a view of the
wrong phase" to "therefore an explicit-target API call for X will also come out blank."
Those are different code paths inside Revit and can, and did, behave differently.

## The rule

**Do not infer tag/annotation rendering behavior from phase names, phase sequence, or any
other indirect signal — including `Element.GetPhaseStatus()`, which returns `None` for
`Room`/`SpatialElement` and so isn't even a signal at all for that type.** If a design
depends on "does explicit-target placement X render populated under condition Y," that
claim needs a **direct, live test of X under Y**, not a plausible mechanism, not a
one-sample anecdote, and not carrying forward a rule that worked for a *different* Revit
API (e.g. a UI pick tool, which IS visibility-driven).

## How to run the direct test safely

1. **Use a real, loaded link and a real room/element** — not a synthetic/mocked one. The
   whole point is testing the actual API against actual Revit state; a mock can't surface
   what a mock-author didn't anticipate (which is exactly what happened twice here).
2. **Wrap the probe in a `SubTransaction`, then `RollBack()`.** Never mutate the live model
   to run a decisive test, and never hold an open transaction across turns — this is
   available inside the harness the MCP-driven probe runs in (it already holds an open
   transaction; `SubTransaction` nests inside it and rolls back cleanly).
3. **Call `doc.Regenerate()` before inspecting the result.** Reading a freshly-created
   element's derived state (e.g. `TagText`) before regeneration can show stale/default
   values that look like a false positive or false negative for the very thing you're
   testing.
4. **Test both directions of any binary condition**, not just the one your hypothesis
   predicts will fail. The phase-sequence design here was disproven partly because someone
   tested the *later*-phase direction, not just the earlier-phase direction the design was
   built to fix — testing only one direction would have shipped it again.
5. **Sample real existing failures and resolve their actual cause before generalizing.**
   Don't diagnose a blank tag as "probably phase" — resolve its `TaggedRoomId`/link
   reference and check: is the link loaded? Does the room still exist? Is it placed
   (`Area > 0`)? A large-enough census (hundreds, not one or two) turns "probably" into
   "100% of N traced to cause X."

## What this already proved, worth reusing as a documented finding

- **An unloaded `RevitLinkInstance` is a primary, easily-missed cause of a blank
  explicit-target tag** — `RevitLinkInstance.GetLinkDocument()` returns `null`, so nothing
  downstream can resolve, and this has nothing to do with phase. Check link-load state
  before reaching for a phase explanation.
- **`Room.GetPhaseStatus()` is not usable for Rooms** — it returns `PhaseStatus.None`
  regardless of the room's actual phase state, because Rooms never populate
  `PHASE_CREATED`/`PHASE_DEMOLISHED`. Any design leaning on it is building on a signal that
  isn't there.
- These are **specific to the tested Revit version and model** — restate that scope
  explicitly whenever citing this finding rather than generalizing it to "Revit" broadly or
  to every tag/annotation family.

## Scope boundary

This is a verification *method*, not a blanket claim that phase never matters anywhere in
Revit. Native, visibility-driven placement (e.g. a UI pick tool constrained to the active
view's visible elements) genuinely is phase-filtered — that part of Revit's behavior was
never in question. What's in question, every time, is whether a *specific* explicit-target
API call inherits that filtering — and that's an empirical question with a five-minute
answer via steps 1–4 above, not a five-minute guess.

## When to use / not use

Use for: any Revit tool whose eligibility/precondition logic makes a claim about what
causes an element to render blank, missing, or incorrect — before writing that logic, and
especially before reusing someone else's plausible-sounding rule for it.

Not for: general Revit API questions unrelated to explicit-target placement or
phase-adjacent eligibility claims. For the specific room-tag findings this method already
produced, use [[revit-room-tag-phasing]] directly instead of re-deriving them.
