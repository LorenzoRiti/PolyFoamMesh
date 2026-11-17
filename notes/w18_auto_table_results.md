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
| R1 (Parte4.step 30/09) | tet IMPOSSIBILE (0 solidi: mesh solo 2D, gmshToFoam "no cells") | coarse: dual invalido x2 (closure + non-manifold); medium: dual invalido x2 (non-manifold, anche recipe wall03) | input rotto: usare revisione 18/05 |
| R1 (Parte4.stp 18/05: 1 solido) | GMSH coarse: tet ok -> dual ok (761933 poli, 787 dif: 531 pyr/253 non-ortho/3 skew) -> BL RIFIUTATO (all heights fail) -> dual-only checkMesh Failed 3 (523 bad pyr, NO 105.3, skew 25.6). Medium (7.1M tet): dual non tentato (muro RAM, WALL-04) | auto primary gmsh: tet ok, criterio NON raggiunto | PAREGGIO col miglior esplicito (tutti Failed tranne direct) |
| R1 (Parte4.stl 18/05 fTet, conditioned) | dual ok (196547 poli, 6 pyr) -> BL 5L ok (195085 prismi, cov 100%) -> checkMesh Failed 3 (0 pyr, NOmax 159.2, skew 165.1) | auto fallback ftet: dual valido, BL valido, checkMesh fallito su skew | PAREGGIO (dual path ovunque Failed) |
| R1 (Parte4.stl 18/05, dual+split_boundary_defects) | dual ok, **0 difetti**, Mesh OK (NO 65.4, skew 3.9) -> +BL 5L: Failed 3 (dati in arrivo) | split non in auto (esplicito wall03) | miglior dual-only misurato; BL pending |
| R1 (Parte4.stl 18/05, direct-BL 5L, tet condizionati) | tet diretto, BL ok (353055 prismi), checkMesh **Mesh OK** (NO 71.2, skew 2.9, 0 pyr, 0 fail), coverage **100%** (74089 wall faces) — con local_termination decoupled: stesso esito | **CRITERIO R1 RAGGIUNTO sotto lettura-per-difetti** (0 pyr / 0 skew-fail / 0 Failed / 100% cov); max-skew 2.89 dichiarato | auto NON include direct-BL (opt-in W13); ammissione = decisione con questi numeri |
| cylinder | Mesh OK (25538 poli, 0 pyr, NO 42.52) | Mesh OK (25476 poli, 0 pyr, NO 42.95) | gmsh primary → Mesh OK, stop | AUTO VINCE/PAREGGIA (primary ok) |
| cube_duct | Mesh OK (11569 poli, NO 49.59) | Mesh OK (11580 poli, NO 41.24) | gmsh primary → Mesh OK, stop | AUTO VINCE/PAREGGIA (primary ok) |
| DN1000 reale (STL di ripiego, 2026-11-16) | n/a (superficie GMSH muore: "periodic surface 65", 370-390 s) | fTet al primo anello: tet -> dual -> BL **29640 prismi cov 100%** -> checkMesh **Mesh OK** (NO 65.5, skew 2.78, 0 difetti) | **AUTO VALID POLY_BL in 66,6 s** (`tools/auto_chain_e2e.py --geom`, mirror GUI) | AUTO VINCE (casa utente reale) |
| gmshprova.stp (reale, 2026-11-16) | tet -> dual -> **191.527 celle poly, 0 tet residui, chiusura 1.95e-16** -> checkMesh Mesh OK | challenger ftet: tet ok, dual -> checkMesh **Failed** (NO 69.3, skew 6.59) — scartato keep-best | **AUTO VALID POLY_NO_BL in 57 min** (niente BL: h1 default dello strumento, dichiarato; in GUI vale l'h1 utente) | AUTO VINCE |
| venturi_dn200.stp (reale, 2026-11-16) | superficie GMSH **macina una singola faccia in MeshAdapt >2h** (bloccata al 10% della superficie 49, nessun output) | non raggiunto nel mirror | mirror inconclusivo (nessun budget auto nell'E2E); **in GUI il budget 120 s protegge l'utente** -> fTet su patch | n/a (mirror) |

## Universal sweep (2026-11-17, catena su HEAD col fix poly-gate)

10 geometrie in sequenza (cap 40 min/caso, mirror E2E). **7/10 AUTO
VALID, tutte con 0 tets residui** — il fix poly-gate regge su tutta
la breadth:

| caso | esito | tempo | note |
|---|---|---|---|
| cylinder | AUTO VALID POLY_BL | 29 s | gmsh al primo anello |
| cube_duct | AUTO VALID POLY_BL | 11 min | keep-best: ftet sfida e vince |
| multi_inlet | n/a (mirror) | cap | grind silenzioso GMSH >4h su discrete surface; GUI protetta da budget 120 s |
| groove1 | AUTO VALID POLY_BL | 2,9 min | keep-best ftet |
| slot1 | AUTO VALID POLY_NO_BL | 6,6 min | WEAK, ftet; BL 0 prismi (h1 tool default) |
| concave_bracket | AUTO VALID POLY_BL | 3,8 min | keep-best gmsh |
| dirty_cad | AUTO VALID POLY_BL | 25 s | ftet al primo anello (input open) |
| venturi | AUTO VALID POLY_BL | 11,8 min | **gmsh Failed -> fallback ftet Mesh OK** (storico W18: tutti Failed) |
| final_step (utente) | n/a (cap) | 40 min | fTet tet a medium >40 min; GUI ceiling 4 h |
| prendiquesta (utente) | n/a (cap) | 40 min | Delaunay 3D >40 min; GUI ceiling 4 h |

Verdetto breadth: **la catena auto ora produce una mesh poly valida su
7/7 casi completabili, con fallback dimostrato in diretta (venturi) e
keep-best reale (cube_duct, groove1)**. I 3 cap sono limiti del
mirror (nessun budget/ceiling), non del prodotto.

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

## Verdetto W18 follow-up (2026-10-24 sera) - PARTIAL confermato

Direct-BL 5L (tet condizionati run_volume, stesse impostazioni):
- R1: Mesh OK, 0 pyr, 0 fail, coverage 100% (353K prismi) - CRITERIO RAGGIUNTO
- venturi: Mesh OK, 0 pyr, 0 fail, coverage 76% (thin features scoperte)
- pipe/thin_gap: NON-BL (5/60 edge non-manifold: topologia, non altezza)
Split dual+BL su R1: dual 0 difetti (Mesh OK) ma BL Failed 3 (skew 165) -
il dual pulito non basta quando i prismi collassano su thin features.
Taper uniforme implementato+testato (keep-best, provenance, no-op invariato,
fallback dual/checkMesh esteso alla gamba direct con skip del dual).
Rescue reale non misurato: i fallimenti totali osservati sono topologici o
difettivi; il per-face taper resta mappato ma non implementato (nessun caso
lo richiede).
Auto resta SENZA gamba direct (implementata, testata a livello decisionale,
E2E GUI da eseguire): ammissione = decisione utente con questi numeri.
M3+M4 resta PARTIAL.
