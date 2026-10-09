# H4 generalization: decoupled_vertex on cube, groove1, slot1

Date: 2026-09-09. Written BEFORE the measurement. H4 validated
`local_termination="decoupled_vertex"` on the collapsed valve (success,
0 added defects, 4,008/42,130 faces excluded, 75,360 prisms) and on the
healthy cylinder (no-op identical to "decoupled", 0 exclusions, 74,808
prisms, Mesh OK). Cube duct, groove1, slot1 remain to be measured.

## Predictions (same method as FASE 3/9)

- **Cube duct** (9 cells, tessellated in-memory): no input defect.
  `decoupled_vertex` on a healthy cube = no-op identical to
  `decoupled`/`standard` (no defect to exclude -> vertex-closure
  stays empty). I predict: 108 prisms, 0 exclusions, scale 1.0,
  Mesh OK, ~0s. Identical to what was measured in FASE 3/9.
- **groove1** (682 cells, pyr_input 4, low_fade 2): the per-face
  cascade (decoupled/standard) discarded 0 additional exclusions and
  succeeded at scale 0.1 with all the residual defects (1,062 prisms =
  3 × 354 wall faces). With vertex-mode, I expect
  *equivalent or slightly better*: the vertex-closure covers the rim
  but on groove1 the cascade with 4 input defects was already at the minimum. I predict
  that `decoupled_vertex` makes 0 additional exclusions and succeeds at
  scale 0.1 with ~10,062 prisms, scale 0.1 (identical to "decoupled");
  or scale 1.0 if the vertex-closure touches enough to close the
  remaining ones (perhaps 0 exclusions anyway).
  Numerical prediction: 10,062 prisms (1,062 in the predictor), 0 additional
  exclusions, Mesh OK, scale 0.1.
- **slot1** (9,372 cells, pyr 42, low 17): in FASE 3 with `decoupled`
  scale 1.0 round 1 gave 0 exclusions (132 exclusions collected in the round,
  equivalent vertex-closure) and 79,614 prisms Mesh OK. With
  `decoupled_vertex`, the per-vertex closure on the same input
  *might* close the cascade in a single round (more excluded faces
  compared to per-face). I predict: 0 exclusions, ~79,614 prisms
  (identical to "decoupled"), scale 1.0, Mesh OK. or scale 0.1 with
  one fewer iteration. In any case: same geometry, same
  checkMesh, same prisms (~79,614).

## Why

The vertex-closure on input without additional defects (cube) and on input
with minimal "rim" defects (groove1, slot1) closes at the fixed point
identical to the face-mode. The valve was the only case in which the
vertex-closure changes the fixed point: there the 373 vs 370 of the face-mode
were covered by the rim at 0.1. On groove1/slot1, the cascade was
already flat (0 additional exclusions), so the vertex-closure adds no
information.

## Reading criterion

- If H4 confirms on all 3: the mode `decoupled_vertex` is
  the recommended default option for the production collapsed BL
  on all geometry types (when the user enables it). Documented
  in `docs/residual_risks.md`.
- If H4 on some geometry is equivalent to `decoupled` or slightly
  worse: document and flag it.
- If H4 fails on some geometry: document and understand why
  (perhaps the rim is "stale" and the per-vertex propagation changed
  something unintentionally).

In any case, **no default changed** in this session
(`local_termination=False` by default). All results are
measured, never assumed.

Working dir: `C:/polybench2/h4_generalization/`. For the existing tet backups
(groove1, slot1) I reuse `C:/polybench/{groove1,slot1}/...`.
