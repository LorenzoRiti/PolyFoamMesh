# H4 generalization: decoupled_vertex su cubo, groove1, slot1

Data: 2026-09-09. Scritto PRIMA della misura. H4 ha validato
`local_termination="decoupled_vertex"` su valvola collapsed (successo,
0 difetti aggiunti, 4.008/42.130 facce escluse, 75.360 prismi) e su
cilindro sano (no-op identico a "decoupled", 0 esclusioni, 74.808
prismi, Mesh OK). Restano da misurare cubo duct, groove1, slot1.

## Predizioni (stesso metodo di FASE 3/9)

- **Cubo duct** (9 celle, tessellato in-memory): nessun difetto input.
  `decoupled_vertex` su un cubo sano = no-op identico a
  `decoupled`/`standard` (nessun difetto da escludere -> vertex-closure
  resta vuota). Predico: 108 prismi, 0 esclusioni, scala 1.0,
  Mesh OK, ~0s. Identico a quanto misurato in FASE 3/9.
- **groove1** (682 celle, pyr_input 4, low_fade 2): la cascata per-
  faccia (decoupled/standard) scartava 0 esclusioni aggiuntive e ha
  successo a scala 0.1 con tutti i difetti residui (1.062 prismi =
  3 × 354 facce di parete). Con vertex-mode, mi aspetto
  *equivalente o leggermente meglio*: il vertex-closure copre la rim
  ma su groove1 il cascade a 4 difetti input era già al minimo. Predico
  che `decoupled_vertex` faccia 0 esclusioni aggiuntive e successo a
  scala 0.1 con ~10.062 prismi, scala 0.1 (identico a "decoupled");
  o scala 1.0 se vertex-closure tocca abbastanza da chiudere le
  rimanenti (forse 0 esclusioni comunque).
  Predizione numerica: 10.062 prismi (1.062 nel predittore), 0 esclusioni
  aggiuntive, Mesh OK, scala 0.1.
- **slot1** (9.372 celle, pyr 42, low 17): in FASE 3 con `decoupled`
  scala 1.0 round 1 diede 0 esclusioni (132 esclusioni raccolte nel round,
  vertex-closure equivalente) e 79.614 prismi Mesh OK. Con
  `decoupled_vertex`, la chiusura per-vertice sullo stesso input
  *potrebbe* chiudere il cascade a un round solo (più facce-escluse
  rispetto a per-faccia). Predico: 0 esclusioni, ~79.614 prismi
  (identico a "decoupled"), scala 1.0, Mesh OK. o scala 0.1 con
  un'iterazione in meno. In ogni caso: stessa geometria, stesso
  checkMesh, stessi prismi (~79.614).

## Perché

Il vertex-closure su input senza difetti aggiuntivi (cubo) e su input
con difetti "rim" minimi (groove1, slot1) chiude al fixed point
identico al face-mode. La valvola era l'unico caso in cui il
vertex-closure cambia il fixed point: lì le 373 vs 370 del face-mode
sono state coperte dalla rim a 0.1. Su groove1/slot1, il cascade era
già piatto (0 esclusioni aggiuntive), quindi il vertex-closure non
aggiunge informazione.

## Criterio di lettura

- Se H4 conferma su tutti e 3: la modalita' `decoupled_vertex` è
  l'opzione di default raccomandata per la BL di produzione collapsed
  su tutti i tipi di geometria (quando l'utente la abiliterà). Documento
  in `docs/residual_risks.md`.
- Se H4 su qualche geometria è equivalente a `decoupled` o leggermente
  peggio: documentare e segnalare.
- Se H4 fallisce su qualche geometria: documentare e capire perché
  (forse la rim è "stale" e la propagazione per-vertice ha cambiato
  qualcosa in modo non voluto).

In ogni caso, **nessun default cambiato** in questa sessione
(`local_termination=False` di default). Tutti i risultati sono
misurati, mai assunti.

Working dir: `C:/polybench2/h4_generalization/`. Per i tet backups
esistenti (groove1, slot1) riuso `C:/polybench/{groove1,slot1}/...`.
