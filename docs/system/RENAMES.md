# Workspace Rename / Move History

> **Relocated 2026-08-22** — moved from `{repos}\AI-Dev\system\` to `{profile}\docs\system\` (AI-Dev extraction). `{repos}\AI-Dev` is now the owner's personal sandbox; project roots are `{repos}\BIMpossible*`, `{profile}`, `{tools}`, `{repos}\AI-Dashboard`, `{repos}\AI-Brain-Data`. Historical `{repos}\AI-Dev\...` paths below translate via `RENAMES.md`.

When following historical references in older docs, build logs, or archived files, use this map to translate retired paths to their current locations. Add a new entry every time a folder is renamed, moved, or removed at any level under `{repos}\AI-Dev\`.

Entries are newest-first. Each entry should include: the date, the old path, the new path (or "removed"), the scope of the change, and (optionally) where source material can still be found.

---

## 2026-06-16 — MVP-named strategy files renamed (MVP-terminology scrub)

- **Old:** `BIMpossible_Workspace/00_Strategy/BIMpossible_Phase32x_NavigationUX_MVP.md`
- **New:** `BIMpossible_Workspace/00_Strategy/BIMpossible_Phase32x_NavigationUX_v1.md`
- **Old:** `BIMpossible_Workspace/00_Strategy/BIMpossible_Phase33_Step2_MVPLock.md`
- **New:** `BIMpossible_Workspace/00_Strategy/BIMpossible_Phase33_Step2_ScopeLock.md`
- **Scope/why:** Owner directive 2026-06-16 — remove "MVP" framing from the project. Filenames de-MVP'd as part of the workspace-wide scrub; inbound references updated; file contents also scrubbed (MVP→v1 / ScopeLock). Both are superseded historical specs (kept in place, not archived). See memory `feedback_no-mvp-framing.md`.

---

## 2026-05-27 — `PreseasonPickem-app\` moved into `Preseason Pick'em\`

- **Old:** `{repos}\AI-Dev\PreseasonPickem-app\`
- **New:** `{repos}\AI-Dev\Preseason Pick'em\PreseasonPickem-app\`
- **Scope:** Application code folder moved from workspace root into a nested folder under the documentation workspace. Repo internals (`.git\`, `app\`, etc.) are unchanged.
- **Why preserved as a move note:** Build logs in `Preseason Pick'em\01_BuildLog\` (dated before the move) reference the old root path. Translate `{repos}\AI-Dev\PreseasonPickem-app\` → `{repos}\AI-Dev\Preseason Pick'em\PreseasonPickem-app\` mentally when following them.

## 2026-05-22 to 2026-05-27 — `_holding\` removed

- **Old:** `{repos}\AI-Dev\_holding\` (top-level transient staging folder)
- **New:** Removed (folder no longer exists).
- **Scope:** Source documents that lived here were rolled into the formal Phase 3.2 and 3.3 build specs on 2026-05-22. The folder was kept temporarily, then removed.
- **If you need source material:** See `BIMpossible_Workspace\01_BuildLog\archive\2026-05\2026-05-22__phase32-33-specs-locked.md` for the rollup summary, or the live specs at `BIMpossible_Workspace\00_Strategy\BIMpossible_Phase32_BuildSpec.md` and `BIMpossible_Phase33_BuildSpec.md` (the content from `_holding\` is integrated there).

## 2026-05-28 — `AI-Dev Working\` folder created

- **Old:** Multiple tooling folders (`scripts\`, `sandbox\`, `inbox\`, `templates\`, `Settings\`, `_backups\`) lived at `{repos}\AI-Dev\` root.
- **New:** All moved into `{repos}\AI-Dev\AI-Dev Working\`.
- **Scope:** Container folder created to separate tooling and transient working files from project folders. See `system\SYSTEM-RULES.md` for the current layout.
- **Note:** `_holding\` was scheduled for removal as part of this reorg and was removed before the move (see entry above).

## 2026-08-22 — AI-Dev extraction (tooling / dashboard / vault / system docs)
- **Old → New:**
  - `{repos}\AI-Dev\.tools\{ctxdex,ctxcheck,graphify,skillspector,state,local-audit}` → `{tools}\…`; `{repos}\AI-Dev\.tools\*.exe` → `{tools}\bin\`
  - `{repos}\AI-Dev\Dashboard`, `{repos}\AI-Dev\Dashboard-auto` → `{repos}\AI-Dashboard\Dashboard`, `{repos}\AI-Dashboard\Dashboard-auto`
  - `{repos}\AI-Dev\AI-Brain-Data` → `{repos}\AI-Brain-Data` (private vault — not a BIMpossible assistant corpus)
  - `{repos}\AI-Dev\system\*.md` → `{profile}\docs\system\`
  - `{repos}\AI-Dev\{BIMpossible,BIMpossible_Workspace,Add-Ins,…}` → `{repos}\BIMpossible*` (earlier 2026-08-22 cutover; AI-Dev copies are frozen rollback roots)
- **Why preserved:** every build log, queue entry, and dashboard doc before 2026-08-22 cites the old roots. Source units stay frozen in `{repos}\AI-Dev` during burn-in; `{repos}\AI-Dev` itself is the personal sandbox afterwards.
- **Evidence:** `{repos}\BIMpossible-Workspace\01_BuildLog\migration-2026-08\2026-08-22__AI-Dev-Extraction-Plan.md`
