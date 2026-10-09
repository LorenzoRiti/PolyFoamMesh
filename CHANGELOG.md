# Changelog

Tutte le modifiche notevoli a PolyFoamMesh sono documentate qui.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e
il progetto segue [Semantic Versioning](https://semver.org/lang/it/).

## [2.3.0] - 2026-10-09

### Aggiunto

- **Manuale utente completo in inglese** (`docs/USER_GUIDE.md`), riscritto
  per la UI corrente: workflow, viewer, boundary layer (con lettura del
  log), refinement, temi e troubleshooting.
- **Viewer**: Section Cut deterministico che taglia in tutte le
  rappresentazioni; Measure funzionante (distanza a due click su CAD e
  mesh); rappresentazioni unificate stile ParaView (`Surface`, `Surface
  with Edges`, `Wireframe`); highlight patch solido; aspetto mesh più
  curato (smooth shading, edge adattivi).
- **Workflow dock**: la voce statica "Next step" è diventata **Next
  action**, un suggerimento azionabile che segue lo stato reale (idle, in
  corso, finito, errore).
- **Suddivisione patch stile BaramMesh**: popup all'import (STEP e STL)
  con "Feature angle" (5–179°) e checkbox "Use CAD face names when
  available"; con la checkbox off la superficie viene suddivisa per
  angolo in patch `surface_1..N`. I **nomi delle patch sono editabili**
  (doppio click) nella tabella patch, ora spostata nel dock sinistro in
  basso (unico pannello: la lista duplicata nel tab Geometry è stata
  rimossa). Il rename si propaga a meshing, BL e export.
- **Supporto Linux (fase 1 - core)**: l'app ora esegue i comandi
  OpenFOAM/cfMesh nativamente su Linux (`bash -lc` invece di `wsl.exe`),
  con bashrc configurabile via variabile `POLYFOAMMESH_OF_BASHRC`
  (default `/usr/lib/openfoam/openfoam2512/etc/bashrc`). Dati applicativi
  in posizione XDG (`~/.local/share/polyfoammesh`, override
  `XDG_DATA_HOME`) con migrazione legacy intatta. Launcher `launch.sh`,
  spec PyInstaller Linux (`PolyFoamMesh-linux.spec`) e job CI
  `build-linux`/`attach-linux` (il job Windows resta invariato). Sezione
  "Linux (from source)" in INSTALL.md/.it.md. Comportamento Windows
  verificato **byte-identico** (18/18 controlli su comandi e script
  generati).
- **Cache della mesh tet (salta GMSH) — AUTOMATICA**: quando gli input
  che determinano la mesh non cambiano, il `.msh` di un run precedente
  viene riusato (fingerprint SHA256, copia nella nuova case dir, stessa
  continuazione della pipeline) saltando l'intero stadio GMSH — il più
  lento. Nessuna checkbox: è sempre attiva. "Clear mesh cache" nel menu
  Tools, prune automatico (5 voci / 2 GB), tutto fail-soft. **Nota di
  correttezza**: lo stadio GMSH "cuoce" il layout degli strati vicino
  alla parete nel `.msh` (campo BoundaryLayer da nLayers/spessore/
  espansione) e applica il floor celle-parete come campo di size: questi
  input fanno parte del fingerprint, quindi cambiare i parametri BL
  invalida la cache (giusto); le altre impostazioni BL (apply-to-all,
  filtri, budget) restano escluse.
- **Visibilità del boundary layer nel viewer**: checkbox "Show BL" che
  classifica le celle prisma (seed dalle facce wall + test di forma +
  flood multi-strato) e colora i BL in arancione sul core grigio.
- **Log durante la mesh**: il pannello log creava UN blocco HTML unico e
  illimitato → re-layout quadratico (10k righe = 29 s, 50k > 300 s) e
  lag della GUI "senza motivo". Ora batching su timer, blocchi reali
  (il tetto righe funziona davvero), coalescing delle righe ripetute
  ("Improving..." collassate con contatore, "still running" sostituita):
  50k righe in ~0.24 s. Nessun mirror di log esisteva, nessuna fedeltà
  persa.
- **Supporto Linux (fase 2 - GUI)**: il gate di disponibilità ora è
  "OpenFOAM disponibile (nativo o WSL)" (non più solo WSL), il probe RAM
  usa la RAM dell'host anche su Linux, il case root non dipende più da
  `C:\`, il kill processi è nativo su POSIX, e tutti i messaggi/dialoghi
  WSL-only (OOM, memoria GMSH, ParaView, "WSL not found") sono
  condizionali per piattaforma — su Windows **byte-identici**.

### Corretto

- **BOUNDARY LAYER SALTATO SU OGNI RUN DELL'APP (critico)**: la guardia
  anti-doppio-BL contava le celle dual con forma da prisma e ne trovava
  ~2.000 **coincidenze** su un dual senza BL (misurato: 2012/24.286 =
  8% su un cono reale) → il worker credeva che il BL esistesse già
  ("GMSH-baked BL detected") e **saltava lo stadio BL su ogni geometria**.
  Ora la guardia richiede una copertura reale: ≥ 50% delle facce wall
  con owner a forma di prisma (misurato: 0.14 senza BL vs 0.89 con BL).
  **Verificato end-to-end sul cono reale dell'utente** (percorso worker):
  5 layer su 100% del muro, 69.615 prismi, checkMesh **Mesh OK**
  (NO 53.5, skew 1.54, pyramids 0).
- **Deselezione della faccia/patch nel viewer**: click sullo sfondo
  vuoto ora **deseleziona davvero** (il vecchio codice usava
  `pick_mouse_position`, che su un click a vuoto restituisce l'attore
  *stale* del picker VTK: "niente colpito" non si verificava mai) e
  ricliccare la patch evidenziata la **deseleziona** (toggle). Rimosso
  anche il fallback su `picked_actor`, che ri-selezionava la patch
  precedente a ogni click a vuoto.
- **Doppio pulsante Cancel durante un run**: `set_meshing_state(True)`
  rietichettava ancora il pulsante "Generate Mesh" come "Cancel" rosso
  (retaggio di quando il Cancel dedicato non esisteva), quindi durante la
  mesh se ne vedevano **due**. Ora il pulsante run resta "Generate Mesh"
  (disabilitato) e l'unico stop è il Cancel dedicato sotto "Reset All".
- **Qualità dual su geometrie complesse (opt-in, keep-best)**: nuovo
  `repair_concave_cells_keepbest` (merge delle celle concave con gate di
  **severità massima**: si accetta solo se NO/skew massimi non peggiorano)
  + `compact_unused_points` (bug latente corretto: il merge lasciava punti
  orfani → checkMesh "Unused points" hard-fail; corretto anche un
  `startFace` corrotto al round 2 che faceva SEGFAULTare checkMesh).
  Misurato con checkMesh reale: **valvola piramidi 340 → 46** (errori NO
  10 → 1), venturi 224 → 110; sul venturi la barra PASS non è raggiungibile
  con merge/smoothing — la causa è **topologica** (il dual a una cella per
  vertice di bordo non può essere convesso attraverso la piega concava
  della gola; provato: nemmeno con input 8.6× più fine cambia), quindi
  tutto opt-in, default byte-identico, corpus invariato (slot1 protetto
  dal gate di severità). La guardia anti-skew dello smoother resta opt-in
  (baratta NO per skew).
- **BL multi-layer "consuming" - ORA DEFAULT**: il motore BL può
  consumare strati di celle dual (rilocazione armonica limitata dei
  vertici interni + guardia anti-inversione per-edge + guardia sliver
  core), così lo stack completo entra anche dove la prima cella è
  sottile. **Verificato sul tubo reale**: da **1 a 6 layer** su 100% delle
  facce, primo strato = richiesto (y+ vero), checkMesh PASS (NO 56.75 vs
  60.50). Per forzare il vecchio comportamento:
  `POLYFOAMMESH_BL_CONSUME=0`. **Nota**: l'aspect ratio delle celle core
  adiacenti allo stack cresce (checkMesh accetta, 103 sul tubo).
- **BL multi-layer anche sulle geometrie complesse (venturi)**: sui dual
  con facce sliver il consumo non si spegne più globalmente — le facce
  **sliver-affected** (bordo sliver, vertice in cella sliver, +2 anelli
  di adiacenza) vengono **escluse dall'estrusione** (0 layer lì, celle
  pinned nel reflow: niente prismi torti) e lo stack completo gira sul
  muro sano. I **cappucci inlet/outlet** sono esclusi geometricamente
  (normale allineata all'asse di flusso + piano al bbox estremo + patch
  planare ≥4 facce, + anello rim): non sono pareti. **Verificato sul
  venturi reale** (checkMesh reale, dual dal backup): 43.782 prismi,
  mediana **6 layer** sul 60.7% del muro (tutto il sano), primo strato
  = richiesto, e checkMesh **migliore del baseline dual-only**: pyramids
  **199 < 224**, aspect max **209 OK** (erano 3182), NO/skew/neg
  identici. Tubo e corpus bit-identici.
- **Dual: input misto tet+prismi con stitching esatto**: il convertitore
  ora accetta mesh di input con prismi (da qualunque sorgente), li
  preserva verbatim nel polyMesh finale e cuce l'interfaccia al core
  dualizzato (quad mediani per triangolo d'interfaccia, split dei lati,
  niente T-junction). Verificato con checkMesh reale su mesh sintetiche
  (Mesh OK). Il worker salta il BL interno quando i prismi sono già
  presenti (guardia doppio-BL).
- **Verdetto sul BL "cotto" in GMSH**: impossibile con GMSH 4.x — il
  campo BoundaryLayer supporta solo strati 2D (il 3D è stato rimosso da
  GMSH 2.15.0) e aborta sulle curve adiacenti a due superfici; l'unica
  via 3D (extrudeBoundaryLayer) richiede geometrie del kernel interno,
  non STEP/STL importati. Verificato empiricamente su venturi e
  multi_inlet: zero prismi in ogni configurazione. Il tentativo resta
  dietro `POLYFOAMMESH_GMSH_BL=1` (default OFF, fallback loggato).
- **Hang GMSH (Parte2/venturi) RISOLTO**: causa radice misurata — la
  MeshAdapt 2D (Algorithm=1) va in loop infinito quando le è installato
  un size field non uniforme su superfici cilindriche/raccordi; Delaunay
  (5) completa lo stesso campo in ~2 s. Ora c'è una **modalità robusta**
  (`POLYFOAMMESH_GMSH_ROBUST=1`): healing OCC + Delaunay + niente
  ottimizzazione, e il **watchdog** la attiva da solo: se GMSH non
  produce output per 5 minuti → kill + retry robusto una volta → se si
  blocca ancora, errore chiaro. Parte2 completa in **149 s** (702k nodi,
  .msh valido); percorso default bit-identico sui casi che funzionano.
- **Prismi degeneri rifiutati**: un build BL con primo strato < 5% del
  richiesto (venturi: 80 nm!) produce pancake sub-micron e non-ortogonalità
  133° — ora viene rifiutato (meglio niente BL che un BL spazzatura):
  venturi default NO 133.6 → 43.3.
- **Campo sharp-edge opt-in** (`POLYFOAMMESH_GMSH_SHARP_EDGES=1`): il run
  di regressione del corpus non ha mostrato regressioni, ma il campo
  resta opt-in per principio keep-best finché un corpus non prova che
  aiuta più di quanto costi.
- **Calibrazione y+: convenzione `wallShearStress`**: i solver
  incomprimibili OpenFOAM scrivono il campo come τw/ρ (cinematico), ma il
  codice divideva per ρ → u_tau sbagliato di √ρ (×31 per l'acqua). Ora
  default cinematico (`u_tau = √|τw|`), convenzione dinamica disponibile
  via `kinematic_shear=False`, con nota nel risultato e nel dialog.
- **Deselezione patch nel viewer**: un click sullo **spazio vuoto** del 3D
  ora rimuove l'evidenziazione e la selezione della riga (prima restavano
  per sempre, rendendo inutile la selezione).
- **y+ / primo strato del boundary layer (errore in catena, caso reale)**:
  il calcolo fisico era corretto (flat-plate, k-epsilon y+ 30) ma il
  pannello salvava il primo strato solo come **frazione di `max cell`**;
  quando `max cell` cambiava (slider/adaptive, 0.171→0.0122) la stessa
  frazione produceva un primo strato **14× più piccolo** (85.8 µm → 6.1
  µm nel log) e nella simulazione y+ reale ~0.09 invece di 30. Ora il
  valore calcolato è memorizzato in **metri (assoluto)** ed è quello che
  va al motore; la frazione resta solo per visualizzazione. In più: il
  calcolo si **ricalcola automaticamente** quando cambiano Velocity /
  Fluido / Wall treatment (prima restava quello vecchio, es. calcolato a
  10 m/s e usato a 1 m/s), un edit manuale della frazione torna in
  modalità manuale, e il messaggio dichiara che la stima flat-plate su
  flussi curvi/interni può differire ~2-4×.
- **Case root con spazio nel percorso utente** (regressione della fase 2
  Linux): il fallback "senza spazi" riscriveva il path sostituendo gli
  spazi con underscore, creando un albero inesistente
  (`C:\Users\Davide_Valoroso\...`). Tutti i comandi WSL puntavano lì:
  checkMesh leggeva una directory vuota (`cells=8`), il pannello qualità
  mostrava zeri e la conversione dual riportava `cells ... -> 0`. Ora il
  root è senza spazi **per costruzione**: `<disco>:\cfmesh_cases` su
  Windows (dove stanno i casi reali), `tempdir/cfmesh_cases` su POSIX.
- **Guard di manifoldità del boundary BL** (caso reale "paletta"): le
  strisce terminator del BL ai bordi BL/non-BL finivano nel blocco
  boundary (misurato: +139.360 volti = esattamente i terminator, 32 edge
  non-manifold) → checkMesh FAIL con 230.126 volti "multiply connected".
  Ora `_validate` rifiuta un build con boundary non 2-manifold: sulla
  paletta la mesh scritta è quella dual-only manifold (multiply-connected
  0, aspect OK) invece del disastro. Il fix algoritmico del terminator
  (step interno invece che striscia di boundary) resta programmato.
- **Ruoli patch anche dopo il meshing**: la tabella patch su mesh
  generata mostra il tipo **inferito geometricamente** (inlet/outlet/wall)
  invece di "other", e il **rename post-meshing** scrive nel `boundary`
  (backup + validazione). La selezione BL usa gli stessi ruoli inferiti e
  il floor parete di A ora considera solo le superfici wall.
- **Coerenza formula A**: `_BL_CELLS_ACROSS_STACK` 5→3 con la derivazione
  dal budget di inversione (0.34): il floor parete ora rispetta davvero
  il budget.
- **Kill dei processi sicuro su Linux**: i processi OpenFOAM/cfMesh
  vengono lanciati con `start_new_session` e terminati con `killpg` (con
  guardia esplicita per non colpire mai il processo della GUI); su Windows
  resta `taskkill /T` invariato. Prima su POSIX `killpg` poteva uccidere
  l'app stessa e due helper basati su `taskkill` erano no-op (processi
  orfani dopo Cancel).
- **Guard RAM su Linux**: il probe RAM usava `ctypes.windll` (inerte su
  POSIX con fallback 999 GB); ora su Linux si usa la RAM reale dell'host.
- **Refinement box ora fanno davvero ciò che promettono**: i campi zona
  GMSH usavano `VOut = -1`, che combinato in MIN faceva prendere `-1`
  ovunque fuori dalle box e mandava GMSH in un loop di retry infinito (la
  mesh non si infittiva mai, o non finiva). Ora `VOut` finito +
  **transizione graduata** (`Thickness`): niente più blocco rettangolare a
  gradino netto. In più clamp del rapporto di raffinamento (max 4×) e del
  budget di celle aggiunte (~250k) per evitare esplosioni di conteggio
  (caso reale: 1.26M tetraedri richiesti → ~305k).
- **BL: limiter del gradiente di altezza** (stile cfMesh
  `relThicknessTol`): un vertice con budget di inversione profondo
  accanto a uno sottile creava uno "spike" che piegava la superficie
  estrusa in celle non-positive e faceva rifiutare l'intero BL; ora lo
  stack è limitato al gradiente e le mesh sane restano identiche
  (verificato byte-identico).
- **Measure**: togliendo la spunta spariscono marker, etichette e
  riepilogo (prima restavano sullo schermo); il pulsante Clear resta.
- **Boundary layer COMPLETI (copertura 100% della parete)**: la semantica
  binaria scartava le facce sui vertici concavi o a budget esaurito,
  lasciando buchi nel guscio (misurato: 5.9% delle facce di parete senza
  BL su un caso reale, "i BL vengono estrusi solo in parte"). Ora il
  percorso standard scarta solo le facce con difetti d'input reali e
  tiene tutte le altre con stack compresso (sottile ma presente, con
  altezza minima anti-degenerazione). Se il primo strato risulta
  compresso, il log lo dichiara e suggerisce una mesh di superficie più
  grossa per rispettare lo y+ richiesto.
- **Boundary layer reali (non più "successi" finti)**: il layer poteva
  risultare valido ma invisibile (1 strato quando ne erano richiesti 14)
  perché il fixpoint di consistenza è un minimo su tutta la parete
  connessa: un solo vertice a budget ridotto appiattiva ogni faccia.
  Ora un build valido ma appiattito viene rifiutato e il layer viene
  costruito con il massimo numero di strati che la mesh regge a piena
  altezza (`capped N -> k` nel log), con primo strato effettivo, mediana
  e copertura riportati nel log. Sul caso reale Part2: 1 strato a y+
  target esatto (h1 pieno) o 2 strati con h1 dimezzato, checkMesh PASS.
- **Unità `drop_bnd`** (id faccia locali confrontati con posizioni nella
  selezione filtrata): con il filtro sliver attivo ("BL robusto")
  venivano flaggate facce arbitrarie; il golden del valve passa da
  75.360 a 76.052 prismi (scala ed esclusioni invariate).
- **Volume delle celle** misurato con triangolazione esatta dal centroide
  (prima Newell/centroide, approssimato su facce non planari); la
  tolleranza di conservazione (5e-3) documenta la shell ceduta
  by-design sotto le facce escluse.
- **Stack compressi silenziosamente**: il cap ora scatta anche quando
  regge un solo strato a piena altezza (prima 14 strati venivano
  compressi in ~1 mm con y+ sbagliato di 20x senza avviso).

### Modificato

- **Layer visibili automaticamente (checkbox "Show BL" rimossa)**: non
  serve più spuntare nulla — appena una mesh viene visualizzata, le celle
  del boundary layer sono classificate in background e colorate
  **arancioni** sul core grigio (una fascia da 0.5-1 mm è invisibile nel
  colore-per-patch: mostrarla non è un'opzione, è informazione).
- **Robust boundary layer sempre attivo**: la casella "Robust boundary
  layer (recommended, experimental)" è stata rimossa — il bundle
  misurato (filtro facce sliver 0.9 + terminazione a budget) ora si
  attiva **da solo** quando si spunta "Enable Boundary Layers". Niente
  più knob da sapere: spunti il BL e lui entra con le impostazioni
  giuste.
- **Dock sinistro ordinato**: la tabella patch ora **riempie** lo spazio
  sotto il workflow (era cappata a 140px con una banda grigia vuota
  sopra) e il titolo "Patches" ha lo stesso stile di "Next action": la
  colonna sinistra è una lista ordinata (Workflow → Next action →
  Patches).
- **Chiusura concava automatica**: il worker ritenta da solo
  `decoupled_vertex` (H4) quando il percorso standard fallisce, poi
  dimezza il primo strato. Si attiva dove serve (giunzioni concave:
  groove1/slot1, valvola production) senza degradare i casi già gestiti
  dallo standard. La guardia memoria (8M facce / 1.5M celle) protegge il
  retry della chiusura.

### Rimosso

- Checkbox "Chiusura avanzata per geometrie concave (sperimentale)" dal
  pannello Boundary Layers, `get_bl_concave_closure()` e la chiave
  `concaveClosure` dal payload GUI. Il flag legacy resta solo per il
  merge repair post-BL (off di default, misurato in regressione su
  groove1).
- Pulsante **Full Auto** dalla ribbon. Il **Cancel** non sta più nella
  ribbon: è stato spostato nel tab Mesh, **sotto "Reset All"**, dove
  l'utente guarda durante un run (appare solo mentre la mesh è in corso).
- Combo **Decimation** dal viewer (la policy Auto resta fissa
  internamente).
- Tab **Quality** vuota dal pannello sinistro (mostrava solo "No quality
  data yet"; il checkMesh resta nel log, nel quality panel e nel report
  PDF).
- Pulsante **"Auto-Fix Quality"** dal quality panel (e il relativo
  segnale `fix_requested`): il gate di qualità ritenta già in automatico,
  il pulsante era ridondante.

### Modificato

- **Niente più pulsanti per cose che l'app deve fare da sola** (richiesta
  utente): rimossi "Calibrate from simulation" (il modulo di calibrazione
  resta interno), "Fit BL to mesh" (la compressione dello stack la
  gestisce il motore e il log la dichiara), il combo "Surface resolution"
  (ora **derivato automaticamente dallo slider Detail**: very
  coarse/coarse → Coarse, medium → Medium, fine/very fine → Fine/Very
  fine) e la checkbox della cache mesh (sempre attiva). Il calcolo y+ è
  **automatico**: parte all'attivazione del BL e si aggiorna quando
  cambiano Velocity/Fluido/Wall treatment; un edit manuale dei valori
  viene rispettato (nessuna sovrascrittura).
- **Lista mesher**: **Polymesh è il percorso di default** (prima
  Cartesian cfMesh, che resta come alternativa); il pulsante
  "FEM Tetra (GMSH)" è stato **rimosso** (nicchia che duplicava lo stadio
  GMSH — l'algoritmo tetraedrico interno resta come fallback automatico
  per geometrie complesse).
- **Viewer durante i run**: il render periodico (30 FPS) viene messo in
  **pausa** mentre la mesh è in corso e ripreso alla fine, per non
  competere con i worker (helper unico `_set_run_state`).
- **Feature angle**: range esteso a 5–179° (era 5–80°, non permetteva
  valori come 89°); l'ultimo angolo usato viene ricordato.
- **Interfaccia interamente in inglese**: pannelli, dialoghi, tooltip e
  messaggi di log (erano rimaste stringhe italiane in params_panel,
  main_window, viewer e nei template del motore).
- **Tema chiaro/scuro**: causa radice delle "isole" di colore misto
  trovata nel QSS applicativo (background opaco ereditato dalle QLabel);
  fix runtime + dialoghi About/Template palette-driven e colori di
  testo/contrasto theme-aware (contrasto verificato, minimo 6.0).
- **Flusso refinement box**: il picking nel viewer ora crea una box
  centrata sul punto (prima creava una sfera legacy e non mostrava mai
  la box); lista zone con etichette leggibili e livello sincronizzato
  con la selezione.
- **Workflow**: le spunte seguono il processo reale (geometry -> sizing
  -> generate -> quality) anche per i percorsi GMSH/autopoly/native,
  non solo cfMesh.

## [2.2.0] - 2026-09-08

### Cambi di rottura (breaking)

- **Percorso dati rinominato**: i dati applicativi sono passati da
  `%APPDATA%\cfmesh-autogui\` a `%APPDATA%\polyfoammesh\` (log, sessioni,
  telemetria octopoda). Alla prima esecuzione la nuova cartella viene
  popolata automaticamente copiando il contenuto della vecchia, se
  presente. Chi ha installato una versione precedente non perde nulla, ma
  il vecchio percorso non viene più scritto. Le impostazioni applicative
  (tema, parametri recenti) vivono nel registro di Windows e non cambiano
  posizione: restano dove erano, così le preferenze dell'utente sopravvivono
  al rename del pacchetto (`cfmesh_autogui` → `polyfoammesh`).

### Aggiunto

- **Pipeline di release**: su push di un tag `v*`, GitHub Actions builda
  l'eseguibile PyInstaller + l'installer Inno Setup su Windows e li allega
  alla GitHub Release del tag (con changelog estratto da questo file).
  Rimossi gli script installer obsoleti (NSIS e Inno duplicati); corretto
  `UninstallDisplayName` in `inno_setup.iss` (direttiva spezzata su due
  righe).
- **Avviso di sostituzione algoritmo nella GUI**: quando il motore sostituisce
  silenziosamente un percorso di meshing (boundary layer disattivato,
  parallelo→seriale, retry GMSH più grossolano, retry di qualità), ora il
  log panel mostra una riga `[SUBSTITUTED]` evidenziata — mai più silenzio.
- **Documentazione bilingue**: `docs/INSTALL.md` e `docs/USER_GUIDE.md`
  riscritte in inglese (principali), versioni italiane in
  `docs/INSTALL.it.md` / `docs/USER_GUIDE.it.md`, link incrociati in cima.
- **Community**: `CODE_OF_CONDUCT.md`, template PR in
  `.github/PULL_REQUEST_TEMPLATE.md`, 5 issue "good first issue" sul repo
  pubblico.
- `_paths.py`: fonte unica per la directory dati applicativi e migrazione
  one-shot dal percorso legacy.

## [2.1.0] - 2026-09-08

### Cambi di rottura (breaking)

- **Rename del pacchetto Python**: `cfmesh_autogui` → `polyfoammesh`. Gli
  import nel codice, nei test e nei tool sono stati aggiornati; i moduli
  entry point della CLI sono ora `polyfoammesh`, `polyfoammesh-mesh` e
  `polyfoammesh-batch`. L'eseguibile PyInstaller si chiama `PolyFoamMesh`.

### Aggiunto

- CI riportata verde su GitHub Actions (era rossa su ogni push): `pytest-qt`
  negli extra, percorsi compatibili POSIX in `config.py`/`validation.py`,
  dipendenze `networkx` e `rtree` dichiarate.

### Corretto

- Zone di raffinamento manuali: `setAsBackgroundMesh` scartava il campo
  adattivo geometrico; ora MIN-combinato (`gmsh_wrapper.py`).
- Corpi curvi chiusi (es. sfera) tessellati non-watertight: vertici
  coincidenti a cuciture/poli ora fusi e triangoli degeneri scartati
  (`geometry.py::tessellate_patches`).
- Test che sovrascrivevano `sample_cad/` tracciato; stub sempre-skip
  sostituito con un test di integrazione reale.
- Storia git: blob da ~278 MB (`installer/output/*.exe`) rimosso dalla
  storia di `master` con `filter-branch`.

### Nota

- L'eseguibile e l'installer precompilati non vengono committati nel repo:
  si trovano rispettivamente in `dist/` e `installer/output/` (entrambi
  ignorati da `.gitignore`) e vengono generati localmente o dalla pipeline
  di release.

[2.2.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.2.0
[2.1.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.1.0
