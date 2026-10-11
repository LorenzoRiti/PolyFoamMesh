# Changelog

All notable changes to PolyFoamMesh are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) and
the project follows [Semantic Versioning](https://semver.org/lang/it/).

## [2.3.0] - 2026-10-09

### Added

- **Direct OpenFOAM export** (menu Export Mesh → "OpenFOAM Case
  Folder..."): copies the case to a chosen folder, **without** the
  requirements of the BaramFlow export (which rejects cases with empty/
  incomplete 0/ fields). Exports mesh + system + fields if present: enough to
  launch it directly in OpenFOAM (WSL/Linux: `foamRun`,
  `simpleFoam`). The regenerable and heavy artifacts are left out
  (processor*, VTK_view, mesh.msh, tet backup).
- **Complete user manual in English** (`docs/USER_GUIDE.md`), rewritten
  for the current UI: workflow, viewer, boundary layer (with log
  reading), refinement, themes and troubleshooting.
- **Viewer**: deterministic Section Cut that slices in all
  representations; working Measure (two-click distance on CAD and
  mesh); unified ParaView-style representations (`Surface`, `Surface
  with Edges`, `Wireframe`); solid patch highlight; more polished mesh
  appearance (smooth shading, adaptive edges).
- **Workflow dock**: the static "Next step" item became **Next
  action**, an actionable suggestion that follows the real state (idle, in
  progress, finished, error).
- **BaramMesh-style patch splitting**: popup on import (STEP and STL)
  with "Feature angle" (5–179°) and a "Use CAD face names when
  available" checkbox; with the checkbox off the surface is split by
  angle into patches `surface_1..N`. **Patch names are editable**
  (double click) in the patch table, now moved to the bottom-left dock
  (single panel: the duplicated list in the Geometry tab was
  removed). The rename propagates to meshing, BL and export.
- **Linux support (phase 1 - core)**: the app now runs the
  OpenFOAM/cfMesh commands natively on Linux (`bash -lc` instead of `wsl.exe`),
  with a configurable bashrc via the `POLYFOAMMESH_OF_BASHRC` variable
  (default `/usr/lib/openfoam/openfoam2512/etc/bashrc`). App data
  in the XDG location (`~/.local/share/polyfoammesh`, override
  `XDG_DATA_HOME`) with legacy migration intact. `launch.sh` launcher,
  Linux PyInstaller spec (`PolyFoamMesh-linux.spec`) and CI jobs
  `build-linux`/`attach-linux` (the Windows job remains unchanged). Section
  "Linux (from source)" in INSTALL.md/.it.md. Windows behaviour
  verified **byte-identical** (18/18 checks on generated commands and
  scripts).
- **Tet mesh cache (skips GMSH) — AUTOMATIC**: when the inputs
  that determine the mesh do not change, the `.msh` from a previous run
  is reused (SHA256 fingerprint, copy into the new case dir, same
  completion of the pipeline) skipping the entire GMSH stage — the
  slowest one. No checkbox: it is always active. "Clear mesh cache" in the
  Tools menu, automatic prune (5 entries / 2 GB), all fail-soft. **Correctness
  note**: the GMSH stage "bakes" the layout of the layers near
  the wall into the `.msh` (BoundaryLayer field from nLayers/thickness/
  expansion) and applies the wall-cell floor as a size field: these
  inputs are part of the fingerprint, so changing the BL parameters
  invalidates the cache (correct); the other BL settings (apply-to-all,
  filters, budget) stay excluded.
- **Boundary layer visibility in the viewer**: "Show BL" checkbox that
  classifies the prism cells (seed from the wall faces + shape test +
  multi-layer flood) and colours the BLs orange over the grey core.
- **Log during meshing**: the log panel created ONE single, unbounded
  HTML block → quadratic re-layout (10k lines = 29 s, 50k > 300 s) and
  GUI lag "for no reason". Now batching on a timer, real blocks
  (the row cap actually works), coalescing of repeated lines
  ("Improving..." collapsed with a counter, "still running" replaced):
  50k lines in ~0.24 s. No log mirror existed, no fidelity
  lost.
- **Linux support (phase 2 - GUI)**: the availability gate is now
  "OpenFOAM available (native or WSL)" (no longer WSL-only), the RAM probe
  uses the host RAM on Linux too, the case root no longer depends on
  `C:\`, process kill is native on POSIX, and all WSL-only
  messages/dialogs (OOM, GMSH memory, ParaView, "WSL not found") are
  platform-conditional — on Windows **byte-identical**.

### Fixed

- **BL aborted by a degenerate vertex in a refinement zone**: the BL
  precompute raised "wall vertex has no interior support" on the
  micro-sliver vertices that local refinement can create (measured: a
  13 um sliver with a single incident cell), killing the WHOLE boundary
  layer.  Those vertices are now excluded like the other sliver classes
  (0 layers there, pinned, logged: "excluded N wall vertex(es) without
  interior support (degenerate/refined spots)"); the honest failure is
  kept only when the problem is systemic (>5% of wall vertices or >5000).
  Verified on the user's refined tube through the full worker chain:
  456,018 prisms, median 6, 97.7% coverage, real checkMesh Failed 1
  (aspect ratio on 1,192 cells only - pyramids 0, non-ortho OK, skew OK).
  Clean-mesh outputs are byte-identical (cone A/B hash-verified).
- **Dual fails on the production venturi (open cells at the throat)**: the
  dual face of a boundary primal edge that is coplanar with the surface
  had its orientation sign decided by round-off noise, so it could be
  emitted backwards and leave the dual cell combinatorially open (16
  cells, "relative closure error 1.6e-1 - the dual complex is not
  watertight" - the user's venturi could not be meshed at all).  The
  converter now detects open cells with the exact closure invariant it
  already trusts, and self-heals ONCE: the offending vertices are merged
  into their shortest-edge neighbour (venturi: 8 vertices merged, 24
  collapsed tets dropped, every boundary face keeps its original patch),
  then the dual is rebuilt.  Verified: the venturi dual completes in both
  boundary modes with real checkMesh watertight (only the known throat
  NO/skew class remains), and the full app path now runs dual + BL
  (70,488 prisms, 6 layers, 100% coverage).  On clean meshes the cleanup
  never fires: dual-only polyMesh SHA-256 identical on cone/valve/
  benchmark-venturi (orchestrator re-verified the cone A/B).
- **Adaptive sizing runaway (non-convergence) on some geometries**: with
  adaptive sizing ON, GMSH's 2D mesher could loop forever on curved
  surfaces (measured on the benchmark venturi: invalid elements growing
  2->6524 over 15 split rounds, max silent gap 251 s just under the
  300 s no-output watchdog, no `.msh` after 40+ minutes).  The watchdog
  now recognises the **non-convergence signature** (>=100 invalid
  elements, or >=8 split rounds with non-decreasing counts after 30 s of
  2D meshing) and kills the attempt within ~40 s; the robust retry
  (Delaunay 2D + **2D optimizer enabled on this path only** for quality)
  then completes - measured full pipeline on the benchmark venturi:
  valid mesh + real checkMesh within ~50 s total (was: hang, no mesh).
  If even the robust retry fails, a final **manual-equivalent sizing**
  attempt (sizes derived from the same target) guarantees a mesh, with
  an honest log.  Healthy runs never trigger the detector (max 6
  invalid / 4 splits measured).  Default paths (manual sizing, robust on
  hangs) are unchanged; the optimizer stays off elsewhere because it
  doubles-to-triples robust wall time on large STEP parts.
- **GMSH stall on large curved geometries (Parte4-class)**: the volume run
  could stall in the 2D phase (MeshAdapt looping on the last curved/plane
  surface) and the robust retry could not recover: it did not pin Delaunay
  per-surface and used HXT 3D, whose boundary recovery leaves a
  non-manifold primal the dual converter rejects.  Robust mode now pins
  **Delaunay 2D on every surface** and uses **Delaunay 3D** for STEP/OCC
  input (STL keeps HXT); the watchdog budget scales 300 s -> **900 s** with
  the estimated cell count and the heartbeat logs the GMSH process **CPU%**
  so a busy-but-silent run is visible instead of being killed as a hang;
  the final error message describes the real retry.  Measured on the
  user's geometry (Parte4.stp, medium): completes in **151.6 s** (was:
  stall + double kill); tube/valve1/Parte2 regression runs unchanged.
- **BaramFlow/direct export now ships a CLEAN case**: processor*, VTK_view,
  mesh.msh, the pre-BL tet backup, postProcessing, previous results
  (numeric time dirs; 0/ is preserved), local.cfg and *.foam are left out;
  the BaramFlow export also drops our system/decomposeParDict (BaramFlow
  writes its own - a stale one made its runs unable to write results).
- **Patch rename keeps the 0/ fields in sync**: renaming only
  constant/polyMesh/boundary left the fields listing the old name, so
  OpenFOAM/BaramFlow aborted on load and the BaramFlow export refused the
  case ("missing files" popup).  Every 0/ field's boundaryField entry is
  renamed too (one-time .bak per field).
- **Oversized popups**: Qt message boxes grew as wide as their longest
  line, leaving huge half-empty dialogs.  An application event filter
  wraps the text and caps it to 480 px.
- **BOUNDARY LAYER SKIPPED ON EVERY APP RUN (critical)**: the
  anti-double-BL guard counted dual cells with a prism shape and found
  ~2,000 **coincidences** on a dual without BL (measured: 2012/24,286 =
  8% on a real cone) → the worker believed the BL already existed
  ("GMSH-baked BL detected") and **skipped the BL stage on every geometry**.
  The guard now requires real coverage: ≥ 50% of wall faces
  with a prism-shaped owner (measured: 0.14 without BL vs 0.89 with BL).
  **Verified end-to-end on the user's real cone** (worker path):
  5 layers on 100% of the wall, 69,615 prisms, checkMesh **Mesh OK**
  (NO 53.5, skew 1.54, pyramids 0).
- **Face/patch deselection in the viewer**: clicking the empty
  background now **really deselects** (the old code used
  `pick_mouse_position`, which on an empty click returns the picker's
  *stale* actor: "nothing hit" never occurred) and
  clicking the highlighted patch again **deselects** it (toggle). Also removed
  the fallback on `picked_actor`, which re-selected the previous patch
  on every empty click.
- **Double Cancel button during a run**: `set_meshing_state(True)`
  still relabelled the "Generate Mesh" button as a red "Cancel"
  (a leftover from when the dedicated Cancel did not exist), so during
  meshing **two** were visible. Now the run button stays "Generate Mesh"
  (disabled) and the only stop is the dedicated Cancel under "Reset All".
- **Dual quality on complex geometries (opt-in, keep-best)**: new
  `repair_concave_cells_keepbest` (merge of concave cells with a
  **maximum-severity** gate: accepted only if the maximum NO/skew do not worsen)
  + `compact_unused_points` (latent bug fixed: the merge left orphan
  points → checkMesh "Unused points" hard-fail; also fixed a
  corrupted `startFace` at round 2 that made checkMesh SEGFAULT).
  Measured with real checkMesh: **valve pyramids 340 → 46** (NO errors
  10 → 1), venturi 224 → 110; on the venturi the PASS bar is not reachable
  with merge/smoothing — the cause is **topological** (the dual with one cell per
  boundary vertex cannot be convex across the concave fold
  of the throat; tested: not even an 8.6× finer input changes it), so
  everything is opt-in, default byte-identical, corpus unchanged (slot1 protected
  by the severity gate). The smoother's anti-skew guard remains opt-in
  (trades NO for skew).
- **Multi-layer "consuming" BL - NOW DEFAULT**: the BL engine can
  consume layers of dual cells (bounded harmonic relocation of the
  interior vertices + per-edge anti-inversion guard + core sliver guard),
  so the full stack fits even where the first cell is
  thin. **Verified on the real pipe**: from **1 to 6 layers** on 100% of the
  faces, first layer = requested (true y+), checkMesh PASS (NO 56.75 vs
  60.50). To force the old behaviour:
  `POLYFOAMMESH_BL_CONSUME=0`. **Note**: the aspect ratio of the core cells
  adjacent to the stack grows (checkMesh accepts, 103 on the pipe).
- **Multi-layer BL on complex geometries too (venturi)**: on duals
  with sliver faces the consumption no longer switches off globally — the
  **sliver-affected** faces (sliver edge, vertex in a sliver cell, +2
  adjacency rings) are **excluded from extrusion** (0 layers there, cells
  pinned in the reflow: no skewed prisms) and the full stack runs on the
  healthy wall. The **inlet/outlet caps** are excluded geometrically
  (normal aligned with the flow axis + plane at the extreme bbox + planar
  patch ≥4 faces, + rim ring): they are not walls. **Verified on the
  real venturi** (real checkMesh, dual from the backup): 43,782 prisms,
  median **6 layers** on 60.7% of the wall (all the healthy part), first layer
  = requested, and checkMesh **better than the dual-only baseline**: pyramids
  **199 < 224**, aspect max **209 OK** (were 3182), NO/skew/neg
  identical. Pipe and corpus bit-identical.
- **Dual: mixed tet+prisms input with exact stitching**: the converter
  now accepts input meshes with prisms (from any source), preserves them
  verbatim in the final polyMesh and stitches the interface to the
  dualised core (median quads per interface triangle, side splits,
  no T-junctions). Verified with real checkMesh on synthetic meshes
  (Mesh OK). The worker skips the internal BL when prisms are already
  present (double-BL guard).
- **Verdict on the GMSH-"baked" BL**: impossible with GMSH 4.x — the
  BoundaryLayer field supports only 2D layers (3D was removed from
  GMSH 2.15.0) and aborts on curves adjacent to two surfaces; the only 3D
  route (extrudeBoundaryLayer) requires internal-kernel geometries,
  not imported STEP/STL. Verified empirically on venturi and
  multi_inlet: zero prisms in every configuration. The attempt stays
  behind `POLYFOAMMESH_GMSH_BL=1` (default OFF, fallback logged).
- **GMSH hang (Parte2/venturi) FIXED**: measured root cause — the
  MeshAdapt 2D (Algorithm=1) goes into an infinite loop when a non-uniform
  size field is installed on it on cylindrical surfaces/fillets; Delaunay
  (5) completes the same field in ~2 s. There is now a **robust mode**
  (`POLYFOAMMESH_GMSH_ROBUST=1`): OCC healing + Delaunay + no
  optimisation, and the **watchdog** activates it on its own: if GMSH does not
  produce output for 5 minutes → kill + robust retry once → if it
  still hangs, a clear error. Parte2 completes in **149 s** (702k nodes,
  valid .msh); default path bit-identical on the cases that work.
- **Degenerate prisms rejected**: a BL build with a first layer < 5% of the
  requested one (venturi: 80 nm!) produces sub-micron pancakes and 133°
  non-orthogonality — now it is rejected (better no BL than a garbage BL):
  venturi default NO 133.6 → 43.3.
- **Sharp-edge field opt-in** (`POLYFOAMMESH_GMSH_SHARP_EDGES=1`): the
  corpus regression run showed no regressions, but the field
  stays opt-in by keep-best principle until a corpus proves that it
  helps more than it costs.
- **y+ calibration: `wallShearStress` convention**: OpenFOAM
  incompressible solvers write the field as τw/ρ (kinematic), but the
  code divided by ρ → u_tau wrong by √ρ (×31 for water). Now
  the default is kinematic (`u_tau = √|τw|`), the dynamic convention is available
  via `kinematic_shear=False`, with a note in the result and in the dialog.
- **Patch deselection in the viewer**: a click on the **empty space** of the
  3D view now removes the highlight and the row selection (previously they
  stayed forever, making the selection useless).
- **y+ / boundary-layer first layer (chained error, real case)**:
  the physical calculation was correct (flat-plate, k-epsilon y+ 30) but the
  panel saved the first layer only as a **fraction of `max cell`**;
  when `max cell` changed (slider/adaptive, 0.171→0.0122) the same
  fraction produced a first layer **14× smaller** (85.8 µm → 6.1
  µm in the log) and in simulation a real y+ of ~0.09 instead of 30. Now the
  computed value is stored in **metres (absolute)** and is the one that
  goes to the engine; the fraction remains only for display. In addition: the
  calculation **recomputes automatically** when Velocity /
  Fluid / Wall treatment change (previously it kept the old one, e.g. computed at
  10 m/s and used at 1 m/s), a manual edit of the fraction returns to
  manual mode, and the message states that the flat-plate estimate on
  curved/internal flows can differ by ~2-4×.
- **Case root with a space in the user path** (phase-2 Linux
  regression): the "no spaces" fallback rewrote the path replacing the
  spaces with underscores, creating a non-existent tree
  (`C:\Users\Davide_Valoroso\...`). All the WSL commands pointed there:
  checkMesh read an empty directory (`cells=8`), the quality panel
  showed zeros and the dual conversion reported `cells ... -> 0`. Now the
  root is without spaces **by construction**: `<drive>:\cfmesh_cases` on
  Windows (where the real cases live), `tempdir/cfmesh_cases` on POSIX.
- **BL boundary manifoldness guard** (real "blade" case): the BL
  terminator strips at the BL/non-BL borders ended up in the boundary
  block (measured: +139,360 faces = exactly the terminators, 32 edge
  non-manifold) → checkMesh FAIL with 230,126 "multiply connected" faces.
  Now `_validate` rejects a build with a non-2-manifold boundary: on the
  blade the written mesh is the manifold dual-only one (multiply-connected
  0, aspect OK) instead of the disaster. The algorithmic fix of the terminator
  (internal step instead of a boundary strip) remains scheduled.
- **Patch roles after meshing too**: the patch table on a generated
  mesh shows the **geometrically inferred** type (inlet/outlet/wall)
  instead of "other", and the **post-meshing rename** writes to `boundary`
  (backup + validation). The BL selection uses the same inferred roles and
  A's wall floor now considers only the wall surfaces.
- **Formula A consistency**: `_BL_CELLS_ACROSS_STACK` 5→3 with the derivation
  from the inversion budget (0.34): the wall floor now really respects
  the budget.
- **Safe process kill on Linux**: OpenFOAM/cfMesh processes
  are launched with `start_new_session` and terminated with `killpg` (with
  an explicit guard to never hit the GUI process); on Windows
  `taskkill /T` remains unchanged. Previously on POSIX `killpg` could kill
  the app itself and two `taskkill`-based helpers were no-ops (orphan
  processes after Cancel).
- **RAM guard on Linux**: the RAM probe used `ctypes.windll` (inert on
  POSIX with a 999 GB fallback); now on Linux the host's real RAM is used.
- **Refinement boxes now really do what they promise**: the GMSH zone
  fields used `VOut = -1`, which combined in MIN made `-1` win
  everywhere outside the boxes and sent GMSH into an infinite retry loop (the
  mesh never refined, or never finished). Now a finite `VOut` +
  **gradual transition** (`Thickness`): no more rectangular block with a
  sharp step. Plus clamping of the refinement ratio (max 4×) and of the
  added-cell budget (~250k) to avoid count explosions
  (real case: 1.26M tetrahedra requested → ~305k).
- **BL: height-gradient limiter** (cfMesh `relThicknessTol`
  style): a vertex with a deep inversion budget next to a thin
  one created a "spike" that bent the extruded surface
  into non-positive cells and made the whole BL be rejected; now the
  stack is limited to the gradient and healthy meshes stay identical
  (verified byte-identical).
- **Measure**: unchecking it removes markers, labels and
  summary (previously they stayed on screen); the Clear button remains.
- **COMPLETE boundary layers (100% wall coverage)**: the binary semantics
  discarded faces on concave vertices or with an exhausted budget,
  leaving holes in the shell (measured: 5.9% of wall faces without
  BL on a real case, "the BLs are only partially extruded"). Now the
  standard path discards only faces with real input defects and
  keeps all the others with a compressed stack (thin but present, with a
  minimum anti-degeneration height). If the first layer turns out
  compressed, the log states it and suggests a coarser surface
  mesh to respect the requested y+.
- **Real boundary layers (no more fake "successes")**: the layer could
  turn out valid but invisible (1 layer when 14 were requested)
  because the consistency fixpoint is a minimum over the whole connected wall:
  a single vertex with a reduced budget flattened every face.
  Now a valid but flattened build is rejected and the layer is
  built with the maximum number of layers the mesh can hold at full
  height (`capped N -> k` in the log), with effective first layer, median
  and coverage reported in the log. On the real Part2 case: 1 layer at the exact y+
  target (full h1) or 2 layers with h1 halved, checkMesh PASS.
- **`drop_bnd` unit** (local face ids compared with positions in the
  filtered selection): with the sliver filter active ("robust BL")
  arbitrary faces were flagged; the valve golden goes from
  75,360 to 76,052 prisms (scale and exclusions unchanged).
- **Cell volume** measured with exact triangulation from the centroid
  (previously Newell/centroid, approximate on non-planar faces); the
  conservation tolerance (5e-3) documents the shell given up
  by-design under the excluded faces.
- **Silently compressed stacks**: the cap now triggers even when
  only one layer fits at full height (previously 14 layers were
  compressed into ~1 mm with y+ wrong by 20x without warning).

### Changed

- **Layers visible from the mesh itself (overlay removed)**: the checkbox
  "Show BL", the orange cell colouring and the colorbar that
  appeared after meshing were **removed** (user request:
  unwatchable). Boundary layers are seen as in the previous
  release: in "Surface with Edges" the stack appears as the band of
  dense edges at the wall — it is the mesh itself, no overlay.
- **Poly pipeline performance (profile-driven, all bit-identical)**: the
  dual converter is 24-43% faster (face-topology cache + pure-Python
  coordinate shadows in the edge emitter), the BL precompute 45-50%
  faster (numpy grouping/scatter aggregation; element values stay scalar
  to keep the ULP-exact inversion thresholds), and the consume reflow
  ~2x faster (hoisted degree array + pinned-target pruning).  A per-run
  `[perf] stage times:` line now covers every stage and
  `POLYFOAMMESH_PROFILE=1` dumps cProfile hot lists for the dual and BL.
  Every change was gated on byte-identical polyMesh SHA-256 on the
  regression cases (orchestrator-verified A/B).
- **Mesher order: Polymesh first, cfMesh second** and cfMesh no longer
  pre-ticks "Convert to polyhedral mesh": the hex-dominant mesh is the
  point there, the poly dual is opt-in (Polymesh keeps forcing the
  conversion ON, hidden).
- **English-only repository policy**: all public documentation
  (CHANGELOG, README, INSTALL, USER_GUIDE, residual risks, CONTRIBUTING,
  CODE_OF_CONDUCT, THIRD_PARTY_LICENSES, GitHub templates) and all
  developer docs, plans and reasoning notes are now in English; the
  Italian duplicates (INSTALL.it.md, USER_GUIDE.it.md) were removed.
- **Robust boundary layer always active**: the "Robust boundary
  layer (recommended, experimental)" checkbox was removed — the measured
  bundle (sliver-face filter 0.9 + budget termination) now activates
  **by itself** when "Enable Boundary Layers" is checked. No more
  knobs to know: check the BL and it comes in with the right settings.
- **Tidy left dock**: the patch table now **fills** the space
  under the workflow (it was capped at 140px with an empty grey band
  above) and the "Patches" title has the same style as "Next action": the
  left column is an ordered list (Workflow → Next action →
  Patches).
- **Automatic concave closure**: the worker retries
  `decoupled_vertex` (H4) by itself when the standard path fails, then
  halves the first layer. It activates where needed (concave
  junctions: groove1/slot1, production valve) without degrading the cases already
  handled by the standard path. The memory guard (8M faces / 1.5M cells) protects
  the closure retry.

### Removed

- "Advanced closure for concave geometries (experimental)" checkbox from the
  Boundary Layers panel, `get_bl_concave_closure()` and the
  `concaveClosure` key from the GUI payload. The legacy flag remains only for the
  post-BL merge repair (off by default, measured in regression on
  groove1).
- **Full Auto** button from the ribbon. The **Cancel** is no longer in the
  ribbon: it was moved to the Mesh tab, **under "Reset All"**, where
  the user looks during a run (it appears only while the mesh is in progress).
- **Decimation** combo from the viewer (the Auto policy remains fixed
  internally).
- Empty **Quality** tab from the left panel (it showed only "No quality
  data yet"; checkMesh remains in the log, in the quality panel and in the
  PDF report).
- **"Auto-Fix Quality"** button from the quality panel (and the related
  `fix_requested` signal): the quality gate already retries automatically,
  the button was redundant.

### Changed

- **No more buttons for things the app must do on its own** (user
  request): removed "Calibrate from simulation" (the calibration module
  stays internal), "Fit BL to mesh" (stack compression is
  handled by the engine and stated in the log), the "Surface resolution"
  combo (now **derived automatically from the Detail slider**: very
  coarse/coarse → Coarse, medium → Medium, fine/very fine → Fine/Very
  fine) and the mesh cache checkbox (always active). The y+ calculation is
  **automatic**: it starts when the BL is enabled and updates when
  Velocity/Fluid/Wall treatment change; a manual edit of the values
  is respected (no overwrite).
- **Mesher list**: **Polymesh is the default path** (previously
  Cartesian cfMesh, which remains as an alternative); the
  "FEM Tetra (GMSH)" button was **removed** (a niche that duplicated the
  GMSH stage — the internal tetrahedral algorithm remains as an automatic fallback
  for complex geometries).
- **Viewer during runs**: the periodic render (30 FPS) is
  **paused** while the mesh is in progress and resumed at the end, to avoid
  competing with the workers (single helper `_set_run_state`).
- **Feature angle**: range extended to 5–179° (was 5–80°, it did not allow
  values like 89°); the last used angle is remembered.
- **Interface entirely in English**: panels, dialogs, tooltips and
  log messages (Italian strings had remained in params_panel,
  main_window, viewer and in the engine templates).
- **Light/dark theme**: root cause of the mixed-colour "islands"
  found in the app QSS (opaque background inherited from QLabel);
  runtime fix + palette-driven About/Template dialogs and
  theme-aware text/contrast colours (contrast verified, minimum 6.0).
- **Refinement box flow**: picking in the viewer now creates a box
  centred on the point (previously it created a legacy sphere and never showed
  the box); zone list with readable labels and level synchronised
  with the selection.
- **Workflow**: the checkmarks follow the real process (geometry -> sizing
  -> generate -> quality) also for the GMSH/autopoly/native paths,
  not just cfMesh.

## [2.2.0] - 2026-09-08

### Breaking changes

- **Data path renamed**: the app data moved from
  `%APPDATA%\cfmesh-autogui\` to `%APPDATA%\polyfoammesh\` (logs, sessions,
  octopoda telemetry). On first run the new folder is
  populated automatically by copying the contents of the old one, if
  present. Those who installed a previous version lose nothing, but
  the old path is no longer written. The app settings
  (theme, recent parameters) live in the Windows registry and do not change
  location: they stay where they were, so the user's preferences survive
  the package rename (`cfmesh_autogui` → `polyfoammesh`).

### Added

- **Release pipeline**: on push of a `v*` tag, GitHub Actions builds
  the PyInstaller executable + the Inno Setup installer on Windows and attaches
  them to the tag's GitHub Release (with the changelog extracted from this file).
  Removed the obsolete installer scripts (duplicated NSIS and Inno); fixed
  `UninstallDisplayName` in `inno_setup.iss` (directive split over two
  lines).
- **Algorithm substitution notice in the GUI**: when the engine silently
  substitutes a meshing path (boundary layer disabled,
  parallel→serial, coarser GMSH retry, quality retry), the
  log panel now shows a highlighted `[SUBSTITUTED]` line — never
  silent again.
- **Bilingual documentation**: `docs/INSTALL.md` and `docs/USER_GUIDE.md`
  rewritten in English (primary), with the Italian counterparts under
  `docs/`, cross-linked at the top.
- **Community**: `CODE_OF_CONDUCT.md`, PR template in
  `.github/PULL_REQUEST_TEMPLATE.md`, 5 "good first issue" issues on the
  public repo.
- `_paths.py`: single source for the app data directory and one-shot
  migration from the legacy path.

## [2.1.0] - 2026-09-08

### Breaking changes

- **Python package rename**: `cfmesh_autogui` → `polyfoammesh`. The
  imports in the code, tests and tools were updated; the CLI
  entry-point modules are now `polyfoammesh`, `polyfoammesh-mesh` and
  `polyfoammesh-batch`. The PyInstaller executable is called `PolyFoamMesh`.

### Added

- CI green again on GitHub Actions (it was red on every push): `pytest-qt`
  in the extras, POSIX-compatible paths in `config.py`/`validation.py`,
  `networkx` and `rtree` dependencies declared.

### Fixed

- Manual refinement zones: `setAsBackgroundMesh` discarded the geometric
  adaptive field; now MIN-combined (`gmsh_wrapper.py`).
- Closed curved bodies (e.g. sphere) tessellated non-watertight: coincident
  vertices at seams/poles now merged and degenerate triangles dropped
  (`geometry.py::tessellate_patches`).
- Tests that overwrote tracked `sample_cad/`; always-skip stub
  replaced with a real integration test.
- Git history: ~278 MB blob (`installer/output/*.exe`) removed from the
  `master` history with `filter-branch`.

### Note

- The prebuilt executable and installer are not committed to the repo:
  they are found in `dist/` and `installer/output/` respectively (both
  ignored by `.gitignore`) and are generated locally or by the release
  pipeline.

[2.2.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.2.0
[2.1.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.1.0
