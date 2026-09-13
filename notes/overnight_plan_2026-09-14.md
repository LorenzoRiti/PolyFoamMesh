# Overnight plan — 2026-09-14

Agreed with the user before they went to sleep: a more substantial plan
than the original quick-wins list, grounded in real literature research
done up front (not after implementing, to avoid the earlier mistake of
claiming novelty before checking).

## What was researched tonight

1. **GETMe (Geometric Element Transformation method)** — real, cited
   family of mesh smoothing algorithms (Vartziotis & Wipper) that
   transforms elements toward their "ideal" shape via gradient ascent
   of an isoperimetric-quotient-like quality measure, instead of plain
   neighbour-averaging (Laplacian). Read the actual paper
   (arxiv 1406.4333, "Laplacian smoothing revisited"). **Honest finding**:
   the polyhedral/3D generalization is NOT a simple formula swap — the
   paper's own words: "a better polyhedral generalization... is bound to
   be a bit more involved," using eigenvalue-based quality measures (λ1,
   mean ratio) that need careful, unfamiliar derivation. Implementing
   this correctly, safely, and verifiably in one unsupervised overnight
   session is not realistic — flagged as a DEFERRED item, not attempted,
   rather than risk a subtle geometric bug with nobody available to
   catch it.
2. **"Optimized Dual-Volumes for Tetrahedral Meshes"** (Jacobson, arxiv
   2406.08647) — read in full. Solves a DIFFERENT problem (numerical
   properties of a Laplacian/mass matrix for PDE discretization on
   scalar fields), not cell-shape quality for a CFD control-volume mesh.
   Does not transfer directly. The one transferable insight (choice of
   dual "center" determines cell shape) is already what our barycentric
   choice deliberately trades off against Delaunay-input robustness —
   not a free lunch, deferred as its own investigation, not attempted
   tonight (would overlap with what smoothing already does — moving
   interior points — without a clear, safe, novel mechanism to add).
3. **Weighted Union-Find with parity constraints** — well-established
   classical CS technique (DSU with parity, used for bipartiteness /
   signed-graph-consistency checking), not something requiring a paper
   citation. This is the one item pursued tonight: a candidate
   replacement for the BFS component-labelling loop in
   `_solve_global_windings`, potentially much faster (near-O(1)
   amortized with path compression vs O(V+E) BFS with Python-level
   per-edge overhead), verified byte-for-byte identical to the existing
   BFS before any production change — matching the standing caution
   about this function's documented history of catastrophic bugs in a
   prior (different) algorithm.

## What is being attempted tonight (in order, each committed once verified)

1. Union-Find-with-parity as a side-by-side verified alternative to the
   BFS in `_solve_global_windings` — implemented, tested for EXACT
   equivalence (same x[] assignment, same conflict count, same
   component structure) against the existing BFS on every available
   real fixture (valve, and small synthetic graphs), before considering
   a production swap. If equivalence doesn't hold exactly, it is
   reported as a negative result, not forced in.
2. Reconnect the now-fixed skewness formula to
   `poly_cell_merge.repair_concave_cells_if_safe`'s guardian, so it can
   finally see the slot1-type regression that the old (buggy) formula
   could not.
3. Re-validate cell-merge + smoothing on all 5 reference geometries with
   real checkMesh.

## Result 1: Union-Find with parity — verified equivalent, but SLOWER (negative result)

Implemented a weighted Union-Find-with-parity solver as a standalone
side-by-side experiment (never touched production code) and verified it
against the existing BFS on the real valve fixture (1,241,048 faces):
**exact equivalence confirmed** (same conflict count, same component
partition, same relative face-parity structure within every component).

**But the speed hypothesis was wrong**: BFS took 11.42s, Union-Find took
19.58s — Union-Find was 1.7x SLOWER, not faster. In pure Python, both
algorithms are a single O(E) pass over the same edge list with similar
per-edge constant overhead; Union-Find's usual advantage (near-O(1)
amortized queries) doesn't help here since each edge is only touched
once — my two-pass path-compression `find()` has MORE per-step Python
overhead (extra list operations) than the BFS's simple deque
propagation, not less. Predicted faster, measured slower — not swapped
into production (no benefit, would only add complexity). Documented
here as a real, honest, verified-but-negative result rather than
silently dropped.

## Result 2: skewness reconnected to the merge guardian — real gain, one honest caveat found

Wired the now-fixed skewness count (`tet_poly_dual._detect_defects`'s
`skew` count) into `poly_cell_merge.repair_concave_cells_if_safe`: the
merge is now rejected if it would increase the skew violation count,
exactly closing the specific gap that let the slot1 regression through
before the formula fix (verified: on the slot1 pure-dual case, the
guardian now correctly rejects a merge it used to wrongly accept,
locked in by a new test,
`test_repair_concave_cells_if_safe_rejects_when_skew_would_worsen`).

**But a real nuance surfaced testing this on the production valve
post-BL case** — the exact case this session had ALREADY validated with
REAL checkMesh as a clean, safe improvement (340 → 58 wrong-oriented
faces, max skewness UNCHANGED at 22.1645, "Failed 4" both before and
after). Running it back through the now-skew-aware guardian: it
REJECTS this merge, because the skew violation COUNT rose 4 → 14 — even
though the MAX skew value (what checkMesh's pass/fail verdict actually
tracks) did not move at all. The guardian's count-based check is
stricter than what checkMesh itself would flag as a regression: more
faces crossed the >4 threshold marginally, without the worst offender
getting any worse.

**Decision**: left the count-based check as-is rather than "fixing" it
further tonight without real validation — this is a genuine,
non-trivial judgement call (should the gate track count or max? both
are defensible; the honest answer needs measuring across more real
cases, not a guess made once, alone, at 1am). Documented here plainly:
the guardian is now MORE conservative than checkMesh's real verdict on
at least one case we already independently validated as safe. This
means the guardian may reject some merges that would actually be fine
in practice — a safe failure mode (it never accepts something bad, it
can only be overly cautious), consistent with its own documented
contract ("not a guarantee against every possible regression... always
confirm with a real checkMesh run").

## What is explicitly NOT attempted tonight (and why)

- GETMe-style transformation smoothing — real complexity found via
  actual paper reading, deferred rather than risked.
- Dual-center optimization — overlaps with existing smoothing mechanism
  without a clear novel safe angle, deferred.
- `_solve_global_windings`'s BFS itself is only REPLACED if the
  Union-Find alternative is proven byte-identical across every real
  fixture available — otherwise this item ends at "measured, verified
  equivalent conceptually, not swapped in" as an honest partial result.

All work follows the standing discipline: prediction/reasoning written
before testing, real checkMesh validation before any claim of
correctness, local commits only (never pushed to GitHub), full fast
suite green before every commit.
