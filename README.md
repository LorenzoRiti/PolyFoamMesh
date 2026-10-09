# PolyFoamMesh

**Open-source polyhedral mesh generator for OpenFOAM** — builds the mesh with
GMSH (tetrahedra) and our **dual poly** engine with boundary layer, all from a
GUI. cfMesh/cartesianMesh remains available as an alternative path, it is not
the primary one.

License: [GPLv3](LICENSE) · [Third-party licenses](THIRD_PARTY_LICENSES.md) · [How to contribute](CONTRIBUTING.md)

## Who is it for

You are a CFD engineer, a student or a researcher who today prepares OpenFOAM
meshes by writing `blockMeshDict`/`snappyHexMeshDict`/`meshDict` by hand and
discovering quality problems only after running `checkMesh`. This app puts GMSH
and cfMesh behind a GUI: load a geometry, generate a mesh with one click, watch
the quality while it is computed, and export a case ready for the solver.

## How it compares

| | PolyFoamMesh | snappyHexMesh "bare" | Ansys Meshing / ANSA / Pointwise |
|---|---|---|---|
| Cost | Free, open source (GPLv3) | Free (part of OpenFOAM) | Commercial licenses, often expensive |
| Automation | 1-click + auto quality-fix | Manual, text dictionaries | High, but proprietary workflow |
| Maturity | Active beta, known and documented limits | Mature, but no GUI | Mature |
| Constraint | OpenFOAM only | OpenFOAM only | Multi-solver |

It is not a replacement for a mature commercial tool on very complex geometries
— it is meant for those who work with OpenFOAM and want to save the hours
currently lost writing dictionaries and interpreting `checkMesh` by hand. The
known limits are documented in [docs/residual_risks.md](docs/residual_risks.md),
not hidden.

## User documentation

- **[Installation](docs/INSTALL.md)** — Windows installer or from source
- **[User guide](docs/USER_GUIDE.md)** — how the workflow works, tab by tab
- **[Known limits](docs/residual_risks.md)** — what does not work yet and why

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                     GUI (PySide6)                    │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │Geometry │ │  Mesh    │ │ Advanced │ │ Quality │ │
│  │   Tab   │ │   Tab    │ │   Tab    │ │   Tab   │ │
│  └─────────┘ └──────────┘ └──────────┘ └─────────┘ │
│        │             │              │           │    │
│        ▼             ▼              ▼           ▼    │
│  ┌──────────────────────────────────────────────┐   │
│  │              Core Engine Layer                │   │
│  │  FeatureDetector  GMSH Wrapper  cfMesh Runner │   │
│  │  meshDict Gen     Mesh Converter  Quality Fix │   │
│  └──────────────────────────────────────────────┘   │
│        │             │              │                │
│        ▼             ▼              ▼                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐    │
│  │  GMSH    │ │  cfMesh  │ │  OpenFOAM        │    │
│  │ (native) │ │ (WSL2)   │ │  checkMesh/...   │    │
│  └──────────┘ └──────────┘ └──────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## Main features v2.0

| Feature | Description | Status |
|---------|-------------|--------|
| **GMSH + cfMesh Hybrid** | GMSH analyzes curvature/sizing, cfMesh fills the volume | P0 |
| **GMSH Direct** | Complete tetra+BL mesh on Windows, without WSL | P0 |
| **Feature Detection** | Automatic detection of sharp edges, thin gaps, curvature | P0 |
| **New Case Wizard** | Guided 3-step workflow (Geometry → Mesh → Quality) | P0 |
| **Tabbed parameters panel** | Geometry/Mesh/Advanced/Quality tabs | P0 |
| **Quality pipeline** | checkMesh → auto-fix → re-run (max 3 iterations) | P0 |
| **Quick Mesh (1-Click)** | Ctrl+M for an instant mesh with auto-suggested parameters | P0 |
| **Export CGNS/VTU** | Exports the mesh to CGNS and ParaView VTU formats | P1 |
| **Section plane** | Interactive section in the 3D viewer | P1 |
| **Undo/Redo** | Ctrl+Z/Y with QUndoStack + ParamChangeCommand | P0 |
| **Case templates** | Presets for internal flow, external aerodynamics, CHT | P1 |
| **QualityFixWorker** | Auto-fix loop — checkMesh → fix → re-run (max 3) | P0 |
| **ParaView integration** | Tools > Launch ParaView (subprocess.Popen) | P1 |
| **Plugin system** | Plugin(ABC) via importlib, plugins/ folder | P2 |
| **PDF quality report** | Professional mesh-quality report with charts | P0 |

## Quick install

Requirements: Windows 10/11, Python 3.11+ (only for the from-source install),
optionally OpenFOAM v2512 in WSL2. Full guide (prebuilt installer or from
source): **[docs/INSTALL.md](docs/INSTALL.md)**.

```bash
pip install -e ".[test]"
pip install gmsh meshio reportlab
polyfoammesh
```

Workflow and shortcuts: **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)**.

## Project structure

```
polyfoammesh/
├── src/polyfoammesh/
│   ├── app.py                  # Entry point (splash, theme, i18n)
│   ├── config.py               # OpenFOAM/WSL configuration
│   ├── _version.py             # Single source of truth for the version (imported everywhere)
│   ├── core/                   # Engine layer (no GUI import)
│   │   ├── geometry.py         # CAD import (cadquery, trimesh)
│   │   ├── gmsh_wrapper.py     # Wrapper around the GMSH Python API
│   │   ├── meshdict_gen.py     # meshDict generator for cfMesh
│   │   ├── feature_detector.py # Sharp edges, gaps, curvature
│   │   ├── stl_writer.py       # Surface STL export
│   │   ├── boundary_reader.py  # OpenFOAM boundary parser
│   │   ├── case_setup.py       # OpenFOAM case setup + controlDict
│   │   ├── openfoam_runner.py  # cfMesh runner + error analysis
│   │   ├── mesh_converter.py   # MSH → OpenFOAM conversion
│   │   └── …                   # workflow, session, validation, …
│   ├── commercial/             # Advanced meshing modules
│   │   ├── mesh_engine.py      # Multi-algorithm engine with escalation
│   │   ├── quality_engine.py   # Quality auto-fix loop
│   │   ├── adaptive_loop.py    # OODA adaptive refinement
│   │   ├── snappy_hex_mesh.py  # SnappyHexMesh pipeline
│   │   ├── batch_mesh.py       # Headless batch meshing
│   │   └── …                   # ~30 modules (BL, mosaic, amr, …)
│   ├── api/server.py           # Optional headless FastAPI server
│   └── gui/
│       ├── main_window.py      # Main window (menu, export, undo)
│       ├── params_panel.py     # 4-tab parameters panel
│       ├── viewer_widget.py    # 3D viewer (PyVistaQt)
│       ├── new_case_wizard.py  # Guided 3-step wizard
│       ├── quality_panel.py    # checkMesh + PDF + auto-fix
│       ├── task_runner.py      # Single concurrency pattern (TaskManager)
│       ├── pdf_report.py       # PDF quality report generator
│       ├── log_panel.py        # Log output panel
│       ├── about_dialog.py     # About dialog
│       ├── branding.py         # Splash screen, app icon
│       ├── design_tokens.py    # Design token system
│       ├── theme.py            # Light/dark theme manager
│       ├── style.py            # QSS styling helpers
│       └── constants.py        # App constants
├── plugins/                    # Plugin system (Plugin ABC)
│   └── plugin_base.py          #   Plugin(ABC) base class
├── templates/                  # Case presets (JSON)
├── locale/                     # i18n translations + build scripts
│   ├── polyfoammesh.pro        #   Qt project file
│   ├── compile_i18n.bat        #   Windows batch compiler
│   └── compile_i18n.py         #   Python compiler
├── installer/                  # NSIS / Inno Setup scripts
├── tests/                      # pytest suite (96 files, ~1,000 tests)
├── benchmarks/                 # Meshing benchmark scripts and results
├── dist/                       # PyInstaller EXE output
│   └── PolyFoamMesh.exe        #   Standalone executable
└── docs/                       # Documentation + handoff notes
```

## Test

```bash
# Full suite (~1,000 tests; slow tests are marked, runs everything)
pytest tests/ -v

# Fast subset used by the pre-commit hook (pure logic, no GMSH/Qt/WSL)
pytest tests/test_workflow.py tests/test_meshdict_gen.py tests/test_journal.py \
       tests/test_template_engine.py tests/test_octopoda.py \
       tests/test_settings_migration.py tests/test_config.py tests/test_validation.py -q
```

### Pre-commit gate (local quality check)

The repo includes a `.pre-commit-config.yaml` that runs automatically before
every commit:

1. **ruff check** (rules E9/F/B) on staged `.py` files
2. **fast pytest subset** (pure-logic tests, ~18 s)

Install once with:

```bash
python -m pre_commit install
```

Bypass in an emergency with `git commit --no-verify`.

## Documentation for developers/maintainers

- **Rebuild exe + installer** (PyInstaller one-dir + Inno Setup):
  [docs/dev/DISTRIBUZIONE.md](docs/dev/DISTRIBUZIONE.md)
- **Internal technical notes** (handoff, polyhedral mesher status, development
  logs): [docs/dev/](docs/dev/) — not needed to use the app, useful only for
  those who touch the meshing engine

## License

GPLv3 — see [LICENSE](LICENSE) and [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
