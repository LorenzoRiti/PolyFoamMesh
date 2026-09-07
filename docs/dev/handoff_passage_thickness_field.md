# Campo di sizing dal passaggio locale (local passage width) — algoritmo nuovo

Contesto: dopo i fix del giro precedente (toggle reale, tetti su DistMax/
DistMin, viewer che mostra solo il bordo), l'utente ha chiesto: "non
esistono algoritmi seri di meshing automatico open source? ma seri" e poi
"sviluppa un algoritmo di meshing sizing automatico, che capisce dove
raffinare e raffina dove serve... con ricerche approfondite".

## Ricerca fatta prima di scrivere codice

1. **GMSH ha già un algoritmo ufficiale serio**: `Mesh.AutomaticMeshSizeField`
   (asse mediano + curvatura, mantenuto dal team GMSH). **Verificato con un
   test diretto**: il nostro build (wheel pip) fallisce con `"Gmsh has to be
   compiled with HXT and P4EST"`. **Verificato anche che conda-forge non lo
   builda** (letta la recipe `meta.yaml` del feedstock): non è disponibile
   senza compilare GMSH da sorgente con P4EST+MPI — costo alto, non un
   semplice cambio di pacchetto.
2. **Fondamento matematico confermato**: la quantità che ogni mesher serio
   usa per "capire dove serve rifinire" è la **local feature size** (LFS) —
   distanza dall'asse mediano del solido — approssimata classicamente coi
   **poli di Voronoi** (Amenta & Bern, alla base di CGAL e di GMSH stesso).
3. **Tentativo Voronoi-poles, fallito numericamente**: prototipo su un
   campionamento sparso/strutturato (due piani paralleli) — risultati
   sbagliati di un ordine di grandezza. È un problema noto e documentato in
   letteratura (il metodo richiede campionamento denso e non degenere,
   gestione robusta di celle di Voronoi illimitate/degeneri). Scartato per
   rischio di fragilità troppo alto rispetto al beneficio.
4. **Trovata l'alternativa giusta già nel repo**: `core/geometry.py` ha
   già un ray-caster vettorizzato e testato (`_sample_thickness_one_mesh`,
   usato da `analyze_local_thickness`) — ma **collassa tutto a statistiche
   globali** (p1/p5/p10...) per il pulsante "Auto-Suggest Cell Sizes"
   (oggi nascosto), buttando via esattamente l'informazione spaziale
   ("DOVE è stretto") che serve per rifinire solo dove serve.
5. **Validato con tre esperimenti prima di toccare codice di produzione**:
   - due piani paralleli con gap noto = 0.1 → range dei valori confuso
     inizialmente per un errore di orientamento delle normali (il ray-cast
     misura la larghezza del volume che la mesh RACCHIUDE, cioè il dominio
     fluido in un caso CFD — non lo spessore della parete solida);
   - un singolo box sottile (rappresenta un canale stretto) → **0.1000
     esatto**; un cubo grande (camera aperta) → **2.0000 esatto**;
   - **il test decisivo**: un venturi vero, un'UNICA mesh connessa
     largo→stretto→largo (corpo di rivoluzione) → **1.99 ai due lati larghi
     (atteso 2.0), 0.34 alla gola (atteso ~0.30)** — campo genuinamente
     locale, non una statistica per corpo.
   - **confermato anche su GMSH vero e su un venturi.stl reale**
     (`C:/cfmesh_poly_bench/venturi.stl`): il campo scende monotonicamente
     lungo l'asse (0.121 → 0.075 su 10 bucket), minimo assoluto 0.00075.

## Cosa ho implementato

### `core/geometry.py`
- **`sample_thickness_field(mesh, n_samples, bbox_max, seed) -> (points,
  thickness)`** — nuova funzione, variante di `_sample_thickness_one_mesh`
  che restituisce anche i PUNTI (non solo i valori), necessaria per un
  campo spaziale. Duplica ~30 righe della logica di ray-casting invece di
  refactorizzare la funzione esistente: `_sample_thickness_one_mesh` è già
  usata in produzione (Auto-Suggest), toccarla comportava un rischio di
  regressione non necessario per un guadagno di pulizia del codice.

### `core/gmsh_wrapper.py`
- **`_extract_boundary_trimesh(gmsh_mod)`** — estrae l'intera superficie
  discreta già tassellata da GMSH (tutte le entità 2D, non superficie per
  superficie) come un unico `trimesh.Trimesh`, usando i node tag di GMSH
  come chiave di vertice condivisa (niente merge per tolleranza di
  coordinate — la mesh di GMSH è già conforme alle giunzioni). Verificato:
  su un box 2×1×0.5, watertight, volume esatto 1.0.
- **`_sample_passage_thickness_field(gmsh_mod, h_min, h_max, cells_across,
  max_samples)`** — orchestratore: estrae il bordo, chiama
  `sample_thickness_field`, converte spessore → dimensione target
  (`clamp(thickness / cells_across, h_min, h_max)`). Degrada a "nessun
  campionamento" su qualunque eccezione (mai blocca la meshatura).
- **`_configure_curvature_size_field`**: esteso con parametri opzionali
  `passage_points`/`passage_target_sizes`, uniti all'insieme di punti PRIMA
  del rilassamento growth-rate — così la transizione tra la strettoia e la
  curvatura circostante è anch'essa gradita, non un salto netto.
- **`_configure_adaptive_sizing`**: il vecchio ciclo di campi Ball basato
  su `_detect_surface_gaps` (distanza bounding-box tra coppie di
  superfici — una proxy, non la distanza vera, cieca a strettoie formate
  da più di due superfici) è **sostituito** dalla chiamata al nuovo campo
  di spessore. `_detect_surface_gaps` stessa **non è stata rimossa**: serve
  ancora altrove per l'euristica di rischio polyDualMesh (conteggio gap,
  uso completamente diverso dal sizing) — bug trovato e corretto durante
  l'implementazione: avevo rimosso la variabile `gaps` insieme al ciclo
  Ball, rompendo quell'euristica; rimessa.

## Verificato (non solo unit test isolati)

- **GMSH vero, no WSL**, su `sample_cad/cylinder_test.stl`: mesh generata
  correttamente (5.317 tet), nessuna eccezione, `passage_samples=6000` nel
  log.
- **GMSH vero su un venturi reale** (`C:/cfmesh_poly_bench/venturi.stl`):
  51.614 tet, nessun crash, campo di spessore verificato scendere
  monotonicamente verso la gola (vedi sopra).
- **6/6 test nuovi verdi** (`tests/test_passage_thickness_field.py`):
  4 puramente trimesh/numpy (portabili, no GMSH), 2 con GMSH vero (box
  semplice, veloce, no WSL).
- **23/23 test preesistenti verdi** (`test_geometry.py`,
  `test_cell_sizing.py`, `test_sizing_resolves_features.py`) — `
  analyze_local_thickness` e il resto della pipeline di sizing esistente
  non sono stati toccati/rotti.
- **27/27 test della sessione precedente ancora verdi** (adaptive sizing,
  slider, viewer) — nessuna regressione incrociata.

## Nota a margine osservata, non causata da questo lavoro

Durante i run reali compare due volte `Error: Unknown OpenCASCADE entity
of dimension 1 with tag N` — stampato da GMSH durante "Meshing 1D" su
input STL (percorso `classifySurfaces`/`createGeometry`, geometria
discreta senza vero kernel OCC). **Non blocca la meshatura** (confermato:
mesh generata correttamente in tutti i run) e non sembra originare da
`_extract_boundary_trimesh` (che non chiama nessuna API OCC-specific,
solo `getNodes`/`getElements`) — plausibilmente pre-esistente al lavoro di
oggi, legato al percorso di ricostruzione STL→geometria discreta già
documentato altrove nel codice (`_scan_feature_sizes`'s fallback per
"STL input goes through classifySurfaces()..."). Non approfondito oltre
per limiti di tempo — se compare anche su un checkout pulito prima di
questa sessione, è confermato non causato da questo lavoro.

## NON fatto / da verificare alla ripresa

1. **Non ho confrontato con checkMesh reale (WSL)** il "prima vs dopo"
   sulla stessa geometria (es. venturi) per misurare l'effetto sul
   conteggio celle/qualità — solo verificato che la generazione non si
   rompe e che il campo è geometricamente corretto. Prossimo passo naturale
   (richiede WSL, quindi da coordinare per non contendere risorse).
2. **BUG TROVATO E CORRETTO durante la stesura di questo stesso
   checkpoint**: il codice usava `df.get("min_thick_cells", 8)`, ma
   `_GMSH_DETAIL` (il dizionario dei livelli in gmsh_wrapper.py) **non ha
   affatto quella chiave** — esiste solo in `core/geometry.py`'s
   `_DETAIL_PRESETS` (usato per l'Auto-Suggest, dizionario diverso). Il
   default `8` veniva quindi usato SEMPRE, silenziosamente, per ogni
   livello di dettaglio — lo slider "Mesh Fineness" non aveva alcun
   effetto sul numero di celle attraverso la strettoia. Corretto:
   `df.get("cells_across", 8)`, che riusa la chiave che ESISTE davvero in
   `_GMSH_DETAIL` ed è già tarata per livello (8/13/20/32/48). Verificato
   di nuovo dopo il fix: 12/12 test verdi.
3. **Performance su parti molto grandi**: `max_samples=6000` di default
   è lo stesso ordine di grandezza già usato per il campo di curvatura
   (20.000) ma più basso — non misurato il tempo di ray-casting su una
   geometria con centinaia di migliaia di triangoli di bordo (il venturi
   di test ne aveva poche migliaia). trimesh senza `pyembree` installato
   può essere lento su mesh molto dense — da profilare su un caso reale
   grande prima di dichiarare la performance a posto.
4. **Non wired nella GUI come opzione visibile** — è un miglioramento
   dell'algoritmo esistente dietro il toggle "Rifinitura automatica" già
   presente, non richiede nuova UI, ma non è stato comunicato/documentato
   all'utente finale nell'app stessa (tooltip, changelog).

## File toccati

- `src/cfmesh_autogui/core/geometry.py` (+funzione nuova, nessuna modifica
  a codice esistente)
- `src/cfmesh_autogui/core/gmsh_wrapper.py` (2 funzioni nuove, 1 estesa
  con parametri opzionali, 1 sostituzione di logica in
  `_configure_adaptive_sizing`)
- `tests/test_passage_thickness_field.py` (nuovo, 6 test)
