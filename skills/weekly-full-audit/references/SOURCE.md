# Lens references — provenance

These nine files are verbatim copies. They were copied, never moved; the originals stay in place.

| Field | Value |
|---|---|
| Source repository | BIMpossible-Workspace |
| Source path (repo-relative) | `04_PromptsThatWorked/Audits/bimpossible-audit-skill/references/` |
| Source commit | `f0355bbc3758e6e83fb4aa1b31d9ebe6a8926030` (last commit touching the folder) |
| Copied | 2026-10-17 |

| File | git blob SHA (equal to the source blob at the commit above) |
|---|---|
| audit-matrix.md | fc56fd5c0694ebbfa2f20b2abb03ea6427235656 |
| auto-fix.md | 71db15bb089f72c30e6e1ee672ca17143bb0d222 |
| google-audit.md | dc263b05a062453fefdafd984a7cb9d7eb30eccd |
| hygiene-audit.md | b92a5160cee0f8288199412bc704968e20be0982 |
| performance-audit.md | 26175cafd5ed342524227ef67a3c6c140f7fad8c |
| perp-audit.md | 80bda08b46269e5e22536a401ae8a81c2b7ece08 |
| reliability-audit.md | 27e5712198f3d67e8a5d4c0b8b3792df8f689551 |
| security-audit.md | 0578d45240a77a3b1661b59e7042d4953886019c |
| slop-audit.md | 6879780f766afa83ddfb1ade2b2e074bf7755fae |

Re-sync: compare each blob with `git hash-object` against the source folder at its newest commit; re-copy on difference and update both tables. Never edit a copy in place.

## Machine paths inside the copies

Five lines in three copies name machine paths: `auto-fix.md` (2), `hygiene-audit.md` (1), `performance-audit.md` (2). They are part of the verbatim lens text. The skill never executes or resolves them; every path the skill uses comes from its own arguments. `auto-fix.md` is read only when `--autofix` is active, and step 13 substitutes `--repo-root` for its working-directory lines.
