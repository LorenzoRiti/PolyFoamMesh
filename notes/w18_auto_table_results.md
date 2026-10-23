# W18 §3.4 — tabella auto vs espliciti (misurata 2026-10-24)

Fonte: `C:\polyfoammesh_w18\w18_table.json` (20 righe, driver
`tools/w18_auto_table.py`, settings uniformi detail=medium, dual
production + smooth_free_boundary, checkMesh reale OF2512/WSL2).
Colonna "auto" = simulazione della chain (primary; fallback se
primary invalido/Failed): il meccanismo GUI (§3.3) implementa la stessa
catena (tet-fail → dual-invalid → checkMesh Failed-non-accepted).

Legenda: Q = quality gate dual (neg 0); BLp = bl_produced (non eseguito
in tabella — BL è stage separato, coperto da W13/lane).

| caso | gmsh esplicito | fTet esplicito | auto (chain) | verdetto |
|---|---|---|---|---|
| venturi | dual ok (134152 poli, 5 pyr) → checkMesh Failed (NO 61.58, 1 fail) | dual ok (27369 poli, 1 pyr) → checkMesh Failed (NO 42.95, 1 fail) | gmsh → Failed; fallback ftet → Failed; catena esaurita = errore dichiarato | PAREGGIO (tutti Failed) |
| concave_bracket | Failed (NO 65.54, 2 pyr) | Failed (NO 51.56, skew 4.15 genuine, 2 fail) | gmsh → Failed; fallback ftet → Failed | PAREGGIO |
| dirty_cad | NON eseguibile (input open; riga gmsh del driver = artefatto di fallback, mesh da fTet — vedi nota) | Mesh OK (10785 poli, 0 pyr, NO 25.84, skew 1.15) | fTet primary → Mesh OK, stop | AUTO VINCE |
| mini_valve | dual INVALIDO su 998K tet (gate chiusura, 682 difetti) | dual INVALIDO su 978K tet | gmsh → invalido; fallback ftet → invalido; errore dichiarato | PAREGGIO (tutti invalidi a questi settings; recipe wall03 lr=0.1+split resta il config noto Mesh OK ×2, fuori tabella uniform-detail) |
| thin_gap | dual INVALIDO su 3.2M tet | dual INVALIDO su 3.2M tet | errore dichiarato | PAREGGIO |
| cylinder | Mesh OK (25538 poli, 0 pyr, NO 42.52) | Mesh OK (25476 poli, 0 pyr, NO 42.95) | gmsh primary → Mesh OK, stop | AUTO VINCE/PAREGGIA (primary ok) |
| cube_duct | Mesh OK (11569 poli, NO 49.59) | Mesh OK (11580 poli, NO 41.24) | gmsh primary → Mesh OK, stop | AUTO VINCE/PAREGGIA (primary ok) |
| R1 (Parte4.step 30/09) | tet IMPOSSIBILE (0 solidi: mesh solo 2D, gmshToFoam "no cells") | coarse: dual invalido ×2 (closure + non-manifold); medium: dual invalido ×2 (non-manifold stesso edge, anche con recipe wall03) | R1 spento per input: gli unici solidi/watertight sono la revisione 18/05 (vedi sotto) |
| R1 (Parte4.stp 18/05: 1 solido) | GMSH volume ok (coarse) → dual ok (761933 poli, 787 dif: 531 pyr/253 non-ortho/3 skew) → BL 5L RIFIUTATO (all heights fail) → checkMesh dual-only **Failed 3** (523 bad pyr, NOmax 105.33, skew 25.57). Medium (7.1M tet): dual non tentato (muro RAM, WALL-04) | auto primary gmsh: tet ok, criterio NON raggiunto; fallback ftet = riga sopra |
| R1 (Parte4.stl 18/05: watertight, 68798 facce) | fTet eps 0.3: 73337 tet → dual ok (18165 poli, **58 pyr**) → BL 5L ok (58965 prismi, coverage **100%**, h1 0.509x) → checkMesh **Failed 3** (160 bad pyr, NOmax 141.13, skew 40.49) | ESITO: criterio R1 NON raggiunto (pyr/skew/Failed fuori barra; solo coverage ok). Solo il direct-BL 1L (W13, opt-in) lo centra — fuori auto per design |

Note misurate:
- Dirty_cad/gmsh: il driver ha registrato la mesh di fallback come
  colonna gmsh (effective_source=ftetwild nei tet.stats:
  epsilon_abs/tagging = firma fTet). L'esito esplicito corretto è
  "GMSH non carica input open" (wall03). Corretto in analisi, non
  rigenerato (evidenza conservata nel JSON).
- Dual-invalid su ~1M+ tet (mini_valve/thin_gap gmsh, thin_gap ftet):
  il gate di chiusura scatta prima del checkMesh su mesh enormi a
  detail=medium uniforme. Lane A usava sizing diversi (89K/33K celle).
  Il confronto auto-vs-esplicito resta fair (stessi settings); i numeri
  assoluti NON sono confrontabili con Lane A.
- dirty_cad medium: fTet chiude il buco (envelope) → dual valido Mesh OK;
  a lr=0.1 (wall03) il buco resta e il dual rifiuta (corretto). Dettaglio
 -dipendente, dichiarato.
