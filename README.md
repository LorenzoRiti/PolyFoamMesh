# PolyFoamMesh

**Preprocessore CFD open source per OpenFOAM** — combina GMSH + cfMesh in una
GUI che genera mesh pronte per CFD senza scrivere dizionari OpenFOAM a mano.

Licenza: [GPLv3](LICENSE) · [Licenze di terze parti](THIRD_PARTY_LICENSES.md) · [Come contribuire](CONTRIBUTING.md)

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

## Architettura

```
┌─────────────────────────────────────────────────────┐
│                   GUI (PySide6)                      │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │Geometria │ │  Mesh    │ │ Avanzato │ │ Qualità │ │
│  │   Tab    │ │   Tab    │ │   Tab    │ │   Tab   │ │
│  └─────────┘ └──────────┘ └──────────┘ └─────────┘ │
│        │             │              │           │    │
│        ▼             ▼              ▼           ▼    │
│  ┌──────────────────────────────────────────────┐   │
│  │           Livello Core Engine                 │   │
│  │  FeatureDetector  GMSH Wrapper  cfMesh Runner │   │
│  │  meshDict Gen     Mesh Converter  Quality Fix │   │
│  └──────────────────────────────────────────────┘   │
│        │             │              │                │
│        ▼             ▼              ▼                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐    │
│  │  GMSH    │ │  cfMesh  │ │  OpenFOAM        │    │
│  │ (nativo) │ │ (WSL2)   │ │  checkMesh/...   │    │
│  └──────────┘ └──────────┘ └──────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## Funzionalità principali v2.0

| Funzionalità | Descrizione | Stato |
|---------|-------------|--------|
| **GMSH + cfMesh Hybrid** | GMSH analizza curvatura/sizing, cfMesh riempie il volume | P0 |
| **GMSH Direct** | Mesh tetra+BL completa su Windows, senza WSL | P0 |
| **Feature Detection** | Rilevamento automatico spigoli vivi, gap sottili, curvatura | P0 |
| **New Case Wizard** | Workflow guidato in 3 passi (Geometria → Mesh → Qualità) | P0 |
| **Pannello parametri a tab** | Tab Geometria/Mesh/Avanzato/Qualità | P0 |
| **Pipeline qualità** | checkMesh → auto-fix → ri-esecuzione (max 3 iterazioni) | P0 |
| **Quick Mesh (1-Click)** | Ctrl+M per mesh istantanea con parametri auto-suggeriti | P0 |
| **Export CGNS/VTU** | Esporta la mesh nei formati CGNS e ParaView VTU | P1 |
| **Piano di sezione** | Sezione interattiva nel viewer 3D | P1 |
| **Annulla/Ripeti** | Ctrl+Z/Y con QUndoStack + ParamChangeCommand | P0 |
| **Template di case** | Preset per flusso interno, aerodinamica esterna, CHT | P1 |
| **QualityFixWorker** | Loop di auto-fix — checkMesh → fix → ri-esecuzione (max 3) | P0 |
| **Integrazione ParaView** | Tools > Launch ParaView (subprocess.Popen) | P1 |
| **Sistema plugin** | Plugin(ABC) via importlib, cartella plugins/ | P2 |
| **Report qualità PDF** | Report professionale sulla qualità della mesh con grafici | P0 |
| **Localizzazione italiana** | UI + help + docs in italiano | P1 |

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

## Struttura del progetto

```
polyfoammesh/
├── src/polyfoammesh/
│   ├── app.py                  # Entry point (splash, theme, i18n)
│   ├── config.py               # Configurazione OpenFOAM/WSL
│   ├── _version.py             # Unica fonte di verità per la versione (importata ovunque)
│   ├── core/                   # Livello motore (nessun import GUI)
│   │   ├── geometry.py         # Import CAD (cadquery, trimesh)
│   │   ├── gmsh_wrapper.py     # Wrapper della API Python di GMSH
│   │   ├── meshdict_gen.py     # Generatore meshDict per cfMesh
│   │   ├── feature_detector.py # Spigoli vivi, gap, curvatura
│   │   ├── stl_writer.py       # Export STL di superficie
│   │   ├── boundary_reader.py  # Parser dei boundary OpenFOAM
│   │   ├── case_setup.py       # Setup case OpenFOAM + controlDict
│   │   ├── openfoam_runner.py  # Runner cfMesh + analisi errori
│   │   ├── mesh_converter.py   # Conversione MSH → OpenFOAM
│   │   └── …                   # workflow, session, validazione, …
│   ├── commercial/             # Moduli di meshing avanzato
│   │   ├── mesh_engine.py      # Motore multi-algoritmo con escalation
│   │   ├── quality_engine.py   # Loop di auto-fix qualità
│   │   ├── adaptive_loop.py    # Raffinamento adattivo OODA
│   │   ├── snappy_hex_mesh.py  # Pipeline SnappyHexMesh
│   │   ├── batch_mesh.py       # Meshing batch headless
│   │   └── …                   # ~30 moduli (BL, mosaic, amr, …)
│   ├── api/server.py           # Server FastAPI headless opzionale
│   └── gui/
│       ├── main_window.py      # Finestra principale (menu, export, undo)
│       ├── params_panel.py     # Pannello parametri a 4 tab
│       ├── viewer_widget.py    # Viewer 3D (PyVistaQt)
│       ├── new_case_wizard.py  # Wizard guidato in 3 passi
│       ├── quality_panel.py    # checkMesh + PDF + auto-fix
│       ├── task_runner.py      # Pattern unico di concorrenza (TaskManager)
│       ├── pdf_report.py       # Generatore report qualità PDF
│       ├── log_panel.py        # Pannello di output log
│       ├── about_dialog.py     # Finestra About
│       ├── branding.py         # Splash screen, icona app
│       ├── design_tokens.py    # Sistema di design token
│       ├── theme.py            # Gestore tema chiaro/scuro
│       ├── style.py            # Helper QSS di stile
│       └── constants.py        # Costanti dell'app
├── plugins/                    # Sistema plugin (Plugin ABC)
│   └── plugin_base.py          #   Classe base Plugin(ABC)
├── templates/                  # Preset di case (JSON)
├── locale/                     # Traduzioni i18n + script di compilazione
│   ├── polyfoammesh.pro        #   File progetto Qt
│   ├── compile_i18n.bat        #   Compilatore batch Windows
│   └── compile_i18n.py         #   Compilatore Python
├── installer/                  # Script NSIS / Inno Setup
├── tests/                      # Suite pytest (96 file, ~1.000 test)
├── benchmarks/                 # Script e risultati dei benchmark di meshing
├── dist/                       # Output EXE di PyInstaller
│   └── PolyFoamMesh.exe        #   Eseguibile standalone
└── docs/                       # Documentazione + note di handoff
```

## Test

```bash
# Suite completa (~1.000 test; i test lenti sono marcati, esegue tutto)
pytest tests/ -v

# Sottoinsieme veloce usato dal pre-commit hook (logica pura, no GMSH/Qt/WSL)
pytest tests/test_workflow.py tests/test_meshdict_gen.py tests/test_journal.py \
       tests/test_template_engine.py tests/test_octopoda.py \
       tests/test_settings_migration.py tests/test_config.py tests/test_validation.py -q
```

### Gate pre-commit (controllo qualità locale)

Il repo include un `.pre-commit-config.yaml` che gira automaticamente prima
di ogni commit:

1. **ruff check** (regole E9/F/B) sui file `.py` in staging
2. **sottoinsieme pytest veloce** (test di logica pura, ~18 s)

Installa una volta con:

```bash
python -m pre_commit install
```

Bypassa in caso di emergenza con `git commit --no-verify`.

## Documentazione per sviluppatori/manutentori

- **Rebuild exe + installer** (PyInstaller one-dir + Inno Setup):
  [docs/dev/DISTRIBUZIONE.md](docs/dev/DISTRIBUZIONE.md)
- **Note tecniche interne** (handoff, stato del mesher poliedrico, log di
  sviluppo): [docs/dev/](docs/dev/) — non necessarie per usare l'app, utili
  solo per chi tocca l'engine di meshing

## Licenza

GPLv3 — vedi [LICENSE](LICENSE) e [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
