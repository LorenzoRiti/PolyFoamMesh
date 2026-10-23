# W17 — Installer + release: diagnosi sistemica (zero-cost + 3 fatti macchina)

Date: 2026-10-23. Metodo: letti GOAL/ROADMAP/prompts, entrambe le spec,
4 script installer, app.py frozen-dispatch, pyproject, .gitignore, storia
binari; verificati: `PyInstaller 6.21.0` presente, `ISCC.exe` ASSENTE
(entrambi i path default), WSL2-Ubuntu presente ma Stopped, `pytetwild
0.4.2` presente nel dev env. Nessuna build eseguita in questa fase.

Operative prompt: `.opencode/prompts/M3_W17_installer_release.md` ==
prompt utente (identici). Nessun conflitto; si procede.

## 1. Stato distro (cosa produce oggi la build)

- **Spec**: `PolyFoamMesh.spec` (win, 118 righe) + `PolyFoamMesh-linux.spec`
  (100 righe), one-dir, entry `src/polyfoammesh/app.py` (= pyproject
  `[project.scripts]`). Ultima build reale 2026-10-12: `dist/PolyFoamMesh/`
  + `installer/output/PolyFoamMesh-2.3.0-Setup.exe` presenti su disco,
  untracked+gitignored (corretto). Build 12/10 = tree pre-W11..W17:
  da ricostruire, non da spedire.
- **Script canonico**: `installer/inno_setup.iss` (2.3.0, per-user
  `%LocalAppData%`, no admin, path relativi `..\dist\PolyFoamMesh`,
  lzma2, assoc .step/.stp/.stl, uninstall pulito). Coperto da
  `tools/check_version_sync.py` (3 fonti: pyproject/_version.py/.iss).
- **Dipendenza WSL/OF**: non spedita. `installer/setup_wsl_openfoam.ps1`
  (admin, WSL2+Ubuntu+OF v2512 con cfMesh bundled). App = "demo no-WSL":
  CFD Poly (GMSH) + FEM Tetra standalone; cfMesh/checkMesh solo con WSL2.
- **Frozen-readiness in src**: `app.py:main` fa dispatch di
  `--feature-detect/--watertight/--gmsh-*/--ftetwild-volume` (re-entry
  dei worker nel frozen exe), `core/subprocess_quiet.py` centrale.
  MA `app.py:183`: il dispatch gmsh frozen "not yet hit in practice
  since this app hasn't shipped as a frozen build" — rami frozen MAI
  eseguiti live. Nessun test li copre (`sys.frozen` mai settato in suite).

## 2. Difetti della spec / tooling

- **D1 — 3 script installer morti e tracked**: `installer/installer.iss`
  (2.0.1, admin `{autopf}`, OutputDir `..\build`, AppId placeholder),
  `installer/installer.nsi` (2.0.1, admin), `installer/nsis_installer.nsi`
  (2.0.0, admin, spedisce `..\src\polyfoammesh\*.py` GREZZI — modello
  sbagliato, non frozen). Zero riferimenti in repo (verificato su
  README/docs/CHANGELOG/pre-commit/pyproject). Da `git rm`.
- **D2 — version drift**: i 3 morti portano 2.0.1/2.0.0. Spariscono con D1.
- **D3 — `check_version_sync.py` non cablato**: esiste, funziona, ma
  nessun hook/CI/test lo esegue. Gate fittizio per assenza.
- **D4 — comando build PyInstaller non documentato**: la .spec non dice
  come invocarla; la .iss documenta solo ISCC. Il gate deve fissarlo
  (`pyinstaller PolyFoamMesh.spec` da root, env con extra ftetwild).
- **D5 — `pytetwild` (extra opzionale `ftetwild`) manca dalla spec**:
  installato qui (0.4.2) ma non in `hiddenimports` né `collect_all`;
  il frozen lo perde e il path fTetWild muore con ModuleNotFound invece
  dell'hint esplicito previsto (pyproject:64-66). Difetto reale.
  Fix onesto: collect guarded (presente→includi, assente→warning come
  il ramo gmsh della linux spec) + release build con `.[ftetwild]`.
- **D6 — `excludes` tarati sul bloat di QUESTA macchina** (torch/cv2/...):
  innocui su macchina pulita (no-op), ma documentano non-riproducibilità.
  Rischio inverso già morso 2 volte (casadi/nlopt erano REALI): serve il
  test che nessun `excludes` amputi un import diretto di `src/` (+
  transitivi noti: casadi via cadquery, nlopt via sketch_solver).
- **D7 — dist/ e output/ stantii** (12/10, pre-W11): non spedibili.
  Ricostruzione = build PyInstaller (~400-700MB, minuti) + ISCC (ASSENTE
  qui) → passo manuale documentato, non gate automatico.
- **D8 — R1-173334 verità persa**: `C:\cfmesh_cases\gmsh_direct_20261020_173334/`
  NON esiste (solo run 10-22/23); `mesh.msh` 22.6MB + log TEMP
  effimeri. L'E2E full-BL da REGRESSION_CASES non è rieseguibile
  as-recorded. Ricostruzione pesante (run fTetWild eps 0.3). LIMITE da
  dichiarare; E2E del gate su STL tracked (mini_valve/venturi/dirty_cad).

## 3. Collaudo attuale (automatizzato vs manuale)

| Cosa | Stato |
|---|---|
| Test GUI offscreen (QApplication) | automatizzati, widget-level |
| Frozen-exe dispatch branches | MAI eseguiti (no test, no build shippata) |
| Contenuto dist/ (bundle completo) | nessun test |
| Version sync 3 fonti | script esiste, mai eseguito da gate |
| Smoke installer (installa→avvia→disinstalla) | manuale, una volta (12/10), non registrato |
| E2E tet+dual+BL+convert+checkMesh | manuale via lane; checkMesh reale richiede WSL+OF (Ubuntu qui Stopped, OF da verificare) |
| R1-173334 E2E (caso obbligatorio) | input verità perso → non rieseguibile; debito |
| Tempi registrati | solo nei log di lane, nessun artefatto standard |

## 4. Design (blocchi; dettagli in reasoning)

- **B1 spec corretta**: fix D5 (pytetwild guarded-collect) + comando build
  documentato in-spec; `git rm` 3 script morti (D1/D2); test statici di
  spec (entry/rthook/datas/excludes-vs-imports/no-absolute-paths).
- **B2 gate eseguibile** `tools/release_gate.py`: stage
  version-sync → spec-static → smoke (import+offscreen entry, frozen-simulated
  via env) → E2E flusso su STL tracked con tempi/numeri → checkMesh se
  WSL+OF presenti. Ogni stage pass/fail; limiti stampati (no clean-machine,
  no ISCC, no R1-verità, no full-bundle-build di default).
- **B3 E2E reale**: nel gate come stage (marcato slow/wsl dove serve),
  numeri vs baseline Lane A (mini_valve PASS 8070/64.33) con tolleranze;
  niente passi manuali.
- **B4 pulizia**: rm morti; .gitignore già ok (output/, dist/, build/,
  tools/innosuite/); `valve_dual.npz` 27MB RESTA (fixture referenziata
  con skip-guard — rimuoverla romperebbe 6 test; decisione documentata);
  max altro blob 294KB sorgente. Test "no binari tracked" via allowlist
  estensioni + soglia taglia.
- **B5 docs**: INSTALL.md (comando build + ISCC + limiti), CHANGELOG,
  GOAL; diagnosi+reasoning.

## 5. Stima carico e rischi

- Medio-piccolo: 1 gate script (~250 righe), 1 test file (~10 test),
  1 fix spec (pytetwild guarded), 3 `git rm`, 2 note, doc touch-up.
- Rischio 1: E2E con checkMesh richiede OF in WSL — se assente, stage
  dichiarato-skipped (onesto, non fittizio). Rischio 2: build PyInstaller
  completa troppo pesante per gate per-commit → flag `--build`, default
  static+E2E-source. Rischio 3: modificare le spec rompe la build 12/10
  funzionante → il gate statico + build reale di verifica la ricontrolla.
