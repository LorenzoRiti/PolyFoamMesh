# Sizing And Estimates: predicted (geometric) vs actual cell count

Date: 2026-09-09. Written BEFORE the measurement. The section
`docs/residual_risks.md` "Sizing And Estimates" reports: "The Mesh
Fineness slider value is a budget/cap (10K..20M cells), not a
guarantee. The displayed geometry estimate is derived from the
tessellated solid volume and the derived cell sizes; when the volume
cannot be trusted (open/non-watertight tessellation) the estimate is
omitted rather than invented."

That is: the Mesh Fineness slider estimate is a *budget* and an informational
label, not a guarantee. It is based on the geometric formula
(`estimate_cell_count_geometric` in `core/geometry.py`) and on the patch
sizes from `compute_patch_cell_sizes`. The computation of the predicted cell
count is a weighted sum (bulk + boundary refinement) that does not
capture everything the mesher does (e.g. packing factors,
gradation, boundary layer inflation).

## Measurement

`tools/sizing_estimate_rebench.py`:
1. Builds the GMSH cylinder at 3 fineness levels
   (coarse/medium/fine).
2. For each level: computes the prediction via
   `estimate_cell_count_geometric(meshes, volume, max_cell, min_cell,
   patch_sizes)`.
3. Counts the actual cells from the written polyMesh.
4. Prints the pred/actual ratio.
5. Builds a second case with `tessellate_patches` of a cube
   WITH a hole (non-watertight) and checks that the estimate
   function refuses (returns empty, "omitted") — this is the
   "omitted rather than invented" case of the section.

## What I expect

- The prediction is an order of magnitude (cf. the historical comment
  in the code "estimate_cell_count: ... (a few boundary-layer-ish
  cells)"). For a dense cylinder (medium, h ~0.005 m, L ~2 m, r=0.5
  m) the bulk ~ volume / h^3 ~ 6.3 / 1.25e-7 ~ 5e7 cells (absurd);
  the real formula is weighted with patches and "near-wall refinement".
  We will measure directly for the cylinder, without a priori
  assumptions. I predict: the pred/actual ratio will be between 0.3 and 3.0 on the
  three levels (i.e. an order of magnitude), which is already
  informative on the trade-off "budget/cap, not a guarantee".
- Non-watertight case: the formula returns None/empty; no invented
  number, as documented.

## Reading criterion

- If the prediction is within ~2x (typical for regular meshes), the
  "budget/cap" trade-off holds; I document a ratio per level
  to give product/UX a datum on where the estimate deviates.
- If the deviation is 3x or more, signal of a formula that does not
  capture some effect of the mesher (e.g. growth near features,
  packing); I document and propose: either a fallback to a more
  conservative estimate, or a warning log when the pred/actual deviation
  at the first actual run is high (if measurable at runtime), or
  simply a fix to the computation (in the formula, not to the default).
- No default changed for the measurement alone.

Working dir: `C:/polybench2/sizing_estimate_rebench/`.
