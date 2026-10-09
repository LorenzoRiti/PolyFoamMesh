# Slot1 decoupled_vertex exclusion anomaly: prediction

Date: 2026-09-11. Written BEFORE looking at the bench data
(`C:/polybench2/h4_generalization/result.json`). Objective: understand
why on slot1 `decoupled_vertex` excludes 378 faces against 132 of
`decoupled` (≈3x), while on cube_duct and groove1 the two modes are
identical (0 exclusions in both).

## Known measurements (H4 generalization, 2026-09-11)

| geometry | pyr_input | decoupled excluded | decoupled_vertex excluded | ratio |
|---|---|---|---|---|
| cube_duct | 0 | 0 | 0 | 1.0x |
| groove1 | 0 | 0 | 0 | 1.0x |
| slot1    | 22 | 132 | 378 | 2.86x |

## Prediction (written before inspecting the data)

The structural cause I suspect is the **per-vertex propagation
on the local topology of slot1**. The 22 pyramid defects in input on
slot1 are localized (typical of narrow-slit geometries), and the
wall vertices of the collapsed dual of slot1 are **high-valence
shared** among many incident faces (because of the small cross
section). The per-face cascade (face-mode) excludes
exactly the faces associated with the defective prisms (~132); the
per-vertex cascade (vertex-mode) propagates the exclusion to ALL the faces
incident to each "bad" vertex, and with high sharing valence
this incident set is ~3x larger than the set of direct faces. On
cube_duct and groove1 the topology is simple (no input defect,
empty cascade capture → identical).

So my working hypothesis: **the 3x factor is proportional to the
local topology (high sharing valence of vertices between faces
incident to defects), not to the number of defects itself**. Consequences:
1. mesh quality (skewness, aspect ratio) does not worsen — vertex-mode
   excludes a superset of "at-risk" prisms that face-mode had
   kept, so the resulting mesh is equal or slightly
   better;
2. the "cost" in number of lost prisms (711) is proportional to the
   local topology, not a general defect of the vertex criterion.

## How I will inspect the data

To verify the hypothesis I must:
1. Extract from `result.json` the exclusion sets of `decoupled` and
   `decoupled_vertex` on slot1 (if the tool saved them: it saved
   `excluded_iterative` as a count, not the face set).
2. If I do not have the saved sets: re-run `local_termination="decoupled"`
   and `local_termination="decoupled_vertex"` on slot1 with additional
   instrumentation (instrument the engine to log `len(added)` per
   round and the face sets), then compute the intersection and difference
   of the sets and verify the topological propagation.
3. Evaluate whether the 711 extra prisms excluded by vertex-mode are
   concentrated (near the 22 input defects) or distributed, and
   quantitatively whether the resulting mesh has comparable or better
   skewness/aspect.

## What I expect to find

- `decoupled_vertex_excluded ∖ decoupled_excluded` non-empty and of
  size ~246 (= 378 − 132; some are already in the intersection).
  Moreover: the vertices excluded by vertex-mode but not by decoupled are
  shared among several faces of the defect cluster.
- The mesh quality (skewness, aspect ratio) is similar or slightly
  better, because the 711 lost prisms were "borderline" (high aspect
  ratio, skewness) — excluding them improves aggregate quality. The
  aggregate `max_skewness`/`max_aspect_ratio` does not capture the difference
  because it is the max; a distribution (e.g. skewness histogram) is needed
  to see whether vertex-mode reduces the heavy tail.

## Honesty

If the inspection of the data does not confirm the hypothesis (e.g. the 246 extra
faces are distributed, not concentrated, or the quality worsens
significantly), the honest admission will be: the 3x difference on
slot1 is probably an effect of the vertex cascade on borderline input,
and I will document the measurement without claiming to have isolated
the cause.

No default changed, no modification to the engine or the GUI.
The deliverable is a paragraph in `docs/residual_risks.md` (append to the
H4 generalization sub-section).
