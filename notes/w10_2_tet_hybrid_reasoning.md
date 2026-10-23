# W10.2 - hybrid tet source: reasoning and predictions (2026-10-22)

Predecessor: `notes/w10_2_tet_hybrid_diagnosis.md`. Predictions written
before the contract/policy implementation and the surface/patch changes.

## Claim

The existing STEP hybrid (GMSH surface + tagged physical groups -> fTetWild
volume) is the correct composition. W10.2 should not merge meshes: it should
give that composition one `TetSource` contract, a deterministic opt-in
`auto` policy with fallback/keep-best, and carry the GMSH surface strengths
that currently stop at `generate_surface_stl`.

## Predictions

1. `TetSourceResult.to_mapping()` round-trips requested/effective source,
   settings, names, stats, stage times, failure reason and fingerprint.
2. `auto` on STEP with pytetwild importable selects `ftetwild` (GMSH surface
   + fTetWild volume); on STL it selects `ftetwild` without a GMSH surface;
   when pytetwild is absent it selects `gmsh`; same inputs -> same plan.
3. A primary failure in `auto` runs the other source once, records both
   attempts and the fallback reason; a success with an empty/invalid MSH is
   a failure, not a "kept" result.
4. Keep-best score is (success, patch names present, ambiguity low, time);
   a failed later attempt never replaces a successful earlier one.
5. Surface refinement requested by the GUI reaches GMSH's 2D surface via
   the existing zone fields and lowers the local target size; the resulting
   fTetWild surface carries the same tagged NPZ. If GMSH cannot apply it,
   the result says so and keeps the unrefined surface.
6. Patch mapping: normal compatibility + second-nearest margin reduces
   thin-gap swaps compared with pure nearest centroid; the old centroid
   result stays the fallback when normals are missing. Stats include
   normal-filtered count and ambiguous fraction.
7. The cache fingerprint includes the selected/requested source and the
   transported surface settings; changing source or refinement invalidates
   it.
8. `auto` is opt-in; explicit `gmsh`/`ftetwild` and the existing GUI default
   do not change. The old paths remain available.
9. The union of two tet meshes is rejected by contract/policy tests: no
   conforming interface is created, so no merged mesh is written.
10. R1 downstream stays on the same .msh contract; the source field appears
    in the W10.1 BL report/log. No BL double-application.
11. fTetWild absence or `subdivide_tets` failure falls back to GMSH with a
    visible source/provenance; no crash.
12. Full fast suite and ruff stay green; real GMSH/fTetWild A/B is reported
    only where actually run.

## Falsification signals

- Auto chooses a source but the result does not name it, or a fallback is
  invisible.
- A failed/empty attempt is selected as best.
- A refinement request changes the cache key but not the surface.
- Patch filtering silently drops a patch or increases swaps.
- `auto` changes the explicit path/default.
- A merged mesh is written without a conforming-interface proof.

## Measurement plan

- Unit: preflight/selection, deterministic auto, fallback, keep-best,
  result mapping, fingerprint, union rejection, patch thin-gap synthetic,
  refinement option forwarding, cache invalidation, GUI offscreen label.
- Real: existing benchmark JSONs for the GMSH/fTetWild comparison; a small
  STEP surface refinement probe if the GMSH runtime is available; R1 is
  reused from W10.1 for the downstream contract. Full corpus auto A/B is
  not claimed unless run.
- Suite + ruff.

## Closing evidence

- `tests/test_tet_source.py`: 12 passed - deterministic STEP/STL choices,
  fTetWild-absent GMSH, fallback, empty-primary rejection, both-failure,
  provenance/settings/fingerprint, explicit source, CLI forwarding, patch
  normal filter, refinement stats, union rejection, cache keys.
- Surface probe: 210 -> 748 triangles; inside median edge 0.476 -> 0.250 mm;
  tagged 3 patches retained. Outside also refined to 0.245 mm (field
  propagation, declared).
- Cache: `ftw_eps_frac`/`ftw_simplify`/`ftw_adaptive` are now canonical;
  version bumped to 2. This fixes a measured stale-mesh reuse.
- Full fast suite: **1659 passed, 2 skipped, 20 deselected, 1 xfailed**;
  `ruff check src/ tests/` green.

## Still open / declared

- No full corpus auto A/B and no both-source R1 volume run in this pass.
  `auto_tet` is opt-in; explicit paths remain unchanged.
- Feature angle is accepted for provenance but not applied to the fTetWild
  volume: GMSH CAD entities already encode feature edges, and fTetWild has
  no equivalent volume option. It is reported `feature_angle_applied=False`.
- The mesh union is rejected by explicit guard; no conforming interface was
  built.
- W10.1 BL R1 remains the downstream truth on the fTetWild source; the GMSH
  source was not re-run through dual+BL in this pass.
