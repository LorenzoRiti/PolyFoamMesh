# Real boundary layers on the poly path (2026-10-06)

References: commits `d654cdb`, `8f4b0e4`, `2ea4191`. Verification on the
user's real case (`Part2.stp`) and on the BL suite (81 tests) + cylinder.

## Symptom

On Part2 the log said "14 layers, local 1..1" with total thickness
0.00049 m: the BL was **accepted as a success** but it was a 1-layer
shell, invisible at part scale and useless for the y+ target. In other
cases the engine **compressed** 14 layers into 1.17 mm (first layer 0.03 mm,
y+ wrong by 20x) without saying so.

## Root causes (all measured)

1. **The consistency fixpoint is a MIN over the whole connected wall.**
   A single vertex with a reduced budget (`max_h` small) flattens to 1
   layer all faces reachable through shared vertices. On Part2:
   `support_d` p50 = 2.35 mm, required stack 43 mm → 45% of vertices with
   a 1-layer budget at clamp 0.35 → median 1 over 13,081 faces.
   The per-vertex budget is `max_h = angle_fade * min(clamp*d,
   0.5*edge_cap)` (bl_poly `_build`, ~line 2109).
2. **Validation was blind to the effective layers**: no check on
   median/coverage → `local 1..1` passed (volume preserved, checkMesh
   clean).
3. **`drop_bnd` with wrong units**: keys = local face ids
   (`fi - n_int`), test = POSITION in the filtered selection (`bi in
   drop_bnd`). With `min_base_edge_fraction=0.9` (the "robust BL"
   default) the positions diverge from the ids and the test flagged faces
   at random. The valve golden (75,360 prisms) encoded the bug.
4. **Newell/centroid volume measurement** approximated on non-planar faces
   (dual n-gons): 1.16e-3 of "error" on Part2 that was instead the
   shell actually yielded under the faces excluded by the sliver filter.

## Fix

- **Truth in the result** (`PolyBoundaryLayerResult`): layer histogram per
  face (`layer_hist`), weighted median (`median_layers`), coverage %,
  `stats["first_layer_effective_m"]` and `first_layer_ratio`. Honest log:
  `N prism cells (K layers requested, median ..., coverage ...%, first
  layer X (requested Y))`.
- **Utility guard**: a valid but flattened build (median 1 with
  n>=2 requested) is REJECTED with an explicit warning; the fallback
  path continues.
- **Mesh-aware cap** (`_supported_layers`): with `terminate_by_budget`,
  `n_layers` is limited to the maximum k that at least 90% of vertices
  support at full height (`capped N -> k` in the log). On Part2: 14 → 1 with
  full h1 (exact y+), 14 → 2 with h1/2.
- **Binary semantics in the standard path** (`zero_concave=True` when
  `terminate_by_budget`): face = full stack (compressed only where
  `max_h` bites) or excluded; no fixpoint percolation.
- **Exact volumes** (`_exact_cell_volumes`, fan triangulation
  from the face centroid) for positivity/conservation/min-volume/ratio.
  Conservation tolerance 5e-3: documents the shell yielded by-design
  under the excluded faces (Part2 worst: 1.16e-3); a real hole fails
  the closure test at 1e-8 first.
- **`drop_bnd` translated into positions** at the entry of `_build`.
- **Effective first layer reported**; warning if < 50% of the physical
  target ("y+ off by the same factor").

## Verification (Part2.stp, dual mesh 31,775 cells, 14,746 boundary faces)

| Attempt | Outcome | Prisms | Layers | First layer | checkMesh |
|---|---|---|---|---|---|
| h1=0.000728 (full) | capped 14 → 1 | 13,081 | 1 of 100% | 0.000728 (ratio 1.00) | PASS (44,867 cells, NO 57.74, skew 1.649, neg 0) |
| h1=0.000364 (h1/2) | capped 14 → 2 | 26,162 | 2 of 100% | 0.000364 (ratio 1.00) | PASS (57,948 cells, NO 58.52, skew 1.654, neg 0) |

- Before: fake 1-layer BL accepted, or BL rejected altogether.
- Cylinder (collapsed boundary test): unchanged, 3 layers on every face.
- Valve production (golden): 76,052 prisms (was 75,360 with the units bug),
  scale 0.6 and 4,008 exclusions unchanged.
- Suite: 58 engine+cylinder tests green, 81 BL tests green, fast suite
  1283 passed.

## Architectural limit (for the next program)

The stack cannot exceed the depth of the first dual cell
(`support_d`): the BL reuses only the first layer of cells, it does not
consume interior ones (unlike snappy). On Part2 `support_d` p50 = 2.35 mm
→ at most ~1.17 mm of stack at clamp 0.5. For thicker BLs you need:
- larger surface cells (the cap warning suggests it), or
- a BL that consumes more layers of interior cells (big change).

## Automatic concave closure (commit 2ea4191)

The checkbox "Advanced closure for concave geometries (experimental)" was
removed: `local_termination="decoupled_vertex"` (H4) is now an AUTOMATIC
fallback of the worker chain:

```
1. robust standard (binary + cap)         ← now handles concave cases on its own
2. concave closure (decoupled_vertex)     ← only if step 1 fails
3. halved first layer (0.5, then 0.25)    ← last resort
```

Reason: the LT ran first and degraded the cases the new standard
handles; it remains the only path that closes the layer on concave
junctions (groove1/slot1, production valve — see `notes/fase3_reasoning.md`).
The memory guard (8M faces / 1.5M cells) now protects the closure retry.
The legacy flag `concaveClosure` remains only for the post-BL merge repair
(off by default; it was measured in regression on groove1).

## Follow-up before release

- Re-validate the 9/10 benchmark vs snappy with the new BL semantics (the
  score had been measured with the old one: concave cases passed
  even with the failed BL).
- Re-measure end-to-end groove1/slot1 with the new chain (the automatic
  fallback is covered by unit tests, not by a real measurement).
- Viewer: prism cell coloring (BL visibility) — today the viewer
  colors by patch.

## Full BLs - complete-coverage policy (2026-10-07)

Measurement on the user's real run (gmsh_direct_20261007_091301, 279,894
cells, n=6, h1=9 mm, robust OFF, only wall patch surface_2): the wall
had BL coverage **95.0%** - 5,883 faces without a prism ("BL extruded only
partially"). Cause: the binary semantics (zero_concave) discarded the faces
with ngle_fade<0.5 or max_h<=0, and the fixpoint (non-binary path)
zeroed the faces on vertices without budget.

Fix in l_poly._build:
- the standard loop **always** uses the binary semantics (before only in
  budget mode): the fixpoint is a MIN over the whole connected wall and
  any vertex with a reduced budget floods it to 1 layer;
- **only** the faces with a real input defect are discarded (drop_bnd);
- concave / zero-budget vertices keep the face with a **compressed**
  stack (thin but present) and a minimum-height floor
  (1e-3 * h1) avoids degenerate prisms.

Verification (Part2 dual, same config): surface_2 coverage **100.0%**,
86,154 prisms, 6 layers over 14,359 faces, checkMesh PASS (NOmax 70.15,
skew 1.741, neg 0). Trade-off declared in the log: first layer compressed
to 2% of requested (the near-wall mesh supports ~1.2 mm of stack;
for y+ 30 you need a thicker surface mesh). BL suite 58 green.
