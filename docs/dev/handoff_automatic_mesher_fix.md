# Automatic mesher fix — state and continuation

Context: the user reported that the automatic mesher (the
"Polymesh"/GMSH path, the one "Automatic" selects by default after Reasonix's
rebrand in `839b020`) refines "randomly" — even in geometries where it
should not, and even when the user thinks they have not selected anything
automatic. They asked for: 1) a real ON/OFF switch, 2) that ON works
well (refines only where needed: curvature, edges, constrictions).

## Diagnosis (confirmed by three independent sources)

1. **The code**: `src/cfmesh_autogui/gui/params_panel.py`, the checkbox
   `_adaptive_sizing_check` was `setVisible(False)` + `setChecked(False)` +
   the comment "Superseded by the Mesh Fineness slider" — **dead**. In
   `main_window.py:_start_gmsh_volume_worker` the condition was
   `if adaptive or max_cells_target > 0:` and `max_cells_target` (derived
   from the Mesh Fineness slider) **is never zero** (`_slider_to_cells` maps
   0..20 to 10K..20M) → the adaptive path started ALWAYS, the checkbox
   was unreachable. There was no way to get manual/uniform sizing
   from the GUI.
2. **The official GMSH documentation**: "When mesh element sizes are fully
   specified by a mesh size field, it is often desirable to set
   `MeshSizeFromPoints = 0; MeshSizeFromCurvature = 0;
   MeshSizeExtendFromBoundary = 0` to prevent over-refinement." The code
   did the exact opposite: `_configure_adaptive_sizing` in
   `gmsh_wrapper.py` built explicit, well-designed fields (Distance/
   Threshold on small curves, Ball on constrictions, an a-priori bounded
   curvature field via `setSizeCallback`) and THEN left ACTIVE
   also GMSH's native curvature engine (`MeshSizeFromCurvature=1`),
   which has no idea which curve features matter for CFD — it requests N
   elements per circle EVERYWHERE, limited only by the GLOBAL
   `CharacteristicLengthMin`. A genuinely small feature in
   one spot of the CAD (a manufacturing fillet of 0.25mm on a 3m valve)
   lowers that global minimum, and the native engine then applies it to
   EVERY curved surface of the part, regardless of relevance. The
   properly made curvature-field callback (`min(lc, target)`) cannot
   protect itself: `min()` always takes the finer value, so the
   native engine (without a limit) always wins over our bounded field.
3. **Commercial practice** (Pointwise, ANSYS Fluent Meshing, cfMesh):
   "curvature-based" + "proximity/gap-based" local sizing are the two
   standard ingredients — which the code ALREADY HAD (small-curve
   detection in `_scan_feature_sizes`, gap detection in
   `_detect_surface_gaps`), only drowned by the native engine above.

A second defect, minor but real, found during the investigation:
`_scan_feature_sizes` classified as "small" the curve in the 25th percentile
of length **relative to the other curves of the part**, without an absolute
floor tied to the part scale. On a uniform geometry (a simple tube,
a straight duct — no real "large body / small fillet" structure) the
shortest quartile was still marked "small feature" and refined locally,
because a shortest quartile always exists by statistical construction.

## Fixes applied (committed in the working tree, not yet in a git commit)

Files touched:
- `src/cfmesh_autogui/core/gmsh_wrapper.py`
  - `_configure_adaptive_sizing`: disabled
    `Mesh.CharacteristicLengthFromCurvature`, `Mesh.MeshSizeFromCurvature`,
    `Mesh.MeshSizeFromPoints`, `Mesh.MeshSizeExtendFromBoundary` (all set to
    0). Sizing now depends ONLY on the explicit fields (Distance/
    Threshold/Ball + the a-priori bounded curvature field). The
    manual/explicit path (`if user_lc:`) was NOT touched — there
    GMSH's native curvature is normal, expected behavior for a
    min/max sizing without fields, and is not the source of the reported
    defect (it was simply unreachable before this fix).
  - `_scan_feature_sizes`: added an absolute cap
    `small_cutoff = min(small_cutoff, max_extent * 0.08)` — a curve is
    "small" only if it is also small in an absolute sense relative to the part, not
    only relative to the other curves. Does not alter the reference case
    (valve 3m/fillets 0.25mm: the quartile there is already well below 8%).
- `src/cfmesh_autogui/gui/params_panel.py`
  - The checkbox is now **visible**, in the "Mesh" tab under the Mesh
    Fineness slider (no longer buried in Advanced), **checked by default** (ON =
    recommended), with a tooltip explaining ON/OFF in Italian.
  - Removed the second dead checkbox in Advanced that overrode the real
    one with a hidden one (duplication bug found during the fix).
  - `_on_adaptive_sizing_toggled` now makes `_max_cell`/`_min_cell`
    (Advanced tab) **actually editable** when OFF, not just cosmetically
    greyed out — before they were always read-only and ignored anyway.
- `src/cfmesh_autogui/gui/main_window.py`
  - `_start_gmsh_volume_worker`: the condition is now `if adaptive:` (read
    from the real checkbox state), no longer `if adaptive or
    max_cells_target > 0:`. `max_cells_target` is passed to the worker
    ONLY when adaptive is ON (on the manual path it is not needed and is not
    read by `generate_volume_mesh`).

New tests (all green, no WSL/GMSH-real dependency):
- `tests/test_adaptive_sizing_curvature.py` — 5 tests: the native curvature
  engine is disabled on the adaptive path (with and without the a-priori
  field); the min/max limits stay set; a uniform geometry
  does not mark curves as "small"; a genuinely small feature stays
  correctly marked.
- `tests/test_slider_cell_sizing.py` — 2 tests added: the checkbox is
  real/visible/ON by default; OFF makes Max/Min Cell Size editable and
  ON puts them back read-only.

Verified: `pytest tests/test_adaptive_sizing_curvature.py
tests/test_slider_cell_sizing.py -q` → 13/13 green.
`python -m py_compile` / `ast.parse` on all three touched GUI/core
files → no syntax errors.

## Second fix (after a user screenshot with Polymesh on a bent bracket)

The user relaunched the app with the fixes above and showed a screenshot:
Poly mesh, `checkMesh` **PASS** with objectively good numbers (skew 3.10,
non-ortho 53.8 — better than the average documented in this project),
39,276 cells from 306,759 tet (ratio ~7.8×, consistent with the barycentric
dual), count almost identical to the geometric estimate (~39K). The
budget therefore was NOT exploding — but visually the mesh on the
surface appeared uniformly "rough/grainy" everywhere, without a
visible gradient fine-near-the-bend / coarse-in-the-flat-plane.

Cause found (second real bug, independent of the first):
`DistMax` of the Threshold field on small curves was
`max(small_cutoff * 20, min_size * 40)`, **without any absolute cap**.
With the 8% cap of `max_extent` already applied to `small_cutoff` (first
fix), the worst case gave `DistMax = max_extent * 1.6` — **larger
than the entire part**. `DistMax` is the radius within which the field "ramps up" from
fine to `coarse_max`: if it exceeds the dimensions of the part, the ramp never
completes, and the whole part stays inside the "still fine" zone — hence
the absence of a visible gradient, even with a correct total cell
count.

Fix: `dist_max = min(max(small_cutoff*20, min_size*40), max_extent*0.15)`
— the influence zone stays a genuinely local neighborhood (15% of the
part), it can no longer swallow the entire geometry. Does not touch the
reference case (valve 3m/fillets 0.25mm: there the natural value is already well
below 0.45m).

**Collateral bug found while applying this cap**: `DistMin` (computed
as `max(small_cutoff*2, min_size*4)`) can reach `max_extent*0.16` in the
same limiting case — **larger** than the new `DistMax` cap
(`max_extent*0.15`), which would invert the two field parameters
(DistMin > DistMax, invalid). Added `dist_min = min(dist_min,
dist_max * 0.5)` to guarantee the correct order in every case.

Tests added in `tests/test_adaptive_sizing_curvature.py`
(`test_dist_max_never_exceeds_a_local_neighbourhood_of_the_part`):
verifies that `DistMax <= max_extent*0.15` and `DistMin < DistMax` in the
limiting case (small_cutoff at the cap). 6/6 green together with the other tests of the
first fix (total 14/14 with `test_slider_cell_sizing.py`).

**Not yet verified**: whether this second fix REALLY solves
the visual appearance complained about by the user — a new run on the same
geometry (the bent bracket from the screenshot) is needed to see whether a
visible gradient now appears (fine only near the bend, coarse in the flat
panels). I do not have the CAD file to reproduce it autonomously. **If the
problem persists after this fix**, the next suspect is the
"cells_across" rule (bulk sizing from the cross-section, not from
local features) — ask for the full log (`Show Log` in the app, line
`Adaptive sizing: min=... max=... small_curves=.../... gaps=...
surfaces=...`) to have the real numbers instead of hypothesizing.

## Third fix — the viewer, not the mesher (probable real cause of the screenshot)

The user explicitly asked: "I would like to see the mesh as surface
with edges but without the tet inside, only the poly visible". Checking
`src/cfmesh_autogui/gui/viewer_widget.py` I found that **both** "Volume
Mesh" **and** "Surface + Edges" loaded the SAME `internal.vtu`
(the entire volume) and drew it with `style="wireframe"` on ALL
cells — not just the outer boundary. Looking at the mesh from outside, the internal
edges of ALL the cells of the volume appear overlaid in screen projection
(in the screenshot's case, 39,276 cells) — this produces
exactly the "noise/grain" effect seen in the screenshot,
**regardless of how well graded the mesh is**. It is very
likely that this, not the two previous sizing fixes, is the real
cause of the complaint — a perfectly graded mesh (fine only
near the bend, coarse in the flat panels) would still have
that appearance if viewed with this rendering mode.

Fix: new static method `ViewerWidget._prepare_internal_vtu_grid(grid,
mode)` in `viewer_widget.py` — when `mode == "surface_edges"`, it applies
`grid.extract_surface(algorithm="dataset_surface")` (VTK's
`vtkGeometryFilter`) BEFORE the wireframe: it keeps only the external boundary faces, and
keeps them as real polygons (a pentagonal face of a poly cell stays
a pentagon, it is not triangulated — verified: a pentagon/quad never
becomes a triangle). "Volume Mesh" stays unchanged (it exists on purpose
to inspect the interior).

Verified with an analytic fixture (2 adjacent unit hexahedra,
sharing one face): 2 volume cells → **10** exact boundary faces
(12 total − 2 shared, correctly excluded), all quads, zero
triangles. `algorithm="dataset_surface"` explicitly pinned against a
PyVista FutureWarning about the default changing.

Test: `tests/test_viewer_surface_only.py` — 3/3 green, no dependency
on the bundled sample dataset (fixture built by hand, deterministic).

**Not yet verified**: the real rendering in the GUI (a real run is needed
with `Show Mesh` → "Surface + Edges" to confirm visually). The logic
is isolated and tested, the final on-screen appearance is not.

## Fourth fix — the real bug: the initial dispatch ignored the drop-down menu

After the third fix (`extract_surface` + `style="surface"` for "Surface +
Edges") the user relaunched the app and saw the internal tet/cells **still**
visible. The cause was not in the rendering I had just
fixed — it was a second bug, independent, in the path that decides
WHAT to draw on the first load after meshing:

- `show_mesh()` sets the drop-down menu to "Surface + Edges"
  (`_view_selector.setCurrentIndex(...)`, with `blockSignals(True)` to
  avoid a double trigger) and then schedules `_delayed_display_mesh()`
  via `QTimer.singleShot`.
- `_delayed_display_mesh()` called **always and regardless**
  `self._display_mesh()` — the "Volume Mesh" mode (wireframe
  of the entire internal volume) — **completely ignoring** what the drop-down
  menu showed. The comment in the code explained only why it does not
  use `_on_view_changed` (to avoid a double start of foamToVTK), but
  the direct call had remained hooked to `_display_mesh()` instead
  of `_display_surface_edges()` — probably a leftover of a
  previous version in which "Volume Mesh" was the default view
  (consistent with an old commit in the log: "fix: switch viewer to Volume
  Mesh automatically after meshing", then the default changed to
  "Surface + Edges" without updating this point).

Practical result: the menu showed "Surface + Edges" selected, but the
mesh EFFECTIVELY drawn was always the full wireframe of the
volume — the previous fix on `_display_surface_edges()` had no
effect because that method was never called on this
path.

Fix: `_delayed_display_mesh()` now reads `_view_selector.currentIndex()`
and dispatches explicitly to `_display_surface_edges()` /
`_display_cad()` / `_display_mesh()` accordingly, instead of calling
always `_display_mesh()`.

**Technical note**: the file `viewer_widget.py` contains pre-existing
mojibake (UTF-8 em-dashes doubly re-encoded, `â€"` instead of
`—`) in several comments — not caused by this session, but it made
the first edit attempt with the Edit tool silent/failed
due to a string mismatch. Resolved by writing and running a dedicated
Python script with explicit `\r\n` newlines (the file uses
CRLF) instead of relying on the edit tool's exact string
matching. I did not clean the pre-existing mojibake elsewhere in the file
(out of scope).

Test: `tests/test_viewer_delayed_display_dispatch.py` — 4/4 green.
Builds a "bare" `ViewerWidget` with `ViewerWidget.__new__(...)` (not
`object.__new__`, which PySide6/Shiboken rejects for `QObject`
subclasses) and verifies the correct dispatch for each of the 3 modes
plus the disabled-selector case.

**Verified with a real run** (not mock, not WSL — real GMSH +
real offscreen VTK rendering):

1. `generate_volume_mesh` on `sample_cad/cylinder_test.stl` (real GMSH):
   `Adaptive sizing: min=0.00010 max=0.00020 small_curves=0/2 gaps=0
   surfaces=3` → **0 curves marked "small" on a simple cylinder**,
   direct confirmation of the 8% absolute cap on real geometry, not just
   on the synthetic test fixture. Mesh generated without errors (11,335
   tet at "fine", 1140 at "medium").
2. `ViewerWidget._prepare_internal_vtu_grid` + `add_mesh(style="surface",
   show_edges=True)` on a real polyhedral mesh (5-face cells,
   triangles+quads, not hex/tet) → offscreen screenshot: full
   shaded surface, real edges visible, **no spurious
   triangulation diagonal**. Confirmed that 18 boundary faces (out of 6
   polyhedral cells, 5 faces each = 30 total − 12 shared) stay
   real polygons (`{3, 4}` vertices, not all triangulated to 3).

**Still not verified**: the true click-through of the desktop GUI (I have no
interactive desktop-control tools in this session) — the verification above uses
the exact same components (same GMSH, same
viewer static method, same `add_mesh` call) but not a run
of the user-packaged app.

## NOT done / to verify on resumption

1. **Full suite not yet finished**: `pytest tests/ -q -k "not
   bench"` was launched in the background (~00:50 start) and has not yet
   produced output after several minutes while actively consuming CPU
   (confirmed with `Get-Process`) — likely a slow collection phase
   (heavy imports: gmsh, cadquery, pyvista on ~100+ test files) rather than
   a hang. **On resumption: read the output of that command before
   declaring anything finished.** If failures emerge in the GUI
   files (`test_bl_gui_wiring.py`, `test_mesher_buttons.py`, any test
   that reads `_adaptive_sizing_check`/`_max_cells_target`/`_max_cell`/
   `_min_cell` by position or previous state), they are the immediate
   priority to fix before considering the fix closed.
2. **cfMesh (WSL) path**: checked `meshdict_gen.py` —
   `build_object_refinements` is purely opt-in (no zone = no
   refinement), it does not seem to have the equivalent of the always-on
   native engine. Not investigated further due to time limits; if the symptom
   "refines randomly" reappears even when explicitly selecting
   cfMesh (not Polymesh/Automatic), look there.
3. **Real visual verification**: no real mesh was launched (neither via
   GUI nor headless) on a test geometry to visually CONFIRM
   that the new behavior produces a sensible mesh (refined only
   near real features) — the tests cover the logic of the GMSH options and
   the GUI wiring, not the outcome on a real mesh. **Next concrete
   step**: take `sample_cad/` or a case in `C:/polybench`,
   generate with Automatic ON and OFF, compare visually/with checkMesh.
4. **Commit**: none of this has been committed yet (working tree
   shared with Reasonix — verify `git status`/`git diff` first,
   Reasonix may have touched `params_panel.py` again in the
   meantime for BL Phase 2).
5. I did not touch `commercial/mesh_engine.py`/`autopoly_bridge.py` — if
   "Automatic" also routes there for certain geometries (not verified
   how deeply it covers `_resolve_auto_mesher`), there could be
   a third point with the same "always-true flag" pattern to check.

## How to resume

```
cd "C:\Users\Davide Valoroso\cfmesh-autogui"
git status --short   # what is mine, what is Reasonix's, before touching anything else
# read the outcome of the full suite (if the bash process is still alive,
# wait for the notification; otherwise relaunch):
"/c/Users/Davide Valoroso/AppData/Local/Programs/Python/Python311/python.exe" -m pytest tests/ -q -k "not bench" --tb=short
```
