# M2 milestone — reasoning and predictions (2026-10-22)

Predecessor: `notes/m2_milestone_diagnosis.md`.  Written before the final
E2E and before committing the structural fixes.  No mesh default changes.

## Claim to establish

M2 can close only if the measured exits are true.  The audit says they are
not: BL coverage is 9/15 ≥98% on fTetWild and 0/15 on GMSH/poly, y+ per
patch does not exist, W2 speed is unmeasured, and the corpus was not
re-validated.  This milestone therefore does **not** claim M2 closure.  It
closes the structural defects that are safe to fix now, proves them with
tests and E2E, and writes the remaining W2/W5/W6/W9 work with priority and
owner in the docs.

## Structural fixes and predictions

1. **One checkMesh parser.**  `parse_checkmesh_output` will map
   `CheckMeshReport.from_output`; the duplicate `_CHECK_*` regex block in
   `openfoam_runner.py` disappears.
   - Prediction: all 6 `test_checkmesh_parsing` and 8
     `test_checkmesh_report` tests pass; the real R1 output still yields
     253,681 cells, 39 face pyramids, 5 skew, 26 non-ortho errors and
     `Failed 3`; the healthy v2512 fixture still yields no defects.
   - Falsification: any metric changes on the fixtures, or a failed report
     becomes passed without `accept()`.
2. **Named phase budgets.**  `PHASE_BUDGETS`/`PhaseBudget` become the one
   declaration of 300/3600 (GMSH), 180 (surface), 600/14400 (fTetWild),
   600 (checkMesh), 900 (gmshToFoam), 300 (polydual), 300/3600 (dual/BL).
   Worker class constants derive from them.
   - Prediction: every value is byte-identical to the pre-change literal;
     the 28 GMSH watchdog tests and 11 phase-watchdog tests pass; no
     behavior change.
   - Falsification: any worker timeout differs, or a test needs a new
     value.
3. **One session BL state.**  `SessionSnapshot` drops the unused split
   `bl_enabled/bl_n_layers/bl_thickness_ratio/bl_expansion_ratio` fields and
   carries `bl_config` (the schema-versioned mapping already used by
   QSettings/presets).
   - Prediction: old snapshot JSON with split fields still loads (unknown
     fields filtered) with `bl_config == {}`; a new snapshot round-trips the
     mapping; no GUI caller breaks (there is none).
   - Falsification: a real snapshot fails to load or a caller needs a
     removed field.

## E2E predictions

- **R1 mandatory case** (real fTetWild `.msh`, convert + dual + BL +
  checkMesh): current package 100–130 s total, 253,681 cells, 174,265
  prisms, 100% coverage, h1 0.1418 mm, `Failed 3` (39/5/26 classes).
  Baseline 120.8 s; no regression expected (W7/W8 add liveness only).
- **Cube-duct healthy E2E** (GMSH volume + gmshToFoam + dual + BL +
  checkMesh): should complete; BL should be accepted or the failure
  declared with `CheckMeshReport`; record tet/convert/dual/BL times and
  final metrics.  If the BL fails, that is a W5 gap, not a reason to lower
  a gate.
- **M2 exit**: predicted NOT MET.  The E2E cannot change the corpus
  coverage numbers; it only confirms the real case is unchanged and the
  packaging path still works.

## Keep-best / rollback

- The parser consolidation is guarded by both parser suites and the real R1
  output; if any metric moves, restore the old parser and keep the structured
  model as a parallel field (but document the divergence).
- Budget constants are behavior-preserving aliases; a mismatch reverts the
  alias, not the values.
- Session cleanup has no GUI caller; the old loader already ignores unknown
  keys.
- No mesh default, attempt order, clamp, or validation threshold is changed
  in this milestone.

## Closing evidence

- **R1 real E2E** (`C:\cfmesh_cases\m2_e2e_r1`, current tree): convert
  4.8 s + dual/BL 92.6 s = 110.0 s total.  253,681 cells, 174,265 prisms,
  100% wall coverage, h1 0.1418 mm vs 1.26 mm requested (0.11×), checkMesh
  `Failed 3` (39 pyramids, 5 skew, 26 non-ortho errors, 32
  `wrongOrientedFaces`).  Baseline log 120.8 s and the same counts: no
  regression, ~9% faster.
- **Cube-duct full E2E** (GMSH volume + gmshToFoam + dual + BL + checkMesh):
  tet 129.1 s, convert 9.5 s, dual+BL 183.7 s, total 334.4 s; final 256,767
  cells, BL coverage 100%, checkMesh `Failed 1` (non-ortho 80.48°, one
  check).  The BL first layer is 0.0176 mm against 0.5 mm requested
  (0.035×), which is the same W5/W6 compression gap seen on R1.  The
  healthy path completes; no crash/freeze.  A second independent cube run
  produced a different `.msh` SHA-256 and 256,653 cells: GMSH's parallel
  tessellation is not byte-deterministic.  M2 adds the opt-in
  `POLYFOAMMESH_GMSH_THREADS=1` pin; default behaviour is unchanged and the
  hash-level fix has not yet been validated end-to-end.
- **Parser consolidation**: 6 `test_checkmesh_parsing` + 9
  `test_checkmesh_report` pass; R1 still 253,681/39/5/26 and healthy v2512
  still passes with no structured defect.
- **Budgets/session**: 11 `test_phase_watchdog` (including constants from
  `PHASE_BUDGETS`) and 6 `test_session` pass.
- **Full fast suite**: 1614 passed, 2 skipped, 20 deselected, 1 xfailed;
  `ruff check src/ tests/` green.
- **M2 verdict**: NOT CLOSED.  W5/W6/W9 are feature/A-B work; the milestone
  documents them with priority instead of manufacturing a closure.  The
  structural fixes landed without changing a mesh default.
