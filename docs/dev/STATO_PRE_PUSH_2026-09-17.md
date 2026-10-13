# Stato pre-push — 2026-09-17

Handoff per decidere se sincronizzare `master` sul repo pubblico.
44 commit dal 9 settembre. Verifiche di oggi: full suite **1206 passed,
2 skipped, 1 xfailed, 0 failed** in ~14 min (skip = 2 test venturi-throat
tessellation-dependent; xfail preesistente); `ruff check src/ tests/`
pulito; igiene note (commit `9d1e6d1`, tree pulito). Nessun remote
configurato in questo clone (`git remote -v` vuoto): pushare è comunque
una decisione tua, non del tooling.

## 1. Cosa è cambiato dal 9 settembre (per temi, non per commit)

**RAM / scalabilità** (Lane A + fix `d0fd434`):
- Guard di sicurezza concaveClosure/merge: da skip sopra 1,5M celle a skip
  sopra **8M facce** — la memoria scala con le facce (~0,8 KB/faccia hex,
  ~1,0 dual; dual ≈ 8,2 facce/cella vs ≈ 3 hex), non con le celle.
- `_face_geometry` (bl + tet) a blocchi da 250k facce, output bit-identici;
  parity-graph del winding a batch + CSR (~10 B/lato vs ~150);
  `vert_faces` solo vertici di parete; winding con flip in place.
- Misurato: run sintetico reale 4,99M celle / 15,06M facce, picco 13,05 GB
  (build) e 10,6 GB (merge) sotto la barra 16 GB.
- Dual-5M celle (~41M facce) proietta ~50 GB: fuori portata senza riscrittura
  della rappresentazione facce — resta skippato dal guard, per costruzione
  non può più crashare da questo path. Dettagli:
  `notes/ram_crash_rootcause_reasoning.md`.

**Copertura BL su geometrie concave** (Lane B e derivate):
- `local_termination` decoupled/decoupled_vertex (H4): la valvola passa da
  impossibile a success a scala 1.0; generalizzato su cubo/groove1/slot1.
- `local_height_retry`: -5,0% esclusioni, checkMesh quasi-neutro.
- Combinato most_visible + retry: **6195 esclusioni** (-12,2% da 7054),
  promosso a combo opt-in documentata.
- Winding solve globale esatto (sostituisce il greedy che collassava a
  scala 0.6 con 378.605 volumi non positivi).
- `early_exit_intermediates`: 15 → 4 build, ~2215 s → ~450 s su valvola.
- Merge repair post-BL (stesso flag concaveClosure): valvola 340 → 58
  facce male orientate, aspect ratio invariato.
- Sbloccato `smoothed` + `dual_convexity` (era un raise stantio).

**Qualità mesh** (confronto snappyHexMesh sul cilindro):
- Smoothing-gate fix (gira sempre, non solo con difetti sopra soglia):
  NOmax -19%, skew -16% reali.
- Skewness formula fix (era somma invece di max di OpenFOAM): ora match
  esatto su due mesh indipendenti; il gate del merge vede davvero lo skew.
- Compactness/axis-balance opt-in: NOmax -12,4%, media -19,3% (≈ snappy),
  skew -8,6%, aspect invariato. Planarity guard + tie-break stretto (`<`).
- Modalità zonale worst-element (minimax L_inf): -0,5% sul max aspect a
  costo zero — micro-guadagno, NON cablata in produzione.

**Bugfix**: `read_label_list` scambiava payload binari con byte 0x29 per
ASCII (mesh corrotta) — fix + regression test. Wiring GUI: checkbox
concaveClosure + merge repair (entrambi opt-in, default off).

## 2. Cosa è DEFAULT oggi

**Invariato rispetto a prima.** Chi non tocca opzioni ottiene bit per bit
il comportamento storico (`area_weighted`, niente local termination, niente
retry, smoothing default, niente merge). Prova:
- suite completa verde inclusi i run di default sul cubo sano;
- ogni `normal_method` è testato a parità di prismi vs `area_weighted`;
- modalità zonale spenta ≡ default bit-identico (test dedicato);
- compactness/axis/compactness off di default (firma invariata);
- GUI: i due soli wiring sono checkbox opt-in spenti di default.

## 3. Cosa è opt-in / sperimentale (con numeri veri)

| Flag | Cosa fa | Guadagno misurato | Costo misurato | Raccomandato? |
|---|---|---|---|---|
| `local_termination="decoupled_vertex"` | terminazione locale per-vertice su concave | valvola: da fail a success scala 1.0 | ~8% wall shell ceduta nelle zone escluse | sì, su mesh concave di produzione |
| `local_height_retry=True` | retry a stack corto prima di escludere | -5,0% esclusioni | +1,3% severeNO | sì, con decoupled_vertex |
| `normal_method="most_visible"` | normale min-max per vertice | -6,3% esclusioni | +5,2% severeNO, checkMesh misto | situazionale (copertura > ortogonalità) |
| combo most_visible + retry | entrambi | **-12,2% esclusioni** | +2,9% severeNO vs most_visible solo | sì, per max copertura |
| `normal_method="smoothed"` | laplaciano sui normali | ~neutro / situazionale | ~neutro | situazionale |
| `normal_method="guided"` | bilaterale guidato (Zhang 2015) | **0 (no-op misurato)** | 0 | no (resta testato nel codice) |
| smoothed + dual_convexity | sblocco combo | **0 (no-op, -0,23%)** | 0 | no |
| `use_compactness` / `use_axis_balance` (smoother) | passi shape-aware | NOmax -12%, skew -9% | nessuno misurato | sì su dual sani |
| `worst_aspect_thr` / zonale | focus worst-element | -0,5% max aspect | 0 | no (non cablato in produzione) |
| merge repair post-BL (via concaveClosure) | fonde celle concave | wrong-orient 340 → 58 | nessuno misurato | sì, con concaveClosure |
| `early_exit_intermediates` | salta scale intermedie | 15 → 4 build | rischio se il difetto guarisce a scala media | sì con local_termination |
| guard 8M facce | skip oltre soglia | niente crash possibili | feature skippata oltre soglia | automatico (sicurezza) |

## 4. Provato e SCARTATO (non riprovarci senza leggere prima la nota)

- `smoothed` + `dual_convexity`: no-op (-0,23%), il dual fade termina già
  tutto (`notes/smoothed_dual_blend_reasoning.md`).
- `guided`: no-op di principio — i normali dual non sono rumorosi e al
  ridge il vertice media comunque tra i lati
  (`notes/guided_normal_filter_reasoning.md`).
- Merge-for-aspect sul cilindro: aspect -10,6% MA NOmax +80%, skew +233%
  (il gate conta violazioni, non severità — `notes/cylinder_aspect_clusters_reasoning.md`).
- `aspect_lam=10`: NOmax +36%, skew +12% reali (replica in-process cieca —
  `notes/getme_compactness_reasoning.md`).
- Thin-first: non generalizza, restringe lo spessore BL 10x in silenzio
  (FASE 4, `docs/residual_risks.md`).
- `split_rounds=1`: +20% tempo, zero difetti in meno (keep-best sceglie round 0).

## 5. Limiti noti residui

1. **Gap aspect ratio cilindro 4,49 vs 3,02 snappy**: servono split
   direzionali delle slab al tappo (diagnosi precisa esistente), operatore
   non scritto — unico lavoro strutturale lasciato aperto, con specifica.
2. **Soglia RAM misurata solo su hex sintetico** (15M facce → 13 GB); su
   dual la copertura oltre 1,24M facce è proiezione con margine, non misura.
   Il guard a 8M facce è conservativo per entrambi.
3. Suite: 2 skip (venturi-throat, tessellation-dependent) + 1 xfail
   preesistente; le validazioni pesanti (valvola, 5M, checkMesh) girano solo
   via WSL/OpenFOAM 2512, non in CI.
4. Nessun remote in questo clone: il push sul pubblico è fuori dallo scopo
   di questa sessione per regola esplicita.

## 6. Cosa NON è stato toccato (e perché non serviva)

- `installer/`, `.github/`, `pyproject.toml`, `src/polyfoammesh/commercial/`:
  **zero commit dal 9/9** (verificato via git log) — packaging, CI e parte
  commerciale fuori ambito dichiarato.
- GUI: unico file toccato `gui/params_panel.py`, solo i due checkbox opt-in
  (commit `6511369`, `5ae0b51`); nessun redesign, nessuna logica di meshing.
- `main` pubblico / `origin`: mai toccati (non esiste nemmeno il remote qui);
  branch locali di lavoro (`backup-*`, `claude/*`, `pr-7-*`) non toccati.
- Default di ogni parametro: invariati (vedi §2).
