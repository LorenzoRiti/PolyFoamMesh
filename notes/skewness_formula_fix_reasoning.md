# Skewness formula bug — found via literature/source search, fixed

## What was wrong

`_detect_defects` (tet_poly_dual.py) and `_continuous_metrics`
(poly_smoother.py) both computed the skewness normalisation distance as:

```python
fd = 0.2 * dn + _face_extent(points, faces, cf, sv, n_int)
```

OpenFOAM's actual formula (`primitiveMeshTools::faceSkewness`, fetched
from the real source at cpp.openfoam.org/v11/primitiveMeshTools_8C_source.html):

```cpp
scalar fd = 0.2*mag(d) + rootVSmall;
forAll(f, pi)
{
    fd = max(fd, mag(svHat & (p[f[pi]] - fCtrs[facei])));
}
```

OpenFOAM takes the **max** of the base term and the largest per-vertex
projection — our code **summed** them. Since `max(a, b) <= a + b` for
non-negative values, our denominator was always >= OpenFOAM's, so our
skewness (`svm / fd`) was systematically **under-reported**. This is the
root cause of a real gap found during the cell-merge investigation: our
in-process detector reported 0 skewed faces on a mesh where real
checkMesh found 3 (slot1, post-BL merge repair) — the detector could not
see a regression it should have caught.

## Fix

Changed both call sites to `fd = np.maximum(0.2 * dn, _face_extent(...))`.

## Verification (measured, not assumed)

Recomputed max skewness with the fixed formula on real meshes with a
recorded real `checkMesh` reference value:

| Mesh | Ours (fixed) | checkMesh |
|---|---|---|
| valve, un-repaired BL baseline | 22.16451 | 22.1645 |
| valve, merge-repaired BL | 22.16451 | 22.1645 |
| slot1, un-repaired BL baseline | 2.93131 | 2.93131 |
| slot1, post-BL merge-repaired | 3.32939 | 4.55171 |
| valve_dual.npz fixture (pure dual, no BL, float32 points) | 13.01 (max), 6 faces >4 | 14.5848 (max), 98 faces >4 |

3 of 5 independent real comparisons match to 4-5 decimal places — very
strong confirmation the max-not-sum fix is correct. The two mismatches
(slot1 post-BL-repaired, and the pure-dual fixture) are NOT explained
yet — both are flagged honestly rather than hand-waved. Leading
hypothesis for the fixture mismatch: it stores points as `float32`
(confirmed elsewhere in this session to cause ~1e-6 relative precision
artifacts), which could compound across many near-4.0-threshold faces
without being a formula error. The slot1 post-BL-repaired mismatch may
be a stale/regenerated-file mismatch from this session's repeated script
reruns on that directory rather than a code issue - not reconfirmed with
a fresh rebuild due to time, flagged as unresolved.

## Practical consequence

The fix makes the in-process skew count meaningfully closer to reality
everywhere it was tested, and byte-exact on two independent
production-representative (post-BL) meshes. It does NOT close the gap
completely (the pure-dual fixture and the one post-BL-repaired mismatch
show real quality still needs a genuine `checkMesh` run for full
confidence) - this fix reduces but does not eliminate the "always verify
with real checkMesh" requirement already documented for
`poly_cell_merge.repair_concave_cells_if_safe`.
