# Lane A — RAM crash root cause: prediction BEFORE measurement (2026-09-15)

## Prediction (written before any new code or load test)

Primary suspect: NOT one dict, but several whole-mesh Python structures alive
at the same time during `concaveClosure` (decoupled_vertex BL + post-BL merge):

1. `faces` as list-of-lists (`foam_mesh_io.read_faces`): ~15M faces on a 5M
   hex mesh, each a Python list + non-cached ints. Expected order: ~200 B/face,
   i.e. multi-GB by itself.
2. `_build_precompute.vert_faces`: vertex -> incident faces for EVERY vertex
   (built over ALL faces, not just wall vertices). Total entries = sum of face
   sizes (~60M on 5M hexes). Expected to rival or exceed `faces` itself.
3. `_build` output copies: `new_faces`/`new_own`/`new_nb` duplicate the whole
   mesh plus prisms, while the input mesh is still alive.
4. Merge repair validation recomputes full-mesh metrics (`_cell_metrics`,
   `_skew_count`) on both pre- and post-merge meshes.

Expected isolation result: `decoupled_vertex` alone (BL precompute + build) is
the larger residual RAM consumer vs merge repair alone; `merge_cell_groups`'s
`for c in range(n_cells)` is a TIME bottleneck (pure Python renumber loop),
not the RAM killer — but vectorizing it is still worthwhile once RAM is fixed.

Falsification: if sampled RSS shows merge repair alone dominating, or the
5M-cell `faces` list itself already exceeding ~8GB with nothing else alive,
then the representation (list-of-lists faces) is the structural problem and
point vectorization is insufficient — escalate instead of patching blindly.

## Measurement protocol (to be filled with real numbers)

- Synthetic structured-hex box generated locally (no user geometry).
- Sampled `psutil.Process().memory_info().rss` around: mesh materialization,
  (a) decoupled_vertex BL alone, (b) merge repair alone on an existing BL mesh,
  (c) both together.
- PASS bar: real ~5M-cell run, peak RSS <= 16GB, root cause + before/after in
  this note, safety threshold updated only to the measured-safe value.

## Results (pending)
