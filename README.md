# PolyFoamMesh

**Preprocessore CFD open source per OpenFOAM** — combina GMSH + cfMesh in una
GUI che genera mesh pronte per CFD senza scrivere dizionari OpenFOAM a mano.

Licenza: [GPLv3](LICENSE) · [Third-party licenses](THIRD_PARTY_LICENSES.md) · [Contributing](CONTRIBUTING.md)

## A chi serve

Sei un ingegnere CFD, uno studente o un ricercatore che oggi prepara mesh
OpenFOAM scrivendo `blockMeshDict`/`snappyHexMeshDict`/`meshDict` a mano e
scoprendo i problemi di qualità solo dopo aver lanciato `checkMesh`. Questa
app mette GMSH e cfMesh dietro una GUI: carichi una geometria, generi una
mesh con un click, vedi la qualità mentre viene calcolata, esporti il case
pronto per il solver.

## Come si confronta

| | PolyFoamMesh | snappyHexMesh "nudo" | Ansys Meshing / ANSA / Pointwise |
|---|---|---|---|
| Costo | Gratis, open source (GPLv3) | Gratis (parte di OpenFOAM) | Licenze commerciali, spesso costose |
| Automazione | 1-click + auto quality-fix | Manuale, dizionari a testo | Alta, ma workflow proprietario |
| Maturità | Beta attiva, limiti noti e documentati | Maturo, ma nessuna GUI | Maturo |
| Vincolo | Solo OpenFOAM | Solo OpenFOAM | Multi-solver |

Non è un sostituto di un tool commerciale maturo su geometrie molto complesse
— è pensato per chi lavora con OpenFOAM e vuole risparmiare le ore che oggi
si perdono a scrivere dizionari e a interpretare `checkMesh` a mano. I limiti
noti sono documentati in [docs/residual_risks.md](docs/residual_risks.md),
non nascosti.

## Documentazione utente

- **[Installazione](docs/INSTALL.md)** — installer Windows o da sorgente
- **[Guida all'uso](docs/USER_GUIDE.md)** — come funziona il workflow, tab per tab
- **[Limiti noti](docs/residual_risks.md)** — cosa non funziona ancora e perché

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

## Installazione rapida

Requisiti: Windows 10/11, Python 3.11+ (solo per l'installazione da
sorgente), opzionalmente OpenFOAM v2512 in WSL2. Guida completa (installer
precompilato o da sorgente): **[docs/INSTALL.md](docs/INSTALL.md)**.

```bash
pip install -e ".[test]"
pip install gmsh meshio reportlab
polyfoammesh
```

Workflow e scorciatoie: **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)**.

## Project Structure

```
cfmesh-autogui/
├── src/cfmesh_autogui/
│   ├── app.py                  # Entry point (splash, theme, i18n)
│   ├── config.py               # OpenFOAM/WSL configuration
│   ├── _version.py             # Single version source (imported everywhere)
│   ├── core/                   # Engine layer (no GUI imports)
│   │   ├── geometry.py         # CAD import (cadquery, trimesh)
│   │   ├── gmsh_wrapper.py     # GMSH Python API wrapper
│   │   ├── meshdict_gen.py     # cfMesh meshDict generator
│   │   ├── feature_detector.py # Sharp edges, gaps, curvature
│   │   ├── stl_writer.py       # Surface STL export
│   │   ├── boundary_reader.py  # OpenFOAM boundary parser
│   │   ├── case_setup.py       # OpenFOAM case setup + controlDict
│   │   ├── openfoam_runner.py  # cfMesh runner + error analysis
│   │   ├── mesh_converter.py   # MSH → OpenFOAM conversion
│   │   └── …                   # workflow, session, validation, …
│   ├── commercial/             # Advanced meshing modules
│   │   ├── mesh_engine.py      # Multi-algorithm engine + escalation
│   │   ├── quality_engine.py   # Quality auto-fix loops
│   │   ├── adaptive_loop.py    # OODA adaptive refinement
│   │   ├── snappy_hex_mesh.py  # SnappyHexMesh pipeline
│   │   ├── batch_mesh.py       # Headless batch meshing
│   │   └── …                   # ~30 modules (BL, mosaic, amr, …)
│   ├── api/server.py           # Optional FastAPI headless server
│   └── gui/
│       ├── main_window.py      # Main window (menu, export, undo)
│       ├── params_panel.py     # 4-tab parameter panel
│       ├── viewer_widget.py    # 3D viewer (PyVistaQt)
│       ├── new_case_wizard.py  # 3-step guided wizard
│       ├── quality_panel.py    # checkMesh + PDF + auto-fix
│       ├── task_runner.py      # One concurrency pattern (TaskManager)
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
├── tests/                      # pytest test suite (96 files, ~1.000 tests)
├── benchmarks/                 # Meshing benchmark scripts + results
├── dist/                       # PyInstaller EXE output
│   └── PolyFoamMesh.exe        #   Standalone executable
└── docs/                       # Documentation + handoff notes
```

## Testing

```bash
# Full suite (~1.000 tests; slow tests marked, run everything)
pytest tests/ -v

# Fast subset used by the pre-commit hook (pure logic, no GMSH/Qt/WSL)
pytest tests/test_workflow.py tests/test_meshdict_gen.py tests/test_journal.py \
       tests/test_template_engine.py tests/test_octopoda.py \
       tests/test_settings_migration.py tests/test_config.py tests/test_validation.py -q
```

### Pre-commit gate (local quality check)

The repo ships a `.pre-commit-config.yaml` that runs automatically before
every commit:

1. **ruff check** (rules E9/F/B) on staged `.py` files
2. **fast pytest subset** (pure-logic tests, ~18 s)

Install once with:

```bash
python -m pre_commit install
```

Bypass in an emergency with `git commit --no-verify`.

## Documentazione per sviluppatori/manutentori

- **Rebuild exe + installer** (PyInstaller one-dir + Inno Setup):
  [docs/dev/DISTRIBUZIONE.md](docs/dev/DISTRIBUZIONE.md)
- **Note tecniche interne** (handoff, stato del mesher poliedrico, log di
  sviluppo): [docs/dev/](docs/dev/) — non necessarie per usare l'app, utili
  solo per chi tocca l'engine di meshing

## License

GPLv3 — vedi [LICENSE](LICENSE) e [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
