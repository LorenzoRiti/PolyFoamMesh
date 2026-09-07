# Prompt agente — blindare concorrenza e log (anti-freeze)

Usa questo file come task prompt per una sessione agente dedicata al
**blindaggio** del codice GUI: chiudere il debito residuo dopo
`TaskManager` e la burst protection del log panel.

Documenti correlati:

- `docs/concurrency.md` — pattern canonico (autoritativo)
- `.slim/deepwork/hardening-anti-freeze.md` — audit e fasi del deepwork
- `.slim/deepwork/freeze-meshing-valve.md` — regressione white-screen su run verbosi
- `docs/found_while_hardening.md` — bug trovati durante il pass (solo report, no fix qui)

---

## Contesto

**Repo:** `C:\Users\Davide Valoroso\cfmesh-autogui`  
**Stack:** Python 3.11, PySide6, WSL2 + OpenFOAM 2512  
**Branch di partenza:** `master` (commit `87e744f` o successivo)

**Problema già visto in sessioni reali:** freeze UI, white screen, Windows
"Not Responding" su run verbosi (cartesianMesh, flood di warning GMSH).

**Architettura target già introdotta:**

| Modulo | Ruolo |
|--------|-------|
| `src/cfmesh_autogui/gui/task_runner.py` | `TaskManager` — unico owner di QThread/worker |
| `src/cfmesh_autogui/gui/log_panel.py` | burst cap + `setMaximumBlockCount` |
| `src/cfmesh_autogui/gui/main_window.py` | `_submit_task()` come entry point per lavoro lungo |

**Commit recente rilevante:** `87e744f` — re-entry guard spostati su
`_tasks.is_running(...)`, rimossi alcuni `wait()` sulla GUI thread. Resta
debito tecnico (vedi sotto).

---

## Obiettivo

Blindare il codice così che:

1. Nessun modulo tocchi `QThread` direttamente tranne `task_runner.py`
2. Nessun `thread.wait()` / `thread.quit()` sulla GUI thread
3. Nessun doppio avvio task — re-entry guard centralizzato
4. Log flood non possa saturare la GUI thread
5. Shutdown/cancel torni UI utilizzabile in **< 2 s**
6. Test di regressione coprano i casi reali di freeze

---

## Lavoro da fare (in ordine)

### 1. Eliminare `_cleanup_thread` e gli attr `_*_thread` residui

**File:** `src/cfmesh_autogui/gui/main_window.py`

- Cercare tutte le chiamate a `_cleanup_thread(...)` e sostituirle con:
  - `self._tasks.cancel("<task_name>")` se il task potrebbe essere attivo
  - oppure solo reset dell'attr `_*_worker = None` se il task è già finito
- Rimuovere `_cleanup_thread` se non serve più
- Rimuovere ogni attr `_*_thread` non più usato
- Verificare che `_submit_task()` resti l'unico modo per avviare lavoro lungo

**Acceptance:**

```powershell
rg "_cleanup_thread|_gmsh_thread|_watertight_thread|_samr_thread|thread\.wait|thread\.quit" src/cfmesh_autogui/gui/main_window.py
```

→ zero match su lifecycle manuale (eccetto commenti/doc)

---

### 2. Centralizzare i re-entry guard

**File:** `src/cfmesh_autogui/gui/main_window.py`

Ogni azione utente che avvia lavoro deve controllare
`self._tasks.is_running(name)` con il **task name canonico**, non label tipo
`_gmsh_worker`.

**Task names standard:**

| Task name | Descrizione |
|-----------|-------------|
| `gmsh_volume` | volume mesh GMSH |
| `gmsh_convert` | gmshToFoam |
| `feature_detect` | gap/feature detection |
| `checkmesh` | checkMesh |
| `wsl_check` | validazione WSL/OpenFOAM |
| `parallel_mesh` | meshing parallelo |
| `polydual` | conversione poly dual |
| `autopoly` | autopoly nativo |
| `samr` | solution-adaptive refinement |
| `quality_fix` | ciclo quality fix |
| `watertight` | check watertight |
| `export` | export case |
| `decompose` | decompose |

Messaggi log/UI → nomi user-friendly; guard interno → sempre `task_name`.

**Acceptance:**

- Nessun `getattr(self, "_*_thread")` rimasto
- Nessun `thread.isRunning()` rimasto in `main_window.py`

---

### 3. TaskManager — chiudere i buchi di lifecycle

**File:** `src/cfmesh_autogui/gui/task_runner.py`

- Verificare che `_force_cleanup` e `shutdown` non lascino task in `_tasks`
  con `state == "running"`
- Dopo `_force_cleanup`, assicurarsi che `_release` venga sempre chiamato
  (no leak di `_retired`)
- Semplificare `_finalize`: rimuovere ramo morto `finished` vs `else`
- Documentare esplicitamente che `thread.wait()` in `shutdown` è last-resort
  e non va replicato altrove

**Acceptance (test):**

- submit → cancel → `mgr.running == []` entro 2 s
- submit → shutdown → nessun thread `isRunning()` dopo budget
- doppio submit stesso name → secondo rifiutato

---

### 4. LogPanel — hardening burst + thread safety

**File:** `src/cfmesh_autogui/gui/log_panel.py`

Mantenere:

- cross-thread append via `QMetaObject.invokeMethod(..., QueuedConnection)`
- `_BURST_PENDING_CAP` drop con contatore `_dropped`
- `document().setMaximumBlockCount(_MAX_LOG_LINES)`

Verificare:

- `clear_log()` azzera `_pending` e `_dropped`
- opzionale: escape HTML su testo grezzo prima degli highlight `[ERROR]`,
  `[WARN]`, ecc.
- **non** reintrodurre prune O(n) con cursor walk

**Acceptance:**

- `tests/test_log_flood.py` passa
- flood 50k linee da worker thread: `_pending <= cap`, nota "soppresse"
  visibile, UI pumpabile

---

### 5. Subprocess live throttle (seconda linea di difesa)

**File:** `src/cfmesh_autogui/core/openfoam_runner.py` (`_stream_subprocess`)

Confermare che:

- tutte le linee finiscono in `stdout_lines` / `stderr_lines` per analisi
  finale
- `on_line` live è throttled (~50 linee/s) con nota soppressione
- ogni worker che streamma log usa questo path, non
  `Popen` + emit diretto unbounded

**Acceptance:**

- `test_stream_subprocess_throttles_live_lines_but_captures_all` passa
- nessun worker fa loop `for line in proc.stdout: emit(line)` senza throttle

---

### 6. Audit anti-pattern in tutta la codebase

```powershell
rg "QThread\(|\.wait\(|processEvents\(|time\.sleep\(" src/
rg "thread\.start\(|moveToThread\(" src/ --glob '!**/task_runner.py'
rg "threading\.Thread\(" src/
```

Per ogni hit:

- lavoro lungo → migrare a `TaskManager.submit`
- test/utility → ok, ma documentare
- sulla GUI thread → spostare o eliminare

Consultare la tabella audit in `.slim/deepwork/hardening-anti-freeze.md`
(sezione Phase 1) per priorità.

---

### 7. Test di regressione da aggiungere/rafforzare

**File:** `tests/test_task_runner.py`, `tests/test_log_flood.py`

Aggiungere se mancanti:

- cancel durante subprocess (`kill_hook` invocato)
- task con `finished` a 2 arg (duck-typed worker)
- re-entry guard su nomi reali (`gmsh_volume`, `checkmesh`)
- shutdown con 2+ task concorrenti
- log flood + task attivo insieme (GUI resta responsiva)

**Vincoli test:**

- `QT_QPA_PLATFORM=offscreen`
- niente sleep lunghi; usare timeout e predicate loop
- non testare `terminate()` come path principale su Windows (GIL race),
  solo cooperative cancel
- **non** eseguire test WSL/openfoam_runner pesanti in locale se noti timeout
  (vedi note in `hardening-anti-freeze.md`)

---

## Regole non negoziabili

- **NON** introdurre un secondo pattern di concorrenza
- **NON** fare `wait()` sulla GUI thread in codice nuovo
- **NON** connettere plain Python callables con `QueuedConnection` ai signal
  Qt worker
- **NON** aumentare scope oltre hardening (no refactor estetici non correlati)
- Diff minimo; commenti solo dove spiegano failure mode reali osservati
- Bug non fixati → `docs/found_while_hardening.md`, non commit misti

---

## Definition of Done

- [ ] Zero lifecycle QThread manuale fuori da `task_runner.py`
- [ ] Tutti i long-running job passano da `_submit_task` / `TaskManager.submit`
- [ ] Re-entry guard uniformi su task name canonici
- [ ] Log flood bounded + subprocess throttled
- [ ] Test verdi:

  ```powershell
  python -m pytest tests/test_task_runner.py tests/test_log_flood.py -q
  ```

- [ ] Nessun file in `.opencode/` committato
- [ ] `docs/concurrency.md` aggiornato se cambia il contratto pubblico

---

## Output atteso dall'agente

1. Diff focalizzato con spiegazione breve per ogni cambio
2. Elenco dei pattern anti-freeze rimossi (file + linea)
3. Risultato pytest
4. Debiti residui accettati esplicitamente (es. `wait()` solo in shutdown
   last-resort)

---

## Comandi utili

```powershell
# Stato repo
git status
git diff

# Audit rapido
rg "_cleanup_thread|_thread|isRunning|processEvents" src/cfmesh_autogui/gui/

# Test subset hardening
python -m pytest tests/test_task_runner.py tests/test_log_flood.py -q
```

---

## Note per l'orchestratore

Se altri agenti lavorano in parallelo su `main_window.py` o
`openfoam_runner.py`, rileggere il file prima di editare e mantenere le
modifiche **additive**. Non riformattare file condivisi.
