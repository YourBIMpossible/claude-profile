# {repos}\AI-Dev — System Rules (Canonical)

> **Precedence superseded by `{profile}\CLAUDE.md`** (Operating agreement + stop list). Where this file conflicts with it, CLAUDE.md wins.

> **Relocated 2026-08-22** — moved from `{repos}\AI-Dev\system\` to `{profile}\docs\system\` (AI-Dev extraction). `{repos}\AI-Dev` is now the owner's personal sandbox; project roots are `{repos}\BIMpossible*`, `{profile}`, `{tools}`, `{repos}\AI-Dashboard`, `{repos}\AI-Brain-Data`. Historical `{repos}\AI-Dev\...` paths below translate via `RENAMES.md`.

## Operating boundary
Never operate outside the project roots listed above (`{repos}\AI-Dev` is sandbox-only). Never modify files outside the connected workspace folder.

## Subfolder rules
Before working in a subfolder, read its `system/SYSTEM-RULES.md` or `.claude/CLAUDE.md` if present. Subfolder rules ADD to these; if one contradicts a rule here, the more specific instruction (repo CLAUDE.md, then the current task) wins — name the conflict in one line of the report and continue.

## Folder structure (historical — pre-extraction layout, reorganized 2026-05-28)
> Superseded by the 2026-08-22 extraction (see header). This describes the old single-root `{repos}\AI-Dev\` layout, retained for translating older docs via `RENAMES.md`. The live project roots are listed in the header; `{repos}\AI-Dev` is now the owner's personal sandbox only.

Project folders and `system/` lived at the `{repos}\AI-Dev\` root; tooling/transient folders lived in `AI-Dev Working/`.
- Projects (`{repos}\AI-Dev\`): **Add-Ins/** (Revit add-ins, C#, git), **AI-Brain-Data/** (Obsidian vault), **BIMpossible/** (platform code, git), **BIMpossible_Workspace/** (docs/planning, no code), **BIMpossible Site/** (marketing site), **BIMpossible-Prompts/** (prompt exports, flagged for consolidation), **Families by AI/**, **Laundry Gig/**, **Preseason Pick'em/** (contains Next.js `PreseasonPickem-app/`).
- **system/** — top-level rules + personal context (this file, HOW-I-WORK.md, DecisionLog_Format.md, ZIMTools_ClaudeCode_Setup.md, RENAMES.md, `_backups/`). Kept at root so the global CLAUDE.md auto-read of `system/SYSTEM-RULES.md` works.
- **AI-Dev Working/** — *archived 2026-09-06* to `{repos}\zArchive\ai-dev-extraction-20260906\` (untouched since 2026-05-28, no consumer). Formerly held tooling + transient files: `scripts/`, `sandbox/` (wipeable), `inbox/`, `templates/` (incl. `Skills/`), `Settings/` (credentials, not version-controlled), `_backups/`, `.obsidian/`.
- Folder renamed/moved/removed? Record it in `system/RENAMES.md`, and consult it when older docs reference old paths.

## Backup policy (per-folder)
Backup-before-modify applies in **documentation folders** only — `BIMpossible_Workspace/`, `AI-Brain-Data/`: copy original to `_backups/<filename>_<YYYY-MM-DD-HHMMSS>.<ext>` and verify it exists before modifying. Does NOT apply in `Add-Ins/`, `BIMpossible/`, `scripts/`, `sandbox/` (git is version control).

## Deletion
Always require explicit user approval before deleting any file, regardless of permission mode. Where a `99_Archive/` exists, prefer move-to-archive when undecided.

## Archive immutability
Never modify files inside any `99_Archive/` folder (including renames or cosmetic edits). Preserve as-archived.

## Decision logging (format in `system/DecisionLog_Format.md`, read only when logging)
- Cross-cutting / learning: `AI-Brain-Data/decision-log/YYYY-MM-DD.md`
- BIMpossible-specific: `BIMpossible_Workspace/01_BuildLog/YYYY-MM-DD.md`
- Add-Ins app-level: `Add-Ins/decision-log/YYYY-MM-DD.md`
When unsure which applies, use the repo-specific one and say so.

## Communication style
Direct; no filler, affirmations, or restating. Flag things that seem wrong/incomplete and say why briefly. Questions only when the answer changes the design; one at a time. Confirm before destructive operations (the canonical stop list is `{profile}\CLAUDE.md`). No re-explaining known things; no unsolicited rewrites of documents just written.

## Working style
`system/WORKING-STYLE.md` (read at session start). Applies to all work unless overridden per task (e.g., "hotfix only", "MVP spike").

## Session start
Read `system/SYSTEM-RULES.md` and `system/WORKING-STYLE.md` every session; also the subfolder's rules file when working in one. `system/HOW-I-WORK.md` is a personal profile — read when background/preferences are relevant, not every session.
