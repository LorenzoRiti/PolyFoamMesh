# FASE 6 — early-exit: ragionamento PRIMA dei test

Data: 2026-09-09. Scritto prima di qualsiasi run della variante early-exit
proposta per ridurre il tempo sulla valvola (37 min nel decoupled, 15
build totali quando 4 scale grosse falliscono prima che 0.1 validi).

## Punto di partenza: l'early-exit "esci alla prima scala valida" ESISTE GIÀ

L'engine `_run_local_termination` (e il loop fallback in `run()`)
controlla `if ok: return self._accept_built(...)` ad ogni `ok, msg =
self._validate(...)`. Appena una scala + un round validano, il loop
esce e scrive la mesh. Sul cilindro/cubo/groove1 (casi sani) il
successo arriva a 1.0 round 0: il loop fa **1 sola build** e termina
(cilindro: 17,8 s). Su slot1 (medio) il successo arriva a 1.0 round 1
dopo 132 esclusioni: **2 build** (36,8 s). Sulla valvola *nessuna*
delle 4 scale grosse (1.0, 0.6, 0.35, 0.2) valida: servono 12 build
sprecate + 3 build a 0.1 = 15 build totali. Quindi l'early-exit
"fermati alla prima scala valida" **è già attivo**; il "tempo
perso" sulla valvola non è un bug di "non esce", è che solo 0.1
funziona davvero.

## Dove si può risparmiare davvero

Sulla valvola, le 4 scale grosse sono tutte "ugualmente inutili" (pyr
count 1010-1019 > 1007 input). Se dopo che 1.0 round 0 fallisce
saltassi 0.6/0.35/0.2 e andassi diretto a 0.1, risparmierei 9 build
(3 round × 3 scale) = ~15 min sulla valvola. La domanda è: questa
euristica è SICURA in generale?

## Strategia proposta (opt-in, default invariato)

Nuovo parametro engine: `run(..., local_termination=..., 
early_exit_intermediates: bool = False)`. Quando True E siamo in
modalità `local_termination` (decoupled o carry) E la scala 1.0 round
0 ha fallito, le scale intermedie (0.6, 0.35, 0.2) vengono skippate
e si procede direttamente a 0.1.

## Ipotesi e rischi

- **Ipotesi implicita**: "se 1.0 round 0 fallisce, le intermedie non
  aiuteranno" — nella valvola è vero (le 4 scale hanno pyr simile
  1010-1019, e solo 0.1 ha pyr ≤ 1007 = input). Non è universalmente
  vero: in teoria un caso futuro potrebbe avere pyr_input=1050, pyr a
  1.0=1070, pyr a 0.6=1048, pyr a 0.1=1060 — le intermedie aiutano.
  In quel caso skip_intermediates fallirebbe a 0.1 e il run
  fallirebbe pulito (mesh invariata). Documento questo come rischio
  accettabile per l'opt-in.
- **Su healthy**: skip non si attiva (1.0 round 0 valida prima che
  qualsiasi euristica scatti). Stesso risultato di decoupled senza
  flag.
- **Su slot1 (medio)**: 1.0 round 0 fallisce (50 pyr > 42 input) ma
  round 1 valida. Lo skip si attiverebbe? Dipende: skip è "se 1.0
  round 0 ha fallito, salta intermedie". Round 0 fallisce, skip si
  attiva, intermedie skippate, 0.1 tentato. Ma 0.1 sulla slot1 con
  set di esclusioni da 1.0 (carry mode) o set pulito (decoupled)
  potrebbe non validare (la slot1 standard a 0.1?). Non abbiamo
  misure di slot1 a 0.1. Possibile che decoupled + skip + slot1
  fallisca. Documento il rischio: la euristica ottimizza la valvola
  al costo potenziale di slot1 (e simili "medi che validano a 1.0 con
  1 round di exclusion"). Se succede, documentiamo e decidiamo.

## Previsioni sulla valvola (con decoupled + skip)

- Scala 1.0: 1 build (round 0, fail). Esclusioni accumulate dentro 1.0:
  ~2.000-3.000 (in decoupled, set resettato dopo — quindi irrilevanti).
- Skip 0.6, 0.35, 0.2: 0 build risparmiate.
- Scala 0.1: 3 build (round 0 fail, round 1 fail, round 2 valida).
  Esclusioni: 1.946+112+0 = 2.058 (identico al decoupled, perché
  decoupled resetta le esclusioni a 0.1).
- Totale: **4 build** vs 15 attuali. **Tempo previsto**: ~4 × ~100-120 s
  = **400-500 s** vs 2.215 s. Saving: ~30 min (~80% più veloce).
- checkMesh: dovrebbe essere identico al decoupled (885 wrong, Failed 4,
  skew 47,6, aspect 15.419).
- Prismi: 445.716 (identico).

## Previsioni sui casi sani

- Skip non si attiva mai (1.0 round 0 valida). Risultato identico al
  decoupled: scala 1.0, spessore pieno, aspect invariato.

## Previsioni su slot1 (rischio)

- 1.0 round 0 fallisce (50 pyr > 42). Skip si attiva. 0.6/0.35/0.2
  skippate. 0.1 tentato: con decoupled + skip, le esclusioni partono
  dal set pulito (decoupled reset). A 0.1, pyr a 0.1 round 0: ???.
  Non abbiamo misure. Se pyr_round0_0.1 ≤ 42 (input), valida. Altrimenti
  round 1/2 con exclusion progressive. Probabilmente simile al
  thin-first su slot1: 0 escl, valida al primo round. **Ipotesi**:
  skip su slot1 = identico al thin-first (scala 0.1, 0 escl, 80.004
  prismi). Ma è una previsione, non una misura.

## Cosa misurare

1. Engine: implementare `early_exit_intermediates` opt-in + 1 test
   (healthy cube con skip=True = identico a decoupled senza skip).
2. Bench: aggiungere `--early-exit-intermediates` flag al
   `tools/bench_bl_thinfirst.py` e misurare:
   - cilindro/cubo/groove1/slot1 con skip=True vs decoupled (no
     skip): devono essere identici per i sani, e per slot1 verifico
     se la euristica regge o meno.
   - valvola con decoupled+skip: 4 build attese, ~400-500 s attesi,
     2.058 escl attese, 445.716 prismi attesi, checkMesh 885 atteso.

## Criterio di lettura

- Se valvola decoupled+skip = 2.058 escl, 445.716 prismi, ~450 s
  (~8x più veloce del decoupled): la euristica funziona, è un
  candidato per il default *quando* la terminazione locale verrà
  abilitata (con la caveat documentata del rischio su slot1).
- Se slot1 decoupled+skip fallisce o degrada: la euristica è
  troppo aggressiva; va misurata una versione "skip solo se 1.0 round
  0 fallisce con pyr_count_round0 ≥ pyr_count_round0 della scala più fine
  già tentata" (cioè skip solo se la pyr peggiora monotonicamente).
- Se valvola decoupled+skip dà risultato diverso da decoupled
  (es. più esclusioni o non valida): l'euristica ha rotto qualcosa,
  va rivista o rimossa.
