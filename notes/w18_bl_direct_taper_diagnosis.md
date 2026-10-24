# W18.1 — direct-BL 5L + taper loop: diagnosi (zero-cost + misure W18)

Date: 2026-10-24. Contesto: W18 auto implementato e committato
(`75dba0d`, PARTIAL per R1 box). Due task ordinati dall'utente:
(1) misurare direct-BL 5 strati su R1; (2) taper loop automatico
grow→misura→assottiglia→rimisura invece di rifiuto totale.

## 1. Stato misurato (fonti)

- W13 direct-BL 1L su R1 (tet GMSH 380K): Mesh OK, 100% coverage, h1
  0.999, 90.5 s (`notes/w13_bl_on_tets_reasoning.md`). 5L MAI misurato
  su nessun path — buco esatto da chiudere.
- R1 dual+BL 5L (fTet 18/05, 73K tet): dual ok (58 pyr) → BL ok
  (58965 prismi, coverage 100% da `boundary_layer_report`,
  h1 0.509x) → checkMesh Failed 3 (160 bad pyr, NOmax 141.1, skew 40.5)
  (`C:\polyfoammesh_r1b\r1_result.json`, tool `r1_rerun_parte4.py`).
- R1 GMSH course (STEP 18/05, 1 solido): tet ok → dual ok (761K poli,
  787 dif) → BL RIFIUTATO (all heights fail) → dual-only Failed 3
  (523 bad pyr, NO 105.3) (workdir `C:\polyfoammesh_r1gmsh`).
- Motore BL oggi: scala globale (attempt loop su scale decrescenti,
  `bl_poly.py` docstring righe 49-75) + opt-in locali (consume,
  drop-first, decoupled retry, budget). Su rifiuto totale: mesh
  invariata + errore. Niente assottigliamento per-patch automatico.
- GUI: BL logga parametri + warning (`bl_report`, h1 fidelity);
  nessun controllo taper (non esiste il concetto). Coerenza = log/report
  devono mostrare gli strati effettivi per patch quando il taper atterra.

## 2. Perché serve (non "sarebbe bello")

- Task 1 decide R1: se direct-5L su fTet (o GMSH) dà Mesh OK/Failed-1
  con coverage ≥98%, l'auto ha una rotta R1 misurata (policy: tet
  diretto quando dual invalido? da definire DAI numeri, non prima).
  Se fallisce: R1 resta NOT MET con 5 negativi invece di 4.
- Task 2 serve OVUNQUE, non solo R1: il finale W17 (3/15 per `bl_produced`)
  e Lane A (BL failed su mini_valve entrambi i path) mostrano che il
  rifiuto totale è il collo sistematico. Il taper lo converte in
  degradazione graziosa misurata (N strati dove c'è spazio, 0 dove non
  c'è, coverage risultante nel report).

## 3. Design

- Task 1: riuso `build_bl_on_tets(case, 5, 0.00126, 1.2)` su tet fTet
  18/05 condizionati (`run_volume`, NON raw tetrahedralize — il raw
  salta pinch-repair/snap/drop e produce boundary non-manifold che dual
  e BL rifiutano; causa trovata, non ipotesi). Script
  `r1_rerun_parte4.py --direct-bl`. Se fallisce: follow-up con kwargs
  production (minBaseEdge 0.9, terminateByBudget) — una variabile alla
  volta.
- Task 2: nel `PolyBoundaryLayerEngine.run`, quando la validazione
  fallisce a scala S su un sottoinsieme di patch/facce: invece di
  scendere SOLO di scala globale, marca le patch fallite e riprova con
  `n_layers` ridotto SOLO lì (taper), tenendo N pieno dove passa.
  Provenance: `bl_report` deve contenere `layers_per_patch` +
  `tapered_patches` (test lo assertano). Default ON solo se non peggiora:
  keep-best vs baseline (stessi input, confronto coverage*quality).
  Niente nuovi knob GUI in prima battuta: log + report (coerenza senza
  controlli).
- GUI coerenza: mostrare `layers_per_patch`/taper nel pannello BL
  (read-only, come Cell Sizes) + log; nessun selettore manuale (W18).

## 4. Stima/rischi

- Task 1: solo run (~10-20 min su STL 18/05) + nota numeri. Rischio:
  boundary non-manifold anche coi tet condizionati → STOP onesto.
- Task 2: ~150 righe engine + test con mesh sintetiche (thin-gap
  sintetico? riusa fixtures) + 1 test E2E su caso reale piccolo.
  Rischio principale: cambiare l'esito su casi che oggi passano
  (keep-best test su corpus piccolo obbligatorio prima/dopo).
