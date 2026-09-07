# MEGAPROMPT — Chiudere il mesher poliedrico + boundary layer

> Handoff completo per un agente esterno che NON conosce il progetto.
> Leggi tutto prima di toccare un file. Alla fine (§10) c'è un blocco che devi
> compilare col TUO contributo.

---

## 1. Operatività — sbagliare qui costa un'ora

- **Repo**: `C:\Users\Davide Valoroso\cfmesh-autogui`, branch `master`, Windows 11.
- **Interprete Python con le dipendenze** (numpy, gmsh, cadquery, PySide6, pytest):
  ```
  C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe
  ```
  Il `python` sul PATH è un venv rotto **senza pytest**. Usa SEMPRE quello sopra.
  Nei comandi qui sotto è abbreviato `PY`.
- **WSL2 Ubuntu + OpenFOAM v2512** in `/usr/lib/openfoam/openfoam2512`. Serve solo
  per `checkMesh` / `gmshToFoam` / i solver — mai per generare la mesh.
- **Percorsi di lavoro SENZA spazi**: `OFConfig.validate_case_path()` rifiuta gli
  spazi e la conversione WSL mangia lo spazio in `C:\Users\Davide Valoroso\...`.
  Usa `C:\polybench\` e `C:\polybench2\`.
- **Casi bench già pronti** (con `constant/polyMesh_tet_backup` intatto, riuso
  istantaneo): `C:\polybench\ref1` (blocco con foro), `C:\polybench\valve1`
  (valvola reale), `cube1`, `groove1`, `smoke`. Geometria CAD reale:
  `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp` (valvola, 89 superfici B-Rep).
- **Regola non negoziabile**: ogni affermazione sulla qualità di una mesh va
  sostenuta da un `checkMesh` REALE con numeri reali. Mai "dovrebbe funzionare".
  Confronti solo sulla STESSA mesh tet (GMSH non è deterministico: rigenerarla
  invalida il confronto). Riproduci ogni risultato 2-3 volte prima di chiamarlo
  stabile.
- **Working tree**: fai `git status` e `git diff` PRIMA di toccare qualsiasi cosa.
  Committa a fasi, con messaggi che dicono i numeri misurati.

---

## 2. Cos'è già fatto e funziona (NON rifarlo)

Percorso di produzione, tutto dentro questo repo, nessun tool esterno:

```
STL/STEP → GMSH tet (Windows, nativo)
         → gmshToFoam (WSL)
         → TetPolyDualConverter   (core/tet_poly_dual.py)   → 100% poly
         → PolyBoundaryLayerEngine (core/bl_poly.py)        → prismi di parete
         → checkMesh (WSL)
```

- `core/tet_poly_dual.py` — dual baricentrico/mediano. Una cella per vertice
  primale, una faccia interna per spigolo primale, bordo = suddivisione ESATTA
  dei triangoli di bordo (3 quad planari per triangolo). 100% poly per
  costruzione. Nessun clipping CAD (è un dual MEDIANO, non Voronoi: i punti di
  bordo stanno già sulla superficie). Ha un **detector in-process delle metriche
  di checkMesh che coincide ESATTAMENTE con checkMesh** (895 piramidi predette /
  895 riportate sulla valvola) — è l'infrastruttura più utile del repo, usala
  per iterare senza round-trip WSL.
- `core/bl_poly.py` — motore BL advancing-layer NOSTRO, puro numpy. Estrude
  prismi anisotropi dalle quad di parete della mesh poly già costruita. Conforme
  per costruzione. Validazione interna con i criteri di checkMesh + fallback
  keep-best (mesh mai lasciata a metà).
- `core/foam_mesh_io.py` — unico reader/writer polyMesh (ASCII/binario,
  faceList/faceCompactList). Ogni scrittura passa da qui.
- `core/poly_smoother.py` — smoothing laplaciano keep-best, bordo pinnato.

### Numeri misurati (checkMesh reale, da battere)

| Caso | Risultato |
|---|---|
| `ref1` blocco+foro, 251k tet | **Mesh OK**, 100% poly, 0 facce mal orientate, skew 2.25, non-ortho 54.4, 8.8 s |
| venturi coarse | GMSH 39.792 tet → **Mesh OK** → dual 8.074 celle poly → **Mesh OK** |
| cilindro + BL (tools/bench_bl_poly_dual.py) | **505.950 celle prisma + 72.483 poliedri, Mesh OK** |
| `valve1` valvola, 824k tet | **Failed 3 checks**, 852 facce mal orientate, 1027 difetti residui (replica in-process), skew 17.12, non-ortho 95.72, volume conservato ~1e-15 |

La baseline valvola è **pinnata** in `tools/valve_defect_baseline.py`: qualunque
tuo cambiamento va confrontato con quei numeri, non con un ricordo.

---

## 3. Strade GIÀ TENTATE E BOCCIATE — non ripercorrerle

Il difetto della valvola: dove un vertice primale di bordo sta su uno **spigolo
di feature CONCAVO** del CAD, la sua cella duale avvolge l'angolo ed è
genuinamente **non convessa** → il centroide cade fuori da una delle sue stesse
quad di bordo → fallisce il criterio "face pyramids" di checkMesh. Le celle sono
chiuse, a volume positivo e watertight: fallisce un check che ASSUME convessità.
Riguarda 268-462 celle = **0.25-0.29% della mesh**. È una proprietà della
geometria (89 superfici con giunzioni concave), non del tet.

| Tentativo | Esito MISURATO | Stato |
|---|---|---|
| Agglomerazione defect-driven (fondere la cella difettosa nel vicino più sano) | Peggiora in modo monotono: 766 → 839 → 1.225 → 1.835 → 2.929 in 4 round | Codice rimosso. **NON rifare** |
| Split della stella del vertice con angolo diedro NON segnato | 895 → 1.215 (splittava anche gli spigoli convessi sani) | **NON rifare** |
| Split della stella del vertice con test di concavità SEGNATO (`split_rounds`) | Implementato e verificato numericamente (cubo: 0 concavi; scanalatura a L: concavi rilevati). Sulla valvola scatta su 352 vertici ma il keep-best lo scarta: non migliora | Resta nel file, `split_rounds=0` di default |
| Wedge cells "Direction A" (una cella cuneo che POSSIEDE lo spigolo concavo, 614 spigoli) | Implementato, scatta, scartato dal keep-best | `wedge_cells=True` è attivo dal runner ma non risolve |
| `median_faces=True` | Sposta il difetto: 852 piramidi → 766, ma skew 17 → **470**. Nessuno domina | Attivo di default, non è la leva |

**Idea che sembra buona e invece è morta a priori**: triangolare le facce di
bordo difettose. Le quad di bordo sono **planari per costruzione**, quindi ogni
sotto-triangolo giace nello stesso piano e ha lo stesso segno nel test delle
piramidi. Non può funzionare. Non provarci.

---

## 4. Cosa manca DAVVERO — i tre buchi, in ordine di valore/costo

### G1 — Il BL viene messo su TUTTO il boundary, inlet e outlet inclusi
`core/openfoam_runner.py` riga ~1179 chiama il motore BL con `apply_to_all=True`
**hardcoded**. Per la CFD è sbagliato: celle sottilissime addossate a una BC di
pressione/velocità, profilo d'ingresso sballato, celle sprecate. **Colpisce ogni
caso, non solo le geometrie patologiche.**

Perché è così: `bl_poly._select_faces` (riga ~441) auto-chiude la selezione
attraverso gli spigoli, perché un BL parziale lascerebbe seam non-manifold, e
`_build` (riga ~675) solleva `ValueError` se uno spigolo ha ≠2 facce selezionate.
È un limite dell'implementazione, non una necessità geometrica.

### G2 — Il BL si arrende GLOBALMENTE se una zona locale è brutta
`bl_poly.run` prova 5 scale decrescenti `(1.0, 0.5) … (0.1, 0.05)` sull'INTERA
mesh; se nessuna passa la validazione, **niente BL da nessuna parte**. Un pugno
di vertici su feature concave azzera il lavoro su tutto il resto (è il motivo per
cui la valvola resta senza BL). C'è già `angle_fade` (riga ~563) che sfuma
l'ALTEZZA sulle feature, ma il NUMERO di layer è uniforme, e un layer sfumato al
5% resta un layer sottilissimo che fa fallire il criterio delle piramidi invece
di sparire.

### G3 — Il difetto concavo del dual (0.25% celle, checkMesh non-OK)
Vedi §3. Tutte le strade a livello di convertitore sono chiuse con numeri.

---

## 5. IL PIANO — quattro fasi, ognuna con un gate

### FASE 0 — Decidere il criterio di accettazione (FALLA PER PRIMA)
**Domanda**: quel 0.25% di celle rende la mesh inutilizzabile, o è solo un check
che assume convessità su celle che il solver digerisce benissimo?

`tools/poly_solver_validation.py` fa già esattamente questo confronto
(potentialFoam su mesh tet vs mesh poly, stessa geometria, stesse BC: volume,
portata, max|U|, statistiche di pressione) ma è puntato su `ref1`, che passa già.
**Puntalo sulla valvola** (`C:\polybench\valve1`, backup tet già presente).

- Se la poly della valvola risolve, converge e conserva la portata entro qualche
  % → G3 **non è un bug**, è un limite documentato. Tutto il budget va su G1/G2.
- Se diverge o non conserva → G3 torna prioritario, fai la Fase 3.

**Costo**: ore, non giorni — l'infrastruttura è scritta.
**Gate**: numeri del solver riportati (residui, iterazioni, portata in/out,
conservazione) + decisione scritta in `docs/`. **Non saltare questa fase**: può
farti risparmiare la fase più cara del piano.

### FASE 1 — BL selettivo per patch (chiude G1)
Il pezzo tecnico: sullo spigolo di confine tra zona-BL e zona-non-BL, la faccia
laterale del prisma ha UN SOLO prisma incidente. Invece di allargare la selezione
(cosa che fa oggi), quella faccia deve diventare una **faccia di BOUNDARY
assegnata alla patch adiacente** (quella senza BL). È esattamente ciò che fanno
snappy/cfMesh al bordo di una zona di layer: topologicamente pulito, nessun seam
non-manifold.

Conseguenza contabile: `nFaces`/`startFace` della patch non-BL crescono delle
facce laterali nuove, e l'ordinamento delle facce di boundary va rifatto di
conseguenza. È la parte fastidiosa, ma è contabilità, non geometria.

Poi si espone il comportamento giusto: **BL di default solo sulle patch di tipo
`wall`**, mai su inlet/outlet, con override esplicito nella GUI.

- **Verifica**: cilindro con inlet/outlet nominati → BL solo sulla parete,
  `checkMesh` = **Mesh OK**, e `n_prism_cells == n_layers × facce_di_parete`
  (NON × tutte le facce di boundary).
- **Gate**: Mesh OK + conteggio prismi corretto su 2 geometrie diverse.

### FASE 2 — Terminazione locale dei layer (chiude G2)
Passare da altezza-variabile a **conteggio-variabile**: dove i layer non ci
stanno, mettine meno localmente (n → n−1 → … → 0), non assottigliare tutto
ovunque. È il trattamento standard dei codici BL di produzione.

**Attenzione, è una modifica topologica**: due facce di parete adiacenti con n
diverso condividono facce laterali di altezze diverse → serve una faccia di
transizione che chiuda il gradino. È il pezzo di algoritmo vero di questa fase.

Valore collaterale: migliora anche geometrie che oggi passano, perché smette di
assottigliare l'intero BL per colpa di una zona.

- **Gate**: valvola con BL parziale VALIDO (checkMesh) + cilindro invariato
  rispetto ai 505.950 prismi / Mesh OK (regressione).

### FASE 3 — Il difetto concavo (G3) — SOLO se la Fase 0 lo giustifica
Le strade a livello di convertitore sono chiuse (§3). Quella che i numeri
lasciano aperta è **il taglio planare locale**: tagliare la cella avvolta con il
piano bisettore dello spigolo concavo, ottenendo due metà convesse i cui
centroidi cadono dentro. È decomposizione convessa classica.

La difficoltà non è il taglio, è la **conformità**: le celle vicine che
condividono facce con quella tagliata vanno tagliate lungo la stessa traccia del
piano. È bounded (solo le celle che toccano lo spigolo concavo), ma è un
algoritmo nuovo con una campagna di regressione WSL dietro.

- **Costo alto, rischio alto**: è l'unica fase che potrebbe non produrre nulla.
- **STOP RULE**: se il taglio planare non batte **852 facce mal orientate /
  1027 difetti residui** su `tools/valve_defect_baseline.py`, **FERMATI** e
  documenta onestamente: "copertura 100%, qualità migliore su ogni asse del
  convertitore precedente, restano N celle non convesse su giunzioni concave del
  CAD per la ragione Y". **Niente quinto algoritmo.**

---

## 6. Cosa NON fare (misurato, non opinioni)

- **NON modificare il convertitore per far felice il BL**, né la pipeline tet a
  monte (`gmsh_wrapper.py`) per far felice il convertitore. Ogni volta che questo
  vincolo è stato violato è costato una sessione.
- **NON riaprire** `median_faces` / `split_rounds` / agglomerazione / split non
  segnato: A/B già fatti, numeri in §3.
- **NON triangolare le facce di bordo** per il difetto piramidi: planari, morto a
  priori (§3).
- **NON inseguire checkMesh-verde sulla valvola prima della Fase 0.**
- **NON lasciare due implementazioni "per sicurezza"**: un convertitore, un
  writer (`foam_mesh_io`), un parser. È il fallimento che questo lavoro esiste
  per chiudere.
- **NON dichiarare un successo da un solo run** (GMSH non è deterministico).
- **NON cambiare in silenzio** quale motore ha prodotto una mesh: sempre una riga
  di log esplicita.
- **NON sostituire silenziosamente** una mesh fallita con un fallback: la
  decisione di prodotto è **keep-with-warning** (si tiene la mesh, warning
  visibile, backup tet in `constant/polyMesh_tet_backup`).

---

## 7. Comandi di verifica (da repo root)

```powershell
# Test offline veloci (no WSL, no GMSH) — esegui i due NELLO STESSO comando:
# intercetta le regressioni da leak di monkeypatch
& $PY -m pytest tests/test_tet_poly_dual.py tests/test_poly_smoother.py -q --tb=short

# Suite completa
& $PY -m pytest tests/ -q --tb=short

# Baseline valvola pinnata (guardia di regressione, richiede WSL)
& $PY tools/valve_defect_baseline.py
& $PY tools/valve_defect_baseline.py --conv-only   # solo convertitore, no checkMesh

# Verifica numerica del test di concavità segnato (no WSL)
& $PY tools/concavity_verify.py

# BL sul nostro dual, end-to-end con checkMesh (WSL)
& $PY tools/bench_bl_poly_dual.py

# Bench convertitore su caso pronto, riusando la backup tet (WSL)
& $PY tests/bench_tet_poly.py C:\polybench\valve1 --conv dual --reuse

# Validazione col solver (Fase 0)
& $PY tools/poly_solver_validation.py
```

---

## 8. File che toccherai (e quelli che NON devi toccare)

**Toccherai**:
- `src/cfmesh_autogui/core/bl_poly.py` — Fasi 1 e 2 (`_select_faces` ~441,
  `_build` ~511, controllo spigoli ~675, `angle_fade` ~563, fallback scale ~383)
- `src/cfmesh_autogui/core/openfoam_runner.py` ~1159-1199 — passaggio dei
  parametri BL, `apply_to_all` hardcoded
- `src/cfmesh_autogui/gui/main_window.py` — esposizione della scelta patch
- `tools/poly_solver_validation.py` — Fase 0, puntarlo sulla valvola
- `tests/` — nuovi test per ogni fase
- `docs/residual_risks.md` + `docs/poly_mesher_STATO.md` — aggiornare a fine lavoro

**NON toccare senza dichiararlo come cambio separato e revisionato**:
- `src/cfmesh_autogui/core/gmsh_wrapper.py` (pipeline tet a monte)
- `src/cfmesh_autogui/core/tet_poly_dual.py` (algoritmo del convertitore) —
  eccetto la Fase 3, se e solo se la Fase 0 la giustifica
- `src/cfmesh_autogui/core/foam_mesh_io.py` (writer unico, contratto stabile)

**Documenti di contesto da leggere**:
- `docs/poly_mesher_megaprompt.md` — la storia completa e i numeri di ogni
  tentativo (è la fonte di §3)
- `docs/residual_risks.md` §Polyhedral Conversion — limiti accettati
- `docs/poly_mesher_STATO.md` — attenzione: parla del mesher NATIVO CARTESIANO,
  che è un ramo sperimentale SEPARATO (menu Tools), non il percorso di produzione
  descritto qui. Non confonderli.
- `docs/poly_dual_handoff.md` — **parzialmente OBSOLETO**: dice che il test di
  concavità segnato e i wedge cells sono da fare, ma sono già implementati e già
  misurati. Fidati di §3 di questo documento.

---

## 9. Uso dei subagenti — come parallelizzare senza farsi male

**Le fasi sono sequenziali per dipendenza** (la Fase 2 modifica le stesse
strutture della Fase 1): NON lanciare subagenti che scrivono su `bl_poly.py` in
parallelo. Quello che si parallelizza bene:

**Ondata 1 — ricognizione (parallelo, sola lettura, prima di scrivere codice)**
- SA-1: mappa esatta del flusso dati BL — chi chiama `PolyBoundaryLayerEngine`,
  con quali parametri, da dove arrivano (GUI → worker → engine). Output: catena
  di chiamate con file:riga.
- SA-2: mappa della contabilità delle patch in `bl_poly._build` — dove
  `nFaces`/`startFace` vengono ricalcolati, quali invarianti l'ordinamento delle
  facce deve rispettare (interne ordinate per owner/neighbour, boundary in ordine
  di patch). Output: la lista degli invarianti da non rompere in Fase 1.
- SA-3: inventario dei test esistenti che coprono BL e dual — quali falliranno
  se cambio la selezione delle facce. Output: lista di test + cosa asseriscono.
- SA-4: legge `docs/poly_mesher_megaprompt.md` e conferma/smentisce ogni numero
  citato in §3 di questo documento. Output: discrepanze trovate.

**Ondata 2 — Fase 0 (parallelo, indipendenti)**
- SA-5: adatta `poly_solver_validation.py` alla valvola ed esegue.
- SA-6: in parallelo esegue `valve_defect_baseline.py` per confermare che la
  baseline pinnata è ancora valida sul working tree corrente.

**Ondata 3 — implementazione (SEQUENZIALE, un solo agente scrive)**
Fase 1, poi Fase 2. Un subagente alla volta su `bl_poly.py`. Puoi però tenere in
parallelo un subagente **verificatore avversariale** che, dato il diff, prova a
costruire un controesempio geometrico che rompe la nuova selezione delle facce
(patch che si toccano su uno spigolo, patch con una sola faccia, patch non
connessa). Non scrive codice: produce casi di test.

**Regola per ogni subagente**: deve restituire NUMERI e file:riga, non
rassicurazioni. Se un subagente dice "dovrebbe funzionare", il suo output non
vale — rilancialo chiedendo la misura.

---

## 10. IL TUO CONTRIBUTO — blocco obbligatorio da compilare

Non limitarti a eseguire il piano: aggiungi del tuo. Compila e consegna:

```python
CONTRIBUTO = {
    "autore": "<modello/agente, data>",

    "comprensione": """
    <Cos'è il percorso poly+BL, dov'è il difetto residuo, perché le strade in
    §3 sono chiuse. Dimostra di aver capito la differenza fra il percorso di
    produzione (tet GMSH -> dual -> BL) e il mesher nativo cartesiano.>
    """,

    "fase_0_esito": """
    <I numeri del solver sulla valvola: residui, iterazioni, portata in/out,
    conservazione del volume. E la DECISIONE che ne consegue: G3 è un bug o un
    limite documentato?>
    """,

    "cosa_ho_implementato": """
    <Per ogni fase: file toccati, algoritmo, e i NUMERI misurati prima/dopo
    (checkMesh: verdetto, facce mal orientate, skew, non-ortho, aspect, volume,
    tempo; BL: n. celle prisma, spessore totale).>
    """,

    "proposta_personale": """
    <Un'idea tua, tecnica e misurabile, che non era nel piano. Con il numero che
    la sostiene o l'esperimento che la verificherebbe.>
    """,

    "limiti_residui": """
    <Cosa resta aperto e PERCHÉ, con la causa geometrica/algoritmica. Se ti sei
    fermato per la stop rule della Fase 3, dillo con i numeri.>
    """,

    "regressioni": "<test verdi/rossi, e ogni regressione dichiarata>",
}
```

## 11. Consegna attesa

Alla fine mostra: i NUMERI (verdetto checkMesh, facce mal orientate, skew,
non-ortho, aspect, volume, tempo, celle prisma), i file toccati, i test verdi, e
ogni limite residuo con la sua causa. **Niente "dovrebbe funzionare".**
