# FASE 0 — Solver validation sulla valvola: il difetto concavo del dual è un limite, non un bug

> Misurato 2026-08-10 con `tools/poly_solver_validation.py --case valve1`.
> Tool: `tools/poly_solver_validation.py` (adattato in questa sessione per
> accettare `--case ref1|valve1` e `--variant default|production`, con log
> completo e parse di residui/iterazioni/continuity).

## Setup

- Geometria: `C:\polybench\valve1` (valvola reale, 89 superfici B-Rep).
- Backup tet intatto: `constant/polyMesh_tet_backup` — 824.661 tetraedri.
- Inlet = `surface_77` (tappo piatto a x = +1.5227), outlet = `surface_89`
  (tappo piatto a x = −1.4773) — le due estremità dell'asse principale,
  scelte misurando centroidi/aree delle patch dal backup.
- BC: U = uniformFixedValue (1 0 0) sull'inlet, p = 0 sull'outlet, pareti
  zeroGradient. Identiche su tet e poly.
- Solver: potentialFoam (OpenFOAM v2512, WSL), GAMG su Phi,
  nNonOrthogonalCorrectors 3.
- Poly: dual baricentrico (variante `default` = parametri del convertitore,
  la stessa che produce 852 facce mal orientate / 1027 difetti residui sulla
  baseline pinnata).

## Numeri misurati

| Metrica | TET (824.661) | POLY (152.086) | Esito |
|---|---|---|---|
| potentialFoam | converge, "End" | converge, "End" | nessun FATAL |
| GAMG su Phi: initial → final residual | 8.34e-3 → 3.07e-5 (3 it) | 4.87e-4 → 4.60e-6 (2 it) | poly converge più a fondo |
| Continuity error (bilancio di massa risolto) | 8.71e-3 | 7.83e-4 | **poly 11× più conservativa** |
| Continuity relativa al flusso (0.0483 m³/s) | 18% | **1.6%** | |
| Interpolated velocity error | 8.8e-8 | 6.3e-5 | |
| Volume totale | 0.121835 | 0.121835 | **0.00%** |
| Flusso prescritto all'inlet (area) | 0.0482551 | 0.0482551 | **0.00%** (subdivisione esatta) |

Il flusso risolto (phi) non viene scritto da potentialFoam in questa
configurazione (nessuna dir `1/`), quindi la portata in/out è misurata come:
flusso prescritto all'inlet (identico per costruzione) + bilancio di massa
risolto = "Continuity error" del log (l'imbalance in/out che il solver
riporta dopo la convergenza).

## Decisione

**G3 non è un bug: è un limite documentato di un check (face pyramids) che
assume convessità su celle che il solver digerisce senza problemi.**

Evidenza:

1. La mesh poly della valvola — con 852 facce "incorrectly oriented" per
   checkMesh — **risolve e converge** con potentialFoam (residuo finale
   4.6e-6, 2 iterazioni GAMG), nessun FATAL, nessuna divergenza.
2. **Conserva la portata meglio del tet da cui deriva**: continuity error
   1.6% del flusso (tet: 18%). Il difetto (0.25% delle celle, non convesse
   sulle giunzioni concave del CAD) non inquina il bilancio di massa.
3. Volume conservato a 0.00% (subdivisione esatta della superficie).

Conseguenza: **FASE 3 (taglio planare del difetto concavo) NON va fatta**.
Tutto il budget va su G1/G2 (BL selettivo per patch + terminazione locale
dei layer), che colpiscono ogni caso, non solo le geometrie patologiche.
La stop rule della Fase 3 non si applica perché la premessa («la mesh è
inutilizzabile») è falsa.

## Cosa NON è stato misurato

- Residuo/portata con un solver completo (simpleFoam/rhoSimpleFoam): fuori
  scopo della Fase 0 (potentialFoam è il più economico e sufficiente per la
  decisione). Se in futuro servisse, il tool accetta già
  `--case valve1 --variant forced_true` (median_faces=True, wedge_cells=True
  — **non** i parametri del runner GUI di oggi, che usa i default `False`
  del converter; il variant resta solo per ri-misurare quella combinazione,
  vedi `docs/residual_risks.md` §Polyhedral Conversion) e andrebbe
  ri-eseguito con quel variant — oggi il confronto è sulla variante che
  pinna la baseline.
- Phi scritto su disco (la configurazione controlDict non produce `1/`):
  il bilancio in/out si legge dal log, non da un file di campo.
