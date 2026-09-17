# FASE 3 — Ragionamento e previsioni (scritti PRIMA dei test)

Data: 2026-09-09. Scritto prima di lanciare qualsiasi benchmark di FASE 3.
Le previsioni vanno confrontate con i risultati a fine lavoro; una
previsione sbagliata e' un dato, non un fallimento.

## Fatti misurati disponibili (da FASE 2)

| Fatto | Valore |
|---|---|
| Valvola (dual) | 152.086 celle, 1.241.048 facce, 226.542 facce di parete |
| Difetti INPUT valvola (face pyramids, in-process = checkMesh) | 1.007 (895 checkMesh) |
| Celle duali non-convesse (dual_convexity) | 393 |
| Vertici di parete con fade duale < 0.5 | 1.123 (0 con angle_fade) |
| Winding globale, path standard, per scala | 0 non chiuse; neg volumes 44/23/22/32/29 (stage1c) |
| local_termination thick-first (engine, dual_convexity) | successo a 0.1, 19.625 escluse (8,7%), 1602 s |
| local_termination solo scala 0.1 (stage2e, fresh) | successo, 2.060 escluse (0,9%) in 2 round |

Meccanica dal codice (`bl_poly.py`):
- `zero_concave` scarta una faccia se: `bi in drop_bnd` (le 1.007 facce
  ownership di celle gia' non-convesse nell'input) OPPURE almeno un suo
  vertice ha `angle_fade < 0.5` OPPURE `max_h <= 0`.
- `angle_fade[w] = fade * min(clamp_factor * support_d[w], 0.5 *
  min_edge[w])`; con `dual_convexity`, `fade = max(0.05, 1 -
  n_bad/n_owner)` per vertice.
- Dopo ogni fallimento `_collect_exclusions` aggiunge le facce i cui
  prismi hanno volume <= 0 o violano il criterio piramide (incluse le celle
  core con facce nuove violate); le esclusioni si portano tra le scale;
  max 3 round per scala, scale dalla PIU' SPESSA (1.0) alla PIU' SOTTILE
  (0.1).

## Q1 — Perche' l'8,7%? E' predicibile a priori?

Previsione: le 19.625 NON sono difetti reali ma in gran parte il prodotto
di due strati distinti:

1. **Seed predicibile (a priori, senza costruire nulla)**: unione di
   (a) le ~1.007 facce `drop_bnd` e (b) le facce con un vertice a fade
   duale < 0.5, cioe' il quartiere delle 1.123 vertici. Con ~2-6 facce per
   vertice, mi aspetto **2.000-5.000 facce di seed**, calcolabili in
   anticipo con `_build_precompute` e nessun tentativo.
2. **Cascata (non predicibile senza costruire)**: le facce al BORDO della
   regione esclusa, dove il prisma di transizione ha alcuni vertici
   estrusi e altri no. Prevedo che la cascata domini il totale:
   **cascata = 19.625 - seed ≈ 15.000-17.000 facce**, concentrate nei
   round delle scale spesse. Evidenza indiretta gia' disponibile: ai
   warning del run engine, OGNI scala spessa fallisce con ~1.010-1.019
   piramidi (input 1.007) NONOSTANTE le esclusioni gia' fatte — il
   difetto si RIGENERA al bordo della regione esclusa (whack-a-mole);
   3 round x 3 scale spesse x ~1-2k facce/round producono l'ordine di
   grandezza osservato.
3. **Pattern geometrico**: le escluse stanno in raggio 0-2 facce dalle
   1.007 facce difettose dell'input (giunzioni concave tra le 89
   superfici). Previsione: >= 90% delle escluse entro 2 anelli di
   adiacenza di una faccia difettosa; il resto entro 3-4.

Test previsto: misurare seed esatto, composizione del set finale per
anelli di adiacenza, overlap seed/finale.

## Q2 — Winding globale vs terminazione binaria: due affidabilita' diverse?

Previsione: SI', sono due componenti con natura diversa.

- `_solve_global_windings` e' un solver ESATTO di un problema topologico
  (orientabilita'): O(F+E), deterministico, nessun parametro. Fallisce
  solo su mesh non orientabili o con edge non-manifold, e in quel caso lo
  DICHIARA (`n_conflicts`, `n_nonmanifold_edges`) invece di riparare.
  Previsione: sulle geometrie non estreme (cilindro, cubo, groove, slot)
  non fara' praticamente nulla (0 flip o pochi), perche' il problema di
  chiusura non si presenta.
- La terminazione binaria e' un'euristica geometrica: il successo dipende
  dalla densita' locale dei difetti e dalla scala dei layer. E' l'unico
  pezzo che scala con la difficolta' della geometria.
- Quindi: **il winding da solo NON basta sulla valvola** (gia' misurato:
  neg volumes 22-44 per scala col path standard), perche' il problema
  residuo e' geometrico (prismi invertiti), non di orientamento.
  Su geometrie "meno estreme" il winding e' probabilmente sufficiente
  perche' non c'e' nulla da riparare: la terminazione binaria diventa un
  no-op a costo ~zero (esce al primo tentativo).

## Q3 — Perche' la valvola e' il caso peggiore?

Previsione: perche' e' l'unica geometria in cui il dual mesh di INPUT
viola gia' il criterio piramide (1.007 facce; cilindro/cubo 0). Il
meccanismo e' strutturale: una cella duale che avvolge uno spigolo CAD
concavo e' non-convessa; estrudere uno strato sottile da una parete sopra
quella cella produce prismi che ereditano la non-convessita' (checkMesh
'face pyramids'). Il numero di spigoli concavi e la loro densita' rispetto
alla mesh decidono la difficolta'.

**Predittore proposto (economico):** contare su una mesh dual, senza
estrudere nulla, `pyr_input` (facce che violano piramide nell'input) e
`n_wall_verts_fade_lt_half` (dual_convexity). Facile = 0/0; difficile =
alto/alto. La valvola = 1.007 / 1.123 e' estrema. Previsione per le altre:
cilindro 0/0, cubo 0/0, groove1 poche decine/centinaia, slot1 idem.

## Q4 — Quali geometrie FALLIRANNO? (previsione prima del test)

Prevedo successo (con 0-5% escluse) su:
- cilindro e cubo: 0 escluse, esito identico al path standard.
- groove1 (scanalatura a L, 1 spigolo rientrante diritto): successo alla
  prima o seconda scala, escluse poche % (persone attorno allo spigolo).
- slot1 (fessura): successo, possibilmente piu' escluse di groove1 se lo
  spigolo rientrante ha piu' facce contigue; < 10%.

Prevedo FALLIMENTO (errore pulito, mesh invariata) su:
- geometrie con difetti concavi PIU' DENSITELLI della mesh: molti spigoli
  rientranti a 1-2 facce l'uno dall'altro (fusioni/colate con boss, reti
  di canali). La cascata di esclusione non si chiude in 3 round e la mesh
  del bordo resta non valida.
- mesh molto grossolane attorno a uno spigolo concavo: lo scarto di 1-2
  facce remove ~tutto il supporto del vertice (max_h <= h1 ovunque) e
  l'engine arriva a "dropped every selected face" / "excluded too many".
- geometrie con celle duali degeneri (sliver) al confine: il criterio
  binario non ha una via di mezzo (1 layer) e preferira' l'errore pulito.

E soprattutto: mi aspetto che **l'ordine delle scale sia un artefatto
misurabile**: partendo da 0.1 (thin-first) la stessa valvola dovrebbe
chiudersi con ~2.000-3.000 escluse invece di 19.625 (rapporto ~7-10x),
perche' i round spessi escludono facce che a 0.1 sarebbero valide. Se
thin-first NON riduce le escluse, la mia spiegazione della cascata e'
sbagliata.

## Q5 — Tempo in funzione della dimensione

Previsione: il costo per tentativo e' ~lineare in F (facce), dominato da
`_build_precompute` (~58 s sulla valvola, che ha 1,9M facce nel mesh
costruito) + `_build` + validazione. Il tempo TOTALE = n_tentativi x
costo_per_tentativo. Geometrie sane: 1 tentativo (scala 1.0), quindi
~0 overhead vs standard. Valvola: 13 tentativi (~123 s l'uno) = 1602 s.
Previsione per il cilindro (~70k celle): ~10-20 s per tentativo, 1
tentativo.

## Criterio di lettura dei risultati

- Se seed ~2k e thin-first ~2k: "l'8,7% non e' intrinseco, e' un
  artefatto dell'ordine delle scale; il costo intrinseco del criterio
  binario e' ~0,9%".
- Se l'overlap seed/finale e' alto e gli anelli sono 0-2: il set e'
  predicibile a priori e si puo' sostituire l'iterazione con un drop
  calcolato (lavoro futuro, NON in questa fase).
- Se groove/slot passano con poche escluse: il predittore pyr_input +
  low-fade-verts separa facile/difficile. Se falliscono: il predittore va
  raffinato (densita' dei difetti, non solo conteggio).

# RISULTATI (compilati dopo i test)

## Valvola — predizione vs misura

| Previsione | Misura | Esito |
|---|---|---|
| Seed predicibile 2.000-5.000 facce | **2.737** (1,21%) | ✓ |
| Thin-first 2.000-3.000 escluse | **2.058** (0,91%) | ✓ |
| >=90% delle escluse entro 2 anelli da una faccia difettosa | **91,5%** (803+651+429 di 2.058) | ✓ |
| Cascata dominante nel 19.625 thick-first (~15-17k) | thin-first cascata = **230** (0,1%); il resto del 19.625 e' overreaction delle scale spesse | ✓ nella sostanza |

Dettagli misurati (run thin-first, solo scala 0.1, logica di esclusione di
produzione):
- round 0: 447.610 prismi, 2 volumi negativi, 1.226 piramidi (input 1.007)
  -> 1.946 escluse, 114,8 s;
- round 1: 0 negativi, 1.012 piramidi -> +112, 90,7 s;
- round 2: 1.007 piramidi (= input), validate OK, +0, 99,4 s.
- checkMesh della mesh validata: 597.802 celle, **885 facce male orientate
  vs 895 input** (migliore), skew 47,6, aspect max 15.419, "Failed 4"
  (il quarto e' l'aspect ratio, intrinseco). Prismi conservati: 445.716 vs
  410.668 del thick-first (+8,5%).

Anatomia del set finale (2.058):
- 1.828 = seed (88,8%), 230 = cascata pura (11,2%);
- seed totale 2.737: 66,8% finisce davvero escluso (il seed
  sovra-approx: 909 facce seed NON serviva escluderle);
- composizione: 803 dentro drop_bnd (le facce difettose input), 1.828
  toccano un vertice a fade<0.5, 0 solo drop_bnd;
- anelli da una faccia difettosa: 0->803, 1->651, 2->429, 3->80, >3->95.
- il predittore costa 27,1 s (vs 114,8 s del primo build): il set delle
  escluse e' calcolabile a priori con recall 88,8%.

## Predittore sulle altre geometrie (prima dei run BL)

| Geometria | pyr_input | low-fade dual | seed | Previsione | Misura |
|---|---|---|---|---|---|
| valvola | 1.007 | 1.123 | 2.737 | difficile | LT ok @0.1, 2.058 escluse iter |
| cilindro | 0 | 0 | 0 | no-op | **no-op confermato** |
| cubo duct | 0 | 0 | 0 | no-op | **no-op confermato** |
| groove1 | 4 | 2 | 18 | poche escluse | **ok @1.0, 18-28 scartate** |
| slot1 | 42 | 17 | 86 | poche escluse | **standard FALLISCE; LT ok @1.0, 132-186 escluse** |

Nota onesta su una previsione sbagliata: avevo previsto per groove1
"poche decine/centinaia" di vertici a fade basso; la misura e' **2**
(difetti 4). Il predittore resta corretto nella DIREZIONE (0 = facile) ma
la mia scala mentale dell'ordine di grandezza era gonfiata di ~1-2 ordini.

## Geometrie piccole — esiti completi

| Geometria | standard | local_termination (dual) | local_termination (angle) |
|---|---|---|---|
| cilindro | 74.808 prismi, scale 1.0, Mesh OK, 13,5 s | **identico** (0 escluse, 12,6 s) | identico (13,2 s) |
| cubo (9 celle) | 108 prismi, Mesh OK, 0,0 s | identico (0 escluse) | identico |
| groove1 (682) | 3.372 prismi = TUTTO a 1 layer, scala 0.1, **checkMesh Failed 2** (2 err. non-ortho, skew 46,2) | **10.062 prismi (3 layer), scala 1.0, Mesh OK** (skew 3,29), 18 scartate, 1,4 s | 10.032 prismi, Mesh OK, 28 scartate, 3,2 s |
| slot1 (9.372) | **FALLISCE** (tutte le scale invalide, 34,5 s) | **79.614 prismi, scala 1.0, Mesh OK** (skew 2,89), 216 senza BL = 0,8%, 27,1 s | 79.593 prismi, Mesh OK, 190 scartate, 27,7 s |

Previsioni Q4: nessuno dei fallimenti LT previsti si e' verificato su
questo set; l'unico fallimento misurato e' il path STANDARD su slot1.
LT su slot1 e' il caso piu' forte: sblocca un BL che il percorso standard
non riesce a costruire a nessuna scala, con lo 0,8% di parete scoperta e
Mesh OK.

## Correzione contabile (importante)

`res.stats["local_excluded_faces"]` conta solo le esclusioni ITERATIVE;
le facce scartate dal criterio binario (`zero_concave`, seed) sono
separate e vengono rivalutate a ogni round (un vertice puo' scendere
sotto 0,5 quando perde una faccia incidente "buona"). Quindi:
- valvola thin-first: iterative 2.058, ma senza-BL totale = 3.684 =
  1,63% (445.716 prismi / 2 layer = 222.858 facce coperte);
- valvola thick-first (engine): iterative 19.625, senza-BL totale =
  21.208 = 9,36% (410.668 prismi).
- groove1 lt_dual: 18 scartate = seed (0 iterative); lt_angle: 28 = 4
  drop_bnd + 24 iterative. slot1 lt_dual: 216 = 86 seed + 132 iterative
  − 2 in overlap.

## Tempo vs dimensione

| Geometria | celle dual in | celle build | tempo engine |
|---|---|---|---|
| cubo duct | 9 | 117 | ~0,03 s |
| groove1 | 682 | 10.744 | 1,4-3,2 s |
| cilindro | 10.618 | 85.426 | 12,6-13,5 s |
| slot1 | 9.372 | 88.986 | 27,1 s |
| valvola | 152.086 | ~598k | ~100 s/tentativo |

Circa lineare con overhead fisso; il totale = tentativi x costo. La
valvola thick-first paga 13 tentativi (1.602 s) contro i 3 del thin-first
(~305 s di build + checkMesh).

