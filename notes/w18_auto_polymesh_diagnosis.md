# W18 — Polymesh auto + cfMesh visibile: diagnosi (zero-cost)

Date: 2026-10-24. Metodo: letti registry, tet_source, params_panel (CTA +
Engine combo), test_mesher_registry, GOAL (W13/W10.2/W17/MILESTONE),
REGRESSION_CASES, Lane A JSON (10/06), finale poly 23/10, finale ftetwild
23/10. Nessun run, nessun edit.

## 1. Stato attuale (fatti, con file)

- `mesher_registry.py`: 6 key lossless (`gmsh_direct_poly` default,
  `ftetwild_poly`/`auto_tet`/`bl_on_tets` experimental,
  `gmsh_direct`/`cfmesh` advanced_only). `DEFAULT_MESHER_KEY =
  "gmsh_direct_poly"` (legacy misurato). `ENGINE_KEYS` = tutti i 6.
- `tet_source.py`: `choose_tet_source` sceglie SOLO per suffisso
  (STEP→fTetWild, STL→fTetWild, no-pytetwild→GMSH) + `run_tet_source`
  con fallback singolo. `auto` = source policy opt-in, NON engine auto.
- UI (`params_panel.py:502-576,695-713`): una CTA visibile `Polymesh`
  (key `gmsh_direct_poly`), 3 bottoni nascosti, `Engine` combo in Advanced
  con tutti gli `ENGINE_KEYS`. `cfmesh` invisibile (advanced-only).
- `tests/test_mesher_registry.py`: pinna 1 CTA + combo con tutti i key —
  va riscritto (W18 lo richiede esplicitamente: due CTA, no combo).
- Dispatch (`main_window.py:2200-2260,3657-3665`): `auto_tet` → plan →
  ricalcolato come `ftetwild_poly`/`gmsh_direct_poly`; fallback una volta.
- GOAL W13: R1 A/B stesso tet (380.011): direct-BL 1L Mesh OK
  (100%, h1 0.999, 90.5 s) vs dual-BL Failed 3 (h1 0.474, 221.4 s).
  Verdetto: opt-in, NON promosso (mancano thin_gap/venturi/tubo + 5L).
- MILESTONE_REVIEW: PARTIAL (R1 NOT RUN — verità 22.6 MB persa;
  STEP 30/09 trovato = 89 patch vs 7 originali, run fTetWild ucciso a
  ~50 min senza output). R1 va rimisurato qui.

## 2. Perché non è automatico

`choose_tet_source` ignora: watertight, thin features, patch count,
dimensione, refinement zones, qualità misurata per caso. Assegna
fTetWild a TUTTI gli STEP — ma Lane A mostra GMSH→dual PASS su 9/10
(corpus STL/STEP misti) e il finale ftetwild 23/10 mostra quality-FAIL
quasi ovunque (dettaglio sotto). Il routing per suffisso non ha base
di misura; la base esiste solo sparsa nelle note.

## 3. Tabella vincitori misurati (solo numeri citati, resto = TBM)

Legenda: Q = quality gate dual (neg 0, NO<70, skew<4); BLp = bl_produced.

| caso | vincitore misurato (tet→dual) | numeri | fonte |
|---|---|---|---|
| cylinder | gmsh_direct_poly | PASS Lane A (6822/38.13/1.29) + PASS finale W17 | Lane A JSON, 20261023T172959 |
| pipe | gmsh_direct_poly | PASS W17 (10657/60.81/2.76) | 20261023T172959 |
| cube_duct | gmsh (Q ok, BL no) | Lane A PASS; W17 FAIL solo BLp (4218/51.87/3.93) | Lane A, W17 |
| groove1 | gmsh (Q ok, BL no) | Lane A PASS; W17 FAIL solo BLp | Lane A, W17 |
| slot1 | gmsh (Q ok, BL no) | Lane A PASS; W17 FAIL solo BLp | Lane A, W17 |
| multi_inlet | gmsh (Q ok, BL no) | Lane A PASS (961/49.99); W17 identico, FAIL solo BLp | Lane A, W17 |
| thin_gap | gmsh (Q ok, BL no) | Lane A PASS (89379/69.62); W17 (27205/66.25) FAIL solo BLp | Lane A, W17 |
| concave_bracket | gmsh (Q ok, BL no) | Lane A PASS; W17 (23756/62.66) FAIL solo BLp | Lane A, W17 |
| venturi | gmsh (Q ok coarse, BL no) | Lane A PASS (816/43.28); W17 identico FAIL solo BLp. N.B.: production dual-only = muro WALL-01 (NO 97.2) — risoluzione diversa, non contraddizione | Lane A, W17, WALL-01 |
| mini_valve | ftetwild_poly | GMSH tet→dual Failed 1 (NO 78-88) vs fTet tet→dual Mesh OK ×2 (67.80/3.92, 64.68/3.93) | wall_03 |
| dirty_cad | ftetwild_poly (solo tet) | GMSH non carica; fTet tet Mesh OK (3515); dual rifiuta open (corretto); snappy FATAL | wall_03, Lane A |
| non_watertight | pareggio (rifiuto corretto) | PASS=W17 (rejected) | W17 |
| box_obstacle | TBM (nessun verde) | W17 gmsh NO 96.98 FAIL; ftetwild 23/10 FAIL | W17, ftetwild 23/10 |
| external_aero | TBM (nessun verde) | W17 skew 6.09 FAIL; ftetwild 23/10 FAIL | W17, ftetwild 23/10 |
| pipe_constriction | gmsh (Q ok, BL no) | W17 (58155/66.75/3.07) FAIL solo BLp | W17 |
| R1 (173334) | TBM per auto; parziali: dual-only Failed 1 (1 pyr, 79.4K), dual+5BL Failed 3 (39 pyr), direct-BL 1L Mesh OK (100%, h1 0.999) | REGRESSION_CASES, GOAL W13 | — |

Fatto strutturale: il dual è stabile quasi ovunque (Q vera FAIL solo su
box_obstacle/external_aero/dirty_cad); il collo sistematico è BLp
(thin features + validazione — debito WALL-02 noto). L'auto NON deve
promettere BL dove è murato: policy onesta = tet-source auto, BL come oggi.

## 4. Design

- **Preflight economico** (riuso `preflight_tet_source` + estensione):
  suffix, size_bytes, pytetwild, refinement zones (esistono) +
  NUOVO: watertight (tetrahedralize? no — surface check economico:
  `boundary_faces` su STL? per STEP serve surface GMSH… costo!),
  patch count (dalla surface/NPZ), bbox dims. Watertight su STEP costa
  una surface GMSH — dichiarare quando si salta (fallback: suffix).
- **Policy deterministica** `choose_polymesh_engine(preflight)` →
  (primary, fallback1, fallback2, reason), tabella-driven:
  dirty/non-watertight → ftetwild (GMSH non carica); mini_valve-like?
  NO — niente euristica per-nome-geometria ( Vietato overfittare sui
  nomi). Regole per FEATURE: open/non-manifold → fTetWild;
  pytetwild assente → GMSH; default → GMSH (9/10 Lane A) con fallback
  fTetWild; fTetWild con fallback GMSH solo dove misurato meglio
  (dirty_cad). bl_on_tets MAI in auto (W13: opt-in non promosso).
  Tabella vincitori sopra = la base; TBM restano GMSH-default.
- **Fallback chain reale**: riuso `run_tet_source` (primary→fallback) +
  estensione a 2 fallback; validità = MSH non-vuoto + patch names
  (esistente); checkMesh gate DOPO il dual nel flusso GUI (esiste nel
  worker) → se Failed e fallback disponibile, riprova e logga.
  Keep-best: vince l'esito valido; catena esaurita = errore con catena.
- **Provenienza**: riuso `TetSourcePlan/Result.to_mapping()` + reason
  string nel log/report; cache key = fingerprint esistente + policy
  version (bump a `tet_source` fingerprint? nuovo campo, non breaking).
- **UI**: `Polymesh` CTA → key `auto` (NUOVO key registry, default=True);
  `cfMesh` CTA visibile di nuovo (key `cfmesh`, hex separato, richiede
  WSL/OF — avviso esistente); Engine combo RIMOSSO da main+Advanced;
  bottoni nascosti restano (lossless). Tooltip Polymesh: "sceglie il
  motore dalla geometria". Preset/progetti con key esplicita:
  `normalize_mesher_key` già migra; restore usa key salvata (rispetto
  scelta) — verificare `restore_params` + `get_mesher_type`.
- **CLI/config**: key esplicite invariate; `auto` nuovo default;
  legacy default solo via CLI/config (`--mesher gmsh_direct_poly`).

## 5. Riuso / rifattorizza / elimina + stima / rischi

- Riuso: tet_source (plan/run/provenance/fingerprint), registry
  (aggiungi key, non riscrivere), run_benchmarks/compare (tabella §3.4),
  gate E2E (run_e2e_flow) per misure auto.
- Rifattorizza: `choose_tet_source` → wrapper di `choose_polymesh_engine`
  per source (una policy, non due); `_resolve_auto_mesher` in main_window
  (legge il plan).
- Elimina: Engine combo UI (+ test che lo pinnano — riscrittura
  richiesta, non cancellazione cieca); NIENTE motori/tests cancellati.
- R1 (§3.4 + exit): input = Parte4.step 30/09 (89 patch ≠ 7: dichiarare
  la deriva). Criterio "0 pyr / ≤1 skew / Failed≤1, coverage ≥98%"
  AMBIGUO: skew ≤1 come max-skewness non è mai misurato su R1
  (dual-only 3.31, direct 2.477); va operazionalizzato (difetti skew ≤1?
  max-skew ≤4 come gate standard?). Default operativo proposto:
  pyramids=0 E skew-faces≤1 E Failed≤1 E coverage≥98% — se il criterio
  intendeva altro, il prompt deve dirlo prima della misura.
- Stima: policy+preflight ~150 righe, UI ~80, test ~250 (≥10),
  tabella §3.4 = 8 casi × (auto + 2-3 espliciti) in background,
  R1 full ~30-60 min (fTet ucciso ieri a ~50 min su 86 MB surface).
  Rischi: watertight-preflight costoso su STEP (surface GMSH ~1 min —
  accettabile, è il flusso auto_tet esistente); flake fTetWild (~1/5
  non-manifold su mini_valve — retry già codificato in wall03);
  tentazione combo-di-sicurezza = VIETATA dal prompt.
