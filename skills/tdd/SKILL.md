---
name: tdd
description: Red-green TDD for behavior changes in tested code (Python/pytest, TS/JS, off-Revit C#). Use BEFORE writing implementation for any feature or bug fix — bug fix = failing regression test first. Not for throwaway scripts, docs/config, or Revit-API-bound glue (extract the testable core instead).
---

# TDD — red, green, refactor

The contract: **no implementation before a failing test; no "done" without pasted green output.**

## The loop

1. **Red** — write ONE test for the next small behavior. Run it; it must fail for the
   RIGHT reason (a failed assertion, not an import/fixture error). Keep the failing output.
2. **Green** — write the minimum code that passes. Run it; keep the passing output.
3. **Refactor** — clean up while green; run again.
4. Repeat in small slices until the behavior is covered.

## Hard rules (house scars, not theory)

- **Bug fix = regression test first.** Reproduce the bug in a test that fails on current
  code before touching a fix. Can't reproduce it → you don't understand the bug yet;
  keep diagnosing.
- **Assert outcomes, not survival.** The house defect is "reports success, quietly did
  nothing." A test that only proves "no exception" passes on do-nothing code. Assert the
  observable result: the value changed, the row exists, the count moved N → N+1.
- **Watch every test fail once.** A test never seen red may be testing the mock, the
  fixture, or nothing at all.
- **Never weaken an assertion to get to green.** If the test was wrong, fix the test and
  watch it fail again for the new right reason.
- **Evidence, not claims.** The report quotes the test run ("N passed in X.XXs") with the
  command that produced it — pasted, not paraphrased.
- **Unconfirmed system boundary → confirm before testing around it.** If it's not obvious
  where "the seam" actually is (which layer owns the contract, what the real external
  edge is), ask the user before writing a test that assumes one.
- **Mock system boundaries, not internal collaborators.** Stub the external edge (network,
  filesystem, a real third-party API); never mock the code under test or its neighbors —
  that tests the mock, not the design.
- **No tautological tests.** If the test computes its expected value with substantially
  the same logic as the implementation, it will fail together with a real bug and pass
  together with a wrong one. Assert against an independently-derived expected value.

## Scope boundaries

- **Revit API code** can't run headless — and mocking the Revit API tests the mock, so
  don't. Extract decision/parsing/selection logic into plain classes and TDD those; keep
  the API boundary thin and cover it via the live-smoke/audit track instead.
- **Spikes** are fine untested: explore freely, then either discard the spike or backfill
  tests before it merges.
- **Sequencing:** TDD gets the change green locally; the normal gate (`/converge-review`
  on BIMpossible, repo CI elsewhere) still runs before push. Neither replaces the other.
