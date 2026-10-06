# GRRP Reviewer role

Discovery against the run brief. Writes findings; never decides parent closure or
expands scope.

1. Start only after `grrp.py init` (brief + criteria exist). Read `status` output:
   known IDs are not rediscovered.
2. Load every supplied finding as `source: original` — Low/Info and "missing
   proving test" included. Nothing is dropped; a retention/noise filter (e.g.
   review-all NOT RETAINED) still records the item with `assessment: disproved`
   + evidence, or `intentional_behavior` + evidence.
3. Record each substantive finding immediately: location, evidence,
   consequence, confidence, required proof, suggested classification.
4. Check `/next` (`queue.yaml`) and other ledgers before proposing work already
   owned → `externally_owned` + `duplicate_of:`.
5. Evidence discipline: static reading is not runtime proof; merged is not
   deployed; deployed is not verified; injected identity is not real auth; one
   platform/version is not all; a waiver is not verification. Prefer
   red-before/green-after.
6. Read-only discovery proceeds without editorial check-ins. Never mutate
   production data to simplify proof; never touch another session's work.
7. At closeout reconcile coverage: every supplied item has a block, and the
   brief's scope was actually covered (say what was not, in the change log).
