# Istruzioni permanenti — Davide Valoroso / PolyFoamMesh

Queste regole vincolano ogni sessione di lavoro su questo repo e hanno
precedenza su skill, abitudini dell'agente e suggerimenti di tooling.

## 1. NIENTE SUBAGENTI. Mai.

- NON usare mai il tool `subagent`, in nessuna forma e per nessun motivo:
  niente explorer/fixer/oracle/designer delegati, niente sessioni figlie,
  niente dispatch paralleli a subagenti.
- Eccezione una tantum già consumata (ottobre 2026): da ora in poi il
  divieto è assoluto, salvo esplicita richiesta contraria dell'utente.

## 2. Solo background job propri, controllati dall'agente

- Il lavoro lungo (suite, benchmark, build, E2E, run di misura) va fatto
  con comandi `shell` in background lanciati e monitorati
  dall'agente stesso nella sessione principale.
- Controllare al massimo l'avanzamento necessario (niente polling
  continuo); non aprire nuovi lavori dipendenti prima che i prerequisiti
  siano chiusi.
- Mai più di quanto la macchina regge: pesare run WSL/OpenFOAM,
  PyInstaller e suite completa prima di sovrapporli.

## 3. Il lavoro lo fa l'agente, in sessione

- Nessuna delega del lavoro reale: lettura codice, diagnosi, edit,
  test, verifica e commit avvengono nel contesto principale.
- Prima di ogni commit: gate locale (`pytest -m "not slow and not wsl"`
  + `ruff check src/ tests/`) e previsione scritta dove previsto.
- Commit locali; push solo su ordine esplicito.
- MAI stage/commit di `benchmarks/run_comparison.py`,
  `tests/test_run_comparison.py` (modifiche utente pre-esistenti);
  `PolyFoamMesh_Technical_Report.pdf` resta untracked, mai committato.
