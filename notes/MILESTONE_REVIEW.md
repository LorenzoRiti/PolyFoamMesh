# MILESTONE REVIEW M4 — audit indipendente (2026-10-23)

Data: 2026-10-23. HEAD: `30faf8e`. Versione: 2.3.0. Procedura:
`.opencode/prompts/MILESTONE_REVIEW.md`. Questo file unico assorbe i ruoli
diagnosi+reasoning+verdetto (niente doppioni). Reviewer indipendente:
ogni numero sotto è rieseguito o riloggiato oggi, mai citato a memoria.
M2 resta NOT CLOSED (ROADMAP); nessuna review M3 è mai stata scritta —
questo audit copre Exit M3 + Exit M4 insieme.

Baseline: working tree con sole modifiche utente preesistenti
(`benchmarks/run_comparison.py`, `tests/test_run_comparison.py`) +
untracked noti; PDF generato untracked. Niente binari tracked
(gate repo-clean PASS 23/10).

## 1. W17 verificato davvero (ri-eseguito oggi, non citato)

| Stage | Misura 23/10 | Fonte |
|---|---|---|
| versions | PASS, 2.3.0 ×3 | gate output |
| spec-static | PASS, 12 excludes, nessun amputato | gate output |
| smoke | PASS, gmsh ok, pytetwild ok | gate output |
| repo-clean | PASS, installer/ == allowlist | gate output |
| e2e-flow | PASS, venturi 3447 tet → 1082 poli, vol-err 1.7e-16, 5.8 s | gate `--e2e` |
| checkmesh | rc=0, verdict **Failed** (NOmax 90.82, 154 piramidi, skew 25.7) = WALL-01 | `C:\polyfoammesh_gate\checkmesh.log` |
| build | PASS, bundle 195 s, `dist/PolyFoamMesh/PolyFoamMesh.exe` + `_internal/pytetwild` + `gmsh-4.15.dll` verificati | gate `--build` |
| frozen smoke | exe vivo 45 s offscreen, nessun crash (killato) | run manuale |
| assemble | SKIP — ISCC assente (limite dichiarato, resta scritto) | gate output |

Fix emersi dalla verifica (dentro questo audit, non nascosti): COLLECT
falliva su `dist/` non vuota → `--noconfirm` in gate+spec+docs; il case
E2E non aveva skeleton → riusato `write_control_dict` + fvSchemes/
fvSolution della GUI invece di testo a mano.

## 2. Tabella exit criteria

| Criterio | Atteso | Misurato | Fonte | Verdetto |
|---|---|---|---|---|
| M3 preset funzionanti | default da misure | W10 + evidence pinanti (W16, 10 test) | tests/test_docs_numbers.py | MET |
| M3 corpus senza configurazione | run unattended | poly 23/10: 15/15 eseguiti, 0 crash/infra-fail; quality 3/15 green, resto = muri dichiarati | `benchmarks/results/20261023T172959_poly.json` | PARTIAL |
| M3 docs completi | guide+report+numeri | W16: USER_GUIDE/README/CHANGELOG + report v2 +51pp + test numeri | commit 6c5b119 | MET |
| M3 installatori generati | win+linux+setup | bundle win fresco + smoke ✓; ISCC ✗ assente; linux ✗ no host | gate `--build`, dist/ | PARTIAL |
| M4 lane lunghe con verdetto | misurate | W11 MET, W13 MET opt-in, WALL-03 MET, WALL-05 MET-con-residui, WALL-01/02/04 misurate-aperte, REORG chiuso | GOAL + note wall_* | MET |
| M4 benchmark finale | corpus completo | poly 23/10 + ftetwild 23/10 (15/15 ciascuno); nessuna regressione mesh vs Lane A (identità mini_valve/venturi/multi_inlet; resto stabile/migliore); snappy non ri-eseguito (dichiarato) | JSON 23/10 vs comparison_20261006 | MET |
| M4 residual risks | aggiornato | sezione W17+finale scritta oggi | docs/residual_risks.md | MET |
| R1 rieseguito (casi obbligatori) | prima/dopo + checkMesh | NOT RUN: `mesh.msh` 22.6MB + log TEMP + copia W13 spariti; nessuna Parte4 a provenienza certa | ricerca disco 23/10 | NOT RUN |

## 3. Audit strutturale

- Default/flag: nessun default promosso in W17/chiusura (`--e2e/--build/
  --assemble` opt-in; `bl_produced` hard gate è dell'harness benchmark,
  non dell'app — semantica diversa da Lane A, dichiarata, non cambiata
  qui). Nessun fallback silenzioso nuovo.
- Debiti: R1-input (owner: utente — fornire CAD Parte4 o accettare i
  numeri W13 come verità R1 finale), ISCC (owner: maintainer —
  installare Inno 6 + `gate --assemble`), Linux (owner: maintainer —
  host/CI + smoke), semantica `bl_produced` (owner: maintainer —
  FAIL hard o warning come Lane A + re-run).
- Duplicazioni: 3 installer morti rimossi (W17); spec win/linux mirror
  con stesso contratto pytetwild; nessun parser/config duplicato nuovo.
- Onestà: checkMesh Failed e R1 NOT RUN riportati con numeri, non
  mediati; `bl_produced` 12/15 FAIL non spacciato per regressione
  (quality gates tutti veri dove la mesh esiste).

## 4. Suite + ruff freschi (oggi, non ieri)

`pytest -m "not slow and not wsl"`: **1719 passed**, 2 skipped,
22 deselected, 1 xfailed (186.96 s). `ruff check src/ tests/`: green.
(F821 sulle .spec: preesistenti, fuori scope gate.)

## 5. Verdetto

```
Milestone: M3+M4 — chiusura formale
Data: 2026-10-23 · HEAD: 30faf8e · Versione: 2.3.0
Esito: PARTIAL
Criteri: preset MET · corpus PARTIAL · docs MET · installer PARTIAL ·
  lane MET · benchmark-finale MET · residual-risks MET · R1 NOT RUN
Regressioni: nessuna (identità Lane A su 3 geometrie, resto stabile/migliore)
Suite: 1719 passed · ruff: green
Debiti accettati: R1-input, ISCC, Linux, bl_produced (azioni sopra)
Azioni: vedi §3, owner/data/criterio per ciascuna
```

PARTIAL, non MET: due criteri parziali + un NOT RUN, tutti con azione
dichiarata. Per regola ("solo MET chiude"): **niente bump, niente tag,
niente release section** in questo commit. BACKLOG_1/2/3: post-M4 per
definizione, non servono. REORG: già eseguito e chiuso (`18e9f28`),
nessun resto (WALL-05 non doveva assorbirlo: lane separata, MET).
