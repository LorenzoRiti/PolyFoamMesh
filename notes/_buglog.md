# Buglog and out-of-lane notes / cross-cutting decisions

Found a problem outside your lane? Note it here with file+line and
one line of description. Do not fix it "while you're at it".

## [2026-09-08] Lane F — repo hygiene measurements (clone size)

Clone of the public repo, reachable objects (measured via
`git rev-list --objects` + `cat-file --batch-check`):

- `origin/master` (the new branch with the rename): **98.1 MB** —
  `.py` 43 MB (normal history, ~300 commits), `.npz` 27.3 MB
  (`tests/fixtures/valve_dual.npz`, the only large tracked fixture),
  `.log` 12.3 MB + `.exe` 11.3 MB = **~23 MB of historical junk NO longer in
  the tree** (`.opencode/opencode-loop/loop.log`, `tools/innosetup/*.exe`).
- `origin/main` (public default, NOT touched by the rewrite): **37.0 MB**,
  still contains `src/cfmesh_autogui/` (old name).

Decisions:

- **`valve_dual.npz` stays tracked.** It is under the GitHub limit (100 MB),
  the tests that use it are `@pytest.mark.slow` (skipped in CI) and it is
  regenerated via `tools/poly_fixture_builder.py --regression-fixture`.
  Moving it to Git LFS would require `git lfs migrate` = history rewrite
  + force-push on the public repo = **user decision, not made here**
  (see MEGA_PLAN rules: never force-push without explicit confirmation).
- **Historical junk (~23 MB)**: the `.log`/`.exe` blobs not tracked in the tree
  bloat every clone but removing them requires a history rewrite +
  force-push. **Noted for user decision**, not touched.
- `.gitignore`: deduplicated the `dist/` / `build/` entries (they were 2x).
- `tools/bench_*.py`: **all kept** (commit 2026-09-08, `polyfoammesh`
  imports clean, `bench_bl_poly_partial.py` is the Lane B gate)
  → no deletions.

## [2026-09-08] OUT OF LANE — public default branch = old code

`origin/main` (default of the public repo) still contains
`src/cfmesh_autogui/` (pre-rename). The new, renamed code lives on
`master` (`origin/master`). Implication for Lane G: a visitor of the
repo sees `main` as default → old name. **Cross-cutting decision for
the user**: move the default branch to `master` (or merge) before/after the
release. Not touched by any lane to avoid conflicts.

## [2026-09-08] OUT OF LANE — README.md:151 stale claim

`README.md:151` claims `dist/PolyFoamMesh.exe` but the real artifact in the
working copy is `dist/CFMesh-AutoGUI/` (pre-rename build, not tracked).
README is the most contended file (MEGA_PLAN rules): not touched, noted here
for the user.

## [2026-09-09] OUT OF LANE — read_label_list: binary mistaken for ASCII

Found during PHASE 2 (Lane B health gate), **fixed in place** because
it blocked verification: `core/foam_mesh_io.py::read_label_list` used
`b")" in probe` as ASCII proof; an OpenFOAM binary payload with a byte
0x29 in the first 64 bytes (cell index 41/296/10537...) passed as ASCII, the
per-line parser returned an EMPTY list and `_rename_cylinder_patches`
rewrote the polyMesh with `neighbour 0` →dual conversion failed upstream.
Reproduced 100% on a real gmshToFoam `neighbour` (404 KB binary →
0 elements). Fix: ASCII only if ALL the probe bytes are
`[ \t\n\r\v\f-0-9)]`. Regression:
`tests/test_local_refinement_boxes.py::test_read_label_list_binary_payload_with_0x29_byte`.
Note for the future: gmshToFoam writes the polyMesh in BINARY when the
case's controlDict has `writeFormat binary` (the bench skeleton does);
the readers handle both formats — do not assume ASCII.
