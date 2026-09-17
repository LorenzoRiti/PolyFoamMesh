# H4 — mappatura delle esclusioni per-VERTICE (piano + previsione)

Data: 2026-09-09. Scritto PRIMA di implementare e misurare.
Contesto: FASE 7/8 hanno mostrato che la BL sulla topologia di produzione
(collapsed) della valvola NON chiude con il modello attuale di esclusione
per-FACCIA:
- baseline (decoupled, max_rounds=3, widen=0): fallisce a 0.1 r2 con
  385 pyr vs input 370, 2.189 esclusioni;
- max_rounds=6: plateau a 379 (0.1 r3-r5), 2.207 esclusioni;
- widen=1 (espansione di 1 anello del set per-faccia): IDENTICO a widen=0
  (2.207 esclusioni, stessa traiettoria) — il cascade per-faccia raggiunge
  lo stesso fixed point con 6 round, quindi allargare di un anello non
  aggiunge nulla al fixed point;
- H2: 360/370 dei difetti input sono su facce di bordo (escludibili).

## Perche' un modello per-vertice (razionale)

Nel dual collapsed una faccia di bordo e' una poligonale ~= la stella di
un vertice primale di bordo; i suoi VERTICI (vertici duali sul bordo) sono
condivisi con le facce vicine. Il modello attuale esclude facce intere e
propaga un round alla volta; il vertice condiviso e' la vera unita' di
condivisione topologica del problema (un prisma e' difettoso per come i
suoi vertici si estrudono, non per la faccia in se').

Semantica "vertex-closure": mantengo `excluded_verts` (vertici di parete);
una faccia selezionata viene estrusa solo se NESSUNO dei suoi vertici e'
escluso. Quando un prisma/cella core e' invalido, escludo TUTTI i vertici
della sua faccia di base (non la faccia). Questo rende la propagazione
coerente con la topologia: tutte le facce incidenti a un vertice cattivo
cadono nello stesso round, invece che una alla volta nelle iterazioni.

Nota di design: la modifica riguarda SOLO le esclusioni ITERATIVE; il drop
binario iniziale (`zero_concave`, fade < 0.5 / drop_bnd) resta per-faccia
invariato, per isolare l'effetto misurato.

## Ipotesi da verificare

H4a — I ~9 pyr residui (379-385 vs 370) sono attribuibili a poche facce
KEPT i cui vertici non sono (ancora) esclusi, ma che sono adiacenti (per
vertice) a facce gia' escluse. In tal caso vertex-closure le cattura e la
valvola collapsed CHIUDE.
H4b — I residui provengono da facce i cui vertici NON sono toccati da
alcuna esclusione (difetto non mappabile ne' per faccia ne' per vertice).
In tal caso vertex-closure non cambia il fixed point e H4 fallisce come
widen=1: il problema e' che il gate `n_pyr <= input` non e' raggiungibile
con la costruzione binaria, e serve un'altra strada (es. trattare il dual
collapsed diversamente per la BL).

## Previsione (numeri attesi)

- **P1 (probabilita' di chiusura)**: 45%. Il widen=1 no-op suggerisce che
  il fixed point per-faccia sia gia' "chiuso per adiacenza"; vertex-closure
  coincide in gran parte con quel fixed point. Il margine e' piccolo
  (9 facce), quindi non e' impossibile, ma il segnale e' debole.
- **P2 (se chiude)**: successo a scala 0.1, esclusioni effettive
  (facce senza BL) **2.300-2.900** (poco piu' delle 2.189 per-faccia,
  piu' le facce incidenti extra), prismi ~2x le facce rimaste; checkMesh
  reale atteso con "wrong oriented" <= 340 (input) — non possiamo
  garantirlo ma il gate in-process e' la condizione.
- **P3 (se non chiude)**: fallimento pulito come il baseline, stallo
  <= 385; il set escluso sara' un SUPERSET del fixed point per-faccia
  (2.207 <= escl <= 3.500) e la conta pyr non scendera' sotto ~379.
- **P4 (tempo)**: conversione collapsed ~2 min; run BL 3-6 build
  (~60-100 s l'uno) se chiude (~5-10 min), 15 build (~15-18 min) se
  fallisce. checkMesh +1 min.
- **P5 (casi sani)**: cilindro/cubo/groove/slot: vertex-closure = no-op
  (0 esclusioni), stesso esito del face-level; il fixed point coincide.
- **P6 (regression test)**: nuovo `tests/test_bl_collapsed_valve.py`
  (marker slow; skip se `C:/polybench/valve1/.../polyMesh_tet_backup`
  manca): converte collapsed (converter production kwargs), esegue
  `local_termination="decoupled_vertex"` col protocollo valvola
  (n_layers=2, h1=1e-5, dual_convexity), e pinna l'esito misurato:
  - se chiude: assert success, scale==0.1, esclusioni nel range
    misurato, prismi > 0, + invarianti mesh (senza WSL);
  - se fallisce: assert clean-failure (errors non vuoti, n_prism_cells==0,
    mesh byte-identica) come fa il slow test della fixture exact.

## Criterio di lettura

- Se H4a: implementare `decoupled_vertex` come modalita' opt-in
  documentata (NON default), scrivere i numeri in docs (FASE 9) e il
  regression test con i valori misurati. La lane BL-su-collapsed
  avrebbe la sua soluzione con la topologia di produzione.
- Se H4b: documentare il negativo con la diagnostica delle facce
  residue (perche' la mappatura manca) e la conclusione strutturale:
  la costruzione binaria non chiude la valvola sul dual collapsed; il
  percorso di produzione poly+BL su geometrie concave resta non
  supportato e va deciso a livello di prodotto (es. exact dual per la
  BL, o trattamento dedicato del collapsed).

# RISULTATI (compilati dopo le misure, 2026-09-09)

## H4 SUCCESS — la valvola collapsed di produzione CHIUDE

Misura (`tools/bench_bl_production_topology.py --cases valve
--variants production --modes decoupled_vertex`, default max_rounds=3,
criterio dual_convexity; conversion collapsed ~105 s):

- **success=True a scala 0.6**, 5 build, 296,8-297,6 s;
- **4.008 facce escluse** su 42.130 (9,5%);
- **75.360 prismi** (2 layer x 37.680 facce coperte);
- traiettoria: 1.0 r0 (54 neg) -> r1 383 vs 370 -> r2 373 vs 370
  (round esauriti: il fixed point del vertex-closure a 1.0 NON scende
  sotto il gate); 0.6 r0 (38 neg) -> r1 valida con l'esclusione;
- checkMesh reale: **340 wrong oriented (= input, ZERO aggiunti)**,
  10 errori non-ortho (= input), NOmax 95,615 (= input), skew 27,47
  (input 22,16), aspect max 6.555 (input 100,8), **Failed 4** vs input
  Failed 3 (l'unico check in piu' e' l'aspect ratio, intrinseco ai
  prismi BL sottili come sulla valvola exact).
- max_rounds=6: identico (1.0 si ferma comunque al fixed point a r2;
  0.6 chiude in 2 round). Il gate a scala 1.0 non e' raggiungibile.

## Previsioni vs misure (onesto)

- P1 (chiusura 45%): **SI, ha chiuso** — la probabilita' era troppo
  pessimista; il segnale "widen=1 no-op" non implicava l'inutilita' del
  vertex-closure (che a 0.6 cattura in UN round un set 4x piu' grande).
- P2 (esclusioni 2.300-2.900, scala 0.1): **misurato 4.008 a scala 0.6**
  — piu' facce del previsto (9,5% vs 5,5-7% atteso) ma **scala piu'
  spessa** (0.6) e quindi strato limite migliore del previsto.
- P3/P4 (se falliva / tempi): non applicabili; tempi misurati 297 s
  (5 build), coerenti con la forbice prevista.
- P5 (casi sani no-op): confermato dal unit test sul cubo sano.
- P6 (regression test): implementato in
  `tests/test_bl_collapsed_valve.py` (slow, skip se il tet backup
  manca), pinna success, scala 0.6, escl=4.008, prismi=75.360;
  verificato verde in 406,6 s (6:46).

## Letttura

H4a confermata: il vertex-closure cattura in un round solo la rim che
il modello per-faccia impiegava piu' round a (non) chiudere; la
differenza decisiva e' che il vertex-closure a 0.6 esclude le facce
incidenti ai vertici difettosi TUTTE INSIEME, mentre il per-faccia a
0.1 si fermava 9-15 pyr sopra il gate. La modalita' resta OPT-IN
("decoupled_vertex"); nessun default cambiato. Sul dual exact il
comportamento precedente non cambia (stessa modalita' del resto).
