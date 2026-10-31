# graphify deep dive — 2026-08-07

Scope: how to get more data in, more utility out, and more efficiency from graphify on this
machine. Three evidence tracks: (A) what's actually installed and running here, (B) the library
vs. the skill wrapper, (C) upstream + community + alternatives. Track A is complete and is
written up first because it changes what the other tracks are worth.

---

## A. What is actually deployed on this machine

### A1. There is no `uv` on this machine — the skill's install path is dead code

`SKILL.md` Step 1 (lines 66–100) branches three ways to find a Python interpreter. On this box:

| Branch | What happens here |
|---|---|
| 1. `command -v uv` → `uv tool run graphifyy` | **fails — `uv` is not installed at all.** No `%APPDATA%\uv\tools`, no `%LOCALAPPDATA%\uv\tools`, `uv` not on PATH. |
| 2. shebang from the `graphify` binary | fails — `graphify.exe` is a Windows binary; `head -1` yields non-path bytes and the `case` guard at SKILL.md:79 correctly rejects it. |
| 3. `PYTHON="python3"` | **this is the one that runs.** |

The actual install is **pip**, not uv:

```
~\AppData\Local\Python\pythoncore-3.14-64\Scripts\graphify.exe   (on PATH)
~\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\graphify\
                                                       ...\graphifyy-0.8.35.dist-info
graphify --version → graphify 0.8.35
```

`AI-Brain-Data\graphify-out\.graphify_python` confirms it:
`~\AppData\Local\Python\pythoncore-3.14-64\python.exe`.

**This inverts the risk recorded in the `graphify-cycle-detection-artifacts` memory.** That memory
flags "silent unpinned `uv tool install --upgrade graphifyy`" as an auto-update hazard. That line
(SKILL.md:87) **cannot execute here** — it sits behind `command -v uv`. The fallback that does run
is `python3 -m pip install graphifyy -q` (SKILL.md:91), and that only fires when `import graphify`
*fails*. Since the import succeeds, nothing ever reinstalls.

Net: graphify on this machine is **frozen at 0.8.35 and will never self-upgrade**. The real
exposure is the opposite of what was recorded — staleness, not drift. Upgrading is a deliberate
`pip install -U graphifyy`, and nothing will prompt for it.

### A2. The manifest and the graph live in different directories

`Update-Graph.ps1` (BIMpossible repo root) does `Push-Location $PSScriptRoot` then
`& $graphify update ".\backend"`. The result splits across two folders:

```
{repos}\AI-Dev\BIMpossible\graphify-out\
    manifest.json                 83.9 KB   2026-07-21     <- ONLY this file
{repos}\AI-Dev\BIMpossible\backend\graphify-out\
    graph.json                  10,610 KB   2026-07-21
    GRAPH_REPORT.md                149 KB   2026-07-21
    dashboard.html                  41 KB   2026-07-21
    .graphify_labels.json           13 KB   2026-07-21
    metrics-history.jsonl           35 KB   2026-07-21
    cache\                        1 file
    .graphify_root                   0 KB   -> contains the literal string `backend`
```

The manifest is written **relative to CWD**; the graph is written **relative to the input path**.
Confirmed by content: the root-level `manifest.json`'s keys are all `backend/aec/...`,
`backend/tests/...` — it is the backend scan, parked one directory above the backend's own graph.

Two consequences:

1. **`graphify update` is CWD-sensitive in a way nothing documents.** Run it from anywhere other
   than the BIMpossible repo root and `detect_incremental` diffs against a *missing* manifest, so
   every file reads as new — a full re-extraction presented as an incremental update. Run it from
   the root and it works. `Update-Graph.ps1` gets this right only because of its `Push-Location`.
2. Per `references/update.md:104`, the merge step calls
   `build_merge(..., graph_path='graphify-out/graph.json')` — also CWD-relative. From the repo root
   that path resolves to `BIMpossible\graphify-out\graph.json`, **which does not exist**. The
   CLI `graphify update` evidently uses its own internal pathing (the backend graph is current), but
   the *skill's* `--update` flow, run at the repo root, would merge into nothing.

### A3. `.graphify_root` is written in a format the interpreter cannot read

SKILL.md:99 writes the scan root with `echo "$(cd INPUT_PATH && pwd)"`. In Git Bash on Windows,
`pwd` emits an MSYS path. Observed:

```
AI-Brain-Data\graphify-out\.graphify_root   =  {repos}/AI-Dev/AI-Brain-Data
```

The interpreter recorded alongside it is native Windows Python
(`<python install>\pythoncore-3.14-64\python.exe`), which cannot resolve `{repos}/AI-Dev/AI-Brain-Data`. So
`graphify update` with no arguments — the whole point of saving the root — is broken for any
skill-created output directory on this machine. The CLI-created one is broken differently: it holds
the relative string `backend`, which is only correct from one specific CWD.

Fix is one line: `pwd -W` (or `cygpath -w`) when on MSYS, or write the path from Python rather than
from the shell.

### A4. Three deployments, three different states

| Location | Created by | State |
|---|---|---|
| `AI-Brain-Data\graphify-out\` | the `/graphify` skill | Complete: graph.json 176 KB, GRAPH_REPORT.md, graph.html, labels, 71 cache files (144.5 KB). 2026-06-28. |
| `BIMpossible\backend\graphify-out\` | `graphify update` CLI via `Update-Graph.ps1` | Complete graph (10.6 MB) but **1 cache file** — AST-only runs never populate the semantic cache. 2026-07-21. |
| `BIMpossible_Workspace\graphify-out\` | stray run, 2026-06-09 | **Misplaced artifact.** Contains a manifest whose keys are `aec/…`, `tests/…` — that is BIMpossible's *backend*, indexed into the Workspace repo. No graph. Safe to delete. |

No `cost.json` exists anywhere, so the per-run token-cost tracker SKILL.md Step 9 describes has
never produced a file here. `~\.graphify` does not exist either — so the **global
multi-repo graph feature has never been used**.

### A4b. Coverage gaps — most of the portfolio has never been indexed

A full sweep of `{repos}\AI-Dev` finds exactly four `graphify-out` directories (the three in A4 plus the
backend one). That means:

- **`Add-Ins` — the C# Revit add-in suite, one of the two primary repos — has never been
  graphified.** graphify 0.9.x ships a C# grammar plus `.sln`/`.csproj`/`.xaml`/`.razor` handling
  and WPF/XAML↔ViewModel binding extraction (#1460/#1473). This is the largest single
  "more data in" opportunity available, and it needs the upgrade first: 0.8.35 predates the
  Go/C#-adjacent sourced-stub fixes.
- **BIMpossible's frontend has never been graphified** — `Update-Graph.ps1` scans `.\backend` only.
  The React/TS half of the platform, and every backend↔frontend contract, is absent from the graph.
- No `.graphifyignore` exists anywhere, so no run has ever tuned its own corpus; every scan takes
  the `.gitignore`-derived default.

### A5. `Update-Graph.ps1` is manual-only and nothing calls it

Its own header (lines 48–54) records that both callers were removed: `Push-And-Verify.ps1` dropped
the refresh block 2026-07-06 (`dc03a98`), and `Complete-Wave.ps1` was deleted 2026-07-10. So the
BIMpossible graph is **17 days stale as of today** and will stay stale until run by hand. It also
resolves graphify by PATH with a hardcoded fallback to the pip Scripts dir — which, per A1, is the
same install the skill uses, so at least there is no version split.

---

## B. Library vs. skill wrapper — the skill drives about a third of it

All line references below are against the installed 0.8.35 at
`…\pythoncore-3.14-64\Lib\site-packages\graphify\`.

**SKILL.md documents 14 subcommands; the binary implements 43.**

### B1. The documentation is actively wrong about ignore files

`references/*` state flatly that no ignore mechanism exists — no `.graphifyignore`, no
`--exclude`, no gitignore handling. **All three exist:**

- **`.graphifyignore`** — `detect.py:722` (`_load_graphifyignore`), read at `:749`. Full gitignore
  syntax: comments, `\#` unescape, `!` negation, leading-`/` anchoring, last-match-wins, parent-
  exclusion (`:760–837`). Walks the ancestor chain from the VCS root down.
- **`.gitignore`** — the per-directory fallback when no `.graphifyignore` is present (`detect.py:751`).
  **graphify does respect gitignore.**
- **`.graphifyinclude`** — allowlist that opts hidden files back in (`detect.py:840`).
- **`--exclude PAT`** (repeatable) on `extract` (`__main__.py:3959` → `detect.py:988`). Appended
  last and anchored at scan root, so it **overrides** the ignore files. Missing from CLI help too.

This is the most consequential inaccuracy. The skill's documented mitigation for a large corpus is
"narrow to a subfolder manually" (SKILL.md:134–140); the real mitigation is a `.graphifyignore`.
We have none anywhere (finding A4b), so every scan we have ever run took the raw default.

### B2. Leiden clustering has never actually run on this machine

The `leiden` extra gates `graspologic`, which is **Python <3.13 only**. This machine is
**Python 3.14.4** and `graspologic` is **MISSING** — verified by import probe. It cannot be
installed. So clustering has **silently fallen back to Louvain** on every graph we have built, and
will continue to. The community counts charted on the Mission Control ledger are Louvain numbers.

Not necessarily wrong — just not what the docs imply, and worth knowing before trusting community
structure. (Related: closed issue #48, Louvain fallback hanging on large sparse graphs.)

Same probe: `python-docx` is **missing**, so `.docx` ingestion is silently unavailable; `openpyxl`
is present, so `.xlsx` works. `networkx` and `tree_sitter` are present.

### B3. Subcommands with zero documentation anywhere

| Subcommand | `__main__.py:` | What it does |
|---|---|---|
| **`affected "X"`** | 2730 | **Reverse traversal — blast radius of changing X.** `--relation` (repeatable), `--depth` (default 2), `--graph`. The natural "what breaks if I change this" tool, and nothing drives it. |
| `tree` | 3318 | D3 collapsible-tree HTML (`GRAPH_TREE.html`). |
| `export callflow-html` | 3505 | Mermaid architecture / call-flow HTML. |
| **`provider list\|show\|add\|remove`** | 2507 | **Register custom LLM backends** — any OpenAI-compatible endpoint with base URL, default model, env key, per-token pricing. Not even in `--help`. |
| `prs` | 2624 | PR dashboard/triage (`--triage --worktrees --conflicts --wrong-base`). |
| `global add\|remove\|list\|path` | 3780 | Cross-repo global graph at `~/.graphify/global-graph.json`, `--as TAG`. |
| `diagnose multigraph` | 2971 | Same-endpoint edge-collapse risk report. |
| `check-update <path>` | 3310 | Cron-safe `needs_update` poll. |
| `cache-check`, `merge-chunks`, `merge-semantic` | 4446/4479/4527 | **The skill re-implements all three by hand** (SKILL.md:209, 249–259, 282–329). |
| `install --platform P` | 2266 | 20 platforms; the skill only ever shows the Claude path. |

`graphify update`'s own flags are undocumented too: `--force` (3250), `--no-cluster` (3258),
`GRAPHIFY_FORCE=1` (3255).

### B4. Performance knobs the skill hand-rolls instead of using

SKILL.md hand-rolls parallelism ("chunks of 20–25 files", :228) and references a `parallel_limit`
it never defines (:199). The binary exposes real knobs, none of them documented:

| Flag | Default | Note |
|---|---|---|
| `--max-concurrency N` | **4** | Parallel semantic chunks. Conservative — help says "set 1 for local LLMs", implying headroom upward for cloud. |
| `--token-budget N` | 60000 | Per-chunk token cap. |
| `--max-workers N` | `cpu_count` | AST subprocess count. |
| `--api-timeout S` | 600 | |
| `--resolution F` / `--exclude-hubs F` | 1.0 / none | Clustering tuning. |
| `--dedup-llm` | off | LLM-assisted entity dedup; not in `--help`. |

Other conservative hardcaps: `query --budget` 2000 tokens, **CLI `query` depth hardcoded to 2 with
no flag** (`:2715`), `explain` prints only the top 20 connections (`:2956`), HTML node limit 5000.

**Capability inversion:** MCP `query_graph` accepts `depth` (default **3**, clamped to 6,
`serve.py:691`) while the CLI hardcodes 2. **The MCP path is strictly more capable than the CLI
path the skill uses.**

Ollama and claude-cli calls are **serialized by default** unless `GRAPHIFY_OLLAMA_PARALLEL` /
`GRAPHIFY_CLAUDE_CLI_PARALLEL` are set (`llm.py:1473`, `:1477`) — directly relevant if we route
semantic extraction through local Ollama.

### B5. Ingestion the skill never drives

- **Live PostgreSQL introspection** — `--postgres DSN` (`__main__.py:3963`, `pg_introspect.py:11`).
  Maps tables/views/functions/FKs into the graph. Runs `SERIALIZABLE READ ONLY DEFERRABLE`. Needs
  `graphifyy[postgres]`. **This would put the BIMpossible schema in the same graph as the code.**
- **MCP config files as corpus** — `mcp_ingest.py:69–85`. Auto-detects `.mcp.json`,
  `claude_desktop_config.json` etc. by basename, emits `mcp_server`/`mcp_command`/`env_var` nodes.
  Reads env-var *names* only, never values (`:246`). No flag — it just happens.
- **Office documents** — `.docx`/`.xlsx` converted to Markdown sidecars in `graphify-out/converted/`
  (`detect.py:595`), with structured `xlsx_extract_structure` (`:505`).
- **Google Workspace** — `--google-workspace`, exports `.gdoc/.gsheet/.gslides` via the `gws` CLI.
- **29 tree-sitter grammars** (`extract.py:10950` is authoritative), including `.sln`/`.csproj`,
  PowerShell, Terraform/HCL, Salesforce Apex.

**Genuinely absent:** `.pptx`, `.ipynb`, `.csv`, `.epub`. `scip_ingest.py` exists but is **dead
code** — its own docstring says "Not wired to the CLI in this phase", zero callers. `transcribe.py`
has no in-package caller either; the skill is the only thing that drives Whisper.

### B6. Embeddings are genuinely absent — no hidden capability to unlock

Package-wide grep confirms: no embeddings, no vector store, no FAISS/Chroma/sentence-transformers,
no cosine similarity. Retrieval is **IDF-weighted keyword scoring only** (`serve.py:104–170`). The
one fuzzy subsystem is MinHash/LSH in `dedup.py` (threshold 0.7), used for *entity dedup during
build*, not for query. The skill's own characterization (`query.md:14`) is accurate.

So "better retrieval" is not a graphify configuration problem — it would mean bolting a vector
layer on beside it.

### B7. MCP server is bigger than documented

`exports.md:49` lists 7 tools, stdio only. Reality (`serve.py`): **10 tools, 6 resources, 2
transports.**

- 3 undocumented tools, all GitHub rather than graph: `list_prs`, `get_pr_impact`, `triage_prs`.
  These shell out to `gh`/`git` with a **caller-supplied `repo` argument** — an MCP client can
  point the server at any repo the local `gh` token can read. Read-only, argv list, no shell.
- 6 undocumented resources: `graphify://report`, `://stats`, `://god-nodes`, `://surprises`,
  `://audit`, `://questions`. Note these **bypass the `sanitize_label` hardening** the tool path
  uses (`:974`, `:988`, `:1011`).
- **HTTP transport, entirely undocumented** (`serve.py:1190`): `--transport http --host 127.0.0.1
  --port 8080 --api-key --path /mcp --stateless`. API key via `GRAPHIFY_API_KEY`, enforced by
  `_ApiKeyMiddleware` accepting `X-API-Key` or `Authorization: Bearer`. Binding to `127.0.0.1` fits
  the local-only house rule.
- No prompts (`list_prompts`/`get_prompt` absent).

### B8. Privacy item — query logging is ON by default, and it has already been writing

`querylog.py:33` appends JSONL to `~/.cache/graphify-queries.log` on **every** MCP `query_graph`
call, recording the verbatim question, corpus path, node count, and duration.
`GRAPHIFY_QUERY_LOG_RESPONSES=1` additionally stores the full response body. No rotation, no size
cap, failures swallowed by a bare `except: pass`.

**That file exists on this machine** — verified. Disable with `GRAPHIFY_QUERY_LOG_DISABLE=1`, or
relocate with `GRAPHIFY_QUERY_LOG`. It is local-only (no network), so this is a hygiene item rather
than a leak, but it is on by default and undocumented.

### B9. There *is* a plugin mechanism — for backends, not node types

- **`~/.graphify/providers.json`** (`llm.py:151`, merged into `BACKENDS` at `:229`) registers any
  OpenAI-compatible endpoint. Managed by the undocumented `graphify provider add|list|show|remove`.
- **`./.graphify/providers.json`** — project-local variant, **ignored unless
  `GRAPHIFY_ALLOW_LOCAL_PROVIDERS=1`** (`llm.py:197–208`), because a cloned repo could otherwise
  redirect your corpus *and your API key* to an attacker endpoint. Security-relevant, undocumented,
  and correctly defaulted. Worth knowing before anyone flips that flag.

Node/edge **types** remain a closed enum — no registration point for new node kinds. Complete list
of config files: `providers.json` (user + project), `.graphifyignore`, `.gitignore`,
`.graphifyinclude`. No `graphify.toml`, no `.graphifyrc`.

### B10. Cache invalidation has no CLI

`cache.py` exposes two cache kinds (`ast`, `semantic`) separately addressable via
`cache_dir(root, kind=)`, plus `cached_files()` and **`clear_cache(root)` (`:336`)** — the
invalidation escape hatch, which **no CLI subcommand exposes**. The only documented escape is
`extract --force`.

### B11. Other useful library surface with no driver

`analyze.find_import_cycles(G, max_cycle_length=5, top_n=20)` (`analyze.py:623`) — no docs, no MCP
tool, no CLI flag. **`top_n=20` is the display cap already documented in `Update-Graph.ps1`'s
comments as the reason cycle counts charted as a flat line.** Also: `export.to_canvas` (Obsidian
Canvas), `export.prune_dangling_edges`, `backup_if_protected` (graph.json *is* backed up before
overwrite — the skill never says so; disabled by `GRAPHIFY_NO_BACKUP`), `diagnostics.diagnose_file`,
`symbol_resolution` (cross-file call resolution, bash `source` edges),
`cluster.remap_communities_to_previous` (**stable community IDs across rebuilds** — directly useful
for the metrics ledger), `llm.estimate_cost`.

### B12. Known library bug

`tree --top-k-edges` help text claims default 12; the actual default is **0**
(`__main__.py:3330`, confirmed `tree_html.py:570`).

## C. Upstream

Canonical repo: **`https://github.com/Graphify-Labs/graphify`** (`safishamsi/graphify` redirects to
it). PyPI dist `graphifyy`. Actively maintained — 1,350 commits in the last 52 weeks, commits every
day from 2026-07-23 through today, ten issues closed on 2026-08-07 alone.

> **Reading trap:** the default branch is **`v8`**, not `main`. `main`'s `CHANGELOG.md` stops at
> 0.1.8 (April 2026). Anything read off `main` is four months stale.

### C1. We are 52 releases behind, and the gap contains our exact bugs

0.8.35 shipped **2026-06-07**. Latest is **0.9.36**, shipped **2026-08-07** (today). Fifty-two
tagged releases in between.

**The phantom-import-cycle defect recorded in the `graphify-cycle-detection-artifacts` memory is a
known upstream bug, upstream named it, and upstream fixed it — entirely inside the window we
missed.** Verified in source at `graphify/extractors/engine.py` @ `v8`, `ensure_named_node`
(~lines 2489–2511): unresolved cross-file symbols now emit a **sourceless** stub with
`source_file: ""`, and the referencing file's path moved to an internal `origin_file` key that is
stripped before serialization. The code comment calls the old behaviour "the phantom-duplicate-node
bug (#1402)" and explains that a *sourced* stub is what baked the referencing file's path into the
node id — which is precisely the 140 mis-attributed external symbols that manufactured 89 fake
cycles on the BIMpossible backend.

| Version | Date | Issue | Fix |
|---|---|---|---|
| **0.8.36** | 06-08 | #1196 | `find_import_cycles()` called `nx.simple_cycles()` with no `length_bound` → report generation hung at 100% CPU on large graphs. **Filed against 0.8.35 specifically.** Fixed one day after our version was cut. |
| **0.8.46** | 06-23 | #1402 | Cross-file type-annotation references stop minting N+1 nodes with the referencing file path baked into the id. |
| **0.8.51** | 06-28 | #1462 | Same-label imported-type stubs stay distinct when there is no project definition to rewire onto — the release note's own example is two files that both `from pathlib import Path`, i.e. exactly the stdlib case. |
| **0.8.51** | 06-28 | #1500 | Go extractor migrated off the sourced-stub fallback. |
| **0.9.0** | 06-28 | #1504/#1509 | Full-path node IDs — removes the ID-collision substrate. **Breaking.** |
| **0.9.1** | 06-28 | #1515 | Extends #1462 to Julia, Fortran, Go, Rust, PowerShell, ObjC. |

Tracking issue **#1186** (filed against 0.8.35, "ghost-duplicate nodes for cross-file type
references") is deliberately left open for remaining per-language cases; the maintainer confirmed
on 2026-06-27/28 that 0.9.0 addresses most of it.

**Residual, by design:** module-level import handlers still materialize a `type=module` node
carrying the *importing* file's path (`engine.py` ~2531–2543, citing #1327) — without that anchor
`build_from_json` prunes the import edge as dangling. So our pattern survives for module nodes
specifically, consciously chosen. `graph_signals.py`'s filter stays useful for that narrow class.

### C2. The upgrade is not free — plan for the 0.9.0 break

- **Node IDs changed** from parent-dir+filename to full repo-relative path. Graphs auto-migrate on
  next build with no LLM re-bill, **but nodes that had already collided are only recovered by
  `graphify extract --force`.** Neo4j stores need re-import, GraphML layouts go stale, and no
  persisted node ID survives.
- **Pin explicitly to 0.9.36.** 0.9.35 was tagged but its PyPI publish was cancelled (#2514), so a
  bare resolve can silently land on 0.9.34.

### C3. Do not trust `--update` for correctness — this is the largest open defect family

Six-plus open issues show incremental runs silently losing edges while reporting success:

| # | Impact |
|---|---|
| #2276 | **silently drops 56% of edges while reporting success** |
| #2406 / #2230 | cross-file edges to *unchanged* neighbours lost (batch-scoped resolution) |
| #1711 | −12% doc↔code edges per update cycle |
| #2033 / #2459 | `kind="semantic"` default makes incremental re-extract the whole corpus semantically |
| #2259 / #1151 | absolute-vs-relative `source_file` → `--update` **duplicates the entire graph. Windows-specific.** |

That last pair is ours: it is the same CWD/path-shape family as finding A2 above. Combined with the
manifest/graph directory split, our `--update` path has two independent ways to be wrong.

Corollary: `Update-Graph.ps1` running `graphify update` is exactly the mode with the worst
correctness record. Full `--force` rebuilds are the honest option when the graph must be right.

### C4. Capability surface we are not using at all

Everything below exists in 0.9.x and never appears in our workflow:

**Ingestion.** 36 tree-sitter grammars — including **C# plus `.sln`/`.csproj`/`.xaml`/`.razor`**,
which covers the Add-Ins repo. Also PostgreSQL live introspection
(`extract --postgres "postgresql://…"`), Rust workspaces (`--cargo`), Terraform/HCL, MCP configs
(`.mcp.json`, `claude_desktop_config.json`), package manifests, Office `.docx`/`.xlsx`, PDFs,
images, video/audio + YouTube URLs.

**Query.** `affected <symbol>` (blast radius), `path A B`, `explain`, `cluster-only`, `label`.

**MCP server.** `graphifyy[mcp]` exposes `query_graph`, `get_node`, `get_neighbors`,
`shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs` over stdio **or http**
(`--host/--port/--api-key/--stateless`). One server holds several project graphs, LRU-bounded by
`GRAPHIFY_MAX_CONTEXTS` (default 8).

**Git integration.** `graphify hook install` — post-commit/post-checkout AST-only rebuild at **zero
API cost**, which is the automation `Update-Graph.ps1` lost when its callers were deleted.
⚠️ **Superseded — see E5. Do not install this hook.** Upstream #791 (closed without a fix) shows it
spawns unbounded concurrent rebuilds with no lock. Also a **git merge driver for `graph.json`**
(union-merge, no conflict markers), which is unaffected and still useful.

**Multi-repo.** Three mechanisms: `merge-graphs a.json b.json --out merged.json`; a global graph
(`graphify global add graphify-out/graph.json --as myrepo` → `~/.graphify/global-graph.json`, or
`extract ./repo --global --as myrepo`); or one MCP server holding several graphs. A BIMpossible +
Add-Ins + Workspace unified graph is directly supported.
*Caveat:* #2081 (open) — `built_at_commit` is read from CWD, not the extraction target, so a
multi-repo driver script stamps graphs with an unrelated repo's commit.

**Backends.** Nine LLM backends including **Ollama** (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`,
`GRAPHIFY_OLLAMA_NUM_CTX`, `GRAPHIFY_OLLAMA_KEEP_ALIVE`). Semantic extraction can run fully local —
which matters given the "no code leaves this machine" constraint.

**Exports.** FalkorDB push, Mermaid callflow (`export callflow-html`), SVG (`[svg]` extra),
GraphML, Neo4j cypher/bolt push, Obsidian, markdown wiki.

**Tuning env vars.** `GRAPHIFY_MAX_WORKERS`, `GRAPHIFY_MAX_GRAPH_BYTES` (512 MiB default cap on
`graph.json`), `GRAPHIFY_MAX_OUTPUT_TOKENS`, `GRAPHIFY_API_TIMEOUT`, `GRAPHIFY_MAX_RETRIES`,
`GRAPHIFY_MAX_CONTEXTS`, `GRAPHIFY_LLM_TEMPERATURE`, query logging. Flags: `--timing` (per-stage
wall-clock), `--token-budget`, `--max-concurrency`, `--resolution`, `--exclude-hubs`, `--code-only`,
`--allow-partial`, `--force`.

**Config.** `.graphifyignore` (gitignore syntax, merged with `.gitignore`, its patterns win
including `!` negation). No YAML/TOML config file exists — that request (#792) was closed.

### C5. Known scale ceilings

- `graph.json` capped at 512 MiB (`GRAPHIFY_MAX_GRAPH_BYTES`). Our largest is 10.6 MB — no concern.
- HTML viz aggregates past 5,000 nodes; WebGL renderer requested (#2361, open).
- `detect()` is CPU-bound on ignore-pattern matching: 35+ min on a 16k-file monorepo (#1958, open);
  ~100s of a 131s incremental run spent in ignore matching for a **2-file change** (#2226, open).
  Both open. Our repos are far smaller, but this is why incremental feels slow.
- Flat semantic-cache directory hits ~20k entries at monorepo scale (#1709, open).
- 462 open PRs against 391 open issues — issues close fast, PRs pile up. Don't count on an
  upstream PR landing quickly.

## D. Getting Add-Ins (C#) into the graph honestly

graphify does support `.cs`, but its C# extraction is the **syntactic tier** — tree-sitter, no type
resolution. That is good enough for a file-relevance map and structural breadth; it is *not* a call
graph. Edges are tagged `EXTRACTED` / `INFERRED` / `AMBIGUOUS`, which is an honest confidence
signal and the right thing to filter on.

### D1. Why syntactic C# can't produce a real call graph

tree-sitter gives a CST with zero type information. For C# that specifically loses: overload
resolution (`Foo(int)` vs `Foo(string)` are indistinguishable), the receiver type on `x.Bar()` so
you cannot tell *which* `Bar`, extension methods, `var` and target-typed `new()`, generic
instantiation, `using` aliases, partial classes, and anything from a referenced assembly with no
source. What you get is "this file mentions an identifier defined in that file."

The ecosystem agrees: nobody has built a serious C# symbol index on tree-sitter. `github/stack-graphs`
is **archived** and only ever shipped Java/JS/Python/TS. `github/semantic` is **archived** and never
supported C#. Sourcegraph's syntactic tier (`scip-syntax`) has no C#; their C# story is Roslyn.

### D2. The Revit-specific trap — and it is the same one we already know

**Revit instantiates `IExternalCommand`/`IExternalApplication` implementations by reflection from
the `.addin` manifest.** Reflection is invisible to every static analyzer, IL-level included. So a
naive call graph reports **every BIMpossible ribbon command as an unreachable root — i.e. as dead
code**. Roots must be seeded by scanning for types implementing those interfaces *and* parsing the
`.addin` XML.

This is the same blind spot recorded in the `addins-zero-callers-proof` memory (`.addin` manifests
as one of the six checks that a grep misses). A graph would not have rescued that analysis; it would
have confidently agreed with the wrong answer. Any graph-driven dead-code claim on Add-Ins needs
the same six-check discipline layered on top.

### D3. If we want accurate C# edges — ranked

| Rank | Approach | Effort | Accuracy |
|---|---|---|---|
| 1 | **Mono.Cecil walker over compiled DLLs** | 1–3 days | High for static edges; needs 4 post-passes |
| 2 | **`scip-dotnet` (Roslyn) → `scip print --json`** | 2–4 hrs | Highest symbol accuracy; call edges must be derived |
| 3 | **CodeGraphContext** (wraps scip-dotnet, local Kuzu) | 1–2 hrs | Same as #2, packaged; you inherit their schema |
| 4 | tree-sitter hand-rolled (what graphify does) | — | name matching only |

**`scip-dotnet`** (`sourcegraph/scip-dotnet`, Apache-2.0, last push 2026-08-01) is the cheapest real
win: `dotnet tool install --global scip-dotnet`, then `scip-dotnet index` at the repo root →
`index.scip`, then `scip print --json`. **Fully offline** — `SRC_ACCESS_TOKEN` is only for the
upload step we'd never run. Caveat: it needs a `.csproj`/`.sln` that restores cleanly, and SCIP is a
*definition/reference occurrence* format — turning it into `caller → callee` means range-joining
each reference against its enclosing definition (~50 lines, mechanical, and it conflates "calls"
with "mentions").

**Mono.Cecil** (MIT, 2,989★, NuGet 0.11.6) reads net472 and modern .NET alike, needs no MSBuild and
no restore, and with `ReadSymbols = true` plus portable PDBs gives file+line via
`Instruction.SequencePoint`. It is low-activity but alive — fine for a read-only metadata walker
over a frozen format (ECMA-335). If the staleness matters, **AsmResolver** (MIT, last push
2026-07-30) is the actively-maintained equivalent.

What a Cecil pass misses, and must be post-processed: **virtual/interface dispatch** (the `callvirt`
operand is the *statically declared* method, never the runtime override — needs class-hierarchy
analysis emitting `MAY_CALL` fan-out, and this is the single biggest gap); **async/iterator state
machines** (`async Task M()` compiles to a stub calling `AsyncTaskMethodBuilder.Start<<M>d__3>`
with the real body in `<M>d__3.MoveNext()` — re-parent via the `[AsyncStateMachine]` attribute);
**generics** (operands bake in the instantiation, so canonicalize via `.GetElementMethod()`);
**properties** (appear as `get_X`/`set_X`). Also invisible: reflection, DI registrations, event
wiring, `dynamic`, P/Invoke, COM interop, and WPF/XAML `IComponentConnector.Connect`.

**Existing off-the-shelf "graph from assemblies" tools are all dead ends.** `clovett/CodeGraph` is
the closest (Mono.Cecil-based, genuine method-level edges via `-m`) but is a 2★, five-year-old
personal tool that emits DGML only — read it as a reference implementation, don't depend on it.
AsmSpy and the `dotnet-*-graph` family are assembly/project-level only. `il2graph` does not exist.
ILLink `--dump-dependencies` emits the *trimmer's reachability* graph (XML only, no DGML despite the
folklore) and requires a trimmable publish, which net472 Revit add-ins cannot do at all.

### D4. Recommended shape if we pursue it

Two passes merged, **with provenance on every edge** — tag each `source: cecil-il | scip-roslyn |
astgrep-heuristic` plus a confidence. Cecil over `bin\` DLLs as the primary (no MSBuild, works
identically on net472 and net8), `scip-dotnet` to correct the virtual/interface fan-out with real
Roslyn resolution wherever a solution restores, and optionally `ast-grep --json` as a targeted
patcher for DI registrations and reflection-by-string. Provenance tagging is the piece every
off-the-shelf tool quietly skips, and it is what makes a merged graph honest.

**Verdict for now:** run graphify on Add-Ins for breadth (it is nearly free), treat its C# call
edges as `AMBIGUOUS`-tier, and only invest in Cecil/scip if we actually want to ask reachability or
blast-radius questions about the add-in suite.

## E. Community, alternatives, and what the research actually says

### E0. Two measurements that reframe the whole question

**The BIMpossible graph, measured directly:** 10,082 nodes / 20,208 edges / 10.9 MB. Edge mix:
`calls` 6963, `contains` 6919, `rationale_for` 1781, `imports` 1358, `uses` 1166, `method` 695,
`imports_from` 607, `references` 597, `inherits` 118. Source files: 7,406 `.py`, 2,624 `.json`,
41 `.md`, 2 `.sh`.

**Every node carries `_origin: ast`. 100% of them.** Not one LLM-derived code node in the whole
graph. The semantic pass — the expensive half of the pipeline, the part the skill spends most of
its bash on — contributed no nodes to the graph we actually use. That is the single most
surprising measurement in this document, and it should be reproduced before any effort goes into
tuning semantic extraction.

**Add-Ins: 3,565 `.cs` files, 341,467 LOC, 126 `.csproj`, and no `.sln` at all.** The missing
solution file matters — every Roslyn and SCIP path in section D assumes one exists.

### E1. The literature does not support adopting a GraphRAG framework

This is the finding that cancels the most work, so it gets stated first.

- **arXiv 2601.08773** (Jan 2026), *AST-Derived Graphs vs LLM-Extracted Knowledge Graphs*:
  deterministic tree-sitter graphs index **in seconds** where LLM extraction takes hours, score
  **highest on correctness**, and LLM extraction **silently skipped 377 files** on one repo.
  Caveats: single author, preprint, 15 queries/repo, Java-only, and the author built the winning
  system. Directional, not proof — but it points at the architecture graphify already has.
- **arXiv 2506.06331** (Wuhan/Peking, 2025), unbiased GraphRAG evaluation: after correcting for
  position bias (**swapping answer order changes LLM-judge win rates by >30%**), LightRAG's
  reported 66.70% win rate over naive RAG **collapses to 39.06% — naive RAG wins**. Best method
  gains under 10%, at **77× the tokens**.
- **arXiv 2502.11371** (Michigan State / Meta / IBM, v3 Mar 2026): no single winner. Plain vector
  RAG wins single-hop (64.78 vs 61.03 F1); graph wins multi-hop by ~3 points; summarization is a
  wash. KG construction achieves only **~65% entity coverage**, and costs **~50× the indexing
  time**.
- **WildGraphBench** (arXiv 2602.02053, Feb 2026), messy real-world corpora: graph methods **lose
  at summarization**, the exact task GraphRAG was marketed for, because extraction and
  neighborhood summarization both degrade on noisy input.
- Microsoft's own LazyGraphRAG post says it matches global-search quality at **0.1% of indexing
  cost and >700× lower query cost** — read in reverse, the vendor is stating that global search
  costs roughly 1000× more than necessary. And **LazyGraphRAG does not exist as code**: zero
  matching paths in the 1,126-file `main` tree, no CHANGELOG entry through 3.1.1, last maintainer
  comment 2024-12-09.

**Synthesis across three independent groups:** graph retrieval helps *only* on genuine multi-hop
aggregation, by 2–12 points; it loses on single-hop lookup and on noisy-corpus summarization; and
a large share of published wins do not survive debiased evaluation.

**Consequence for us: do not build community summarization, and do not adopt an LLM-extraction
pipeline.** Our corpus is code, our queries are localization queries, and we already get the
structure deterministically.

### E2. Retrieval upgrades, cheapest first

These are the "more utility" answers, and the order matters — each is independently useful and
none requires the next.

**1. Replace BFS with Personalized PageRank.** `networkx.pagerank(G, personalization=…)` where the
matched nodes are the personalization vector. Roughly ten lines in `Dashboard\graph_signals.py`,
no new dependency, no model, no index. BFS treats every hop-2 node as interchangeable; PPR makes
scores distance-decaying and degree-aware, so a hub reached from several seeds outranks a random
neighbour. Supported by both HippoRAG papers.

**2. Add a local cross-encoder reranker — before any vector store.** This is the most reliably
positive intervention in the entire research sweep, worth **5–15 nDCG points**, and it needs no
index and no embedding pipeline. The decisive number:

| Reranker | Params | MTEB-Code | License |
|---|---|---|---|
| **Qwen3-Reranker-4B** | 4B | **81.20** | Apache-2.0 |
| Qwen3-Reranker-0.6B | 0.6B | **73.42** | Apache-2.0 |
| bge-reranker-v2-m3 | 0.6B | **41.38** | Apache-2.0 |

**The widely-repeated "BGE-M3 + bge-reranker-v2 is the production default" advice is actively
wrong for code — a 32-point gap on exactly our domain.** Qwen3-Reranker-4B fits comfortably in
16 GB. **Ollama has no rerank endpoint** (PR #7219 / issue #4510 still open), so this runs through
sentence-transformers `CrossEncoder` directly.

**3. Only then, embeddings — as a *second* seed source, not a replacement.** **arXiv 2605.16352
(LARGER, May 2026)** is the most directly applicable paper found: it seeds from **lexical matches,
not embeddings**, aligns to graph anchors, and expands with confidence filtering — **+13.9
file-level Acc@5**, and +11.8 with fixed rather than tuned hyperparameters (that near-parity means
it transfers). **For code identifiers, exact-token matching beats dense embeddings as a seed
source.** Fuse the two seed lists with Reciprocal Rank Fusion, k=60. (RRF's k=60 is purely
empirical — Cormack/Clarke/Büttcher SIGIR 2009; anything in 40–80 performs the same. A learned
convex combination beats it, but that needs labels we don't have.)

### E3. If we do add embeddings — the specifics that silently break

**Model.** `qwen3-embedding:4b` (2.5 GB q4, Apache-2.0, 2560 dims MRL-truncatable, 40 K context,
one `ollama pull`) tops CORE-Bench L1 at **72.7** of everything tested. `qwen3-embedding:0.6b`
(639 MB) scores **66.9** — beating a 1.5 B code specialist and a 7 B general model — and is the
right starting point.

**`nomic-embed-text` is already pulled here and is the wrong model for code.** Nomic has never
published a code score for it despite 81.5 M Ollama pulls; its architectural sibling
`modernbert-embed-base` scores 48.8 CoIR against gte-modernbert-base's 71.5; Nomic shipped two
separate code models, which you don't do if the general one handles code; and Ollama caps it at
2 K context. Evidence is suggestive rather than conclusive — but there is *zero* evidence in its
favour for code, and the alternative is one `ollama pull`.

**Three traps that degrade retrieval with no error message:**

- **Prefixes are model-specific and Ollama does not apply them for you.** `nomic-embed-text` needs
  `search_query: ` / `search_document: `. Qwen3 needs `Instruct: {task}\nQuery: {q}` on the
  **query only** — documents get **no prefix** — and the instruction must be byte-identical at
  index and query time or the space silently misaligns.
- **Use `/api/embed`, never `/api/embeddings`.** The legacy endpoint applies **no L2
  normalization**, which is a real correctness bug if you assume unit vectors for cosine.
- **`truncate` defaults to `true` and the response contains no indication that truncation
  happened** (Ollama issue #14259). You will embed half a function and never know. Pass
  `truncate: false` when indexing so it throws, and set `num_ctx` explicitly on every call
  (it defaults to 2048).

**Chunking — this reverses the prevailing advice.** arXiv 2605.04763 (May 2026), 864 controlled
configurations: **AST-aware chunking does not beat a plain sliding window** (cAST statistically
indistinguishable), **function-level chunking is the thing to avoid** (Cliff's δ = −1.0, maximal
effect — it strands functions from their imports and class context), and **cross-file context
length is the dominant lever** (2048→8192 buys up to 4.2 pp). Recipe: sliding window ~2000
non-whitespace chars, negligible overlap, with a prepended header carrying file path, language,
enclosing class/namespace and imports.

**Store: LanceDB.** Apache-2.0, actively released (0.36.0, 2026-07-29), `pip` with a Windows
wheel, no server, and it ships ANN + BM25 + hybrid fusion + reranker hooks in one package — every
other option makes you assemble at least one piece yourself. Sizing: 200 k chunks × 768 × f32 ≈
600 MB.

⚠️ **`sqlite-vec` has a new problem:** SQLite's own core team is building **Vec1**
(sqlite.org/vec1, announced 2026-03-30 by Dan Kennedy) with real ANN via IVFADC+OPQ. sqlite-vec
has been pre-v1 for two years. Don't couple deeply to its SQL surface. `sqlite-vss` is confirmed
abandoned by the same author. DuckDB's `vss` still self-describes as a proof of concept with **no
WAL recovery — corruption on unclean shutdown**.

### E4. Tools worth knowing about, and the ones that are traps

**Genuinely useful complements:**

- **`grepai`** ([yoanbernabeu/grepai](https://github.com/yoanbernabeu/grepai)) — MIT, single Go
  binary, local Ollama embeddings by default, file-watcher daemon, MCP server, call-graph tracing.
  Benchmarked at 97% Claude Code input-token reduction. **Closes graphify's stated blind spot in
  about an hour with zero integration work.** Code only leaves the machine if you explicitly
  configure the OpenAI provider.
- **`txtai`** ([neuml/txtai](https://github.com/neuml/txtai)) — Apache-2.0, v9.12.0 (2026-07-30),
  only 11 open issues, six years of continuous maintenance. It does **not** do LLM entity
  extraction; its graph is NetworkX-backed with edges from similarity plus whatever you supply.
  For everyone else that's a limitation; for us it's the feature — **it's the only project that
  accepts our existing graph as-is instead of demanding an LLM rebuild it.**
- **LlamaIndex `PropertyGraphIndex`** — MIT, very active. Don't adopt the framework; note that
  **`ImplicitPathExtractor` makes no LLM calls and no embedding calls** and would ingest our JSON
  for zero tokens, and steal the `VectorContextRetriever` pattern (~50 lines over our own graph).
- **`scip-python`** — a pyright fork with **real type inference**, genuinely more accurate than
  tree-sitter for the FastAPI backend. Marginal though: our 6,963 tree-sitter `calls` edges are
  already decent.
- **VizTracer under pytest** — `sys.monitoring` on 3.12+, low overhead, gives *observed* call
  edges that resolve DI, dynamic dispatch, and FastAPI's framework-driven entry points that no
  static tool can see. Merge as `_origin: runtime`. **Highest signal-per-hour idea in this
  document for the Python side**, and the same trick would fix the Revit reflection blind spot in
  section D if applied to a running Revit session.

**Licence and egress disqualifications** — these matter because of the stated goal of eventually
packaging automations as a service:

| Tool | Why it's out |
|---|---|
| **CodeQL** | T&Cs prohibit use on any non-open-source codebase without paid GitHub Advanced Security. Running it on Add-Ins would be a licence violation. |
| **GitNexus** | PolyForm Noncommercial — commercial use needs a paid Akon Labs licence. |
| **Sourcebot** | FSL-1.1 bans "Competing Use"; `ee/` is separately licensed. Not open source. |
| **FalkorDB** | SSPLv1. Local use is fine today; productising is not. |
| **jina-code-embeddings, SFR-Embedding-Code, jina-embeddings-v4** | CC-BY-NC / Qwen Research — non-commercial. |
| **claude-context** | Defaults to Milvus/Zilliz **Cloud**. |
| **voyage-code, OpenAI/Cohere/Gemini embeddings** | API-only — code egress. |
| **Sourcegraph Cody, Greptile, DeepWiki, Augment remote** | Hosted, full-codebase upload. |
| **cognee** | **Silently falls back to OpenAI if you configure only the LLM or only embeddings.** Telemetry on by default. Its `codify` code pipeline was moved out of core and the MCP server dropped the tool entirely. |

**Dead or dying, do not adopt:**

- **Kùzu is dead** — repo archived 2025-10-10, company acqui-hired by Apple. It is still the
  default or documented backend in cognee, CodeGraphContext's **Windows** path, and LlamaIndex's
  Kuzu store. **Any recommendation naming Kuzu is stale.** The credible successor is
  **LadybugDB** (MIT, active, Cypher + columnar + built-in full-text *and* vector indices) — if we
  ever want a real graph DB, that's the one.
- **Microsoft GraphRAG** — active but self-described as "a demonstration and not an officially
  supported Microsoft offering". Ollama is acknowledged, not supported; the docs themselves warn
  about malformed JSON, and issue #1186 (Ollama fails) was closed **as not planned**. v3 replaced
  fnllm with LiteLLM, which invalidates **every pre-v3 Ollama fork and blog post**. Its
  person/org/geo/event ontology is wrong for code anyway.
- **nano-graphrag** (no release since 2024-10), **fast-graphrag** (PyPI 0.0.5, pinned `<3.13`),
  **Axon** (no commits since Mar 2026) — all stalled. fast-graphrag's "27× faster, 40% more
  accurate" is self-reported by a company selling the managed version, with no published accuracy
  comparison.
- **Joern** — C# frontend exists in the free OSS tier, but its AST dependency `DotNetAstGen` was
  **archived 2026-04-23**, and it's AST-level so cross-assembly calls stay unresolved. Overkill;
  revisit only if we ever want taint analysis.
- **repomix / code2prompt / gitingest** — flat-blob packers, orthogonal to a graph. Ignore the
  category.
- **pyan3 / code2flow / pydeps** — all worse than the 6,963 `calls` edges we already have.
  code2flow's own README calls its output "a pretty good estimate" and it has no JSON export.

⚠️ **Star counts on this whole category are unreliable.** CMU's ICSE 2026 paper documents ~6 M
suspected fake stars across 18,617 repos. graphify shows 104 k stars at four months old;
`colbymchenry/codegraph` 65 k at seven months. Judge these on the README and the binary, not the
badge. An independent 2026 survey separately flags that both 40 k-star "leaders" in this space are
roughly single-maintainer projects.

### E5. Correction to C4 — do not install graphify's git hook

Section C4 listed post-commit hooks as a zero-cost freshness win. **That is wrong, and upstream
knows it.**

[Issue #791](https://github.com/Graphify-Labs/graphify/issues/791): the post-commit hook spawns
**unbounded detached rebuilds with no concurrency lock**, and **ignores the `GRAPHIFY_CHANGED`
data it computes**, forcing a full re-glob instead of an incremental update. The reporter observed
~10 concurrent rebuilds at 4.1 GB each on a 32 GB machine, with free RAM dropping to 60 MB.
**Closed without a fix.** Related: #1018 (the hook doesn't skip `graphify-out`-only commits, so it
triggers on its own output) and #653 (update drops code-file semantic nodes).

**Do this instead:** drive `graphify update` from the existing `Dashboard\Refresh-Dashboard.ps1`
on a schedule, or install the hook on exactly one repo behind our own lockfile. Never blanket-install.

### E6. Storage — we don't need a graph database

At 10 k nodes / 20 k edges, Neo4j buys almost nothing. The Neo4j+GDS crossover against in-process
NetworkX starts somewhere around 50–100 k nodes, and NetworkX is comfortable well past that. Even
a full portfolio graph — backend plus 341 k lines of Add-Ins plus the smaller tools — lands in the
40–80 k node range, still in-process territory.

What Neo4j *would* buy: Cypher as a stable query language for the Dashboard, persistence without
reloading 11 MB of JSON per query, and indexed multi-hop traversal. **The decisive point is that
this is a ten-minute test, not a project** — `graphify export neo4j --push bolt://localhost:7687`
already exists and uses `MERGE`, so it's idempotent and safe to re-run.

**Recommendation: keep `graph.json` as the source of truth.** Add a graph DB only if we catch
ourselves hand-writing traversals in `graph_signals.py` that Cypher would express in one line —
and if that happens, reach for **LadybugDB** (MIT, embedded, no server, vectors built in) rather
than Neo4j.

---

## F. What other teams actually do with it

The discussion for this tool is not on Reddit, Hacker News, or in blogs — those were swept and are
essentially empty (HN submissions max out at 10 points and 0–1 comments; Reddit returned zero
graphify content through every accessible route, corroborated independently by two agents). **The
real corpus is the GitHub issue tracker and the CI workflows and `CLAUDE.md` files other teams have
committed to public repos.** That turns out to be a much better source anyway.

### F1. The consensus production pattern — four orgs converged on it independently

Azure ([LogicAppsUX](https://github.com/Azure/LogicAppsUX/blob/main/.github/workflows/update-knowledge-graphs.yml)),
openzigs, Cap-go, and Praetorian all arrived at the same architecture without coordinating:

1. **AST-only.** `--code-only` / `--no-cluster` — pure tree-sitter, no LLM, no API key,
   deterministic, **~30 seconds for a whole workspace**. Azure's workflow header says it outright:
   *"no LLM, no API keys, ~30s for all libs."* Every production workflow found uses this path.
2. **One graph per library, not one graph per monorepo.** Azure loops over eight packages
   separately. This is simultaneously the performance fix and the clustering-quality fix.
3. **Sanitize paths before committing.** graphify bakes absolute local paths into `graph.json` and
   `GRAPH_REPORT.md`; Azure strips them in CI.
4. **`PYTHONHASHSEED=0`.** See F3 — without it, rebuilds produce noisy diffs.
5. **Pin the version.** Praetorian pins `graphifyy==0.8.35` — coincidentally exactly our version.

Official guidance (discussions #369/#426) is to treat the graph as **a build artifact, not source**
— "like a compiled binary: one person (or CI) regenerates it, everyone else consumes it." Commit
`graph.json` / `graph.html` / `GRAPH_REPORT.md`; gitignore `cache/`, `manifest.json`, `cost.json`.
And since `.graphifyignore` uses gitignore syntax, `cp .gitignore .graphifyignore` is the
recommended starting point (which also independently confirms B1).

### F2. The routing rule — "grep answers backward, the graph answers forward"

This is the single most useful thing the community has produced about this tool, and it comes from
[issue #2216](https://github.com/Graphify-Labs/graphify/issues/2216), benchmarked on 6,130 files /
122,114 nodes:

| Question | ripgrep | graphify |
|---|---|---|
| "who calls `record_terminal_result`?" | **0.013 s, 3 exact hits** | **4.9 s, 102 nodes, truncated by token budget** |
| "what does `terminal_tool()` call?" | can't answer | 454 nodes across 8+ files |

**~377× slower and a worse answer** on the first; the only tool that works on the second. The
complaint is that the shipped `SKILL.md` `description:` routes *every* codebase question through
the graph.

The best implementation of the correct split is
[citizenweb3/validatorinfo's `CLAUDE.md`](https://github.com/citizenweb3/validatorinfo/blob/main/CLAUDE.md),
which keeps exact-string lookups on grep and reserves the graph for impact-before-change via
`graphify affected`. That file is worth copying almost verbatim.

Corroborating from the other direction: [#985](https://github.com/Graphify-Labs/graphify/issues/985)
— a user evangelizing the tool inside his own org can't justify it, because each `query` costs
~2K tokens and 3–4 invocations run 8K+ for a workflow grep does cheaper. **Open since 2026-05-22,
one comment, no maintainer reply.**

**Reconciling this with the marketing:** the README's headline **71.5× token reduction** is measured
on a **52-file** corpus, baselined against *pasting the entire corpus into context* — not against
grep. The same README reports **~1×** on a 6-file repo. Both #985 and #2216 can be true at once.

### F3. `PYTHONHASHSEED=0` — and an independent confirmation of B2

[Issue #1667](https://github.com/Graphify-Labs/graphify/issues/1667): two runs on an **unchanged**
corpus differ on 196 of 11,819 community assignments. Root-caused in-thread to **the Louvain
fallback**, because seeded Leiden needs `graspologic`, which is gated to **Python <3.13**, so most
installs silently never get the deterministic path.

**That is finding B2, reached independently by the upstream community.** Our machine is Python
3.14.4 — we are permanently on the non-deterministic fallback.

**The workaround works and is verified in-thread: set `PYTHONHASHSEED=0`** and `graph.json` becomes
byte-identical across rebuilds. Essential if we ever commit the graph.

Downstream cost: [#2298](https://github.com/Graphify-Labs/graphify/issues/2298) — LLM community
labels are discarded on *any* membership change, so a one-node delta re-labels a 200-node
community. That is the mechanism behind the $7.89-per-run figure in F4.

### F4. Why `--update` costs so much — and the defect that is in our exact runbook

⚠️ **[Issue #2033](https://github.com/Graphify-Labs/graphify/issues/2033) is the most directly
applicable finding in this section.** The shipped `--update` runbook **omits `kind="ast"`**, so
`detect_incremental()` defaults to `kind="semantic"` and reports **629 of 629 files changed when
the real delta was 12** — dispatching ~29 semantic subagents for nothing. Our
`references/update.md` calls `detect_incremental(Path('INPUT_PATH'))` with no `kind` argument, so
**we have this defect.**

Compounding it, [#2227](https://github.com/Graphify-Labs/graphify/issues/2227): the `code_only`
fast-path check hard-codes **31 of 94** code extensions, so code-only updates silently take the
full semantic path anyway.

The measured decomposition ([#1710](https://github.com/Graphify-Labs/graphify/issues/1710)): the
deterministic math is **0.89 s at 14.8 k nodes**, but the agent-walked `/graphify --update` takes
**~14 minutes at $7.89 per run**. **Over 90% is LLM-turn overhead**, mostly re-labeling communities
whose membership barely changed.

And you can't see what you're spending: [#1769](https://github.com/Graphify-Labs/graphify/issues/1769)
and #658 — `cost.json` never appends after the first run and reports 0 tokens. (Consistent with our
A-track finding that **no `cost.json` exists anywhere on this machine**.)

### F5. The graph cannot tell you it is stale

[Issue #1624](https://github.com/Graphify-Labs/graphify/issues/1624): as of 0.9.33, `graph.json`
carries **no timestamp at all** — `generated_at` and `built_at_commit` are both absent. A consumer
cannot distinguish a graph built five minutes ago from one built five months ago. The community PR
adding a stamp plus a stale warning has been **open since 2026-07-03, unmerged**.

Related: [#2051](https://github.com/Graphify-Labs/graphify/issues/2051) — stale semantic nodes for
**deleted files** are preserved indefinitely and returned as authoritative.

**Combined with F2, that is the real risk profile: an agent answering confidently from a graph that
no longer describes the code, with no signal that anything is wrong.** Our BIMpossible graph is 17
days stale and carries no way to say so.

### F6. Edge quality varies enormously by language — check yours first

**108 issue titles concern edges; 93 of them contain the word "silently."** Extraction failures
don't error — they produce a thinner graph that looks fine.

Measured recall bugs: Kotlin #2526 (**45% → 95%** from a two-line fix), Objective-C #1475
(*~60% of relationships silently dropped*), plus Swift #1327, Scala #2055, Rust #2234, C++ #2348,
SQL #2180, and TS #2340 (`${configDir}` in tsconfig paths drops **every** aliased import in a TS
5.5 monorepo). False positives too: #2236 (Python `uses` cross-products every local class against
every imported symbol at confidence 0.5).

**Practical rule: check the open edge-recall issues for your primary language before trusting the
graph.** Recall spans roughly 45% to 95% depending on language. This applies directly to the C#
question in section D — that is exactly the kind of coverage nobody has measured for us.

Also relevant to god-node metrics: [#2460](https://github.com/Graphify-Labs/graphify/issues/2460) —
identifier resolution is **name-only**, producing false cross-module edges and inflated
betweenness. **The "god nodes" can be wrong**, which undermines the headline feature.

### F7. CLI parser traps, verified verbatim from Praetorian's workflow

- **graphify silently ignores unrecognized flags** — a typo'd flag is skipped, not rejected.
- **A dash-leading path is treated as "no path"**, and it then silently scans `.` instead.
- **There is no `--` end-of-options separator**; a stray `--` is silently consumed.

⚠️ **Supply chain: the single-`y` `graphify` name on PyPI is an unregistered typosquat slot** —
`https://pypi.org/pypi/graphify/json` returns **404**, confirmed this session. With near-daily
releases and a large audience, `pip install graphify` is one keystroke away from installing whatever
someone registers there. Our existing memory notes the name confusion; this sharpens it from
"awkward naming" to "an open slot worth watching."

### F8. Scale ceilings, confirmed from the field

[#341](https://github.com/Graphify-Labs/graphify/issues/341): `graphify update` ran **114 minutes
without completing on a 96-core Xeon** (50 k+ files, ~450 k nodes) and had to be killed. Cause:
`_rebuild_code()` calls `suggest_questions()`, which runs `nx.betweenness_centrality(G)` — O(V×E),
roughly 310 billion operations. [#1958](https://github.com/Graphify-Labs/graphify/issues/1958):
`detect()` is CPU-bound for **35+ minutes on a ~16 k-file monorepo** while `git ls-files` enumerates
the same corpus in about a second, the time going to per-path ignore evaluation across 37 nested
`.gitignore` files.

**51 issue titles are Windows-specific** — SIGALRM guards that no-op, focus-stealing console
windows, backslash allowlist rejections. **Windows is a second-class platform here**, which is
consistent with our own A2/A3 findings being Windows path bugs.

### F9. Third-party confirmation of E5 (don't hook it)

The one substantive third-party writeup (dev.to, with an affiliation caveat — it may have been
written by the author of a competing tool, so weight its measurements above its recommendation)
reports wiring `graphify update` into a Claude hook produced *"3 stuck processes at 65–73% CPU
each, load average 12+, RAM saturated, machine unresponsive."* Meanwhile the maintainer actively
recommends hooks (discussion #921), because passive `CLAUDE.md` rules get rationalized past.

**Both are true. The resolution is `ahelme/graphify-kit`'s approach: an advisory, never-blocking
PreToolUse nudge rather than the upstream blocking hook, and never `graphify update` inside it.**

### F10. Maintenance reality

104 k stars, 10 k forks, created **2026-04-03 — four months old**, Apache-2.0, 96+ PyPI releases in
four months, YC S26. Origin was a Karpathy tweet.

**Maintained intensely, but by one person and asymmetrically.** safishamsi has **956 commits**; the
#2 contributor has 27. Issues are often filed and closed the same day — genuinely responsive. But
**135 merged PRs out of 1,194 — 462 open, 597 closed unmerged**, with PRs open since April.

That asymmetry is why **workaround-carrying skill forks exist** (e.g.
`coolbao0131/graphify-skill-customized`, carrying fixes for the graphml hyperedge crash, the
query-parser punctuation bug, and the `--update` graph-diff direction bug). The existence of those
forks is itself the maintenance signal.

A visible split in the user base: the top-reacted *open* issues are all "more languages, cheaper
local backends" — the mass audience. The quality complaints in F2/F5/F6 come from a much smaller
set of sophisticated users, and **those are the issues that stay open longest.**

Two small discrepancies worth recording: the YC announcement says *"MIT licensed. Free forever"*
while the repo's actual licence is **Apache-2.0**; and one reviewer reports `GRAPH_REPORT.md`
*"came out blank on my run."*

---

## Findings, ranked

### Things that are quietly broken or wrong right now

| # | Finding | Fix effort |
|---|---|---|
| **A1** | graphify is **frozen at 0.8.35** and structurally cannot self-upgrade — the `uv tool install --upgrade` path sits behind `command -v uv` and `uv` is not installed anywhere on this machine. The `graphify-cycle-detection-artifacts` memory records the opposite risk (silent auto-upgrade drift); it is inverted and needs correcting. | — |
| **A3** | `.graphify_root` is written by `echo "$(cd … && pwd)"` under Git Bash, producing an **MSYS path** (`{repos}/AI-Dev/…`) that the recorded Windows interpreter cannot resolve. Argless `graphify update` is broken for every skill-created output directory. | 1 line |
| **A2** | `graphify update` writes `manifest.json` CWD-relative but the graph input-path-relative, so BIMpossible's manifest and graph live in different directories. Makes `--update` CWD-sensitive. Same defect family as upstream #2259/#1151, which **duplicates the entire graph on Windows**. | wrapper change |
| **B1** | The reference docs state flatly that **no ignore mechanism exists**. `.graphifyignore`, `.gitignore` fallback, `.graphifyinclude`, and `--exclude PAT` all exist. This changes the recommended large-repo workflow from "manually narrow to a subfolder" to "write an ignore file". We have none anywhere. | doc fix + 1 file |
| **B2** | **Leiden clustering has never run on this machine.** The `leiden` extra needs `graspologic`, which is Python <3.13; this machine is 3.14.4 and `graspologic` cannot install. Clustering has silently fallen back to Louvain on every graph, including the community counts on the metrics ledger. | unfixable here — just know it |
| **B8** | Query logging is **on by default**, appending verbatim questions to `~/.cache/graphify-queries.log` with no rotation and no size cap. **The file already exists here.** Local-only, so hygiene rather than leak. | 1 env var |
| **E5** | `graphify hook install` spawns **unbounded concurrent rebuilds with no lock** (upstream #791, ~10 × 4.1 GB observed, closed without a fix). This retracts the "zero-cost hooks" line in C4. | don't install it |
| **C3** | `--update` is upstream's worst-tested path: #2276 silently drops 56% of edges while reporting success; #1711 loses 12% of doc↔code edges per cycle. `Update-Graph.ps1` uses exactly this mode. | prefer `--force` |
| **F4** | **Our `references/update.md` has upstream defect #2033 verbatim** — it calls `detect_incremental(path)` with no `kind` argument, so it defaults to `kind="semantic"` and reports *every* file as changed. Upstream measured 629/629 reported against a real delta of 12, dispatching ~29 semantic subagents for nothing. Combined with #1710 (>90% of `--update` wall-clock is LLM-turn overhead, $7.89/run) this is the single largest cost defect found. | 1 kwarg |
| **F3** | Community assignments are **non-deterministic across identical runs** (196 of 11,819 differ) because of the same Louvain fallback as B2. Verified workaround: **`PYTHONHASHSEED=0`** makes `graph.json` byte-identical. | 1 env var |
| **F5** | **`graph.json` carries no timestamp at all** — no `generated_at`, no `built_at_commit`. Nothing consuming our 17-day-stale graph can tell that it is stale. Upstream PR adding this has been open and unmerged since 2026-07-03. | stamp it ourselves |
| **F7** | The CLI **silently ignores unrecognized flags**, treats a **dash-leading path as "no path" and scans `.` instead**, and has no `--` separator. A typo'd flag in `Update-Graph.ps1` would be skipped, not caught. Separately, **the single-`y` `graphify` name on PyPI is an unregistered 404 slot** — one keystroke from a supply-chain incident. | validate args |
| **F6** | Edge recall varies from ~45% to ~95% **by language**, and failures are silent — 93 of 108 edge-related issue titles contain the word "silently". Nobody has measured C# recall, which is exactly what section D depends on. | measure before trusting |

### Coverage gaps

| # | Finding |
|---|---|
| **E0** | **Every node in the BIMpossible graph is `_origin: ast`.** 10,082 nodes, zero LLM-derived. The expensive semantic half of the pipeline contributed nothing to the graph we actually use. Reproduce before tuning semantic extraction. |
| **A4b** | **Add-Ins has never been graphified** — 3,565 `.cs` files, 341,467 LOC, completely invisible. BIMpossible's frontend likewise. `~/.graphify` doesn't exist, so the multi-repo global graph has never been used. |
| **A5** | BIMpossible graph refresh is manual-only and 17 days stale — `Push-And-Verify.ps1` dropped the call 2026-07-06 and `Complete-Wave.ps1` was deleted 2026-07-10. |
| **A4** | `BIMpossible_Workspace\graphify-out\` is a misplaced 2026-06-09 artifact whose manifest keys are BIMpossible's backend. Safe to delete. |

### Capability we own and don't use

| # | Finding |
|---|---|
| **B3** | 43 subcommands exist; the skill drives ~14. Undocumented and useful: **`affected <symbol>`** (blast radius — the natural "what breaks if I change this" tool, driven by nothing), `provider add` (custom LLM backends), `global add` (cross-repo graph), `diagnose multigraph`, `check-update`. The skill **hand-reimplements `cache-check`, `merge-chunks` and `merge-semantic` in bash**. |
| **B4** | Real performance knobs exist and are undocumented: `--max-concurrency` (default **4**), `--token-budget`, `--max-workers`, `--dedup-llm`. The skill hand-rolls parallelism instead. **CLI `query` hardcodes depth 2; MCP `query_graph` defaults to 3 (clamped 6) — the MCP path is strictly more capable than the path the skill uses.** |
| **B5** | Unused ingestion: **live PostgreSQL introspection** (`--postgres DSN`) would put the BIMpossible schema in the same graph as the code. Also MCP-config ingestion, Office documents, Google Workspace. |
| **B7** | MCP server is bigger than documented — 10 tools (not 7), 6 undocumented resources, and an **undocumented HTTP transport** with API-key auth that binds to 127.0.0.1. |
| **B11** | `remap_communities_to_previous` gives **stable community IDs across rebuilds** — directly useful for the metrics ledger. `find_import_cycles`'s `top_n=20` is the display cap already blamed in `Update-Graph.ps1`'s comments for flat-lined cycle counts. |
| **C1** | We are **52 releases behind**. The phantom-import-cycle defect recorded in memory was named and fixed upstream (#1402, 0.8.46) entirely inside the window we missed. |

### What to actually do, in order

**Tier 1 — hours, all pure correctness, no upgrade needed**

1. **Pass `kind="ast"` to `detect_incremental`** in `references/update.md` (F4). One keyword
   argument. Upstream's own measurement is 629 files reported changed against a real delta of 12.
   This is the biggest cost defect found and the cheapest thing on this list.
2. **Fix A3 and A2** — write `.graphify_root` from Python instead of `echo "$(cd … && pwd)"`, and
   run `update` from the graph's own directory. Two one-line fixes; argless `graphify update` is
   currently broken outright.
3. **Set `PYTHONHASHSEED=0`** wherever graphify runs (F3). One environment variable, and it's the
   only available mitigation for the Leiden-unavailable problem in B2.
4. **Adopt the AST-only default** — `--code-only`, no LLM, no key, ~30 s (F1). Four independent
   orgs run production this way, and our graph is already 100% `_origin: ast` anyway (E0), so the
   semantic pass is currently pure cost for no nodes.
5. **Write a `.graphifyignore`** — start from `cp .gitignore .graphifyignore` (B1/F1).
6. **Rewrite the skill's routing rule** to "grep answers backward, the graph answers forward"
   (F2). Exact-symbol lookups go to ripgrep; the graph handles forward/impact questions via
   `affected`. As shipped, the skill routes *everything* through a graph that is 377× slower and
   less accurate on the common case.
7. **Stamp the graph ourselves** with build time and commit (F5), since upstream won't. Nothing
   currently consuming our 17-day-stale graph can tell it's stale.

**Tier 2 — a day or two each**

8. **Graphify the Add-Ins repo at all.** 341 k lines are currently invisible. Tree-sitter C# is a
   file-relevance map, not a call graph — accept that and tag the edges `AMBIGUOUS`. **Seed
   `IExternalCommand` implementations as known graph roots first** (D2), or dead-code analysis will
   confidently report every ribbon command as unreachable. Then **measure C# edge recall before
   trusting anything** (F6) — it spans 45–95% by language and nobody has measured C#.
9. **Replace BFS with Personalized PageRank** in `graph_signals.py` (E2). Ten lines, no dependency.
10. **Add a Qwen3 cross-encoder reranker** (E2). 5–15 nDCG points, no index, no embedding pipeline,
    and the conventional bge-reranker advice is 32 points wrong for code.
11. **Schedule `graphify update` from `Refresh-Dashboard.ps1`** — *never* the git hook (E5/F9).
12. **Upgrade to 0.9.36 as one deliberate job.** Not free: 0.9.0 changed node IDs, so the existing
    10.6 MB backend graph is invalidated and needs `extract --force`, and any Neo4j export needs
    re-importing. Pin explicitly to **0.9.36** — 0.9.35 was tagged but its PyPI publish was
    cancelled (#2514). Never `--upgrade` (F1/F7).

**Tier 3 — only if retrieval still feels weak after the above**

13. Embeddings as a *second* seed source fused by RRF, `qwen3-embedding` into LanceDB (E2/E3).
14. `scip-dotnet` for type-resolved C#, or a Roslyn extractor if the C# graph becomes load-bearing
    (D3).
15. VizTracer under pytest for *observed* Python call edges tagged `_origin: runtime` (E4) — the
    same trick would fix the Revit reflection blind spot if applied to a live Revit session.

**Explicitly not worth doing:** adopting any GraphRAG framework, building community summarization,
migrating to a graph database, installing graphify's git hook, or replacing graphify with any of
the star-heavy alternatives (E1, E4, E5, E6).
