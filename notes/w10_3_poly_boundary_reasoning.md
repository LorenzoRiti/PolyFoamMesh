# W10.3 - poly quality and dual boundary: reasoning (2026-10-23)

Predecessor: `notes/w10_3_poly_boundary_diagnosis.md`. Prediction written
before the combo A/B and the release-gate measurement; the R1 quality
numbers are inherited from W10.1 and re-measured on the same dual.

## Claim

R1 has no unsolved dual-only defect: **1 pyramid in 79,416 cells is the
dual-only residual**, and W10.1's quality BL removes the 38 BL-added
pyramids.  The missing piece for release is attribution and an explicit
acceptance boundary, not another geometry knob.  The edge-filter 0.9 +
budget combo fixes pyramids but loses coverage (measured below); it must
not be promoted.  Venturi remains a W14 topological wall.

## Predictions

1. Fresh R1 dual-only = 1 pyramid, NO max 48.46, skew 3.31 (reproduce
   existing baseline).
2. Old standard BL = 39 pyramids / 5 skew / 26 non-ortho; difference says
   38 pyramids are BL-added, not dual-only.
3. W10.1 quality BL = 0 pyramids / 1 skew / Failed 1 at 99.47% coverage;
   the release gate accepts it only as a documented residual.
4. Edge-filter 0.9 + budget on R1 = 0 pyramids but coverage below 98%
   (predicted 80-90%); reject, do not promote.
5. `poly_release.py` measures dual-only and BL separately and never
   double-counts; missing/unreadable meshes are fail-soft.
6. The gate rejects any pyramid/negative volume and any coverage <98%; it
   accepts R1 quality's one skew only with `residual` populated.
7. The old combo stays opt-in; W10.1 quality remains default-on for
   fTetWild; explicit paths and presets are unchanged.
8. Venturi 224 pyramids/NO97.2/skew18.3 is not moved by the release gate;
   claiming a fix would be fake.  W14 is the only path.
9. No second dual/converter/merge/repair chain is added.
10. Full suite and ruff stay green.

## Falsification signals

- R1 dual-only is >1 pyramid on the fresh dual (then attribution changes).
- The combo reaches >=98% coverage (then promotion deserves an A/B).
- The release gate accepts a pyramid-bearing mesh.
- A documented residual is hidden from the log/report.
- A passing corpus row regresses.
- The gate reintroduces one of the forbidden levers.

## Measured results

### R1 attribution

Using `_detect_defects` on the fresh dual and the W10.1 outputs:

| Mesh | Cells | Pyramids | Attribution |
|---|---:|---:|---|
| dual-only | 79,416 | 1 | baseline |
| standard BL | 253,681 | 39 | +38 BL |
| quality BL | 255,456 | 0 | -1 vs dual-only |
| edge-filter/budget combo | 233,561 | 0 | coverage 87.1% |

Real checkMesh for the quality mesh: **Failed 1** (1 highly skew face),
0 pyramid errors, 0 non-ortho errors; coverage 99.47%; h1 0.1424 mm
(0.113x, W10.1 cap).  Standard: Failed 3 (39 pyr/5 skew/26 non-ortho).
Dual-only: the known 1-pyramid class, no BL terms.

### Combo A/B

`min_base_edge_fraction=0.9`, budget termination, 5 layers, same h1:
154,145 prisms, 0 pyramids, **87.1% coverage** (4,566 sliver-edge faces
marked thin), h1 0.114x.  Rejected: coverage below the 98% floor and worse
than W10.1 quality (99.47%).  Not promoted; remains opt-in.

### Release gate

`evaluate_release(bl, coverage, dual_only=dual)`:

- R1 quality -> accepted with residual ["real checkMesh still reports one
  skew class"].
- R1 standard -> rejected: 39 pyramids.
- combo -> rejected: 87.1% coverage.
- negative volume -> rejected.

## Scope decision

No new topological operator is attempted.  The prompt explicitly forbids
retrying merge/relax, compact-fan, triangulate, smoothing, feature-angle,
sharp-edge, edge-cap, min-base-edge, budget-term-alone, planarize and
zonal-worst-element.  A directional SPLIT/boundary-star rewrite is W14 and
would not fit the release scope.  W10.3 therefore lands the attribution +
release gate and documents the wall; no fake default promotion.

## Closing evidence

- `tests/test_poly_release.py`: **10 passed** - attribution, acceptance,
  strict checkMesh rejection, pyramid/negative/coverage rejection, combo
  rejection, fail-soft missing mesh, mapping and forbidden-lever guard.
- Full fast suite: **1669 passed, 2 skipped, 20 deselected, 1 xfailed**;
  `ruff check src/ tests/` green.
- Open: venturi wall (224 pyramids/NO 97.2/skew 18.3), full corpus re-run
  with the release gate, and W14 topology.
