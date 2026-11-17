# WORKLOG — registro condiviso del lavoro

Convenzione (richiesta utente, 2026-11-16): ogni sessione aggiunge qui
una sezione datata con commits, misure e aperti. Le voci del
`CHANGELOG.md` vengono redatte INSIEME a partire da questo registro
(l'utente revisiona, l'agente scrive). Niente push senza ordine
esplicito; i due file utente (`benchmarks/run_comparison.py`,
`tests/test_run_comparison.py`) non si toccano mai.

---

## 2026-11-16 — fTetWild/DN1000: chiusura loop, poly-gate, refinement quotidiano

### Commit
| SHA | Contenuto |
|---|---|
| `ca272e0` | Circuit breaker anti-loop GMSH (`PartitionLoopBreaker`): a 2000 ripetizioni di "Partitioning face N ... same partition" kill via watchdog + catena con fallimento nominato; log GUI consuma lo spam (1 riassunto/500). Test: 37 verdi. |
| `7b3e9f9` | Handover con A/B `opt_iter` su DN1000 (knob scartato: ±4 s). |
| `7bf4a9f` | Piano fallback dichiarato all'avvio (3 rami auto) + nota `FAIL subdivide_tets` + `--geom` nell'E2E + docs. |
| `1e815a4` | A/B fine opt_iter (R1/Parte4, eps 0,3 mm): cap 6 = **+43% tempo** → previsione confutata, default 15 confermato. |
| `696ee38` | **Fix "tet veloce ma niente poly"**: il dual+BL era gateato dietro il checkMesh delle tets grezze (che fallisce quasi sempre) → al primo fail parte UNA volta (dichiarato), il checkMesh dopo il dual è autorevole. + viewer default "Surface with Edges" (CAD incluso) + last-chance "sempre poly" (eps allentato ×2, cap [20,30%], dichiarato) + `reference_first_height` fail-soft + `--geom`. |
| *(in tree, test mirati verdi)* | **Batch refinement quotidiano**: Reset ammazza i task in corsa; righe di task fermi/annullati scartate dal relay; Cancel → verdetto "cancelled" (mai "finished"); FAIL subdivide_tets coalescito (nota + riassunto/20, SEMI-FAIL incluso); doppio tag `[tet:ftetwild]` eliminato; caricamento geometria rifiutato durante un run; patch STL "ASCII" → nome file (+ auto-split riattivato, dichiarato); h1 BL clampato anche su max_cell/3 col motivo a log; battito dual 8→30 s. |

### Misure chiave (casi reali utente)
- **DN1000**: AUTO VALID POLY_BL in **66,6 s** (BL 29.640 prismi, cov 100%, checkMesh Mesh OK NO 65,5 / skew 2,78 / 0 difetti). Prima: ore in loop di partizione (>500k tentativi).
- **gmshprova**: AUTO VALID POLY_NO_BL in ~57 min — dual 191.527 celle poly, 0 tet residui, chiusura 1,95e-16; keep-best su GMSH (fTet challenger Failed NO 69,3/skew 6,59).
- **venturi_dn200.stp**: superficie GMSH macina UNA faccia in MeshAdapt >2h (mistero mirror risolto); in GUI il budget auto 120 s protegge → fTet su patch.
- **opt_iter**: coarse ±4 s; fine cap 6 = +43% (converge da solo a pass 9). Default 15 ovunque.
- **Ampiezza in corso**: universal sweep 10 geometrie (8 corpus + final.step + prendiquesta), cap 40 min/caso → `C:/cfmesh_cases/universal_sweep_results.json`. Parziali: cylinder POLY_BL 28,6 s; cube_duct POLY_BL 11 min.

### Bug utente chiusi oggi
1. "ore in loop, log non esplicativo" → breaker + log nominato (DN1000).
2. "tet veloce ma niente poly e niente BL" (venturiprovabaram.stl, 9 run in catena) → poly-gate fix (`696ee38`).
3. "processi becchi dopo stop/reset" → Reset cancel_all + relay guard + verdetto onesto.
4. "FAIL subdivide_tets = errore?" → nota + coalescenza.
5. "h1 suggerito 0,457 m" → clamp mesh-fit (max_cell/3); Nota: il valore usciva da velocity ~0,02 m/s — input da rivedere nel pannello.

### Aperti (per il prossimo giro)
- Sweep universale: completata **7/10 AUTO VALID** (6 POLY_BL +
  1 POLY_NO_BL, tutte 0 tets residui); 3 inconclusive per cap del
  mirror (multi_inlet: grind silenzioso >4h, GUI protetta da budget
  120 s; final_step: tet >40 min a medium, GUI ceiling 4 h;
  prendiquesta: Delaunay 3D >40 min). Venturi: GMSH Failed ->
  **fallback ftetwild -> Mesh OK** (storico W18: tutti Failed).
- Run di conferma GUI dell'utente (riavvio + final.step + velocity/h1 a posto).
- Firma PARTIAL 2.4.0 (R1 per-difetti, corpus, installer Linux) — utente.
- Rebuild frozen/installer se serve l'exe — su richiesta (~10 min).
- Refinement documentati non iniziati: gap aspect cilindro (SPLIT direzionale), dual-5M RAM (compact-face), taper per-face, dual venturi 74,0 + piramidi concave, benchmark robust-ON da rifare.
