---
name: ctxcheck
description: "Repo reality-check CLI - validates that a repo's documented claims (anchor files, referenced paths, env/endpoint contracts, commit/ADR links, claimed commands, doc freshness) still match reality. Use before implementing against docs/config, before a release, or when about to rely on an old command, path, or ADR. Read-only; reports drift, never fixes it."
---

# ctxcheck — repo reality-check

CLI at `F:/Claude-Tools/ctxcheck/ctxcheck.py` (single-file stdlib Python, its
own local git repo). It answers one question: **do this repo's documented
claims still hold?**

## When to run

- Before implementing something based on a repo's docs, config, or SKILL claims.
- Before a release or merge that leans on documented contracts.
- After infrastructure or repo-layout changes (moved dirs, renamed services,
  new compose file, rerouted endpoints).
- Whenever you're about to trust an old command, file path, commit SHA, or ADR
  reference found in documentation — check it instead of assuming.

## Commands

```bash
py F:/Claude-Tools/ctxcheck/ctxcheck.py run claude-profile
```

`run <name>` resolves `configs/<name>.toml` inside the ctxcheck repo
(currently: `claude-profile`, `bimpossible`, `memory`). Alternatives: `run <repo-dir>`
(uses `<repo>/.ctxcheck.toml`) or `--config <file> --repo <dir>`.

```bash
py F:/Claude-Tools/ctxcheck/ctxcheck.py run bimpossible --only files,endpoints
py F:/Claude-Tools/ctxcheck/ctxcheck.py run bimpossible --json
py F:/Claude-Tools/ctxcheck/ctxcheck.py run claude-profile --verbose
py F:/Claude-Tools/ctxcheck/ctxcheck.py run claude-profile --strict
```

- `--only` filters categories: `files,refs,env,compose,endpoints,links,commands,staleness`.
- `--json` for machine-readable results; `--verbose` shows PASS lines too.
- `--strict` promotes WARNs to a failing exit — use for release gates.

## The severity contract

- **Declared claims FAIL hard** (exit 1): anything the config asserts — required
  files, declared refs, env names, compose services, endpoint routes, commands.
- **Discovered drift WARNs** (exit 0 unless `--strict`): issues found by
  scanning — markdown path refs, commit SHAs, ADR links, staleness — because
  scans have false positives.

## Working rules

- Target configs live **in the ctxcheck repo** (`configs/*.toml`), so ctxcheck
  validates a target repo without ever modifying it.
- **Never auto-fix warnings.** A WARN in another repo's docs is that repo's
  work: report it, or queue one line for a session in the owning repo.
- The `commands` category executes owner-authored config entries verbatim
  (trusted local input; `shell=True` by design). Add commands only to
  ctxcheck's own configs, never generate them from untrusted content.

## Interpretation rules (learned the hard way)

- FastAPI route prefixes may live on `APIRouter(prefix=...)` in each router
  file, not in `main.py` — write endpoint checks against `backend/**/*.py`.
- Staleness uses git last-commit date; file `mtime` is only the fallback when
  the target isn't a git repo. A fresh mtime on an old commit is not fresh.

## Boundaries

No hook, no MCP layer, no config files planted in target repos. ctxcheck never
runs *itself*; it is invoked externally on a schedule (wired 2026-09-07):
`/audit` runs it first in both modes (`skills/audit/SKILL.md`, "Reality-check"),
and the `bimpossible-weekly-full-audit` scheduled task runs it as its REALITY-CHECK
step every Monday — FAILs become `CTX-` findings, WARNs a drift appendix.
Scope guard per `docs/adr/0001`: ctxcheck reads and reports — it never indexes,
rewrites, or fixes.
