# Profile maintenance — standing operational rules

Standing rules for operating this repo as a *live deployment*, not just a codebase.
Dated session notes stay in their own files; this file holds only rules that remain
true until explicitly retired.

## Hooks: never switch the checkout under a registered hook (2026-08-07)

`~/.claude/settings.json` registers hooks by absolute path into this repo's main
checkout. A `git checkout`/branch switch that removes the hook file makes the hook
*executable* missing — the harness surfaces that as a **blocking** PostToolUse error
before the hook's own fail-open logic can ever run. Observed live during the
2026-08-07 release sync: the main checkout sat on a branch predating the hook, and
every Bash call blocked with "can't open file ... ctxdex_autoindex.py".

For any future checkout/worktree transition in this repo:

1. Temporarily remove or disable the hook registration in `settings.json`, switch
   the checkout, then restore the registration; **or**
2. Prefer a stable installed hook path outside a mutable git checkout (copy-on-
   deploy), so branch state can never take the executable away.

Fail-open only protects against errors *inside* the hook script. A missing script
is upstream of that protection.
