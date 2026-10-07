# Changelog

Tutte le modifiche notevoli a PolyFoamMesh sono documentate qui.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e
il progetto segue [Semantic Versioning](https://semver.org/lang/it/).

## [Non rilasciato]

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

### Corretto

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
- Pulsante **Full Auto** (il pulsante Cancel resta, centrato nella
  ribbon).
- Combo **Decimation** dal viewer (la policy Auto resta fissa
  internamente).
- Tab **Quality** vuota dal pannello sinistro (mostrava solo "No quality
  data yet"; il checkMesh resta nel log, nel quality panel e nel report
  PDF).

### Modificato

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
