# W1 in-flight reasoning — 2026-10-22

Previsioni scritte PRIMA delle misure A/B (protocollo W1, ROADMAP.md
«W1 — Chiusura in-flight»). Tre fronti: clearance batched + shift opt-out
(fTetWild), eps default 0.3 vs 0.03 mm, smoothing 3→2 iterazioni.

## Fronte 1 — clearance batched + opt-out `shift_boundary_skew`

**Stato di partenza**: `shift_boundary_skew` misurato non-monotono sui
dual fTetWild (pulisce qualche skew ma crea piramidi); la clearance
batched (backtrack 1.0/0.5/0.25 + guardia volume, 11/11 sul medium)
è l'unica strategia che abbassa i difetti senza crearne. Wiring
produzione: `openfoam_runner.py` (`shift_boundary_skew = not
smooth_free_boundary`); benchmark fTetWild: `shift_boundary_skew=False`.
**Previsione**: l'opt-out sul percorso fTetWild mantiene i difetti residui
a Failed 3 (27 pyr + 59 skew baseline) senza peggiorarli; con clearance
batched i pyramid residui scendono (già misurato 11/11 sul medium).
**Segnale di smentita**: se con shift OFF i pyramid residui salgono sopra
il baseline (27), la misura confuta e lo shift resta ON con clearance solo.

## Fronte 2 — eps default 0.3 vs 0.03 mm

**Stato di partenza**: eps assoluta 0.3 mm (milestone A, decisione utente
2026-10-19 «quality first»); measure_eps03 completata il 19/10:
**2,370,773 tets in 3738 s (~62 min), max_energy 7.55, avg 3.39**.
Confronto con eps=1e-3 relativa (= 3 mm su pezzo 3 m): 1424 s tet.
**Previsione**: 0.3 mm è il default giusto (fedele: deviazione ≤ 0.3 mm
garantita dall'envelope) al costo di ~2.6× il tempo tet del default
relativo; 0.03 mm aumenterebbe il tempo di almeno ~3-5× ulteriormente
(≈ 3-5 h tet fine) per un guadagno di fedeltà sotto 0.03 mm non
richiesto dall'utente («eps 1e-4/1e-5 fedeli» = 0.3 mm assoluto su pezzo
3 m). Default confermato 0.3 mm.
**Segnale di smentita**: se 0.03 mm su corpus rispetta il tempo E2E fine
≤ 15 min (tet ≤ 700 s) — improbabile dato 62 min per tet a 0.3 mm —
la decisione va riveduta.

## Fronte 3 — smoothing 3→2 iterazioni (A/B su corpus)

**Stato di partenza**: `smooth_iters=3` default nel duale (`tet_poly_dual.py`,
wiring `--ftet-dual-smooth-iters` nel benchmark aggiunto 2026-10-22).
Baseline corpus fTetWild (smooth=3, 20261022T000125_ftetwild.json):
15 geometrie, 70.3 min totale; per stadio duale: 8.7 s (concave_bracket),
1.25 s (cube_duct), 0.28 s (cylinder), difetti residui 3/0/0 su
concave_bracket.

**Previsione**: da 3 → 2 iterazioni, il tempo duale scende ~30-40% (una
iterazione Laplacian in meno su ciascuna geometria); i difetti checkMesh
(campionati NO/skew/aspect) restano entro il rumore: la smoothing è
keep-best con floor planarity, quindi 2 iterazioni converge a un ottimo
leggermente peggiore ma dentro la banda. Aspettami: duale fine ≤ 130 s
totale per corpus (era 294 s baseline ROADMAP pre-ottimizzazioni),
differenze difetti ≤ ±5% rispetto a smooth=3.
**Segnale di smentita**: se su ANY geometria del corpus i difetti
(pyramid/non-ortho/skew) peggiorano oltre il ±5%, o il floor dihedral ≥
10° / p1 ≥ 30° si rompe, lo smoothing resta a 3 (scarto documentato).

## Verdetto (post-misura)

Da compilare al termine della battery A/B (smooth=2 vs baseline smooth=3
in `benchmarks/results/20261022T000125_ftetwild.json`).
