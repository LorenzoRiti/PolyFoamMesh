# FASE 5 — variante "decoupled": previsione PRIMA dei test

Data: 2026-09-09. Scritto prima di lanciare le misure della variante
decoupled appena implementata nel motore
(`run(local_termination="decoupled")`, opt-in, default invariato).

## Cosa fa la variante (implementata in `bl_poly.py`)

Stesso loop di `_run_local_termination` (max 3 round per scala, scale
1.0 -> 0.6 -> 0.35 -> 0.2 -> 0.1), ma con il set di esclusioni
AZZERATO al cambio di scala (`decoupled=True`):
- prima si prova la scala RICHIESTA (1.0, spessore pieno);
- se valida, la mesh esce a spessore pieno (niente degradazione 10x del
  thin-first puro);
- se fallisce, si scende di scala con un set di esclusioni NUOVO: le
  esclusioni accumulate a una scala grossa non "avvelenano" le scale
  sottili;
- la modalità legacy `local_termination=True` (carry, default del
  percorso opt-in) resta byte-identica: il reset scatta solo con
  `"decoupled"`, che è esplicitamente richiesto.
Il conteggio finale riportato da `res.stats["local_excluded_faces"]` è
quello della scala che ha validato.

## Perché dovrebbe risolvere il problema

- FASE 4 ha mostrato che il thin-first puro vince in esclusioni/prismi ma
  degrada lo spessore (valida sempre a 0.1, anche quando 1.0 andrebbe
  bene). La causa era l'ORDINE: accettare la prima scala che valida.
- La causa delle 19.625 esclusioni thick-first sulla valvola era il
  TRASPORTO delle esclusioni tra scale: le scale grosse escludono facce
  che a 0.1 sarebbero valide (whack-a-mole), e il set continua a
  crescere. Misura FASE 3: partendo puliti a 0.1 servono 2.058
  esclusioni.
- Decoupled combina i due: prova 1.0 (spessore pieno); solo se fallisce
  scende, e ogni scala ripaga solo i propri difetti.

## Previsioni

### Cilindro, cubo duct, groove1 (sani, validano a 1.0 round 0)

- Il reset non entra mai in gioco (successo alla prima scala, primo
  round).
- Previsione: **identici al thick-first** in tutto: scala 1.0, stessi
  prismi (74.808 / 108 / 10.062), 0 esclusioni, aspect max 18,3 / 78,7 /
  15,9 (spessore pieno). Tempo identico.

### Slot1 (medio, valida a 1.0 dopo 1 round di esclusioni)

- Anche qui il reset non entra in gioco: fallimento e successo restano
  DENTRO la scala 1.0 (round 0 fallisce con 50 piramidi > 42 input,
  round 1 valida dopo 132 esclusioni).
- Previsione: **identico al thick-first**: scala 1.0, 79.614 prismi, 132
  esclusioni iterative (216 totali), aspect 16,7, ~36 s. (Migliore del
  thin-first sullo spessore: aspect 16,7 vs 128,5.)

### Valvola (il caso che conta)

- Scala 1.0: 3 round, fallisce (come thick-first: 50 -> 6 -> 6 volumi
  negativi), esclusioni accumulate ~2.000-4.000 DENTRO la scala 1.0;
- scala 0.6: set azzerato, 3 round, fallisce (come thick-first: ~1.019
  piramidi > 1.007);
- 0.35 / 0.2: idem, falliscono con i propri set freschi;
- scala 0.1 con set NUOVO: dovrebbe riprodurre ESATTAMENTE il run
  thin-first (stesso codice, stesso input): 3 round, 1.946 + 112 + 0 =
  **2.058 esclusioni iterative**, **445.716 prismi**, checkMesh
  **885 facce male orientate** (vs 895 input), skew ~47,6, aspect max
  ~15.419, "Failed 4".
- Tempo previsto: ~15 build (3+3+3+3+3) x ~95-115 s = **24-30 min**
  (nessun guadagno di tempo: le scale grosse vengono comunque tentate;
  il guadagno sono le esclusioni, non il tempo).
- Rischio previsto: basso. Il percorso 0.1 è deterministico e identico
  a quello già misurato due volte (stage2e e FASE 3 thin).
  Unica incertezza: quante esclusioni accumulano le scale grosse PRIMA
  del reset (non influenzano il risultato finale, solo i warning).

## Obiettivo dell'utente e criterio di successo

- Valvola: ~2.058 esclusioni (non 19.625) + spessore/`scale` = 0.1
  (nessuna alternativa lì) + checkMesh non peggiore del thin-first.
- Casi sani: scala 1.0 piena (nessuna degradazione dello spessore).
- Se il decoupled NON riduce le esclusioni della valvola sotto ~3.000,
  la mia spiegazione (trasporto tra scale = causa) è sbagliata e va
  riscritta.
- Se i casi sani non restano a 1.0, c'è un bug nell'implementazione.

# RISULTATI (compilati dopo il run, 2026-09-09)

## Casi sani (misura, tools/bench_bl_thinfirst.py, checkMesh reale)

| Geometria | THICK | THIN | DECOUPLED |
|---|---|---|---|
| cilindro | scala 1.0, 74.808, 0 escl, aspect 18,3 | scala 0.1, aspect 104,3 | **scala 1.0, 74.808, 0 escl, aspect 18,3** |
| cubo duct | scala 1.0, 108, aspect 78,7 | scala 0.1, aspect 781,6 | **scala 1.0, 108, aspect 78,7** |
| groove1 | scala 1.0, 10.062, aspect 15,9 | scala 0.1, aspect 90,8 | **scala 1.0, 10.062, aspect 15,9** |
| slot1 | scala 1.0, 79.614, 132 escl, aspect 16,7 | scala 0.1, 80.004, 0 escl, aspect 128,5 | **scala 1.0, 79.614, 132 escl, aspect 16,7** |

DECOUPLED è identico a THICK in tutto: previsione confermata al 100%.
Il reset non scatta mai perché il successo arriva dentro la scala 1.0.

## Valvola

- successo a scala 0.1, **2.058 esclusioni** (previste), **445.716
  prismi** (previsti), 3.684 facce senza BL = 1,63% (come thin-first);
- checkMesh: 885 facce male orientate vs 895 input, "Failed 4", skew
  47,6, aspect max 15.419, non-ortho 55 — identico al thin-first;
- traiettoria dei warning: fallimenti a 1.0 (50→6→6 volumi negativi),
  0.6 (42 neg → 1.037/1.020 piramidi), 0.35 (15/1 neg → 1.017), 0.2
  (4/1 neg → 1.016), poi 0.1 fresco al terzo round (1.946+112) valida;
- **tempo 2.215 s (37 min)**: più lento della previsione (24-30 min) e
  più lento sia del thin (305 s) sia del thick (1.602 s). Causa: prova
  comunque tutte le scale (15 build, 3 round ciascuna) e al 0.1 serba
  il costo dei 3 round freschi. Il vantaggio è nelle esclusioni/prismi,
  non nel tempo.

## Confronto finale (valvola)

| Variante | escl. iter. | senza BL | prismi | checkMesh | tempo |
|---|---|---|---|---|---|
| THICK (legacy) | 19.625 | 21.208 (9,36%) | 410.668 | 891 wrong, Failed 4 | 1.602 s |
| THIN (sperim.) | 2.058 | 3.684 (1,63%) | 445.716 | 885 wrong, Failed 4 | 305 s |
| **DECOUPLED** | **2.058** | **3.684 (1,63%)** | **445.716** | **885 wrong, Failed 4** | **2.215 s** |

## Conclusione

La variante decoupled centra l'obiettivo: cattura il vantaggio
thin-first (9,5x meno esclusioni, +8,5% prismi, checkMesh migliore)
SENZA degradare lo spessore sui casi sani (scala 1.0 piena). È la
candidata naturale per la semantica della terminazione locale quando
verrà abilitata: "spessore richiesto se possibile, solo i difetti
pagano". Resta il costo in tempo sulle geometrie difettose; una
possibile ottimizzazione (saltare le scale intermedie / riusare le
esclusioni quando la firma del fallimento è la stessa) va misurata a
parte. Nessun default cambiato (`local_termination` resta off di
default; `True` mantiene la semantica carry misurata in FASE 2/3).
