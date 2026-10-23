# REORG — Architettura e routing: diagnosis (costo zero)

Date: 2026-10-23. Zero-cost step: no runs, no code changes. Method:
`wc`-level inventory, repo-wide import/entry-point grep (no vulture
installed — declared as a method limit: module-level proof is
complete, function-level dead code is NOT exhaustively proven, so the
default for anything doubtful is keep + `legacy` mark, never delete).

## 1. Inventario moduli (KB su disco)

core (top): `bl_poly.py` 232, `openfoam_runner.py` 208,
`tet_poly_dual.py` 201, `gmsh_wrapper.py` 180,
`ftetwild_wrapper.py` 75, `geometry.py` 71, `solution_adaptive.py` 59,
`native_mesher.py` 47, `hex_poly_dual.py` 42, `poly_smoother.py` 42,
`bl_config.py` 35, `case_setup.py` 29, `meshdict_gen.py` 25,
`solver_calibration.py` 25, `poly_cell_merge.py` 23.
gui: `main_window.py` 293, `params_panel.py` 127,
`viewer_widget.py` 119, `task_runner.py` 27.
commercial/: 33 moduli (tutti sotto, §2). tools/: 57 script
(52 bench/probe one-off + 5 utilità).

## 2. Codice morto: audit con prova — NESSUN MODULO RIMUOVIBILE

| Candidato (prompt) | Prova di vita | Verdetto |
|---|---|---|
| `commercial/adaptive_*`, `amr.py` | test dedicati (`test_adaptive_*.py`, `test_amr.py`) + GUI ribbon SAMR (`main_window.py:412,5820`) | VIVO, non toccare |
| `commercial/cloud_mesh.py` | `test_cloud_mesh.py` (init/auth) | VIVO (stub testato), non toccare |
| `commercial/mosaic.py`, `poly_aggregator.py` | `test_mosaic.py`, `test_poly_aggregator.py` (intera suite) | VIVI, non toccare |
| `commercial/snappy_hex_mesh.py`, `mmg_adaptation.py` | dispatch `mesh_engine.py:822,841` + `test_snappy_hex_mesh.py`, `test_mmg_adaptation.py` | VIVI, non toccare |
| `commercial/*` restanti (engine, quick_mesh, template, bc_editor, watertight, cad_healer, ...) | import GUI (`bc_dialog`, `template_selector`, `params_panel:1781`, `main_window:943`, `new_case_wizard`), `api/server.py`, `config.py`, `__init__.py` export | VIVI, non toccare |
| `core/hex_poly_dual.py`, `native_mesher.py` | `commercial/native_poly_bridge.py:28-29,153,181` + CLI `__main__` | VIVI, non toccare |
| `core/poly_smoother.py`, `poly_cell_merge.py` | `poly_remediation.py:130`, `tet_poly_dual.py:827` + suite | VIVI, non toccare |
| `core/solution_adaptive.py` | `gmsh_wrapper.py:2361` + test e2e | VIVO, non toccare |
| `core/validation.py::validate_bl_params` | chiamante reale `commercial/watertight.py:182` + export `core/__init__.py` | VIVO (legacy ma usato); migrazione a BLConfig = cambio comportamento in path altrui → NON in questo blocco |
| tools/ 57 script | zero import da src/tests/benchmarks (grep: nessun `from tools.`/`import tools.`); unica eccezione `tools/poly_fixture_builder.py`, citato in un messaggio di skip di `test_tet_poly_dual.py:302` (non eseguito) | import-isolati; spostarli in `archive/` romperebbe i riferimenti storici nelle note (`tools/cylinder_aspect_trial.py` ecc. citati in ≥10 note) senza alcun guadagno funzionale → NON spostare; al massimo indice (valutato in §5, scartato: un indice da mantenere a mano marcisce) |

Conseguenza per il Blocco A: **zero rimozioni**. L'exit criterion
("zero rimozioni senza prova") è soddisfatto per costruzione. Non
esiste "codice morto con prova" in questo tree al livello modulo;
il livello funzione non è esaurientemente provato (no vulture) e per
quello la regola è tenere.

## 3. Duplicati strutturali: analisi, non duplicati

| Candidato (prompt/residual risks) | Analisi | Verdetto |
|---|---|---|
| `_face_geometry` bl_poly vs tet_poly_dual | Contratti DIVERSI per definizione: bl_poly = aree Newell + **centroide = media aritmetica** dei vertici; tet_poly_dual = decomposizione a ventaglio sul centroide + **centroide = baricentro pesato sulle aree**. Aree matematicamente uguali ma diverso arrotondamento; centroidi diversi per costruzione. I consumer (winding solve duale, budget BL) sono tarati sul proprio. | NON duplicati. Consolidare = cambiare i numeri. Non toccare; questo chiude il debito "duplicati _face_geometry" come *decisione*, non come residuo |
| `_cell_centroids` (bl_poly, replica esatta OF) vs `_cell_centres` (tet_poly_dual, media stime) | Algoritmi diversi (piramidi pesate vs media), precisioni diverse, consumer diversi | NON duplicati. Non toccare |
| `mesh_config.py` vs `mesh_presets.py` vs `bl_presets.py` vs `preset_store.py` | Ruoli diversi: modello MeshConfig / snapshot builtin su MeshConfig / store BL (sottoclasse di PresetStore, una sola lingua preset) / store generico. W10 li ha già unificati | NON duplicati. Non toccare |
| `validate_bl_params` vs `BLConfig` | Vecchio validatore con chiamante vivo (watertight). Migrazione = cambio comportamento in un workflow altrui, senza mandato | NON consolidare in REORG. Resta `legacy` di fatto; owner implicito: watertight flow |

Conseguenza per il Blocco B: **zero consolidamenti**. Ogni candidato
del prompt è un falso duplicato a ispezione ravvicinata, e il
consolidamento cambierebbe output numerici (violerebbe §1.6/§5).

## 4. Routing attuale (verificato, non presunto)

Un solo dispatcher mesher: `params_panel.get_mesher_type()` (autorità =
Advanced Engine combo) → `main_window._current_mesher_type` →
worker (`GmshVolumeWorker`/`FtetWildVolumeWorker`/`DualPolyWorker`/
`BlOnTetsWorker`/cfMesh) → `openfoam_runner` → core. Registro unico:
`core/mesher_registry.py` (W13). Nessun secondo dispatcher creato dopo
W13. Percorsi commercial legacy (`autopoly_bridge`,
`native_poly_bridge`, `mesh_engine` con enum SNAPPY/MM G) sono
raggiungibili ma storicizzati di fatto: GUI principale non li propone
come CTA (verificato W13). Non duplicano il routing poly: non toccare.

Entry point: `app.py`, CLI per-modulo (`python -m ...`), GUI dispatch,
`api/server.py` (mock-tested). Nessun entry point orfano trovato tra
quelli del prompt.

## 5. Test e tooling

- Suite: 1697 passed (W13) + 5 WALL-03 = 1702 attesi al gate; marker
  `slow`/`wsl` per i percorsi costosi; `test_mesher_registry.py`
  fissa CTA unica + migrazione.
- tools/: 52 bench/probe one-off + 5 utilità (`tet_poly_dual_cli.py`,
  `tet_to_poly.py`, `hex_box_mesh.py`, `poly_fixture_builder.py`,
  `check_version_sync.py`, `refresh_readme_assets.py`,
  `capture_readme_screens.py`, generatori report/pdf). Niente li
  importa; le note li citano per percorso. Spostarli = costo certo
  (riferimenti storici rotti), guadagno nullo → restano.
- `benchmarks/`: harness attivo (`run_benchmarks.py`,
  `run_comparison.py` — quest'ultimo con modifiche utente non
  committate, non mie, non toccate).

## 6. Valore da tenere (mappa: tutto resta dov'è)

tet→poly storico + varianti (edge-filter, budget, local termination,
consume): `bl_poly.py` + `bl_attempt_policy.py`, invocabili da
CLI/worker/GUI come oggi. GMSH / fTetWild / ibrido / BL-on-tets:
`mesher_registry.py` + Advanced Engine + CLI `--mesher`
(`test_mesher_registry.py`). Preset Fast/Balanced/Fine: `mesh_presets.py`
+ `PresetStore` + `test_gui_mesh_presets.py`. Patch multi-regione:
`mesh_converter.py` + `test_mesh_converter_multiregion.py` (13).
Report per-patch + y+: `boundary_layer_report` + GUI log. Nessuna voce
orfana: ogni cosa buona ha già casa e test — la mappa prima→dopo è
l'identità (nessuno spostamento proposto).

## 7. Design per i blocchi (previsione di carico)

- Blocco A: audit sopra → zero rimozioni + record in
  `residual_risks.md`. Nessun rischio (zero righe toccate fuori docs).
- Blocco B: zero consolidamenti (non-duplicati provati). Nessun rischio.
- Blocco C: NON giustificato — il layout non ha rallentato W13
  (registry aggiunto pulitamente) né causa bug noti; ristrutturare
  15+ file senza movente viola §8 ("non eseguirla: consegna A+B").
  Valutazione esplicita, non rinvio pigro.
- Stima reale: diagnosi + reasoning + record docs + gate. Nessuna
  delle 10–16 file / 400–1.200 righe stimate serve: la misura dice
  che il lavoro è *non fare danni*. Dichiararlo è il deliverable
  onesto, non la scusa.
