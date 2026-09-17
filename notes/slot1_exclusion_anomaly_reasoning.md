# Slot1 decoupled_vertex exclusion anomaly: prediction

Data: 2026-09-11. Scritto PRIMA di guardare i dati del bench
(`C:/polybench2/h4_generalization/result.json`). Obiettivo: capire
perché su slot1 `decoupled_vertex` esclude 378 facce contro 132 di
`decoupled` (≈3x), mentre su cube_duct e groove1 i due modi sono
identici (0 esclusioni in entrambi).

## Misure note (H4 generalizzazione, 2026-09-11)

| geometry | pyr_input | decoupled escluded | decoupled_vertex escluded | ratio |
|---|---|---|---|---|
| cube_duct | 0 | 0 | 0 | 1.0x |
| groove1 | 0 | 0 | 0 | 1.0x |
| slot1    | 22 | 132 | 378 | 2.86x |

## Predizione (scritta prima di ispezionare i dati)

La causa strutturale che sospetto è la **propagazione per-vertice
sulla topologia locale di slot1**. I 22 difetti piramide in input su
slot1 sono localizzati (tipico di geometrie a fessura stretta), e i
vertici di parete del dual collapsed di slot1 sono ad **alta valenza
condivisi** tra molte facce incidenti (per via della sezione
trasversale piccola). Il cascade per-faccia (face-mode) esclude
esattamente le facce associate ai prismi difettosi (~132); il cascade
per-vertice (vertex-mode) propaga l'esclusione a TUTTE le facce
incidenti a ciascun vertice "cattivo", e ad alta valenza di condivisione
questo set incidenti è ~3x più grande del set di facce dirette. Su
cube_duct e groove1 la topologia è semplice (nessun difetto in input,
cattura del cascade vuota → identico).

Quindi la mia ipotesi operativa: **il fattore 3x è proporzionale alla
topologia locale (alta valenza di condivisione di vertici tra facce
incidenti a difetti), non al numero di difetti in sé**. Conseguenze:
1. la qualità mesh (skewness, aspect ratio) non peggiora — vertex-mode
   esclude un superset di prismi "a rischio" che il face-mode aveva
   mantenuto, quindi la mesh risultante è uguale o leggermente
   migliore;
2. il "costo" in numero di prismi persi (711) è proporzionale alla
   topologia locale, non un difetto generale del criterio vertex.

## Come ispezionerò i dati

Per verificare l'ipotesi devo:
1. Estrarre da `result.json` i set di esclusioni di `decoupled` e
   `decoupled_vertex` su slot1 (se il tool li ha salvati: ha salvato
   `excluded_iterative` come conteggio, non il set di facce).
2. Se non ho i set salvati: ri-eseguire `local_termination="decoupled"`
   e `local_termination="decoupled_vertex"` su slot1 con strumentazione
   aggiuntiva (instrumentare l'engine per loggare `len(added)` per
   round e i set di facce), per poi fare l'intersezione e la differenza
   dei set e verificare la propagazione topologica.
3. Valutare se i 711 prismi esclusi in più da vertex-mode sono
   concentrati (vicino ai 22 difetti input) o distribuiti, e
   quantitativamente se la mesh risultante ha skewness/aspect
   comparabili o migliori.

## Cosa mi aspetto di trovare

- `decoupled_vertex_excluded ∖ decoupled_excluded` non vuoto e di
  dimensione ~246 (= 378 − 132; alcuni sono già nell'intersezione).
  Inoltre: i vertici esclusi da vertex-mode ma non da decoupled sono
  condivisi tra più facce del cluster di difetti.
- La qualità mesh (skewness, aspect ratio) è simile o leggermente
  migliore, perché i 711 prismi persi erano "borderline" (alti aspect
  ratio, skewness) — escluderli migliora la qualità aggregata. Il
  `max_skewness`/`max_aspect_ratio` aggregato non cattura la differenza
  perché si tratta del max; serve una distribuzione (es. istogramma di
  skewness) per vedere se vertex-mode riduce la coda pesante.

## Onestà

Se l'ispezione dei dati non conferma l'ipotesi (es. le 246 facce in più
sono distribuite, non concentrate, o la qualità peggiora
significativamente), l'ammissione onesta sarà: la differenza 3x su
slot1 è probabilmente un effetto del cascade vertex su input
borderline, e documenterò la misura senza pretendere di aver isolato la
causa.

Nessun default cambiato, nessuna modifica all'engine o alla GUI.
Il deliverable è un paragrafo in `docs/residual_risks.md` (append alla
sotto-sezione H4 generalization).
