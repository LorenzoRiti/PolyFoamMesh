# FASE 6 — early-exit: reasoning BEFORE the tests

Date: 2026-09-09. Written before any run of the early-exit variant
proposed to reduce the time on the valve (37 min in decoupled, 15
total builds when 4 coarse scales fail before 0.1 validates).

## Starting point: the early-exit "exit at the first valid scale" ALREADY EXISTS

The engine `_run_local_termination` (and the fallback loop in `run()`)
checks `if ok: return self._accept_built(...)` at every `ok, msg =
self._validate(...)`. As soon as a scale + a round validate, the loop
exits and writes the mesh. On the cylinder/cube/groove1 (healthy cases) the
success comes at 1.0 round 0: the loop does **1 single build** and ends
(cylinder: 17.8 s). On slot1 (medium) the success comes at 1.0 round 1
after 132 exclusions: **2 builds** (36.8 s). On the valve *none* of the
4 coarse scales (1.0, 0.6, 0.35, 0.2) validates: it takes 12 wasted builds
+ 3 builds at 0.1 = 15 total builds. So the early-exit
"stop at the first valid scale" **is already active**; the "time
lost" on the valve is not a bug of "not exiting", it is that only 0.1
actually works.

## Where you can really save

On the valve, the 4 coarse scales are all "equally useless" (pyr
count 1010-1019 > 1007 input). If after 1.0 round 0 fails I
skipped 0.6/0.35/0.2 and went straight to 0.1, I would save 9 builds
(3 rounds × 3 scales) = ~15 min on the valve. The question is: is this
heuristic SAFE in general?

## Proposed strategy (opt-in, default unchanged)

New engine parameter: `run(..., local_termination=..., 
early_exit_intermediates: bool = False)`. When True AND we are in
`local_termination` mode (decoupled or carry) AND scale 1.0 round
0 failed, the intermediate scales (0.6, 0.35, 0.2) are skipped
and it proceeds directly to 0.1.

## Hypotheses and risks

- **Implicit hypothesis**: "if 1.0 round 0 fails, the intermediates will not
  help" — on the valve it is true (the 4 scales have similar pyr
  1010-1019, and only 0.1 has pyr ≤ 1007 = input). It is not universally
  true: in theory a future case could have pyr_input=1050, pyr at
  1.0=1070, pyr at 0.6=1048, pyr at 0.1=1060 — the intermediates help.
  In that case skip_intermediates would fail at 0.1 and the run
  would fail cleanly (mesh unchanged). I document this as an acceptable
  risk for the opt-in.
- **On healthy**: skip does not activate (1.0 round 0 validates before
  any heuristic triggers). Same result as decoupled without
  the flag.
- **On slot1 (medium)**: 1.0 round 0 fails (50 pyr > 42 input) but
  round 1 validates. Would the skip activate? It depends: skip is "if 1.0
  round 0 failed, skip intermediates". Round 0 fails, skip
  activates, intermediates skipped, 0.1 tried. But 0.1 on slot1 with
  the exclusion set from 1.0 (carry mode) or a clean set (decoupled)
  might not validate (standard slot1 at 0.1?). We have no
  slot1 measurements at 0.1. It is possible that decoupled + skip + slot1
  fails. I document the risk: the heuristic optimizes the valve
  at the potential cost of slot1 (and similar "medium cases that validate at 1.0 with
  1 round of exclusion"). If it happens, we document and decide.

## Predictions on the valve (with decoupled + skip)

- Scale 1.0: 1 build (round 0, fail). Exclusions accumulated within 1.0:
  ~2,000-3,000 (in decoupled, set reset afterwards — so irrelevant).
- Skip 0.6, 0.35, 0.2: 0 builds saved.
- Scale 0.1: 3 builds (round 0 fail, round 1 fail, round 2 validates).
  Exclusions: 1,946+112+0 = 2,058 (identical to decoupled, because
  decoupled resets the exclusions at 0.1).
- Total: **4 builds** vs 15 today. **Expected time**: ~4 × ~100-120 s
  = **400-500 s** vs 2,215 s. Saving: ~30 min (~80% faster).
- checkMesh: it should be identical to decoupled (885 wrong, Failed 4,
  skew 47.6, aspect 15,419).
- Prisms: 445,716 (identical).

## Predictions on healthy cases

- Skip never activates (1.0 round 0 validates). Result identical to
  decoupled: scale 1.0, full thickness, aspect unchanged.

## Predictions on slot1 (risk)

- 1.0 round 0 fails (50 pyr > 42). Skip activates. 0.6/0.35/0.2
  skipped. 0.1 tried: with decoupled + skip, the exclusions start
  from the clean set (decoupled reset). At 0.1, pyr at 0.1 round 0: ???.
  We have no measurements. If pyr_round0_0.1 ≤ 42 (input), it validates. Otherwise
  round 1/2 with progressive exclusions. Probably similar to
  thin-first on slot1: 0 excl, validates at the first round. **Hypothesis**:
  skip on slot1 = identical to thin-first (scale 0.1, 0 excl, 80,004
  prisms). But it is a prediction, not a measurement.

## What to measure

1. Engine: implement `early_exit_intermediates` opt-in + 1 test
   (healthy cube with skip=True = identical to decoupled without skip).
2. Bench: add the `--early-exit-intermediates` flag to
   `tools/bench_bl_thinfirst.py` and measure:
   - cylinder/cube/groove1/slot1 with skip=True vs decoupled (no
     skip): they must be identical for the healthy ones, and for slot1 I verify
     whether the heuristic holds or not.
   - valve with decoupled+skip: 4 builds expected, ~400-500 s expected,
     2,058 excl expected, 445,716 prisms expected, checkMesh 885 expected.

## Reading criterion

- If valve decoupled+skip = 2,058 excl, 445,716 prisms, ~450 s
  (~8x faster than decoupled): the heuristic works, it is a
  candidate for the default *when* the local termination gets
  enabled (with the documented caveat of the slot1 risk).
- If slot1 decoupled+skip fails or degrades: the heuristic is
  too aggressive; a version "skip only if 1.0 round
  0 fails with pyr_count_round0 ≥ pyr_count_round0 of the finest scale
  already tried" must be measured (i.e. skip only if the pyr worsens monotonically).
- If valve decoupled+skip gives a different result from decoupled
  (e.g. more exclusions or does not validate): the heuristic broke something,
  it must be revised or removed.
