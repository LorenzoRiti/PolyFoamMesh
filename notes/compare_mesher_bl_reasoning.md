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

## Esito primo corpus (2026-11-10) contro previsione
- GMSH 2/14 con BL (previsto 6-9): sbagliato. 10/14 run "gmsh" sono in realtà
  fallback su fTetWild (STL), quindi GMSH puro non è confrontabile qui.
- fTetWild 4/14 (previsto 5-8): sotto. snappy 11/14 (previsto 10-13): giusto.
- Causa dominante dei Failed poly: piramidi di bordo residue del DUAL (conteggio
  del dual = errori checkMesh), non il BL né il mesher. Il BL rompe da solo
  solo cube_duct/gmsh (skew 4.7, h1 effettivo 13x il richiesto).
- Scoperto: gli script misuravano un dual con opzioni diverse dalla GUI.

## A/B dual sugli stessi tet (28 mesh, 12 geometrie)
difetti totali / casi a zero: GUI-GMSH 1313/6, GUI-fTet (split) 389/6,
W18 514/10, bordo libero+split 230/12. Bordo libero = parete spostata in media
3% della cella (vs 0.6% bloccato): più della prima cella BL. Non adottato.

## Previsione per il cambio (prima della misura)
Cambio: percorso GMSH con le opzioni del percorso fTetWild (bordo bloccato,
split piramidi, niente shift). Fedeltà invariata (0.62% vs 0.59%).
Previsto sul corpus reale, run "gmsh": `Mesh OK` con BL da 1 a 3-4 su 14;
nessun caso oggi `Mesh OK` deve diventare Failed (rischio: box_obstacle,
dual da 1 a 5 difetti). Se un caso regredisce senza compensazione, niente cambio.

## Esito del cambio (BL + checkMesh reali, stessi tet, 44 run)
`Mesh OK` con prismi > 0: opzioni vecchie GMSH 5/22, opzioni uniche 7/22
(+box_obstacle/ftetwild, +multi_inlet/gmsh). Nessun caso OK diventa Failed:
previsione rispettata, cambio adottato. box_obstacle/gmsh resta Failed in
entrambe (2 errori -> 1). Ancora aperto: su 12/22 run il BL viene rifiutato
(0 prismi) e il dual ha difetti sui casi con spigoli concavi.

## Lettura
- Se GMSH supera fTetWild di >= 2 casi con BL, il default GMSH resta.
- Se un engine poly arriva a >= 10/14 con BL, il claim "meglio di snappy"
  si può discutere sui difetti; altrimenti per il 16 niente claim su snappy.
- I due BL non sono confrontabili uno a uno (5 strati poly vs template expert).
