# CFMesh-AutoGUI → MEGA LOOP: Commercial CFD Preprocessor

<!--
THIS FILE IS THE PROMPT TO REPEAT AT EVERY SESSION.
The agent MUST read this file, load Octopoda, and start the loop.
THE LOOP DOES NOT STOP UNTIL ALL P0s ARE COMPLETE AND GREEN.
-->

## Vision

Turn CFMesh-AutoGUI into the **definitive CFD preprocessor for OpenFOAM and BaramFlow** — the equivalent of **Ansys Meshing + Fluent Watertight Workflow + ANSA + Pointwise**, but in a single free/open-source desktop app, very elegant, with an automatic workflow.

## Design principles (absolute constraints)

1. **ONE CLICK** — The user must be able to obtain a decent mesh with a SINGLE CLICK. "Quick Mesh" must work on any geometry.
2. **AUTOMAGIC** — Automatic detection of: features, gaps, curvature, regions, patches, walls, inlet/outlet. The user must not touch anything.
3. **GUIDED** — Progressive wizard (like Ansys Fluent Watertight Workflow) that guides the user step by step.
4. **TRANSPARENT** — The user SEES what happens: real-time 3D preview, mesh appearing, quality updating.
5. **ZERO CFD JARGON** — Hide technical details behind sliders "Very Fine / Fine / Medium / Coarse / Very Coarse".
6. **ALL LOCAL** — Geometry → Mesh → BC → Setup → Export → Run, all in the same app.
7. **FEEDBACK AT EVERY STEP** — Progress bar, colored log, 3D preview, quality dashboard.

## Complete pipeline (what the app MUST do in order)

```
┌────────────────────────────────────────────────────────────┐
│  CFMesh-AutoGUI — Commercial CFD Preprocessor Pipeline     │
├────────────────────────────────────────────────────────────┤
│ STEP 1: GEOMETRY IMPORT (STEP/STL/IGES/BREP)              │
│   ├── Auto-detect units (mm/cm/m/in)                       │
│   ├── Auto-heal (stitch, fill holes, remove sliver faces)  │
│   ├── Auto-detect features (sharp edges, curvature, gaps)  │
│   ├── Auto-classify patches (wall/inlet/outlet/symmetry)   │
│   └── 3D preview with section, measure, zoom               │
│                                                            │
│ STEP 2: QUICK MESH (ONE CLICK)                            │
│   ├── Auto cell size based on: bounding box + curvature    │
│   ├── Auto-BL (wall detection + y+ target + layers)       │
│   ├── Cartesian fill (cfMesh) OR polyhedral OR tetrahedral │
│   ├── 3D mesh preview with color-coded quality            │
│   └── ETA + estimated cell count                           │
│                                                            │
│ STEP 3: ADVANCED MESH (when Quick Mesh is not enough)     │
│   ├── Local refinement: box/sphere/cylinder/point sources  │
│   ├── BL customization: select patches, N layers, growth   │
│   ├── Inflation layers on selections                       │
│   ├── Face sizing: mesh size on specific faces             │
│   └── Multiple regions (CHT, FSI, multi-material)         │
│                                                            │
│ STEP 4: QUALITY CHECK + AUTO-FIX                           │
│   ├── checkMesh (skewness, non-ortho, aspect ratio, etc.) │
│   ├── 3D quality heatmap (cells colored by quality)        │
│   ├── Auto-fix: smooth + refine bad cells (max 3 iter)    │
│   ├── PDF report (metrics, histogram, screenshot)          │
│   └── Thresholds: commercial defaults, editable            │
│                                                            │
│ STEP 5: BOUNDARY CONDITIONS (PREVIEW + SETUP)             │
│   ├── Auto-detect patch type: wall/inlet/outlet/symmetry   │
│   ├── Visual UI: click patch in 3D → assign name/type      │
│   ├── BC templates: internal_flow, external_aero, CHT      │
│   ├── Condition preview: color patches by type             │
│   └── Export OpenFOAM 0/ (boundary, U, p, k, omega, nut)  │
│                                                            │
│ STEP 6: SOLVER SETUP (PREVIEW)                            │
│   ├── Solver selector: simpleFoam/pimpleFoam/pisoFoam/     │
│   │   overPimpleDyMFoam/reactingFoam/chtMultiRegionFoam   │
│   ├── Scheme presets: steady/low/med/high turbulence       │
│   ├── Turbulence: laminar/kEpsilon/kOmegaSST/LES/DES      │
│   ├── Materials: air/water/idealGas/polymaterial           │
│   └── Export: controlDict, fvSchemes, fvSolution, fields   │
│                                                            │
│ STEP 7: EXPORT + RUN                                        │
│   ├── Export: OpenFOAM case / BaramFlow / CGNS / VTU      │
│   ├── Run local (WSL2) or submit to a cluster (Slurm/AWS) │
│   ├── Monitor: residuals plot, force coeff, probes         │
│   └── Post: paraView launch / results in viewer            │
└────────────────────────────────────────────────────────────┘
```

## P0 — Core Pipeline (IMPLEMENT NOW, in order)

Every P0 is **a complete module** in `src/cfmesh_autogui/commercial/`. Every module MUST:
- Have complete type hints
- Have PEP 257 docstrings
- Be tested (`tests/test_NAME.py`)
- Be integrated into the UI (MainWindow or wizard)
- Pass `ruff check --fix`

AFTER EVERY MODULE → `python -m py_compile` + `pytest tests/test_NAME.py -x -q`
If it fails → fix → repeat (max 3 cycles) → THEN move to the next.

### P0.1: `commercial/geometry_pipeline.py` — Import + Healing + Feature Detection
- Load STEP/STL/IGES/BREP with format auto-detect
- Automatic healing: stitch (gap < 0.1mm), fill holes (diam < 5mm), remove sliver (area < 1mm²)
- Feature extraction: sharp edges (dihedral angle > 30°), curvature (radius < 10% BB), thin gaps (< 5% BB)
- Auto-classify patches: wall (angle > 45° from horizontal), inlet/outlet (planar faces on the bounding box), symmetry
- Auto-detect units: compare the bounding box with typical values (mm/cm/m/in)
- Preview: show feature lines, curvature map, patch colors
- Integration: improved `NewCaseWizard` Step 1 (feature preview)
- Test: `tests/test_geometry_pipeline.py`

### P0.2: `commercial/mesh_engine.py` — Multi-algorithm mesh engine
Unify ALL meshing methods into a single interface:
- `CartesianHex` (cfMesh via WSL2) — default, hex-dominant
- `Polyhedral` (cfMesh polyDualMesh post-process) — for Star-CCM+ style
- `Tetrahedral` (GMSH Direct) — for complex geometries, no WSL
- `HexCorePolyBoundary` (Mosaic-style) — hex core + poly transition + prism BL
- `CartesianCutCell` (cfMesh cartesianCutMesh) — cut-cell type
Auto-select: based on geometry + target solver. Automatic fallback if a method fails.
Parameters exposed as a "Coarse ↔ Fine" slider (5 levels), not numbers.
- Test: `tests/test_mesh_engine.py`

### P0.3: `commercial/quick_mesh.py` — One click, mesh ready
- Takes geometry from P0.1
- Auto-chooses algorithm (P0.2)
- Auto-chooses cell size (curvature + thickness + BB)
- Auto-chooses BL (wall detection + y+ target 30 for RANS)
- Generates the mesh, saves it in `constant/polyMesh`, checks quality
- Output: cell count, synthetic quality report, mesh preview
- UI: "Quick Mesh (Ctrl+M)" button always visible in the Ribbon
- Integrated into the wizard as optional Step 2 ("Use Quick Mesh?" → Yes/No)
- Test: `tests/test_quick_mesh.py`

### P0.4: `commercial/quality_engine.py` — Quality check + Heatmap + Auto-fix
- Complete metrics: max skewness, max non-orthogonality, max aspect ratio, min volume, n neg cells
- 3D heatmap: per-cell color based on the worst metric (green=OK, yellow=warning, red=bad)
- Quality histogram (matplotlib embedded in a QWidget)
- Intelligent auto-fix:
  - Skewness > 0.9 → local smoothing (Laplace + optimization)
  - Non-ortho > 70 → refine region + smooth
  - Neg volume → remove + local remesh
  - Aspect ratio > 1000 → split cells
  - Max 3 iterations, stop on convergence
- PDF report: metric table + histogram + 3D screenshot + recommendations
- Test: `tests/test_quality_engine.py`

### P0.5: `commercial/bc_editor.py` — Boundary Condition visual editor
- Reads mesh patches from `constant/polyMesh/boundary`
- Shows patches in the 3D viewer with colors by type
- Auto-detect type based on:
  - Name (inlet/outlet/wall/symmetry in the patch name)
  - Normal (points toward the bounding box → inlet/outlet)
  - Geometry (curved → wall, flat on the boundary → patch)
- Editor: click a patch in the 3D view → rename, change type
- BC templates: load presets by case type
- Export: writes the `0/` directory with boundary, U, p, epsilon, omega, k, nut
  with intelligent default values
- Test: `tests/test_bc_editor.py`

### P0.6: `commercial/solver_setup.py` — Automatic solver config
- Template for each solver:
  - `simpleFoam` — steady RANS, SIMPLE
  - `pimpleFoam` — transient RANS, PIMPLE
  - `pisoFoam` — transient LES, PISO
  - `reactingFoam` — combustion
  - `chtMultiRegionFoam` — conjugate heat transfer
  - `overPimpleDyMFoam` — overset + moving mesh
- Turbulence: laminar / kEpsilon / kOmegaSST / LES (Smagorinsky / WALE) / DES
- Scheme presets: "Robust" (upwind), "Balanced" (blended), "Accurate" (linearUpwind)
- Materials: fluid + solid (for CHT) with a property table
- Export: `system/controlDict`, `fvSchemes`, `fvSolution`, `constant/transportProperties`, `constant/turbulenceProperties`
- Test: `tests/test_solver_setup.py`

### P0.7: `commercial/template_engine.py` — Template manager + fast Wizard
- Predefined templates:
  - `internal_flow` — pipe, manifold, elbow, valve
  - `external_aero` — airfoil, car, drone, building
  - `cht` — heat sink, heat exchanger, electronics cooling
  - `multiphase` — damBreak, tank filling, wave
  - `moving_body` — propeller, turbine, valve opening
  - `conjugate_ht` — PCB cooling, LED cooling
  - `combustion` — burner, flame, combustor
- Each template contains: example geometry, mesh preset, BC, solver setup
- UI: "New Case from Template" → grid of cards with icon + description
- User-defined templates: save the current case as a template
- Test: `tests/test_template_engine.py`

### P0.8: `commercial/one_click_run.py` — Geometry → Mesh → BC → Setup → Run
- One button: "Full Auto"
- Input: geometry file + output dir + target quality
- Auto: import → heal → mesh → check → fix → BC → solver setup → run
- Output: complete OpenFOAM case + quality report + residuals
- Single progress bar + detailed log
- Test: `tests/test_one_click_run.py`

## P1 — Commercial Enhancements (after all P0s are green)

| # | Module | Inspired by | Description |
|---|--------|-------------|-------------|
| 1 | `commercial/amr.py` | Star-CCM+ AMR | Solution-based adaptive mesh refinement |
| 2 | `commercial/overset.py` | suggar++ | Overset grids for moving bodies |
| 3 | `commercial/morphing.py` | ANSA Morph | RBF/FFD mesh deformation |
| 4 | `commercial/batch_mesh.py` | Pointwise Glyph | Batch meshing with journal |
| 5 | `commercial/exporter.py` | — | CGNS, VTU, Ansys, Abaqus, SU2, Fluent |
| 6 | `commercial/parallel_mesh.py` | cfMesh MPI | Domain decomposition + parallel fill |
| 7 | `commercial/multi_region.py` | chtMultiRegion | Conformal interfaces for CHT/FSI |
| 8 | `commercial/ai_assist.py` | ML-driven | ML for cell sizing, quality prediction |
| 9 | `commercial/cloud_submit.py` | — | Slurm/AWS Batch job submission |
| 10 | `commercial/journal.py` | — | Macro recorder + Python scripting API |

## P2 — Enterprise (after P0+P1 are green)

- Licensing: floating license server / subscription
- SSO: Azure AD, Okta, Google
- REST API: headless FastAPI for CI/CD
- Plugin marketplace: download + install from a repository
- Audit trail: Octopoda + blockchain hash
- Dashboard: Grafana + InfluxDB for usage telemetry
- Enterprise installer: MSI + GPO + silent deploy

## UX Guidelines (absolute constraints)

```
┌─────────────────────────────────────────────────────────┐
│ RIBBON (like Ansys Workbench)                          │
│ [Home] [Geometry] [Mesh] [Quality] [BC] [Solver] [Run] │
├─────────────────────────────────────────────────────────┤
│ WORKFLOW TREE (left)           │  MAIN CONTENT          │
│ ✅ 1. Import Geometry          │  [3D Viewer +          │
│ ✅ 2. Quick Mesh               │   Parameters +         │
│ ⏳ 3. Quality Check            │   Wizard step]         │
│ ⬜ 4. Boundary Conditions      │                        │
│ ⬜ 5. Solver Setup             │                        │
│ ⬜ 6. Export & Run             │                        │
├─────────────────────────────────────────────────────────┤
│ STATUS BAR: cells | max_skew | non-ortho | WSL | time  │
└─────────────────────────────────────────────────────────┘
```

- **Sliders everywhere**: "Very Fine / Fine / Medium / Coarse / Very Coarse" instead of numbers
- **Tooltip on every input**: explanation in simple Italian + physical formula
- **Live 3D preview**: every parameter change updates the viewer in real time
- **Semantic colors**: green (OK), yellow (warning), red (bad) for every metric
- **Progress bar**: ETA + percentage + current phase + scrolling log
- **Shortcuts**: Ctrl+M (Quick Mesh), Ctrl+Shift+Q (Quality), Ctrl+R (Run), Ctrl+N (New)
- **Theme**: ANSYS-inspired, light/dark/system, design tokens

## Loop execution rules

1. READ `COMMERCIAL_LOOP.md` (this file)
2. LOAD Octopoda: `octo = OctopodaRuntime(); octo.remember("phase", "commercial_loop")`
3. CHECK `octo.recall("implemented_features")` — what has already been done?
4. CHOOSE the next NOT implemented P0 (from the list above, in order)
5. IMPLEMENT: create `commercial/NAME.py` + `tests/test_NAME.py` + integrate UI
6. VERIFY: `python -m py_compile` + `pytest tests/test_NAME.py -x -q`
7. IF IT FAILS → analyze → fix → repeat (max 3)
8. IF IT PASSES → `ruff check --fix` → register in Octopoda
9. REGISTER:
```python
done = octo.recall("implemented_features") or []
done.append({"feature": "P0.N — Name", "module": "commercial/NAME.py", "status": "PASS"})
octo.remember("implemented_features", done)
```
10. IF other P0s remain → GO BACK TO STEP 4 (loop)
11. IF all P0s green → congratulations! → move to P1

## Acceptance criteria (all P0s)

- [x] Every module compiles without errors (`python -m py_compile`)
- [x] Every test passes (`pytest tests/test_NAME.py -x -q`)
- [x] Integrated into `main_window.py` (menu, ribbon or wizard)
- [x] The user can complete the pipeline from the wizard without opening a terminal
- [x] Quick Mesh produces a mesh on a test STL geometry in < 60s
- [x] PDF report opens correctly (reportlab 5.0.0, MeshReportPDF class in gui/pdf_report.py)
- [x] OpenFOAM case export is valid (checkMesh gives no fatal errors)
- [x] Everything runs on Windows 11 + WSL2 Ubuntu + OpenFOAM v2512
