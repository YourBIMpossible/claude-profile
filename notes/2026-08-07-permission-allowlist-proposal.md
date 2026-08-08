# Proposal: narrow read-only Bash allowlist (NOT implemented)

Status: **proposal/audit only** — no settings.json change made. Owner ruling
2026-08-07: permission policy is a control boundary; narrow it from observed
failures, not generalized intent.

## Observed classifier blocks (the evidence base)

Exact commands the auto-mode classifier blocked in the 2026-08-07 session
lineage, with mutation assessment:

| Blocked command | Read-only? | Allowlist candidate? |
|---|---|---|
| `gh pr view 5` | Yes — GitHub API GET, renders PR | **Yes** |
| `python F:/AI-Dev/.tools/ctxdex/ctxdex.py stats` | *Almost* — see caveat below | **Yes, with caveat** |
| `gh pr merge 6 --squash --delete-branch` | **No** — merges + deletes branches | No — correct scrutiny |
| `git rebase --skip; git status --short \| head; git log --oneline -8` | **No** — rebase mutates history | No — correct scrutiny (compound contained a mutator) |

Only the first two were false positives. The other two blocks were the
classifier doing its job; retry-after-explicit-instruction was the right flow.

## Resolved executables and argument shapes

- **gh** — `C:\Program Files\GitHub CLI\gh` (v2.93.0). `gh pr view [<number|url|branch>] [--json ...] [--comments] [--web]` performs API reads only; `--web` opens a browser (annoying, not mutating). No flag of `pr view` mutates state.
- **ctxdex** — `py F:\AI-Dev\.tools\ctxdex\ctxdex.py stats [--project NAME]`, Python 3.14 via the `py` launcher. Working dir irrelevant (`--project` defaults to CWD folder name, used only as a DB filename key).

**Caveat:** `ctxdex stats` calls `sqlite3.connect()`, which **creates an empty
`data/<project>.db` if absent** — a (harmless, empty-schema) filesystem write.
"Read-only" is therefore not literally true. Acceptable residual, but it must
be stated, and an arbitrary `--project` value creates an arbitrarily-named DB
file inside `ctxdex\data\` only.

## Narrowest configuration change (if approved later)

Two `permissions.allow` entries in `~/.claude/settings.json`:

```json
"Bash(gh pr view:*)",
"Bash(py F:/AI-Dev/.tools/ctxdex/ctxdex.py stats:*)"
```

Nothing broader: no `gh pr:*` (would cover merge/close/edit), no `gh api:*`
(arbitrary REST verbs), no bare `ctxdex.py:*` (would cover `index`/`purge`,
which write and delete).

## Adversarial review

- **Compound-command smuggling** (`gh pr view 5; rm -rf x`, `&&`, `|`): Claude
  Code's permission engine splits on shell operators and each segment must
  match a rule — the prefix rule does not blanket-approve the compound. Risk
  low, but verify behavior on the installed version before trusting it.
- **Command substitution / expansion** (`gh pr view $(...)`, backticks, `%VAR%`
  / `$VAR` tricks): substitution inside an otherwise-matching command must not
  auto-approve; Claude Code treats commands containing substitution as
  unmatched. Same verify-before-trust note.
- **gh aliases:** `gh alias set` cannot shadow built-in commands (gh refuses),
  so `pr view` cannot be redefined. One alias exists today (`co` = `pr
  checkout`, mutating) — it is NOT covered by the proposed prefix and must
  stay uncovered. `gh alias set` itself is not allowlisted.
- **Git aliases:** no git rules proposed, so the git-alias attack surface
  (e.g. an alias whose expansion mutates) is not opened at all.
- **Misleading "read-only" subcommands:** `gh pr view --web` launches a
  browser (accepted); `ctxdex stats` creates an empty DB (stated above).
  Neither `gh pr view` nor `stats` can modify repo state, settings, or
  credentials through any documented flag as of the versions pinned here.
- **Path spoofing:** the ctxdex rule pins the absolute script path — a
  different `ctxdex.py` elsewhere does not match. The rule breaks (fails
  closed) if the tool moves.

## Rollback path

1. Before any change: copy `settings.json` to
   `settings.json.pre-allowlist-<date>.bak` (same pattern as the
   context-budget disable backup).
2. Rollback = delete the two lines (or restore the backup). Hooks hot-reload;
   permission changes take effect next session at latest.
3. Re-verify with a deliberate `gh pr view` in a fresh session.

## Decision

Deferred. Revisit when block frequency justifies it; implement exactly the two
rules above, nothing wider, with the backup-first rollback.
