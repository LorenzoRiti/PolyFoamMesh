# W11 - multi-patch and zero-config: diagnosis (2026-10-23)

Scope: trace how physical regions become OpenFOAM patches, where regions
were lost, how BL/report/view consume them, and what must change to make
the default multi-region path work without manual mapping. Inputs:
`mesh_converter.py`, `foam_mesh_io.py`, `boundary_reader.py`,
`patch_roles.py`, `bl_cells.py`, `bl_poly.py`, `openfoam_runner.py`,
`viewer_widget.py`, `case_setup.infer_patch_roles`, R1 and the existing
multi-patch tests.

## 1. Current pipeline

1. `.msh` -> meshio (`mesh.cells`, `cell_data["gmsh:physical"]`,
   `field_data`).
2. `mesh_converter.msh_to_of_polymesh()`:
   - records a physical tag per 2D surface element (`surface_face_tag_map`);
   - records a physical tag per 3D cell (`cell_phys_map`);
   - extracts faces, marks shared faces internal, boundary faces get a tag;
   - builds patches and writes polyMesh.
3. `foam_mesh_io.read_polymesh()` reads `points/faces/owner/neighbour/
   boundary`; `boundary_reader.parse_boundary` gives patch names/types.
4. `case_setup.infer_patch_roles` / `patch_roles` classify wall vs
   inlet/outlet/symmetry; `poly_bl_patch_selection` chooses BL patches.
5. `PolyBoundaryLayerEngine` extrudes per selected patch name;
   `boundary_layer_report` aggregates coverage/h1/y+ per patch.
6. Viewer `read_openfoam_mesh_patches` builds one PolyData per patch and
   the patch table/colors use the same boundary names.

## 2. Root causes found (not downstream filters)

### 2.1 Untagged 2D face shadows the 3D region tag

In Phase 1, every non-3D element was stored in `surface_face_tag_map` even
when its physical tag was **0**. In Phase 2 the boundary tag was
`surface_face_tag_map.get(key, cell_phys_map.get(owner, 0))`; because the
key existed with value 0, the fallback never fired. A multi-region `.msh`
with only 3D physical groups (a common Gmsh export) therefore collapsed all
boundary faces to `surface_0`: regions lost, BL skipped, viewer/report
disagreed. This is the W11 root defect.

### 2.2 2D and 3D physical tags shared one name dictionary

`tag_to_name[tag] = name` was dimension-blind. A physical surface "wall"
tag 1 and a physical volume "fluid" tag 1 overwrote each other; whichever
appeared last named both. The first fix separated the name maps, but the
boundary *bucket* was still keyed by the numeric tag, so a surface `wall`
tag 1 and an untagged `fluid` volume tag 1 still merged into one patch.
The final converter groups boundary faces by the **resolved name**, not by
the bare tag; this is the only identity OpenFOAM actually stores.

### 2.3 Boundary `neighbour` placeholders corrupted `n_int`

`mesh_converter` builds a face-aligned neighbour list with `-1` for boundary
faces. `foam_mesh_io.write_polymesh` wrote those placeholders verbatim.
`read_polymesh` then returned `n_int == len(neighbour) == n_faces`, so
`_select_faces` computed `n_bnd = 0`, `poly_bl_patch_selection` found names
but BL saw no boundary faces, and the report returned `{}`. OpenFOAM's
`neighbour` list must contain internal faces only. This was the reason the
synthetic two-region case selected `region_a` but built zero prisms.  The
writer now filters placeholders and the reader normalises old files by
dropping a boundary-placeholder tail; a negative value before a valid
neighbour is rejected as corruption instead of silently shifting faces.

### 2.4 Duplicate/empty/ghost names

Two physical groups can sanitize to the same OpenFOAM word ("wall-a",
"wall a"); the old converter emitted duplicate patch names. A tag 0 with
no faces produced a `surface_0` patch/ghost. Zero boundary faces produced a
fake `default` with `nFaces=0` (viewer row with no geometry). Names were
not sanitized for OpenFOAM words at all.  A face used by more than two
cells was silently handled as a boundary face on its third occurrence; a
non-manifold input now fails before writing, with the face and cell in the
message.  Every boundary face is counted exactly once and patch `nFaces`
must sum to the boundary face total.

### 2.5 Report hid non-BL caps

`boundary_layer_report` iterated only `_wall_patches()`: R1 has 7 patches,
but the report listed 5 (surface_2/surface_7 are caps). The viewer showed
7. That is a report/view divergence and looks like lost patches.

### 2.6 GUI had a second wall classifier

`main_window._is_wall_patch` was a local blocklist containing bare
`"pressure"`/`"velocity"`; it classified a blade pressure side as non-wall,
while `patch_roles` deliberately does not. This is the zero-config split
that W11 has to remove.

## 3. Real/synthetic evidence

- R1 (`w10_1_r1_policy`): 7 boundary patches; 5 selected wall patches;
  5-layer BL; 99.51% measured W11 coverage (99.47% W10.1 run); 0 pyramids /
  1 skew / Failed 1 (W10.1/W10.3).  `boundary_layer_report` now lists all
  7, with `bl_selected` false on the caps; h1 0.1155x requested, the same
  declared support cap.
- Synthetic two-region `.msh` (two disconnected/connected tets, 3D physical
  names `region_a`/`region_b`): before the fix untagged surfaces produced
  `surface_0`; after the fix the converter emits exactly two patches with
  4 boundary faces each. A mixed 2D "wall" + 3D "fluid" collision keeps both
  (`wall` 1, `fluid` 4, `solid` 3); duplicate sanitized names merge; an
  untagged mesh gets one `default` patch with all boundary faces; a
  three-cell face is rejected as non-manifold.
- Viewer `read_openfoam_mesh_patches` and report return the same patch
  names on the synthetic case.
- No real multi-region `.msh` was available in `C:\cfmesh_cases`; the
  synthetic case is the declared representative.

## 4. Design

Reuse, do not add a pipeline:

- `mesh_converter`: dimension-scoped physical-name maps, tag-0 fallback to
  the 3D region, sanitized names, duplicate names merged deterministically,
  every boundary face accounted exactly once, no empty/ghost patch.
- `foam_mesh_io.write_polymesh`: single writer filters `-1` placeholders
  from `neighbour`; this is the invariant every consumer already assumed.
- `boundary_layer_report`: list all patches with `bl_selected`; keep global
  coverage over selected wall faces; per-patch h1/y+; no duplicate names.
- `main_window._is_wall_patch`: delegate to `patch_roles.is_wall_patch`.
- Viewer already consumes boundary names; after the converter fix its
  patch table/colors and the report names agree.
- Zero-config: with no field_data, 3D tags auto-name `region_N`, untagged
  becomes `default`; no manual `boundary_patches` needed.

## 5. Reuse / remove

Reuse `read_polymesh`, `parse_boundary`, `infer_patch_roles`,
`patch_roles`, W4/W10 per-patch report fields and the existing viewer.
Remove the second GUI classifier; remove tag/name collisions and the
0-face placeholder from the converter. No second converter/dual/BL chain.

## 6. Load estimate and risks

Files: `mesh_converter.py`, `foam_mesh_io.py`, `openfoam_runner.py`,
`main_window.py`, two test files, docs. ~6-8 files, ~250-400 net lines.
Risks: a duplicate-name merge must preserve contiguous face blocks (the
converter now orders by resolved name); a real multi-region case can expose
group combinations not in the synthetic matrix; report caps now appear with
coverage 0 and must not trigger wall warnings.

