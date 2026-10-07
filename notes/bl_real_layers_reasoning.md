# Boundary layer reali sul percorso poly (2026-10-06)

Riferimenti: commit `d654cdb`, `8f4b0e4`, `2ea4191`. Verifica sul caso
reale dell'utente (`Part2.stp`) e sulla suite BL (81 test) + cilindro.

## Sintomo

Sul Part2 il log diceva "14 layers, local 1..1" con spessore totale
0.00049 m: il BL veniva **accettato come successo** ma era un guscio da
1 strato, invisibile a scala parte e inutile per lo y+ target. In altri
casi il motore **comprimeva** 14 strati in 1.17 mm (primo strato 0.03 mm,
y+ sbagliato di 20x) senza dirlo.

## Cause radice (tutte misurate)

1. **Il fixpoint di consistenza è un MIN sull'intera parete connessa.**
   Un solo vertice con budget ridotto (`max_h` piccolo) appiattisce a 1
   layer tutte le facce raggiungibili per vertici condivisi. Sul Part2:
   `support_d` p50 = 2.35 mm, stack richiesto 43 mm → 45% dei vertici con
   budget 1 layer a clamp 0.35 → mediana 1 su 13.081 facce.
   Il budget per-vertice è `max_h = angle_fade * min(clamp*d,
   0.5*edge_cap)` (bl_poly `_build`, ~riga 2109).
2. **La validazione era cieca ai layer effettivi**: nessun controllo su
   mediana/copertura → `local 1..1` passava (volume conservato, checkMesh
   pulito).
3. **`drop_bnd` con unità sbagliate**: chiavi = id locali di faccia
   (`fi - n_int`), test = POSIZIONE nella selezione filtrata (`bi in
   drop_bnd`). Con `min_base_edge_fraction=0.9` (il default "BL
   robusto") le posizioni divergono dagli id e il test flaggava facce a
   caso. Il golden del valve (75.360 prismi) codificava il bug.
4. **Misura volume Newell/centroide** approssimata su facce non planari
   (n-goni del dual): 1.16e-3 di "errore" sul Part2 che era invece la
   shell realmente ceduta sotto le facce escluse dal filtro sliver.

## Fix

- **Verità nel result** (`PolyBoundaryLayerResult`): istogramma layer per
  faccia (`layer_hist`), mediana pesata (`median_layers`), copertura %,
  `stats["first_layer_effective_m"]` e `first_layer_ratio`. Log onesto:
  `N prism cells (K layers requested, median ..., coverage ...%, first
  layer X (requested Y))`.
- **Guardia di utilità**: un build valido ma appiattito (mediana 1 con
  n>=2 richiesti) viene RIFIUTATO con warning esplicito; il percorso di
  fallback prosegue.
- **Cap mesh-aware** (`_supported_layers`): con `terminate_by_budget`,
  `n_layers` viene limitato al massimo k che almeno il 90% dei vertici
  regge a piena altezza (`capped N -> k` nel log). Sul Part2: 14 → 1 con
  h1 pieno (y+ esatto), 14 → 2 con h1/2.
- **Semantica binaria nel percorso standard** (`zero_concave=True` quando
  `terminate_by_budget`): faccia = stack pieno (compresso solo dove
  `max_h` morde) o esclusa; niente percolazione del fixpoint.
- **Volumi esatti** (`_exact_cell_volumes`, triangolazione a ventaglio
  dal centroide di faccia) per positività/conservazione/min-volume/ratio.
  Tolleranza conservazione 5e-3: documenta la shell ceduta by-design
  sotto le facce escluse (Part2 peggiore: 1.16e-3); un buco reale fallisce
  prima il test di chiusura a 1e-8.
- **`drop_bnd` tradotto in posizioni** all'ingresso di `_build`.
- **Primo strato effettivo riportato**; warning se < 50% del target
  fisico ("y+ off by the same factor").

## Verifica (Part2.stp, mesh dual 31.775 celle, 14.746 facce di bordo)

| Tentativo | Esito | Prismi | Layer | Primo strato | checkMesh |
|---|---|---|---|---|---|
| h1=0.000728 (pieno) | capped 14 → 1 | 13.081 | 1 su 100% | 0.000728 (ratio 1.00) | PASS (44.867 celle, NO 57.74, skew 1.649, neg 0) |
| h1=0.000364 (h1/2) | capped 14 → 2 | 26.162 | 2 su 100% | 0.000364 (ratio 1.00) | PASS (57.948 celle, NO 58.52, skew 1.654, neg 0) |

- Prima: BL finto a 1 layer accettato, oppure BL rifiutato del tutto.
- Cilindro (test collapsed boundary): invariato, 3 layer su ogni faccia.
- Valve production (golden): 76.052 prismi (era 75.360 col bug unità),
  scala 0.6 e 4.008 esclusioni invariate.
- Suite: 58 test engine+cilindro verdi, 81 test BL verdi, fast suite
  1283 passed.

## Limite architetturale (per il prossimo programma)

Lo stack non può superare la profondità della prima cella dual
(`support_d`): il BL riusa solo il primo strato di celle, non ne consuma
di interne (a differenza di snappy). Sul Part2 `support_d` p50 = 2.35 mm
→ massimo ~1.17 mm di stack a clamp 0.5. Per BL più spessi servono:
- celle di superficie più grosse (il warning del cap lo suggerisce), o
- un BL che consuma più strati di celle interne (cambio grande).

## Chiusura concava automatica (commit 2ea4191)

La checkbox "Chiusura avanzata per geometrie concave (sperimentale)" è
stata rimossa: `local_termination="decoupled_vertex"` (H4) è ora un
fallback AUTOMATICO della catena del worker:

```
1. standard robusto (binario + cap)         ← ora gestisce da solo i casi concavi
2. chiusura concava (decoupled_vertex)      ← solo se il passo 1 fallisce
3. primo strato dimezzato (0.5, poi 0.25)   ← ultima spiaggia
```

Motivo: la LT girava per prima e degradava i casi che lo standard nuovo
gestisce; resta l'unico percorso che chiude il layer su giunzioni concave
(groove1/slot1, valvola production — vedi `notes/fase3_reasoning.md`).
La guardia memoria (8M facce / 1.5M celle) ora protegge il retry della
chiusura. Il flag legacy `concaveClosure` resta solo per il merge repair
post-BL (off di default; era misurato in regressione su groove1).

## Follow-up prima del rilascio

- Ri-validare il benchmark 9/10 vs snappy con la nuova semantica BL (il
  punteggio era stato misurato con la vecchia: i casi concavi passavano
  anche col BL fallito).
- Rimisurare end-to-end groove1/slot1 con la catena nuova (il fallback
  automatico è coperto dai test unitari, non da una misura reale).
- Viewer: colorazione delle celle prism (visibilità BL) — oggi il viewer
  colora per patch.

## BL completi - complete-coverage policy (2026-10-07)

Misura sul run reale dell'utente (gmsh_direct_20261007_091301, 279.894
celle, n=6, h1=9 mm, robust OFF, solo patch parete surface_2): la parete
aveva copertura BL **95.0%** - 5.883 facce senza prisma ("BL estrusi solo
in parte"). Causa: la semantica binaria (zero_concave) scartava le facce
con ngle_fade<0.5 o max_h<=0, e il fixpoint (percorso non-binario)
azzerava le facce sui vertici senza budget.

Fix in l_poly._build:
- il loop standard usa **sempre** la semantica binaria (prima solo in
  modalita' budget): il fixpoint e' un MIN sull'intera parete connessa e
  qualunque vertice a budget ridotto la inonda a 1 layer;
- si scartano **solo** le facce con difetto d'input reale (drop_bnd);
- i vertici concavi / a budget zero tengono la faccia con stack
  **compresso** (sottile ma presente) e un floor di altezza minima
  (1e-3 * h1) evita i prismi degeneri.

Verifica (Part2 dual, stessa config): surface_2 coverage **100.0%**,
86.154 prismi, 6 layer su 14.359 facce, checkMesh PASS (NOmax 70.15,
skew 1.741, neg 0). Trade-off dichiarato nel log: primo strato compresso
al 2% del richiesto (la mesh vicino alla parete regge ~1.2 mm di stack;
per y+ 30 serve una mesh di superficie piu' grossa). Suite BL 58 verdi.
