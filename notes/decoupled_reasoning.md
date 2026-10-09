# FASE 5 — "decoupled" variant: prediction BEFORE the tests

Date: 2026-09-09. Written before launching the measurements of the variant
just implemented in the engine
(`run(local_termination="decoupled")`, opt-in, default unchanged).

## What the variant does (implemented in `bl_poly.py`)

Same loop as `_run_local_termination` (max 3 rounds per scale, scales
1.0 -> 0.6 -> 0.35 -> 0.2 -> 0.1), but with the exclusion set
RESET at the scale change (`decoupled=True`):
- first it tries the REQUESTED scale (1.0, full thickness);
- if valid, the mesh comes out at full thickness (no 10x degradation of
  the pure thin-first);
- if it fails, it steps down in scale with a NEW exclusion set: the
  exclusions accumulated at a coarse scale do not "poison" the thin
  scales;
- the legacy mode `local_termination=True` (carry, default of the
  opt-in path) stays byte-identical: the reset triggers only with
  `"decoupled"`, which is explicitly requested.
The final count reported by `res.stats["local_excluded_faces"]` is
that of the scale that validated.

## Why it should solve the problem

- FASE 4 showed that pure thin-first wins in exclusions/prisms but
  degrades the thickness (it always validates at 0.1, even when 1.0 would be
  fine). The cause was the ORDER: accepting the first scale that validates.
- The cause of the 19,625 thick-first exclusions on the valve was the
  TRANSPORT of exclusions across scales: the coarse scales exclude faces
  that at 0.1 would be valid (whack-a-mole), and the set keeps
  growing. FASE 3 measurement: starting clean at 0.1 needs 2,058
  exclusions.
- Decoupled combines the two: try 1.0 (full thickness); only if it fails
  does it step down, and each scale repays only its own defects.

## Predictions

### Cylinder, cube duct, groove1 (healthy, validate at 1.0 round 0)

- The reset never comes into play (success at the first scale, first
  round).
- Prediction: **identical to thick-first** in everything: scale 1.0, same
  prisms (74,808 / 108 / 10,062), 0 exclusions, aspect max 18.3 / 78.7 /
  15.9 (full thickness). Identical time.

### Slot1 (medium, validates at 1.0 after 1 round of exclusions)

- Here too the reset does not come into play: failure and success remain
  WITHIN scale 1.0 (round 0 fails with 50 pyramids > 42 input,
  round 1 validates after 132 exclusions).
- Prediction: **identical to thick-first**: scale 1.0, 79,614 prisms, 132
  iterative exclusions (216 total), aspect 16.7, ~36 s. (Better than
  thin-first on thickness: aspect 16.7 vs 128.5.)

### Valve (the case that matters)

- Scale 1.0: 3 rounds, fails (like thick-first: 50 -> 6 -> 6 negative
  volumes), exclusions accumulated ~2,000-4,000 WITHIN scale 1.0;
- scale 0.6: set reset, 3 rounds, fails (like thick-first: ~1,019
  pyramids > 1,007);
- 0.35 / 0.2: same, they fail with their own fresh sets;
- scale 0.1 with a NEW set: it should reproduce EXACTLY the
  thin-first run (same code, same input): 3 rounds, 1,946 + 112 + 0 =
  **2,058 iterative exclusions**, **445,716 prisms**, checkMesh
  **885 wrongly oriented faces** (vs 895 input), skew ~47.6, aspect max
  ~15,419, "Failed 4".
- Expected time: ~15 builds (3+3+3+3+3) x ~95-115 s = **24-30 min**
  (no time gain: the coarse scales are tried anyway;
  the gain is the exclusions, not the time).
- Expected risk: low. The 0.1 path is deterministic and identical
  to the one already measured twice (stage2e and FASE 3 thin).
  Only uncertainty: how many exclusions the coarse scales accumulate BEFORE
  the reset (they do not affect the final result, only the warnings).

## User objective and success criterion

- Valve: ~2,058 exclusions (not 19,625) + thickness/`scale` = 0.1
  (no alternative there) + checkMesh not worse than thin-first.
- Healthy cases: full scale 1.0 (no thickness degradation).
- If decoupled does NOT reduce the valve exclusions below ~3,000,
  my explanation (transport across scales = cause) is wrong and must be
  rewritten.
- If the healthy cases do not stay at 1.0, there is a bug in the implementation.

# RESULTS (filled in after the run, 2026-09-09)

## Healthy cases (measurement, tools/bench_bl_thinfirst.py, real checkMesh)

| Geometry | THICK | THIN | DECOUPLED |
|---|---|---|---|
| cylinder | scale 1.0, 74,808, 0 excl, aspect 18.3 | scale 0.1, aspect 104.3 | **scale 1.0, 74,808, 0 excl, aspect 18.3** |
| cube duct | scale 1.0, 108, aspect 78.7 | scale 0.1, aspect 781.6 | **scale 1.0, 108, aspect 78.7** |
| groove1 | scale 1.0, 10,062, aspect 15.9 | scale 0.1, aspect 90.8 | **scale 1.0, 10,062, aspect 15.9** |
| slot1 | scale 1.0, 79,614, 132 excl, aspect 16.7 | scale 0.1, 80,004, 0 excl, aspect 128.5 | **scale 1.0, 79,614, 132 excl, aspect 16.7** |

DECOUPLED is identical to THICK in everything: prediction confirmed 100%.
The reset never triggers because the success comes within scale 1.0.

## Valve

- success at scale 0.1, **2,058 exclusions** (predicted), **445,716
  prisms** (predicted), 3,684 faces without BL = 1.63% (like thin-first);
- checkMesh: 885 wrongly oriented faces vs 895 input, "Failed 4", skew
  47.6, aspect max 15,419, non-ortho 55 — identical to thin-first;
- warning trajectory: failures at 1.0 (50→6→6 negative volumes),
  0.6 (42 neg → 1,037/1,020 pyramids), 0.35 (15/1 neg → 1,017), 0.2
  (4/1 neg → 1,016), then fresh 0.1 at the third round (1,946+112) validates;
- **time 2,215 s (37 min)**: slower than predicted (24-30 min) and
  slower than both thin (305 s) and thick (1,602 s). Cause: it tries
  all the scales anyway (15 builds, 3 rounds each) and at 0.1 it pays
  the cost of the 3 fresh rounds. The advantage is in the exclusions/prisms,
  not in the time.

## Final comparison (valve)

| Variant | excl. iter. | without BL | prisms | checkMesh | time |
|---|---|---|---|---|---|
| THICK (legacy) | 19,625 | 21,208 (9.36%) | 410,668 | 891 wrong, Failed 4 | 1,602 s |
| THIN (experimental) | 2,058 | 3,684 (1.63%) | 445,716 | 885 wrong, Failed 4 | 305 s |
| **DECOUPLED** | **2,058** | **3,684 (1.63%)** | **445,716** | **885 wrong, Failed 4** | **2,215 s** |

## Conclusion

The decoupled variant hits the objective: it captures the thin-first
advantage (9.5x fewer exclusions, +8.5% prisms, better checkMesh)
WITHOUT degrading the thickness on healthy cases (full scale 1.0). It is the
natural candidate for the local termination semantics when
it gets enabled: "requested thickness if possible, only the defects
pay". The time cost on defective geometries remains; a
possible optimization (skip the intermediate scales / reuse the
exclusions when the failure signature is the same) must be measured
separately. No default changed (`local_termination` stays off by
default; `True` keeps the carry semantics measured in FASE 2/3).
