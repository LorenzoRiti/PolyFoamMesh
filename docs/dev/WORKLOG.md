# WORKLOG — registro condiviso del lavoro

Convenzione (richiesta utente, 2026-11-16): ogni sessione aggiunge qui
una sezione datata con commits, misure e aperti. Le voci del
`CHANGELOG.md` vengono redatte INSIEME a partire da questo registro
(l'utente revisiona, l'agente scrive). Niente push senza ordine
esplicito; i due file utente (`benchmarks/run_comparison.py`,
`tests/test_run_comparison.py`) non si toccano mai.

---

## 2026-11-17 — Patch management: rename/deselezione/colonna Role + verdetto mesh utente

### Verdetto utente: DIVERGENZA in Baram — diagnosis + A/B in corso (17-11)
- Il caso divergente e' `gmsh_direct_20261117_093620` (Parte4.stp,
  importato in Baram alle 10:53).  Colpevole primario: **22 face
  wrong-oriented + skew 19,17** (flussi non conservativi) — non y+.
- **Sonda 1 (99 s)**: dal backup tets dello stesso caso, duale puro
  niente BL → checkMesh **Failed** (non-ortho 137,9, 37 bad pyramids,
  skew 6,31; dual 104.485 poly, 59 difetti, vol_err 0,25%).  →
  "disattiva il BL" NON basta su questa geometria: il duale da solo
  rompe l'orientamento.
- Evidenza chiave dai log GUI: il duale su 2,7M tets fTetWild lasciava
  5 face capovolte (0,0002%) e le tets GREZZE GMSH erano pulite
  (pyramids OK, skew 1,23, non-ortho 88,7 < 90) → il problema di
  orientamento e' CREATO dal duale su questa topologia (89 facce,
  50+ gap stretti), non dalla taglia.
- **A/B 2 in corso (~25-40 min)**: GMSH tets alla taglia RAW senza
  budget-correction x1,5 (`max_cells_target=0`) + BL diretto sulle
  tets (nLayers=7, h1=0,6745 mm, growth 1,2) saltando il duale —
  e' il ramo direct-BL dell'auto che l'utente aveva annullato.
  Criterio: 0 wrong-oriented + skew < 2 → mesh eseguibile (il gate
  checkMesh resta piu' severo: non-ortho > 70 e' comunque "Failed"
  formale ma < 90 gira con nonOrthoCorrector).
- **A/B 2 — esito (18 min GMSH + 100 s conv + BL 31 min + checkMesh)**:
  GMSH tets alla taglia **RAW senza budget-correction** (`detail=medium`,
  `max_cells_target=0`) → **8.072.045 tets** (~4,5x il run GUI rindenato
  x1,5).  gmshToFoam 100 s.  **BL diretto sulle tets: rifiutato** dopo
  1884 s ("all height scales failed validation; mesh left unchanged")
  con la config semplificata della sonda (niente local termination del
  pannello) — la mesh resta valida.  **checkMesh: Mesh OK** (0 check
  falliti, skew 1,095, 0 bad pyramids, non-ortho 89,7): prima mesh
  **eseguibile** di questa geometria; la ricetta = taglia raw + salta
  il duale.  Caso esportato in `C:/baram/probe_tetbl_raw_parte4_mesh`
  (setup_case placeholder BC).
- Conclusione operativa (da portare in prodotto con A/B): su geometrie
  gap-dense il **budget-correction globale x1,5 danneggia la qualita'
  del tet** (e il duale rompe l'orientamento).  Candidati: policy
  "raw prima, correzione solo se sopra il tetto RAM" e/o conversione
  1:1.  Il BL-on-tets con la config completa del pannello resta da
  misurare (la sonda usava solo la mapping base).
- Sonda: `Temp/opencode/probe_dual_nobl_parte4.py` (esito:
  `C:/cfmesh_cases/probe_dual_nobl_093620`) e
  `probe_tetbl_raw_parte4.py` + `probe_tetbl_resume.py`
  (`C:/cfmesh_cases/probe_tetbl_raw_parte4`).

### Patch management (richiesta utente 17-11) — implementato (in tree, test mirati verdi)
- **Rename che non "non le cambia"**: `_on_patch_item_changed` ora
  SANITIZZA (`;{}"/` e spazi → `_`, doppi underscore collassati) e
  commette; collisione → suffisso numerato (`inlet` → `inlet_2`,
  `viewer.unique_patch_name`); solo nomi senza alcun carattere legale
  revertono.  Anche il rename da click 3D (`_on_face_picked`) sanitizza
  invece di rifiutare con QMessageBox.
- **Freeze sul rename**: `_rename_cad_patch` non chiama più
  `set_suggest_meshes` (ricomputo numpy-heavy sincrono = l'impallamento)
  → `params.invalidate_mesh_caches()` (drop cache, recompute pigro).
  Le mesh nel panel sono gli STESSI oggetti → i nomi restano freschi.
- **Deselezione**: il toggle re-click esisteva già (`_on_mesh_picked`,
  giro 2.5.0); aggiunto **ESC** via `eventFilter` sul viewer (skip se
  editing inline della tabella — probe `state().value` per la
  TypeError di PySide6 sui flag — e i popup/dialog sono finestre
  separate).  Test reale su pannello costruito (offscreen).
- **Colonna Role** (prima "Type"): combo auto/inlet/outlet/symmetry/wall
  precompilata dall'inferenza (`_display_patch_type` per mesh,
  `detect_patch_type` per CAD); scelta utente → `patch_role_changed`
  → override su `params.patch_role_overrides` (storage params come da
  decisione) → consumato da `_user_is_wall_patch` (BL wall selection,
  3 call-site) e `_case_setup_kwargs` (`patch_roles=` di setup_case —
  seam GIÀ pulito: il parametro esisteva, l'override vince sui nomi).
  Gli override seguono i rename (anche `apply_case_patch_rename`) e
  vengono azzerati su Reset.
- TDD: `tests/test_patch_management_fixes.py` 23 test scritti prima
  (RED 17 fallimenti comportamentali verificati) → GREEN.  Attenzioni
  PySide6 emerse: Signal.emit e sender() su istanze `__new__` bare
  alzano RuntimeError (test usano shadow MagicMock + closure), `State
  & State` e `int(State)` alzano TypeError (fallback `.value`).

### Aperti (per il prossimo giro)

---

## 2026-11-16 — fTetWild/DN1000: chiusura loop, poly-gate, refinement quotidiano

### Commit
| SHA | Contenuto |
|---|---|
| `ca272e0` | Circuit breaker anti-loop GMSH (`PartitionLoopBreaker`): a 2000 ripetizioni di "Partitioning face N ... same partition" kill via watchdog + catena con fallimento nominato; log GUI consuma lo spam (1 riassunto/500). Test: 37 verdi. |
| `7b3e9f9` | Handover con A/B `opt_iter` su DN1000 (knob scartato: ±4 s). |
| `7bf4a9f` | Piano fallback dichiarato all'avvio (3 rami auto) + nota `FAIL subdivide_tets` + `--geom` nell'E2E + docs. |
| `1e815a4` | A/B fine opt_iter (R1/Parte4, eps 0,3 mm): cap 6 = **+43% tempo** → previsione confutata, default 15 confermato. |
| `696ee38` | **Fix "tet veloce ma niente poly"**: il dual+BL era gateato dietro il checkMesh delle tets grezze (che fallisce quasi sempre) → al primo fail parte UNA volta (dichiarato), il checkMesh dopo il dual è autorevole. + viewer default "Surface with Edges" (CAD incluso) + last-chance "sempre poly" (eps allentato ×2, cap [20,30%], dichiarato) + `reference_first_height` fail-soft + `--geom`. |
| *(in tree, test mirati verdi)* | **Batch refinement quotidiano**: Reset ammazza i task in corsa; righe di task fermi/annullati scartate dal relay; Cancel → verdetto "cancelled" (mai "finished"); FAIL subdivide_tets coalescito (nota + riassunto/20, SEMI-FAIL incluso); doppio tag `[tet:ftetwild]` eliminato; caricamento geometria rifiutato durante un run; patch STL "ASCII" → nome file (+ auto-split riattivato, dichiarato); h1 BL clampato anche su max_cell/3 col motivo a log; battito dual 8→30 s. |

### Misure chiave (casi reali utente)
- **DN1000**: AUTO VALID POLY_BL in **66,6 s** (BL 29.640 prismi, cov 100%, checkMesh Mesh OK NO 65,5 / skew 2,78 / 0 difetti). Prima: ore in loop di partizione (>500k tentativi).
- **gmshprova**: AUTO VALID POLY_NO_BL in ~57 min — dual 191.527 celle poly, 0 tet residui, chiusura 1,95e-16; keep-best su GMSH (fTet challenger Failed NO 69,3/skew 6,59).
- **venturi_dn200.stp**: superficie GMSH macina UNA faccia in MeshAdapt >2h (mistero mirror risolto); in GUI il budget auto 120 s protegge → fTet su patch.
- **opt_iter**: coarse ±4 s; fine cap 6 = +43% (converge da solo a pass 9). Default 15 ovunque.
- **Ampiezza in corso**: universal sweep 10 geometrie (8 corpus + final.step + prendiquesta), cap 40 min/caso → `C:/cfmesh_cases/universal_sweep_results.json`. Parziali: cylinder POLY_BL 28,6 s; cube_duct POLY_BL 11 min.

### Bug utente chiusi oggi
1. "ore in loop, log non esplicativo" → breaker + log nominato (DN1000).
2. "tet veloce ma niente poly e niente BL" (venturiprovabaram.stl, 9 run in catena) → poly-gate fix (`696ee38`).
3. "processi becchi dopo stop/reset" → Reset cancel_all + relay guard + verdetto onesto.
4. "FAIL subdivide_tets = errore?" → nota + coalescenza.
5. "h1 suggerito 0,457 m" → clamp mesh-fit (max_cell/3); Nota: il valore usciva da velocity ~0,02 m/s — input da rivedere nel pannello.

### Analisi ipotesi utente (17-11, run Parte4 con poly-gate fix)
- Osservazione: "Cradle calava le celle solo 10-20%, noi -80%: forse i
  problemi di qualita' derivano da questo".  Analisi: la caduta 5,5x e'
  strutturale del duale baricentrico (1 cella/vertice, T~5,5V), la
  risoluzione NON cala (taglia cella ~ edge tets).  I difetti di QUESTO
  log tracciano a: compressione BL (h1 effettivo 0,0114 mm vs 0,674
  richiesto = 0,02x, y+ 0,455 vs 30) su celle duale di bordo "pancake"
  alle caratteristiche sottili (89 superfici, 50+ gap) + budget-
  correction x1,5 che rindegna GLOBALMENTE e peggiora i gap.  Catena
  funzionata end-to-end (fallback dichiarato, dual su tets grezze
  fail, BL 5+ tentativi dichiarati, mesh tenuta e mostrata).
- Leve proposte (da misurare con l'utente): (a) floor taglia locale
  legato ai gap sottili (il budget-correction x1,5 globale va contro
  il BL); (b) target edge piu' fine = stack piu' sottili; (c)
  eventuale modalita' conversione 1:1 (tet->poly per tet, stile
  Fluent/Cradle) come A/B — onestamente: gli sliver restano sliver,
  il BL soffrirebbe uguale, il guagno e' solo il conteggio.
- NOTA per l'utente: il progetto salvato porta ancora l'override
  esplicito gmsh_direct_poly (BYPASS auto dichiarato nel log) — click
  sulla CTA Polymesh per tornare all'auto e al pin.

### Aperti (per il prossimo giro)
- Sweep universale: completata **7/10 AUTO VALID** (6 POLY_BL +
  1 POLY_NO_BL, tutte 0 tets residui); 3 inconclusive per cap del
  mirror (multi_inlet: grind silenzioso >4h, GUI protetta da budget
  120 s; final_step: tet >40 min a medium, GUI ceiling 4 h;
  prendiquesta: Delaunay 3D >40 min). Venturi: GMSH Failed ->
  **fallback ftetwild -> Mesh OK** (storico W18: tutti Failed).
- Run di conferma GUI dell'utente (riavvio + final.step + velocity/h1 a posto).
- Firma PARTIAL 2.4.0 (R1 per-difetti, corpus, installer Linux) — utente.
- Rebuild frozen/installer se serve l'exe — su richiesta (~10 min).
- Refinement documentati non iniziati: gap aspect cilindro (SPLIT direzionale), dual-5M RAM (compact-face), taper per-face, dual venturi 74,0 + piramidi concave, benchmark robust-ON da rifare.
