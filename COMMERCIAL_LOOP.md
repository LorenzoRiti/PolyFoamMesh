# CFMesh-AutoGUI → MEGA LOOP: Preprocessore CFD Commerciale

<!--
QUESTO FILE È IL PROMPT DA RIPETERE A OGNI SESSIONE.
L'agente DEVE leggere questo file, caricare Octopoda, e iniziare il loop.
IL LOOP NON SI FERMA FINCHÉ TUTTE LE P0 NON SONO COMPLETATE E VERDI.
-->

## Visione

Trasformare CFMesh-AutoGUI nel **preprocessore CFD definitivo per OpenFOAM e BaramFlow** — l'equivalente di **Ansys Meshing + Fluent Watertight Workflow + ANSA + Pointwise**, ma in un'unica app desktop gratuita/open-source, elegantissima, con workflow automatico.

## Principi di design (vincoli assoluti)

1. **UN CLICK** — L'utente deve poter ottenere una mesh decente con UN SOLO CLICK. "Quick Mesh" deve funzionare su qualsiasi geometria.
2. **AUTOMAGICO** — Detection automatica di: feature, gap, curvature, regioni, patches, pareti, inlet/outlet. L'utente non deve toccare niente.
3. **GUIDATO** — Wizard progressivo (tipo Ansys Fluent Watertight Workflow) che guida l'utente passo-passo.
4. **TRASPARENTE** — L'utente VEDE cosa succede: preview 3D in tempo reale, mesh che appare, qualità che si aggiorna.
5. **ZERO CFD JARGON** — Nascondi dettagli tecnici dietro slider "Molto Fine / Fine / Media / Grossolana / Molto Grossolana".
6. **TUTTO IN LOCO** — Geometria → Mesh → BC → Setup → Export → Run, tutto nella stessa app.
7. **FEEDBACK OGNI STEP** — Barra di progresso, log colorato, preview 3D, quality dashboard.

## Pipeline completa (cosa DEVE fare l'app in ordine)

```
┌────────────────────────────────────────────────────────────┐
│  CFMesh-AutoGUI — Commercial CFD Preprocessor Pipeline     │
├────────────────────────────────────────────────────────────┤
│ STEP 1: IMPORT GEOMETRIA (STEP/STL/IGES/BREP)             │
│   ├── Auto-detect unità (mm/cm/m/in)                       │
│   ├── Auto-heal (stitch, fill holes, remove sliver faces)  │
│   ├── Auto-detect features (sharp edges, curvature, gaps)  │
│   ├── Auto-classify patches (wall/inlet/outlet/symmetry)   │
│   └── Preview 3D con sezione, misura, zoom                 │
│                                                            │
│ STEP 2: QUICK MESH (UN CLICK)                             │
│   ├── Auto-size cella basata su: bounding box + curvatura  │
│   ├── Auto-BL (wall detection + y+ target + layers)       │
│   ├── Cartesian fill (cfMesh) O polyhedral O tetrahedral   │
│   ├── Preview mesh 3D con qualità color-coded             │
│   └── ETA + cell count stimato                             │
│                                                            │
│ STEP 3: ADVANCED MESH (quando Quick Mesh non basta)       │
│   ├── Local refinement: box/sphere/cylinder/point sources  │
│   ├── BL customization: select patches, N layers, growth   │
│   ├── Inflation layers su selezioni                        │
│   ├── Face sizing: mesh size su facce specifiche           │
│   └── Multiple regions (CHT, FSI, multi-material)         │
│                                                            │
│ STEP 4: QUALITY CHECK + AUTO-FIX                           │
│   ├── checkMesh (skewness, non-ortho, aspect ratio, ecc.)  │
│   ├── Quality heatmap 3D (celle colorate per qualità)      │
│   ├── Auto-fix: smooth + refine bad cells (max 3 iter)    │
│   ├── Report PDF (metriche, istogramma, screenshot)        │
│   └── Soglie: default commerciali, editabili               │
│                                                            │
│ STEP 5: BOUNDARY CONDITIONS (PREVIEW + SETUP)             │
│   ├── Auto-rileva tipo patch: wall/inlet/outlet/symmetry   │
│   ├── UI visiva: clicca patch nel 3D → assegna nome/tipo   │
│   ├── Template BC: internal_flow, external_aero, CHT       │
│   ├── Preview condizioni: colora patch per tipo            │
│   └── Export OpenFOAM 0/ (boundary, U, p, k, omega, nut)  │
│                                                            │
│ STEP 6: SOLVER SETUP (PREVIEW)                            │
│   ├── Solver selector: simpleFoam/pimpleFoam/pisoFoam/     │
│   │   overPimpleDyMFoam/reactingFoam/chtMultiRegionFoam   │
│   ├── Scheme presets: steady/low/med/high turbolenza       │
│   ├── Turbolenza: laminar/kEpsilon/kOmegaSST/LES/DES      │
│   ├── Materiali: air/water/idealGas/polymaterial           │
│   └── Export: controlDict, fvSchemes, fvSolution, fields   │
│                                                            │
│ STEP 7: EXPORT + RUN                                        │
│   ├── Export: OpenFOAM case / BaramFlow / CGNS / VTU      │
│   ├── Run local (WSL2) o submit a cluster (Slurm/AWS)     │
│   ├── Monitor: residuals plot, force coeff, probes         │
│   └── Post: paraView launch / risultati in viewer          │
└────────────────────────────────────────────────────────────┘
```

## P0 — Core Pipeline (IMPLEMENTA SUBITO, in ordine)

Ogni P0 è **un modulo completo** in `src/cfmesh_autogui/commercial/`. Ogni modulo DEVE:
- Avere type hints completi
- Avere docstring PEP 257
- Essere testato (`tests/test_NOME.py`)
- Essere integrato nella UI (MainWindow o wizard)
- Passare `ruff check --fix`

DOPO OGNI MODULO → `python -m py_compile` + `pytest tests/test_NOME.py -x -q`
Se fallisce → correggi → ripeti (max 3 cicli) → POI passa al prossimo.

### P0.1: `commercial/geometry_pipeline.py` — Import + Healing + Feature Detection
- Carica STEP/STL/IGES/BREP con auto-detect formato
- Healing automatico: stitch (gap < 0.1mm), fill holes (diam < 5mm), remove sliver (area < 1mm²)
- Feature extraction: sharp edges (angolo diedro > 30°), curvature (raggio < 10% BB), thin gaps (< 5% BB)
- Auto-classify patches: wall (angle > 45° da orizzontale), inlet/outlet (facce piane su bounding box), symmetry
- Auto-detect unità: confronta bounding box con valori tipici (mm/cm/m/in)
- Preview: mostra feature lines, curvature map, patch colors
- Integrazione: `NewCaseWizard` Step 1 migliorato (anteprima feature)
- Test: `tests/test_geometry_pipeline.py`

### P0.2: `commercial/mesh_engine.py` — Motore mesh multi-algoritmo
Unificare TUTTI i metodi di meshing in un'unica interfaccia:
- `CartesianHex` (cfMesh via WSL2) — default, hex-dominant
- `Polyhedral` (cfMesh polyDualMesh post-process) — per Star-CCM+ style
- `Tetrahedral` (GMSH Direct) — per geometrie complesse, no WSL
- `HexCorePolyBoundary` (Mosaic-style) — hex core + poly transition + prism BL
- `CartesianCutCell` (cfMesh cartesianCutMesh) — cut-cell type
Auto-select: basato su geometria + solver target. Fallback automatico se un metodo fallisce.
Parametri esposti come slider "Grossolana ↔ Fine" (5 livelli), non numeri.
- Test: `tests/test_mesh_engine.py`

### P0.3: `commercial/quick_mesh.py` — Un click, mesh pronta
- Prende geometria da P0.1
- Auto-sceglie algoritmo (P0.2)
- Auto-sceglie cell size (curvature + thickness + BB)
- Auto-sceglie BL (wall detection + y+ target 30 per RANS)
- Genera mesh, salva in `constant/polyMesh`, controlla qualità
- Output: cell count, quality report sintetico, preview mesh
- UI: pulsante "Quick Mesh (Ctrl+M)" sempre visibile nella Ribbon
- Integrato in wizard come Step 2 opzionale ("Usa Quick Mesh?" → Sì/No)
- Test: `tests/test_quick_mesh.py`

### P0.4: `commercial/quality_engine.py` — Quality check + Heatmap + Auto-fix
- Metriche complete: max skewness, max non-orthogonality, max aspect ratio, min volume, n neg cells
- 3D heatmap: colore per cella basato su peggior metrica (verde=OK, giallo=warning, rosso=bad)
- Istogramma qualità (matplotlib embeddato in QWidget)
- Auto-fix intelligente:
  - Skewness > 0.9 → local smoothing (Laplace + optimization)
  - Non-ortho > 70 → refine region + smooth
  - Neg volume → remove + remesh locale
  - Aspect ratio > 1000 → split cells
  - Max 3 iterazioni, stop se convergenza
- Report PDF: metrica tabella + istogramma + screenshot 3D + raccomandazioni
- Test: `tests/test_quality_engine.py`

### P0.5: `commercial/bc_editor.py` — Boundary Condition visual editor
- Legge mesh patches da `constant/polyMesh/boundary`
- Mostra patches nel 3D viewer con colori per tipo
- Auto-detect tipo basato su:
  - Nome (inlet/outlet/wall/symmetry nel nome patch)
  - Normale (punto verso bounding box → inlet/outlet)
  - Geometria (curvo → wall, piatto su bordo → patch)
- Editor: click patch nel 3D → rinomina, cambia tipo
- Template BC: carica preset per tipo di caso
- Export: scrive `0/` directory con boundary, U, p, epsilon, omega, k, nut
  con valori di default intelligenti
- Test: `tests/test_bc_editor.py`

### P0.6: `commercial/solver_setup.py` — Solver config automatico
- Template per ogni solutore:
  - `simpleFoam` — steady RANS, SIMPLE
  - `pimpleFoam` — transient RANS, PIMPLE
  - `pisoFoam` — transient LES, PISO
  - `reactingFoam` — combustione
  - `chtMultiRegionFoam` — conjugate heat transfer
  - `overPimpleDyMFoam` — overset + moving mesh
- Turbolenza: laminar / kEpsilon / kOmegaSST / LES (Smagorinsky / WALE) / DES
- Scheme presets: "Robusto" (upwind), "Bilanciato" (blended), "Accurato" (linearUpwind)
- Materiali: fluido + solido (per CHT) con tabella proprietà
- Export: `system/controlDict`, `fvSchemes`, `fvSolution`, `constant/transportProperties`, `constant/turbulenceProperties`
- Test: `tests/test_solver_setup.py`

### P0.7: `commercial/template_engine.py` — Template manager + Wizard veloce
- Template predefiniti:
  - `internal_flow` — pipe, manifold, elbow, valve
  - `external_aero` — airfoil, car, drone, building
  - `cht` — heat sink, heat exchanger, electronics cooling
  - `multiphase` — damBreak, tank filling, wave
  - `moving_body` — propeller, turbine, valve opening
  - `conjugate_ht` — PCB cooling, LED cooling
  - `combustion` — burner, flame, combustor
- Ogni template contiene: geometria di esempio, mesh preset, BC, solver setup
- UI: "New Case from Template" → griglia cards con icona + descrizione
- Template user-defined: salva caso corrente come template
- Test: `tests/test_template_engine.py`

### P0.8: `commercial/one_click_run.py` — Geometria → Mesh → BC → Setup → Run
- Un pulsante: "Full Auto"
- Input: geometry file + output dir + qualità target
- Auto: import → heal → mesh → check → fix → BC → solver setup → run
- Output: caso OpenFOAM completo + report qualità + residuals
- Barra di progresso unica + log dettagliato
- Test: `tests/test_one_click_run.py`

## P1 — Enhancement Commerciali (dopo tutte le P0 verdi)

| # | Modulo | Ispirato da | Descrizione |
|---|--------|-------------|-------------|
| 1 | `commercial/amr.py` | Star-CCM+ AMR | Adaptive mesh refinement basato su soluzione |
| 2 | `commercial/overset.py` | suggar++ | Overset grids per moving bodies |
| 3 | `commercial/morphing.py` | ANSA Morph | RBF/FFD mesh deformation |
| 4 | `commercial/batch_mesh.py` | Pointwise Glyph | Batch meshing con journal |
| 5 | `commercial/exporter.py` | — | CGNS, VTU, Ansys, Abaqus, SU2, Fluent |
| 6 | `commercial/parallel_mesh.py` | cfMesh MPI | Domain decomposition + parallel fill |
| 7 | `commercial/multi_region.py` | chtMultiRegion | Conformal interfaces per CHT/FSI |
| 8 | `commercial/ai_assist.py` | ML-driven | ML per cell sizing, quality prediction |
| 9 | `commercial/cloud_submit.py` | — | Slurm/AWS Batch job submission |
| 10 | `commercial/journal.py` | — | Macro recorder + Python scripting API |

## P2 — Enterprise (dopo P0+P1 verdi)

- Licensing: floating license server / subscription
- SSO: Azure AD, Okta, Google
- REST API: FastAPI headless per CI/CD
- Plugin marketplace: download + install da repository
- Audit trail: Octopoda + blockchain hash
- Dashboard: Grafana + InfluxDB per telemetria d'uso
- Enterprise installer: MSI + GPO + silent deploy

## UX Guidelines (vincoli assoluti)

```
┌─────────────────────────────────────────────────────────┐
│ RIBBON (come Ansys Workbench)                          │
│ [Home] [Geometry] [Mesh] [Quality] [BC] [Solver] [Run] │
├─────────────────────────────────────────────────────────┤
│ WORKFLOW TREE (sinistra)       │  MAIN CONTENT          │
│ ✅ 1. Import Geometry          │  [3D Viewer +          │
│ ✅ 2. Quick Mesh               │   Parametri +          │
│ ⏳ 3. Quality Check            │   Wizard step]         │
│ ⬜ 4. Boundary Conditions      │                        │
│ ⬜ 5. Solver Setup             │                        │
│ ⬜ 6. Export & Run             │                        │
├─────────────────────────────────────────────────────────┤
│ STATUS BAR: cells | max_skew | non-ortho | WSL | time  │
└─────────────────────────────────────────────────────────┘
```

- **Slider ovunque**: "Molto Fine / Fine / Media / Grossolana / Molto Grossolana" invece di numeri
- **Tooltip su ogni input**: spiegazione in italiano semplice + formula fisica
- **Preview 3D live**: ogni cambiamento parametro aggiorna viewer in tempo reale
- **Colori semantici**: verde (OK), giallo (warning), rosso (bad) per ogni metrica
- **Progress bar**: ETA + percentuale + fase corrente + log scorrevole
- **Shortcuts**: Ctrl+M (Quick Mesh), Ctrl+Shift+Q (Quality), Ctrl+R (Run), Ctrl+N (New)
- **Tema**: ANSYS-inspired, light/dark/system, design tokens

## Regole di esecuzione del loop

1. LEGGI `COMMERCIAL_LOOP.md` (questo file)
2. CARICA Octopoda: `octo = OctopodaRuntime(); octo.remember("phase", "commercial_loop")`
3. CONTROLLA `octo.recall("implemented_features")` — cosa è già stato fatto?
4. SCEGLI la prossima P0 NON implementata (dalla lista sopra, in ordine)
5. IMPLEMENTA: crea `commercial/NOME.py` + `tests/test_NOME.py` + integra UI
6. VERIFICA: `python -m py_compile` + `pytest tests/test_NOME.py -x -q`
7. SE FALLISCE → analizza → correggi → ripeti (max 3)
8. SE PASSA → `ruff check --fix` → registra in Octopoda
9. REGISTRA:
```python
done = octo.recall("implemented_features") or []
done.append({"feature": "P0.N — Nome", "module": "commercial/NOME.py", "status": "PASS"})
octo.remember("implemented_features", done)
```
10. SE mancano altre P0 → TORNA AL PASSO 4 (loop)
11. SE tutte P0 verdi → congratulazioni! → passa a P1

## Criteri di accettazione (tutte le P0)

- [x] Ogni modulo compila senza errori (`python -m py_compile`)
- [x] Ogni test passa (`pytest tests/test_NOME.py -x -q`)
- [x] Integrato in `main_window.py` (menu, ribbon o wizard)
- [x] L'utente può completare la pipeline dal wizard senza aprire terminale
- [x] Quick Mesh produce mesh su geometria STL di test in < 60s
- [ ] Report PDF si apre correttamente
- [x] Export caso OpenFOAM è valido (checkMesh non dà errori fatali)
- [x] Tutto gira su Windows 11 + WSL2 Ubuntu + OpenFOAM v2512
