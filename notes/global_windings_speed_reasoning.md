# `_solve_global_windings` speed — prediction before touching anything

## What's actually slow (from `profile_bl_precompute.py`, valve case)

Profiled cumtime for `_solve_global_windings`: ~153.9s tottime / 195.5s
cumtime across 5 fallback-scale builds. The function has three phases:

1. Build `edge_occ`: pure-Python double loop, `for fi, f in enumerate(faces): for k in range(m): ...` —
   O(sum of face degrees), one dict-append per face-edge-side. On the valve
   this is ~hundreds of thousands of faces x 3-4 edges x up to 2 occurrences
   (owner+neighbour) = millions of Python-level dict operations.
2. Build `adj`: a second pure-Python loop over `edge_occ.items()`, grouping
   occurrences by cell to find the exactly-2-occurrences pairs and their
   parity — same order of Python-level overhead as phase 1.
3. BFS component-parity solve (`deque`-based): genuinely sequential graph
   traversal, one component at a time, with conflict detection.

## Prediction

Phases 1 and 2 are pure data restructuring (build a graph from face/edge
adjacency) — no correctness-critical decision-making, they just re-arrange
known input into a different lookup shape. This is the same category of
"safely vectorizable" work as the Laplacian relax fix: replace nested
Python loops over ragged data with a flatten + numpy `argsort`/grouping
pass, keep the exact same graph as output.

Phase 3 (BFS) is the actual correctness-critical logic — the one the
project's own history warns about (the "previous repair" that left cells
unclosed / caused a catastrophic flip was a *different, greedy* algorithm;
this BFS is not that, but it IS the piece a moment's carelessness could
break silently on a topological edge case). **Prediction: I will NOT touch
phase 3.** I expect it to keep its current cost (it can't be vectorized —
it's an inherently sequential fixed-point over a graph with early conflict
detection) but that phases 1+2, which look like the bulk of the Python-loop
overhead by line count and are pure numpy-friendly restructuring, should
shrink dramatically (in line with the Laplacian relax result: -39% came
from replacing a similar per-vertex Python loop).

I will verify the graph (`adj`, `n_incompat`) built by the new vectorized
code is **byte-identical** (same edges, same parities, same n_incompat) to
the old code on both a small synthetic ragged mesh and the real valve
converter output, before ever letting it drive a real BL build. If it is
not identical, I revert — no partial credit, no "close enough."
