# Residual Risks And Known Limitations

Status after the Commercial Hardening pass (Deepwork). This document records
the known, accepted limitations that remain after the hardening work, so a
future session does not rediscover them and product decisions have a single
source of truth.

## Status as of 2026-09-17

A RAM-exhaustion crash was found and fixed on the boundary-layer /
cell-merge path (a real ~5M-cell run with "concave closure" on exhausted
32GB and crashed the OS). Root cause was Python dict/list structures
built over the whole mesh instead of the cells/faces actually involved;
now chunked/vectorized. A ~5M-cell / 15M-face synthetic run now peaks at
~13GB; the safety guard moved from a rough 1.5M-cell cap to a measured
8M-face threshold. **That threshold is still only measured on a
synthetic hex mesh, not on the real dual/poly path** — see
[#9](https://github.com/LorenzoRiti/PolyFoamMesh/issues/9).

Three literature-grounded boundary-layer extrusion-normal strategies were
evaluated against real `checkMesh` on concave geometries (most-visible
normal, Laplacian-smoothed normal, guided bilateral filter), plus a
batched per-vertex local-height retry. The most-visible + local-height-
retry combination is shipped as an opt-in that measurably improves wall
coverage over either alone; the other variants were measured and are
documented as honest no-ops, not shipped. All of this is opt-in;
defaults are unchanged.

The reference cylinder still has a real aspect-ratio gap against
snappyHexMesh (4.49 vs 3.02). A direct cell-merge fix was tried and
rejected by real checkMesh (non-orthogonality +80%, skewness +233%); a
zonal smoothing pass helped marginally (-0.5%) but hit a topology-bound
wall. The diagnosed fix is a directional split operator, not yet
written — see [#8](https://github.com/LorenzoRiti/PolyFoamMesh/issues/8).

**Open issues tracking the largest known gaps** (good starting points for
contributors — each has a diagnosis, prior attempts, and acceptance
criteria already written up):

- [#8 — Directional split operator for the cylinder aspect-ratio gap](https://github.com/LorenzoRiti/PolyFoamMesh/issues/8)
- [#9 — Validate the RAM safety threshold on real dual/poly geometries](https://github.com/LorenzoRiti/PolyFoamMesh/issues/9)
- [#10 — Vectorize the remaining O(n_cells) loop in the cell-merge repair](https://github.com/LorenzoRiti/PolyFoamMesh/issues/10)

See `CHANGELOG.md` for the full list of what changed in this pass.

## Meshing Timeouts

- GMSH volume meshing uses a 3600 s wall-clock timeout in every path
  (GUI worker, subprocess helper, adaptive solver). A Very Fine run capped at
  20M cells can still exceed it on very large parts; the failure is surfaced
  clearly in the log, but the estimate in the UI should be consulted first.
- `gmshToFoam` uses 900 s. Extremely large MSH files (many millions of
  elements) may exceed it; the case directory is left in a convertible state.

## Polyhedral Conversion

- The barycentric dual is the only tet->poly path in use. `wedge_cells` and `median_faces` are disabled by default. On the valve A/B measurement, `median_faces=True` produced 1,056 residual defects versus 1,027 with `median_faces=False`, so the default remains off. `wedge_cells` made no difference in the same A/B measurement and is incompatible with the collapse configuration. A post-construction quality smoothing pass (`core/poly_smoother.py`, keep-best, interior vertices only, boundary pinned) is enabled by default. On CAD parts with
  many concave re-entrant features the dual can still produce a small number
  of non-convex boundary cells (a few hundred on the reference valve),
  failing checkMesh; the poly mesh is then kept and shown with a warning
  instead of silently falling back to tet.
- `split_rounds` remains disabled: re-measured on the 2026-09-09
  regenerated valve fixture (`TetPolyDualConverter(case,
  split_rounds=0|1)`; `tools/split_rounds_rebench.py`). Both runs
  produce **identical defect counts** (pyramid 863, non_ortho 203,
  skew 3, total 1069, volume drift 3.4e-16) — the converter's
  keep-best in `_run` selects round 0 and discards later rounds.
  `split_rounds=1` adds ~28 s wall time (138.1 s → 166.4 s, +20%)
  for zero defect improvement. Default unchanged (`split_rounds=0`
  off). A second re-measure on the next converter change is enough
  to update this note.
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
- **FASE 5 — variante "decoupled" implementata e misurata (2026-09-09)**.
  Sulla scorta di FASE 3/4 è stata implementata la variante proposta:
  opt-in nel motore `run(local_termination="decoupled")` (`bl_poly.py`):
  stesso loop (max 3 round, scale 1.0 → 0.1) ma il set di esclusioni
  viene **azzerato al cambio di scala** — si prova prima la scala
  RICHIESTA (1.0, spessore pieno) e solo se fallisce si scende, senza
  far pagare alle scale sottili i difetti delle scale grosse. `True`
  (legacy carry) e il default `False` restano invariati; test nuovi
  `test_bl_local_termination_decoupled_mode_on_healthy_cube` e
  `test_bl_local_termination_rejects_unknown_mode` (fast suite 1153
  verdi). Previsione scritta prima in `notes/decoupled_reasoning.md`.

  Misure (`tools/bench_bl_thinfirst.py --modes thick,thin,decoupled
  [--valve]`, checkMesh reale):
  - cilindro/cubo/groove1/slot1: DECOUPLED **identico a THICK** in tutto
    — scala 1.0 (spessore pieno), stessi prismi (74.808 / 108 / 10.062 /
    79.614), stesse esclusioni (0/0/0/132), stessi aspect max (18,3 /
    78,7 / 15,9 / 16,7) e tempi. Il reset non entra mai in gioco quando
    il successo arriva dentro la scala richiesta: nessuna degradazione
    dello spessore del thin-first puro.
  - valvola: successo a scala 0.1 con **2.058 esclusioni** (previste e
    ottenute) e **445.716 prismi** — esattamente il risultato thin-first
    (3.684 facce senza BL = 1,63%). checkMesh: **885 facce male orientate
    vs 895 input**, "Failed 4" (quarto = aspect ratio), skew 47,6,
    non-ortho 55 — identico al thin-first.
  - **Tempo valvola: 2.215 s (37 min)** — più lento della previsione
    (24-30 min) e più lento sia del thin (305 s) sia del thick (1.602 s):
    prova comunque tutte le scale (15 build, 3 round per scala). Il
    vantaggio è in esclusioni/prismi, non nel tempo.
  - Traiettoria warning valvola: fallimenti a 1.0 (50→6→6 volumi
    negativi), 0.6, 0.35, 0.2, poi 0.1 fresco al terzo round
    (1.946+112) valida.

  **Verdetto**: decoupled centra l'obiettivo — cattura il vantaggio
  thin-first sulla valvola (9,5x meno esclusioni, +8,5% prismi,
  checkMesh migliore) SENZA degradare lo spessore sui casi sani
  (restano a scala 1.0 piena). È la candidata naturale per la semantica
  della terminazione locale quando verrà abilitata (FASE 2 in
  validazione utente): "spessore richiesto se possibile, solo i difetti
  pagano". Resta il costo in tempo sulle geometrie difettose; una
  possibile ottimizzazione (saltare scale intermedie o riusare le
  esclusioni a firma di fallimento identica) richiede una misura
  dedicata. Default invariato, nessun wiring GUI.

  Tabella riassuntiva valvola (stesso input fixture, n_layers=2,
  h1=1e-5, dual_convexity):

  | variante | escl. iterative | facce senza BL | prismi | checkMesh | tempo |
  |---|---|---|---|---|---|
  | THICK (legacy) | 19.625 | 21.208 (9,36%) | 410.668 | 891 wrong | 1.602 s |
  | THIN (sperim.) | 2.058 | 3.684 (1,63%) | 445.716 | 885 wrong | 305 s |
  | DECOUPLED | 2.058 | 3.684 (1,63%) | 445.716 | 885 wrong | 2.215 s |
- **FASE 6 — early-exit-intermediates implementato e misurato
  (2026-09-09)**. Per rispondere alla domanda "un early-exit (fermati
  alla prima scala valida) riduce il tempo sulla valvola?": L'engine
  ha GIÀ un early-exit alla prima scala valida (`if ok: return
  self._accept_built(...)`) — la cilindro/cubo/groove1 validano a
  scala 1.0 round 0 e fanno **1 sola build** (12,8 s, 1,4 s, 0,0 s).
  Il tempo lungo della valvola nel decoupled (2.215 s, 15 build) non
  è un bug di "non esce": è che solo 0.1 valida e le 4 scale grosse
  sono tutte "ugualmente inutili" (pyr count 1010-1019 vs input 1007).
  Per risparmiare quei 9 build è stata implementata la variante
  opt-in `early_exit_intermediates`: se 1.0 round 0 fallisce, le scale
  intermedie (0.6, 0.35, 0.2) vengono skippate e si va diretto a 0.1.
  Implementazione in `bl_poly.py` (parametro engine, default off;
  trigger dentro `_run_local_termination` dopo il fallimento di 1.0
  round 0; statistiche in `res.stats["local_termination_early_exit_intermediates"]`).
  Test: `test_bl_local_termination_early_exit_intermediates_noop_on_healthy`
  (fast suite 1154 verdi). Previsione scritta prima in
  `notes/early_exit_reasoning.md`.

  Misure (`tools/bench_bl_thinfirst.py --modes early_exit [--valve]`,
  checkMesh reale):

  | Geometria | DECOUPLED | EARLY_EXIT | Δ |
  |---|---|---|---|
  | cilindro | scala 1.0, 74.808, 0 escl, 17,8 s, aspect 18,3 | scala 1.0, 74.808, 0 escl, 12,8 s, aspect 18,3 | identico (no-op) |
  | cubo | scala 1.0, 108, 0 escl, 0,0 s, aspect 78,7 | scala 1.0, 108, 0 escl, 0,0 s, aspect 78,7 | identico |
  | groove1 | scala 1.0, 10.062, 0 escl, 2,1 s, aspect 15,9 | scala 1.0, 10.062, 0 escl, 1,4 s, aspect 15,9 | identico |
  | **slot1** | scala 1.0, 79.614, 132 escl, 36,3 s, aspect 16,7 | **scala 0.1, 80.004, 0 escl, 25,2 s, aspect 128,5** | **degenera in thin-first** |
  | **valvola** | scala 0.1, 445.716, 2.058 escl, 2.215 s, checkMesh 885 | **scala 0.1, 445.716, 2.058 escl, 414 s, checkMesh 885** | **identico, 5,4x più veloce** |

  **Verdetto**: sulla valvola centra l'obiettivo: stesso risultato del
  decoupled (2.058 escl, 445.716 prismi, checkMesh 885) in **414 s
  invece di 2.215 s (5,4x)** con sole 4 build (1.0 round 0 fail +
  skip + 0.1 round 0, 1, 2) invece di 15. Traiettoria warning valvola:
  1.0 round 0 fail (50 volumi negativi) → skip 0.6/0.35/0.2 → 0.1
  round 0 fail (2 neg) → 0.1 round 1 fail (1012 piramidi > 1007
  input) → 0.1 round 2 valida con 2.058 escl.

  **Rischio documentato**: la euristica è sicura sulla valvola ma
  **degenera in thin-first su slot1** (scala 0.1, aspect 128,5
  invece di 1.0/16,7): il trigger "1.0 round 0 fallisce → skip
  intermedie" si attiva anche quando 1.0 avrebbe validato dopo 1 round
  di esclusioni (slot1: round 0 fallisce con 50 pyr > 42 input, ma
  round 1 con 132 excl valida). Lo skip fa perdere quel round 1 e
  costringe a 0.1 con set pulito (= thin-first). Quindi
  `early_exit_intermediates` è candidato *solo* per geometrie tipo
  valvola (dove 1.0 non ha speranza di validare a nessun round); per
  geometrie tipo slot1 è troppo aggressivo. Una euristica più fine
  ("skip solo se round 0 e round 1 di 1.0 falliscono entrambi") o
  ("skip solo se pyr_round0 di 1.0 non migliora scendendo") richiede
  una misura dedicata. Default invariato, nessun wiring GUI.

  Tabella riassuntiva valvola (stesso input fixture, n_layers=2,
  h1=1e-5, dual_convexity):

  | variante | escl. iterative | facce senza BL | prismi | checkMesh | tempo |
  |---|---|---|---|---|---|
  | THICK (legacy) | 19.625 | 21.208 (9,36%) | 410.668 | 891 wrong | 1.602 s |
  | THIN (sperim.) | 2.058 | 3.684 (1,63%) | 445.716 | 885 wrong | 305 s |
  | DECOUPLED | 2.058 | 3.684 (1,63%) | 445.716 | 885 wrong | 2.215 s |

  **CAVEAT (FASE 7, 2026-09-09)**: i numeri valvola di FASE 6 sono
  **pinned alla fixture stale** `valve_dual.npz` (generata 2026-08-01 con
  un converter diverso da quello attuale) e il trigger di
  `early_exit_intermediates` è stato **corretto in FASE 7** (saltava anche
  i round 1-2 della scala 1.0, non solo le intermedie). Su un input
  riconvertito con il converter attuale la storia cambia: vedi §FASE 7.
- **FASE 7 — BL sulla topologia di PRODUZIONE (collapsed) + fixture
  stale (2026-09-09)**. Origine: `openfoam_runner.py:1652` converte il
  poly di produzione con `collapse_smooth_edges=True,
  boundary_feature_angle=40.0, collapse_volume_tolerance=0.10`, mentre
  TUTTE le misure BL FASE 2-6 (fixture e bench) usano il converter con i
  default (**collapse OFF**, tre quad per triangolo). Il dual collapsed ha
  3-6x meno facce di bordo e `bl_poly` estrude un prisma per faccia:
  misurato con `tools/bench_bl_production_topology.py` (nuovo).

  | | exact (default) | production (collapsed) |
  |---|---|---|
  | cilindro, facce bordo | 24.936 (quad) | **4.412** (4-8-goni, 5,65x meno) |
  | cilindro, drift volume | 1,4e-16 | 0,128% |
  | cilindro, BL 3 layer | scala 1.0, 0 escl, **74.808** prismi, 13,0 s, Mesh OK | scala 1.0, 0 escl, **13.236** prismi, 5,6 s, Mesh OK |
  | valvola, facce bordo | 226.542 (quad) | **42.130** (4-9-goni, 5,4x meno) |
  | valvola, difetti input (checkMesh wrong) | 863 | **340** |
  | valvola, drift volume | 3,4e-16 | 1,08% |
  | valvola, BL | **successo scala 1.0** (2 build): 2.525 escl, 444.924 prismi, 217 s; checkMesh 836 wrong (< 863 input), skew 38,1 (vs 14,6 input), aspect 1.543, Failed 4 | **FALLISCE**: `decoupled` 15 build in 892,7 s, 2.189 escl, stallo 375-444 piramidi vs 370 input; mesh invariata |

  - **La fixture valvola è STALE**: `tests/fixtures/valve_dual.npz`
    (2026-08-01) registra 895 wrong / 55 errori non-ortho / skew 44,3;
    una conversione fresca dello STESSO tet backup oggi dà 863 wrong / 9
    errori / skew 14,6. Tutti i numeri valvola di FASE 2-6 sono quindi
    rappresentativi solo di quello snapshot: sulla conversione attuale lo
    stesso pipeline si comporta diversamente (esempio: il successo a 0.1
    con 2.058 escl non si riproduce; il nuovo input valida a **scala 1.0
    round 1** con 2.525 escl e spessore pieno).
  - **Bug dell'early-exit FASE 6 trovato e corretto**: il trigger
    scattava a 1.0 round 0 e saltava anche i round 1-2 della scala
    richiesta (non solo le intermedie). Mascherato dalla fixture stale
    (dove 1.0 era comunque senza speranza) e causa reale della
    "degenerazione" su slot1. Ora il trigger scatta solo quando la scala
    1.0 ha esaurito i suoi round: su cylinder/cubo/groove1 resta no-op;
    sulla valvola exact ri-misurata dà successo a 1.0 round 1 in 217 s
    (identico al legacy). Test fast 12/12 verdi.
  - **Previsione vs misura**: avevo previsto (70%) che il BL chiudesse
    anche sulla topologia di produzione; **misura: fallisce** (30%
    previsto). Sui casi sani la previsione era corretta (successo, 5,65x
    meno prismi, Mesh OK).
  - **Implicazioni (nessun default cambiato)**: (1) la fixture va
    rigenerata e i numeri pinnati aggiornati prima di qualunque altra
    misura valvola; (2) il percorso BL di produzione su geometria concava
    **non è oggi supportato** (il collapsed è ottimo per conteggio celle e
    sui casi sani, ma la mappatura esclusioni→poligoni non chiude la
    valvola): da affrontare come lane dedicata (es. esclusione per-vertice
    o granularità mista), non in questa sessione; (3) i bench BL esistenti
    usano ancora il dual exact: dichiararlo in questo documento evita di
    confondere i due mondi.
- **FASE 8 — fixture rigenerata, fast test re-pinnati, BL collapsed
  measurement (widen=0/1) (2026-09-09)**. Tre esiti consecutivi su
  questa lane; le conclusioni oneste, **nessun default cambiato**.

  1. **Fixture valvola `tests/fixtures/valve_dual.npz` rigenerata** con
     il converter attuale (2026-09-09, dimensione 27,3 MB, identica
     alla precedente; il file `valve_dual_checkmesh.txt` registrato
     accanto): checkMesh riporta **863 facce male orientate, 9 errori
     non-ortho, skew 14,58, Failed 3**, contro i vecchi pinnati
     (895/55/44,3) — un miglioramento del converter sullo stesso tet
     backup. L'in-process detector `_detect_defects` riporta
     **863 / 203 / 3** (pyr / non-ortho-det / skew-det): 863 pyr coincide
     con checkMesh; 203 = checkMesh "nonOrthoFaces" scritto sul set (la
     863 → 9 "errors" >70° del checkMesh); 3 è la skew-det
     (checkMesh ne scrive 98 "highly skew"). Il `tests/test_tet_poly_dual`
     parametrize e' stato aggiornato a
     `{"pyramid": 863, "non_ortho_det": 203, "skew_det": 3}`.
  2. **`poly_fixture_builder.py` corretto**: il fvSchemes iniziale non
     conteneva `divSchemes`/`laplacianSchemes`/`interpolationSchemes`/
     `snGradSchemes` e `checkMesh` 2512 falliva con `FATAL IO ERROR:
     Entry 'divSchemes' not found`. Aggiunto il set completo. Tutti i
     successivi run del builder producono un checkMesh valido.
  3. **Slow test `test_valve_fixture_bl_invariants`**: passa sul nuovo
     fixture (373 s, clean-failure: volumi negativi per scala 1.0→0.1
     con 104/64/48/40/16 counts; mesh invariata, polyMesh esiste). La
     docstring "must FAIL CLEANLY" resta corretta; un FileNotFoundError
     accidentale in una run precedente era dovuto a una **doppia
     esecuzione concorrente** del test (il fixer ha rilanciato il
     comando, la seconda istanza ha cancellato `valve_bl` con `rmtree`
     mentre la prima girava) — un artefatto di concorrenza, non un bug
     del motore. Per ridurre la finestra di race, una soluzione
     semplice sarebbe rendere la `case` unica per run (es. tramite
     timestamp nel nome) — non implementata qui.
  4. **BL collapsed widening experiment (FASE 8)**: parametro engine
     opt-in `local_exclude_widen: int = 0` (default invariato) in
     `bl_poly.run()` (validato 0..3), con metodo `_widen_exclusions`
     che espande l'elenco di esclusioni raccolto in ogni round di N
     anelli di vicini di bordo (costruiti da
     `pre["bnd_edge_faces"]`). Test:
     `test_bl_local_exclude_widen_param` (cubo sano riusce con
     `widen=2`; `widen=4` errore). Bench:
     `tools/bench_bl_production_topology.py --exclude-widen N`.
  5. **Misura collapsed valve con `local_exclude_widen=1`** (production
     + decoupled + `max_rounds=6`): 2.064 s, 25 build, **2.207
     esclusioni** (identico a widen=0), stallo 373-378 (0.2) / 379
     (0.1 r3-r5), fallimento pulito. **H6 widen=1 falsificata**:
     allargare di 1 anello non aggiunge facce al set escludibile (la
     frontiera del rim e' gia' 1-anello in molte posizioni, o la
     pyr-violation al rim non e' semplicemente "spostata di 1 anello").
     `widen=2` non misurato (costo ~30 min, atteso negativo).
  6. **H2 confermata, H3 + H6 widen=1 falsificate**: il difetto del
     BL collapsed sulla valvola non e' chiudibile con exclusion
     granularity (face + 1 anello). Il gap strutturale (H4, mapping
     esclusione → per-vertice) resta la strada da percorrere per
     supportare la BL di produzione su geometrie concave. Tutto
     rimane opt-in: nessun default e' cambiato.
- **FASE 9 / H4 — esclusioni per-VERTICE: la valvola collapsed di
  produzione CHIUDE (2026-09-09)**. Nuova modalita' opt-in
  `local_termination="decoupled_vertex"` (default invariato):
  identica a `"decoupled"` ma l'unita' di esclusione iterativa e' il
  VERTICE di parete — quando un prisma/cella core e' invalido si
  escludono TUTTI i vertici della sua faccia di base; una faccia e'
  estrusa solo se nessuno dei suoi vertici e' escluso. Sul dual
  collapsed (una poligonale di bordo = la stella di un vertice) una
  vertice difettoso propaga a tutte le facce incidenti in UN round.
  Il drop binario iniziale (`zero_concave`) resta per-faccia,
  invariato; `local_exclude_widen` e' un no-op in questa modalita'.
  Implementazione in `core/bl_poly.py`
  (`_run_local_termination(..., vertex_exclusions=True)`); unit test
  `test_bl_local_termination_decoupled_vertex_on_healthy_cube`
  (fast suite 15/15 nel file); regressione slow
  `tests/test_bl_collapsed_valve.py` (skip se il tet backup di
  C:/polybench/valve1 manca).

  Misura (production converter kwargs, `n_layers=2`, h1=1e-5,
  dual_convexity, max_rounds=3; conversione collapsed ~105 s):
  - **success=True a scala 0.6**, 5 build, ~297 s;
  - **4.008 facce escluse su 42.130 (9,5%)**, **75.360 prismi**;
  - traiettoria: 1.0 r0 fail (54 volumi negativi) → r1 383 vs 370 →
    r2 373 vs 370 (fixed point del vertex-closure: il gate a 1.0 NON
    e' raggiungibile); 0.6 r0 fail (38 neg) → r1 valida;
    `max_rounds=6` da' lo stesso risultato (1.0 si ferma comunque);
  - checkMesh reale: **340 facce male orientate = esattamente
    l'input (ZERO aggiunte)**, 10 errori non-ortho (= input), NOmax
    95,6 (= input), skew 27,5 (input 22,2), aspect 6.555 (input
    100,8), "Failed 4" vs input "Failed 3" — l'unico check in piu'
    e' l'aspect ratio, intrinseco ai prismi BL sottili.
  Confronto con il modello per-faccia sullo stesso input: FASE 7/8
  falliva a tutte le scale (stallo 373-385 vs 370, 2.189-2.207
  esclusioni). Previsione precedente (45% di chiusura, 2.300-2.900
  esclusioni a 0.1) era **pessimista sul successo e conservativa sul
  conteggio**: ha chiuso con piu' facce (4.008) ma a scala piu'
  spessa (0.6). Nessun default cambiato: la modalita' e' opt-in e
  documentata.
  - **Interazione da NON combinare**: `early_exit_intermediates`
    salterebbe proprio la scala 0.6 che questa modalita' usa per
    validare (1.0 esaurisce senza chiudere → il flag va diretto a
    0.1). La combinazione non e' stata misurata e non e'
    raccomandata; il flag resta pensato per i casi in cui 1.0 e'
    senza speranza e le intermedie inutili (valvola exact, fixture).
  - **No-op sui sani, verificato**: cilindro exact con
    `decoupled_vertex` = identico a `decoupled` (scala 1.0, 0
    esclusioni, 74.808 prismi, `Mesh OK`, 13,6 s); cubo sano nel
    unit test. Su mesh senza difetti il vertex-closure non raccoglie
    nulla per costruzione (break al primo round).
  - **Generalizzazione a cubo/groove1/slot1 (2026-09-11,
    `tools/h4_generalization_rebench.py`)**: previsione scritta
    prima in `notes/h4_generalization_reasoning.md`, misura con
    checkMesh reale via WSL, confronto diretto `decoupled` vs
    `decoupled_vertex` sullo stesso input:
    - **cube_duct** (9 celle, 0 difetti in input): identici in
      tutto — 0 esclusioni, 108 prismi, scala 1.0, `Mesh OK`.
      Previsione (no-op) **confermata**.
    - **groove1** (682 celle, 3.372 facce): identici — 0
      esclusioni, 10.062 prismi, scala 1.0, `Mesh OK`. Previsione
      (equivalente a `decoupled`) **confermata**.
    - **slot1** (9.372 celle, 26.754 facce, 22 difetti piramide in
      input): entrambi chiudono (scala 1.0, `Mesh OK`, 0 difetti
      aggiunti), ma **non identici**: `decoupled` esclude 132 facce
      → 79.614 prismi; `decoupled_vertex` esclude **378** facce
      (~3x) → **78.903** prismi (-711). La previsione (0 esclusioni
      aggiuntive, risultato numericamente identico a `decoupled`) e'
      **smentita**: il cascade per-vertice su slot1 propaga a piu'
      facce del face-mode pur convergendo allo stesso esito
      qualitativo (chiude, nessun difetto aggiunto). Nessun default
      cambiato; `decoupled_vertex` resta opt-in.
  - **Diagnosi del ~3x su slot1 (2026-09-11,
    `tools/slot1_exclusion_inspect.py`)**: l'ipotesi iniziale
    "valenza per-vertice più alta su slot1" (più facce incidenti
    per vertice → propagazione per-vertice più ampia) è
    **smentita**. Misura della valenza dei vertici di parete sul
    dual convertito (numero di facce di parete incidenti per vertice)
    su ciascuna delle tre mesh:

    | geometry | n wall verts | mean | max | p50 | p90 | p99 |
    |---|---|---|---|---|---|---|
    | cube_duct | 38 | 3.79 | 5 | 12.0 | 28.0 | 31.6 |
    | groove1 | 3374 | 4.00 | 9 | 248.0 | 1854.6 | 2208.7 |
    | slot1 | 26756 | 4.00 | 9 | 261.0 | 14271.2 | 17483.1 |

    La valenza media (4.0 vs 4.0 vs 3.79) e massima (9 vs 9 vs 5)
    sono simili tra slot1 e groove1 (la differenza cube-duct è
    irrilevante per il confronto: è il caso "no-op"). Quindi la
    causa del ~3x esclusioni su slot1 NON è la valenza locale ma
    la **scala assoluta**: slot1 ha ~7.9x più vertici di parete
    di groove1 (26756 vs 3374), e il cascade vertex-mode propaga
    l'esclusione a tutti i vertici incidenti alle facce difettose
    (chiusura 1-anello). Più vertici → più superficie nella
    closure → più facce escluse, anche con valenza simile. Il
    fattore osservato (~3x) è circa 38% del rapporto di vertici
    (7.9x) perché il closure cattura solo i vertici incidenti ai
    difetti (22 input → ~22 × valenza ≈ ~100 vertici catturati),
    non tutti i 26756. Il rapporto 2.86x tra set esclusi (378 vs
    132) è la proiezione locale della propagazione per-vertice
    sulla sottoregione dei difetti, non un effetto del modello
    vertex-mode sul resto della mesh. Conclusione: il modello vertex
    fa il suo lavoro (chiude la mesh, Mesh OK), ma su input con
    geometrie ad alta densità di boundary vertices (canali stretti,
    fessure), l'amplificazione è proporzionale al numero di
    vertici incidenti alla closure — non un bug del criterio
    vertex, una proprietà della topologia locale. Nessun default
    cambiato.
  - **Wiring in GUI come opzione opt-in (2026-09-11)**: `decoupled_vertex`
    è ora raggiungibile dall'utente — checkbox "Chiusura avanzata per
    geometrie concave (sperimentale)" nel pannello Boundary Layers
    (`gui/params_panel.py`, `get_bl_params()["concaveClosure"]`), spenta
    di default. Quando spuntata, `core/openfoam_runner.py`
    (`DualPolyWorker`) passa `local_termination="decoupled_vertex"` al
    motore invece di lasciarlo invariato (`False`). Nessun altro
    parametro è esposto (il `concavity_criterion` resta al default
    `"angle_fade"`, non `"dual_convexity"` usato nel benchmark FASE 9).
    **Validazione end-to-end reale** (non mock — `DualPolyWorker.run()`
    chiamato direttamente sulla valvola di produzione, stesso percorso
    di codice della GUI, checkMesh via WSL): 75.200 prismi (2 layer),
    340 facce mal orientate = **esattamente il baseline pre-BL** (zero
    aggiunte), 10 errori non-ortho (= baseline), NOmax 95,6 (= baseline),
    "Failed 4 mesh checks" (= atteso, il difetto concavo è un limite
    noto della conversione, non introdotto dal BL). Cioè: **anche col
    criterio di default `angle_fade`** (non quello usato nel benchmark
    originale) il vertex-cascade chiude comunque la mesh senza
    aggiungere difetti — il ciclo di validazione per-round è basato su
    controlli checkMesh diretti (volumi/piramidi), non solo sul fade
    delle normali, quindi il criterio iniziale conta meno del previsto.
    Copertura test: `tests/test_bl_gui_wiring.py` (checkbox
    off-by-default, propagazione a `get_bl_params()`),
    `tests/test_dual_poly_options.py` (propagazione end-to-end, mockata,
    fino a `local_termination` nel motore). Nessun default cambiato.

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
- **Verified (2026-09-09):** the main GUI meshing path does not go through
  `MeshEngine.run` — it uses `RetryRunner`/`cartesianMesh` directly. A grep
  in `src/polyfoammesh/gui/` for `MeshEngine().run(`, `QuickMesh().run(`
  and `engine.run(` returns zero matches: `_on_quick_mesh` calls only
  `engine.auto_select(...)` (algorithm choice, no escalation) and
  `qm._auto_bl_params(...)` (BL parameter helper, no meshing); the actual
  meshing goes through `self._params.run_meshing` → `self._on_run_meshing`
  → `RetryRunner` (`self._runner = RetryRunner(self._of_config)`). The GUI
  therefore never produces an `algorithm_substituted` from `MeshEngine`,
  so there is no MeshEngine-driven substitution to surface in the GUI log.
  The substitution is recorded in the artefacts that *do* flow through
  `MeshEngine` (quick mesh one-click, full-auto one-click, A/B
  verification — all headless). The 5 substitution/fallback paths the
  GUI *can* trigger (BL off, detail reduced, Gmsh algorithm coarser,
  parallel→serial, BL disabled) are already wired through
  `_log_substitution` and covered by `tests/test_gui_substitution_notice.py`
  (4 tests, all green). If a future change makes the GUI call
  `MeshEngine.run` (so that an `algorithm_substituted` becomes possible
  in the GUI), a wiring + test mirroring the existing pattern is the
  minimal addition.
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
    load. `commercial/mosaic.py` likewise only runs whole-mesh `polyDualMesh 90` — its "Mosaic" name is historical and its docstrings now say so.
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
- **Sizing estimate rebench (2026-09-09)**: measured the predicted
  vs actual cell count for a cylinder GMSH build at the production
  pipeline (medium fineness, `tools/sizing_estimate_rebench.py`).
  Actual = 52,459 cells. Predicted by
  `estimate_cell_count_geometric(meshes, volume, core_cell,
  patch_sizes)` across a sweep of `max_cell_size` (the slider):
  - max_cell = 0.10: predicted 7,251 (ratio 0.14x)
  - max_cell = 0.05: predicted 58,011 (ratio **1.11x**) ← near budget
  - max_cell = 0.02: predicted 906,431 (ratio 17.28x)
  - max_cell = 0.01: predicted 7,251,455 (ratio 138.23x)
  The "blind" formula (`estimate_cell_count`) gives 58,011 (ratio
  1.11x). The geometric formula is accurate near the production
  target (medium, max_cell=0.05) but diverges massively at finer
  slider values — the slider value is indeed a budget, not a
  guarantee, and the formula's two-zone model (bulk + near-wall
  shell) underestimates the inflation of the boundary layer on
  small core cells. The "omitted on non-watertight" path is enforced
  upstream of the formula (compute_volume in main_window); the
  formula's own fallbacks (volume=0 → floor (100,100,100);
  patch_sizes=None → blind estimate, NOT omitted) are documented.
  No default changed; this is a measurement-only entry.
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

- The frozen EXE is rebuilt on demand (`pyinstaller --clean --noconfirm PolyFoamMesh.spec`). The `dist/` artifact is not refreshed automatically
  and may lag the source tree.
- The API server module (`polyfoammesh.api.server`) imports cleanly with
  the installed FastAPI; it remains a CI/CD surface, not part of the GUI.

## Performance — poly conversion / smoothing speed (2026-09-13)

- **Real production run observed the problem before any synthetic
  benchmark**: a GUI session on a real CAD part (`Parte4.stp`, 10.3M tet
  cells after GMSH) logged the barycentric-dual conversion + quality
  smoothing taking **~34 minutes total, of which ~27.5 minutes (80%) was
  the smoothing pass alone**, for a marginal defect improvement (738 vs
  750 total, ~1.6%).
- **Root cause found by profiling (`cProfile`) the real valve case, not
  guessing**: `poly_smoother._laplacian_relax` (one Laplacian relaxation
  step, called once per smoothing candidate) had an explicit
  `for v in range(len(points))` Python loop over every vertex — on the
  152K-point valve this function alone was **~50% of total conversion
  time** (81.7s of 165.5s); on the 11M-point production mesh from the
  real session, the same per-vertex Python loop explains the observed
  185–206s per smoothing step.
- **Fix**: rewrote `_laplacian_relax` as one vectorised scatter-add
  (`np.add.at` over a flattened (vertex, neighbour) edge list built once
  per smoothing run via the new `_flatten_adjacency`, not per step) —
  same weighted/unweighted Laplacian formula, verified against an
  independent reference loop written fresh in
  `tests/test_laplacian_relax_matches_independent_reference_loop`
  (`rtol=1e-12`). No behaviour change: the valve conversion still
  produces the identical 526 residual defects, at less than half the
  total conversion time.
- **Measured (valve1, same `cProfile` run before/after, `total` stage
  time)**: **165.5s → 100.3s (-39%)**. `_laplacian_relax` no longer
  appears in the top-30 profiled functions at all. The remaining
  smoothing cost is now dominated by the per-candidate quality
  re-evaluation (`_face_geometry`/`_detect_defects`/`_face_extent`,
  called once per accepted/rejected smoothing step over the *whole*
  mesh) — a legitimate further optimisation (incremental/local
  re-evaluation instead of whole-mesh), not yet attempted.
- Not yet re-measured on the original 11M-point production case (too
  slow to iterate on for every change); the valve number is the honest
  basis for the -39% claim. Expect a larger relative win on bigger
  meshes since the fixed cost scaled with vertex count, not mesh
  complexity.

### Boundary layer "silent stall" — real bug found and fixed (not just slow)

- **Root cause of a real production incident**: a GUI session on the
  10.3M-tet CAD part (`Parte4.stp`) produced zero log output for 22+
  minutes after the poly conversion finished, until the app's own
  liveness watchdog fired ("Task 'polydual' stalled (no liveness for
  1362s)") and force-cancelled the task as a zombie — the user then had
  to force-quit the app. This looked like (and functioned as) a hang,
  even though it may not have been an infinite loop.
- **Cause, found by reading (not guessing)**: `PolyBoundaryLayerEngine.
  _build_precompute` runs several genuinely expensive, pure-Python,
  per-wall-vertex loops (normals, feature-angle fade, interior-support
  distance, boundary edge map) before the BL engine's own progress
  logging ("building prism side faces X%...") ever starts. Its own
  `_gil_yield` helper — whose docstring already admits *"the minutes the
  BL step takes"* — only does `time.sleep(0)`, with **no log output at
  all** during this phase. On a mesh with hundreds of thousands of wall
  vertices this phase can legitimately run for many minutes with zero
  visible sign of life, indistinguishable from a genuine deadlock to
  both the user and the app's own watchdog.
- **Fix (this pass)**: added periodic (~20 steps) progress logging to
  all four precompute loops in `_build_precompute` (`bl_poly.py`),
  matching the existing "[bl] ... X%..." style used elsewhere in this
  file. Purely additive — no computed value changed; verified via a
  real end-to-end run on the valve (`DualPolyWorker.run()`, GUI code
  path, `concaveClosure=True`) producing the byte-identical result
  measured before (75,200 prism cells, 340 wrong-oriented faces,
  `Failed 4 mesh checks`) and the fast suite unchanged (1161 passed).
  This does not make the phase faster — it makes a long-but-alive phase
  distinguishable from a hang, so the watchdog stops false-firing and
  the user sees real progress instead of an apparently frozen app.
- **Bigger finding while profiling this (real cProfile run, valve
  case, `PolyBoundaryLayerEngine.run()` with
  `local_termination="decoupled_vertex"`)**: `_build_precompute` itself
  is *not* the single largest cost — `_solve_global_windings` (the
  exact BFS-based face-orientation solver, `bl_poly.py`) is, at 153.9s
  tottime / 195.5s cumtime over 5 build attempts (~31–39s each),
  slightly ahead of `_build_precompute`'s 35.5s/106.4s. It is
  pure-Python (`defaultdict(list)` graph construction + BFS), which
  explains the cost, but it is also the function whose docstring
  records a documented history of catastrophic correctness bugs in a
  previous (greedy, non-exact) implementation — see the "previous
  repair was a per-cell greedy flip" note in its own docstring. **Not
  touched in this pass**: optimising it safely needs the same
  discipline as its original validation (checkMesh on multiple real
  geometries, not just the fast unit suite) and more time than this
  session had; flagged here as the next, larger, real lever rather than
  attempted under time pressure.

- **`_solve_global_windings` graph construction vectorized (2026-09-13)**,
  addressing the lever flagged above — but WITHOUT touching the
  correctness-critical part. The function has two phases: (1) build the
  face-adjacency parity graph from the ragged face list (pure data
  restructuring: which two face-sides share which edge, with what
  parity), and (2) a BFS over that graph to solve for a consistent
  winding per connected component (the part with the documented history
  of catastrophic bugs in a prior *different, greedy* algorithm). Only
  phase 1 was rewritten, as `_build_face_parity_graph` — from two nested
  Python loops (one dict-append per face-edge-side) to a numpy
  flatten + lexsort + group-by-run construction. Phase 2 (the BFS,
  `deque`-based, one component at a time) is byte-for-byte unchanged.

  Correctness: verified the new function produces an **exactly
  identical** graph (same face pairs, same parities, same non-manifold
  edge count) to an independently-written reference loop, on (a) a
  hand-built synthetic mesh with mixed face degrees (tri/quad/pentagon)
  and a deliberate non-manifold edge, and (b) the real recorded valve
  dual fixture (1,240,048 faces, 6,193,947 face-vertex entries) — not
  just spot-checked, the full graph compared key-for-key. See
  `notes/global_windings_speed_reasoning.md` and the two new tests in
  `tests/test_bl_poly.py` (`test_face_parity_graph_matches_reference_on_synthetic_ragged_mesh`,
  `test_global_windings_vectorized_graph_matches_reference_on_valve_dual`).

  Speed: measured on the valve (`local_termination="decoupled_vertex"`,
  5 build attempts, real cProfile run) — `_solve_global_windings`
  cumtime **195.5s → 181.3s (-7.3%)**. Honest disclosure: the BEFORE
  number is from the original profiling session (documented above); an
  attempt to re-measure BEFORE on the same run in this session was
  aborted because the host was unusually loaded (a live rerun of the
  unmodified code made too little CPU progress over 45+ minutes to be
  practical) — the comparison is same-methodology, same-fixture,
  cross-session rather than same-session back-to-back. The gain is real
  but modest: most of the function's cost lives in the BFS itself
  (untouched, and not vectorizable — it's an inherently sequential
  graph traversal with early conflict detection) and in downstream
  per-cell volume/closure checks, not in the graph construction that was
  rewritten. Fast suite green (1163 passed, 2 skipped, 1 xfailed), ruff
  clean. Not a big win, but a safe, fully-verified one with zero
  behavioural change to the correctness-critical solve.

- **`_build_precompute` normals loop — vectorized, then REVERTED (negative
  result, 2026-09-13)**. Same session, next candidate lever: the
  per-vertex-normals loop called `_newell` once per boundary face
  (~40k calls per build on the valve) in a Python `for` loop. Batched all
  boundary faces into one vectorized Newell + scatter-add pass
  (`_batched_wall_normals`), sidestepping `_newell`'s own documented
  per-call overhead (its docstring already says numpy is slower than pure
  Python for a single small polygon — this was a different thing, doing
  all faces in ONE numpy pass rather than optimizing one call). Verified
  exactly equivalent to an independent per-face reference loop, on a
  synthetic mixed-degree mesh and the real valve fixture's boundary faces
  (`rtol=1e-9`). Fast suite green (1165 passed).

  Measured on the valve, same profiling script: total run time **434.6s,
  UP from 418.5s (+3.9%)**; `_build_precompute` cumtime **116.5s, up from
  111.5s (+4.5%)**. The eliminated loop was not actually the dominant
  cost — most `_newell` calls (and cost) come from the SEPARATE
  angle-fade loop (per wall vertex, over its incident faces), which this
  change did not touch, so removing ~40k of ~600k `_newell` calls bought
  little, and the new flatten/lexsort-free bookkeeping (`np.fromiter`,
  `np.add.at`) added enough overhead to be a net wash or slight
  regression within measurement noise on this run.  **Reverted** — kept
  the code at its previous (already-committed) state rather than carry
  correctness-verified-but-unhelpful complexity. This is exactly the
  "measure, don't assume" discipline this project runs on: a predicted
  win that didn't pan out, written down honestly instead of merged
  anyway. Any future attempt at this function should target the
  angle-fade loop's `_newell` calls instead, not the boundary-normals
  loop.
- **Mesh I/O (`foam_mesh_io.read_faces`/`read_points`) investigated,
  no worthwhile win found** — an honest negative result, not skipped.
  Both readers have comments admitting multi-minute cost on 12M-point
  meshes, and the converter's own output (verified on a real 1M-face
  `constant/polyMesh/faces` file) is written in the slow ASCII
  `faceList` path, not the vectorisable binary/compact one (`gmshToFoam`
  is configured to write binary via `case_setup.py`, but our own
  `write_faces`/`write_polymesh` always write ASCII, so anything that
  re-reads our own converter's output — the BL engine, the viewer —
  hits this path). Tried replacing the per-line Python parse with one
  bulk regex pass over the whole data block
  (`re.finditer(r"\(([^()]*)\)", block)`): verified byte-for-byte
  identical output against the original parser on that same real file,
  but only **1.782s → 1.657s (-7%)** on 1,056,636 faces — the actual
  cost is the per-face `int()`/`split()` conversion, which a regex
  front-end doesn't remove. Reverted rather than keep a small win that
  also drops the periodic `time.sleep(0)` GIL-yield (the same class of
  "silent-freeze" risk just fixed in the BL precompute above) for a
  gain too small to justify the added complexity and re-introduced
  risk. A real win here would need a fundamentally different
  representation (e.g. switching the project's own writer to the
  compact/binary format end-to-end) — a bigger, cross-cutting change
  than a mesh-I/O micro-optimisation, out of scope for this pass.
- **Cartesian cfMesh and FEM Tetra (GMSH direct) paths checked for
  Python-side overhead, none found worth chasing**: both meshers are
  thin Python orchestration around an external binary
  (`cartesianMesh`/`gmsh` via WSL2 or subprocess) — the actual meshing
  time is spent inside that external process, which this codebase does
  not control. The shared preprocessing step both paths go through
  first, geometry tessellation (`geometry.py::tessellate_patches`), has
  no explicit per-vertex Python loop (unlike `bl_poly.py`/
  `poly_smoother.py` before this pass) — it already delegates to
  OCC/cadquery's own tessellation. No further mesher-side speedup
  identified beyond the poly-conversion/BL work above.

## Repository Hygiene (Lane F, 2026-09-11)

- **`tests/fixtures/valve_dual.npz` (27 MB, tracked) stays committed —
  regeneration measured, not assumed.** `tools/poly_fixture_builder.py`
  rebuilds it from the real valve tet backup (824,661 tetrahedra,
  1,687,079 faces): the barycentric dual conversion alone (round 0,
  before any smoothing or checkMesh) took >120 s in a real run started
  for this check (dual-cell pass at 9.2 s, quality pass at 3.4 s, then
  multiple smoothing iterations still running when stopped). Generating
  this fixture on every test invocation would make the "fast suite"
  not fast. Keeping the pre-built npz committed is the correct call;
  no change made.
- **No other tracked file in the 5-50 MB range**: `find tests/ tools/
  -size +5M -size -50M -type f` returns only `valve_dual.npz` and
  `tools/innosetup/innosetup-6.7.3.exe` — the latter is **untracked**
  (`git ls-files` confirms; removed from tracking and gitignored in
  commit `2c87293`, "rimossi dal tracking i binari grandi"), so it does
  not affect clone size. Nothing to change.
- **No orphaned/broken scripts found in `tools/`** (34 files). A quick
  `ast.parse` sweep flagged `tools/poly_fixture_builder.py` for a
  leading UTF-8 BOM (`U+FEFF`) — a false positive of the probe method
  (`ast.parse` on text decoded as plain `utf-8` doesn't strip the BOM
  the way Python's own `utf-8-sig`-aware source loader does); running
  the script directly (`python tools/poly_fixture_builder.py
  --regression-fixture`) works and was confirmed doing real work in
  this check. The convention already documented in
  `.pre-commit-config.yaml` ("tools/ holds tracked scratch") means
  one-off benchmark scripts from finished investigations are kept
  intentionally, not deleted once their result lands here. No script
  was found to be genuinely broken or duplicated; no cleanup performed.

## Non-star-shaped ("face pyramid") cell repair via merge (2026-09-13)

Separate investigation from the speed work above, triggered by a
literature check on the H4/decoupled_vertex boundary-layer closure
(FASE 9): none of the individual techniques used there turned out to be
novel (barycentric dual-mesh conversion, per-vertex local BL
termination, and BFS-based consistent face orientation are all
established techniques — see the literature citations in the session
transcript), but the specific combination for a no-WSL Python pipeline
wasn't found published anywhere searched.

**Root cause measured**: on the production-collapsed valve dual
(152,086 cells, 42,130 wall faces), 348 cells (0.23%) fail checkMesh's
"face pyramids" test (a cell must be star-shaped w.r.t. the exact
centroid OpenFOAM's own `primitiveMeshTools::makeCellCentresAndVols`
computes — not a formula we can change, it IS checkMesh's ground
truth). These 348 cells are the root cause of the H4 boundary layer's
own exclusion cascade (the BL validation gate is "don't add MORE
pyramid violations than the input already had" — `bl_poly.py` line
~1922 — so fewer input violations tightens that budget, an important
and initially counter-intuitive mechanism: reducing violations doesn't
automatically reduce BL exclusions one-for-one).

**Rejected approach**: Lee (2015)'s literature method for concave dual
cells ("cut-along-concave-edge") assumes the concavity is a dihedral
fold between TWO faces meeting at a known model edge. Measured: 345 of
358 concave cells here (96%) fail on exactly ONE face with no natural
"concave edge" partner — a structurally different defect (a collapsed,
irregular polygon's plane not containing the cell centroid), not the
one that method fixes. Splitting the one offending face doesn't help
either (a planar face split into pieces keeps the same orientation
problem against the centroid).

**Working approach**: merge the offending cell with an interior
neighbour (removing the shared face), conceptually the same operation
as OpenFOAM's own cell agglomeration. Implemented in
`core/poly_cell_merge.py` (`merge_cell_groups`, `find_merge_candidates`,
`repair_concave_cells_if_safe`), see
`notes/poly_cell_merge_reasoning.md`. Measured on the valve: 302/348
(86.8%) concave cells fixed by merging with the first interior
neighbour whose virtual merge passes the pyramid test.

**A real bug was found and fixed during this work**: the merge
renumbers cells, which can silently break OpenFOAM's "upper triangular
face order" requirement (owner non-decreasing across the whole internal
face list) even though every individual face still has owner <
neighbour — checkMesh reported it separately as "Faces not in upper
triangular order" on a real 227k-cell mesh. Fixed by sorting internal
faces by `(owner, neighbour)` after renumbering; covered by
`test_poly_cell_merge.py::_assert_upper_triangular_order`, invoked by
every mesh-validity test in that file.

**Timing matters, measured**: merging BEFORE building the boundary
layer works but has a real cost — on the valve, real checkMesh: wrong-
oriented faces 340 → 61 (-82%), but max cell aspect ratio nearly
TRIPLED (6,555 → 19,665) and severely non-orthogonal faces rose
859 → 1,320. Merging AFTER the boundary layer is built (so the merge
selection sees the actual prism geometry it sits next to) avoids that
regression entirely on the valve: 340 → 58 wrong-oriented (-83%),
aspect ratio UNCHANGED (6,555, identical), non-orthogonality slightly
BETTER (839 severe vs 859, 4 errors vs 10). Two follow-up hypotheses to
explain/fix the pre-BL regression were tested and both falsified:
compactness-based neighbour selection (no different result — usually
only one viable candidate exists per cell, not a real choice) and
volume-ratio-based selection (merged-pair volume ratio, median 2.3x,
is not an outlier vs the mesh-wide median of 1.6x). The actual
mechanism is the interaction between the merged cell and the already-
built anisotropic BL prisms sitting on it, not the neighbour choice.

**Not a universal win — measured on slot1 too**: the same post-BL
merge technique, applied to the slot1 reference geometry (already
`Mesh OK` at baseline), introduced 3 new highly-skewed faces (max
skewness 2.93 → 4.55) that checkMesh flags as a failure the baseline
did not have — even though the in-process replica showed a real
pyramid-violation improvement (15 → 1) and exact volume conservation.
Attempted fix: re-running this project's own already-validated
`poly_smoother.smooth_dual_mesh` (keep-best Laplacian relax) as a post-
merge quality pass — did NOT help, because its in-process skew detector
reported 0 both before and after while real checkMesh still found 3
skewed faces: the in-process skew metric does not match checkMesh's own
skewness formula closely enough to see this specific regression.

**Final design — a guardian, not a blanket fix**:
`repair_concave_cells_if_safe` applies the merge only if it does not
increase the in-process pyramid-violation count and conserves volume
(both metrics ARE exact matches to checkMesh, unlike skew) — otherwise
it returns the mesh completely unchanged. This makes the repair safe by
construction for the metrics it can check exactly, but it is explicitly
NOT a guarantee against every possible regression (the slot1 skewness
case would still pass this in-process gate, since the gate cannot see
it) — the module docstring and this section both say so. **Not wired
into the GUI or any default path.** Any real use of this repair on a
specific production case should be confirmed with a real `checkMesh`
run before trusting it, exactly like every other opt-in feature in this
project. Tests: `tests/test_poly_cell_merge.py` (7 tests, including one
on the real valve fixture and a healthy-geometry no-op check). Fast
suite green throughout.

## Skewness formula bug found and fixed (2026-09-13)

Follow-up from the cell-merge investigation above, motivated by a
directed literature/source search after the in-process skew detector
missed a real regression on slot1. Fetched OpenFOAM's actual
`primitiveMeshTools::faceSkewness` C++ source
(cpp.openfoam.org/v11/primitiveMeshTools_8C_source.html): the skewness
normalisation distance is `max(0.2*|d|, max-per-vertex-projection)`.
Our `_detect_defects` (tet_poly_dual.py) and `_continuous_metrics`
(poly_smoother.py) both computed it as a **sum** instead —
`fd = 0.2*dn + _face_extent(...)` — which silently inflates the
denominator and under-reports skewness everywhere, the root cause of
the earlier finding that our detector said "0 skewed faces" on a mesh
real checkMesh flagged with 3.

Fixed both call sites to use `np.maximum(...)`. Verified against 5 real
comparisons (see `notes/skewness_formula_fix_reasoning.md`): exact match
(4-5 decimal places) on two independent post-boundary-layer meshes
(valve baseline and merge-repaired, both 22.16451 vs checkMesh's
22.1645); much closer but not exact on two others (slot1 post-BL-repair,
and the `valve_dual.npz` fixture which stores points as float32 — a
precision source already documented elsewhere in this file). Honest
disclosure: the two non-exact cases are NOT fully explained (leading
hypothesis is float32 precision / a stale regenerated file, not
reconfirmed) — flagged rather than hand-waved. Updated the pre-existing
`test_detect_defects_matches_recorded_checkmesh` regression test (which
had explicitly pinned the OLD wrong count with a comment admitting the
gap) to the new, closer count. Fast suite green (1170 passed).

Practical consequence: this materially improves confidence in the
in-process quality gates used elsewhere in this project (including
`poly_cell_merge.repair_concave_cells_if_safe`), but does not eliminate
the need to confirm with a real `checkMesh` run before trusting a
production case — the residual gap on two of five comparisons is proof
of that on its own.

## Smoothing silently skipped on healthy meshes — found via a snappyHexMesh comparison (2026-09-14)

Triggered by a direct, measured comparison against `snappyHexMesh` on a
plain cylinder at a comparable cell count (10,649 our poly cells vs
11,880 snappy cells): real checkMesh showed snappy winning on every
quality metric (aspect ratio 3.02 vs 4.43, max non-orthogonality 29.1°
vs 51.3°, max skewness 0.86 vs 1.53).

**Root cause**: `TetPolyDualConverter`/`hex_poly_dual.py` default
`smooth=True` (the quality-driven Laplacian smoothing pass is meant to
always run), but the call site gated it behind `best_defects > 0` —
which only counts THRESHOLD-crossing cells (pyramid <= 0, non-ortho
> 70°, skew > 4). Our cylinder had 0/0/0 by that count, so smoothing
was silently skipped despite real headroom below those thresholds.

**Fix**: removed the `best_defects > 0` gate in both files — the
smoother is keep-best by construction, so this can only help or leave
the mesh unchanged. Measured on the cylinder (real checkMesh): max
non-orthogonality 51.3° → 41.7° (-19%), max skewness 1.53 → 1.28 (-16%).
Aspect ratio essentially unchanged (4.43 → 4.46) — snappyHexMesh still
wins on all three metrics, this closes PART of the gap, not all of it.

**A second, real bug found while fixing the first**: removing the gate
exposed that the tie-breaking acceptance never tracked face planarity
as a quality axis — on tiny/degenerate synthetic test meshes (a single
tet, a 12-tet perturbed cube) with zero real non-ortho/skew defects to
improve, an accepted "tied" step (using `<=`, "not worse") measurably
distorted exactly-planar faces (deviation 0.0021 vs a 1.7e-6 tolerance)
chasing a microscopic, meaningless objective difference. Fixed with two
changes: (1) strict `<` instead of `<=` for tie-breaking (a real
improvement required, not merely "not worse" — alone insufficient,
since the accepted step really was marginally "better" by an objective
blind to planarity), and (2) a hard planarity guard in
`poly_smoother.smooth_dual_mesh`, same pattern as the existing
volume-positivity/volume-ratio guards: track the worst per-face
planarity deviation and reject any candidate that makes it meaningfully
worse than the current best. One pre-existing test's pinned exact face
count (122 → 162) needed updating as a consequence — not a regression,
the properties that matter (triangulation truly eliminates non-planarity)
still hold exactly; see `notes/smoothing_gate_fix_reasoning.md` for the
full account. Fast suite green (1170 passed).

## Test Matrix

- Fast (no WSL) suite: green.
- Real WSL/OpenFOAM smoke (GMSH tet -> gmshToFoam -> dual poly -> checkMesh):
  `Mesh OK` on the reference cylinder case.
- Full WSL regression runs are not part of the fast suite; they require
  OpenFOAM v2512 in WSL2 and are executed manually or via the harnesses in
  `tools/`/`tests/bench_*`.
