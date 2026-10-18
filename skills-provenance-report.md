# Skill provenance report — vendored/hand-modified skills (2026-08-30)

Traced the git-history-stripped, hand-modified skills back to their upstream GitHub sources. All stats independently verified via `gh api` (not agent claims taken at face value).

## 1. ui-ux-pro-max (active skill — line-by-line diff done 2026-08-30)
- **Upstream:** [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) (uupm.cc), MIT, 123,212 stars / 13,190 forks — verified real, actively maintained (pushed 2026-08-27). Path: `.claude/skills/ui-ux-pro-max/`.
- **This is a genuine two-way divergence, not simple staleness — don't bulk re-vendor.**
  - **SKILL.md — local has prose upstream lacks.** Local's workflow instructions carry an entire "Query Contract" section (retry-once discipline, verify-before-applying, "do not persist unverified output") plus explicit authorization guards — "Never use `--force` without explicit user authorization," "Read an existing MASTER.md before deciding whether `--force` is justified." Upstream's *current* `main` has **none of this** — its guidance is looser ("Always start with `--design-system`" vs local's more careful "use when the task needs a coherent product-wide direction"). This reads like deliberate hand-hardening on the local copy, matching the style of guardrails this workspace already enforces elsewhere (approval-before-action, no silent overwrites). **Do not overwrite this file wholesale — you'd lose real safety rails upstream doesn't have.**
  - **Data/scripts layer — upstream has substantial new capability local never had.** `scripts/core.py` is 464 lines locally vs. 993 upstream (21 functions missing: relevance scoring, query rewriting, legacy-stack handling). `scripts/validate_data.py` is 114 lines locally vs. 1091 upstream (essentially a stub vs. a full validator). Upstream added `scripts/reasoning_contract.py` (new, doesn't exist locally), a `scripts/tests/` suite (doesn't exist locally), and a `data/stacks/` directory with 20 framework-specific CSVs (React, Vue, Next.js, SwiftUI, Flutter, etc.) — **entirely absent locally**, despite the earlier (wrong) research-agent claim that "stacks match."
  - **`data/colors.csv` and `data/products.csv` — schema rewrite, not row tweaks.** Same row count (193) both sides, but ~100% of rows differ — upstream restructured the column layout, not just updated values. This is coupled to `core.py`/`design_system.py` reading that schema — you can't take the new CSV without the new scripts, and vice versa.
  - **Guideline/style/icon/motion counts are real and upstream is ahead:** 98 vs 119 UX guidelines, 84 vs 88 styles, 104 vs 106 icons, 16 vs 18 GSAP presets — but these live inside the same coupled data/script system above, not standalone additions.
- **Why this isn't a simple pull:** the data schema and the scripts that read it changed together upstream. Cherry-picking one file breaks the pairing; taking everything means re-doing your SKILL.md hardening on top of upstream's file, by hand, afterward.
- **Recommendation:** leave as-is unless you specifically want the new stack-CSV coverage or the relevance/query-rewrite engine. If you do want it later, treat it as a full re-vendor of `data/` + `scripts/` together, followed by manually re-applying the Query Contract / `--force`-authorization prose back into the fresh `SKILL.md` — not a partial merge.

## 2. ClaudeKit family — banner-design, brand, design-system, slides, ui-styling
- **Upstream:** same repo as #1 — [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill), `.claude/skills/{name}/SKILL.md`. (The `ckm:` prefix in local frontmatter comes from author metadata `claudekit`, not a separate `claudekit-skills` repo — that's a same-author red herring.)
- **CORRECTED 2026-08-30 — direct diff against upstream `main`, not agent claims.** The earlier research-agent summary of specific fixes/PR numbers was largely wrong or stale; here's what the actual byte diff shows:
  - **`SKILL.md` files:** `banner-design`, `ui-styling`, `brand`, `design-system`, `slides` bodies are content-identical to upstream. **CORRECTED 2026-08-30 (re-verified with a fresh fetch):** the `<args>$ARGUMENTS</args>` line in `slides` is backwards from what was first reported — local already has it, current upstream `main` does not. Left it alone. The `name: ckm:xxx` prefix was real and has been fixed (`name: brand`/`design-system`/`slides`/`ui-styling`, matching upstream's plain naming; `banner-design` was already correct).
  - **`brand/scripts/sync-brand-to-tokens.cjs` — local is AHEAD, not behind.** Local uses `execFileSync` with array args; upstream `main` uses `execSync` with string-interpolated file paths (a command-injection risk upstream, not present locally). Local also has more defensive/robust color-table parsing and null-guards upstream lacks. **Do not re-vendor this file** — it would reintroduce a real vulnerability.
  - **`ui-styling/scripts/shadcn_add.py` — local is AHEAD, not behind.** Local reads the pinned shadcn version from `package.json` (fallback `"2.3.0"`); upstream `main` calls `shadcn@latest` unpinned (nondeterministic builds — the opposite of what the earlier report claimed).
  - **`ui-styling/scripts/tailwind_config_gen.py` — local is AHEAD, not behind.** Local has the CWE-94 plugin-name allowlist regex (`_VALID_PLUGIN_NAME`) blocking code injection via crafted `require()` strings; upstream `main` has no such check.
  - **`brand/scripts/{extract-colors,validate-asset,inject-brand-context}.cjs` — identical**, zero diff.
  - **`design-system/scripts/generate-slide.py` — was genuinely behind, FIXED 2026-08-30 (`afa0097`).** Upstream had `from html import escape` plus `_e()`/`_safe_url()` helpers used across title/URL/content injection points; local had **zero** HTML-escaping anywhere in the file — a real XSS gap. Pulled the fix directly; local file is now byte-identical to upstream `main`.
- **Action:** done — `generate-slide.py` escaping pulled, and `ckm:` → plain naming fixed across `brand`/`design-system`/`slides`/`ui-styling`. Do NOT bulk re-vendor `sync-brand-to-tokens.cjs`, `shadcn_add.py`, or `tailwind_config_gen.py` — that would overwrite local's more-secure code with upstream's less-secure current `main`.

## 3. "Elite design" family — brutalist-skill, minimalist-skill, soft-skill, output-skill, brandkit
- **Upstream:** [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill) monorepo, `skills/{name}/SKILL.md`. MIT, 82,459 stars / 5,648 forks — verified real (not inflated).
- **Local vs upstream:** byte-for-byte match on distinctive lines in all five files (last touched 2026-03-20 for the first four, 2026-04-28 for brandkit). No divergence — local copies are current, cosmetic-only edits.
- **Action:** none needed — already current.

## 4. taste-skill
- **Upstream:** same repo, [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill), flagship `skills/taste-skill/SKILL.md` (v2, "design-taste-frontend"), 1206 lines.
- **CONFIRMED 2026-08-30 — direct diff:** local `skills/taste-skill/SKILL.md` was **byte-for-byte identical** to upstream `main`, both 1206 lines. Zero diff.
- **Deliberate local deviation since 2026-09-11:** frontmatter `name:` is `taste-skill` (upstream: `design-taste-frontend`), so it matches the directory — the identity the runtime actually dispatches on, and the convention every other local skill follows. Body unchanged; do not "resync" this line away.
- **Action:** none needed — current apart from that one line.

## 5. Remaining active skills — checked 2026-08-30, none vendored
Scanned every active skill under `skills/` (audit, close, context-budget, converge-review, csuite, ctxcheck, ctxdex, dashboard-update, local-audit, next, all `revit-*`, slop-audit, tdd, wiki) for frontmatter `author`/`version` fields and any GitHub/marketplace reference in the body. None carry third-party attribution. `wiki`'s only GitHub link points at your own private `YourBIMpossible/BIMpossible_Workspace` repo (a doc cross-reference, not an origin). `csuite` has a `version: 1.0.0` field but no author — looks self-versioned, not vendored.
- **Conclusion:** these are all self-authored/proprietary. No upstream exists to diff against — nothing to chase here.

## Settled — no action needed
- `graphify` — resynced and pushed (`f18feb8`, this session, turn 1).
- `taste-skill` — byte-identical to upstream v2.
- `ui-ux-pro-max` — real divergence both directions (your hardening vs. their new capability); user decision: **leave as-is**.
- All 10 parked ClaudeKit/elite-family skills — not active, not worth chasing.
- All other active skills — proprietary, no upstream.

## reverse-engineer-anything (vendored 2026-10-17, unmodified)
- **Upstream:** [morluto/rea](https://github.com/morluto/rea), npm `rea-agents` 6.5.0, MIT. Copied verbatim from `<npm prefix -g>/node_modules/rea-agents/skills/reverse-engineer-anything/` (skill metadata version "42", tool_count 207).
- **Pairing:** the skill describes the `rea` MCP server's tools, so it must track the installed package. Refresh both together: `tools\Install-Rea.ps1 -Update`, then re-copy the skill directory from the new package and commit. Do not hand-edit the skill.
- **Register/install per machine:** `bootstrap.ps1` step 6 (`tools/Install-Rea.ps1`). Runbook with rationale (why not `rea setup`): `F:\Claude-Tools\reports\2026-10-17__rea-install\REA-INSTALL-RUNBOOK.md`.
eports6-10-17__rea-install\REA-INSTALL-RUNBOOK.md`.

