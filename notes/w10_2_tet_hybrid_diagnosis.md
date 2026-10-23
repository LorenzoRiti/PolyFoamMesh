# W10.2 - hybrid tet source: diagnosis (2026-10-22)

Scope: what the current GMSH / fTetWild paths actually share, what each
does better, which GMSH strengths are dropped before the fTetWild volume,
and what a single `TetSource` contract must add without creating a second
pipeline. Inputs: `ftetwild_wrapper.py`, `gmsh_wrapper.py`,
`openfoam_runner.py` (`GmshVolumeWorker` / `FtetWildVolumeWorker`),
`gui/main_window.py`, `gui/params_panel.py`, W1, M2, the benchmark JSONs
and `REGRESSION_CASES.md`.

## 1. Current state

### 1.1 The two production paths

| Path | Surface stage | Volume stage | Output |
|---|---|---|---|
| `gmsh_direct_poly` | GMSH OCC CAD, physical groups, adaptive/curvature/gap sizing, optional refinement zones, feature-aware CAD entities | GMSH 3D Delaunay/HXT | MSH 2.2 with physical groups, exact CAD surface |
| `ftetwild_poly` (STEP) | GMSH surface via `generate_surface_stl`, then `export_tagged_surface_npz` | fTetWild on the surface STL | patched MSH 2.2, patch ids recovered by nearest centroid |
| `ftetwild_poly` (STL) | **skipped** (GMSH cannot re-mesh an imported discrete surface; verified empty output) | fTetWild on the input STL | one/few names from ASCII solids; binary STL -> one patch |

So the "hybrid" is already real for STEP. It is not a single contract:
`GmshVolumeWorker` and `FtetWildVolumeWorker` have separate constructors,
return dicts with different keys, and the GUI branches on
`_current_mesher_type` to decide the label, retry and cache.

### 1.2 Measured strengths (existing evidence)

- fTetWild reference: 2.33M tets vs GMSH 4.47M; AMIPS energy 12 vs 140k;
  mean surface deviation 0.06 mm with simplify off (W1/M2). Venturi ~16x
  faster. fTetWild is the robust volume engine on dirty/complex surfaces.
- GMSH: exact CAD entities, physical groups, refinement zones, curvature /
  gap / passage sizing, feature angle, Delaunay/HXT. Its dual boundary is
  the input surface (not an envelope).
- R1 downstream after W10.1: the quality policy gives 0 pyramids / 1 skew /
  Failed 1 at 99.47% coverage; h1 remains 0.113x of the request (structural
  support cap). This is measured on the fTetWild source.

### 1.3 Measured weaknesses / losses

- fTetWild has no physical groups. `assign_patches` maps every output
  boundary face to the nearest source triangle **centroid** only. It reports
  mean/max assignment distance but not normal disagreement, second-nearest
  ambiguity or per-patch area. On a thin gap the nearest centroid can belong
  to the opposite sheet.
- fTetWild output is an envelope, not the exact CAD surface. The wrapper
  snaps boundary vertices back onto the original surface only when
  `epsilon_abs` is set; otherwise the surface stays inside the envelope.
- GMSH strengths lost on the fTetWild path:
  - `generate_surface_stl` accepts only detail/user_lc/scale; it does **not**
    accept refinement zones, `surface_resolution` or feature angle;
  - `run_volume` does not forward any of those;
  - the GUI explicitly neutralises refinement zones, adaptive, min cell,
    max-cells target, surface resolution, feature angle and BL for the
    fTetWild cache key, because they never reached its volume.
- Cache: `_gmsh_mesh_cache_inputs` already includes the source (`mesher`),
  geometry stat, ftet eps/simplify/adaptive and scale, so the key is not
  source-blind. It neutralised the GMSH-only fields for fTetWild, which was
  correct only while they were not transported. A second defect was found
  in `mesh_cache.CANONICAL_KEYS`: the GUI passed `ftw_eps_frac`,
  `ftw_simplify` and `ftw_adaptive`, but the canonical list ignored them, so
  changing those settings reused a stale `.msh`. W10.2 adds them and bumps
  the cache version.
- Provenance: the result dict has no `source`/`requested_source`/fallback
  field; the GUI infers it from `_current_mesher_type`. A fallback would be
  invisible.

### 1.4 Why a naive union is rejected

fTetWild and GMSH produce two independent volume meshes. Merging them
would create a non-conforming interface (different surface triangulations
and volume vertices) unless a conforming interface is explicitly built and
proved. No such proof exists; a union is therefore not a valid mesh and is
out of scope. The only valid composition is at the **stage interface**:
GMSH surface/tags -> fTetWild volume (already present), or GMSH surface
with refinement -> fTetWild volume (missing).

## 2. Missing single contract

A `TetSource` result should carry at least:

- `requested_source` (`gmsh`, `ftetwild`, `auto`) and `effective_source`;
- primary/fallback reason and every attempt;
- mesh path, patch names, patch map/assignment stats;
- effective settings (detail, max/min cell, surface resolution, refinement
  zones, feature angle, ftet envelope/simplify/adaptive, scale);
- stage times and counts (nodes/tets/faces/RAM if available);
- failure reason and gap classification;
- cache fingerprint and geometry stat.

Today the two worker dicts do not share these keys, so a policy cannot
compare attempts or keep-best without guessing.

## 3. Candidate strategies

### 3.1 Portfolio / auto (chosen)

Preflight: geometry suffix, fTetWild availability, bbox, detail, patch
count, refinement-zone request, thin-feature risk and surface watertightness
where available. Pick a deterministic primary; run it; gate on a non-empty
MSH with valid patch names and a tet count; on failure run the other source
once; keep-best by (success, patch coverage/ambiguity, time/RAM). `auto`
is opt-in; explicit `gmsh` / `ftetwild` stay unchanged. A double run is
allowed only for `auto` and only when the primary fails (default), with an
optional measured both-run mode.

### 3.2 Carry GMSH strengths into the hybrid (chosen)

Extend `generate_surface_stl` with `refinement_zones`, `surface_resolution`
and a declared feature-angle argument; pass them from `run_volume` and the
fTetWild worker. Use the existing `_prepare_refinement_zones` /
`_add_refinement_zone_fields` / `_combine_zone_fields_with_bg` /
`_lower_length_min_for_zones` machinery on the 2D surface. Physical groups
are already carried by the tagged NPZ. Feature angle is a CAD/STL surface
property: exact CAD entities already encode it; for fTetWild volume it has
no direct GMSH option, so it is declared as a surface/tag input, not a
volume parameter.

### 3.2.1 Measured surface-refinement probe (2026-10-23)

On the small STEP cylinder fixture, `generate_surface_stl` was run at
`detail=coarse` with and without a box zone covering half the part. The
zone target was 0.119 mm; the existing clamp/4x guard raised it to 0.250 mm.

| Surface | Triangles | median edge inside | median edge outside |
|---|---:|---:|---:|
| no zone | 210 | 0.476 mm | 0.478 mm |
| zone | 748 | **0.250 mm** | 0.245 mm |

The zone clearly refined the surface (3.6x triangles; inside edge 1.9x
smaller). The outside edge also became finer because GMSH's characteristic
length floor/field propagates; that is reported honestly rather than sold
as strictly local. The tagged NPZ retained the 3 patch names in both runs.

### 3.3 Patch fidelity (chosen if measured ambiguous)

Improve `assign_patches` with a normal-consistency filter: query the k
nearest source triangles, reject normals with dot < threshold, choose the
nearest compatible candidate, and report normal-filter count, second-best
margin and ambiguous fraction. Keep nearest centroid as fallback. Test on
multi-patch and thin-gap synthetic surfaces.

### 3.4 Mesh union (rejected)

No conforming interface proof; two overlapping tet volumes are not a mesh.
Rejected with this evidence, not silently.

## 4. Design and reuse

- New `core/tet_source.py`: `TetSourceKind`, `TetSourceSettings`,
  `TetSourcePreflight`, `TetSourcePlan`, `TetSourceResult`,
  `preflight_tet_source`, `choose_tet_source`, `run_tet_source`.
  `run_tet_source` accepts injectable runners for tests and real runners for
  `gmsh_wrapper.generate_volume_mesh` / `ftetwild_wrapper.run_volume`.
- Extend `gmsh_wrapper.generate_surface_stl` / `ftetwild_wrapper.run_volume`
  and `FtetWildVolumeWorker` with refinement/surface/feature arguments.
- Extend `ftetwild_wrapper.assign_patches` with normal filtering + stats;
  no second patch language.
- Cache: add `requested_source`/`effective_source` and the transported
  settings to `TetSourceResult` and the GUI key; keep the existing
  geometry/source/settings fingerprint.
- GUI: label the existing path `GMSH surface + fTetWild volume`; `Auto`
  remains opt-in. If auto is wired, the primary/fallback is logged and the
  result source is shown; no default change.
- Downstream: the same `.msh` contract feeds dual + BL; W10.1 source field
  must be in the report. R1 and a W10.1 sample are re-run on both sources
  where the source exists.

## 5. Load estimate and risks

Files: new `tet_source.py` (~250 lines), `ftetwild_wrapper.py`,
`gmsh_wrapper.py`, `openfoam_runner.py`, `main_window.py`, `mesh_config.py`,
tests (contract/policy/patch/refinement/cache/GUI), docs. ~10-16 files,
~600-1,000 net lines.

Risks:

- auto primary wrong -> keep-best/fallback and transparent log; no default
  change until corpus A/B.
- surface refinement may change the fTetWild surface and run time; gate the
  cell-size inside/outside zones and deviation; keep off by default.
- normal-filtered patch mapping may drop valid faces near sharp features;
  fall back to nearest and report ambiguity.
- fTetWild absent/`subdivide_tets` failure -> GMSH fallback, logged, no
  crash.
- union is explicitly rejected; do not write a non-conforming mesh.

## 6. What is measured now vs still open

Measured now: the existing hybrid and its strengths; R1 downstream; fTetWild
availability (pytetwild/gmsh/pyvista installed); current patch and cache
behaviour. The corpus auto A/B and both-source R1 sample need the new
policy and a real run; they are not claimed in this diagnosis.
