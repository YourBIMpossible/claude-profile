---
name: ctxcheck
description: Repo reality-check CLI — validates that a repo's documented claims (anchor files, referenced paths, env/compose/endpoint contracts, commit/ADR links, claimed commands, doc freshness) still match reality. Use before implementing against docs/config, before a release or merge, after infra or repo-layout changes, or when about to rely on an old command, path, or ADR from documentation. Read-only against target repos; reports drift, never fixes it.
---

# ctxcheck — repo reality-check

CLI at `F:/AI-Dev/.tools/ctxcheck/ctxcheck.py` (single-file stdlib Python, its
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
py F:/AI-Dev/.tools/ctxcheck/ctxcheck.py run claude-profile
```

`run <name>` resolves `configs/<name>.toml` inside the ctxcheck repo
(currently: `claude-profile`, `bimpossible`). Alternatives: `run <repo-dir>`
(uses `<repo>/.ctxcheck.toml`) or `--config <file> --repo <dir>`.

```bash
py F:/AI-Dev/.tools/ctxcheck/ctxcheck.py run bimpossible --only files,endpoints
py F:/AI-Dev/.tools/ctxcheck/ctxcheck.py run bimpossible --json
py F:/AI-Dev/.tools/ctxcheck/ctxcheck.py run claude-profile --verbose
py F:/AI-Dev/.tools/ctxcheck/ctxcheck.py run claude-profile --strict
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

No hook, no MCP layer, no auto-run, no config files planted in target repos.
Scope guard per `docs/adr/0001`: ctxcheck reads and reports — it never indexes,
rewrites, or fixes.
