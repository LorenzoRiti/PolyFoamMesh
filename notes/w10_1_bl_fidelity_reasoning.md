# W10.1 - BL fidelity: reasoning and predictions (2026-10-22)

Predecessor: `notes/w10_1_bl_fidelity_diagnosis.md`.  Written before the
final suite; the R1 A/B is a real OpenFOAM 2512 run, not a simulation.

## Claim

The BL failure is not a missing retry or a bad clamp factor.  On the R1
dual, **every selected wall vertex is below the requested first layer**:
the median local budget is 0.059x the 5-layer stack, the maximum is 0.164x,
and 91.9% of vertices are bound by the distance to the first interior dual
vertex.  Compressing 5 layers into that budget is the normal
`zero_concave` path, not a fallback; it is why h1 is 0.113x and why the
last prism cuts through the wall-adjacent core cell (the 39 checkMesh
pyramids).

The useful structural question is therefore: can a different *attempt*
remove the pyramids without losing coverage?  Measurement says yes:
`decoupled_vertex` with `pyr_tolerance=0` excludes 187 concave-rim faces
(0.53%), keeps 99.47% coverage, and after clearing the boundary pyramids
leaves **0 pyramids, 1 skew, 1 failed check**.  h1 stays at the structural
0.113x; that cap is declared, not hidden.

## Predictions (before implementation)

1. A generic `height_cap_stats` will reproduce the 64,615 support-bound /
   5,665 edge-bound split from the dual precompute.
2. `effective_h1_stats` from the built first prism will match the
   post-write `boundary_layer_report` (same physical quantity).  The first
   implementation used the re-sorted output face index and read 3.6x too
   large; that is a **falsified prediction**, corrected by storing the
   input wall-face centres in `built`.
3. The coverage denominator must be the selected wall-face count, not the
   number of extruded faces.  The quality attempt leaves 187 bare faces;
   the old denominator would print 100% instead of 99.47%.
4. A two-attempt executor (standard, then quality) will select quality on
   R1 because standard has 33 in-process pyramids and quality has 0.
5. The executor must restore the original dual before attempt 2; otherwise
   attempt 2 adds a BL on top of the standard BL.  This was **confirmed
   the hard way**: the first policy run spent 588 s and failed with
   multiply-connected edges because it re-read the standard BL mesh.
6. `pyr_tolerance=0` alone is not enough; the quality attempt must use the
   vertex-decoupled local termination, then run
   `clear_boundary_pyramids_case` (8 boundary faces cleared on R1).
7. The R1 h1 ratio will remain ~0.113x in **both** attempts; the executor
   must return the quality mesh and report the h1 gap, not call the result
   a fidelity pass.
8. A rollback env flag will preserve the historical one-attempt path for
   one milestone; no default is changed without the measured A/B.
9. y+ must use the achieved h1.  Without a solver u_tau the report returns
   `null`, not a fabricated number; the optional flat-plate/pipe estimate
   is labelled as an estimate.
10. The terminator-internal goal cannot be closed by this policy: R1 has
    **0 terminator faces**; its pyramids are internal top faces.  The
    partial-patch boundary terminator tests stay as the guard for the
    later vertex-splitting work.

## Measured A/B (R1, real checkMesh 2512)

Input: fresh dual from `C:\cfmesh_cases\m2_e2e_r1\mesh.msh`, 79,416
polyhedra, 35,395 selected wall faces; BL 5 layers, h1 1.26 mm, growth 1.2.

| Attempt | Prisms | Coverage | h1 median | Pyramids | Skew | Non-ortho errors | checkMesh |
|---|---:|---:|---:|---:|---:|---:|---|
| Standard (old) | 176,975 | 100% | 0.1423 mm | 39 | 5 | 26 | Failed 3 |
| Quality (new flag) | 176,040 | **99.47%** | 0.1424 mm | **0** | **1** | **0** | **Failed 1** |

Quality details: 187 wall faces (0.53%) excluded at the concave rim; 1,560
boundary terminator faces; 8 boundary pyramids cleared to 0; max skew
4.13, max non-ortho 86.03 (average 11.75).  The h1 cap is unchanged because
the excluded faces were not the cause of the h1 compression; the support
budget is.

## Predicted/measured failures and what they mean

- **h1 0.9x is not achievable on this dual without consume/reflow or a
  taller wall-adjacent cell.**  `consume=True` is disabled on R1 because
  the sliver detector flags all 35,395 wall faces at the 9.376 mm target.
  That is the next root fix, not a parameter.
- **The 39 pyramids were not terminator strips.** They are internal top
  faces; the quality attempt removes them by excluding the affected rim
  faces, which is a measured keep-best result, not a topology repair.
- **Terminator internalisation remains open.**  A correct fix needs split
  wall vertices/cells at the partial-patch rim; changing the side quad
  from boundary to internal without the split would open cells.  Keeping
  the current boundary terminators and documenting the class is safer than
  a half-fix.
- **The quality attempt is default-on for the fTetWild worker** after the
  A/B, with `POLYFOAMMESH_BL_QUALITY_POLICY=0` restoring the old single
  attempt for rollback.

## Closing evidence

- `tests/test_bl_fidelity.py`: **11 passed** — cap split, achieved-h1
  consistency with the report, y+ estimate/null, policy ordering,
  keep-best snapshot, baseline restore, h1 gap, rollback flag.
- `tests/test_bl_poly.py`: **38 passed**; h1 stats now match the post-write
  report on the healthy cube (0.02502 m both).
- R1 quality policy through the executor: standard 46.7 s, quality 88.4 s;
  selected quality; 0 pyramids / 1 skew / 0 non-ortho errors / Failed 1;
  coverage 99.47%; h1 0.1424 mm = 0.113x (declared cap).
- Full fast suite: **1646 passed, 2 skipped, 20 deselected, 1 xfailed**;
  `ruff check src/ tests/` green.
- No claim of h1 0.9x, zero skew, or internal terminators on R1.  The
  quality flag is default-on with `POLYFOAMMESH_BL_QUALITY_POLICY=0`
  restoring the old single attempt.

## Rollback

`POLYFOAMMESH_BL_QUALITY_POLICY=0` returns the compatibility policy
(standard only).  The compatibility policy is still available in code; no
preset or h1 value is changed.  The h1 cap report is additive.
