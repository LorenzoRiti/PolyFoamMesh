# thin-first generalizza oltre la valvola? — Ragionamento PRIMA dei test

Data: 2026-09-09. Scritto prima di qualsiasi run di FASE 4.
La scoperta thin-first (valvola: 9,5x meno esclusioni, +8,5% prismi,
~5x più veloce) va verificata su cilindro, cubo duct, groove1, slot1.

## Fatto da spiegare (misurato, FASE 3)

Sulla valvola, l'engine `_run_local_termination` con scale
spessa→sottile [(1.0,0.5), (0.6,0.35), (0.35,0.2), (0.2,0.1), (0.1,0.05)]
accumula 19.625 esclusioni alle scale 1.0/0.6/0.35/0.2 (le warning
mostrano i fallimenti solo a quelle scale) e a 0.1 valida col set
portato. Se invece si parte a 0.1 con la logica iterativa di
produzione, in 3 round si chiude con 2.058 esclusioni (91% delle quali
è il seed predicibile).

Domanda: è un comportamento specifico della valvola (unica geometria
con difetti input gravi) o generalizza?

## Meccanica che sospetto

In `_run_local_termination` ogni round di una scala più spessa di
un'altra, a parità di difetti geometrici, è PIÙ PERMISSIVO sui
prismi: altezze più grandi producono meno volume negativo e meno
pyramid violations per prisma, ma possono nascondere
whack-a-mole — la faccia esclusa apre un difetto al suo bordo
(perimetro del dropped region), che viene "risolto" solo al
prossimo tentativo. A scala più sottile, ogni difetto che affiora
resta affiorante e l'esclusione è più chirurgica.

Dalla sezione `zero_concave` di `_build`: una faccia è scartata se
(bi in drop_bnd) OR (any vertex fade<0.5) OR (max_h<=0). Il seed è
deterministico da `pre`. La parte iterativa copre le facce che
"appartengono" a celle core con difetti nelle facce nuove o prismi
invertiti.

## Previsioni

### Cilindro (dual 10.618 celle, n_bnd 24.936, pyr=0, low_fade=0)

- `_build` su cilindro: tutti i vertici hanno fade=1 (smooth), max_h
  ampio; nessun drop_bnd; nessun low-fade.
- Previsione: **thin-first identico a thick-first**. La prima scala che
  esegue è round 0 → validate → scritta. Identici in tempo, prisms,
  metriche. Vantaggio = 0.

### Cubo duct (dual 9 celle, n_bnd 36, pyr=0, low_fade=0)

- Previsione: **identico al thick-first** (no difetti, esce round 0).
  Vantaggio = 0.

### Groove1 (dual 682 celle, n_bnd 3.372, pyr=4, low_fade=2, seed=18)

- LT thick-first ha prodotto 10.062 prismi (3 layer × 3.354 facce =
  3.372 - 18 seed) con 0 esclusioni iterative. Scala 1.0, round 0,
  validate.
- LT thin-first inizia da 0.1: qui max_h è scalato di 0.1 (più piccolo);
  ma fade=1 ovunque eccetto i 2 low-fade → stesso seed.
- Previsione: **identico al thick-first** (0 esclusioni in entrambi,
  stessa prisma count). Vantaggio = 0.

### Slot1 (dual 9.372 celle, n_bnd 26.754, pyr=42, low_fade=17, seed=86)

- LT thick-first: 79.614 prismi, 216 senza BL (86 seed + 132 iterative,
  overlap 2), 0 negative.
- LT thin-first inizia da 0.1: max_h ridotto di 0.1, ma fade=1 fuori dai
  17 vertici. Possibile che la scala ridotta riveli più difetti di
  pyramid su prismi sottili (effetto non catturato da `_validate` a
  scala 1.0). Incertezza: pyr_input è 42, sopra la soglia.
- Previsione: **thin-first leggermente peggiore o uguale in % esclusioni**
  (qualche prism in più da escludere iterativamente a 0.1), ma
  **dovrebbe comunque validare in 1-2 round** perché il seed a 0.1
  è comunque 86 e la geometria è la stessa.
- Se thin-first AUMENTA le esclusioni vs thick-first: è un segnale che
  il "vantaggio valvola" non è generale ma dipende dal fatto che la
  valvola ha 1.007 difetti input (un ordine di grandezza sopra slot1):
  thick-first ha abbastanza "bilancio di esclusione" alle scale spesse
  da "pagare" il costo di riparazione, mentre thin-first deve escludere
  tutto subito a 0.1. Su slot1 con 42 difetti, lo spazio di manovra è
  molto più piccolo.

## Cosa mi aspetto di vedere in totale

- Se thin-first = thick-first su cilindro/cubo/groove1 (zero esclusioni
  in entrambi gli ordini) e slot1 è entro ~2x in un senso o nell'altro:
  il "vantaggio valvola" è reale ma non generalizza come miglioramento
  su tutti i casi — è una proprietà della valvola (grossi difetti input
  dove lo spazio di manovra alle scale spesse è grande).
- Predittore: in un caso con difetti input nulli o minimi, l'ordine non
  conta (entrambi escono a round 0 scala 1.0 o 0.1 indifferentemente).
  In un caso con difetti medi (slot1), thin-first è ≤ thick-first. Solo
  in un caso con difetti grandi (valvola) thin-first batte thick-first.

## Criterio di lettura

- thin-first ≠ thick-first sul cilindro/cubo → confido in me.
- groove1 identico → conferma la neutralità dell'ordine su difetti minimi.
- slot1 ≤ thick-first → conferma che il "vantaggio valvola" scala con
  l'ampiezza dei difetti input, non è una magia.
- slot1 > thick-first → segnale che l'inversione non è universalmente
  positiva e che thin-first va abilitato come opzione, non come default.

# RISULTATI (compilati dopo il run, 2026-09-09)

## Misure (tools/bench_bl_thinfirst.py, checkMesh reale)

| Geometria | THICK (1.0→0.1) | THIN (0.1→1.0) |
|---|---|---|
| cilindro | ok, scala 1.0, 74.808 prismi, 0 escl, 17,9 s, aspect 18,3 | ok, **scala 0.1**, 74.808, 0 escl, 17,0 s, **aspect 104,3** |
| cubo duct | ok, scala 1.0, 108, 0 escl, 0,0 s, aspect 78,7 | ok, **scala 0.1**, 108, 0 escl, 0,0 s, **aspect 781,6** |
| groove1 | ok, scala 1.0, 10.062, 0 escl, 2,0 s, aspect 15,9 | ok, **scala 0.1**, 10.062, 0 escl, 1,9 s, **aspect 90,8** |
| slot1 | ok, scala 1.0, 79.614, 132 escl iterative (216 totali), 36,5 s, aspect 16,7 | ok, **scala 0.1**, 80.004, **0 escl iterative** (86 totali = seed), 17,9 s, **aspect 128,5** |

Tutte le mesh sono Mesh OK in entrambe le modalità; il thick-first
riproduce esattamente i risultati LT di FASE 3 (patch fedele).

## Il risultato vero: thin-first riduce lo spessore richiesto di 10x

Su TUTTE e 4 le geometrie THIN valida al PRIMO tentativo (scala 0.1):
l'altezza `first_height` richiesta (5e-3) viene scritta come 5e-4, in
silenzio. Il rapporto aspect ~6-10x lo dimostra (cubo: 781,6/78,7 =
9,93x). Stessi prismi (stesso numero di layer), stessa topologia, ma
BL 10x più sottile del richiesto.

I due effetti sono INTRECCIATI: il vantaggio del thin-first (evitare
le esclusioni accumulate dalle scale spesse) esiste solo dove una scala
spessa fallisce davvero (slot1: 0 vs 132; valvola: 2.058 vs 19.625), e
lì è reale; ma "accetta la prima scala che valida" significa accettare
la 0.1 anche dove la 1.0 funzionerebbe, degradando l'y+ di 10x.

## Previsioni vs misure (onesto)

- cilindro/cubo/groove1: previsto "identico a thick-first" — **SBAGLIATO**
  nello spessore/scala/aspect (stessa topologia, ma scala 0.1 e aspect
  6-10x peggiore). La meccanica ("esce al round 0") era giusta, avevo
  mancato che il round 0 di thin-first È la scala 0.1.
- slot1: previsto "uguale o leggermente peggiore in % esclusioni" —
  **sbagliato in segno** (0 vs 132, quindi meglio), giusto nella
  meccanica (valida in 1 round); mancato del tutto l'effetto spessore.
- Il predittore FASE 3 (pyr_input) resta valido: il vantaggio compare
  solo dove pyr_input > 0 e cresce col numero di difetti.

## Conclusione

L'inversione cieca dell'ordine NON va proposta come default: su mesh
sane viola il contratto del `first_height` richiesto. La variante da
misurare (lavoro futuro, non implementata) è **decoupled**: prova la
scala richiesta per prima; scendendo di scala NON portare le esclusioni
accumulate (reset al cambio scala). Così la valvola otterrebbe ~2.058
esclusioni come thin-first senza degradare i casi sani. Da misurare
prima di qualsiasi proposta.
