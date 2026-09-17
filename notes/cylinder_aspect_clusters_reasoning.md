# Lane D — cylinder aspect-ratio gap (prediction BEFORE measuring)

## Starting point (from notes/getme_compactness_reasoning.md)

Cylinder dual: aspect max 4.49 vs snappyHexMesh 3.02. Vertex relaxation is
at a proven LOCAL OPTIMUM (30 iterations change nothing; aspect_lam 1->10
gains 4.49->4.40 in the replica but regresses real checkMesh non-ortho
36.6->49.6 and skew 1.17->1.31 — reverted). Directional term dominates in
~10,620/10,622 cells. Hypothesis on record: worst cells sit at the flat
end caps, where the dual construction elongates cells in ways vertex
moves cannot fix.

## Prediction

1. Cluster map (to be measured, not assumed): the top-aspect cells will
   concentrate in TWO zones — (a) the flat-cap-adjacent layer, (b) the
   outer-wall-adjacent layer — because both are where the barycentric dual
   of a tet mesh produces directionally stretched cells (cap: prisms
   spanning cap-to-interior; wall: thin boundary-hugging cells). The
   directional term will dominate the compactness term in both.
2. Intervention outlook (honest prior: likely negative): the only
   machinery available for topology change is the concave-cell MERGE,
   which will not fire on merely-elongated (convex, valid) cells — so a
   no-code-change attempt is expected to no-op. A genuine fix (directional
   split of cap cells / local re-triangulation) needs new computational
   geometry that does not exist in this codebase; building it blind in one
   session risks invalid meshes for an uncertain gain.
3. Falsification of the negative: if the cluster map shows the worst cells
   ARE concave/defective (not merely elongated), the merge gate may apply
   and will be tried with real checkMesh. If a handful of cells dominate
   the max (e.g. top-10 >> p99), a surgical manual fix becomes discussable;
   if the tail is broad (hundreds of cells near the max), no local edit
   can close a 4.49->3.02 gap and the honest outcome is a scoped negative.

## Protocol

`C:/polybench2/cylinder_axis_both` (current-best dual cylinder):
per-cell aspect via the in-process replica (fetched OF formula),
rank cells, map top cells to (r, z), confirm/refute the two clusters,
then decide intervention vs documented negative. Real checkMesh verdict
on anything touched.

## Results (measured 2026-09-15, real OF2512 checkMesh via WSL)

Cluster map CONFIRMED as predicted: top-25 worst cells split between the
flat caps (z≈±0.995: cells 2108, 366, 2140, 4093, …) and the outer wall
(r≈0.49: cells 2640, 3379, 3075, 741, …). Directional term == aspect in
all of them (compactness 1.2–1.4, irrelevant). Tail: 14 cells > 4.0,
74 > 3.5, 213 > 3.0 of 10,622 — moderately broad, not a handful.
Zero pyramid violations; the existing merge gate finds no candidates
(convex valid cells), as predicted.

Specimen (worst cell 2108, aspect 4.489): 9 faces / 14 vertices, extent
x=0.067, y=0.055, z=0.013 — a thin WIDE slab hugging the cap, elongated
IN-PLANE, not in z. Correct topology op is an in-plane SPLIT (or finer
cap tessellation), which does not exist in this codebase.

Attempted with existing machinery (`tools/cylinder_aspect_trial.py` on a
scratch copy, never the reference): greedy force-merge of >4.0 cells with
the area-largest interior neighbour, accepted only on local aspect
improvement + clean pyramid/skew/volume gates + no global worsening.
12 merges accepted, in-process max 4.489 → 4.013; then a hard wall (39
gate rejections: further unions violate pyramid/skew validity).

Real checkMesh (`cylinder_axis_both/checkMesh_ref.txt` vs
`cyl_aspect_trial/checkMesh_trial.txt`):

| metric | reference | trial | Δ |
|---|---|---|---|
| max aspect ratio | 4.489 | 4.013 | **-10.6%** |
| non-ortho max | 36.56 | 65.65 | **+80% (regression)** |
| non-ortho avg | 9.606 | 9.680 | ~same |
| max skewness | 1.17 | 3.89 | **+233% (regression)** |
| failed checks | 1 | 3 | **+2 (regression)** |

Verdict: **REJECTED, do NOT promote.** Same lesson as lam=10 (Result 4)
and slot1: the in-process gate counts VIOLATIONS (skew > 4.0, pyramid),
so a 3.89-skew / 65°-non-ortho merged cell passes every gate while real
checkMesh rightly fails it. Merging valid elongated cells trades
max-aspect against severity metrics — the wrong trade (merging identical
needles provably preserves the directional ratio anyway; only the
downward-thickening pairs helped, and those are exhausted).

Honest bottom line: partial diagnostic win (clusters mapped to the cell,
mechanism + correct-op spec documented), intervention measured and
rejected. Closing 4.49 → 3.02 needs a directional SPLIT operator (new
computational geometry: plane-split of cap slabs with closure + patch +
upper-triangular bookkeeping) or finer cap tessellation at meshing time —
scoped future work, not a session task. No production code touched by
this lane (trial script only); reference cases byte-identical.
