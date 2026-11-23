# Changelog

All notable changes to PolyFoamMesh are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.5.0] - 2026-11-23

A release focused on the robustness and speed of the Auto chain
(tet → polyhedral dual → boundary layer → checkMesh), plus the new virtual wind
tunnel. The figures in the summary were measured on the author's real geometries
(a 26-run corpus, production chain, WSL OpenFOAM 2512); figures on the parametric
families (degenerate edges, tet budget, size ratio) are marked as such.

### Measured summary

- Corpus (26 runs): `Mesh OK` with boundary layer 12 → 18, total time 806 → 469 s,
  no case got worse.
- DN1000 (GUI configuration, fTetWild): tet stage from 6.7 million tetrahedra in
  51 minutes to 154 thousand in 38 s; the full chain reaches `Mesh OK` with 99–100 %
  layer coverage in 67 s.
- DN200 venturi and a second part: from "dual on an empty mesh" to `Mesh OK` with a
  boundary layer.
- A laminar-flow part (`lam.stp`): `Mesh OK`, 542 thousand cells, layer 95 %, 517 s.
- Tested and left unchanged: capping fTetWild's `opt_iter` at 6 slows the tet stage
  by 43 % on fine runs, so the default 15 stays and there is no GUI control.

### Added

- **Role column in the patch panel** (replacing "Type"): auto / inlet / outlet /
  symmetry / wall, pre-filled with the inferred role. The user's choice drives which
  surfaces get boundary layers and the BC types written by `setup_case`. Overrides
  follow renames and are cleared by Reset.
- **Virtual wind tunnel** (Tools > Create Wind Tunnel around Object...): the loaded
  CAD becomes the object and the fluid is the box around it. Editable criteria:
  upstream 3 L, downstream 8 L, sides 4 D, above 5 H, maximum frontal blockage 3 %
  (the box widens by itself and says so), optional ground. Patches `inlet`, `outlet`,
  `farfield`, `ground` plus the object with its original names; refinement boxes on
  the object (level 3) and in the wake (level 2).
  - The base cell comes from a cell budget (default 100 thousand; surface cell =
    base/4); the dialog shows the object size along the chosen axes and warns when the
    object has fewer than 10 cells across its thickness.
  - No watertightness auto-repair runs after creation (on the NACA 0024 it turned the
    tunnel into a 3274-face shell with negative volume); the app warns instead when
    the object has open edges.
  - Tet engine: fTetWild first, GMSH as fallback (GMSH on the STL tunnel stalled 2 x
    300 s or segfaulted; fTetWild took 42–100 s). A mesher saved in the project is
    cleared on creation.
  - Measured on the NACA 0024: 102 thousand cells without boxes (4.7 min, layer 85 %);
    186 thousand with boxes (11.7 min, layer 91 %). checkMesh still reports 2–3 pyramid
    faces in both cases.
- **Run-start notice**: stages, expected duration (calibrated on measurements) and a
  warning that the tet stage stays silent for minutes; fTetWild heartbeat every 30 s.
- **Load-time notice** when the volume is a thin shell or the estimate exceeds
  5 million cells, plus an indicative time (about 4.5 s per 1000 estimated cells) and
  a cell size that leads to about 75 thousand cells, from 150 thousand estimated cells
  upward.
- **Explanatory tet-chain log**: the fallback plan is stated at the start; fTetWild's
  `FAIL subdivide_tets` lines (normal retries) are annotated.
- **Parametric geometry families** (`benchmarks/families.py`,
  `benchmarks/run_families.py`): 13 families from 1 cm to 10 m, seeds 0–99 for tuning
  and 1000+ for held-out checks, with a per-class table. Erratum: the first table was
  invalid (objects 1000 times smaller, built in mm and read in m).
- `tools/compare_mesher_bl.py`: compares GMSH / fTetWild / snappy counting only
  `Mesh OK` with a real boundary layer; fallbacks are not credited.
- Comparison with snappy: parser for OpenFOAM v2512 layers, layer coverage from the
  statistics, tet attempts in the report; benchmark and E2E use the same production
  function as the GUI (`production_dual_kwargs`).
- `AVVIA_PolyFoamMesh.cmd`: double-click to start the latest source in the folder.
- Portable distribution: the `PolyFoamMesh` folder (PyInstaller) also as a zip, with
  no installer. The Inno Setup installer stays in `installer/` but was not assembled
  for this version.
- User guide: troubleshooting for the GMSH partition loop and fTetWild's
  `FAIL subdivide_tets` lines.

### Changed (speed)

- **Adaptive size field as a lattice instead of a Python function** (GUI adaptive mode,
  CAD input): GMSH called a Python function 7 million times on a simple 2 m pipe (136 s
  inside the callback out of 109 s of meshing). The field is now precomputed with numpy
  and read by GMSH in C++ (`Structured` field, combined as Max(Min(base, lattice), wall
  threshold), same semantics as before). Measured: 2 m pipe 109 → 22 s; `lam.stp`
  134 → 22.5 s (8 runs out of 8, tets within 3 %); mini_valve 65 → 6 s and multi_inlet
  with -24 % and -11 % tetrahedra. The mesh is **not** identical: counts change by a
  few tens of percent on some sharp-edged geometries, and checkMesh quality with the
  lattice is not yet measured on real geometries. CAD input only (GMSH segfaults on
  STL); 1D/2D single-threaded with the lattice (with more threads one run in three
  crashed); never in robust mode; if GMSH crashes, the normal → robust → manual chain
  restarts without the lattice. `POLYFOAMMESH_SIZE_LATTICE=0` turns it off.
- **fTetWild adaptive size field with a growth cap** (the main cause of slowness in
  the GUI): it could inflate the mesh by tens of times. Growth is now limited to about
  5x the uniform mesh, with the ratios between fine and coarse zones unchanged
  (`FTETWILD_ADAPTIVE_BUDGET` changes it, 0 removes it).
- **fTetWild on STEP reads the already loaded surfaces** instead of going through
  GMSH's surface mesher (slow and fragile): the same valve now tets in 30 s.
- **Auto does not wait minutes for GMSH**: GMSH gets 120 s in total, then fTetWild
  starts (`POLYFOAMMESH_AUTO_GMSH_BUDGET_S` changes the limit, 0 removes it).
- **GMSH loop breaker**: a circuit breaker stops the volume after 2000 repetitions of
  the same partition warning (it could run for hours) and the chain restarts with a
  named failure; the GUI log shows a summary every 500 repetitions.
- **GMSH tet-budget correction in up to three passes** (exponent re-measured each
  pass): `straight_pipe` 11.9x → 2.9x and `venturi` 4.0x → 1.7x over budget. The
  minimum/maximum size ratio is configurable with `POLYFOAMMESH_GMSH_MIN_RATIO`
  (default 0.5; 0.25 refines thin gaps but costs 2–4x the time, and dual quality with it
  is not measured).
- Dual: local filter before the global score and incremental relaxation
  (`IncrementalDefectScore`, same criteria, verified exact). DN1000 dual 188 → 30 s;
  corpus 806 → 391 s with the same verdicts.
- Boundary layer: core reflow with a sparse CSR matrix (9.3 s → 0.95 s per attempt on
  the DN1000); thinner heights are skipped when defects stop decreasing (corpus
  471 → 419 s).
- Dual: concave-cell repair has a time limit (120 s) and reports a heartbeat to the
  watchdog; it stops keeping the best mesh.

### Changed (quality)

- **"Fast tets but no poly"**: polyhedral conversion started only if the raw tets
  passed checkMesh, but they almost always fail, so the chain ran idle. On the first
  checkMesh failure the dual + boundary layer now runs once and the checkMesh after the
  dual is authoritative.
- **"Always poly"**: after the retry at coarser detail, fTetWild gets a last attempt
  with a relaxed envelope (eps x2, limited to [20 %, 30 %]).
- **Suggested size for pipes and ducts** enforces a minimum number of cells across the
  section (6/9/12/18/28 for the five detail levels). It used to give 70 mm for a 255 mm
  pipe (3.6 cells across the diameter); now 17.6 mm. On the real files only the four
  pipes change.
- Boundary layer validated on the criteria that make checkMesh fail (pyramids with
  OpenFOAM's decomposition, non-orthogonality > 90, skew > 4 including wall faces): the
  layer can no longer add defects. Corpus: `Mesh OK` with layer 14 → 17.
- Boundary layer, concave closure on the GMSH path: at least 6 exclusion rounds
  (was 3); wall-pyramid cleanup after the layer runs on every path. Corpus `Mesh OK`
  17 → 18.
- Dual: when few defects remain (≤ 8) it is rebuilt once with a 20-degree edge angle
  and the better mesh is kept (corpus 12 → 14).
- Dual poly: a single set of options (`production_dual_kwargs`) also on the GMSH path;
  L-shaped cells on concave edges (`poly_cell_split`). Residual defects 1313 → 389.
- The first layer is also capped to the cell size (with the reason in the log).
- Default "Surface with Edges" CAD view draws only sharp edges (> 30 degrees) and open
  edges, not every triangle.

### Fixed

- **GMSH adaptive: degenerate edges are no longer "small features".** Poles of spheres
  and fillets have edges as short as the CAD tolerance (~2e-7); counted as minimum
  features they forced the minimum size around them and the 2D mesher never finished
  (filleted-box family: 4/4 timeouts at 150 s → 4/4 in about 6 s).
- **Patches from STEP by angle** (with "CAD face names" off): CAD faces are now grouped
  by the true angle between adjacent faces instead of splitting triangles. A CAD face is
  never cut; a plane tangent to a curved surface (side + fillet) stays separate; a pipe's
  cylinder plus its bend stay one wall. STL files still use the angle between triangles.
- **Log with literal HTML entities**: unlabeled lines (`[WARN]` etc.) showed `&gt;` and
  `&#x27;` instead of `>` and `'`.
- **Patch rename that "does not really change them"**: a name with spaces or the
  characters `;{}"/` was silently rejected. The name is now sanitized (spaces/illegal →
  `_`, collisions get a numbered suffix) and committed from both the table and the 3D
  click.
- **Freeze on rename**: renaming re-ran the full per-patch size computation (numpy on
  the GUI thread). Only the caches are now cleared and recomputed on first use.
- **Impossible deselection**: ESC clears the patch selection.
- **fTetWild on STL ignored refinement boxes**: they now enter the volume size field,
  after the growth cap. Mesh cache version 3 (meshes made with ignored zones are not
  reused).
- **3D-volume gate in `.msh`**: it read every numeric line, so a physical name counted
  as a tetrahedron and STEP files without a solid and with 4 or more surfaces looked
  like they had a volume (no switch to fTetWild, dual on an empty mesh). It now reads
  only the `$Elements` section (MSH 2.x and 4.x).
- **Boundary-layer report**: first-layer thickness and y+ are measured along the wall
  normal and only on thin cells (before: "3.01 mm, y+ 87" for 0.44 mm layers).
- **Cancel and Reset**: after Cancel or Reset the output of a stopped or replaced run is
  discarded (log, progress, notifications) and a job that finishes after Cancel is
  reported as cancelled, not as successful.
- **Tet engine pin**: clicking the pin clears the mesher saved in the project, which
  used to keep forcing GMSH even with the fTetWild pin.
- **Scale applied twice** in the fTetWild fallback on patched STL.
- **Auto no longer declares a poly without boundary layer valid**: the outcome is
  classified (`POLY_BL`, `POLY_NO_BL`, `TET_BL`...) and logged; keep-best prefers the
  one with a layer.
- **E2E Auto**: applies the boundary layer on the dual before checkMesh, with
  h1 = 1 % of the maximum cell.
- Poly smoother: `np.divide(where=)` without `out` left uninitialized memory.
- Mixed tet+prism dual: internal faces written in upper-triangular order and unused
  points removed (checkMesh reported errors on that path).
- Boundary layer rejected on concave cases: two uncovered zones touching at a single
  vertex discarded the whole layer (`_close_bare_pinches`).
- REST API (experimental): jobs no longer stuck in RUNNING, validated `file_id`, upload
  with a size limit, optional key `POLYFOAMMESH_API_KEY`, invalid parameters = 422,
  `/batch` off the event loop. The API still uses the legacy workflow.

### Known limitations

See [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for the full, current list.
The main ones in this release: the `airfoil` class (thin trailing edge) still times out
in adaptive GMSH; the sharp concave throat class (venturi, valve) still fails some
checkMesh checks in strict mode; dual quality with the size lattice is not measured on
all real geometries; nearby-surface refinement is opt-in; the polyhedral dual can make
the solver diverge on geometries with many narrow gaps even when `checkMesh` says
`Mesh OK`; and the packaged app was not tested on a clean machine.

## [2.4.0] - 2026-11-04

The Auto meshing chain and its measurement tooling.

### Added

- **Two visible meshing paths**: `Polymesh` (automatic, default) and `cfMesh` (explicit
  hex). The engine selector was removed; saved projects and presets that name an engine
  keep being honoured.
- **Automatic engine choice per geometry**: a deterministic choice from measured
  preflight (watertightness, file type, fTetWild availability): GMSH first with an
  fTetWild fallback, never swapped silently; every substitution is declared in the log.
- **fTetWild mesher** (optional extra, `pip install pytetwild` or
  `polyfoammesh[ftetwild]`): the tet stage of the poly pipeline can run with fTetWild;
  everything downstream (gmshToFoam, barycentric dual, boundary layers, checkMesh) is
  unchanged. Adaptive local refinement from the ray-cast passage thickness and the
  curvature radius, envelope capped at 5 % of the smallest dimension, absolute epsilon
  0.3 mm as the measured default. Verified on geometry batteries.
- **One `MeshConfig` and one validated `BLConfig`**: shared by the GUI, CLI helpers and
  workers, with units, per-field provenance, schema version, declared clamps, and named
  presets. Presets carry no geometry-specific cell sizes.
- **One `PhaseWatchdog`** for stall, timeout, heartbeat, ETA and declared action across
  GMSH, checkMesh, gmshToFoam, fTetWild, dual and boundary layer; visible progress and
  cancellation for the dual and the layer.
- **Boundary layer directly on tets** (`bl_tets`, opt-in) and a shared attempt policy
  (baseline restore, keep-best). A layer report states achieved versus requested first
  height, coverage and the cap that applied.
- **One `TetSource` contract** (requested/effective source, settings, attempts, failure
  reason, cache fingerprint).
- **Multi-region patches**: a mesh without explicit surface groups produces one patch
  per volume.
- **Release gate** (`tools/release_gate.py`): versions → spec static checks → smoke →
  repo-clean → (opt-in) E2E and real checkMesh → (opt-in) PyInstaller build and
  installer assembly, with declared limits.
- Direct-BL on raw tets after conditioning: on the real reference part, 5 layers reach
  `Mesh OK` with 100 % coverage on 74 thousand wall faces.
- Dirty CAD: an open 10-triangle cube that made snappy produce 0 cells now meshes
  through fTetWild to a checkMesh `Mesh OK` tet mesh; the dual refuses the open input.

### Changed

- The boundary-layer stack hint states facts only: the pre-build prediction of the
  compressed stack was removed because it disagreed with the engine's per-vertex clamp.
- Case directories are created atomically with a numbered suffix, so a path collision
  can no longer crash a run.
- A 3D-element gate runs on the `.msh` before the cache in the GUI (as the CLI already
  did); convert failures advance the chain instead of stopping it.

### Fixed

- Viewer status could stay stuck on "Generating foamToVTK..." after a successful retry.
- Process robustness: stalled phases are detected and reported instead of hanging.

## [2.3.0] - 2026-10-13

The first release with a working boundary layer on the poly path, Linux groundwork
and a full English interface.

### Added

- **Boundary layers on the poly mesh**: a consuming multi-layer boundary layer is the
  default (tube from 1 to 6 layers), with complete wall coverage, exclusion of
  degenerate vertices, a sliver-aware guard and a height-gradient limiter. Layers are
  visible from the mesh itself ("Show BL").
- **Dual quality on complex geometries** (opt-in, keep-best), mixed tet+prism input with
  exact stitching, and self-healing of open cells at a venturi throat.
- **BaramMesh-style patch splitting** on import (STEP and STL), with feature angle
  5–179°, and patch roles recognised after meshing too.
- **Automatic tet mesh cache** (skips GMSH when the inputs are unchanged).
- **Direct OpenFOAM export** (Export Mesh → OpenFOAM case) that ships a clean case.
- **Linux support, phases 1–2**: platform-dispatched OpenFOAM configuration, native
  `bash` on POSIX, XDG data directories, safe process kill, RAM guard, availability
  gate, packaging skeleton (`launch.sh`, `PolyFoamMesh-linux.spec`).
- Viewer: deterministic Section Cut; workflow dock with a "Next step" entry; log panel
  during meshing; refinement boxes picked in the viewer.
- Documentation: Linux installation guide, documentation index, and a complete user
  manual in English (`docs/USER_GUIDE.md`).

### Changed

- **Polymesh is the default path**; cfMesh is second and no longer the fallback.
- **Robust boundary layer is always active**; the checkbox and other buttons for things
  the app must do on its own were removed (including the old Full Auto button,
  Decimation combo and "Auto-Fix Quality").
- **The interface and all documentation are English-only.**
- Poly pipeline performance (profile-driven, bit-identical output); light/dark theme
  fixed; the workflow checkmarks follow the real process.

### Fixed

- **Boundary layer skipped on every app run (critical)**, real boundary layers instead of
  fake "successes", and silently compressed stacks.
- GMSH hang on large curved geometries, adaptive-sizing runaway, and a degenerate prism
  rejection (first layer under 5 % of the stack).
- Refinement boxes now really apply; manual zones keep geometry sizing.
- y+ calibration uses OpenFOAM's `wallShearStress` convention; first-layer error chain
  corrected on a real case.
- cfMesh RAM safeguard was a silent no-op; Linux process kill and RAM probe.
- Paths with a space in the user directory; patch rename keeps the `0/` fields in sync;
  oversized popups; double Cancel button; viewer deselection.
- The test suite no longer rewrites the benchmark corpus.

### Removed

- The Full Auto ribbon button, the viewer Decimation combo and the "Auto-Fix Quality"
  button, along with the related dead code.

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
