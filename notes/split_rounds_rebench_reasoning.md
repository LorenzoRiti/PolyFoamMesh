# Split-rounds re-measurement on the regenerated valve fixture (FASE 6 follow-up)

Date: 2026-09-09. Written BEFORE the measurement. The entries `docs/residual_risks.md`
§Polyhedral Conversion and `docs/dev/handoff_poly_bl_deepseek.md`
(line 93) say: "split_rounds remains disabled: measured to add cost
without reducing defects on the reference part. Re-measure before
enabling." The original measurement (352 concave vertices not-improving)
dates to the pre-FASE 7 converter (pre-smoothing-default,
pre-`_solve_global_windings`, pre-regenerated-fixture). The current converter
may give a different result: bug-fixes, default smoothing, and the global
winding solver changed the landscape.

## Measurement

- `TetPolyDualConverter(case, split_rounds=N)` with N=0 (baseline) and
  N=1 (split). Everything else unchanged (converter defaults on the
  valve tet backup in `C:/polybench/valve1`).
- Output per run: `res.residual_defects`, `res.defect_breakdown`
  (pyramid / non_ortho / skew), `res.volume_before/_after/_drift`,
  `res.time_s` (conversion time).
- A/B comparison on the three numbers, current converter docstring.

## What I expect

The historical comment ("adds ~20s per round for nothing") was measured
on the pre-FASE 7 converter. I predict that the substance does NOT change: the
converter's keep-best selects the round with the fewest
defects, and if no round reduces the defects the "best" is the first
(round 0), and the subsequent rounds are discarded. That is, in total the
final result will be equivalent to split_rounds=0.

I predict (expected numbers on the regenerated fixture, default mode):
- split_rounds=0: residual_defects ~X (the fixture is "fresh" 863/9/14.58
  vs the old 895/55/44.3; the difference of the recent converter
  should be measurable but not striking);
- split_rounds=1: residual_defects <= X (does not improve in the documented
  history; the split of the vertex star breaks it into smaller pieces
  that in turn violate the pyramid test, keep-best discards);
- time: N=1 will have to be higher than N=0 (at least one
  more round); the difference will be on the order of tens of seconds
  (small, on a ~100s converter).

Honestly: 70% that the measurement confirms the status quo; 30% that
something has changed (e.g. the default smoothing and the global winding
solver changed the landscape and the split might now
help, or create defects where it previously did not). In either
case the result will be honest.

## Reading criterion

- If the numbers confirm (split_rounds does not help): honest document,
  no change to the default (split_rounds=0 stays off).
- If they change (split_rounds now helps, or creates defects where
  previously it did not): document the finding, propose updating
  the note in `docs/residual_risks.md` §Polyhedral Conversion, and in
  a separate commit (if the user confirms) the possible
  enabling. For this session: only measurement + docs, **no
  default changed**.

The working dir for the measurement is `C:/polybench2/split_rounds_rebench/`
(to mirror the FASE 7/H4 pattern).
