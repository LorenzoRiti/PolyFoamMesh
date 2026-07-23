# CFMesh-AutoGUI

**Commercial-grade cartesian mesh generator for OpenFOAM** — combina GMSH + cfMesh
in una GUI professionale per produrre mesh pronte per CFD senza intervento manuale.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   GUI (PySide6)                      │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │Geometry  │ │  Mesh    │ │ Advanced │ │ Quality │ │
│  │   Tab    │ │   Tab    │ │   Tab    │ │   Tab   │ │
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

## Key Features v2.0

| Feature | Description | Status |
|---------|-------------|--------|
| **GMSH + cfMesh Hybrid** | GMSH analyzes curvature/sizing, cfMesh fills volume | P0 |
| **GMSH Direct** | Full tetra+BL mesh on Windows, no WSL needed | P0 |
| **Feature Detection** | Automatic sharp edges, thin gaps, curvature analysis | P0 |
| **New Case Wizard** | 3-step guided workflow (Geometry → Mesh → Quality) | P0 |
| **Tabbed Params Panel** | Geometry/Mesh/Advanced/Quality tabs | P0 |
| **Quality Pipeline** | checkMesh → auto-fix → re-run (max 3 iterations) | P0 |
| **Quick Mesh (1-Click)** | Ctrl+M for instant mesh with auto-suggested params | P0 |
| **Export CGNS/VTU** | Export mesh in CGNS and ParaView VTU formats | P1 |
| **Clipping Plane** | Interactive section cut in 3D viewer | P1 |
| **Undo/Redo** | Ctrl+Z/Y with QUndoStack + ParamChangeCommand | P0 |
| **Case Templates** | Presets for internal flow, external aero, CHT | P1 |
| **QualityFixWorker** | Auto-fix loop — checkMesh → fix → re-run (max 3) | P0 |
| **ParaView Integration** | Tools > Launch ParaView (subprocess.Popen) | P1 |
| **Plugin System** | Plugin(ABC) via importlib, plugins/ directory | P2 |
| **PDF Quality Report** | Professional mesh quality report with charts | P0 |
| **Italian Localization** | UI + help + docs in Italiano | P1 |

## Requirements

- **Python** 3.10+
- **GMSH** `pip install gmsh` — runs natively on Windows
- **OpenFOAM v2512** via WSL2 Ubuntu (for cfMesh volume fill)
- **Windows 10/11** (WSL2 support required for cfMesh path)

## Installation

```bash
# 1. Install Python package
pip install -e .

# 2. Install GMSH Python API
pip install gmsh meshio reportlab

# 3. (Optional) OpenFOAM in WSL2
#    wsl --install -d Ubuntu
#    sudo apt install openfoam2516
```

## Usage

```bash
cfmesh-autogui
```

Or directly:

```bash
python -m cfmesh_autogui.app
```

### Quick Start

1. **Ctrl+O** — Load STEP/STL geometry
2. **Ctrl+M** — Quick Mesh (one-click)
3. Use **New Case Wizard** (File → New Case) for guided workflow
4. View mesh quality in the **Quality** tab
5. **Export** → CGNS/VTU for ParaView

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Load Geometry (STEP/STL) |
| Ctrl+R | Generate Mesh / Cancel |
| Ctrl+M | Quick Mesh (one-click) |
| Ctrl+N | Reset All |
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| Ctrl+Q | Exit |

## Project Structure

```
cfmesh-autogui/
├── src/cfmesh_autogui/
│   ├── app.py                  # Entry point (splash, theme, i18n)
│   ├── config.py               # OpenFOAM/WSL configuration
│   ├── core/
│   │   ├── geometry.py         # CAD import (cadquery, trimesh)
│   │   ├── gmsh_wrapper.py     # GMSH Python API wrapper
│   │   ├── meshdict_gen.py     # cfMesh meshDict generator
│   │   ├── feature_detector.py # Sharp edges, gaps, curvature
│   │   ├── stl_writer.py       # Surface STL export
│   │   ├── boundary_reader.py  # OpenFOAM boundary parser
│   │   ├── case_setup.py       # OpenFOAM case setup
│   │   ├── openfoam_runner.py  # cfMesh runner + error analysis
│   │   └── mesh_converter.py   # MSH → OpenFOAM conversion
│   └── gui/
│       ├── main_window.py      # Main window (menu, export, undo)
│       ├── params_panel.py     # 4-tab parameter panel
│       ├── viewer_widget.py    # 3D viewer (PyVistaQt)
│       ├── new_case_wizard.py  # 3-step guided wizard
│       ├── quality_panel.py    # checkMesh + PDF + auto-fix
│       ├── pdf_report.py       # PDF quality report generator
│       ├── log_panel.py        # Log output panel
│       ├── about_dialog.py     # About dialog
│       ├── branding.py         # Splash screen, app icon
│       ├── design_tokens.py    # Design token system
│       ├── theme.py            # Light/dark theme manager
│       ├── style.py            # QSS style helpers
│       └── constants.py        # App constants
├── plugins/                    # Plugin system (Plugin ABC)
│   └── plugin_base.py          #   Plugin(ABC) base class
├── templates/                  # Case presets (JSON)
├── locale/                     # i18n translations + compile scripts
│   ├── cfmesh_autogui.pro      #   Qt project file
│   ├── compile_i18n.bat        #   Windows batch compiler
│   └── compile_i18n.py         #   Python compiler script
├── installer/                  # NSIS / Inno Setup scripts
├── tests/                      # pytest test suite (114 tests)
├── dist/                       # PyInstaller EXE output
│   └── CFMesh-AutoGUI.exe      #   Standalone executable
└── docs/                       # Documentation
```

## Testing

```bash
pytest tests/ -v
# 114 passed, 1 skipped
```

## License

MIT License — © 2026 CFMesh-AutoGUI Project
