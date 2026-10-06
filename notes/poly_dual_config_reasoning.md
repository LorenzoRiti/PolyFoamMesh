# Dual configuration matrix (Phase 2) — reasoning (2026-10-05)

Written BEFORE the Phase-2 measurement (code committed `8cfae3a`/`2ef8c1e`).

## Gap being addressed

Poly rows cube_duct 80.64 / venturi 146.41 / concave_bracket 105.41; dual-only
51.87 / 74.00 / 62.66+36 pyramids. The benchmark poly path runs the converter
with `smooth_iters=3`, `fix_nonplanar_faces="none"` and WITHOUT the
compactness/axis passes — all measured levers that are opt-in and never
exercised here. Internal dual faces are non-planar rings by construction, and
OpenFOAM assumes planar faces (worse non-ortho/skew, per the converter
docstring); `fix_nonplanar_faces="triangulate"` tiles each face's area vector
exactly, so it preserves closure/volume.

## Levers and prior measurements

- `use_compactness`/`use_axis_balance` with the aspect-aware objective:
  measured on the cylinder (real checkMesh) NOmax −12.4%, avg −19.3%,
  skew −8.6%, aspect not regressed. 10 smoothing iterations were used there.
- `fix_nonplanar_faces="triangulate"`: implemented, never measured on the
  corpus.
- `smooth_iters`: benchmark uses 3; the compactness measurement used 10.

## Prediction (write BEFORE the experiment)

- `iters10`: small improvement everywhere (more keep-best passes); venturi
  74→70-73; healthy ≤1° change.
- `compact+axis`: the measured lever; venturi 74→65-70 (PASS possible),
  cube_duct 51.9→46-50 (snappy 46.2 — parity), concave 62.7→58-62.
- `compact+axis+iters10`: best of both; venturi 63-68.
- `+triangulate`: improves skew/non-ortho on non-planar faces (biggest
  expected skew gain: cylinder 2.77→<2, groove1/slot1 similar).
- `triangulate` alone: skew/non-ortho improve; NOmax −2…−5°.
- No healthy geometry regresses >2°; no passing row starts failing.
- Note: these are DUAL-side improvements; the BL damage (+29…+72 NOmax) is
  still present after the Phase-1 kill, so the full-pipeline numbers may not
  move as much as the dual-only deltas suggest. The matrix is judged on the
  FULL pipeline (that is what the gate measures).

## Measurement plan

- Configs (opt-in flags): iters10; compact+axis; compact+axis+iters10;
  compact+axis+iters10+triangulate; triangulate.
- Geometries: cube_duct+venturi (fast) + concave_bracket (full); then the
  best config on the healthy fast set (cylinder/groove1/slot1/thin_gap/multi_inlet).
- Kill criterion: no config may regress a healthy geometry >2° on the primary
  metric; a config that does not improve any gap row is not promoted.

## Status

- [ ] Prediction written
- [ ] B-side measured
- [ ] Verdict

## B-side (full pipeline, committed comparison_20261006T012*.json)

| config | cube_duct | venturi | concave_bracket |
|---|---|---|---|
| baseline | 80.64 / 4.92 / 554 | 146.41 / 3.66 / 109 | 105.41 / 4.37 / 567 |
| iters10 | 80.64 (identical) | 146.41 (identical) | 105.41 (identical) |
| compact+axis | 80.73 | 146.41 | 105.41 |
| compact+axis+iters10 | 80.68 | 154.0 (worse) | 105.41 |
| +triangulate | 83.52 / skew 4.4 / aspect 1939 | 117.9 / **skew 34.9** | 163.69 / skew 48.4 |
| triangulate only | 83.51 / **skew 2.74** / aspect 1939 | 117.57 / skew 30.5 | 163.69 / skew 48.4 |

Dual-only isolation (probe `probe_dual_only_ab.py`, no BL):
- venturi 74.00 → 74.44 (no-op), skew 4.018 → 3.927;
- cube_duct 51.87 → 47.29 (NO better) but skew 3.930 → **4.588 (crosses the gate)**;
- concave 62.66 → 62.66 (no-op; the 36 pyramids dominate).

VERDICT: KILLED. The full pipeline is BL-dominated: dual-side levers cannot
show through (+29…+72 NOmax is added by the BL regardless). The cylinder
result (−12.4% NOmax) does not generalize: on these geometries the lever is
a no-op or trades NOmax for skew. `triangulate` improves cube_duct skew
(4.92→2.74) but destroys venturi/concave (skew 30-48, aspect 1939) — mixed,
not promotable. Lesson: **fix the BL damage first; dual tuning is
unmeasurable until then.**

- [x] Prediction written
- [x] B-side measured
- [x] Verdict: KILLED (not promoted)
