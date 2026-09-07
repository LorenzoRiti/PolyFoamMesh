# PROMPT — Consolidare tet→poly in UN solo convertitore fatto bene

Questo è il prompt di lavoro. Leggilo tutto prima di toccare un file.

---

## 0. Contesto operativo

Repo: `C:\Users\Davide Valoroso\cfmesh-autogui`, branch `master`, base `1513a39`.

- **Python con le dipendenze**: `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`.
  Il `python` di default sul PATH **non ha** cadquery/gmsh/PySide6. Sbagliare
  interprete è il primo modo di perdere un'ora.
- **OpenFOAM 2512** in WSL2 Ubuntu: `source /usr/lib/openfoam/openfoam2512/etc/bashrc`.
  Attenzione: `cd` verso path con spazi va **quotato**, altrimenti bash dà
  "too many arguments" in silenzio.
- **Casi già pronti** in `C:\polybench\` (`ref1..ref3`, `valve1`, `valve2`,
  `smoke`), ognuno con `constant/polyMesh_tet_backup`: si riparte dalla mesh tet
  identica senza rifare GMSH. **Usarli** — rigenerare la mesh invalida ogni
  confronto, perché GMSH non è deterministico.
- Geometria reale: `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp`
  (89 superfici, 3.0 × 0.255 × 0.255 m, 750k–2.2M tet secondo il detail).

**Regola non negoziabile**: ogni affermazione sulla qualità della mesh va
sostenuta da un `checkMesh` reale con numeri reali. Mai "dovrebbe funzionare".
Confronti solo sulla **stessa** mesh tet. Ogni risultato va riprodotto 2–3 volte
prima di chiamarlo stabile.

---

## 1. Da dove si parte (stato misurato, non opinioni)

Esistono **tre** implementazioni tet→poly nel repo. È il problema principale.

| file | cos'è | stato |
|---|---|---|
| `core/tet_poly_dual.py` | dual baricentrico, ~1.200 righe | funziona, è il migliore misurato, agganciato alla GUI |
| `core/terminal_face.py` | merge di tet (Salinas 2023), 3 varianti | legacy, peggiore su ogni asse |
| `commercial/tet_poly_volume.py` | tentativo parallelo di un altro agente, ~31 KB | **non lo importa nessuno**, mai misurato |

### Numeri, stessa mesh tet per entrambi i convertitori

**Blocco con foro cilindrico, 251.048 tet**

| | terminal-face | **dual** |
|---|---|---|
| copertura poly | 80,3% (27.420 tet residui) | **100,0%** |
| checkMesh | Failed **2** | **Mesh OK** |
| facce mal orientate | **1.073** | **0** |
| max skewness | 4,93 (fail) | 2,25 |
| max non-ortogonalità | 88,06 | 54,43 |
| max aspect ratio | 6,98 | 4,47 |
| celle in uscita | 139.227 | 47.528 |
| tempo | 17,6 s | 8,8 s |

3 run indipendenti del dual (mesh GMSH nuova ogni volta: 251.048 / 251.508 /
251.715 tet) → **Mesh OK, 100%, 0 facce mal orientate** tutte e tre.

**Valvola `Parte4.stp`, 824.661 tet**

| | terminal-face | **dual** |
|---|---|---|
| copertura poly | 80% | **100,0%** |
| checkMesh | Failed **4** | Failed **3** |
| facce mal orientate | **3.545** | **895** |
| max skewness | 34,94 | 44,33 |
| errori non-ortogonalità | 5 | 55 |
| max aspect ratio | **1.240 (FAIL)** | 779 (OK) |
| tempo | 62,7 s | 33,7 s |

Secondo run indipendente (1.036.918 tet): 100%, 854 facce, 42 errori
non-ortogonalità, skew 72,1, Failed 3, **37,7 s** (≈36 s per milione di tet).

### Perché il dual è l'algoritmo giusto

Non fonde tet. Costruisce il **complesso duale**: una cella per **vertice**
primale, una faccia interna per **spigolo** primale, e sul bordo una
suddivisione **esatta** dei triangoli di superficie originali. La copertura al
100% è strutturale, non ottenuta a forza di soglie.

Il "clipping del bordo contro il CAD" che `poly_workflow_part2.md` dava per
quasi irrisolvibile **non esiste** in questa costruzione: vale solo per un duale
di **Voronoi/circocentrico**. Il duale **mediano** usa punti che stanno già
esattamente sulla superficie primale, quindi ogni triangolo di bordo (a,b,c) si
divide nei tre quad planari `[a, mid(ab), centroid(abc), mid(ca)]` che lo
piastrellano esattamente. Bordo in uscita identico al bordo in ingresso, patch
per patch. Volume conservato a precisione macchina (drift misurato 0 – 1,1e-16).
Anche l'orientamento è geometrico e non ambiguo: la faccia duale dello spigolo
(a,b) separa esattamente le celle di a e b, quindi la normale **deve** puntare
da a a b — non c'è nessuna euristica di winding da sbagliare.

### Perché la valvola non passa — diagnosi misurata

Delle 766–895 piramidi di faccia invertite:

- **705/766 sono facce di bordo**, non interne;
- le celle che falliscono hanno **le proprie normali di bordo che spaziano
  90–134°**, contro **1,1°** di una cella di bordo tipica;
- il loro **volume è normale** (1,79e-9 contro mediana 2,32e-9) → **non** è un
  problema di sliver né di qualità del tet.

Conclusione: dove un vertice di bordo primale sta su uno **spigolo di feature
concavo del CAD**, la sua cella duale avvolge la feature ed è genuinamente
**non convessa**, quindi il baricentro della cella cade fuori da uno dei suoi
stessi quad di bordo. La cella resta chiusa, a volume positivo e a tenuta: fallisce
un check che **assume convessità**. Riguarda 268–462 celle = **0,25–0,29%**.

### Due riparazioni già provate e bocciate — NON rifarle

1. **Agglomerazione defect-driven** (fondere la cella difettosa nel vicino più
   sano, coppie disgiunte, iterare): peggiora in modo monotono,
   766 → 839 → 1.225 → 1.835 → 2.929. Fondere due celle duali produce una cella
   non convessa più grande. Riproduce indipendentemente il muro della sessione
   terminal-face: **fondere non sistema la forma.** Codice rimosso.
2. **Split della stella del vertice** (`split_rounds`, ancora nel file, default
   0): 895 → 1.215 → 1.218. La costruzione è corretta (a tenuta, conserva il
   volume, tutti gli invarianti passano) ma non paga: spezzare una cella
   avvolgente in cunei scambia una cella non convessa con parecchie sottili che
   si invertono per conto loro. **Ha però un bug noto e mai corretto**: usa
   l'angolo diedro **non segnato**, quindi splitta anche gli spigoli **convessi**
   a 90° che stavano benissimo. Vedi Fase 3.

### L'infrastruttura che vale più del codice

`tet_poly_dual.py` contiene una replica in-process delle metriche di checkMesh
(`_cell_centres`, `_detect_defects`) che **coincide esattamente** con checkMesh
sul conteggio delle piramidi invertite (895 previste / 895 riportate). È questo
che rende le misure sopra affidabili senza un round trip in WSL, ed è da
preservare e testare.

---

## 2. La decisione da prendere

**Un solo convertitore: il dual.** Non un flag, non due strade, non un fallback
al terminal-face. Le ragioni sono misurate, non estetiche: sulla stessa mesh il
dual domina su copertura, facce mal orientate, aspect ratio, tempo, e su entrambe
le geometrie. Non esiste uno scenario in cui il terminal-face è la scelta giusta.

Mantenere due convertitori è esattamente la classe di confusione ("quale ha
prodotto questa mesh?") che è già costata una giornata a questo progetto.

---

## 3. Il piano

### Fase 0 — Consolidamento (mezza giornata)

**Obiettivo: una sola implementazione, zero codice morto, banco di prova nel repo.**

1. **Decidere su `commercial/tet_poly_volume.py`.** Prima leggerlo davvero, poi
   misurarlo con lo stesso banco sulla stessa mesh di `C:\polybench\ref1`. Se non
   batte il dual (previsione: no), **cancellarlo**. Se ha un'idea migliore su
   qualche aspetto, portarla nel dual e poi cancellarlo. Non lasciare due duali.
2. **Estrarre l'I/O in `core/foam_mesh_io.py`.** Oggi `tet_poly_dual.py` importa
   `TerminalFaceConverter` solo per usarne i lettori statici — dipendenza
   architetturalmente sbagliata. Spostare lì: lettura points/faces/owner/
   neighbour/boundary (ASCII **e binario**, `faceList` **e** `faceCompactList`,
   incluso il fix di `_binary_header_parse` del commit `1513a39`) e la scrittura
   bulk. Far puntare lì entrambi i convertitori.
3. **Ritirare il terminal-face dal percorso poly.** Rimuovere la voce di menu
   `Tools → Polyhedral Converter` e il dispatch a doppio worker. Il modulo può
   restare in repo marcato legacy, ma non deve più essere raggiungibile dalla GUI.
   Cancellare le varianti già morte al suo interno (`_merge_leftover_tets`,
   `_merge_leftovers_safe`, `_augment_disjoint_pair_matching`): i loro numeri
   sono documentati, il codice non serve più.
4. **Portare il banco di prova dentro il repo** come `tests/bench_tet_poly.py`.
   Oggi vive in una scratchpad di sessione e sparirà. Deve: costruire il caso da
   STEP, `gmshToFoam`, salvare/ripristinare `polyMesh_tet_backup`, convertire,
   `checkMesh`, ed emettere una **riga di risultato machine-readable** (JSON) con
   copertura, verdetto, facce mal orientate, skewness, non-ortogonalità, aspect
   ratio, volume, tempo. Unificare con `tests/bench_tetpoly_dual.py` dell'altro
   agente, non affiancarlo.

**Criterio di uscita**: un solo `TetPolyConverter` importabile; `ref1` e `valve1`
riprodotti con i numeri della tabella sopra tramite il banco nel repo.

### Fase 1 — Rete di sicurezza prima di toccare l'algoritmo (mezza giornata)

Serve **prima** di modificare la geometria, altrimenti non si sa se un cambio ha
migliorato o rotto.

1. **Test unitari** su mesh minuscole costruite a mano (niente GMSH, niente WSL):
   - un singolo tet → una cella duale per vertice, chiusa, volume positivo;
   - due tet che condividono una faccia;
   - un cubo tetraedrizzato: verifica chiusura (Σ vettori area = 0 per cella),
     volume totale conservato, ordinamento upper-triangular, `owner < neighbour`,
     nessun punto inutilizzato, ogni triangolo di bordo → esattamente 3 quad
     nella patch giusta;
   - rifiuto di un input non-tet (prismi/boundary layer) con messaggio chiaro;
   - rollback: se la scrittura fallisce, il `polyMesh` originale resta intatto.
2. **Test di regressione delle metriche**: verificare che
   `_detect_defects` continui a coincidere con checkMesh sul conteggio delle
   piramidi (oggi 895/895 sulla valvola). Se questa corrispondenza si rompe,
   ogni misura successiva diventa inaffidabile.

**Criterio di uscita**: i test girano in < 30 s senza WSL e senza GMSH.

### Fase 2 — Validazione che conta davvero: far girare un solver (mezza giornata)

**Questo è il pezzo che oggi manca completamente e vale più di chiudere le 895
facce.** `checkMesh: Mesh OK` è geometria, non dice che ci si può calcolare sopra.

1. Sul caso blocco, mesh poly: impostare un caso reale (`potentialFoam` come
   smoke, poi `simpleFoam` laminare o `scalarTransportFoam`) e **farlo
   convergere**. Registrare residui e numero di iterazioni.
2. Fare lo stesso sulla mesh **tet** dello stesso caso e confrontare: portata,
   caduta di pressione, volume totale. Devono coincidere entro tolleranza fisica.
   È questo che dimostra che la conversione è conservativa in pratica, non solo
   in `checkMesh`.
3. Ripetere sulla valvola. Se converge nonostante i 3 check falliti, è
   un'informazione **enorme**: significa che la barra corretta non era `Mesh OK`.
   Se diverge, si sa esattamente perché la Fase 3 conta.

**Criterio di uscita**: un caso poly che converge, con numeri confrontati contro
il tet, salvati nella documentazione.

### Fase 3 — Chiudere il divario sulla valvola (1–2 giorni, con stop rule)

Il difetto è la non convessità su feature **concave**. In ordine di costo
crescente. **Fermarsi al primo che funziona.**

1. **Split solo sui vertici concavi** (il lead economico mai testato).
   Il tentativo esistente usa l'angolo diedro non segnato e quindi splitta anche
   gli spigoli convessi, che stavano bene — probabile causa del peggioramento
   895→1.215. Serve un test di concavità **con segno**: per uno spigolo di bordo
   (a,b) condiviso dai triangoli T1 (normale uscente n1, vertice opposto p1) e T2
   (n2, p2), lo spigolo è concavo se `(p2 − centroid(T1)) · n1 > 0`.
   **Verificare numericamente questo test su un cubo (tutti convessi) e su una
   scanalatura (concavi noti) prima di usarlo** — sbagliare il segno qui fa
   sembrare fallita l'idea giusta, che è esattamente quello che è già successo.
   Poi splittare solo i vertici che sono (a) incidenti ad almeno uno spigolo
   concavo **e** (b) attualmente difettosi.
2. **Se i cunei restano troppo sottili**: partizionare solo lo strato di tet a
   contatto col bordo invece di propagare il taglio per tutta la stella. Le celle
   risultanti sono più compatte.
3. **Se anche questo fallisce**: il problema non è nel convertitore, è a monte.
   Valutare defeaturing o una mesh tet consapevole degli spigoli di feature —
   e questo va aperto come **cambio separato e revisionato**, non nascosto nel
   commit del convertitore.

**Stop rule**: se dopo il punto 2 la valvola non passa, **fermarsi e riportare
onestamente**. Non inventare un quarto algoritmo di conversione. "Copertura
100%, qualità migliore del convertitore precedente su ogni asse, restano N celle
non convesse su giunzioni concave del CAD per la ragione Y" è un risultato
accettabile e prezioso. Continuare a inventare euristiche è ripetere il muro di
ieri con un altro nome.

Da provare in combinazione, non da solo: `median_faces=True`. A/B sulla valvola:
`False` → 895 facce / skew 44,3; `True` → 766 facce / skew 470. Nessuno dei due
domina; potrebbe cambiare in combinazione con lo split corretto.

### Fase 4 — Prodotto: renderlo onesto in GUI (mezza giornata)

1. **Un solo percorso poly**, nessun toggle, con la riga di log che nomina sempre
   il convertitore e l'esito (già implementata, va mantenuta).
2. **Dire il cambio di conteggio celle.** Si passa da 824k tet a 152k celle poly.
   È il normale e desiderabile guadagno del poliedrico, ma senza spiegarlo la
   prima reazione è "mi ha buttato via l'80% della mesh". Il log deve dire
   celle prima → dopo, copertura poly, e che per una data risoluzione target
   bisogna **meshare più fine a monte**.
3. **Quality gate esplicito.** Decidere e implementare cosa succede quando
   `checkMesh` fallisce sulla mesh poly: tenerla con warning visibile, oppure
   fare rollback al tet. **È una scelta di prodotto, va chiesta a Lorenzo, non
   decisa dal codice.** Qualunque sia, deve essere visibile nel log e reversibile
   (il backup tet esiste già).
4. Controllare che `_auto_quality_fix()` (~riga 3133 di `main_window.py`) resetti
   il proprio stato fra un tentativo e l'altro: quella classe di bug ha morso due
   volte in una sola sessione.

### Fase 5 — Documentazione e chiusura

Un solo documento, `docs/poly_converter.md`, che sostituisce
`poly_workflow_part2.md`, `poly_dual_converter.md` e `poly_dual_handoff.md`.
Deve contenere: l'algoritmo e **perché** il bordo non richiede clipping; la
tabella dei numeri misurati; le riparazioni bocciate **con i loro numeri**; il
limite residuo e la sua causa geometrica; come riprodurre tutto.
Aggiornare `poly_workflow_part2.md` con un rimando, non lasciarlo a contraddire
lo stato reale.

---

## 4. Criteri di accettazione finali

Non considerarlo finito finché, su **entrambi** blocco e valvola, non puoi
mostrare:

1. **Copertura poly**: percentuale reale. Non arrotondata per eccesso, non il
   migliore di più run.
2. **checkMesh**: la riga `Failed N mesh checks` **completa**, o `Mesh OK`.
3. **Facce mal orientate**: conteggio esatto, confrontato con il terminal-face
   sulla **stessa** mesh tet (1.073 sul blocco, 3.545 sulla valvola).
4. **Max skewness e max non-ortogonalità**: valori esatti, segnalando
   esplicitamente se sono categorie di fallimento **nuove**.
5. **Un solver che converge** su mesh poly, con i risultati confrontati contro
   la mesh tet (Fase 2).
6. **Tempo** a scala reale (1–2M tet). Riferimento attuale: ≈36 s per milione.
   Non regredire di un ordine di grandezza senza dichiararlo come trade-off.
7. **Stabilità**: 3 run sullo stesso input, numeri riportati tutti e tre.
8. **Un solo convertitore** raggiungibile dalla GUI, e nessun modulo duale
   orfano nel repo.

---

## 5. Cosa NON fare

- Non aggiungere un quarto algoritmo di conversione. Se Fase 3 punti 1–2
  falliscono, il problema è a monte del convertitore.
- Non rifare agglomerazione o split non segnato: entrambi misurati, entrambi
  peggiorativi, numeri sopra.
- Non lasciare due implementazioni "per sicurezza". È il fallimento che questo
  piano esiste per chiudere.
- Non toccare la pipeline di generazione tet (`gmsh_wrapper.py`,
  `openfoam_runner.py` lato GMSH, il flusso tet di `main_window.py`) per
  "aiutare" la conversione poly. Se serve, è un cambio trasversale separato e
  revisionato a parte.
- Non dichiarare un successo da un solo run: GMSH non è deterministico.
- Non cambiare in silenzio quale convertitore ha prodotto un risultato.
