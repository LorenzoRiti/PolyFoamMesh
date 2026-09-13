# Poly cell merge — repair non-star-shaped ("face pyramid") cells

## The problem, precisely

`_cell_centroids` (bl_poly.py:148) computes the cell centre + volume with
the EXACT algorithm OpenFOAM's own `primitiveMeshTools::makeCellCentresAndVols`
uses. checkMesh's "face pyramids" check tests, for every face of a cell,
whether the pyramid formed by that face and the cell centre has positive
volume. On the production-collapsed valve dual mesh, 348 of 152,086 cells
(0.23%) fail this for exactly one face each (measured, not assumed — see
`docs/residual_risks.md`). We cannot change the centroid formula (it IS
checkMesh's own ground truth) and splitting the offending face doesn't
help (a planar face split into pieces keeps the same orientation problem).

## What was measured before touching any code

Tried merging each concave cell with the interior neighbour sharing its
LARGEST internal face: fixed 176/348 (50.6%). Tried ALL available interior
neighbours per cell, taking the first that works: fixed 302/348 (86.8%),
46 cells (13.2%) not fixable by a single-neighbour merge.

## Prediction for the implementation

Merging is a well-known, low-risk operation (conceptually the same as
OpenFOAM's own cell agglomeration for multigrid) — much safer than
inventing a novel cell-splitting geometry algorithm. The real risk is
BOOKKEEPING, not geometry: removing an internal face and renumbering every
cell/face index that comes after it, while preserving:
- owner < neighbour for every remaining internal face (reversing a face's
  vertex order when the merge flips which side is "lower"),
- patches unaffected in face ORDER (only owner id changes on boundary
  faces — no boundary face is added, removed, or reordered, so patch
  `startFace`/`nFaces` only need a uniform shift by the count of REMOVED
  internal faces, not a full rebuild),
- volume conservation (merging is a pure regrouping, not a geometry
  change — total volume must be identical to machine precision, since no
  point moves and no face's geometry changes, only which cell owns it).

I will verify this last point (volume identical to the pre-merge mesh) as
the primary correctness gate, plus a fresh `_pyramid_violations` count on
the merged mesh (must not exceed the pre-merge count, ideally close to
348 - 302 = 46), before ever considering real checkMesh.
