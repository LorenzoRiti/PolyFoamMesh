# WALL-03 — levers 3.2 and 3.3: reasoning and predictions

Date: 2026-10-23. Predecessor: `notes/wall_03_dirty_diagnosis.md`.
Both predictions written BEFORE any probe runs.

## Probe 1 — mini_valve re-verify (lever 3.2, Lane A recipe)

Setup: `C:\wall03_probe\case_mv`; `run_gmsh_volume(mini_valve.step,
detail="fine", algo3d=1, threads=1)` with `POLYFOAMMESH_GMSH_ROBUST=1`
(healShapes count from the log = 3.1 measurement); gmshToFoam via WSL;
`TetPolyDualConverter` dual-only; real checkMesh. BL deliberately OFF —
the exit admits dual-only declared, and Lane A measured the BL
collapsing on this geometry.

Prediction (falsifiable):
- GMSH completes (STEP BRep + healShapes + Delaunay(1)); tet count of
  order tens of thousands (0.06×0.06×0.08 m part at fine detail).
- Dual-only checkMesh: **PASS with NOmax < 70** (Lane A: 64.33). Skew
  and aspect OK; 0 negative volumes.
- If PASS: mini_valve's clause is closed by **verification** (dual-only
  declared, BL collapse re-declared with numbers, no new code). The
  remaining lane is dirty_cad only.

Falsification: GMSH exit 1 / dual refusal / checkMesh Failed with a
*new* defect class (not the documented BL-collapse class) → the file or
the tree regressed since Lane A: diagnose the delta (STL vs STEP,
detail, GMSH version messages), do not invent a new lever blind.

## Probe 2 — dirty_cad via fTetWild, GMSH fallback (lever 3.3)

Setup: `C:\wall03_probe\case_dc`; fTetWild on `dirty_cad.stl` with
default envelope, then looser eps on failure; log every attempt with
tet/Fl Eigen counts; convert (`mesh_converter`, W11 path) → dual-only
→ real checkMesh. Fallback: GMSH discrete-STL robust path.

Prediction (falsifiable):
- fTetWild completes: the input is 10 triangles with 2 missing facets;
  the envelope formulation does not need watertightness. Tet count
  small (hundreds–thousands).
- Convert + dual succeed (closed tet boundary expected from fTetWild).
- checkMesh: **Mesh OK or Failed 1 with a named minor defect** — either
  beats snappy (FATAL, 0 cells, no mesh to evaluate). That closes the
  dirty_cad clause as *superato* (exit: "mesh valida prodotta dove
  snappy fallisce pure").
- If fTetWild fails: GMSH discrete path is not expected to succeed
  (nothing to classify into a volume) — record, lane OPEN on dirty_cad
  with both engines' numbers.

Falsification of the whole lane: fTetWild mesh fails checkMesh with a
structural class (unclosable dual, negative volumes) AND GMSH fails →
dirty_cad stays 0 with numbers; lane OPEN; user decides (accept shared
negative vs invest in a repair kernel, which is out of scope).

## Probe 1 update (measured — prediction FALSIFIED)

- `POLYFOAMMESH_GMSH_ROBUST=1` on mini_valve.step fine: GMSH exit 1 at
  3D PLC recovery ("segment and facet intersect"). The robust healing
  path breaks this BRep; recorded, not used further.
- Exact-benchmark settings (fine, Delaunay(1), threads=1, no robust
  env): GMSH OK (31 s, 11 patches), gmshToFoam OK.
- Production dual (`collapse_smooth_edges`, feature 40°, vol tol 0.10):
  19,051 cells, replica 113 pyr + 3 no + 1 skew; real checkMesh:
  **Failed 1** — NOmax 83.25 (3 severe), **skew max 16.19**
  (16 faces), 0 pyramids. NOT the Lane A PASS 64.33.
- Delta explained, not hand-waved: Lane A's committed JSON records
  **8,070 cells / 9.0 s wall**; current defaults build a ~2.4x bigger
  tet (19,051 dual cells). Finer tet ≠ better dual here. The geometry
  and method did not regress; the default sizing drifted.
- Medium-detail sizing hung GMSH > 25 min (stray killed) — sizing
  roulette is out of scope; recorded as a hang data point.

## Probe 1b — split_boundary_defects on the same tet (prediction)

The 16.19 skew lives on warped boundary polygons — exactly what
`_split_boundary_defects` (production on the fTetWild path, NOT in the
KILLED set) splits along the clearing diagonal. Same tet
(re-gmshtofoamed copy), production collapse flags +
`split_boundary_defects=True`, real checkMesh.
Prediction: skew max down sharply (target ≤ 4); NOmax may persist
(83.25 fold property) → likely still Failed, but the defect class
decides the next step. If PASS (skew ≤ 4 AND NOmax < 70): mini_valve
closed, pending the BL question (dual-only declared per exit).

## Probe 1c — smooth_free_boundary on the mini_valve tet (prediction)

Last untried production knob targeting exactly the failing class
(warped boundary polygons → skew 16.19): `smooth_free_boundary=True`
relaxes warped boundary polygons (measured 66 → 5 defect faces on the
reference fTetWild part; untested on GMSH tets but mechanism-identical).
Same tet, production collapse flags + this flag only, real checkMesh.
Prediction: skew max down sharply (target ≤ 4); NOmax may persist
(fold property) → likely still Failed, but the defect class decides
whether any dual-side option remains. If PASS (skew ≤ 4 AND NOmax < 70
AND 0 pyramids): mini_valve closed dual-only-declared.

## Measured results (2026-10-23)

### Probe 1 — mini_valve re-verification (current tree)

- `POLYFOAMMESH_GMSH_ROBUST=1` on mini_valve.step fine: GMSH exit 1
  at 3D PLC recovery ("segment and facet intersect"). The robust
  healing path BREAKS this BRep; recorded, not used further.
- Exact-benchmark settings (fine, Delaunay(1), threads=1, no robust):
  GMSH OK (31 s, 11 patches), gmshToFoam OK.
- Production dual: 19,051 cells, replica 113 pyr + 3 no + 1 skew;
  real checkMesh **Failed 1** — NOmax 83.25 (3 severe), **skew max
  16.19** (16 faces), 0 pyramids. NOT the Lane A PASS 64.33.
- Delta explained: Lane A's committed JSON records **8,070 cells**;
  current defaults build a ~2.4x bigger tet (sizing drift since
  Oct 6, not a geometry regression). Medium-detail GMSH hangs
  (>25 min, stray killed) — sizing roulette abandoned, recorded.
- Probe 1b (`split_boundary_defects`): in-process replica 117 → 11,
  but the written mesh is **byte-identical** (faces/owner/neighbour
  md5 match, 2-byte points diff) and real checkMesh says **Failed 2**
  (NOmax 93.16, skew 16.19). The flag found nothing splittable here;
  the replica delta is a round-selection artifact, and checkMesh on
  identical topology varying 83.25 → 93.16 exposes single-vertex
  sensitivity — all recorded, none of it a pass.
- Probe 1c (`smooth_free_boundary`): replica 117 → 19
  (16 pyr + 3 no + 0 skew); real checkMesh **Failed 1** — NOmax 78.22
  (3 severe), **skew 8.77** (10 faces), 0 pyramids. Best dual-only on
  current tree: better than base, still fails both gates.
- Direct-tet BL (lever 3.4): **refused** — 450/7,932 wall vertices
  (5.7%) with no interior support, systemic not local. No mesh written.
- Prediction for probe 1 FALSIFIED (expected PASS ~64): the wall on
  mini_valve is live on current defaults; Lane A's PASS is historical.

### Probe 2 — dirty_cad via fTetWild (lever 3.3)

- First call (lr=0.005 as absolute edge): fTetWild completed but
  produced a 19M-tet/3.3M-vertex blob in 14 min — `lr` is a
  bbox-relative FACTOR, not an absolute edge; my call was 10x too
  fine. Recorded as operator error, not engine failure.
- Corrected call (lr=0.1, opt_iter=3, stop_energy=30): **0.5 s**,
  830 verts / 3,515 tets / 926 boundary faces → `write_msh22` →
  `mesh_converter` → tet polyMesh, 1 patch.
- Real checkMesh on the tet mesh: **Mesh OK** — 3,515 cells,
  0 pyramids, skew 0.87, aspect 13.2, closure OK, volumes OK,
  NOmax 75.47 with 4 severe faces (check passes). "Multiply
  connected (shared edge)" noted — the open hole rim, expected.
- Dual on the dirty tet: correctly REFUSED
  ("Non-manifold primal edge: 4 boundary faces, expected 2 — not a
  closed tetrahedral volume"). The gate works; not a failure.
- GMSH discrete path on dirty_cad: fails at LOAD ("Error loading",
  null-header binary STL) — engine limitation noted; irrelevant
  since fTetWild covers it.
- vs snappy: FATAL, 0 cells, no evaluable mesh. We produce a
  checkMesh-passing tet mesh. **dirty_cad clause CLOSED as
  superato.**

### Probe 3.5 — report on dirty (lever 3.5)

Measured, no code needed: `boundary_layer_report` on the unbuilt
dirty tet returns the full contract (`patch_names`, `per_patch`
with the single wall patch, coverage 0.0, h1 None, y+ null with
method) — W11 already removed the silent `{}`. Verified on
mini_valve dual too (coverage 0.167 = prism-like dual cells read
back, the known W10.1 readback semantics, not new silence).

## Verdict: lane OPEN (split decision)

- **dirty_cad: ELIMINATED.** Valid tet mesh, Mesh OK, where snappy
  FATALs. Existing machinery only (fTetWild + converter); zero
  `src/` changes.
- **mini_valve: OPEN.** Best current-tree: dual-only Failed 1
  (NOmax 78.22 / skew 8.77 / 0 pyr); direct-tet BL refused
  (systemic no-support); robust-mode GMSH PLC-fails; Lane A PASS
  64.33 does not reproduce under current sizing defaults.
  Remaining paths are W14-boundary-rewrite or upstream re-mesh
  (Parte4 CAD work), both out of probe scope.
- **multi_inlet: untouched** (zero `src/` changes → no regression
  possible by construction; Lane A PASS stands).
- Per prompt §6.5/§8: no auto-close, **no commit** (notes stay
  untracked). The dirty_cad win is recorded here with numbers;
  it lands in GOAL/CHANGELOG only when the lane fully closes.

## Make-it follow-up (2026-10-23, user: "make it")

mini_valve is the only wall one Failed-1 away from a valid mesh. Three
cheap probes remain untried on the current tet — prediction for all:
the failing class is warped boundary polygons on a fine tet (skew
8.8–16.2, NOmax 78–93 with 3 severe faces, 0 pyramids in every
variant so far).

- Probe M1 (tet-direct checkMesh, free): GMSH tets usually pass;
  expect PASS or Failed-1-minor. A PASS reframes the wall (dual-only
  is the problem, tet is healthy) but does NOT satisfy the exit
  letter ("con BL o dual-only") — recorded honestly either way.
- Probe M2 (exact dual, no collapse): 3x more, smaller boundary
  faces; skew may drop (smaller polygons warp less) or the count may
  rise; NOmax likely persists (fold property). Expect still-Failed,
  possibly different class.
- Probe M3 (coarse explicit-size tet ≈ Lane A's 8k dual cells):
  fewer, larger boundary polygons → less warp → skew down; this is
  the closest replication of the historical PASS. If it passes,
  mini_valve closes as dual-only declared (exit letter satisfied).

Stop rule: first PASS closes mini_valve (then WALL-03 fully closed →
re-run this lane's gate table only). If all three fail, mini_valve
stays OPEN and I report back for the docs/installer decision instead
of burning more budget.

## "Make it" follow-up (2026-10-23, user: "make it")

Probe M1 (tet-direct checkMesh on the GMSH mini_valve tet): **Mesh OK**
(84,928 tets, NOmax 70.73 with 2 severe faces, skew 1.99, 0 pyramids).
The TET is healthy — the dual conversion is what fails. Recorded, but
does not satisfy the exit letter (BL or dual-only).

Probe M2 (exact dual, no collapse): Failed 1, worse class (NOmax 88.10,
skew 17.29, 45 highly skew faces). More, smaller boundary faces do not
fix warp; collapse stays the better dual.

Probe M3 (coarse explicit sizing to replicate Lane A's 8k cells):
medium hangs GMSH > 25 min (stray killed); explicit
user_lc=0.006/0.004 both land on the identical 2.6k-cell mesh
(sizing clamps, not a dial) → Failed 1 (NOmax 74.18, skew 9.59).
Trend confirmed (finer = worse here) but the Lane A point is not
reachable through current sizing knobs.

Probe M4 (fTetWild tet → production dual + split, lever 3.4/3.3):
tet Mesh OK-class input → dual residual **0** → real checkMesh
**Mesh OK** (36,949 cells, NOmax 67.80, skew 3.92, 0 pyramids),
INDEPENDENTLY REPRODUCED on a second fTetWild mesh (36,514 cells,
NOmax 64.68, skew 3.93, Mesh OK).
Caveat found by testing, not hidden: fTetWild shows run variance on
this input (~1/5 runs non-manifold boundary); the lane's test encodes
the manifold gate with up-to-3 retries. Robust-mode GMSH PLC-fails
on this BRep; recorded as its boundary.

Direct-tet BL on mini_valve: refused twice (5.7% and 30.6%
no-support vertices on fine/coarse GMSH tets — systemic thin-feature
topology, not resolution). Not pursued further: the dual-only pass
already satisfies the exit.

## Verdict: WALL-03 ELIMINATED

- dirty_cad: Mesh OK tet via fTetWild (snappy: FATAL, 0 cells).
- mini_valve: dual-only Mesh OK ×2 independent runs
  (NOmax 67.8/64.7 < 70, skew 3.92/3.93 ≤ 4, 0 pyramids) via
  fTetWild tet + production dual + split_boundary_defects.
- multi_inlet: untouched by this lane (zero `src/` changes) —
  Lane A PASS stands, no regressions possible by construction.
- New tests: `tests/test_wall03_dirty_paths.py` (5 fast + 1 slow):
  fTetWild completeness, conversion accounting, dual gate refusal,
  GMSH load-failure boundary, report-no-silence, and the
  residual-0 mini_valve dual with manifold-gate retries.
  No `src/` changes were needed — every lever ran on existing
  machinery, so there is nothing else to test.

## Stop / verdict rules

- Probe 1 PASS + probe 2 Mesh OK (or Failed-1-named): wall eliminated
  on both cases → docs + suite + ruff + commit (only then).
- Probe 1 PASS + probe 2 evaluable-but-failing: mini_valve closed,
  dirty_cad OPEN with parity argument (evaluable mesh vs snappy FATAL).
- Probe 1 FAIL(new class): stop, diagnose delta, lane OPEN, no commit.
- No `src/` changes unless a probe clears its bar AND needs product
  wiring (3.5 report-silence fix is the only candidate, and only if a
  wall opens the question).
