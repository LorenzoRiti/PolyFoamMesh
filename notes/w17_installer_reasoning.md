# W17 — reasoning e previsioni

Date: 2026-10-23. Predecessore: `notes/w17_installer_diagnosis.md`.
Previsioni scritte PRIMA di edit/build/test.

## B1 — spec corretta (fix D5 + rm morti + test statici)

Predizione: aggiungere il guarded-collect di `pytetwild` alla win spec
(pattern identico al ramo gmsh-warn della linux spec: presente→includi,
assente→warning) non cambia il bundle di questa macchina (pytetwild 0.4.2
installato → incluso come prima via analisi, ora esplicito) e rende la
build senza extra un warning invece di un ModuleNotFound a runtime.
I 3 `git rm` non rompono nulla (zero riferimenti verificato; il check
`installer-allowlist` lo rende regression-proof). Il comando build va
come commento in testa alla spec + nel gate, non in una nuova doc.
Falsificazione: un riferimento dimenticato ai morti (il test allowlist
lo trova) o PyInstaller che rifiuta il guarded-collect (fallback:
hiddenimport condizionale via `find_spec`).

## B2 — release gate eseguibile

Predizione: `tools/release_gate.py` con stage
`versions → spec-static → smoke → e2e-flow → checkmesh?` passa su questa
macchina a meno dello stage checkMesh (OF in WSL da verificare: se
assente, `SKIP(dichiarato)` — è il comportamento progettato, non un
fallimento). Ogni stage stampa `PASS/FAIL/SKIP(perché)` + tempi; exit
code = fail se un solo FAIL. Nessun flag fittizio: `--build` (bundle
reale) e `--assemble` (ISCC) default OFF con motivo stampato (peso/
ISCC assente). Falsificazione: stage che passa a vuoto (assert su
artefatti reali: exe/simbolo in dist, numeri mesh misurati, rc checkMesh).

## B3 — E2E reale

Predizione: lo stage e2e usa `benchmarks/geometries/mini_valve.stl`
(tracked, piccolo) via GMSH→dual→checkMesh se OF c'è; baseline attesa
Lane A poly PASS (8070 celle, NOmax 64.33, BL-failed-warning noto) con
tolleranze larghe su tempi, strette su conteggi/flag. Se OF manca: E2E
si ferma al convert+dual con SKIP(checkMesh) dichiarato. R1-173334 resta
fuori dal gate automatico (verità persa — dichiarato in gate + GOAL).
Falsificazione: numeri fuori tolleranza → o regressione vera (blocca) o
baseline da ri-misurare (si aggiorna con data, mai a memoria).

## B4 — pulizia repo

Predizione: dopo `git rm`, `git ls-files` non contiene più
`.iss/.nsi` morti né binari oltre allowlist (`.ico`, `*.stl<1MB`,
`*.png` assets, `valve_dual.npz` fixture con skip-guard). Il test
`test_no_tracked_binaries` (estensioni vietate + soglia 1MB con
allowlist nominativa) rende la regola eseguibile. `valve_dual.npz`
resta per decisione esplicita (6 test lo usano, rimuoverlo = danno).
Falsificazione: blob dimenticato >1MB non in allowlist → o allowlist
con motivo o rimozione.

## B5 — gate finale

Suite fast completa + ruff verdi; commit locale unico con i file W17.
Il pre-commit hook rieseguirà suite+ruff (previsti ~200 s).
`benchmarks/run_comparison.py` + `tests/test_run_comparison.py` (modifiche
utente) e `PolyFoamMesh_Technical_Report.pdf` mai staged — come sempre.
