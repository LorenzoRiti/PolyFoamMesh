# FASE 7 — BL sulla topologia di PRODUZIONE (dual collapsed) vs exact

Data: 2026-09-09. Scritto prima di qualsiasi run. Domanda nata leggendo
`openfoam_runner.py:1652` mentre cercavo il prossimo problema da misurare.

## Il fatto (verificato nel codice, non supposto)

- La produzione (GUI, percorso poly) converte tet->poly con
  `TetPolyDualConverter(case, collapse_smooth_edges=True,
  boundary_feature_angle=40.0, collapse_volume_tolerance=0.10)`
  (`core/openfoam_runner.py:1652`).
- TUTTE le misure BL FASE 2-6 (fixture valvola, bench strumenti,
  stage2e/stage2f) usano `TetPolyDualConverter(case, log=...)` con i
  DEFAULT: `collapse_smooth_edges=False` → dual "exact", tre quad per
  triangolo di bordo. La fixture `tests/fixtures/valve_dual.npz` è stata
  generata allo stesso modo (`tools/poly_fixture_builder.py:67`, default).
- Conseguenza: nessuna misura BL finora è stata fatta sulla topologia che
  l'app consegna davvero. Il dual collapsed ha ~3-6x MENO facce di bordo
  (una poligonale per vertice di bordo, con feature edges mantenuti) e
  `bl_poly` estrude UN stack di prismi per faccia di bordo → prismi,
  esclusioni e difetti vanno rivisti.

## Previsioni (da confrontare con i numeri)

Valvola (stesso tet backup `C:/polybench/valve1`, stesso protocollo
n_layers=2, h1=1e-5, dual_convexity, apply_to_all=True):

1. **Celle dual**: identiche tra exact e collapsed (il collasso tocca solo
   le facce di bordo) — atteso ~152.086 per entrambe.
2. **Facce di bordo**: exact ~226.542 (26% del totale facce); collapsed
   previsto **40.000-80.000** (rapporto 3-6x, come il caso utente
   462.846 → 80.111, 5.8x).
3. **Drift di volume del converter (collapsed)**: < 1% (misurato
   -0,19%/-0,46% sui venturi, -0,00% sulla mesh utente). L'EXACT è
   ~1e-16.
4. **Difetti input (face pyramids, in-process)**: INCERTO. Il test
   piramide guarda facce (incluse le poligonali di bordo) contro il
   centroide di cella; fondere 3 quad in 1 poligonale può cambiare
   l'esito. Previsione larga: **600-1.400** (exact = 1.007, misurato).
5. **BL `decoupled`+`early_exit` sul collapsed**: previsione
   **successo alla scala 0.1 (70% di confidenza)**, esclusioni
   **< 8% delle facce di bordo** (in unità di facce sarà un numero
   piccolo, ~1.000-5.000, perché le facce sono 3-6x meno), prismi
   ~2x le facce rimaste; checkMesh "wrong oriented" **non peggiore di
   895** (50% di confidenza: la fusione poligonale può spostare il
   conteggio in entrambe le direzioni).
6. **Tempo**: conversione collapsed più lenta dell'exact (walk delle
   poligonali), BL più veloce (meno facce da estrudere). Totale stimato
   10-25 min per la valvola.

Cilindro (GMSH, protocollo n_layers=3, h1=0.005):

7. Facce di bordo exact ~24.936 → collapsed previsto **~4.000-6.000**;
   BL successo a scala 1.0 con 0 esclusioni; prismi ~3x facce rimaste;
   `Mesh OK` in entrambi.

## Perché conta

Se il BL funziona anche sul dual collapsed, le misure FASE 2-6 si
trasferiscono alla produzione (con numeri diversi ma stessa storia).
Se NON funziona (o degrada), allora la modalità `local_termination` è
tarata su una topologia che l'app non usa, e il percorso di produzione
poly+BL su geometrie concave è di fatto non validato — un risultato
negativo critico da scrivere subito.

## Criterio di lettura

- Confronto a tre livelli: statistiche converter (facce bordo, drift),
  difetti input, esito BL (successo, scala, esclusioni, prismi, tempo,
  checkMesh reale).
- Se il collapsed non chiude nemmeno con early_exit: provare `decoupled`
  puro e, se fallisce anche lui, documentare il fallimento con i numeri
  (e NON toccare l'engine in questa fase).

# RISULTATI (compilati dopo i run, 2026-09-09)

## Misure

| | exact (default) | production (collapsed) |
|---|---|---|
| cilindro facce bordo | 24.936 | **4.412** (5,65x) |
| cilindro drift | 1,4e-16 | 0,128% |
| cilindro BL early_exit | scala 1.0, 0 escl, 74.808 prismi, 13,0 s, **Mesh OK** | scala 1.0, 0 escl, 13.236 prismi, 5,6 s, **Mesh OK** |
| valvola facce bordo | 226.542 | **42.130** (5,4x) |
| valvola difetti input (wrong) | 863 | **340** |
| valvola drift | 3,4e-16 | 1,08% |
| valvola BL | **successo scala 1.0** (2 build): 2.525 escl, 444.924 prismi, 217 s; checkMesh 836 wrong (< 863), skew 38,1, aspect 1.543, Failed 4 | **FALLISCE**: decoupled 15 build, 892,7 s, 2.189 escl, stallo 375-444 pyr vs 370 input |

## Previsioni vs misure (onesto)

- P1/P2 (celle identiche; facce bordo 40-80k): ✓ (152.086 celle; 42.130 collapsed).
- P3 (drift < 1%): ✓ (1,08% valvola è appena sopra; 0,128% cilindro).
- P4 (difetti 600-1.400): ✗ — misurati **340** (il collapse RIDUCE i difetti input).
- P5 (BL chiuso al 70%): ✗ — **fallisce** sulla topologia di produzione; il
  30% previsto si è materializzato.
- P6 (tempo): conversione collapsed più veloce (100-115 s vs 137-140 s),
  BL più veloce sui casi sani; sulla valvola production il BL decoupled
  ha speso 892,7 s per fallire.
- P7 (cilindro): ✓ identico alla previsione.

## Scoperte collaterali (più importanti del risultato principale)

1. **La fixture valvola è stale** (2026-08-01): 895 wrong / 55 err. non-ortho /
   skew 44,3 vs conversione fresca 863 / 9 / 14,6. Tutti i numeri valvola
   FASE 2-6 sono pinned a quello snapshot; sul converter attuale la
   valvola exact valida a **scala 1.0** con 2.525 esclusioni (non a 0.1
   con 2.058).
2. **Bug dell'early-exit FASE 6**: il trigger scattava a 1.0 round 0 e
   saltava anche i round 1-2 della scala richiesta; mascherato dalla
   fixture stale, era la causa della "degenerazione" su slot1. Corretto:
   il trigger scatta solo a scala 1.0 esaurita. Ri-misura valvola exact:
   successo 1.0 round 1, 217 s (identico al legacy).

## Conclusione

- La topologia di produzione (collapsed) funziona sui casi sani ma **NON**
  supporta la BL sulla valvola con il current local termination.
- Prima di qualsiasi altro lavoro valvola: rigenerare la fixture e
  ri-pinnare i numeri; poi lane dedicata per la mappatura
  esclusioni→poligoni (o granularità mista).
- Nessun default cambiato; la correzione dell'early-exit è interna al modo
  opt-in.
