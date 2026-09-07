# Fix del mesher automatico — stato e continuazione

Contesto: l'utente ha segnalato che il mesher automatico (percorso
"Polymesh"/GMSH, quello che "Automatic" seleziona di default dopo il rebrand
di Reasonix in `839b020`) rifinisce "a caso" — anche in geometrie dove non
dovrebbe, e anche quando l'utente pensa di non aver selezionato nulla di
automatico. Ha chiesto: 1) un vero interruttore ON/OFF, 2) che ON funzioni
bene (rifinisce solo dove serve: curvature, spigoli, strettoie).

## Diagnosi (confermata da tre fonti indipendenti)

1. **Il codice**: `src/cfmesh_autogui/gui/params_panel.py`, il checkbox
   `_adaptive_sizing_check` era `setVisible(False)` + `setChecked(False)` +
   commento "Superseded by the Mesh Fineness slider" — **morto**. In
   `main_window.py:_start_gmsh_volume_worker` la condizione era
   `if adaptive or max_cells_target > 0:` e `max_cells_target` (derivato
   dallo slider Mesh Fineness) **non è mai zero** (`_slider_to_cells` mappa
   0..20 su 10K..20M) → il percorso adattivo partiva SEMPRE, il checkbox
   era irraggiungibile. Non esisteva alcun modo di ottenere sizing
   manuale/uniforme dalla GUI.
2. **La documentazione ufficiale GMSH**: "When mesh element sizes are fully
   specified by a mesh size field, it is often desirable to set
   `MeshSizeFromPoints = 0; MeshSizeFromCurvature = 0;
   MeshSizeExtendFromBoundary = 0` to prevent over-refinement." Il codice
   faceva l'esatto contrario: `_configure_adaptive_sizing` in
   `gmsh_wrapper.py` costruiva field espliciti e ben progettati (Distance/
   Threshold sulle curve piccole, Ball sulle strettoie, un campo di
   curvatura a-priori bounded via `setSizeCallback`) e POI lasciava ATTIVO
   anche il motore di curvatura nativo di GMSH (`MeshSizeFromCurvature=1`),
   che non ha idea di quali feature curve contino per la CFD — chiede N
   elementi per cerchio OVUNQUE, limitato solo dal
   `CharacteristicLengthMin` GLOBALE. Una feature genuinamente piccola in
   un punto del CAD (uno smusso di fabbricazione da 0.25mm su una valvola
   da 3m) abbassa quel minimo globale, e il motore nativo lo applica poi a
   OGNI superficie curva del pezzo, indipendentemente dalla rilevanza. Il
   callback del campo di curvatura fatto bene (`min(lc, target)`) non può
   proteggersene: `min()` prende sempre il valore più fine, quindi il
   motore nativo (senza limite) vince sempre sul nostro campo bounded.
3. **Pratica commerciale** (Pointwise, ANSYS Fluent Meshing, cfMesh):
   "curvature-based" + "proximity/gap-based" local sizing sono i due
   ingredienti standard — che il codice AVEVA GIÀ (rilevamento curve
   piccole in `_scan_feature_sizes`, rilevamento gap in
   `_detect_surface_gaps`), solo annegati dal motore nativo sopra.

Un secondo difetto, minore ma reale, trovato durante l'indagine:
`_scan_feature_sizes` classificava "piccola" la curva nel 25° percentile
di lunghezza **relativo alle altre curve del pezzo**, senza un pavimento
assoluto legato alla scala del pezzo. Su una geometria uniforme (un tubo
semplice, un condotto dritto — nessuna vera struttura "corpo grande /
smusso piccolo") il quartile più corto veniva comunque marcato "feature
piccola" e rifinito localmente, perché un quartile più corto esiste
sempre per costruzione statistica.

## Fix applicati (committati nel working tree, non ancora in git commit)

File toccati:
- `src/cfmesh_autogui/core/gmsh_wrapper.py`
  - `_configure_adaptive_sizing`: disattivati
    `Mesh.CharacteristicLengthFromCurvature`, `Mesh.MeshSizeFromCurvature`,
    `Mesh.MeshSizeFromPoints`, `Mesh.MeshSizeExtendFromBoundary` (tutti a
    0). Il sizing ora dipende SOLO dai field espliciti (Distance/
    Threshold/Ball + il campo di curvatura a-priori bounded). Il percorso
    manuale/esplicito (`if user_lc:`) NON è stato toccato — lì la
    curvatura nativa di GMSH è comportamento normale e atteso per un
    sizing min/max senza field, non è la fonte del difetto segnalato
    (era semplicemente irraggiungibile prima di questo fix).
  - `_scan_feature_sizes`: aggiunto un tetto assoluto
    `small_cutoff = min(small_cutoff, max_extent * 0.08)` — una curva è
    "piccola" solo se lo è anche in senso assoluto rispetto al pezzo, non
    solo relativo alle altre curve. Non altera il caso di riferimento
    (valvola 3m/smussi 0.25mm: il quartile lì è già ben sotto l'8%).
- `src/cfmesh_autogui/gui/params_panel.py`
  - Il checkbox è ora **visibile**, nella tab "Mesh" sotto lo slider Mesh
    Fineness (non più sepolto in Advanced), **checked di default** (ON =
    consigliato), con tooltip che spiega ON/OFF in italiano.
  - Rimosso il secondo checkbox morto in Advanced che sovrascriveva quello
    vero con uno nascosto (bug di duplicazione trovato durante il fix).
  - `_on_adaptive_sizing_toggled` ora rende `_max_cell`/`_min_cell`
    (scheda Advanced) **davvero editabili** quando OFF, non solo grigi
    cosmeticamente — prima erano sempre read-only e ignorati comunque.
- `src/cfmesh_autogui/gui/main_window.py`
  - `_start_gmsh_volume_worker`: la condizione ora è `if adaptive:` (letta
    dal vero stato del checkbox), non più `if adaptive or
    max_cells_target > 0:`. `max_cells_target` viene passato al worker
    SOLO quando adattivo è ON (nel percorso manuale non serve e non viene
    letto da `generate_volume_mesh`).

Test nuovi (tutti verdi, nessuna dipendenza WSL/GMSH reale):
- `tests/test_adaptive_sizing_curvature.py` — 5 test: il motore nativo di
  curvatura è disattivato nel percorso adattivo (con e senza il campo
  a-priori); i limiti min/max restano impostati; una geometria uniforme
  non marca curve come "piccole"; una feature genuinamente piccola resta
  marcata correttamente.
- `tests/test_slider_cell_sizing.py` — 2 test aggiunti: il checkbox è
  reale/visibile/ON di default; OFF rende Max/Min Cell Size editabili e
  ON li rimette read-only.

Verificato: `pytest tests/test_adaptive_sizing_curvature.py
tests/test_slider_cell_sizing.py -q` → 13/13 verdi.
`python -m py_compile` / `ast.parse` su tutti e tre i file GUI/core
toccati → nessun errore di sintassi.

## Secondo fix (dopo screenshot utente con Polymesh su una staffa piegata)

L'utente ha rilanciato l'app con i fix sopra e ha mostrato uno screenshot:
mesh Poly, `checkMesh` **PASS** con numeri oggettivamente buoni (skew 3.10,
non-ortho 53.8 — migliori della media documentata in questo progetto),
39.276 celle da 306.759 tet (rapporto ~7.8×, coerente col duale
baricentrico), conteggio quasi identico alla stima geometrica (~39K). Il
budget quindi NON stava esplodendo — ma visivamente la mesh sulla
superficie appariva uniformemente "grezza/granulosa" ovunque, senza un
gradiente visibile fine-vicino-alla-piega / grossa-nel-piano-piatto.

Causa trovata (secondo bug reale, indipendente dal primo):
`DistMax` del campo Threshold sulle curve piccole era
`max(small_cutoff * 20, min_size * 40)`, **senza alcun tetto assoluto**.
Con il tetto dell'8% di `max_extent` già applicato a `small_cutoff` (primo
fix), il caso peggiore dava `DistMax = max_extent * 1.6` — **più grande
dell'intero pezzo**. `DistMax` è il raggio entro cui il campo "risale" da
fine a `coarse_max`: se supera le dimensioni del pezzo, la rampa non
completa MAI, e tutto il pezzo resta dentro la zona "ancora fine" — da qui
l'assenza di gradiente visibile, anche con un conteggio celle totale
corretto.

Fix: `dist_max = min(max(small_cutoff*20, min_size*40), max_extent*0.15)`
— la zona di influenza resta un vicinato genuinamente locale (15% del
pezzo), non può più ingoiare l'intera geometria. Non tocca il caso di
riferimento (valvola 3m/smussi 0.25mm: lì il valore naturale è già ben
sotto 0.45m).

**Bug collaterale trovato applicando questo cap**: `DistMin` (calcolato
come `max(small_cutoff*2, min_size*4)`) può arrivare a `max_extent*0.16`
nello stesso caso limite — **più grande** del nuovo tetto di `DistMax`
(`max_extent*0.15`), il che invertirebbe i due parametri del campo
(DistMin > DistMax, non valido). Aggiunto `dist_min = min(dist_min,
dist_max * 0.5)` per garantire l'ordine corretto in ogni caso.

Test aggiunti in `tests/test_adaptive_sizing_curvature.py`
(`test_dist_max_never_exceeds_a_local_neighbourhood_of_the_part`):
verifica che `DistMax <= max_extent*0.15` e `DistMin < DistMax` nel caso
limite (small_cutoff al tetto). 6/6 verdi insieme agli altri test del
primo fix (totale 14/14 con `test_slider_cell_sizing.py`).

**Non ancora verificato**: se questo secondo fix risolve DAVVERO
l'aspetto visivo lamentato dall'utente — serve un nuovo run sulla stessa
geometria (la staffa piegata dello screenshot) per vedere se ora compare
un gradiente visibile (fine solo vicino alla piega, grosso nei pannelli
piatti). Non ho il file CAD per riprodurlo autonomamente. **Se il
problema persiste dopo questo fix**, il prossimo sospetto è la regola
"cells_across" (sizing del bulk dalla sezione trasversale, non dalle
feature locali) — chiedere il log completo (`Show Log` nell'app, riga
`Adaptive sizing: min=... max=... small_curves=.../... gaps=...
surfaces=...`) per avere i numeri reali invece di ipotizzare.

## Terzo fix — il viewer, non il mesher (probabile causa reale dello screenshot)

L'utente ha chiesto esplicitamente: "la mesh vorrei vederla come surface
with edges però senza dentro i tetra, solo i poly visibili". Controllando
`src/cfmesh_autogui/gui/viewer_widget.py` ho trovato che **sia** "Volume
Mesh" **sia** "Surface + Edges" caricavano lo STESSO `internal.vtu`
(l'intero volume) e lo disegnavano con `style="wireframe"` su TUTTE le
celle — non solo il bordo esterno. Guardando il mesh da fuori, si vedono
sovrapposti in proiezione schermo gli spigoli interni di TUTTE le celle
del volume (nel caso dello screenshot, 39.276 celle) — questo produce
esattamente l'effetto "rumore/grana" visto nello screenshot,
**indipendentemente da quanto sia ben graduata la mesh**. È molto
probabile che questo, non i due fix precedenti sul sizing, sia la causa
reale di quanto lamentato — una mesh perfettamente graduata (fine solo
vicino alla piega, grossa nei pannelli piatti) avrebbe comunque
quell'aspetto se vista con questa modalità di rendering.

Fix: nuovo metodo statico `ViewerWidget._prepare_internal_vtu_grid(grid,
mode)` in `viewer_widget.py` — quando `mode == "surface_edges"`, applica
`grid.extract_surface(algorithm="dataset_surface")` (il `vtkGeometryFilter`
di VTK) PRIMA del wireframe: tiene solo le facce di bordo esterne, e le
tiene come poligoni veri (una faccia pentagonale di una cella poly resta
un pentagono, non viene triangolata — verificato: un pentagono/quad non
diventa mai un triangolo). "Volume Mesh" resta invariato (esiste apposta
per ispezionare l'interno).

Verificato con una fixture analitica (2 esaedri unitari adiacenti,
condividono una faccia): 2 celle volumetriche → **10** facce di bordo
esatte (12 totali − 2 condivise, escluse correttamente), tutte quad, zero
triangoli. `algorithm="dataset_surface"` fissato esplicitamente contro un
FutureWarning di PyVista sul cambio di default.

Test: `tests/test_viewer_surface_only.py` — 3/3 verdi, nessuna dipendenza
da dataset di esempio bundled (fixture costruita a mano, deterministica).

**Non ancora verificato**: il rendering vero in GUI (serve un run reale
con `Show Mesh` → "Surface + Edges" per confermare visivamente). La logica
è isolata e testata, l'aspetto finale su schermo no.

## Quarto fix — il vero bug: il dispatch iniziale ignorava il menu a tendina

Dopo il terzo fix (`extract_surface` + `style="surface"` per "Surface +
Edges") l'utente ha rilanciato l'app e ha visto **ancora** i tet/celle
interne visibili. La causa non era nel rendering che avevo appena
corretto — era un secondo bug, indipendente, nel percorso che decide
COSA disegnare al primo caricamento dopo il meshing:

- `show_mesh()` imposta il menu a tendina su "Surface + Edges"
  (`_view_selector.setCurrentIndex(...)`, con `blockSignals(True)` per
  evitare un doppio trigger) e poi schedula `_delayed_display_mesh()`
  via `QTimer.singleShot`.
- `_delayed_display_mesh()` chiamava **sempre e comunque**
  `self._display_mesh()` — la modalità "Volume Mesh" (wireframe
  dell'intero volume interno) — **ignorando completamente** cosa il menu
  a tendina mostrava. Il commento nel codice spiegava solo perché non si
  usa `_on_view_changed` (per evitare un doppio avvio di foamToVTK), ma
  la chiamata diretta era rimasta agganciata a `_display_mesh()` invece
  che a `_display_surface_edges()` — probabilmente un residuo di una
  versione precedente in cui "Volume Mesh" era la vista di default
  (coerente con un vecchio commit nel log: "fix: switch viewer to Volume
  Mesh automatically after meshing", poi il default è cambiato a
  "Surface + Edges" senza aggiornare questo punto).

Risultato pratico: il menu mostrava "Surface + Edges" selezionato, ma la
mesh EFFETTIVAMENTE disegnata era sempre il wireframe completo del
volume — il fix precedente su `_display_surface_edges()` non aveva
alcun effetto perché quel metodo non veniva mai chiamato in questo
percorso.

Fix: `_delayed_display_mesh()` ora legge `_view_selector.currentIndex()`
e dispatcha esplicitamente su `_display_surface_edges()` /
`_display_cad()` / `_display_mesh()` di conseguenza, invece di chiamare
sempre `_display_mesh()`.

**Nota tecnica**: il file `viewer_widget.py` contiene mojibake
preesistente (em-dash UTF-8 doppiamente ri-codificati, `â€"` invece di
`—`) in diversi commenti — non causato da questa sessione, ma ha reso
il primo tentativo di modifica con lo strumento Edit silenzioso/fallito
per mancata corrispondenza di stringa. Risolto scrivendo ed eseguendo
uno script Python dedicato con newline `\r\n` espliciti (il file usa
CRLF) invece di affidarsi al matching di stringa esatto dello strumento
di editing. Non ho ripulito il mojibake preesistente altrove nel file
(fuori scope).

Test: `tests/test_viewer_delayed_display_dispatch.py` — 4/4 verdi.
Costruisce un `ViewerWidget` "nudo" con `ViewerWidget.__new__(...)` (non
`object.__new__`, che PySide6/Shiboken rifiuta per le sottoclassi
`QObject`) e verifica il dispatch corretto per ognuna delle 3 modalità
più il caso selettore disabilitato.

**Verificato con un run reale** (non mock, non WSL — GMSH vero +
rendering VTK vero offscreen):

1. `generate_volume_mesh` su `sample_cad/cylinder_test.stl` (GMSH reale):
   `Adaptive sizing: min=0.00010 max=0.00020 small_curves=0/2 gaps=0
   surfaces=3` → **0 curve marcate "piccole" su un cilindro semplice**,
   conferma diretta del tetto assoluto dell'8% su geometria vera, non solo
   sulla fixture sintetica del test. Mesh generata senza errori (11.335
   tet a "fine", 1140 a "medium").
2. `ViewerWidget._prepare_internal_vtu_grid` + `add_mesh(style="surface",
   show_edges=True)` su una mesh poliedrica vera (celle a 5 facce,
   triangoli+quad, non hex/tet) → screenshot offscreen: superficie piena
   ombreggiata, spigoli veri visibili, **nessuna diagonale spuria di
   triangolazione**. Confermato che 18 facce di bordo (su 6 celle
   poliedriche, 5 facce ciascuna = 30 totali − 12 condivise) restano
   poligoni veri (`{3, 4}` vertici, non tutte triangolate a 3).

**Ancora non verificato**: il click-through vero della GUI desktop (non ho
strumenti di controllo interattivo del desktop in questa sessione) — la
verifica sopra usa gli stessi identici componenti (stesso GMSH, stesso
metodo statico del viewer, stessa chiamata `add_mesh`) ma non un run
dell'app impacchettata dall'utente.

## NON fatto / verificare alla ripresa

1. **Suite completa non ancora conclusa**: `pytest tests/ -q -k "not
   bench"` è stato lanciato in background (~00:50 avvio) e non ha ancora
   prodotto output dopo diversi minuti pur consumando CPU attivamente
   (confermato con `Get-Process`) — probabile fase di collection lenta
   (import pesanti: gmsh, cadquery, pyvista su ~100+ file di test) più che
   un hang. **Alla ripresa: leggere l'output di quel comando prima di
   dichiarare qualunque cosa conclusa.** Se emergono fallimenti nei file
   GUI (`test_bl_gui_wiring.py`, `test_mesher_buttons.py`, eventuali test
   che leggono `_adaptive_sizing_check`/`_max_cells_target`/`_max_cell`/
   `_min_cell` per posizione o stato precedente), sono la priorità
   immediata da correggere prima di considerare il fix chiuso.
2. **Percorso cfMesh (WSL)**: controllato `meshdict_gen.py` —
   `build_object_refinements` è opt-in puro (nessuna zona = nessun
   refinement), non sembra avere l'equivalente del motore nativo sempre
   acceso. Non approfondito oltre per limiti di tempo; se il sintomo
   "raffina a caso" si ripresenta anche selezionando esplicitamente
   cfMesh (non Polymesh/Automatic), guardare lì.
3. **Verifica visiva reale**: non è stato lanciato un mesh vero (né via
   GUI né headless) su una geometria di test per CONFERMARE visivamente
   che il nuovo comportamento produca una mesh sensata (rifinita solo
   vicino a feature vere) — i test coprono la logica delle opzioni GMSH e
   il wiring GUI, non l'esito su una mesh reale. **Prossimo passo
   concreto**: prendere `sample_cad/` o un caso in `C:/polybench`,
   generare con Automatic ON e OFF, confrontare visivamente/con checkMesh.
4. **Commit**: nulla di questo è ancora stato committato (working tree
   condiviso con Reasonix — verificare `git status`/`git diff` prima,
   Reasonix potrebbe aver toccato `params_panel.py` di nuovo nel
   frattempo per la Fase 2 del BL).
5. Non ho toccato `commercial/mesh_engine.py`/`autopoly_bridge.py` — se
   "Automatic" instrada anche lì per certe geometrie (non verificato
   quanto in profondità copra `_resolve_auto_mesher`), potrebbe esserci
   un terzo punto con lo stesso pattern "flag sempre-vero" da controllare.

## Come riprendere

```
cd "C:\Users\Davide Valoroso\cfmesh-autogui"
git status --short   # cosa è mio, cosa è di Reasonix, prima di toccare altro
# leggere l'esito della suite completa (se il processo bash è ancora vivo,
# aspettare la notifica; altrimenti rilanciare):
"/c/Users/Davide Valoroso/AppData/Local/Programs/Python/Python311/python.exe" -m pytest tests/ -q -k "not bench" --tb=short
```
