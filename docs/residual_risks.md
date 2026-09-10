# Residual Risks And Known Limitations

Status after the Commercial Hardening pass (Deepwork). This document records
the known, accepted limitations that remain after the hardening work, so a
future session does not rediscover them and product decisions have a single
source of truth.

## CI (2026-09-08)

- The GitHub Actions CI job is green again. Root causes of the original
  failure were NOT the package rename itself but what the rename exposed on
  the Linux runner: `pytest-qt` was missing from the installed extras
  (fixed in the rebrand commit), Windows-style case paths were mangled by
  `Path.resolve()` on POSIX (`config.py`, `core/validation.py` — fixed),
  and two trimesh optional dependencies the code paths actually use
  (`networkx` for CAD healing, `rtree` for native-mesher spatial queries)
  were not declared in `pyproject.toml` (fixed). The fast suite is now
  verified green on Linux (WSL mirror of ubuntu-latest) AND Windows before
  every push.
- The full `master` history could not be pushed for months because a
  278 MB installer `.exe` was committed once (blob still reachable from
  every later commit). The pushed `master` branch history was rewritten
  (filter-branch, `installer/output` dropped from the index across the
  range `origin/main..master`; 297 commits preserved, trees verified
  byte-identical). The pre-rewrite history is kept locally in the
  `backup-master-pre-rewrite` branch. NOTE: local `master` SHAs changed —
  references to old SHAs (e.g. in `.opencode/` notes) now point to the
  backup branch.
- Known limitation kept: the Linux run still cannot verify Windows-drive
  filesystem semantics (`test_engine_set_case_dir_nonexistent` is
  win32-only by design); the two conditional venturi-throat skips in
  `test_throat_venturi.py` remain (documented tessellation-dependent
  limitation of the throat detector).

## Meshing Timeouts

- GMSH volume meshing uses a 3600 s wall-clock timeout in every path
  (GUI worker, subprocess helper, adaptive solver). A Very Fine run capped at
  20M cells can still exceed it on very large parts; the failure is surfaced
  clearly in the log, but the estimate in the UI should be consulted first.
- `gmshToFoam` uses 900 s. Extremely large MSH files (many millions of
  elements) may exceed it; the case directory is left in a convertible state.

## Polyhedral Conversion

- The barycentric dual is the only tet->poly path in use. `wedge_cells` and
  `median_faces` are enabled by default (measured best); a post-construction
  quality smoothing pass (`core/poly_smoother.py`, keep-best, interior
  vertices only, boundary pinned) is enabled by default. On CAD parts with
  many concave re-entrant features the dual can still produce a small number
  of non-convex boundary cells (a few hundred on the reference valve),
  failing checkMesh; the poly mesh is then kept and shown with a warning
  instead of silently falling back to tet.
- `split_rounds` remains disabled: measured to add cost without reducing
  defects on the reference part. Re-measure before enabling.
- `TerminalFaceWorker` was removed as dead code; `terminal_face.py` remains
  only for the historical benchmark harness (`tests/bench_tet_poly.py`).
- **BL + poly path (P3a validated)**: `tools/bench_bl_poly.py` runs
  cartesianMesh (hex) -> polyDualMesh -> checkMesh on the venturi. Result:
  poly mesh has 2848 prism cells + 2720 polyhedra, checkMesh `Mesh OK`
  (skew 1.29, NOmax 54.6). This matches the historical documented value
  (2848 prism cells for venturi) and confirms the cfMesh path already
  produces the prism+poly combo required for wall-resolved CFD. The hex
  mesh before polyDualMesh reports 0 prisms for this geometry/config — the
  prisms appear through the dualisation itself; see bench for details.
- **BL on OUR OWN poly mesh (P3b validated)**: `tools/bench_bl_poly_dual.py`
  runs GMSH tet -> gmshToFoam -> barycentric dual -> `core/bl_poly.py`
  (our own advancing-layer engine) -> checkMesh on a cylinder. Result:
  505,950 prism cells + 72,483 polyhedra, checkMesh `Mesh OK` — i.e. the
  definitive all-ours CFD mesher (poly interior + prism boundary layers).
  The GUI now keeps BL enabled on the "Polyhedral (CFD)" path: the
  converter still only ever sees pure tetrahedra, the layers are added
  after conversion.
- **BL selettivo per patch (FASE 1, 2026-08-10)**: `core/bl_poly.py` non
  auto-chiude più la selezione su tutto il boundary. Con `patch_names`
  (default del runner: solo patch `wall` / nome *wall* / ruoli geometrici
  da `infer_patch_roles`), le facce laterali dei prismi al bordo della zona
  BL giacciono nel piano della parete adiacente e diventano **facce di
  boundary della patch senza BL** (possedute dai prismi); le facce di parete
  non selezionate muovono i vertici condivisi all'ultimo layer. Verifica
  reale (`tools/bench_bl_poly_partial.py`, checkMesh WSL, ri-misurato
  2026-09-09): A. cilindro GMSH con patch nominate inlet/outlet/wall → BL
  solo su wall, 60.354 prismi = 3 layer x 20.118 facce (70.976 celle,
  skew 2.264, NOmax 81.1, `Mesh OK`); B. cubo duct (in-memory) → 81 celle,
  72 prismi = 3 x 24, skew 2.175, NOmax 44.8, `Mesh OK`. I vecchi numeri
  "72 prismi = 3 x 24" erano il cubo, non il cilindro.
  `n_prism_cells == n_layers × facce_di_parete` derivato dal mesh di
  ingresso, mai hardcoded.
- **Solver sulla valvola (FASE 0, 2026-08-10)**: vedi
  `docs/poly_solver_validation.md` — potentialFoam converge sulla poly della
  valvola (residuo finale 4.6e-6, continuity error 1.6% del flusso, volume
  0.00%), quindi il difetto concavo (852 facce "incorrectly oriented",
  checkMesh non-OK) è un **limite documentato**, non un bug: FASE 3 non
  necessaria.
- **FASE 2 (terminazione locale dei layer, 2026-08-10)**: macchina
  implementata (conteggio per-vertice nv basato su angle_fade, conteggio per
  faccia, fixpoint di consistenza che rende il conteggio uniforme per
  componente connessa, gate piramidi rilassato `n_pyr_after <= n_pyr_before`,
  facce violate dell'input ridotte a 1 layer) e verificata su mesh sane
  (cubo 6/6: chiusura 0 celle, volume 1e-6; gate FASE 1 ri-verificato PASS).
  Sulle geometrie con difetti concavi pre-esistenti (valvola) il BL fallisce
  ancora pulito con mesh invariata: la chiusura non regge in nessuna delle
  strategie misurate (199 celle non chiuse nella costruzione non vincolata;
  63.252 nel fixpoint; il drop a 0 layer perde il volume). Causa radice:
  angle_fade >= 0.8 su TUTTI i vertici di parete della valvola — la
  concavità delle celle duali non è rilevabile dalle normali di parete.
   Il fallback globale su 5 scale resta il comportamento della valvola
   finché `local_termination` resta opt-in (vedi risultato FASE 2 sotto).
- **Criterio di concavità duale (Lane B, 2026-09-08)**: nuovo parametro
  opt-in `concavity_criterion="dual_convexity"` in `core/bl_poly.py`
  (funzione `_dual_convexity_wall_fade`; default `"angle_fade"` invariato e
  byte-identico). Il detector misura la convessità delle CELLE DUALI
  direttamente: per ogni vertice di parete, frazione delle facce di parete
  incidenti il cui owner cell è non-convesso nel senso esatto del test
  'face pyramids' di checkMesh (centroide OpenFOAM della cella fuori da una
  sua faccia), mappata nella stessa convenzione 0.05..1.0 di angle_fade.
  Misurato sulla fixture valvola (1.051.199 punti, 1.241.048 facce, 152.086
  celle): il segnale scatta ESATTAMENTE sulla classe di difetto documentata —
  393 celle duali non-convesse (intervallo documentato 268-462), 1007 facce
  difettose (= i 1007 difetti piramide dell'input), 1123 vertici di parete
  con fade < 0.5 contro ZERO per angle_fade (fade >= 1.0 su tutti i 226.538
  vertici, confermando la causa radice documentata). Esito misurato dei due
  run completi (in-process, replica checkMesh; n_layers=2, h1=1e-5,
  apply_to_all=True, 5 scale):
  - `angle_fade` (baseline): chiusura fallita a ogni scala — 161 → 106 → 65
    → 45 → 15 celle non chiuse, nessun volume negativo, 523 s, mesh invariata;
  - `dual_convexity`: 24 → 378.605 celle a volume non positivo (flip globale
    del winding alla scala 0.6, 99.99% della mesh) → 11 → 12 → 6 celle non
    chiuse, 599 s, mesh invariata. Meglio della baseline su 4 scale su 5
    (fino a 6.7x a scale=1.0: 24 vs 161), ma NESSUNA scala arriva a 0 celle
    non chiuse: nessun BL valido nemmeno col criterio nuovo.
  Perché non chiude G2 (misurato, non assunto): il fixpoint di consistenza
  appiattisce il conteggio a uniforme per componente connessa — misurato
  nv=nf=1 su TUTTE le 226.542 facce sotto ENTRAMBI i criteri (le 937 facce
  dell'input segnalate forzano l'intera parete a 1 layer in ~100 passate di
  fixpoint). Un detector per-vertice può quindi cambiare solo l'ALTEZZA
  locale (max_h = fade x ...), non il conteggio: migliora la chiusura ma non
  la porta a 0, e destabilizza la riparazione del winding a scale intermedie.
  Raccomandazione: il segnale di convessità duale è un detector STRETTAMENTE
  migliore del fade (393 celle vs 0) e più utile per la chiusura a 4/5 scale,
  ma da solo non chiude la valvola; per sfruttarlo serve rilassare il
  fixpoint (conteggi per-faccia con facce di transizione che chiudono, o
  fixpoint limitato alla regione difettosa). Default invariato: ri-misurare
  prima di abilitare. Gate WSL (`tools/bench_bl_valve_fase2.py`) NON eseguito:
  nessuna scala produce una mesh valida in-process (il verdetto dell'engine
  è deciso dal replicatore, che sulla valvola coincide con checkMesh 895/895),
  quindi non esiste una mesh nuova da sottoporre a checkMesh — la baseline
  pinnata resta il comportamento della valvola. Gate salute
  (`tools/bench_bl_poly_partial.py`): PASS invariato (cilindro 72 prismi =
  3 x 24, Mesh OK; cubo duct 81 celle, skew 2.175, NOmax 44.8, Mesh OK);
  test veloci `tests/test_bl_poly.py`: 6/6 verdi (9/9 con i nuovi test
  del solver globale e della terminazione binaria).
- **Solver di winding GLOBALE (Lane B, 2026-09-08)**: la riparazione
  greedy per-cella è stata sostituita da una soluzione ESATTA
  (`_solve_global_windings`): vincoli di parità per ogni lato-faccia
  (le due facce di una cella su un edge devono percorrerlo in direzioni
  opposte), risolti con BFS sul grafo delle facce, poi segno globale per
  componente (volume totale positivo). Deterministico, O(F+E). Misurato
  sulla valvola a TUTTE e 5 le scale: **0 celle non chiuse** (max_rel
  ~1e-12, era 24 alla scala 1.0), **volume conservato a 1e-16** (era
  drift), nessun flip globale catastrofico — la vecchia greedy alla scala
  0.6 produceva 378.605 celle a volume non positivo, ora nessuna.
  Il solver riporta anche `n_conflicts` (mesh non orientabile) e
  `n_nonmanifold_edges`: sulla valvola entrambi 0. Gate sano
  (`tools/bench_bl_poly_partial.py`) invariato e PASS: cilindro 60.354
  prismi = 3 x 20.118, `Mesh OK`; cubo duct 81 celle, 72 prismi =
  3 x 24, skew 2.175, NOmax 44.8, `Mesh OK`. Nuovo test di regressione:
  `tests/test_bl_poly.py::test_bl_global_winding_solve_is_exact_after_flip`
  (una faccia ribaltata a mano viene richiusa esattamente).
- **Chiusura BL su concava (FASE 2 completata, 2026-09-09)**: opt-in
  `run(local_termination=True)` produce il primo BL VALIDO sulla valvola.
  Meccanismo in due parti, entrambe opt-in:
  1. terminazione binaria (`_build(..., zero_concave=True)`): una faccia o
     riceve l'intero stack di layer o viene esclusa (0 layer) — niente
     fixpoint di consistenza che appiattisce la regione sana a 1 layer;
  2. esclusione iterativa (`_collect_exclusions`): le facce i cui prismi
     risultano invalidi (volume non positivo, piramidi, celle core
     spostate) vengono rimosse dalla selezione e si riprova; fino a
     5 scale x 3 round, esclusioni portate tra le scale; la mesh viene
     scritta SOLO se il gate di validazione passa (altrimenti resta
     invariata, stesso contratto del percorso standard).
  Misurato end-to-end sulla valvola (1.051.199 punti, 1.241.048 facce,
  226.542 facce di parete; n_layers=2, h1=1e-5, `dual_convexity`):
  successo alla scala 0.1 dopo esclusioni a 1.0/0.6/0.35/0.2 — **19.625
  facce escluse (8.7%)**, 410.668 celle prisma, spessore 2.2e-06, volume
  conservato (0.1218350 vs 0.1218351 d'ingresso), min volume cella
  1.15e-14, 1602 s. checkMesh reale: **891 facce "incorrectly oriented" vs
  895 dell'input (migliore)**, non-ortho max 123.3, max skewness 46.8 vs
  45.0, "Failed 4" vs "Failed 3" — l'unico check in più è l'aspect ratio
  (max 15522, 15.921 celle), intrinseco ai prismi BL sottili (h1 = 1e-6 m
  alla scala 0.1); nessun altro check peggiora. Default invariato
  (`local_termination=False`): il percorso standard sulla valvola fallisce
  ancora pulito come documentato (slow test
  `test_valve_fixture_bl_invariants`, 1 passed in 473 s). Nuovi test in
  `tests/test_bl_poly.py` (chiusura esatta, drop binario, successo su cubo
  sano): 9/9 fast verdi.
  Il bench FASE 1 ha inoltre esposto un bug del reader:
  `foam_mesh_io.read_label_list` scambiava per ASCII un payload binario il
  cui probe da 64 byte conteneva un byte 0x29 (indici cella come 41/296/
  10537), restituendo una lista VUOTA — e il rename patch riscriveva il
  polyMesh corrotto (neighbour=0). Risolto con il test "tutti i byte sono
  testo ASCII" al posto dell'euristica `')' in probe`, più regression test
  `test_local_refinement_boxes.py::test_read_label_list_binary_payload_with_0x29_byte`.
- **FASE 3 — la terminazione locale generalizza? (2026-09-09)**.
  Protocollo: `local_termination=True` su geometrie diverse dalla valvola,
  checkMesh REALE prima/dopo, stesse metriche (celle non chiuse, % escluse,
  tempo). Ragionamento e previsioni scritti PRIMA dei test in
  `notes/fase3_reasoning.md` (untracked). Nessun default cambiato; nessun
  wiring GUI (bloccato sulla validazione utente di FASE 2).

  **Predittore economico (senza estrudere nulla):** contare sul dual
  `pyr_input` (facce che violano il criterio piramide) e i vertici di
  parete con fade duale < 0.5. Ordina la difficoltà esattamente come i
  run: cilindro 0/0, cubo 0/0, groove1 4/2, slot1 42/17, valvola
  1.007/1.123. Costo: 27 s sulla valvola (vs 115 s del primo build).

  **Esiti (n_layers=3, h1=0.005, apply_to_all; `standard` vs
  `local_termination` dual_convexity, checkMesh reale):**
  - cilindro (dual 10.618 celle, 24.936 facce): standard 74.808 prismi,
    scala 1.0, Mesh OK (skew 2.97, NOmax 81.1) — **LT identico, 0
    escluse**, 12,6 s; anche `angle_fade` identico. Nessun peggioramento
    dove il percorso standard già funziona.
  - cubo duct (9 celle): identico, 108 prismi, Mesh OK.
  - groove1 (682 celle, 3.372 facce; predittore 4/2): standard
    "success=True" a scala 0.1 ma con TUTTO a 1 layer e checkMesh
    **Failed 2** (2 errori non-ortho, skew 46,2) — una mesh che checkMesh
    boccia. LT dual: **10.062 prismi (3 layer), scala 1.0, Mesh OK**
    (skew 3,29, NOmax 89,1), 18 facce scartate dal criterio binario (0
    esclusioni iterative), 1,4 s. LT angle: 10.032 prismi, 28 scartate,
    Mesh OK. LT triplica i layer E rende la mesh valida.
  - slot1 (9.372 celle, 26.754 facce; predittore 42/17): standard
    **FALLISCE** a tutte le scale (mesh invariata, 34,5 s). LT dual:
    **79.614 prismi, scala 1.0, Mesh OK** (skew 2,89, NOmax 87,0), 216
    facce senza BL = 0,8%, 27,1 s; LT angle: 79.593 prismi, 190 scartate,
    Mesh OK. Seconda geometria concava dove LT sblocca il BL.
  - valvola: seed predicibile 2.737 facce (1,21%): 937 difettose input +
    1.800 con un vertice a fade<0.5 (0 solo-drop). Run a 0.1 con la
    logica di esclusione di produzione: 3 round (114,8/90,7/99,4 s),
    2.058 esclusioni iterative, di cui **1.828 (88,8%) nel seed** e solo
    230 (0,10% del totale) cascata pura; 91,5% delle escluse entro 2
    anelli di adiacenza da una faccia difettosa (ring 0/1/2 =
    803/651/429). checkMesh: 885 facce male orientate vs 895 input,
    "Failed 4" (quarto = aspect ratio), 597.802 celle.
    Contabilità: `local_excluded_faces` conta solo l'iterativo; il totale
    senza BL è 3.684 facce (**1,63%**) perché i drop binari si rivalutano
    a ogni round (un vertice può scendere sotto 0,5 perdendo una faccia
    incidente).
  - **Finding principale — l'ordine delle scale è il vero costo:**
    l'engine thick-first (1.0 → 0.1) accumula TUTTE le 19.625 esclusioni
    iterative prima di arrivare a 0.1 (i warning mostrano fallimenti solo
    a 1.0/0.6/0.35/0.2; a 0.1 valida con il set portato). Lo stesso 0.1
    thin-first ne richiede 2.058: **9,5x in meno**, 8,5% di prismi in più
    (445.716 vs 410.668) e checkMesh marginalmente migliore (885 vs 891).
    Raccomandazione per un lavoro futuro: invertire l'ordine delle scale
    (o azzerare le esclusioni scendendo di scala) con test dedicati —
    NON implementato qui: nessun default cambiato.
  - **Winding globale vs terminazione binaria:** il solver di winding è
    un risolutore esatto (nessun conflitto/edge non-manifold su nessuna
    geometria; 0 celle non chiuse ovunque). Col solo winding il percorso
    standard della valvola resta invalido per volumi negativi (22-44 per
    scala, stage1c): il residuo è geometrico, non di orientamento. La
    terminazione binaria è l'unico pezzo che scala con la difficoltà:
    no-op su cilindro/cubo, 0,5-0,8% di scarto su groove/slot, 0,9-1,6%
    sulla valvola (thin-first).
  - **Previsioni vs misure:** seed, thin-first e anelli confermati. Una
    previsione sbagliata dichiarata: per groove1 avevo previsto "decine/
    centinaia" di vertici a fade basso, misurati 2 — il predittore ordina
    correttamente ma la soglia facile/difficile va tarata. Nessun
    fallimento di LT nel set; l'unico fallimento misurato è il percorso
    STANDARD su slot1.
  - **Tempo:** ~lineare nella dimensione con overhead fisso (cubo 0,03 s;
    groove 1,4-3,2 s; cilindro 12,6-13,5 s; slot1 27,1 s; valvola ~100
    s/tentativo). Il totale = tentativi × dimensione; il thin-first
    valvola costa ~305 s di build contro i 1.602 s del thick-first.
- **FASE 4 — thin-first generalizza oltre la valvola? NO (2026-09-09)**.
  Domanda: il vantaggio del thin-first misurato sulla valvola (9,5x meno
  esclusioni, +8,5% prismi, ~5x più veloce) vale anche su
  cilindro/cubo/groove1/slot1? Metodo: previsione scritta PRIMA
  (`notes/thinfirst_reasoning.md`), due run per geometria con la stessa
  logica interna e la sola tupla delle scale cambiata
  (`tools/bench_bl_thinfirst.py`; il thick-first riproduce esattamente i
  risultati LT di FASE 3, quindi il patch è fedele).

  **Risultato: come inversione cieca NON generalizza — introduce un
  effetto collaterale grave.** Su TUTTE e 4 le geometrie thin-first
  valida al PRIMO tentativo (scala 0.1) e scrive una BL con
  `first_height` effettivo 10x più piccolo del richiesto (5e-4 invece di
  5e-3), in silenzio:
  - cilindro: THICK scala 1.0 vs THIN scala 0.1; stessi prismi (74.808),
    0 esclusioni, tempo identico (17,9 vs 17,0 s), MA aspect max 18,3 →
    **104,3**;
  - cubo duct: 108 prismi in entrambi, aspect 78,7 → **781,6** (~10x);
  - groove1: 10.062 prismi in entrambi, aspect 15,9 → **90,8**;
  - slot1: THICK 79.614 prismi con 132 esclusioni iterative (216 totali)
    a scala 1.0 in 2 round, 36,5 s; THIN 80.004 prismi con **0 esclusioni
    iterative** (86 totali = solo seed) a scala 0.1 in 1 round, 17,9 s —
    qui il vantaggio valvola si rivede (meno esclusioni, +390 prismi, 2x
    più veloce) ma di nuovo con spessore 10x ridotto (aspect 16,7 →
    **128,5**);
  - valvola (FASE 3): nessun conflitto di spessore possibile — la scala
    1.0 falliva comunque, 0.1 era l'unica valida; lì thin-first resta
    migliore e non danneggia.

  **Lettura:** i due effetti sono intrecciati. Il vantaggio (meno
  esclusioni, più prismi, più veloce) esiste solo dove una scala spessa
  fallisce davvero, e nasce dall'evitare di accumulare esclusioni prima
  di arrivare alla scala che valida. Ma "accetta la prima scala che
  valida" significa accettare la 0.1 anche dove la 1.0 richiesta
  funzionerebbe: su una mesh sana degrada l'y+ di 10x senza fallire.
  Per questo l'inversione NON va proposta come default (né implementata).
  La variante da misurare (lavoro futuro, esplicitamente non
  implementato) è **decoupled**: provare la scala richiesta per prima e,
  scendendo di scala, NON portare le esclusioni accumulate (reset al
  cambio scala) — così la valvola otterrebbe ~2.058 esclusioni come
  thin-first ma i casi sani manterrebbero lo spessore richiesto.
  **Previsioni vs misure (onesto):** avevo previsto "thin-first
  identico a thick-first" su cilindro/cubo/groove1 — SBAGLIATO nello
  spessore/scala/aspect (la meccanica "esce al round 0" era giusta, ma
  il round 0 di thin-first È la scala 0.1). Su slot1 avevo previsto
  "uguale o leggermente peggiore in % esclusioni" — sbagliato in segno
  (0 vs 132, quindi meglio), giusto nella meccanica; mancato l'effetto
  spessore. Il predittore FASE 3 (pyr_input) resta valido: il vantaggio
  compare solo dove pyr_input > 0 e cresce col numero di difetti.
  Default invariato (`local_termination=False`, ordine scale invariato),
  nessun wiring GUI.

## Boundary Layer Patch Selection — single source of truth

- `core/patch_roles.py` is now the **only** place that answers "is this
  boundary patch a wall?". It used to be answered independently, and
  differently, in `gui/main_window.py::_is_wall_patch`,
  `commercial/bl_engine.py::detect_wall_patches`,
  `commercial/bc_editor.py::_name_to_type`, `core/case_setup.py::_patch_role`
  and `core/meshdict_gen.py::infer_patch_type`. All but the last now delegate
  (`infer_patch_type` maps to an OpenFOAM *boundary type*, which needs finer
  distinctions than a role — e.g. `wedge` and `cyclic` are not symmetry
  planes).
- Two-level API on purpose: `classify_patch()` defaults a silent name to
  `wall` (the safe majority, used where a decision is mandatory — BL
  extrusion); `match_role()` returns `None` for a silent name so callers with
  a geometric fallback (`bc_editor`, `case_setup`) still reach it. GMSH names
  every patch `surface_N`, so treating "no keyword" as "wall" pre-empted
  geometry on exactly the cases geometry exists to solve.
- **The "apply BL to every patch" fallback is gone.** When neither the
  boundary type/name nor the geometric inference identifies a wall — closed
  domains, external aero, multi-inlet manifolds — the poly worker
  (`core/openfoam_runner.py::poly_bl_patch_selection`) now excludes only what
  is positively known to be an inlet/outlet/symmetry and extrudes from the
  rest; if nothing is left it **skips the boundary layers**. Previously it
  extruded into all patches, i.e. prisms in the inlets and outlets, on
  precisely the geometries where the least is known. checkMesh accepts that,
  so the failure only surfaced when the solver diverged. A mesh without
  layers is a recoverable warning; a mesh with layers in the inlet is a wrong
  answer.
- An explicit user override ("Apply BL to all patches") is still honoured
  literally — the guard must not make a deliberate choice impossible.
- Regression coverage: `tests/test_poly_bl_patch_selection.py` (the decision
  function) and `tests/test_wall_detection_chain.py` (all four entry points
  must agree, and must agree on "skip", never "everywhere").

## Poly Mesh Quality Remediation

- `commercial/poly_remediation.py` (new). The tet→poly path previously had
  **no** automated remediation: `QualityEngine.auto_fix` and
  `MeshOptimizer.optimize` both drive their loop through a cfMesh
  `cartesianMesh` re-run, which `mesh_remeshable` refuses whenever a tet
  backup exists — i.e. exactly for the dual-poly path. A defective poly mesh
  was analysed and then left untouched.
- The loop is strictly non-regressive: one keep-best `poly_smoother` pass per
  iteration (interior dual vertices, boundary pinned), accepted only when the
  defect count measured by the in-process checkMesh replica strictly
  decreases; otherwise the candidate is discarded and the mesh left
  byte-identical. Bounded by iteration and wall-time caps.
- The concave-feature defect class (boundary-face pyramid failures) is
  detected and **skipped**, per the measured dead ends in
  `docs/handoff_poly_bl_deepseek.md`.
- Degrading to the tet mesh is available but **opt-in only** (reported, never
  applied automatically): the user asked for poly.
- Local refinement is deliberately NOT implemented here:
  `local_refinement_boxes_from_checkmesh_sets` emits cfMesh
  `objectRefinements`, which only make sense as input to a cfMesh re-mesh,
  and this path has no re-mesh.

## Adaptive Escalation Substitutes Algorithms — by design, now reported

- `MeshEngine` escalation is **on by default** (`adaptive_escalation=True`,
  `max_escalation_steps=3`). When the requested algorithm fails the quality
  gates the engine escalates along
  `CartesianHex → HexCorePoly → Tetrahedral → PolyAggregated → SnappyHexMesh`
  and returns `success=True` with a **different topology than requested** — a
  user whose solver needs hex-dominant could get tet and a green checkmark.
- `MeshEngineResult` now exposes `algorithm_substituted` plus
  `original_algorithm`, `escalation_reason` and `metrics_before/after`, and
  this propagates to `QuickMeshResult`, `FullAutoResult`, the one-click JSON
  report and the `verification` A/B suite (which flags `[SUBSTITUTED]`).
  `success` still means "a mesh was produced" — `quality_passed` is the
  separate, authoritative quality flag.
- **Known limitation:** the main GUI meshing path does not go through
  `MeshEngine` (it uses `RetryRunner`/`cartesianMesh` directly and never
  builds a `MeshEngineResult`), so a GUI user is not yet warned in the log.
  The substitution is recorded in the artefacts that do flow through
  `MeshEngine` (quick mesh, one-click, A/B verification). Wiring a GUI
  warning is the remaining work.
- **Redundant rung removed (2026-09-02):** `HEX_CORE_POLY` and `POLYHEDRAL`
  dispatch to the identical pipeline (`_run_cartesian_hex` +
  `_run_polyhedral`), i.e. `polyDualMesh` over the whole mesh. The ladder
  used to climb `Polyhedral → HexCorePoly`, which re-ran the entire pipeline
  only to rebuild the same dual mesh — a no-op that consumed one of just
  three escalation attempts. The rung is now `POLYHEDRAL` (rank 2) instead of
  `HEX_CORE_POLY` (rank 3): identical result for a hex start, and a
  `POLYHEDRAL` start escalates straight to tetrahedral instead of into
  itself. Guarded by `test_escalation_ladder_no_redundant_dispatch` and
  `test_next_escalation_from_polyhedral_skips_the_no_op`.
- **`HexCorePolyBoundary` is not a Mosaic mesher.** It was labelled "Hex Core
  + Poly Boundary (Mosaic-style)" while producing no hex core, no poly
  transition zone and no prisms. Relabelled "Hex → Polyhedral (cfMesh
  polyDualMesh)"; the enum value is unchanged so existing case configs still
  load. `commercial/mosaic.py` likewise only runs whole-mesh `polyDualMesh
  90` — its "Mosaic" name is historical and its docstrings now say so.
  Implementing a true Mosaic topology remains out of scope.
- **`polyDualMesh` increases the cell count (~15-30%)**, it does not reduce
  it. Two descriptions claimed a 40-60% reduction; corrected. The *tet→poly
  barycentric dual* is the one that reduces cells (~4-5x, measured:
  box_obstacle 12597 → 2575, pipe 10014 → 2437). Different code paths — do
  not conflate them.

## Viewer

- Volume-mesh display renders the internal VTU wireframe. On meshes above
  ~2M cells the view is decimated for interactivity; the on-disk mesh is
  never modified. The Section Cut view shows the true internal cells.
- The viewer depends on `foamToVTK` being available inside WSL2/OpenFOAM.
  When it is missing or times out, the viewer shows a ParaView hint instead
  of the mesh.

## Sizing And Estimates

- **Manual refinement zones preserve geometry sizing (2026-09-08)**: the
  refinement-zones block used to call `setAsBackgroundMesh()` on the zone
  fields alone, silently DISCARDING the geometry-adaptive background field
  (curvature/small-feature/gap sizing) whenever a manual refinement box was
  active — documented as an unfixed bug in
  `docs/dev/solution_adaptive_handoff.md` §1.4. Zones are now MIN-combined
  with the existing field (`_combine_zone_fields_with_bg`), so a zone can
  only ask for smaller cells, never change sizing elsewhere. Regression
  tests: `test_gmsh_refinement_boxes.py::test_single_zone_min_combined_with_existing_bg`
  and friends.

- The Mesh Fineness slider value is a budget/cap (10K..20M cells), not a
  guarantee. The displayed geometry estimate is derived from the tessellated
  solid volume and the derived cell sizes; when the volume cannot be trusted
  (open/non-watertight tessellation) the estimate is omitted rather than
  invented.
- The GMSH adaptive path sizes from real geometry features; the cfMesh path
  uses the derived Max/Min cell sizes. The two meshers can therefore produce
  different cell counts for the same slider position.
- Pre-existing test failure (not caused by the hardening pass, reproduced on
  commit d0337b5): `tests/test_sizing_resolves_features.py` failed because
  `cq.Workplane` fixtures were authored in millimetres while
  `tessellate_patches` converts to metres — local thickness was measured
  ~1000x smaller than the test expected (p1 ≈ 5.99e-05 vs 0.06). The
  unit-of-authoring contract for in-memory CAD fixtures needed a product
  decision (author fixtures in metres, or scale before tessellation); do not
  patch the failing assertions to hide it.
  **RESOLVED (2026-08-03)**: the fixtures are now authored at metre scale
  in cadquery millimetres (0.1 m rod = `circle(100)`, throat = `circle(30)`),
  matching the app's documented contract that tessellation converts mm→m.
  The assertions were not touched; the test passes (4/4).
- **Closed curved bodies tessellate watertight now (2026-09-08)**: the old
  "sphere → not watertight" finding from `docs/dev/notes/NIGHT_LOOP_PROMPT.md`
  is fixed. OCC tessellation emits coincident duplicate vertices at the
  seams/poles of curved faces; `tessellate_patches` now merges them and drops
  the degenerate pole triangles, so a cadquery sphere passes
  `check_watertight()` (regression: `test_geometry.py::test_tessellate_closed_curved_body_is_watertight`).
- `test_watertight_meshdict.py::test_volume_mesh_and_quality_steps_do_not_need_a_qt_event_loop`
  is order-sensitive (shared QApplication state); it passes in isolation.

## Packaging And Runtime

- The frozen EXE is rebuilt on demand (`pyinstaller --clean --noconfirm
  PolyFoamMesh.spec`). The `dist/` artifact is not refreshed automatically
  and may lag the source tree.
- The API server module (`polyfoammesh.api.server`) imports cleanly with
  the installed FastAPI; it remains a CI/CD surface, not part of the GUI.

## Test Matrix

- Fast (no WSL) suite: green.
- Real WSL/OpenFOAM smoke (GMSH tet -> gmshToFoam -> dual poly -> checkMesh):
  `Mesh OK` on the reference cylinder case.
- Full WSL regression runs are not part of the fast suite; they require
  OpenFOAM v2512 in WSL2 and are executed manually or via the harnesses in
  `tools/`/`tests/bench_*`.
