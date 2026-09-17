# BL coverage gap — per-vertex local height fallback (prediction before testing)

## What was measured (2026-09-14, instrumented `_collect_exclusions`, real valve run)

Every exclusion round on the real production valve was broken down by
cause:

| round | negative/zero volume prisms | pyramid violation TOUCHING a prism | pyramid violation touching ONLY core cells | new exclusions |
|---|---|---|---|---|
| scale 1.0, round 1 | 128 | 800 | 622 | 1124 |
| scale 1.0, round 2 | 0 | 0 | 376 | 0 |
| scale 0.6, round 1 | 104 | 1029 | 507 | 1235 |
| scale 0.6, round 2 | 0 | 0 | 371 | 1 |

**Finding**: violations that touch ONLY pre-existing core cells (not a
newly-extruded prism) NEVER cause a new exclusion — they are inherited
defects already inside the "don't exceed the input's own violation
count" budget documented elsewhere in this file. Every real exclusion is
caused by the FRESHLY EXTRUDED PRISM being invalid (negative volume) or
introducing a NEW pyramid violation. This means the exclusion is
overwhelmingly a **local extrusion-height problem** at ~230-330 wall
vertices, not a defect inherited from the underlying dual mesh topology.

## Why the current mechanism gives up entirely instead of degrading gracefully

`local_termination="decoupled_vertex"` (H4) deliberately uses BINARY
layer counts (full stack or nothing) — the module's own docstring
explains this was chosen because the older "consistency fixpoint"
(intermediate layer counts, flooding a whole connected region to the
minimum count) had its own worse failure mode (flooding healthy
neighbours down to a shorter stack). Binary avoids that, at the cost of:
a vertex that fails at every tested GLOBAL scale (1.0, 0.6, 0.35, 0.2,
0.1) loses its wall shell entirely — even though the actual failure is
local to just that vertex's own extrusion height, not a fundamental
inability to have ANY coverage there.

## The idea (untested until this note is written)

Add a LAST-RESORT, purely ADDITIVE pass: for wall vertices that would
otherwise be excluded after exhausting every global scale, try a further
LOCAL height reduction (e.g. half the thinnest already-tried height,
`first_height * 0.1 * 0.5^k` for a few `k`) for JUST that vertex's own
prism stack, keeping the SAME layer count (not reducing it, unlike the
old fixpoint) but at a much smaller absolute height. If the resulting
prism is valid (no negative volume, no new pyramid violation) at some
tried local height, keep it instead of excluding the vertex entirely.

This is different from both existing mechanisms:
- vs `decoupled_vertex` binary exclusion: recovers SOME thin coverage
  instead of zero, for vertices whose failure is purely a height problem.
- vs the old "consistency fixpoint": does NOT change the layer COUNT or
  flood a connected region — it only shrinks the ABSOLUTE height of the
  one failing vertex's own stack, so it should not reintroduce the
  "transition band" problem fixpoint had (no neighbouring vertex is
  affected).

## Prediction

I predict this recovers SOME but not ALL of the ~4,162 currently
excluded wall faces on the valve (the negative-volume/pyramid-violation
failures found above are concentrated at ~230-330 root vertices per
scale attempt, but per FASE 9's own documented propagation mechanism,
each root vertex's exclusion can propagate to its whole vertex-star of
incident faces — so recovering the root vertex should recover a
larger multiple of faces, plausibly cutting the 9.5% exclusion rate
significantly, though not to zero, since some vertices may still fail
even at the smallest tested local height).

**Risk**: this touches the same construction path (`_build`,
`_collect_exclusions`) as the documented-fragile winding/prism logic.
Mitigation: implemented as a strictly ADDITIVE last-resort attempt only
for vertices already marked for exclusion (never changes behaviour for
any vertex that already succeeds today), validated with real checkMesh
on ALL 5 reference geometries (cylinder, cube, groove1, slot1, valve)
before considering it anything more than an experimental opt-in, exactly
like every other BL change this session.

If this prediction is WRONG (no meaningful recovery, or it introduces
real defects on any of the 5 geometries), that is reported here
honestly, not hidden, matching the discipline used throughout this
session.

## Literature check (per explicit user request, before implementing)

Re-read Alauzet's closed-advancing-layer paper (already fetched earlier
this session, AIAA, arxiv/INRIA). It describes EXACTLY this class of fix
for tetrahedral closed-advancing-layer BL meshes: when a vertex would be
terminated, don't exclude it — search a small DISCRETE set of candidate
partial positions (the paper uses 0%, 40%, 60% of the expected
displacement, plus "move back to the previous layer's completed
position") and keep the best one that stays valid. This is a genuinely
different mechanism from both of ours: not binary exclusion
(`decoupled_vertex`/H4), and not the old "consistency fixpoint" (which
floods a whole CONNECTED region to the minimum layer count) — the
paper's method decides each vertex independently, no cross-vertex
flooding.

## Infrastructure check — NOT implemented tonight (honest stop)

Checked `_build` (bl_poly.py:1333, ~500 lines): per-vertex layer counts
already exist in the codebase's DATA MODEL (`layers_per_face_min/max`
stats, `nf` dict), but only ever populated by the OLD fixpoint mechanism
(which floods a connected region) — there is no existing entry point for
an INDEPENDENT per-vertex partial-height retry (Alauzet's method) without
non-trivial new plumbing inside `_build` itself, the single most
documented-fragile function in this codebase ("previous repair was a
per-cell greedy flip... left 24 cells unclosed... cascaded into a
catastrophic global flip... 378,605 non-positive volumes").

**Honest decision (original session): not attempted that night.**
Implementing this correctly requires surgery inside `_build`, and
validating it requires a real checkMesh run on all 5 reference
geometries — each of which has taken 10 minutes to over an hour due to
genuine host contention. Given the stakes of this specific function (its
own docstring's warning is not hypothetical — it already happened once),
rushing this without the time to validate properly would repeat exactly
the mistake this project's whole discipline exists to prevent. This was
reported as that session's honest stopping point on this specific lever.

## Follow-up session: why a literal per-vertex serial retry is infeasible,
## and the batched design chosen instead

A true per-vertex serial retry (test vertex 1 alone, then vertex 2 alone,
...) would need one full `_build` + `_collect_exclusions` rebuild PER
CANDIDATE VERTEX. Measured this session: `_build_precompute` alone is
~58s on the valve, and a full `_build` attempt is comparable or larger;
the vertex-exclusion runs measured earlier (`most_visible_normal_
reasoning.md`) have ~5,000-7,000 excluded faces mapping to thousands of
candidate vertices per round. Serial one-at-a-time retries would cost
thousands of rebuilds -- hours to days, not a session. This is the
concrete number behind the "not attempted" decision above, now quantified.

**Resolution: BATCH the retry, one extra rebuild per ROUND, not per
vertex.** `_run_local_termination`'s loop already has a natural batch
point: each round already collects a SET of newly-failing vertices
(`gained`) via one rebuild. Instead of excluding all of `gained`
immediately, try ONE extra rebuild attempt where those specific vertices
get a SCALED-DOWN local height budget (via a new `vertex_height_scale`
multiplier applied to `max_h[w]` inside `_build`, additive/opt-in,
`None` default = today's exact behaviour) instead of being dropped
entirely. Re-run `_collect_exclusions` on this retry build: any vertex
whose faces no longer show up in the new exclusion set is RESCUED (kept
extruded at the reduced height, its scale persisted for later rounds);
any vertex that still fails is excluded exactly as today. This costs
ONE extra rebuild per round (same order of magnitude as the existing
loop, not a new order of magnitude), and is strictly additive: with the
new opt-in `local_height_retry=False` (default), behaviour is
byte-identical to today.

See `notes/local_height_retry_reasoning.md` for the prediction,
verification, and real-valve measurement of this batched design.
