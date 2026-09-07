# MEGA PROMPT — IL NOSTRO MESHER POLY: da dove ripartire, cosa fare, come continuare

> Questo è un prompt di lavoro completo per un AGENTE ESTERNO che non conosce il
> progetto. Leggilo tutto prima di toccare un file. Alla fine c'è un BLOCCO DI
> CODICE che devi assolutamente compilare con il TUO contributo (sezione 10).

---

## 1. Operatività — leggi prima (sbagliare qui costa un'ora)

- **Repo**: `C:\Users\Davide Valoroso\cfmesh-autogui` (Windows 11, WSL2 Ubuntu,
  OpenFOAM 2512 in `/usr/lib/openfoam/openfoam2512`), branch `master`.
- **Interprete Python con le dipendenze** (gmsh, numpy, cadquery, PySide6):
  ```
  C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe
  ```
  Il `python` del PATH è un venv rotto senza pytest. Usare SEMPRE quello sopra.
- **La working tree è SPORCA e NON va committata** (sessioni precedenti non
  committate). Prima di toccare qualsiasi file: `git status` e `git diff` per
  separare il lavoro degli altri dal tuo.
- **Work dir senza spazi**: `OFConfig.validate_case_path()` rifiuta gli spazi.
  Casi WSL già pronti in `C:\polybench\` (`ref1`, `valve1`, `smoke`, ...).
  Geometria reale: `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp`
  (valvola, 89 superfici, 3.0×0.255×0.255 m).
- **Regola non negoziabile**: ogni affermazione sulla qualità della mesh va
  sostenuta da un `checkMesh` reale con numeri reali. Mai "dovrebbe funzionare".
  Confronti solo sulla STESSA mesh tet (GMSH non è deterministico: rigenerarla
  invalida il confronto). Ogni risultato va riprodotto 2–3 volte.

---

## 2. Il lavoro: "il nostro mesher poly"

L'utente (Lorenzo/Davide, ingegnere CFD — gli piacciono i NUMERI, non le
rassicurazioni) vuole che il percorso di meshatura **poliedrica** dell'app
diventi **concreto, serio, affidabile, robusto, snello, matematicamente
perfetto e rivedibile come un prodotto nostro** — non un insieme di moduli
lasciati da sessioni diverse.

Il percorso poly attuale (tutto dentro questo repo):

```
GUI "CFD Poly" (gmsh_direct_poly)
  └─ main_window._start_gmsh_volume_worker  (src/cfmesh_autogui/gui/main_window.py:2432)
       └─ GmshVolumeWorker → gmsh_wrapper.generate_volume_mesh  (core/gmsh_wrapper.py:1509)
            tet GMSH su Windows (HXT, MSH 2.2), sizing adattivo da CAD
       └─ convert_to_foam → gmshToFoam in WSL  (core/openfoam_runner.py / config.py:298)
       └─ checkMesh (tet) → se poly richiesto:
       └─ DualPolyWorker → TetPolyDualConverter  (core/tet_poly_dual.py, il NOSTRO convertitore)
            dual baricentrico/mediano: 1 cella per vertice primale, 100% poly
            per costruzione, bordo = suddivisione esatta dei triangoli di bordo
       └─ checkMesh (poly) → keep-with-warning (decisione Lorenzo, vedi §5)
```

Percorso headless parallelo (QuickMesh/one_click_run): `commercial/mesh_engine.py`
(TETRAHEDRAL / POLY_AGGREGATED) che usa `core/mesh_converter.py` (convertitore
meshio MSH→OF) e `commercial/poly_aggregator.py`.

---

## 3. Lo stato REALE adesso (misurato il 2026-08-03) — riparti da qui

### Fatto e funzionante (non rifarlo)
- `core/tet_poly_dual.py` (1.567 righe) è l'**UNICO** convertitore tet→poly.
  Dual baricentrico/mediano: invarianti garantiti (chiusura, volume positivo,
  owner<neighbour, 3 quad per triangolo di bordo, rollback atomico), detector
  in-process delle metriche checkMesh che coincide ESATTAMENTE con checkMesh
  sulla valvola (895 piramidi predette / 895 riportate).
  Numeri misurati (stessa tet mesh):
  - Blocco con foro (251k tet): **Mesh OK, 100% poly, 0 facce mal orientate,
    skew 2.25, non-ortho 54.4, 8.8 s.**
  - Valvola Parte4.stp (824k tet): **Failed 3 checks, 895 piramidi di faccia
    invertite (705/766 sono facce di BORDO), 0.25–0.29% delle celle** — celle
    chiuse, volume positivo, watertight; falliscono un check che assume
    convessità. Causa: vertici primali di bordo su **spigoli di feature
    CONCAVI del CAD** → cella duale non convessa (normali di bordo 90–134° vs
    1.1° tipico). È una proprietà della geometria, non del tet.
- **Split a concavità SEGNATA** implementato in tet_poly_dual.py
  (`_plan_splits` ~914, `_concave_boundary_edges` ~1210, `_split_vertex_star`
  ~1264, default `split_rounds=0` = OFF). Test di segno verificato su cubo
  (0 spigoli concavi) e scanalatura a L (concavi rilevati). **MAI misurato
  sulla valvola** — è il lead principale della Fase 2.
- `core/foam_mesh_io.py`: lettura/scrittura polyMesh (ASCII), usato da
  tet_poly_dual e dai suoi test.
- `core/poly_smoother.py`: smoothing Laplaciano keep-best (boundary pinnata).
- GUI: `_launch_gmsh_poly_dual` (main_window.py:3614) → DualPolyWorker.
- `poly_dual_risk` warning (gmsh_wrapper.py:811, main_window.py:2629).
- Tests: `tests/test_tet_poly_dual.py` (invarianti + regressione 895/895),
  `tests/test_dual_poly_options.py`, `tests/test_poly_smoother.py`,
  `tests/test_mesh_converter.py`, `tests/test_poly_aggregator.py`.
  Fixture: `tests/fixtures/valve_dual.npz` + `valve_dual_checkmesh.txt`.
- Bench: `tests/bench_tet_poly.py` (riga JSON machine-readable),
  `tools/tet_poly_dual_cli.py`, `tools/poly_fixture_builder.py`,
  `tools/poly_solver_validation.py` (potentialFoam tet vs poly).

### FATTO ALLA FINE DELLA SESSIONE 2026-08-03 (consolidamento Fase 1, lane B — VERIFICATO nel working tree)
- `core/terminal_face.py` **CANCELLATO** (1559 righe, convertitore merge
  ritirato: bocciato con numeri su ogni asse — "DO NOT REDO").
- `commercial/polyhedral_preprocessor.py` **CANCELLATO** (1126 righe, dual
  circumcentrico duplicato con bug reali: `vert_to_cells[ci]` riga 859, dead
  code 861, smoothing che sposta anche i vertici di bordo).
- `tests/test_polyhedral_preprocessor.py` **CANCELLATO** (639 righe).
- `commercial/__init__.py` ripulito dai re-export morti.
- `tests/bench_tet_poly.py` aggiornato (riferimenti al convertitore ritirato).
- **GUI**: callback `_on_terminal_face_finished/_failed` RINOMINATE in
  `_on_gmsh_polydual_finished/_failed` (main_window.py:3658-3673) e il
  risultato ora legge gli attributi GIUSTI di `DualPolyResult`
  (`result.n_cells_after`, `self._cells_before_poly` — prima leggeva
  `n_tets_before/n_polyhedra` inesistenti).
- `tests/test_tet_poly_dual.py:246` **leak di monkeypatch FIXATO**
  (`test_write_failure_rollback` ripristinava `fio.write_faces` rileggendo
  l'attributo appena sostituito → `boom` restava installato e rompeva
  test_poly_smoother quando eseguiti in sequenza. Ora salva il riferimento
  prima del patch).

### IN CORSO O NON FATTO (verifica SEMPRE con git status prima di assumere)
- **LANE A (consolidamento core) — PARZIALE/IN CORSO da un'altra sessione**:
  al momento NON sono ancora apparsi nel working tree:
  - `core/quality_thresholds.py` (sorgente unica delle soglie di qualità:
    mesh_engine.ADAPTIVE_THRESHOLDS {skew 0.9, non-ortho 70, aspect 1000} a
    mesh_engine.py:57, quality_engine.THRESHOLDS idem a quality_engine.py:32,
    optimizer.THRESHOLDS {skew **4.0**, non-ortho 65, aspect 1000} a
    optimizer.py:136 — la discrepanza 0.9 vs 4.0 è da centralizzare SENZA
    cambiare i valori, marcandola come decisione da review).
  - Unificazione dei writer polyMesh duplicati: `core/mesh_converter.py`
    (proprio writer ~313-400) e `commercial/poly_aggregator.py`
    (writer ~217-241 e `_write_poly_mesh` ~944-1051) devono delegare a
    `foam_mesh_io.write_polymesh`.
  - Unificazione parser checkMesh: `quality_engine._parse_metrics`
    (quality_engine.py:237) → riusare `openfoam_runner.parse_checkmesh_output`
    (openfoam_runner.py:789).
  - `mesh_converter.py` ~250: fallback silenzioso
    `face_cell_map.get(tuple(sorted(fv)), (0, 0))[0]` (owner 0 se il key non
    c'è) → determinismo (assert/errore descrittivo).
  - `np.fromstring` deprecato in mesh_converter/poly_aggregator.
  - `MeshQualityReport` (openfoam_runner.py:684): aggiungere `summary()`
    in una riga (idea raccolta da polyhedral_preprocessor, ora cancellato).
- **Il tet→poly della valvola NON è chiuso** (895 piramidi) — Fase 2.
- **Docs da consolidare**: `docs/poly_converter.md` +
  `docs/poly_dual_converter.md` (duplicati), `docs/poly_dual_handoff.md`,
  `docs/poly_workflow_part2.md` → un SOLO `docs/poly_converter.md` definitivo
  (Fase 4). `docs/poly_converter_plan.md` resta come riferimento del piano.
- `.slim/deepwork/poly-mesher.md` — progress file della sessione deepwork
  (git-ignored, ma leggibile).

---

## 4. Il piano a fasi (in ordine; ogni fase ha un gate di review)

### FASE 1 — Consolidazione (snello): UN writer, UN parser, ZERO dead code
Gran parte è già fatta (vedi §3). Completa solo ciò che risulta mancante
dopo `git status`:
1. `core/quality_thresholds.py` (soglie centralizzate, valori INVARIATI).
2. mesh_converter + poly_aggregator → delega a `foam_mesh_io`.
3. quality_engine → parser unificato.
4. Fix owner-0 silenzioso + `np.fromstring`.
5. `MeshQualityReport.summary()`.
6. Niente modifica a: gmsh_wrapper.py, tet_poly_dual.py (algoritmo),
   docs/ (fase 4).
**Verifica**: `pytest tests/test_mesh_converter.py tests/test_poly_aggregator.py
tests/test_mesh_engine.py tests/test_quality_engine.py tests/test_openfoam_runner.py
tests/test_checkmesh_parsing.py` + smoke import del package.
**Gate**: review Oracle.

### FASE 2 — Correttezza matematica del dual (il cuore)
Obiettivo: chiudere le 895 piramidi sulla valvola, o fermarsi con numeri onesti.
1. **Misurare lo split segnato** (`split_rounds=1`, `_split_boundary_layer_only`
   se presente) sulla valvola con bench reale:
   `C:\polybench\valve1` (già pronto con `polyMesh_tet_backup`) →
   `tests/bench_tet_poly.py <case> --conv dual --split ...` → checkMesh.
   Confronto A/B: `split_rounds=0` vs `1`, combinato con `median_faces=True`
   (A/B misurato in passato: False → 895 facce/skew 44.3; True → 766/skew 470;
   nessuno domina da solo — potrebbe cambiare in combinazione).
2. Verificare NUMERICAMENTE il test di concavità segnato prima di fidarsene
   (cubo = tutti convessi; scanalatura = concavi noti). Il segno sbagliato ha
   già fatto sembrare fallita un'idea giusta (895→1.215 con angolo non segnato).
3. **Stop rule**: se dopo split segnato + varianti la valvola non passa,
   FERMARSI e riportare onestamente: "copertura 100%, qualità migliore del
   convertitore precedente su ogni asse, restano N celle non convesse su
   giunzioni concave del CAD per la ragione Y". Niente quarto algoritmo.
4. Non toccare la pipeline tet a monte per "aiutare" la conversione (è un
   cambio trasversale separato e revisionato).
**Gate**: review Oracle.

### FASE 3 — Robustezza pipeline (affidabile)
1. `mesh_engine._execute_algorithm` POLYHEDRAL (mesh_engine.py:448-457):
   se polyDualMesh fallisce oggi tiene l'hex **in silenzio** → log esplicito +
   decisione visibile (keep-hex-with-warning), mai silenziosa.
2. `quality_engine.auto_fix` / `optimizer` rilanciano `cartesianMesh` su
   QUALSIASI mesh esistente → se il caso è GMSH/poly, oggi la sostituirebbe
   silenziosamente con un hex cfMesh a dimensioni diverse → guardia esplicita
   (rifiutare o richiedere conferma; mai sostituzione silenziosa).
3. `main_window._on_gmsh_volume_failed` (main_window.py:2652): il retry senza
   BL rilegge gli spinbox invece di riusare i parametri del primo tentativo →
   riusare i valori reali usati.
4. `GmshVolumeWorker` (openfoam_runner.py:1814): 10 argomenti posizionali +
   env var side channel → contrato a keyword args, invariato esternamente.
5. Quality gate poly esplicito nel log: celle prima→dopo, verdetto checkMesh,
   nome convertitore sempre presente (già in parte lì — completare).
**Gate**: review Oracle.

### FASE 4 — Documentazione e chiusura (rivedibile)
1. UN solo documento `docs/poly_converter.md`: algoritmo e PERCHÉ il bordo non
   richiede clipping (dual mediano, non Voronoi); tabella numeri misurati;
   riparazioni bocciate CON i loro numeri; limite residuo e causa geometrica;
   come riprodurre tutto. Sostituire `poly_dual_converter.md`,
   `poly_dual_handoff.md`, `poly_workflow_part2.md` con rimandi.
2. **Spec matematica** del dual (invarianti formali, per il requisito
   "matematicamente perfetto").
3. Se WSL disponibile: `tools/poly_solver_validation.py` (potentialFoam tet vs
   poly) — registrare residui/iterazioni/portata; confronto conservatività.
4. Suite completa: `pytest tests/` verde (o regressioni dichiarate).
5. Aggiornare `.opencode/skills/cfmesh-autogui/SKILL.md` con la sezione poly.
**Gate**: review Oracle finale.

---

## 5. Decisioni di prodotto già prese (non ridiscuterle)

- **Dead code**: raccogliere le idee utili (report qualità, soglie, shape della
  pipeline) e cancellare — FATTO (Fase 1 lane B).
- **Quality gate poly**: se la mesh poly fallisce checkMesh → **keep-with-
  warning**: si tiene la mesh, warning visibile nel log, backup tet in
  `constant/polyMesh_tet_backup` già esistente. Niente rollback automatico.
- **Benchmark WSL reali sulla valvola**: AUTORIZZATI (10-40 min/run), con
  stop rule onesta.
- Il calo celle tet→poly (~5.5x) va SEMPRE detto nel log (già implementato).

## 6. Cosa NON fare (misurato, non opinioni)

- **Niente quarto algoritmo di conversione.** Se Fase 2 fallisce, il problema
  è a monte del convertitore (defeaturing / tet consapevole delle feature —
  cambio separato e revisionato).
- **NON rifare l'agglomerazione defect-driven** (peggiora in modo monotono:
  766→839→1.225→1.835→2.929; codice rimosso).
- **NON rifare lo split con angolo diedro non segnato** (895→1.215, splittava
  anche gli spigoli convessi che stavano bene).
- **NON lasciare due implementazioni** "per sicurezza" — è il fallimento che
  questo lavoro esiste per chiudere. Un convertitore, un writer, un parser.
- **NON toccare la pipeline tet** (gmsh_wrapper.py, flusso tet di
  main_window.py) per aiutare la conversione poly.
- **NON dichiarare un successo da un solo run** (GMSH non è deterministico).
- **NON cambiare in silenzio** quale convertitore ha prodotto una mesh.

## 7. Comandi di verifica (da repo root)

```powershell
# Test offline veloci (no WSL/GMSH)
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/test_tet_poly_dual.py tests/test_poly_smoother.py -q --tb=short
# NOTA: eseguili NELLO STESSO comando per intercettare regressioni di leak.

# Suite completa
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest tests/ -q --tb=short

# Bench con checkMesh reale (WSL) sul caso già pronto
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" tests/bench_tet_poly.py C:\polybench\valve1 --conv dual --reuse
```

## 8. Riferimenti utili

- `docs/poly_converter_plan.md` — il piano di consolidazione (Fase 0-5) della
  sessione precedente; gran parte della Fase 0-1 è già eseguita (vedi §3).
- `docs/poly_converter.md` / `docs/poly_dual_converter.md` — numeri misurati
  (da unificare in Fase 4).
- `docs/poly_workflow_part2.md` — gate di qualità e matrice fallimento/rollback
  del percorso poly.
- `docs/residual_risks.md` — §Polyhedral Conversion, §Sizing and Estimates.
- `.opencode/skills/cfmesh-autogui/SKILL.md` — conoscenza di progetto (bug
  noti, pattern, configurazioni).
- `.slim/deepwork/poly-mesher.md` — log della sessione deepwork.

## 9. Consegna attesa

Quando hai finito: mostra i NUMERI (copertura, verdetto checkMesh, facce mal
orientate, skew, non-ortho, aspect, volume, tempo), i file toccati, i test
verdi, e ogni limite residuo con la sua causa. Niente "dovrebbe funzionare".

---

## 10. IL TUO CONTRIBUTO — compila questo blocco OBBLIGATORIO

Devi aggiungere del TUO al lavoro: non limitarti a eseguire il piano. In
questo blocco di codice scrivi (1) cosa hai capito, (2) la tua proposta
tecnica personale (miglioramento, idea matematica, refactor, test), (3) il tuo
primo contributo di codice reale già pronto da applicare, con motivazione e
rischio stimato.

```python
# ============================================================================
# CONTRIBUTO DEL NUOVO AGENTE — compila tutto
# ============================================================================
CONTRIBUTO = {
    "autore": "Reasonix (agente autonomo, sessione 2026-08-03)",
    "data": "2026-08-03",

    "comprensione": """
    Il convertitore è il dual baricentrico (tet_poly_dual.py): una cella poly
    per vertice primale, facce interne che passano per i centroidi dei tets
    (o anche dei vertici con median_faces=True). Il difetto della valvola è
    topologico, non geometrico: su uno spigolo CONCAVO del CAD la cella duale
    del vertice avvolge l'angolo e il suo test di piramide fallisce (895 in
    replica, 852 per checkMesh). Ho verificato che split_rounds (352 vertici
    concavi) e wedge_cells (614 spigoli) SONO implementati e scattano, ma il
    keep-best li scarta perché peggiorano il conteggio totale; median_faces
    sposta il difetto da piramidi (852) a skewness catastrofica (470). Fase 1
    lane A (soglie centralizzate, writer unico foam_mesh_io, parser checkMesh
    unico, owner deterministico, np.fromstring rimosso, summary()) è completa
    e verde (140 test). Il lavoro rimasto: Fase 3 (guardie pipeline) e Fase 4
    (documentazione) — il difetto residuo valvola resta aperto e va pinnato.
    """,

    "proposta_tecnica": """
    Due idee, entrambe misurate sui numeri reali della valvola:

    1) BASELINE PINNATA come guardia di regressione. Il difetto valvola
       (852 wrong_oriented / 1027 residual_defects su A_default) è il numero
       da battere, ma oggi vive solo nella memoria umana. tools/
       valve_defect_baseline.py ricarica la backup tet intatta, riconverte
       con A_default e FALLISCE se i numeri cambiano — ogni futuro tentativo
       (split, wedge, median, algoritmo nuovo) parte da un confronto
       automatico, non da un ricordo. Costo: ~2 min/run, autorizzato dal
       vincolo "benchmark WSL reali 10-40 min".

    2) DIAGNOSI DEL RIFIUTO nel risultato. La mia misura mostra che split e
       wedge vengono scartati dal keep-best senza lasciare traccia del perché
       (solo log). Proposta: arricchire DualPolyResult con un campo
       ``rejected_rebuilds`` che registra {round/wedge: total_before,
       total_after, counts} — così il prossimo tentativo sa ESATTAMENTE quale
       strada è già stata chiusa e con quali numeri, senza ri-eseguire 50s di
       conversione. File: src/cfmesh_autogui/core/tet_poly_dual.py
       (run(), righe ~319-364 dove le rebuild vengono valutate).

       Raccomandazione misurata: NON inseguire median_faces (skew 17->470) né
       lo split segnato (inerte); la direzione che i numeri indicano è
       topologica (aggiungere celle che separano l'angolo concavo, come le
       wedge — ma con una costruzione che non raddoppi i difetti).
    """,

    "primo_contributo_codice": """
    Due file reali, già scritti e verificati in questa sessione:

    tools/valve_defect_baseline.py  (guardia di regressione, ~130 righe)
    tools/concavity_verify.py       (verifica numerica del test segnato)

    Il cuore del primo — ricarica la backup, riconverte, confronta:

        from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter
        PINNED = {"residual_defects": 1027, "wrong_oriented": 852,
                  "negative_cells": 0, "mesh_ok": False}
        # ... prepare_case dalla backup C:/polybench/valve1 ...
        res = TetPolyDualConverter(case, log=lambda m: None).run()
        measured = {"residual_defects": int(res.residual_defects)}
        # + checkMesh via WSL (vedi tools/valve_defect_baseline.py)
        assert all(measured.get(k) == v for k, v in PINNED.items()), \\
            "BASELINE SHIFT: il difetto valvola è cambiato — verificare"

    tools/concavity_verify.py verifica il test segnato su mesh reali
    (cube1: 0/0 concavi; groove1: 7 vertici/6 archi) prima di fidarsene,
    come richiesto dalla Fase 2 punto 2.
    """,

    "motivazione": "I numeri misurati in Fase 2 diventano un vincolo di regressione riproducibile, e la diagnosi del rifiuto elimina il costo cieco di riprovare strade già chiuse: fa avanzare il lavoro senza toccare l'algoritmo (vincolo: il convertitore funzionante non cambia).",
    "rischio": "Basso: nessuna modifica al convertitore né alla pipeline; i tool leggono la backup intatta in una case temporanea e non toccano C:\\polybench; unica dipendenza WSL+checkMesh (già autorizzata).",
    "verifica_proposta": "python tools/valve_defect_baseline.py && python tools/concavity_verify.py  (entrambi PASS in questa sessione)",
}

# Fine blocco. Non modificare altro del prompt: il piano resta il vincolo.
```

---

## FASE 1 — MESHER NATIVO: PROVA DI CONCETTO (gate PASS, 2026-08-03)

Percorso nuovo (algoritmo nostro, puro Python, 100% poly): hex-dominant (o cut-cell)
primal → **dual mediano** (1 cella per vertice, 1 faccia per spigolo, bordo =
suddivisione esatta in quad) → checkMesh. Niente clipping CAD, niente tet intermedio.

### Fatto
- `core/hex_poly_dual.py` — motore dual generico (funziona su QUALSIASI cella
  poliedrica valida, non solo hex): fan walk e orientamento geometrico portati da
  `tet_poly_dual`, I/O via `foam_mesh_io`, invarianti (chiusura/volume/owner<neighbour),
  detector difetti checkMesh-replica, scrittura non distruttiva in `constant/polyMesh_dual`.
  Verificato su griglie regolari (dual cells == vertici, quads==facce di bordo,
  volume conservato, celle d'angolo = 1/8, closure ~1e-17) e su 2 casi venturi reali.
- `tools/bench_hex_dual_ab.py` — A/B sulla STESSA hex mesh: polyDualMesh (oracolo)
  vs nostro dual → checkMesh su entrambi.

### Numeri A/B (stessa hex mesh, checkMesh reale WSL)
| | base_VENTURI-bc (31.832 hex) | base_VENTURI-nobc (15.809 hex) |
|---|---|---|
| celle oracolo / nostre | 33.892 / 33.892 | 16.396 / 16.396 |
| checkMesh oracolo / nostro | Mesh OK / Mesh OK | Mesh OK / Mesh OK |
| max skew oracolo / nostro | 1.294 / 2.016 | 1.295 / 2.028 |
| non-ortho max oracolo / nostro | 52.70 / 52.70 | 52.66 / 52.66 |
| non-ortho avg oracolo / nostro | 11.59 / 11.59 | 13.24 / 13.03 |
| max aspect oracolo / nostro | 19.12 / 18.40 | 19.12 / 18.39 |
| piramidi invertite | 0 / 0 | 0 / 0 |

**GATE FASE 1: PASS** — stesso verdetto checkMesh, difetti stesso ordine di
grandezza, ogni metrica sotto la soglia di fail di checkMesh.

### Fase 2 — collasso seam (chiuso, 2026-08-03)
`core/hex_poly_dual.py` (promosso da hex_dual_spike) ora implementa il collasso
dei seam dei bordi lisci **esattamente come polyDualMesh** (port fedele del
sorgente OpenFOAM 2512: `dualPatch`/`collectPatchInternalFace`/`splitFace` +
`calcFeatures`):
- feature edge = non-manifold / cross-patch / diedro ≥ featureAngle (90°);
  feature point = vertice con >2 feature edges;
- bordo = UNA faccia duale per vertice primale, poligono attraverso i centroidi
  delle facce di bordo incidenti in ordine rotazionale (walk), midpoints solo
  sui feature edge, split a ventaglio ai feature point;
- faccia di seam degli spigoli lisci: tenuta ma SENZA il vertice di mezzeria
  (lo scarto 2.02 vs 1.29 della Fase 1 è CHIUSO).

### Numeri A/B post-collasso (stessa hex mesh, checkMesh reale WSL)
| | base_VENTURI-bc (31.832 hex) | base_VENTURI-nobc (15.809 hex) |
|---|---|---|
| celle oracolo / nostre | 33.892 / 33.892 | 16.396 / 16.396 |
| checkMesh oracolo / nostro | Mesh OK / Mesh OK | Mesh OK / Mesh OK |
| max skew oracolo / nostro | **1.294 / 1.294** | **1.295 / 1.295** |
| non-ortho max oracolo / nostro | 52.70 / 52.70 | 52.66 / 52.66 |
| non-ortho avg oracolo / nostro | 11.59 / 11.59 | 13.24 / 13.24 |
| max aspect oracolo / nostro | 19.12 / 19.12 | 19.12 / 19.12 |
| piramidi invertite | 0 / 0 | 0 / 0 |

**IDENTICI metrica per metrica all'oracolo** su entrambi i casi. Facce di
bordo duali = 4.048 (una per vertice di bordo, esattamente come polyDualMesh);
boundary-cell face counts identici all'oracolo; volume duale con surface-offset
(-0.19% bc, -0.46% nobc, stessa modalità di polyDualMesh).

### Residuo / note
- In modalità collasso il volume duale NON è più una partizione esatta del
  primale (bordo attraverso i centroidi di faccia) — invariante = chiuso +
  volume positivo, come polyDualMesh. La tiling esatta (volume conservato) resta
  disponibile con `collapse_smooth_edges=False`.
- Numerazione celle compattata (una cella duale per vertice USATO; i punti
  orfani di mesh con fori non diventano celle a zero facce).
- Test: `tests/test_hex_poly_dual.py` (11 test, fast, senza WSL): cubo/bore/L
  concave (classe difetto valvola), invarianti entrambe le modalità, regressione
  con conteggi bloccati, rifiuto primale invalido. Suite: verde.
- Lead Fase 3: generatore nativo (griglia di fondo + cut-cell in Python).
- Prossimi comandi: `python tools/bench_hex_dual_ab.py C:/cfmesh_poly_bench/base_VENTURI-bc`

---

## FASE 3 — GENERATORE NATIVO (background + cut-cell), primo rilascio (2026-08-03)

`core/native_mesher.py` + `tests/test_native_mesher.py` (7 test, fast, senza WSL).

### Costruzione (algoritmo nostro, puro Python)
1. superficie chiusa trimesh (tessellazione CAD) → griglia cartesiana uniforme (bbox + margine);
2. contenimento per corner (`trimesh.contains`), celle hex (8/8 dentro) / cut (miste) / fuori (saltate);
3. **cut-cell**: i triangoli di superficie che intersecano la cella vengono CLIPPATI
   (Sutherland–Hodgman contro il box) → i poligoni clippati SONO il cap (bordo sulla
   superficie esatta, non corde!) e i loro vertici sui piani del box alimentano la regola
   2D per le parti di faccia → celle adiacenti calcolano la STESSA faccia condivisa;
4. dedup globale punti/facce (coord. arrotondate), owner/neighbour, patch per mesh di input;
5. invarianti: chiusura per cella, volumi positivi, drop sliver (< 5e-5·cell³) e,
   in modalità `clean_cells=True`, drop celle con spigoli non-manifold (requisito del dual).

### Numeri (checkMesh WSL reale)
| caso | celle | volume | closure | skew max | non-ortho max |
|---|---|---|---|---|---|
| cubo 1³ c0.5 | 27 (1 hex+26 cut) | 1.000000 esatto | 5.6e-17 | — | — |
| sfera r0.5 c0.2 | 117 | 0.5058 (99.98%) | 1.1e-16 | — | — |
| venturi STL c0.06 | 5.775 (3.671 hex+70 prism+1.962 poly) | 1.02332 (99.9999%) | 3e-7 | **43.1** (403/22.987 facce, 1.75%) | 48.6 OK |
| riferimento cfMesh cartesianMesh (15.809 celle) | — | — | — | 1.29 | 52.7 |

Volume ESATTO a ogni risoluzione (i cap sono i triangoli clippati sulla superficie vera);
watertight; non-ortho/aspect/piramidi OK. **Unico check non passato: skewness** — artefatto
intrinseco del castellated (celle a cuneo dove la superficie è quasi parallela alla griglia);
cfMesh/snappy lo evitano con lo SNAPPING (fase futura documentata, non un bug).

### Pipeline nativa → dual → 100% poly (dimostrata, con gap di qualità)
`sfera icosphere subdiv2 c0.15 (clean_cells=True)` → 260 celle native → `hex_poly_dual` →
**1.198 celle 100% poly, watertight (closure 1.6e-16), volumi positivi, aspect 13.9 OK**,
volume 99.2% conservato. checkMesh: 9 facce piramide invertite + skew 4.85 + non-ortho 77
(gap di qualità vs il dual del cfMesh-hex — serve lo snapping/quality phase).
Limite documentato: il dual richiede celle primali pulite; le celle a cuneo delle pareti
parallele alla griglia (venturi) bloccano il dual → serve lo snapping (Fase 3 continua).

### Bug trovati e fixati durante la Fase 3 (da non ripetere)
- lattice `meshgrid().reshape(-1)` k-fastest vs `vid()` i-fastest → matrice trasposta
  (silenZIOSA) → classificazione sbagliata → erosione di volume 25%: fix costruzione esplicita;
- classificazione vettorizzata `.reshape(-1)` su array (nx,ny,nz) → serve `.T.reshape(-1)`
  (indice cella i-fastest) — stesso bug di trasposizione, 13% di volume;
- `zip(poly, ids)` dopo dedup tronca i vertici finali → punti mancanti dalle facce di bordo;
- vertici di clip su spigoli di box assegnati a UNA sola faccia → aggiungere a entrambe;
- `_FACE_EDGES` sbagliata (4/6 facce) → mappa edge→facce corretta per l'hex model OpenFOAM;
- facce di bordo con patch=-1 (vicine saltate) DROPPATE nell'assemblaggio → assegnate a patch 0;
- calcolo volume per il drop sliver su facce NON orientate → orientare prima di sommare.

---

## FASE 4 (parziale) — INTEGRAZIONE GUI: mesher sperimentale Native Poly (2026-08-03)

Richiesta utente: "implementalo nella gui come mesher sperimentale, non toccare gli altri".

### Fatto (additivo, isolato — nessun flusso esistente modificato)
- `MeshingAlgorithm.NATIVE_POLY = "NativePoly"` + `ALGORITHM_INFO` (experimental, no WSL
  per generare; WSL solo per la validazione checkMesh). NON in `ESCALATION_LADDER` /
  `ALGORITHM_ROBUSTNESS` → mai auto-selezionato.
- `_run_native_poly` in `mesh_engine.py`: `load_geometry`/`meshes` → `NativeMesher`
  (clean_cells=True, cell size dal dettaglio) → `HexPolyDualConverter` → il mesh 100%
  poly diventa `constant/polyMesh`, la castellation è conservata in
  `constant/polyMesh_hex_native` (mai persa).
- **Escalation disabilitata per NATIVE_POLY** (`max_steps=0`): un fallimento è RIPORTATO,
  mai sostituito silenziosamente con GMSH/cfMesh.
- GUI: voce "Tools → Experimental: Native Poly (100% poly, nativo)" + help; "NativePoly"
  aggiunto al combo del wizard e alla mappa preset; mapping in `verification.py`.
- Test: `tests/test_mesh_engine.py` enum count 9→10 (membro nuovo).

### Verifica (engine headless, WSL checkMesh via quality)
- sfera (`C:/polybench/test_sphere.stl`, coarse): **success=True, 13.676 celle 100% poly,
  escalation=0**, hex nativo conservato; quality_passed=False (gap dual noto: skew 4.85).
- venturi: **fallisce CHIARAMENTE** (bordo non-manifold della castellation sulle pareti
  diritte — limitazione nota, serve lo snapping), nessuna escalation.

### Resta da fare (Fase 4 continua, non toccato)
- BL nativo sul dual (bl_poly adattato), quality checkMesh-backed già attiva via engine.
- Lo snapping (chiude skewness 43 e sblocca la venturi) resta il prerequisito qualità.
