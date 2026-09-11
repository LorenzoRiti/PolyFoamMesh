# Sizing And Estimates: predicted (geometric) vs actual cell count

Data: 2026-09-09. Scritto PRIMA della misura. La sezione
`docs/residual_risks.md` "Sizing And Estimates" riporta: "The Mesh
Fineness slider value is a budget/cap (10K..20M cells), not a
guarantee. The displayed geometry estimate is derived from the
tessellated solid volume and the derived cell sizes; when the volume
cannot be trusted (open/non-watertight tessellation) the estimate is
omitted rather than invented."

Cioe': la stima del Mesh Fineness slider e' un *budget* e un'etichetta
informativa, non una garanzia. E' basata sulla formula geometrica
(`estimate_cell_count_geometric` in `core/geometry.py`) e sui patch
sizes da `compute_patch_cell_sizes`. Il calcolo del cell count
predetto e' una somma pesata (bulk + boundary refinement) che non
cattura tutto cio' che il mesher fa (es. fattori di packing,
graduazione, inflazione di boundary layers).

## Misura

`tools/sizing_estimate_rebench.py`:
1. Costruisce il cylinder di GMSH a 3 livelli di fineness
   (coarse/medium/fine).
2. Per ogni livello: calcola la predizione via
   `estimate_cell_count_geometric(meshes, volume, max_cell, min_cell,
   patch_sizes)`.
3. Conta le celle effettive dal polyMesh scritto.
4. Stampa rapporto pred/actual.
5. Costruisce un secondo case con `tessellate_patches` di un cubo
   CON un buco (non-watertight) e controlla che la funzione di
   stima rifiuti (ritorni vuoto, "omitted") — questo e' il caso
   "omitted rather than invented" della sezione.

## Cosa mi aspetto

- La predizione e' un ordine di grandezza (cfr. il commento storico
  nel codice "estimate_cell_count: ... (a few boundary-layer-ish
  cells)"). Per un cylinder denso (medium, h ~0.005 m, L ~2 m, r=0.5
  m) il bulk ~ volume / h^3 ~ 6.3 / 1.25e-7 ~ 5e7 cells (assurdo);
  la formula reale e' pesata con patches e "near-wall refinement".
  Misureremo direttamente per il cylinder, senza assunzioni a
  priori. Predico: il rapporto pred/actual sara' fra 0.3 e 3.0 sui
  tre livelli (cioe' un ordine di grandezza), il che e' gia'
  informativo sul trade-off "budget/cap, not a guarantee".
- Caso non-watertight: la formula torna None/vuoto; nessun numero
  inventato, come documentato.

## Criterio di lettura

- Se la predizione e' entro ~2x (tipico per mesh regolari), il
  trade-off "budget/cap" regge; documento un rapporto per livello
  per dare al product/UX un dato su dove la stima devia.
- Se la deviazione e' 3x o piu', segnale di una formula che non
  cattura qualche effetto del mesher (es. growth near features,
  packing); documento e propongo: o un fallback a una stima piu
  conservativa, o un log di warning quando la deviazione pred/actual
  al primo run effettivo e' alta (se misurabile a runtime), o
  semplicemente un fix al calcolo (nella formula, non al default).
- Nessun default cambiato per la sola misura.

Working dir: `C:/polybench2/sizing_estimate_rebench/`.
