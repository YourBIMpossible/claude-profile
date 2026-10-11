# vendor/ — pinned copies of upstream-owned hook code

Nothing here is developed in this repo. Each directory is a byte-for-byte pinned copy of
an upstream subsystem's runtime files, kept here only so `bootstrap.ps1` can install it
under `~/.claude/hooks/` (C1 in `hooks/README.md`) instead of the hooks running from a
switchable checkout. Fix bugs upstream, then re-pin. `.gitattributes` marks `vendor/**`
`-text` so the bytes (CRLF, as checked out upstream) are stored and installed unchanged.

## evidence-capture (+ evidence-footprints/shell_paths.py)

- Owner: Claude-Tools (`F:\Claude-Tools`, repo `YourBIMpossible/claude-tools`).
- Pinned from: commit `8a21f0a18d23f0d529e28dedf95a4459d5f4d0e8` (local `main`, 2026-10-06;
  not yet on `origin/main` at pin time — 2026-10-11). Working tree for these paths was clean.
- Unit: every top-level `evidence-capture/*.py` and `evidence-capture/boundary/*.json`
  (exactly the set `pipeline.tool_identity()` hashes), plus `evidence-footprints/shell_paths.py`,
  which `screen.py` imports from `<unit>/../evidence-footprints`. Upstream tests, caches and
  docs are not copied. Standard library only; no third-party dependencies.
- `tool_sha256` of this unit: `ce559bfe38d98a730f0d4195f4349862ec19e253f4acb54d1f0743d3d9bf46e9`
  (identical to the upstream checkout at the pinned commit). The installed copy has no git
  directory above it, so manifests record `tool_commit: null`; this file maps that
  `tool_sha256` back to the commit.
- Not copied: `~/.claude/evidence-capture.json` (store/destination config), the stores
  themselves, any runtime evidence. Those stay where they are.

Re-pin procedure:
1. Upstream: commit the change in Claude-Tools; confirm `git status` is clean for the unit.
2. Here, on a branch: copy the same file set from the upstream working tree into
   `vendor/` (delete files upstream removed), then refresh the table below and the
   `tool_sha256` line (`python -I -c "import sys; sys.path.insert(0, 'vendor/evidence-capture'); import pipeline; print(pipeline.tool_identity())"`).
3. `pytest hooks/tests`; PR; after merge run `bootstrap.ps1`, which replaces the installed
   unit as a whole and keeps the previous one as `<unit>.pre-profile-backup-<stamp>`.

Rollback: delete `~/.claude/hooks/<unit>` and rename the newest
`<unit>.pre-profile-backup-<stamp>` back; to return to the upstream-checkout registration,
restore `~/.claude/settings.json.pre-capture-pin-backup-20261011`.

| File | sha256 |
|---|---|
| `evidence-capture/audit.py` | `938fa8376df40b3acea9165fbad92da0409c094067aad06446e8158623eb9cea` |
| `evidence-capture/backstop.py` | `2d7ae91dac4847cf0e15b188c27b3980f82b5b17e26bb632fa5e2c1191e30673` |
| `evidence-capture/boundary/2.1.280.json` | `a55fdf5fa00fe8fddb9fb656f8f1ab8c939ab6ed08828965794b49bcc8408c75` |
| `evidence-capture/boundary/2.1.281--claude-desktop.json` | `9d06407e3aa3048926e1e9672bbc4c3bce015f6a563a4c9c116f7adaf01eb211` |
| `evidence-capture/boundary/2.1.288--claude-desktop.json` | `e77f23b4d52e486956d92f0e48f579af3a945e4ab93bfe43ecb4bb0c40a7098a` |
| `evidence-capture/boundary_test.py` | `6b794493d9d53b062b49864d98115b07803b1865547df6368ff33e572db65a1c` |
| `evidence-capture/capture.py` | `371353e2d7644a574c7f3e0a5fd0e58bbe055ad3cc4ef1ee4f28170bd3d0d0eb` |
| `evidence-capture/capture_end.py` | `47f6881505f369b8eda0af02c919244befbdf30066c54aefd4ed62813b9886a6` |
| `evidence-capture/capture_start.py` | `ead99aefe781e5f74cf4b6d0106a69d7c996e1fd9a75891e537a54ebcae6a67e` |
| `evidence-capture/causal.py` | `8e4b07b1d988b72dc48ede43522c83a71b119591c962b22e466aafc3446e44dc` |
| `evidence-capture/causal_admit.py` | `28c0a12d28143c19ee91e130d323335aa36352122d815a0168d6c88933a5e440` |
| `evidence-capture/causal_hooks.py` | `e4fc8351a46515da0267ea6848a487c09590766d28432f78a0d2fff2c8928c87` |
| `evidence-capture/client_identity.py` | `16dc8d3ad51bed50632c8c3f8df9eeae6c95435cf8af76c256741573a241f8db` |
| `evidence-capture/clone_builder.py` | `7b25789500d8fa51687cdb7186d14d3df3905b8280d5588f9cfe73d8265c3824` |
| `evidence-capture/common.py` | `cbf3662d522844d854e0ee9d8568a51a50906c039a2b75cd1c11e6eafe8dc3a9` |
| `evidence-capture/desktop_boundary_test.py` | `5b82ca4b1447c47d942cae80076aa277ed8e9d0241c5f6cb44c73f3af0d9d0a4` |
| `evidence-capture/desktop_host_probe.py` | `cf88ece66e592e77f9164b3b19cdc9e7fc48268ca1f0382ad58958f44284fbe3` |
| `evidence-capture/dryrun.py` | `5a0ee0f9ee8021dc9903cf84a0003d00f60cce97768e0c6dfc4fbd9cbe7a9b92` |
| `evidence-capture/isolation.py` | `5c3e2abc430cc457238aff294719c76fcf36345747faccb53007e4a0ca0cc6e0` |
| `evidence-capture/join.py` | `239a08f41b8c7048170ff0b0f2894e868002ed93a2256d33f3c7de8948088168` |
| `evidence-capture/linkage.py` | `cf4132c43703612b0687b657249ac1020ae1b5bf3d99a2c61597d8c9b7b7c867` |
| `evidence-capture/manifest.py` | `3c4647f94d50a0a7ad228d2928dd95a7be03721b32a2858450e4da50a4a90efa` |
| `evidence-capture/pipeline.py` | `389c25952f71a622f135f327276cd7d1c094ed73e86a6897cfb8caaba89ac0e6` |
| `evidence-capture/report.py` | `9922fdd15c4588bb4866cba1cbe5ac2786320181ef7249d341968181293f418c` |
| `evidence-capture/rescreen.py` | `ff145a7903476da570e04d4f0164a4693ffa5c4600bcc3867237780e58037a8c` |
| `evidence-capture/review.py` | `bc7366ed5fc5bdc17b329b477c337ca759493ecf53565491e475749e40a49660` |
| `evidence-capture/screen.py` | `f252f1b674587cf3c938ee5f0fe972b7cf8a9e509f881be6731159340c6d6d05` |
| `evidence-capture/snapshot.py` | `eb6b5b8ebbfb439f03956eb5fdeb4ba77841e0e74b5511dfc189a18959b877a5` |
| `evidence-capture/store.py` | `dd29c8751806d8fac1c83754182dea7005854e9dbb14dd31e8d31687c472d38a` |
| `evidence-footprints/shell_paths.py` | `f3068e77b0d20cfee6f2d0cef7dc42bd86b2d7ccfa52b550df7033c67f81be51` |
