# Confronto GMSH / fTetWild / snappy con BL: previsione (2026-11-10)

Scritta prima del run di `tools/compare_mesher_bl.py` su 14 geometrie.

## Metrica
Conta solo `Mesh OK` **con BL reale** (prismi > 0 per poly, strati medi > 0 per
snappy). Un fallback non viene accreditato al mesher richiesto.

## Previsione
- GMSH poly+BL: 6-9/14 `Mesh OK` con BL. Base: Lane A 9/10 era solo tet->dual;
  il BL toglie validità sui casi con pyr residue (venturi-gmsh).
- fTetWild poly+BL: 5-8/14, più lento; migliore su dirty_cad e STEP senza solidi.
- snappy expert: 10-13/14 `Mesh OK`, ma con copertura strati parziale
  (spessore medio < 70% sui casi con spigoli vivi).

## Lettura
- Se GMSH supera fTetWild di >= 2 casi con BL, il default GMSH resta.
- Se un engine poly arriva a >= 10/14 con BL, il claim "meglio di snappy"
  si può discutere sui difetti; altrimenti per il 16 niente claim su snappy.
- I due BL non sono confrontabili uno a uno (5 strati poly vs template expert).
