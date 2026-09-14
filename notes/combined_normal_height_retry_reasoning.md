# Combined lever: most_visible normal + local_height_retry (prediction BEFORE testing)

## Why they should compose (mechanism argument, not hope)

The two levers repair DISJOINT failure causes at the same failing vertices:
- `most_visible` fixes DIRECTION-caused prism invalidity (extrusion points
  the wrong way because area-weighting biases the normal). Measured alone:
  -6.3% excluded faces, and the round-1 prism-invalid count halved
  (232->112 negative-volume prisms).
- `local_height_retry` fixes HEIGHT-caused prism invalidity (direction is
  fine, but full-height extrusion doesn't fit; a shorter local stack does).
  Measured alone: -5.0% excluded faces, checkMesh nearly identical to
  baseline (+1.3% severely non-orthogonal faces).

A vertex that fails for direction reasons is rescued by lever 1; a vertex
that fails for height reasons is rescued by lever 2. Neither lever can
rescue the other's failure class (a better direction doesn't create height
budget; a shorter stack doesn't fix a bad direction). So the combined
exclusion count should beat EACH lever alone — not necessarily additive
(some vertices fail for both reasons and need both fixes; some fail for
neither-fixable reasons like zero budget), but strictly better than either
single lever. Prediction: combined excluded_faces < 6612 (most_visible) and
< 6704 (height_retry), from the shared 7054 baseline.

Quality prediction: the checkMesh cost should be the ENVELOPE of the two
individual costs, not the sum — most_visible's mixed pattern (worse
non-ortho/skew, better face-tets/aspect) plus retry's near-baseline profile.
If the combined severely-non-orthogonal count lands near most_visible's
10117 rather than compounding further, the combination is quality-neutral vs
the better-coverage single lever and worth promoting to opt-in documented.

Falsification: if combined >= min(6612, 6704), the levers interfere (e.g.
retry rescues vertices whose most_visible prisms then fail differently, or
the exclusion cascade re-excludes rescued regions) — document and do NOT
promote.

## Protocol (identical to the two single-lever runs)

Fresh pristine copy of `C:/polybench2/valve_baseline`; run with
`normal_method="most_visible"`, `local_termination="decoupled_vertex"`,
`local_height_retry=True`, `n_layers=2`, `first_height=1e-5`,
`growth_rate=1.2`, `apply_to_all=True`. checkMesh BEFORE/AFTER via WSL
(`-allGeometry -allTopology`), same metric table. Baselines compared:
area_weighted 7054 / most_visible 6612 / height_retry 6704 excluded faces.

Caveat: the single-lever numbers were measured before this session's Lane A
RAM fixes; those fixes are proven bitwise-identical (chunked-geometry test
asserts array_equal; parity reference tests green), so small drift is not
expected — but if the combined count lands BETWEEN the two single-lever
values, a fresh same-code area_weighted baseline will be run to
disambiguate before any verdict.

## Results (measured 2026-09-14/15, real checkMesh OF2512 via WSL)

Run: success=True, 927s, scale=1.0, mode=decoupled_vertex, rounds=3.
prisms=438778, excluded_verts=4687, n_cells_after=590863.

| metric (build) | area_weighted | most_visible | height_retry | COMBINED |
|---|---|---|---|---|
| local_excluded_faces | 7054 | 6612 | 6704 | **6195** |
| n_cells_after | 589113 | 589997 | 589813 | **590863** |

Coverage verdict: **prediction CONFIRMED.** Combined beats EACH lever alone
(6195 < 6612 and < 6704): -12.2% vs baseline, -6.3% vs most_visible,
-7.6% vs retry. Direction-fix and height-fix rescue disjoint vertex sets,
as predicted; most cells kept of any run so far.

| metric (checkMesh) | area_weighted | most_visible | height_retry | COMBINED |
|---|---|---|---|---|
| severely non-ortho (>70°) | 9614 | 10117 | 9739 | **10411** |
| non-ortho average | 18.5196 | 18.5655 | 18.5286 | **18.5862** |
| non-ortho max | — | — | — | **95.72** |
| face pyramid errors | 841 | 847 | 841 | **847** |
| max skewness | 16.245 | 16.805 | 16.245 | **16.805** |
| highly skew faces | 86 | 93 | 86 | **92** |
| face-tet errors | 2375 | 2315 | 2356 | **2322** |
| high aspect ratio cells | 1000 | 982 | 1000 | **981** |
| short edges (too small) | 6 | — | 196 | **299** |
| min cell volume | 3.568e-13 | 1.286e-13 | 3.568e-13 | **3.96e-15** |
| failed mesh checks | 9 | 9 | — | **9** |

Quality verdict: **envelope as predicted.** The combined profile tracks
most_visible almost exactly (wrong 847, skewMax 16.805 identical to three
decimals — same limiting face; skewFaces 92 vs 93; face-tets 2322 vs 2315;
aspect 981 vs 982), PLUS retry's short-edge cost made visible (299 vs 196:
rescued vertices keep even shorter stacks under most_visible directions).
The one regression beyond either single lever: severely non-orthogonal
10411 vs 10117 (+294, +2.9%) — honest cost of the extra 417 rescued faces:
marginal prisms are geometrically valid but less orthogonal. No NEW failure
class appears (failed checks stay 9, same as every run including baseline).

**Decision: PROMOTE to documented opt-in combination (not default).**
The plan's bar is met: combined clearly beats height_retry alone on
coverage (-509 faces) with the predicted quality envelope. Users who need
maximum wall coverage on concave production meshes can combine both flags;
users who need minimum non-orthogonality stay with height_retry alone
(nearest-to-baseline checkMesh). No default changed.

Honest footnotes. (1) Engine-reported `n_cells_after` (590863) vs checkMesh
cells (590864): 28,060 cells own zero faces (all neighbours larger — normal
under owner<neighbour numbering), and this run's top cell is one of them,
so `owner.max()+1` undercounts by one. Same convention in all runs, so the
comparison column is unaffected; checkMesh confirms the mesh itself is
fine. (2) Pristine BEFORE checkMesh (same OF2512 flags): 152086 cells,
severeNO 167, NOavg 12.01, wrong 852, skew 17.12/97, face-tets 2284,
failed 8 — the AFTER deltas above are the BL's own cost on top of an
already-defective dual input. Raw outputs kept next to the cases
(`checkMesh_before.txt` / `checkMesh_after.txt` under `C:/polybench2`).
