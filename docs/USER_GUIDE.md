# Guida all'uso

Questa guida spiega **come funziona** il workflow, non solo dove cliccare —
utile se vuoi capire perché l'app ti chiede una cosa prima di un'altra.

## Il problema che risolve

Preparare una mesh per OpenFOAM "a mano" oggi significa scrivere dizionari
(`blockMeshDict`, `snappyHexMeshDict`, `meshDict`) a testo libero, indovinare
le dimensioni di cella giuste, e scoprire i problemi di qualità solo dopo
aver lanciato `checkMesh` — spesso dopo un run lungo. PolyFoamMesh mette
GMSH (analisi geometria, sizing) e cfMesh (riempimento volume) dietro una
GUI che fa questi passaggi con un click, mostra la mesh mentre si costruisce,
e corregge da solo i problemi di qualità più comuni.

## Flusso in 4 tappe

```
1. GEOMETRY  →  2. MESH  →  3. QUALITY  →  4. EXPORT
   (carica,       (genera,     (checkMesh    (OpenFOAM
    ripara)        Quick/       + auto-fix)   case / CGNS
                   Advanced)                  / VTU)
```

### 1. Geometry tab
Carichi STEP o STL (`Ctrl+O` o drag&drop). L'app rileva automaticamente
feature (spigoli vivi, gap sottili, curvatura) che useranno per calcolare
dimensioni di cella sensate — non devi indovinarle. Se la geometria non è
watertight, te lo dice prima di provare a meshare (evita run falliti dopo
minuti di attesa).

### 2. Mesh tab — Quick Mesh vs percorsi manuali
- **Quick Mesh (`Ctrl+M`)**: un click, parametri auto-suggeriti dalla
  geometria caricata. È il punto di partenza per chiunque, anche senza sapere
  cosa sia un `meshDict`.
- **CFD Poly (GMSH, no-WSL)**: tetraedri GMSH → dual poliedrico (topologia
  stile STAR-CCM+). Non richiede WSL/OpenFOAM.
- **FEM Tetra (GMSH, no-WSL)**: mesh tetraedrica pura, per solver FEM.
- **Cartesian (cfMesh)**: riempimento cartesiano via cfMesh in WSL2 — la via
  "classica" OpenFOAM, richiede OpenFOAM installato (vedi [INSTALL.md](INSTALL.md)).

Il **Detail slider** (Molto Fine → Molto Grossolana) è un budget di celle
(10K–20M), non una garanzia: la dimensione finale dipende anche dalla
geometria reale.

### 3. Advanced tab (quando Quick Mesh non basta)
- Raffinamento locale (box/sfera/cilindro)
- Boundary layer: seleziona le patch, numero layer, growth rate
- Sizing per faccia
- Regioni multiple (CHT, FSI, multi-materiale)

### 4. Quality tab — checkMesh + auto-fix
Dopo la generazione, l'app lancia `checkMesh` e mostra skewness,
non-ortogonalità, aspect ratio con una heatmap 3D delle celle problematiche.
Se la qualità non passa le soglie, l'auto-fix prova fino a 3 iterazioni
(smoothing + refine mirato) prima di chiederti di intervenire a mano. Il
verdetto finale può essere esportato come **report PDF** (metriche +
istogrammi + screenshot) — utile per documentare la mesh in un progetto.

> Nota: un mesh "quality passed" e un mesh "generato con successo" sono due
> cose diverse. Se l'algoritmo richiesto fallisce le soglie di qualità,
> l'engine può passare automaticamente a una topologia diversa (es. da hex a
> tetraedrico) per garantire comunque un risultato: questo viene segnalato,
> non nascosto — vedi [residual_risks.md](residual_risks.md).

### 5. Export
- **OpenFOAM case**: pronto per `simpleFoam`/`pimpleFoam`/etc.
- **CGNS / VTU**: per ParaView o altri solver.
- **Tools → Launch ParaView**: se installato, apre direttamente il risultato.

## New Case Wizard (alternativa guidata)

`File → New Case` apre un wizard a 3 passi (Geometria → Mesh → Qualità) che
guida passo-passo chi preferisce non usare le tab direttamente — stessa
pipeline, presentata in sequenza.

## Scorciatoie utili

| Tasto | Azione |
|---|---|
| `Ctrl+O` | Carica geometria |
| `Ctrl+M` | Quick Mesh |
| `Ctrl+R` | Genera mesh / annulla |
| `Ctrl+Z` / `Ctrl+Y` | Annulla / ripeti (parametri) |
| `Ctrl+N` | Reset |

## Prossimo passo

Non sai da dove iniziare? Segui la sezione "Provalo in 2 minuti" in
[INSTALL.md](INSTALL.md).
