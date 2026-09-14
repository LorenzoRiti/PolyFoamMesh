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

## Results (measured 2026-09-15, `tools/ram_isolate_bl.py` + `ram_isolate_valve.py`)

Peak RSS per stage (MB, sampler thread @20Hz; baseline ~376MB of imports):

| mesh | cells | faces | mesh | metrics | precomp | build | validate | merge |
|---|---|---|---|---|---|---|---|---|
| hex 32³ | 33k | 101k | 406 | 444 | 423 | 694 | 524 | 552 |
| hex 48³ | 111k | 339k | 463 | 608 | 518 | 1515 | 771 | 873 |
| hex 64³ pre-fix | 262k | 799k | 570 | 1039 | 694 | 2905 | 1449 | 1520 |
| hex 64³ post-F1+F2 | 262k | 799k | 571 | 758 | 693 | 1355 | 1007 | 1481 |
| hex 64³ post-all | 262k | 799k | 571 | 742 | 620 | 1310 | 972 | 1454 |
| hex 200x160x156 | 4,992k | 15.06M | 4852 | 6097 | 4409 | **13049** | 9959 | **10592** |
| dual valve x1 | 152k | 1.24M | 793 | 1182 | 1120 | 2793 | 2024 | 2515 |
| dual valve x5 | 760k | 6.21M | 2989 | 3004 | 4116 | 10507 | 7159 | 8963 |

Overall 5M-hex peak: **13.05GB (build) <= 16GB bar. PASS.**

Root causes found (all confirmed by before/after, not assumed):
1. `_build_face_parity_graph` (bl_poly): 5 int64 occurrence arrays over
   2x sides + stacked group key + lexsort scratch + dict-of-tuples
   adjacency (~150 B/side). Extrapolated 5M-hex: ~12GB in this function
   alone. Fix F2: cell-range batched grouping (groups never split: every
   (edge,cell) group belongs to one cell) + CSR adjacency (~10 B/side).
   Reference-equivalence tests green (pairs, parities, incompat counts,
   valve-dual winding outcome).
2. `_face_geometry` transients (bl + tet variants): dense
   (n_faces, max_len[, 3]) temporaries, ~5-7GB per unchunked 15M-face
   call, invoked dozens of times per run. Fix F1/M: 250k-face blocks,
   bitwise-identical output (new chunk test + full suites green).
3. `vert_faces` over ALL vertices (~2GB @5M). Fix F3: wall vertices only
   (sole lookup site verified) — exact, lookup set unchanged.
4. Winding solve re-copied every face list. Fix: flip in place, copy only
   flipped faces (input exclusively owned by `_build`; no downstream
   in-place mutation — verified).
5. Merge floor 18.1GB even on clean meshes: tet `_face_geometry` +
   `_face_extent` dense bucketed temporaries in the skew gate. Fix M
   (chunked): merge 18.1 -> 10.6GB @5M-hex.

(a)/(b)/(c) isolation: (a) standard single build == one decoupled_vertex
round in peak class (precompute held + one built mesh at a time; rounds
are sequential, temporaries freed per round — same 13.0GB class);
(b) merge alone on the built mesh (production-faithful: input freed, as
openfoam_runner re-reads from disk): 10.6GB; (c) both = max = 13.05GB.

Topology derating (measured, two dual points): dual meshes carry ~8.2
faces/cell (valve fixture) vs ~3.05 hex; build slope ~1.0KB/face dual vs
~0.78KB/face hex. Dual-5M cells (~41M faces) projects to ~50GB — beyond
any point-fix: the list-of-lists face representation itself is the floor
(~0.3KB/face input). A compact-face (offsets+labels) end-to-end rewrite
is the structural fix, scoped as separate future work, NOT attempted here.

Threshold (measured, not eyeballed): guard moved from 1.5M cells to
**8M faces** (`openfoam_runner.py`, both BL and merge sites; faces from
the converter result, legacy cell rule only when faces unknown). 8M faces
projects to ~10.8GB peak on dual topology (two-point fit, 5GB margin to
the bar) and hex meshes peak far lower there (measured 15M -> 13.0GB).
Dual-5M stays skipped: no crash possible through this path.
