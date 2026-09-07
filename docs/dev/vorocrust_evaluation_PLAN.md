# Valutazione VoroCrust come possibile sostituto del mesher nativo

Documento di lavoro — stato aggiornato man mano che le fasi procedono.
Aggiornato al 2026-08-07.

## Perché

Il mesher nativo cut-cell (`core/native_mesher.py` → `core/hex_poly_dual.py`,
vedi `docs/poly_mesher_STATO.md`) ha due limiti strutturali che richiederebbero
riscritture rischiose per essere risolti: lo snapping su pareti parallele alla
griglia (il dual fallisce con "Non-manifold primal edge" su geometrie come un
box) e il refinement locale (nessun octree). Entrambi i problemi sono
strutturali all'approccio cartesiano/cut-cell, non bug da correggere.

[VoroCrust](https://vorocrust.sandia.gov/) (Sandia National Labs, Abdelkader
et al., *"VoroCrust: Voronoi Meshing Without Clipping"*, ACM Trans. Graph.
2020) usa un approccio diverso — piazzamento di seed di Voronoi guidato da un
campo di sizing con garanzie matematiche, **nessuna griglia cartesiana** — che
elimina strutturalmente entrambi i problemi. Codice open source:
[sandialabs/vorocrust-meshing](https://github.com/sandialabs/vorocrust-meshing),
licenza **BSD-3-Clause** (uso commerciale ok), C++/CMake/OpenMP.

**Limite noto dal paper** (dichiarato dagli stessi autori, sez. 4 "Future
Work"): al 2020 VoroCrust **non genera boundary layer** — stesso gap del
nostro percorso nativo oggi. Da riverificare sul codice attuale (2026).

## Fasi

Ogni fase è bloccata dalla precedente (vedi task tracker). Non iniziare una
fase finché la precedente non ha un esito registrato qui sotto.

### Fase 0 — Build in WSL + test OneBox
**Obiettivo**: verificare che si compili e che il loro stesso test case
`OneBox` (box con pareti piatte — il nostro identico caso di fallimento)
produca una mesh valida.
**Perché WSL e non Windows nativo**: le istruzioni di build Windows nel loro
repo sono segnalate come "in sviluppo"; WSL è già l'ambiente che questo
progetto usa in modo affidabile per OpenFOAM/cfMesh.
**Stato**: IN CORSO.

### Fase 1 — Verifica sul nostro caso reale
Esportare uno dei nostri STL (quello che rompe `hex_poly_dual`) in `.obj`,
lanciarlo con VoroCrust, confermare che risolve lo snapping su geometria
nostra (non solo sul loro test sintetico).
**Stato**: bloccata da Fase 0.

### Fase 2 — Convertitore output → OpenFOAM
Il formato di output (probabilmente Exodus) va convertito in
`constant/polyMesh`. `meshio` (già dipendenza del progetto) supporta Exodus —
riuso, non nuova dipendenza. Analogo a `core/mesh_converter.py` (GMSH .msh →
OpenFOAM) già esistente.
**Stato**: bloccata da Fase 1.

### Fase 3 — Integrazione come mesher sperimentale
Wrapper subprocess (stesso pattern di `core/gmsh_wrapper.py`), bridge stile
`commercial/native_poly_bridge.py`, voce nel menu Tools (stesso trattamento
"sperimentale, opt-in" dato al Native Poly di oggi — non nel combo mesher
standard).
**Stato**: bloccata da Fase 2.

### Fase 4 — Confronto su geometrie reali
checkMesh, tempi, robustezza su geometrie vere dell'utente vs percorso CFD
Poly GMSH attuale. Decisione finale: sostituisce il cut-cell nativo o resta
un percorso sperimentale parallelo.
**Stato**: bloccata da Fase 3.

## Log

- 2026-08-07: piano scritto, Fase 0 avviata.
