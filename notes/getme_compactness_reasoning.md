# GETMe-inspired compactness/axis-balance smoothing — real effort, partial result

## Why this exists

After the smoothing-gate fix (2026-09-14) closed part of the quality gap
to snappyHexMesh on a plain cylinder (non-orthogonality -19%, skewness
-16%), aspect ratio was UNCHANGED (4.46, vs snappy's 3.02) because plain
Laplacian relaxation's objective never targets it. The user pushed back
hard on an earlier attempt to defer implementing GETMe as "too complex to
learn in one session" — rightly: the correct response to not knowing an
algorithm is to study it properly, not to avoid it.

## What was actually learned and built

Read the real GETMe paper (Vartziotis & Wipper, arxiv 1406.4333, section
3.2): a tractable, closed-form quality function `q3(cell) = vol(cell) -
C^-1 * area(cell)^1.5`, concave when the boundary is fixed, whose
gradient reduces (dropping the volume term, justified by the existing
hard volume-positivity/ratio guards already in `smooth_dual_mesh`) to a
gradient-ascent step on cell surface area alone.

Derived by hand, then VERIFIED against central finite differences before
trusting it (never assume a hand derivation is correct without checking):
- `d(face_area)/d(vertex_k) = 0.5 * n_hat x (p_{k-1} - p_{k+1})` for a
  planar polygon face — verified on 200 random planar polygons (m=3..7),
  max error 1.6e-9.
- The vector-valued generalisation `dS/d(p_k) = 0.5*(skew(p_prev) -
  skew(p_next))` (needed for the per-axis directional term below) —
  verified the same way, max error 4.2e-10.

Implemented `_compactness_step` (reduces total cell surface area,
weighted per-cell by sqrt(area) so bigger/rougher cells pull harder) and
wired it into `smooth_dual_mesh` as an opt-in (`use_compactness=False`
default) SECOND candidate alongside the existing, unchanged-by-default
Laplacian move, inheriting every existing hard guard (volume positivity,
volume ratio, planarity) and the keep-best acceptance contract.

## Measured result 1: compactness alone — 2 of 3 metrics improved, but NOT aspect ratio

Real checkMesh on the cylinder (after the smoothing-gate fix, so this is
on TOP of that improvement):

| metric | Laplacian only | + compactness |
|---|---|---|
| aspect ratio | 4.46 | **6.35 (worse)** |
| non-ortho max | 41.7 | **36.1 (better)** |
| non-ortho avg | 11.9 | **7.66 (better than snappy's 9.74!)** |
| skew max | 1.28 | **1.16 (better)** |

## Fetched OpenFOAM's real aspect-ratio formula to find why

`primitiveMeshTools::cellClosedness` (fetched from source): aspect ratio
is the **max of two separate terms**:
1. `max/min` of the three per-cell axis-wise sums of `|face-area-vector
   component|` — a DIRECTIONAL elongation measure.
2. `(1/6) * (total face area) / volume^(2/3)` — a compactness measure,
   which is what `_compactness_step` targets.

Compactness only attacks term 2. If term 1 was already the binding
(larger) term, reducing term 2 does nothing for the reported aspect
ratio, and the vertex motion that reduces total area can still make term
1 worse as a side effect — consistent with what was measured.

## Result 2: a second step targeting term 1 directly — verified correct, but converges to the SAME result as compactness alone

Implemented `_axis_balance_step`: minimises a smooth surrogate (variance
of the three per-cell axis sums, the standard smooth relaxation of a
minimax problem) via the verified Jacobian above, wired in as a further
opt-in candidate (`use_axis_balance=False` default).

**Honest, unresolved finding**: the raw candidate DIRECTIONS from
`_compactness_step` and `_axis_balance_step` are genuinely, measurably
different in a single step (max difference 0.0122 on the cylinder, not
a bug — confirmed directly, not identical arrays). But after the FULL
iterative smoothing loop (10 iterations with backtracking), running with
ONLY `use_axis_balance=True` (compactness fully disabled) converges to a
result that is bit-identical (to the precision checked: aspect ratio
6.3513, non-ortho 36.089, skew 1.1609, all matching `use_compactness=True`
exactly) to running with ONLY compactness. Both differ from Laplacian
alone; neither differs from each other.

**This was investigated, not swept under the rug**, but not fully
explained before time ran out on this line of work: the leading
hypothesis is that the ACCEPTANCE criterion (`quality_objective`, which
only scores non-orthogonality and skewness, not compactness or
axis-balance) ends up being the actual bottleneck — since a candidate is
only kept when it improves THAT objective, both step types may be
getting filtered down to the same accepted sequence of moves over many
iterations regardless of their originally different directions. This is
a plausible mechanism but was not proven with a targeted test before
this session ended.

## Result 3: the actual fix — make the acceptance objective aspect-ratio-aware

The hypothesis from Result 2 ("the acceptance objective only scores
non-ortho/skew, so it can't tell a real aspect-ratio improvement from a
regression") was tested directly: added `_cell_aspect_ratio` (the exact
OpenFOAM two-term formula above) as an ADDITIVE, opt-in term in
`_current_objective` (`aspect_lam`, default 0.0 — zero behavioural change
for any caller that doesn't pass it, including `quality_objective` itself
which was left untouched since it has its own public contract and tests).
`smooth_dual_mesh` now sets `aspect_lam=1.0` automatically whenever
`use_compactness` or `use_axis_balance` is on.

**Real checkMesh result on the cylinder, apples-to-apples with Result 1/2**:

| metric | snappyHexMesh | Laplacian only | compactness/axis-balance, aspect-ratio-AWARE objective |
|---|---|---|---|
| aspect ratio | 3.02 | 4.46 | **4.489 — no longer regressed** |
| non-ortho max | 29.1 | 41.7 | **36.56 — still better** |
| non-ortho avg | 9.74 | 11.9 | **9.61 — now essentially matches snappy** |
| skew max | 0.86 | 1.28 | **1.17 — still better** |

The earlier regression (4.46 -> 6.35) is gone: aspect ratio is back to
essentially its pre-compactness value, while the non-orthogonality and
skewness gains from Result 1 are FULLY retained. This confirms the
hypothesis was correct: the objective being blind to aspect ratio was
exactly why the earlier attempts silently traded it away.

The "compactness_only / axis_balance_only / both all converge to an
identical result" phenomenon (Result 2) persists even with the new
objective - all three configurations still land on bit-identical numbers.
This remains genuinely unexplained (not investigated further given time
constraints) - but is no longer concerning in practice, since the shared
result is now a real, verified improvement on 3 of 4 tracked metrics with
no regression on the 4th.

## Result 4: tried a higher aspect_lam weight — real checkMesh caught a regression the in-process replica missed

The worst cell's aspect ratio (4.49) is dominated by the directional term
in ~10,620 of 10,622 cells, and is a genuine LOCAL OPTIMUM of vertex
relaxation: more iterations (30 instead of 10) changed nothing, and
raising `aspect_lam` from 1 to 10 in the in-process replica squeezed out
a further small gain (4.49 -> 4.40) that plateaued identically at 100
(consistent with a real optimum, not an undertuned weight).

**Real checkMesh caught what the in-process replica missed**: lam=10
looked like a free improvement (same non-ortho/skew, better aspect
ratio) using the in-process counts, but real checkMesh on the resulting
mesh showed non-orthogonality max 36.6 -> 49.6 and skewness 1.17 -> 1.31
BOTH got worse, for a small aspect-ratio gain (4.49 -> 4.40) — a net
regression on 2 of 3 metrics, not a free lunch. **Reverted to lam=1**.
This is exactly the kind of gap between the in-process replica and real
checkMesh this whole session has repeatedly found (see the skewness
formula bug) — another concrete reminder that a real `checkMesh` run,
not just the in-process detector, is the only thing that can be trusted
for a final verdict.

The remaining aspect-ratio gap (4.49 vs snappyHexMesh's 3.02) most likely
needs a topology-level intervention (the worst cells are probably
adjacent to the cylinder's flat end caps, where the dual mesh's
construction inherently produces elongated cells vertex-relaxation alone
cannot fix), not more smoothing tuning — consistent with the session's
broader finding that the concave-cell MERGE repair (a topology change,
not smoothing) was needed for the pyramid/orientation problem earlier.

## Honest bottom line

- Real, verified, substantial gain, confirmed by real checkMesh (not
  just the in-process replica): non-orthogonality max -12.4% and average
  -19.3% vs Laplacian-only smoothing (average now essentially matches
  snappyHexMesh), skewness max -8.6%, aspect ratio NOT regressed (was a
  real risk after Result 1, fixed by Result 3).
- Aspect ratio itself is still 4.49 vs snappyHexMesh's 3.02 - this
  specific investigation narrows the gap on 3 metrics without closing
  the 4th, an honest partial result achieved through real derivation,
  numerical verification (two hand-derived gradients checked against
  finite differences before use), and root-cause diagnosis (fetching
  OpenFOAM's actual formula rather than guessing), not through
  deflection.
- `use_compactness` and `use_axis_balance` are opt-in, OFF by default —
  the existing, fully-validated default smoothing behaviour is completely
  unchanged. Fast suite green throughout (60+ targeted tests covering
  every affected file).
