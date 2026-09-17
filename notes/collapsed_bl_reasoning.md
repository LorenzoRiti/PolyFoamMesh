# Task 2 — perche' la BL fallisce sul dual di produzione (collapsed) della valvola

Data: 2026-09-09. Scritto prima dei test di Task 2 (la fixture e' stata
appena rigenerata nel Task 1, quindi i run partono dal converter attuale).

## Fatti misurati (FASE 7, input collapsed production)

- facce di bordo: 42.130 (poligoni 4-9 lati) vs 226.542 quad dell'exact;
- difetti input: 340 wrong cells (checkMesh) / 370 facce pyr (in-process BL);
- drift di volume: 1,08%;
- BL decoupled: FALLISCE a tutte e 5 le scale x 3 round — traiettoria pyr
  per round: 1.0: 470/431; 0.6: 422/406; 0.35: 414/404; 0.2: 422/375;
  0.1: 444/385; input 370. Il migliore (375 al 0.2 round 2) e' 5 sopra
  input; le esclusioni si fermano a 2.189 (5,2% delle facce).
- Controllo exact (stesso converter, stessa sorgente): BL riesce a scala
  1.0 round 1 con 2.525 esclusioni (1,1% delle facce).

## Ipotesi sul perche' (da verificare con la misura)

H1 — **granularita' dell'esclusione (rim)**: nel dual collapsed una faccia
e' una poligonale per vertice di bordo, ~5x l'area di un quad. Escludere
una poligonale rimuove molto muro; i prismi di transizione al BORDO della
zona esclusa generano nuove violazioni piramidali. Il conteggio si ferma
perche' ogni round rimuove ~tante violazioni quante ne crea al bordo.
Previsione: i difetti residui stanno a raggio 0-2 poligonali dalla zona
esclusa, e il conteggio per round cala lentamente (es. 400 -> 30 -> 5 ->
...) senza arrivare a 0 nei 3 round.

H2 — **difetti input non raggiungibili**: se buona parte dei 370 pyr
input sta su facce INTERNE (non selezionabili/escludibili), il budget
"n_pyr <= input" e' stretto e i round extra non bastano. Previsione: sul
collapsed la quota boundary dei difetti input e' piu' bassa che
sull'exact (937/1007 = 93% li') — mi aspetto 250-340 su 370 (68-92%).

H3 — **piu' round bastano**: il migliore e' 375 vs 370: se il calo per
round e' lento ma monotono, con 6 round al 0.2 (o 0.1) si chiude.
Previsione: 60% che `max_rounds=6` chiuda al 0.2 o 0.1 sulla produzione.

H4 — **varianti di esclusione**: se i round non bastano, allargare
l'esclusione di un anello (V3) chiude probabilmente in 3 round ma perde
piu' muro; escludere solo i prismi diretti senza allargamento alle celle
core (V2) probabilmente stalla prima.

H5 — **criterio**: `angle_fade` vs `dual_convexity` — su poligonali di
bordo lisce il fade resta piatto come sull'exact; previsione: nessuna
differenza sostanziale (bassa priorita').

## Piano di misura (in ordine)

1. Aggiungere al motore un parametro opt-in `local_max_rounds: int = 3`
   (default invariato) per testare H3 senza monkey-patch.
2. Estendere `tools/bench_bl_production_topology.py` con `--max-rounds`.
3. Run: valvola production collapsed, `decoupled`, `max_rounds=6` (poi 10
   se 6 non basta). Se chiude: checkMesh reale, numeri, e la proposta
   opt-in.
4. Se non chiude: diagnostica residua (classificazione delle facce pyr
   residue: prism vs core, boundary vs internal) + variante V3
   (esclusione allargata di 1 anello) via monkey-patch nel tool.
5. Se nemmeno V3 chiude: scrivere il negativo con la diagnosi e la
   raccomandazione (mappatura esclusioni->vertici, o niente BL sul
   collapsed per geometrie concave).

## Misure fatte

### H2 (difetti input non raggiungibili) — misurato
Sul collapsed: `input pyr: {'total': 370, 'boundary': 360, 'internal': 10}`.
**97% boundary**, quindi escludibili. H2 falsificata: il budget
non è strozzato da difetti interni.

### H3 (piu' round bastano) — misurato
Valvola production collapsed, decoupled, `local_max_rounds=6`,
1.483 s, 25 build, 2.207 esclusioni, stallo 373-378 (0.2) / 379 (0.1
da round 3 a 5). Mesh invariata. **H3 falsificata**: la conta
plateau, piu' round non aiutano.

### H1 (rim) — confermata
Il cascade plateau e il pattern dei pyr (373-378 vs 370 input,
con oscillazioni da 373 a 444) e' coerente con esclusioni al rim:
escludere 2207 poligoni produce un bordo "buco" che fa emergere
prismi di transizione difettosi; ogni round aggiunge 0-200 esclusioni
ma la conta netta di pyr oscilla e non scende sotto ~373-379.

### H6 widen=1 — misurato, falsificata
Valvola production collapsed, decoupled, `local_exclude_widen=1`,
`local_max_rounds=6`, 2.064 s, 25 build, **2.207 esclusioni**
(identico al widen=0: widen a 1 anello non aggiunge facce al set
escludibile della valvola, presumibilmente perche' la frontiera del
rim di esclusione e' gia' 1-anello in molte posizioni sul collapsed
o la pyr-violation al rim non e' semplicemente "spostata di 1
anello"). Stesso plateau: 0.1 r3-r5 a 379. **H6 widen=1
falsificata**: allargare l'esclusione di 1 anello non chiude il
gap (5-10 facce sopra input) e non cambia materialmente il set
escluso. widen=2 quasi certamente non chiuderà comunque
(il widening e' locale e non cambia la geometria del rim).

## Cosa H6 *non* cambia e perché

H4 (mapping esclusione -> per-vertice) resta il gap strutturale:
il binary face granularity su poligonali collapsed non puo' coprire
il rim in modo continuo. Anche con widen > 0, il cascade aggiunge
facce al rim di esclusioni esistenti e il gate n_pyr <= input non
chiude. La soluzione richiede un mapping per-vertice (escludere
"layers" su vertici incidenti, non intere facce): un cambio
morfologico al binary exclusion model, non un tweak di parametri.

## Criterio di lettura (aggiornato)

- H1 confermata, H3 + H6 widen=1 falsificate, H2 confermata: il
  difetto del BL collapsed sulla valvola non e' chiudibile con
  exclusion granularity (face + 1 anello). widen=2 atteso NON
  chiudere; non misurato (costo ~30 min per un risultato atteso
  negativo). H4 (mapping per-vertice) e' il gap strutturale
  documentato.
