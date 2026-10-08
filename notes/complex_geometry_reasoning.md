# Complex-geometry dual defects — root-cause diagnosis (P0)

Date: 2026-10-08. Task: `docs/superpowers/plans/2026-10-08-complex-geometry-program.md`
Task P0. **Diagnosis only — no repository code was changed.** All numbers below
are from REAL OpenFOAM `checkMesh` (2512, WSL Ubuntu), never the in-process
replica, except where explicitly labelled "in-process".

## 0. Method / provenance

- Case rebuilt from the tet backup with the production kwargs:
  `TetPolyDualConverter(case, collapse_smooth_edges=True, boundary_feature_angle=40.0, collapse_volume_tolerance=0.10).run()`.
- `checkMesh` command: `wsl -d Ubuntu -- bash -lc "source /usr/lib/openfoam/openfoam2512/etc/bashrc 2>/dev/null; cd '<case>' && checkMesh"`.
- Venturi source tet: `C:\cfmesh_cases\gmsh_direct_20261008_103936\constant\polyMesh_tet_backup`
  → 96,196 tets, 204,533 faces (24,282 boundary), 21,798 points.
- Valve source tet: `C:\polybench\valve1\constant\polyMesh_tet_backup`
  → 824,661 tets, 1,687,079 faces (75,514 boundary), 152,086 points.
- Scratch probes under `.superpowers/sdd/2026-10-05-poly-beat-snappy-program/`
  (deleted after): `_rebuild.py`, `_check.py`, `_analyze.py`,
  `_analyze_primal.py`, `_prototype_fan.py`. Case copies under
  `C:\cfmesh_cases\_diag_p0\` (deleted after).

### 0.1 Two different "baseline" meshes — read this first

The dual-only rebuild (production kwargs, fresh) is **21,798 cells / 144,186
faces / 122,441 points**. The on-disk `constant/polyMesh` in the venturi case is
**29,791 cells / 178,980 faces / 141,244 points** — ~8,000 extra cells and more
4-sided faces, consistent with a boundary-layer block on top of the same
interior dual (its max non-orthogonality 97.1771 is bit-identical to the fresh
dual-only build, while its aspect/skew are much higher — the BL signature).

Therefore the mission's headline aspect/skew (51.32 / 65.72) are BL-tainted.
**All A/B measurement in this note uses the fresh dual-only rebuild** (the mesh
the converter actually writes), and the mission's pyramid class (~204) is
reproduced at 224.

## 1. A/B stage attribution (real checkMesh, venturi dual-only)

### 1.1 Headline baselines

| mesh | NO max | NO avg | severe >70 | NO errors | aspect max | skew max | highly skew | **pyramids** | neg vol | failed checks |
|---|---|---|---|---|---|---|---|---|---|---|
| on-disk `polyMesh` (+BL) | 97.1771 | 12.2517 | 74 | 11 | 47.5969 | 59.1763 | 27 | **205** | 0 | 3 |
| **fresh prod dual** | 97.1771 | 11.7154 | 47 | 10 | 27.6227 | 18.3017 | 27 | **224** | 0 | 3 |
| valve prod dual (finer, same geom) | 95.6151 | 10.5694 | 175 | 10 | 100.79 | 22.1645 | 28 | **340** | 0 | 3 |

`NO errors` = checkMesh "Number of non-orthogonality errors"; `severe >70` =
"Number of severely non-orthogonal (> 70 degrees) faces".

### 1.2 Variant table (venturi, real checkMesh)

| variant | NO max | NO avg | severe>70 | NO errors | aspect max | skew max | highly skew | **pyramids** | neg | failed |
|---|---|---|---|---|---|---|---|---|---|---|
| **prod** (collapse + Laplacian×3) | 97.18 | 11.72 | 47 | 10 | 27.62 | 18.30 | 27 | 224 | 0 | 3 |
| collapse OFF (exact per-corner) | 97.26 | 12.44 | 51 | 10 | 24.86 | 20.22 | 36 | **448** | 0 | 3 |
| smoothing OFF (collapse on) | 111.19 | 15.05 | 60 | 16 | 23.57 | 10.84 | 28 | **217** | 0 | 3 |
| `fix_nonplanar_faces=planarize` | 97.18 | 11.72 | 47 | 10 | 27.62 | 18.30 | 27 | 224 | 0 | 3 |
| `fix_nonplanar_faces=triangulate` | 174.04 | 13.29 | 371 | 175 | 27.62 | 128.84 | 173 | **723** | 0 | 3 |
| `boundary_feature_angle=20` | 95.36 | 11.75 | 47 | 10 | 27.59 | 18.52 | 26 | 229 | 0 | 3 |
| `boundary_feature_angle=90` | 97.08 | 11.71 | 47 | 10 | 27.62 | 18.30 | 24 | 216 | 0 | 3 |
| `smooth_iters=8` | (in-process identical to prod: 224/57/7) | | | | | | | ~224 | 0 | – |
| `dissolve_tiny_boundary_stars=0.3` | refused (in-process 356 vs 217) → output ≡ prod | | | | | | | 224 | 0 | 3 |
| **probe** fan offending boundary faces, from primal vertex | 97.13 | 11.72 | 47 | 10 | 27.62 | **722.30** | 153 | **673** | **yes** | 4 |
| **probe** fan offending boundary faces, in-plane (from poly[0]) | 97.15 | 11.72 | 47 | 10 | 27.62 | 18.19 | 83 | **565** | 0 | 3 |

Attribution:

- **Face pyramids (224, all boundary):** created by the boundary dual
  construction. Present without smoothing (217), *halved* by collapse
  (448 → 224 vs exact per-corner). Not a smoothing artefact, not fixable by
  planarizing or re-tessellating the face. Feature-angle is not the lever
  (20/40/90 → 229/224/216).
- **Non-orthogonality (max 97–111°, all internal):** internal dual faces
  around the same feature. Worsened by no-smoothing (111.2), improved by
  smoothing (97.2) but smoothing cannot reach < 70. `triangulate` destroys it
  (174°).
- **Skewness (max 18.3):** present without smoothing (10.8); smoothing
  *raises* the max (10.8 → 18.3) while lowering NO — a measured tail
  regression inside `smooth_dual_mesh` (its keep-best minimises total defect
  *count*, not max severity).
- `planarize` is a no-op on this mesh; `triangulate` is catastrophic.

## 2. Characterising the offending cells (venturi, fresh prod dual)

checkMesh pyramid check uses `face::centre` = **arithmetic vertex mean** for the
pyramid base and `pyramid = (1/3)·areaNormal·(apex − baseCentre)`, with
`apex = mesh.cellCentres()`. The converter's `_detect_defects` uses the
area-weighted `cf`; **on this mesh both give exactly 224**, so the detector count
is correct (see §5).

- **224 failing faces = 224 owner-side, 0 neighbour-side.**
- **208 are boundary faces, 16 internal.**
- **182 failing cells; 182/182 touch a boundary face; 0 interior cells.**
  Every defect lives at the wall.
- Failing-cell x-range: **[-0.379, +0.026]** (domain x ∈ [-1.477, +1.523]),
  concentrated at **x ≈ -0.144** (the throat feature; a secondary cluster near
  x ≈ -0.379). Samples in §2.2.
- Pyramid-cell aspect is **normal** (median 1.38, max 14.9; all-cell max
  27.6) and volumes are normal → **not a sliver/aspect effect**.
- Worst internal non-orthogonal faces (57 faces > 70°, max 97.18) are all
  internal and sit at the **same x ≈ -0.1439** plane, radius ~0.07–0.10.

### 2.1 Mapping back to the primal boundary vertex

Each failing dual cell was mapped to its primal vertex (`cell_vertex` captured
at `_validate`; boundary-vertex incidence from the tet backup).

| statistic (venturi) | failing cells' vertices | all boundary-cell vertices |
|---|---|---|
| boundary-normal spread median | **90.96°** | 6.28° |
| normal spread p90 | **134.35°** | 23.63° |
| normal spread max | 144.86° | 144.86° |
| incident tets (star size) median | 12 | 10 |
| min incident edge median | 3.62e-3 | 7.57e-3 |
| concavity sign, `(v−C)·N < 0` | **151 / 182 concave** | p50 ≈ 0 (neutral) |

So: the failing cells are the duals of primal boundary vertices sitting on
**sharp, predominantly concave, features** — where the incident boundary-face
normals wrap 90–145°. Not tiny stars, not slivers. This is exactly the
mechanism the module docstring predicted for the valve, now measured on the
venturi and quantified.

### 2.2 Sample failing cells (venturi)

`spread` = max angle between the cell's boundary-face normals; `tilt` = angle
between (cellCentre − faceCentre) and the failing face normal (90° ⇒
degenerate/negative pyramid).

| cell | centre (x,y,z) | faces | bnd faces | edge min | edge max | edge ratio | aspect | spread | tilt |
|---|---|---|---|---|---|---|---|---|---|
| 4 | (-0.1438, 0.0283, -0.0730) | 20 | 6 | 1.08e-4 | 5.18e-3 | 48.1 | 1.49 | 117.5° | 83.6° |
| 62 | (-0.1423, 0.0283, 0.0738) | 22 | 6 | 1.55e-4 | 5.27e-3 | 33.9 | 1.33 | 117.6° | 72.0° |
| 89 | (-0.1438, 0.1017, 0.0002) | 21 | 6 | 1.40e-4 | 4.18e-3 | 29.8 | 1.48 | 118.0° | 86.3° |
| 0 | (-0.0091, 0.0409, -0.0974) | 10 | 3 | 3.37e-4 | 4.32e-3 | 12.8 | 2.77 | 98.6° | 88.5° |
| 5 | (-0.1412, 0.0415, -0.0691) | 10 | 4 | 1.84e-4 | 4.07e-3 | 22.1 | 1.84 | 144.9° | 78.4° |
| 7 | (-0.1409, 0.0154, -0.0689) | 10 | 4 | 1.89e-4 | 4.04e-3 | 21.3 | 2.08 | 144.8° | 77.8° |

Dominant boundary patches touched by failing cells: `surface_78` (35 face
occurrences), `surface_49` (18), `surface_46` (18), `surface_76` (18),
`surface_48` (17), `surface_47` (17), `surface_34` (16), `surface_55` (16),
`surface_19` (15), `surface_36` (14), `surface_53` (13), `surface_69` (12),
`surface_84/85` (12 each), then `surface_4/44/21/6/86/87`.

## 3. Healthy-case contrast: the valve is NOT healthy

`valve1` has **nearly the same bounding box** as the venturi
(-1.477..1.523 × -0.098..0.156 × -0.126..0.126) and the same 89 `surface_*`
patches — it is the **same venturi geometry at 8.6× finer resolution**
(824,661 vs 96,196 tets), not an independent healthy part.

| metric | venturi dual (96k tets) | valve dual (825k tets) |
|---|---|---|
| cells | 21,798 | 152,086 |
| pyramids (faces) | 224 (1.03%) | 340 (0.22%) |
| pyramid cells | 182, **100% boundary** | 318, **100% boundary** |
| failing-vertex normal spread p50 / p90 | 91° / 134° | 90° / 117° |
| concavity sign | 151/182 concave | 281/318 concave |
| pyramid-cell aspect median / max | 1.38 / 14.9 | 1.33 / 2.94 |
| NO max / severe>70 | 97.18 / 47 | 95.62 / 175 |
| aspect max / min cell volume | 27.62 / 5.2e-10 | **100.79 / 2.1e-13** |
| skew max | 18.30 | 22.16 |

Interpretation: both fail, for the **same boundary-wrap mechanism**. The finer
valve wraps each cell less (pyramid fraction 4.6× lower, smaller concavity) but
its sliver tets give far worse aspect (100.8) and the same order of NO. So
"What the venturi has that the valve does not" is **not a different defect
type** — it is a coarser resolution of the same sharp, concave throat feature,
which makes the per-cell wrap severe and the pyramid fraction high. There is no
clean healthy complex reference in this pair.

## 4. Ranked root causes

1. **Dual cells wrapping a sharp concave boundary feature (dominant).**
   Boundary vertices at the venturi throat (x ≈ -0.144, secondary x ≈ -0.379)
   have incident boundary normals spanning 91–145° (vs 6° typical) and are
   predominantly concave (151/182). The one-cell-per-boundary-vertex dual is
   non-convex there, its centroid falls outside (tilt 72–88°) one boundary
   polygon, and checkMesh reports an inverted face pyramid. Evidence:
   §1.2 (collapse halves but does not remove; smoothing/planarize cannot fix;
   fanning the face makes it worse), §2, §2.1.
2. **Under-resolved sharp feature ⇒ 97° non-orthogonality on the internal
   dual faces around it.** The primal edge–ring faces there are nearly parallel
   to the cell–cell vector. External to the wall (internal faces, 57 > 70°),
   improved but not cleared by smoothing (111.2 → 97.2). Same feature, so the
   same fix must address both.
3. **Smoother trades non-orthogonality for skew.** Measured: no-smooth →
   smooth changes NO 111.2 → 97.2 but max skew 10.84 → 18.30 and pyramids
   217 → 224. `smooth_dual_mesh` keep-best is on total *count*, blind to max
   severity. This is a real, avoidable part of the residual failure.
4. **No quality acceptance gate in the writer.** `_validate`
   (`tet_poly_dual.py`) checks only topology/closure/positive-volume; it never
   looks at pyramid/NO/skew. The detector already reports the defects
   (`residual_defects = 288`) yet `success=True` and the mesh is written. This
   is why the pipeline "passes" an obviously failing mesh.

## 5. Hypotheses tested and DISPROVED (with numbers)

- **In-process detector is blind to checkMesh's pyramid metric.** Disproved as
  the miss: `_detect_defects` (area-weighted face centre) and checkMesh
  (arithmetic face centre) both give **224** on the venturi and **340** on the
  valve. The detector counts match exactly; the gap is §4.4 (no gate).
- **The collapsed boundary is the cause.** Disproved: collapse *halves*
  pyramids (exact per-corner = 448, collapsed = 224) and improves skew/aspect.
- **Feature-angle tuning fixes it.** Disproved: `boundary_feature_angle`
  20/40/90 → 229/224/216 pyramids (≤3.6 % effect).
- **Re-tessellating the offending boundary face fixes it.** Disproved by
  direct probe: in-plane fan 224 → **565**; fan from the primal vertex
  224 → **673** plus negative volumes and skew 722. (The merge module's
  docstring predicts this: a planar sub-piece keeps the same orientation
  relative to the cell centre.)
- **Sliver effects.** Disproved for the pyramid class: failing cells have
  normal aspect (median 1.33–1.38) and normal volumes; stars are normal-sized.
  (Slivers do dominate the valve's *aspect* metric, but not its pyramids.)
- **`dissolve_tiny_boundary_stars` helps.** Disproved: at 0.3 it is refused by
  its own pyramid non-regression gate (356 vs 217) and the output is unchanged.
- **More smoothing iterations help.** Disproved: `smooth_iters=8` gives the
  same 224/57/7 as the 3-iteration run (smoothing stalls at iteration 3).
- **`planarize` helps.** Disproved: byte-for-byte same numbers as prod.

## 6. Fix candidates (ranked, with code location / expected effect / risk)

> These are for Task P1. Everything here is a proposal; no code was changed.

### C1 — Defect-driven concave-cell merge (highest leverage, already coded)

- **Code:** `src/polyfoammesh/core/poly_cell_merge.py` —
  `find_merge_candidates` / `repair_concave_cells_if_safe` / `merge_cell_groups`.
  Wire it into the poly path (after the BL if a BL is present), keep-best
  against real checkMesh.
- **Why:** it is the only cell-level lever already measured to fix this exact
  defect: on the reference valve it took real-checkMesh wrong-oriented faces
  **340 → 58**, fixing **302/348** concave cells with aspect/NO unchanged when
  applied post-BL (`notes/poly_cell_merge_reasoning.md`). The venturi's 182
  failing cells are all boundary cells; the merge keeps their boundary
  polygons owned by the survivor, so the wall is untouched.
- **Expected:** venturi pyramids 224 → ~40–70; NO may also drop ~10° where the
  merged cell removes the bad internal ring.
- **Risk:** HIGH/known. On `slot1` it turned "Mesh OK" into 3 new highly-skewed
  faces — the in-process gate does not see checkMesh skew. Must be gated by a
  real-checkMesh keep-best loop; never promote blind. It was killed once for
  exactly this reason; P1 must add the real-checkMesh gate the benchmark path
  lacked.

### C2 — Zonal (defect-masked) smoothing of the wrapped boundary cells

- **Code:** `src/polyfoammesh/core/poly_smoother.py` —
  `smooth_dual_mesh` candidate list + `_current_objective` (reuse the existing
  `worst_aspect_thr` zonal machinery: build a vertex mask for cells currently
  failing the pyramid test, add a pyramid/worst-cell penalty term).
- **Why:** the failing cells' tet-centroid vertices are interior dual vertices
  and are movable; only boundary polygon points are pinned. The global
  Laplacian never fixed them because it drags all 100k interior vertices along
  (and nudged pyramids 217 → 224); a *masked* move that only touches the
  offending cells has never been tried.
- **Expected:** can pull a wrapped cell's centre back inside its boundary
  faces; unknown magnitude, but it is the only lever that can fix boundary
  pyramids without moving the surface or changing topology.
- **Risk:** MEDIUM. Movement freedom is limited (boundary points pinned), so it
  may no-op; must keep the existing volume/planarity hard guards and keep-best
  so it can never worsen the written mesh.

### C3 — Refine the input tet mesh at the sharp feature (upstream, GMSH)

- **Code:** the GMSH wrapper / feature high-resolution sizing (not
  `tet_poly_dual`/`poly_smoother`; report to the orchestrator before touching).
- **Why:** the same geometry at 8.6× resolution has a 4.6× lower pyramid
  fraction and ~4× smaller per-cell concavity, and the failing vertices have
  min incident edges ~2× below the population median — the feature is
  under-resolved. Refining the feature edges reduces how far each dual cell
  wraps.
- **Expected:** reduces pyramid frequency, maybe enough to make C1/C2 land;
  alone insufficient (the fine valve still fails at 340 pyramids / aspect 101).
- **Risk:** MEDIUM (cell count and wall time; valve is already 825k tets /
  238 MB; feature refinement can explode the count if unbounded).

### C4 — Real quality gate in the writer (no geometry change; do regardless)

- **Code:** `src/polyfoammesh/core/tet_poly_dual.py` — `_validate` /
  `_run_inner`: treat `residual_defects > 0` (or a real checkMesh run) as a
  hard failure / explicit `success=False` flag rather than writing with
  `success=True`.
- **Expected:** the pipeline can no longer ship a mesh that checkMesh fails;
  makes the P1 keep-best gate meaningful.
- **Risk:** LOW behaviour change; the plan already mandates real checkMesh
  acceptance, so this aligns the code with the plan.

### C5 (secondary) — Max-severity guard in the smoother

- **Code:** `src/polyfoammesh/core/poly_smoother.py` — `smooth_dual_mesh`
  acceptance (`best_obj` / candidate guards): add an L∞ worst-skew (and
  worst-NO) guard analogous to the existing planarity hard guard, so a step
  that lowers total count but raises max skew (measured 10.84 → 18.30) is
  rejected.
- **Expected:** keep the NO gain (111 → 97) while removing the skew tail;
  does not by itself reach PASS.
- **Risk:** LOW/MEDIUM; may reject more steps and stall earlier (leaving NO
  at 111), so gate it on real checkMesh.

## 7. Bottom line

The venturi dual's quality defects are **not** a detector bug, a collapse bug,
a sliver bug, or a face-tessellation bug. They are the expected consequence of
building a one-cell-per-boundary-vertex dual on a **coarse tetrahedral mesh
whose sharp concave throat feature is under-resolved**: the boundary dual cells
wrap the feature and go non-convex (224 inverted boundary pyramids, all at the
wall, 91–145° normal spread, 83 % concave), and the surrounding internal dual
faces get 97° non-orthogonality. Smoothing cannot touch pinned boundary cells;
feature-angle tuning, planarization and face re-tessellation are all measured
non-levers. The two credible levers are the already-coded concave-cell **merge**
(C1, measured 340 → 58 on the valve but needs a real-checkMesh keep-best gate)
and a new **defect-masked smoothing** of the offending cells (C2), with **input
feature refinement** (C3) as the upstream enabler and a **real acceptance gate**
(C4) so failures stop shipping silently.

## 8. P5 design spike — real-checkMesh evaluation of the two boundary-topology fixes

Date: 2026-10-08. Scope: scratch prototypes only (`.superpowers/sdd/2026-10-05-poly-beat-snappy-program/`,
deleted after); **no `src/` or `tests/` change**. Every number below is REAL
OpenFOAM 2512 `checkMesh` (WSL Ubuntu) on a fresh rebuild from the tet backup
with the production kwargs
(`TetPolyDualConverter(case, collapse_smooth_edges=True, boundary_feature_angle=40.0, collapse_volume_tolerance=0.10)`).
Bar to reach: **pyramids ~0, NO < 70, skew < 4, neg 0**.

### 8.1 Prototypes evaluated

- **A — merge + point relaxation.** `poly_cell_merge.repair_concave_cells_keepbest`
  (the production max-severity merge, 224→110 venturi / 340→46 valve) followed by
  a masked relaxation of the failing cells' interior (tet-centroid) vertices,
  iterated for up to 10 steps per round and 4 rounds. Three relaxation modes
  prototyped: `inward` (pull toward the cell's boundary-face mean), `lap`
  (masked `poly_smoother._laplacian_relax`), and `plane` (push each failing
  cell's interior vertices opposite the outward normal of each face whose exact
  checkMesh pyramid test fails, magnitude `|h|`). Acceptance was keep-best:
  accept a step only if the exact pyramid count falls (or is unchanged with a
  lower continuous inversion penalty) and max NO / max skew do not rise.
  Also ran the **existing production smoother** (`smooth_dual_mesh`, 8 iters,
  with and without the C5 `guard_severity` gate) as the post-merge relaxation.
- **B — compact-face dual at the crease (topology proxy).** Group the failing
  *boundary* dual cells into connected components along their shared internal
  faces (optionally pulling in each group's interior neighbours) and merge each
  group into ONE compact cell — i.e. the topology-equivalent of building one
  poly cell per group of primal boundary vertices. Emitted and checked real.

### 8.2 Measured numbers (real checkMesh)

**Venturi** (fresh prod dual-only; baseline §1.2 reproduced exactly):

| variant | cells | pyramids | NO max | NO avg | severe>70 | NO err | skew max | highly skew | neg | failed |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline prod dual | 21,798 | **224** | 97.1771 | 11.7154 | 47 | 10 | 18.3017 | 27 | 0 | 3 |
| **A** merge (keepbest) | 21,654 | **109** | 97.1771 | 12.138 | 53 | 10 | 18.3017 | 40 | 0 | 3 |
| **A** merge + relax (all 3 modes) | 21,654 | **109** | 97.1771 | – | 53 | 10 | 18.3017 | 40 | 0 | 3 |
| **A** merge + global smoother (no guard) | 21,660 | 113 | 96.4151 | 11.6944 | 42 | 10 | **33.5641** | 40 | 0 | 3 |
| **A** merge + global smoother (C5 guard) | 21,660 | **110** | 97.1258 | 12.0929 | 52 | 10 | 18.0918 | 39 | 0 | 3 |
| **B** group merge + interior pull | 20,474 | **540** | 133.637 | 14.1468 | 255 | 35 | **426.057** | 532 | 0 | 3 |
| **B** group merge, boundary only | 21,707 | **335** | 159.056 | 11.9769 | 63 | 59 | **392.562** | 188 | 0 | 3 |

**Valve** (second hard case, fresh prod dual-only):

| variant | cells | pyramids | NO max | NO avg | severe>70 | NO err | skew max | highly skew | neg | failed |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline prod dual | 152,086 | **340** | 95.6151 | 10.5694 | 175 | 10 | 22.1645 | 28 | 0 | 3 |
| **A** merge (keepbest) | 151,763 | **46** | 94.2995 | 10.7029 | 153 | 1 | 21.1737 | 56 | 0 | 3 |
| **A** merge + relax (all 3 modes) | 151,763 | **46** | 94.2995 | – | 153 | 1 | 21.1737 | 56 | 0 | 3 |
| **B** group merge + interior pull | 149,008 | **1,301** | 167.902 | 11.6056 | 633 | 463 | **1,428.01** | 1,184 | 0 | 3 |
| **B** group merge, boundary only | 151,943 | **710** | 149.665 | 10.7679 | 403 | 289 | **1,741.22** | 316 | 0 | 3 |

**Tube (103202) — non-regression:** baseline dual `Mesh OK` (NO 50.8101,
skew 1.63313); A merge refused at round 0 (`concave 0`), output byte-identical,
still `Mesh OK`. Candidate A cannot touch a healthy mesh.

### 8.3 Why the relaxation is a measured no-op (not a tuning failure)

Moving the failing boundary cells' interior vertices cannot fix the boundary
pyramids. Diagnostics on the merged venturi dual:

- The dual cell's OpenFOAM centre is dominated by its **pinned boundary
  polygons**; moving interior tet-centroid vertices changes the centre too
  little to flip a boundary-face pyramid. Every small step in all three modes
  left the pyramid count and the continuous inversion penalty exactly
  unchanged → keep-best rejected it.
- An **unguarded** plane push at `relax=1.0` did lower the continuous inversion
  penalty by only **13 %** (4.93e-7 → 4.30e-7) while the pyramid **count rose
  110 → 177** and max NO exploded **97.2 → 169.1** (moved interior vertices are
  shared with interior cells, which then invert). Larger steps were worse
  (`relax=3/10/50` → 283/637/1495 pyramids, NO 178/177/176, negative volumes).
- The production global smoother post-merge cannot reach the bar either: best
  NO 96.42 at skew 33.56 (no guard), or NO 97.13 / skew 18.09 (C5 guard).

### 8.4 The measured wall

- **Candidate A reaches** pyramids **109 (venturi) / 46 (valve)** — a 51 % / 86 %
  reduction — but the tail is a hard wall: **NO max 94.3–97.2°** and
  **skew max 18.1–21.2°**, unchanged by merge and by every relaxation tried.
  The single worst internal non-orthogonal face (97.18° at x ≈ -0.144) is not
  removed by the merge, so NO cannot move below 70.
- **Candidate B is strictly worse** on every metric at both resolutions
  (pyramids 335–1301, NO 150–168°, skew 393–1741). Removing the internal faces
  across the crease removes exactly the faces that carried acceptable
  angles, and the surviving single large cell has worse ones. Its topology
  proxy is a faithful test of "one cell per group of primal boundary vertices";
  it fails hard.
- **Neither candidate can reach pyramids ~0, NO < 70, skew < 4.** The residual
  NO is an *internal-face* defect at the under-resolved concave feature, not a
  boundary-cell convexity defect, and neither boundary-topology fix touches it.

### 8.5 Cost

- Fresh dual-only build (production): venturi 9.1 s / 21,798 cells; valve
  61.4 s / 152,086 cells.
- Real `checkMesh`: venturi 3.3 s; valve 6.9 s.
- Merge keepbest: **3.3 s** on venturi (in-process; negligible vs build). One
  relaxation round ~1.5 s. B's group merge is comparable but produces garbage.

### 8.6 Recommendation (for the P5-fix task)

**Implement neither A's relaxation nor B.** Keep Candidate A exactly as it
already is — the opt-in, max-severity-gated merge at
`src/polyfoammesh/core/tet_poly_dual.py` (`merge_concave_cells` /
`merge_rounds`, constructor lines 189–190 / 360–380 / 436–437; application in
`_run_inner` lines 703–738) backed by
`src/polyfoammesh/core/poly_cell_merge.py:275` `repair_concave_cells_keepbest`
— because it is a real, gated improvement, but it is **not a pass** and must
stay opt-in. Do **not** add the point relaxation (measured no-op on the
pyramid criterion, regression on the tail); do **not** build Candidate B (the
topology proxy is catastrophic, and stitching a local compact fan into the
regular dual would require new construction in `_build_dual`
(`tet_poly_dual.py:1389`) or the mixed path (`_read_primal_mixed:975`,
`_run_mixed:1167`) for a result measured to be worse).

The P5-fix must attack the **root cause**: the under-resolved concave feature
whose internal dual faces carry 94–97° non-orthogonality. That is the
already-ranked **C3 upstream feature refinement** (GMSH feature sizing; the
8.6× finer valve has the same wall) plus the **C4 real quality gate** in
`tet_poly_dual._validate` (`tet_poly_dual.py:2242`) so failures stop shipping
silently. A maximum-severity guard in `smooth_dual_mesh`
(`poly_smoother.py:468`, the already-added `guard_severity`) is worth keeping
as a low-risk tail protection, but it cannot reach the bar on its own.
