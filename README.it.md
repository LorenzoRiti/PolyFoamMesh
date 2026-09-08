# PolyFoamMesh

![AI-Assisted Development](https://img.shields.io/badge/AI--Assisted-Development-blue)

*[English version](README.md)*

Sviluppato con un approccio human-in-the-loop: l'architettura e le scelte
fisiche sono dell'autore; il supporto dell'IA ha accelerato la stesura.
Dettagli in [AI_COLLABORATION.md](AI_COLLABORATION.md).

Genera mesh poliedriche per OpenFOAM partendo da uno STEP o uno STL, con
boundary layer e senza scrivere dizionari a mano. GMSH e cfMesh fanno la
mesh grezza; sopra c'è un convertitore tet→poly scritto da zero (dual
baricentrico, la stessa topologia che rende note le mesh poliedriche di
StarCCM+) e un motore di boundary layer che lavora direttamente sulla mesh
poliedrica, patch per patch.

Licenza: [GPLv3](LICENSE) · [Licenze di terze parti](THIRD_PARTY_LICENSES.md) · [Come contribuire](CONTRIBUTING.md)

## Screenshot

La scelta del mesher e il motore di boundary layer — le due cose che
questo progetto aggiunge davvero sopra GMSH/cfMesh grezzi — direttamente
nella finestra principale:

<p align="center">
  <img src="docs/assets/mesher-selector.png" alt="Selezione mesher: Cartesian cfMesh, Polymesh o FEM Tetra" width="49%">
  <img src="docs/assets/boundary-layers.png" alt="Pannello di configurazione boundary layer" width="49%">
</p>

*A sinistra: scegli la strategia di meshing per ogni case — Cartesian
cfMesh (WSL2), il mesher poliedrico nativo (senza WSL) o tetraedrico
puro per FEM. A destra: impostazioni boundary layer, con calcolo dello
spessore del primo strato guidato dal y+.*

E il risultato vero — una mesh poliedrica da 398K celle con 9 boundary
layer, `checkMesh` che passa (skewness 2.86, non-ortho 79.1°):

<p align="center">
  <img src="docs/assets/poly-mesh-overview.png" alt="Mesh poliedrica generata da PolyFoamMesh, vista d'insieme" width="49%">
  <img src="docs/assets/poly-mesh-closeup.png" alt="Dettaglio delle celle poliedriche" width="49%">
</p>

## Perché non uso semplicemente cfMesh

cfMesh e GMSH producono mesh grezze — hex o tet — e questo lo fa già
chiunque lavori con OpenFOAM. Il pezzo che mancava, e che questo progetto
aggiunge, viene dopo:

- un **convertitore tet→poly a dual baricentrico**, scritto per questo
  progetto (non è una funzione di cfMesh): prende il tet di GMSH e lo
  trasforma in una mesh 100% poliedrica;
- un **motore di boundary layer che gira sulla mesh poliedrica**, anche
  questo scritto da zero (puro numpy): estrude prismi sulle patch che
  scegli tu, non su tutto il dominio a forza — non conosco altri strumenti
  open source nell'ecosistema OpenFOAM che lo facciano con questo livello
  di controllo dentro una GUI;
- un ciclo di correzione automatica della qualità (checkMesh → fix →
  ri-mesh, fino a 3 iterazioni) e un meccanismo che cambia algoritmo da
  solo se quello scelto non raggiunge la qualità richiesta.

Dove questa pipeline funziona bene e dove no (ci sono geometrie concave su
cui il dual poliedrico non chiude perfettamente) è scritto senza girarci
intorno in [docs/residual_risks.md](docs/residual_risks.md).

## A chi serve

Se prepari mesh per OpenFOAM scrivendo `blockMeshDict`, `snappyHexMeshDict`
o `meshDict` a mano, e scopri se hai sbagliato qualcosa solo dopo aver
lanciato `checkMesh`, questa app ti risparmia gran parte di quel lavoro:
carichi la geometria, generi la mesh con un click, vedi la qualità mentre
viene calcolata ed esporti il case pronto per il solver.

## Come si confronta

Rispetto a **snappyHexMesh nudo**: stesso costo (gratis, entrambi girano su
OpenFOAM), ma qui la mesh poliedrica e il boundary layer sono automatici
invece che una sequenza di dizionari da scrivere a mano.

Rispetto a **Ansys Meshing, ANSA, Pointwise**: loro restano più maturi e
più capaci su geometrie davvero complesse, e coprono più solver — questo
progetto punta solo a OpenFOAM. In cambio è gratis, open source, e la mesh
poliedrica (di solito una feature da licenza commerciale) qui non costa
niente.

Non è un sostituto di un tool commerciale maturo su geometrie difficili —
è per chi lavora con OpenFOAM e vuole la mesh poliedrica senza pagare una
licenza per averla.

## Documentazione utente

- **[Installazione](docs/INSTALL.md)** — installer Windows o da sorgente
- **[Guida all'uso](docs/USER_GUIDE.md)** — come funziona il workflow, tab per tab
- **[Limiti noti](docs/residual_risks.md)** — cosa non funziona ancora e perché

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│                       GUI (PySide6)                       │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐  │
│  │  Geometry │ │    Mesh   │ │  Advanced │ │  Quality  │  │
│  │    Tab    │ │    Tab    │ │    Tab    │ │    Tab    │  │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘  │
│                                                           │
│            │          │          │          │             │
│            ▼          ▼          ▼          ▼             │
│  ┌───────────────────────────────────────────────────┐    │
│  │                 Core Engine Layer                 │    │
│  │   FeatureDetector   GMSH Wrapper   cfMesh Runner  │    │
│  │   meshDict Gen      Mesh Converter   Quality Fix  │    │
│  └───────────────────────────────────────────────────┘    │
│                                                           │
│             │              │               │              │
│             ▼              ▼               ▼              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │     GMSH     │ │    cfMesh    │ │   OpenFOAM   │       │
│  │   (native)   │ │    (WSL2)    │ │checkMesh/... │       │
│  └──────────────┘ └──────────────┘ └──────────────┘       │
└───────────────────────────────────────────────────────────┘
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
polyfoammesh/
├── src/polyfoammesh/
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
│   ├── polyfoammesh.pro        #   Qt project file
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

## Metodologia

Lo sviluppo segue un workflow human-in-the-loop: gli algoritmi fisici, le
tolleranze numeriche e la strategia di gestione errori sono progettati
dall'autore; il supporto dell'IA viene usato per il boilerplate (GUI, test
unitari, template di configurazione, parser di log) sotto quella
direzione, con ogni riga rivista prima del merge. Ripartizione completa,
incluso chi ha scritto cosa: [AI_COLLABORATION.md](AI_COLLABORATION.md).

L'autore umano è pienamente responsabile della correttezza e della
validità fisica di questo codice. L'IA è stata uno strumento di supporto,
non un autore autonomo.

## License

GPLv3 — vedi [LICENSE](LICENSE) e [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
