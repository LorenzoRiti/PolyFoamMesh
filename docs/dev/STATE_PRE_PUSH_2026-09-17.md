# Pre-push state — 2026-09-17

Handoff to decide whether to sync `master` to the public repo.
44 commits since September 9. Today's checks: full suite **1206 passed,
2 skipped, 1 xfailed, 0 failed** in ~14 min (skip = 2 venturi-throat
tessellation-dependent tests; pre-existing xfail); `ruff check src/ tests/`
clean; note hygiene (commit `9d1e6d1`, clean tree). No remote
configured in this clone (`git remote -v` empty): pushing is still
your decision, not the tooling's.

## 1. What has changed since September 9 (by theme, not by commit)

**RAM / scalability** (Lane A + fix `d0fd434`):
- concaveClosure/merge safety guard: from skip above 1.5M cells to skip
  above **8M faces** — memory scales with faces (~0.8 KB/hex face,
  ~1.0 dual; dual ≈ 8.2 faces/cell vs ≈ 3 hex), not with cells.
- `_face_geometry` (bl + tet) in blocks of 250k faces, bit-identical output;
  winding parity-graph in batches + CSR (~10 B/side vs ~150);
  `vert_faces` only wall vertices; winding with in-place flip.
- Measured: real synthetic run 4.99M cells / 15.06M faces, peak 13.05 GB
  (build) and 10.6 GB (merge) under the 16 GB bar.
- Dual-5M cells (~41M faces) projects ~50 GB: out of reach without a rewrite
  of the face representation — stays skipped by the guard, by construction
  it can no longer crash from this path. Details:
  `notes/ram_crash_rootcause_reasoning.md`.

**BL coverage on concave geometries** (Lane B and derivatives):
- `local_termination` decoupled/decoupled_vertex (H4): the valve goes from
  impossible to success at scale 1.0; generalized on cube/groove1/slot1.
- `local_height_retry`: -5.0% exclusions, checkMesh nearly neutral.
- Combined most_visible + retry: **6195 exclusions** (-12.2% from 7054),
  promoted to a documented opt-in combo.
- Exact global winding solve (replaces the greedy one that collapsed at
  scale 0.6 with 378,605 non-positive volumes).
- `early_exit_intermediates`: 15 → 4 builds, ~2215 s → ~450 s on the valve.
- Post-BL merge repair (same concaveClosure flag): valve 340 → 58
  misoriented faces, aspect ratio unchanged.
- Unblocked `smoothed` + `dual_convexity` (was a stale raise).

**Mesh quality** (snappyHexMesh comparison on the cylinder):
- Smoothing-gate fix (always runs, not only with defects above threshold):
  NOmax -19%, skew -16% real.
- Skewness formula fix (was sum instead of OpenFOAM's max): now an exact
  match on two independent meshes; the merge gate really sees the skew.
- Compactness/axis-balance opt-in: NOmax -12.4%, mean -19.3% (≈ snappy),
  skew -8.6%, aspect unchanged. Planarity guard + strict tie-break (`<`).
- Zonal worst-element mode (minimax L_inf): -0.5% on max aspect at
  zero cost — micro-gain, NOT wired into production.

**Bugfix**: `read_label_list` swapped binary payloads with byte 0x29 for
ASCII (corrupted mesh) — fix + regression test. GUI wiring: concaveClosure +
merge repair checkboxes (both opt-in, default off).

## 2. What is DEFAULT today

**Unchanged from before.** Anyone who does not touch options gets bit for bit
the historical behavior (`area_weighted`, no local termination, no
retry, default smoothing, no merge). Proof:
- full suite green including the default runs on the healthy cube;
- every `normal_method` is tested with the same number of prisms vs `area_weighted`;
- zonal mode off ≡ bit-identical default (dedicated test);
- compactness/axis/compactness off by default (signature unchanged);
- GUI: the only two wirings are opt-in checkboxes off by default.

## 3. What is opt-in / experimental (with real numbers)

| Flag | What it does | Measured gain | Measured cost | Recommended? |
|---|---|---|---|---|
| `local_termination="decoupled_vertex"` | local per-vertex termination on concave | valve: from fail to success scale 1.0 | ~8% wall shell given up in the excluded zones | yes, on production concave meshes |
| `local_height_retry=True` | retry with a short stack before excluding | -5.0% exclusions | +1.3% severeNO | yes, with decoupled_vertex |
| `normal_method="most_visible"` | min-max normal per vertex | -6.3% exclusions | +5.2% severeNO, mixed checkMesh | situational (coverage > orthogonality) |
| combo most_visible + retry | both | **-12.2% exclusions** | +2.9% severeNO vs most_visible alone | yes, for max coverage |
| `normal_method="smoothed"` | Laplacian on the normals | ~neutral / situational | ~neutral | situational |
| `normal_method="guided"` | guided bilateral (Zhang 2015) | **0 (measured no-op)** | 0 | no (stays tested in the code) |
| smoothed + dual_convexity | combo unblock | **0 (no-op, -0.23%)** | 0 | no |
| `use_compactness` / `use_axis_balance` (smoother) | shape-aware passes | NOmax -12%, skew -9% | none measured | yes on healthy duals |
| `worst_aspect_thr` / zonal | worst-element focus | -0.5% max aspect | 0 | no (not wired into production) |
| post-BL merge repair (via concaveClosure) | merges concave cells | wrong-orient 340 → 58 | none measured | yes, with concaveClosure |
| `early_exit_intermediates` | skips intermediate scales | 15 → 4 builds | risk if the defect heals at an intermediate scale | yes with local_termination |
| 8M-face guard | skip above threshold | no possible crashes | feature skipped above threshold | automatic (safety) |

## 4. Tried and DISCARDED (do not retry without first reading the note)

- `smoothed` + `dual_convexity`: no-op (-0.23%), the dual fade already terminates
  everything (`notes/smoothed_dual_blend_reasoning.md`).
- `guided`: no-op in principle — the dual normals are not noisy and at the
  ridge the vertex averages between the sides anyway
  (`notes/guided_normal_filter_reasoning.md`).
- Merge-for-aspect on the cylinder: aspect -10.6% BUT NOmax +80%, skew +233%
  (the gate counts violations, not severity — `notes/cylinder_aspect_clusters_reasoning.md`).
- `aspect_lam=10`: NOmax +36%, skew +12% real (blind in-process replica —
  `notes/getme_compactness_reasoning.md`).
- Thin-first: does not generalize, silently shrinks the BL thickness 10x
  (PHASE 4, `docs/residual_risks.md`).
- `split_rounds=1`: +20% time, zero fewer defects (keep-best picks round 0).

## 5. Known residual limits

1. **Cylinder aspect ratio gap 4.49 vs 3.02 snappy**: needs directional
   splits of the slab at the cap (precise diagnosis exists), operator
   not written — the only structural work left open, with a spec.
2. **RAM threshold measured only on synthetic hex** (15M faces → 13 GB); on
   dual the coverage above 1.24M faces is a projection with margin, not a measurement.
   The 8M-face guard is conservative for both.
3. Suite: 2 skips (venturi-throat, tessellation-dependent) + 1 pre-existing
   xfail; the heavy validations (valve, 5M, checkMesh) run only
   via WSL/OpenFOAM 2512, not in CI.
4. No remote in this clone: pushing to the public repo is outside the scope
   of this session by explicit rule.

## 6. What was NOT touched (and why it was not needed)

- `installer/`, `.github/`, `pyproject.toml`, `src/polyfoammesh/commercial/`:
  **zero commits since 9/9** (verified via git log) — packaging, CI and the
  commercial part outside the declared scope.
- GUI: only file touched `gui/params_panel.py`, only the two opt-in checkboxes
  (commits `6511369`, `5ae0b51`); no redesign, no meshing logic.
- Public `main` / `origin`: never touched (not even the remote exists here);
  local work branches (`backup-*`, `claude/*`, `pr-7-*`) not touched.
- Every parameter default: unchanged (see §2).
