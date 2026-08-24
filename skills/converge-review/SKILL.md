---
name: converge-review
description: >-
  Minimal local-verification rule for BIMpossible pushes (2026-08-18
  delivery-workflow rebuild — the old converge loop and Complete-Wave.ps1
  hand-off are retired). Trigger phrases: "converge", "converge-review",
  "get this push-ready".
---

# Converge-Review (retired to a one-line rule, 2026-08-18)

If local verification is red, fix locally and rerun the canonical verifier
(`.\Verify-Local-CI.ps1 -BaseRef origin/main`). Do not use remote CI as a debug loop.

The full delivery contract lives in the code repo's `docs/engineering/DELIVERY.md`.
