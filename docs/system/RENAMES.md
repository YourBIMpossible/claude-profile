# Workspace Rename / Move History

> **Relocated 2026-08-22** — moved from `F:\AI-Dev\system\` to `F:\Claude-Profile\docs\system\` (AI-Dev extraction). `F:\AI-Dev` is now the owner's personal sandbox; project roots are `F:\BIMpossible*`, `F:\Claude-Profile`, `F:\Claude-Tools`, `F:\AI-Dashboard`, `F:\AI-Brain-Data`. Historical `F:\AI-Dev\...` paths below translate via `RENAMES.md`.

When following historical references in older docs, build logs, or archived files, use this map to translate retired paths to their current locations. Add a new entry every time a folder is renamed, moved, or removed at any level under `F:\AI-Dev\`.

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

- **Old:** `F:\AI-Dev\PreseasonPickem-app\`
- **New:** `F:\AI-Dev\Preseason Pick'em\PreseasonPickem-app\`
- **Scope:** Application code folder moved from workspace root into a nested folder under the documentation workspace. Repo internals (`.git\`, `app\`, etc.) are unchanged.
- **Why preserved as a move note:** Build logs in `Preseason Pick'em\01_BuildLog\` (dated before the move) reference the old root path. Translate `F:\AI-Dev\PreseasonPickem-app\` → `F:\AI-Dev\Preseason Pick'em\PreseasonPickem-app\` mentally when following them.

## 2026-05-22 to 2026-05-27 — `_holding\` removed

- **Old:** `F:\AI-Dev\_holding\` (top-level transient staging folder)
- **New:** Removed (folder no longer exists).
- **Scope:** Source documents that lived here were rolled into the formal Phase 3.2 and 3.3 build specs on 2026-05-22. The folder was kept temporarily, then removed.
- **If you need source material:** See `BIMpossible_Workspace\01_BuildLog\archive\2026-05\2026-05-22__phase32-33-specs-locked.md` for the rollup summary, or the live specs at `BIMpossible_Workspace\00_Strategy\BIMpossible_Phase32_BuildSpec.md` and `BIMpossible_Phase33_BuildSpec.md` (the content from `_holding\` is integrated there).

## 2026-05-28 — `AI-Dev Working\` folder created

- **Old:** Multiple tooling folders (`scripts\`, `sandbox\`, `inbox\`, `templates\`, `Settings\`, `_backups\`) lived at `F:\AI-Dev\` root.
- **New:** All moved into `F:\AI-Dev\AI-Dev Working\`.
- **Scope:** Container folder created to separate tooling and transient working files from project folders. See `system\SYSTEM-RULES.md` for the current layout.
- **Note:** `_holding\` was scheduled for removal as part of this reorg and was removed before the move (see entry above).

## 2026-08-22 — AI-Dev extraction (tooling / dashboard / vault / system docs)
- **Old → New:**
  - `F:\AI-Dev\.tools\{ctxdex,ctxcheck,graphify,skillspector,state,local-audit}` → `F:\Claude-Tools\…`; `F:\AI-Dev\.tools\*.exe` → `F:\Claude-Tools\bin\`
  - `F:\AI-Dev\Dashboard`, `F:\AI-Dev\Dashboard-auto` → `F:\AI-Dashboard\Dashboard`, `F:\AI-Dashboard\Dashboard-auto`
  - `F:\AI-Dev\AI-Brain-Data` → `F:\AI-Brain-Data` (private vault — not a BIMpossible assistant corpus)
  - `F:\AI-Dev\system\*.md` → `F:\Claude-Profile\docs\system\`
  - `F:\AI-Dev\{BIMpossible,BIMpossible_Workspace,Add-Ins,…}` → `F:\BIMpossible*` (earlier 2026-08-22 cutover; AI-Dev copies are frozen rollback roots)
- **Why preserved:** every build log, queue entry, and dashboard doc before 2026-08-22 cites the old roots. Source units stay frozen in `F:\AI-Dev` during burn-in; `F:\AI-Dev` itself is the personal sandbox afterwards.
- **Evidence:** `F:\BIMpossible-Workspace\01_BuildLog\migration-2026-08\2026-08-22__AI-Dev-Extraction-Plan.md`
