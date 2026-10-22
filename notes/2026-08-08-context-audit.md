# Context audit — 2026-08-08

Question: did the new tools (ctxcheck, ctxdex, skillspector, context-budget,
dashboard-update, revit-sheet-view-manager, the two hooks) add too much standing
context, versus their claimed value (build memory, make graphify better)?

Baseline: the 2026-08-06 slimdown (superpowers / pr-review-toolkit / plugin-dev
uninstalled, design pack parked).

## What actually loads every session

Only three things cost tokens in EVERY session, regardless of use:

| Item | Size | ~Tokens | Trend since 08-06 |
|---|---|---|---|
| Skill **descriptions** (16 personal skills) | 8,769 chars | ~2,200 | +~2,550 chars (new skills) |
| CLAUDE.md (global 4,916 + {repos}\AI-Dev 1,751) | 6,667 chars | ~1,700 | ↓ (condense landed) |
| MEMORY.md index | 10,084 chars (56 files) | ~2,500 | growing |
| **Total standing** | ~25.5 KB | **~6,400** | |

Skill **bodies** (graphify 34.7KB, taste-skill 87KB, etc.) load only on invoke —
zero standing cost. Same for the memory topic files (only the index loads).

## The new tools, individually

| Tool | Standing cost | Verdict |
|---|---|---|
| ctxdex CLI + autoindex hook | 437-char desc; hook stdout always empty → **0** | KEEP — pure win; converts would-be context floods into on-demand recall |
| ctxcheck CLI | 448-char desc | KEEP — on-demand only |
| skillspector | **0** (CLI, not a skill) | KEEP |
| context-budget skill | 319-char desc | KEEP — its whole job is reducing context |
| dashboard-update skill | 300-char desc | KEEP |
| session-start-pull hook | prints nothing, logs to file | KEEP — 0 |
| revit-sheet-view-manager | **1,043-char desc** | TRIM — see below |

Net new standing cost from everything built since the slimdown: **~650 tokens**.
That is cheap, and the design pattern is right: CLIs + zero-output hooks +
on-invoke skill bodies all cost nothing until used. The memory/graphify thesis
holds — ctxdex and the reranker add capability at ~0 standing cost.

## The real overweight item is NOT the new tools

The four Revit skill descriptions are 3,971 chars — **45% of the entire
personal-skill description budget** for 4 of 16 skills:

- revit-viewport-standardization: 1,208 chars (inlines C# quirk lists — "document-not-doc, no top-level usings, ElementId.Value…" — routing info belongs in the body, not the trigger)
- revit-sheet-view-manager: 1,043 (enumerates every sub-task + a long EXCEPTION paragraph)
- revit-functionality-audit: 868 (method lore — "build BOTH TFMs", house-defect hunting — body material)
- revit-tool-review: 852 (house conventions list — body material)

A description only needs to answer "when do I trigger, and which sibling wins."
Rewriting these four to ~250–300 chars each saves ~2,800 chars (~700 tokens/session)
— more than the entire cost of every new tool combined.

## Other findings

- MEMORY.md: 10KB / 56 files, ~90 lines — inside the 200-line/25KB guidance but
  the fastest-growing standing item. A consolidate-memory pass would retire the
  dated "recent project state" entries (several are now closed/merged).
- Plugin skills (anthropic-skills ×10, hookify ×5, claude-md-management ×2,
  cowork ×2) add roughly as much description text as all personal skills
  combined. hookify and cowork-plugin-management look unused — disabling them in
  settings.json is the next-cheapest win after the Revit descriptions.
- taste-skill body is 87KB — fine while on-invoke, but never invoke it casually.

## Recommendations — EXECUTED same day (commit 2454b7d + settings/memory edits)

1. Rewrite the 4 Revit skill descriptions to trigger + sibling-routing only;
   move quirks/method/conventions fully into bodies. (~700 tokens/session)
2. Disable hookify + cowork-plugin-management plugins if genuinely unused. (~500)
3. Run /consolidate-memory to retire closed "recent project state" entries.
4. Keep building in the current pattern — CLI-first, hooks silent, skill bodies
   fat, descriptions lean. Nothing built since the slimdown needs to be removed.
