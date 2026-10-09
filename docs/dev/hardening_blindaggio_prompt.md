# Agent prompt — hardening concurrency and logging (anti-freeze)

Use this file as a task prompt for a session dedicated to the
**hardening** of the GUI code: closing the residual debt after
`TaskManager` and the log panel's burst protection.

Related documents:

- `docs/concurrency.md` — canonical (authoritative) pattern
- `.slim/deepwork/hardening-anti-freeze.md` — deepwork audit and phases
- `.slim/deepwork/freeze-meshing-valve.md` — white-screen regression on verbose runs
- `docs/found_while_hardening.md` — bugs found during the pass (report only, no fix here)

---

## Context

**Repo:** `C:\Users\Davide Valoroso\cfmesh-autogui`  
**Stack:** Python 3.11, PySide6, WSL2 + OpenFOAM 2512  
**Starting branch:** `master` (commit `87e744f` or later)

**Problem already seen in real sessions:** UI freeze, white screen, Windows
"Not Responding" on verbose runs (cartesianMesh, flood of GMSH warnings).

**Target architecture already introduced:**

| Module | Role |
|--------|------|
| `src/cfmesh_autogui/gui/task_runner.py` | `TaskManager` — sole owner of QThread/worker |
| `src/cfmesh_autogui/gui/log_panel.py` | burst cap + `setMaximumBlockCount` |
| `src/cfmesh_autogui/gui/main_window.py` | `_submit_task()` as the entry point for long work |

**Recent relevant commit:** `87e744f` — re-entry guards moved to
`_tasks.is_running(...)`, some `wait()` on the GUI thread removed. Technical debt
remains (see below).

---

## Goal

Harden the code so that:

1. No module touches `QThread` directly except `task_runner.py`
2. No `thread.wait()` / `thread.quit()` on the GUI thread
3. No double task start — centralized re-entry guard
4. Log flood cannot saturate the GUI thread
5. Shutdown/cancel returns the UI to usable in **< 2 s**
6. Regression tests cover the real freeze cases

---

## Work to do (in order)

### 1. Eliminate `_cleanup_thread` and the residual `_*_thread` attrs

**File:** `src/cfmesh_autogui/gui/main_window.py`

- Find all calls to `_cleanup_thread(...)` and replace them with:
  - `self._tasks.cancel("<task_name>")` if the task might be active
  - or just resetting the attr `_*_worker = None` if the task is already finished
- Remove `_cleanup_thread` if it is no longer needed
- Remove every `_*_thread` attr that is no longer used
- Verify that `_submit_task()` remains the only way to start long work

**Acceptance:**

```powershell
rg "_cleanup_thread|_gmsh_thread|_watertight_thread|_samr_thread|thread\.wait|thread\.quit" src/cfmesh_autogui/gui/main_window.py
```

→ zero matches on manual lifecycle (except comments/docs)

---

### 2. Centralize the re-entry guards

**File:** `src/cfmesh_autogui/gui/main_window.py`

Every user action that starts work must check
`self._tasks.is_running(name)` with the **canonical task name**, not labels like
`_gmsh_worker`.

**Standard task names:**

| Task name | Description |
|-----------|-------------|
| `gmsh_volume` | GMSH volume mesh |
| `gmsh_convert` | gmshToFoam |
| `feature_detect` | gap/feature detection |
| `checkmesh` | checkMesh |
| `wsl_check` | WSL/OpenFOAM validation |
| `parallel_mesh` | parallel meshing |
| `polydual` | poly dual conversion |
| `autopoly` | native autopoly |
| `samr` | solution-adaptive refinement |
| `quality_fix` | quality fix cycle |
| `watertight` | watertight check |
| `export` | case export |
| `decompose` | decompose |

Log/UI messages → user-friendly names; internal guard → always `task_name`.

**Acceptance:**

- No `getattr(self, "_*_thread")` left
- No `thread.isRunning()` left in `main_window.py`

---

### 3. TaskManager — close the lifecycle holes

**File:** `src/cfmesh_autogui/gui/task_runner.py`

- Verify that `_force_cleanup` and `shutdown` do not leave tasks in `_tasks`
  with `state == "running"`
- After `_force_cleanup`, ensure `_release` is always called
  (no `_retired` leak)
- Simplify `_finalize`: remove the dead `finished` vs `else` branch
- Explicitly document that `thread.wait()` in `shutdown` is a last resort
  and must not be replicated elsewhere

**Acceptance (test):**

- submit → cancel → `mgr.running == []` within 2 s
- submit → shutdown → no thread `isRunning()` after budget
- double submit with the same name → second rejected

---

### 4. LogPanel — burst hardening + thread safety

**File:** `src/cfmesh_autogui/gui/log_panel.py`

Keep:

- cross-thread append via `QMetaObject.invokeMethod(..., QueuedConnection)`
- `_BURST_PENDING_CAP` drop with a `_dropped` counter
- `document().setMaximumBlockCount(_MAX_LOG_LINES)`

Verify:

- `clear_log()` resets `_pending` and `_dropped`
- optional: HTML escaping on raw text before the `[ERROR]`,
  `[WARN]`, etc. highlights
- do **not** reintroduce the O(n) prune with cursor walk

**Acceptance:**

- `tests/test_log_flood.py` passes
- flood of 50k lines from a worker thread: `_pending <= cap`, "suppressed"
  note visible, UI pumpable

---

### 5. Subprocess live throttle (second line of defense)

**File:** `src/cfmesh_autogui/core/openfoam_runner.py` (`_stream_subprocess`)

Confirm that:

- all lines end up in `stdout_lines` / `stderr_lines` for final
  analysis
- the live `on_line` is throttled (~50 lines/s) with a suppression note
- every worker that streams logs uses this path, not
  `Popen` + direct unbounded emit

**Acceptance:**

- `test_stream_subprocess_throttles_live_lines_but_captures_all` passes
- no worker does a loop `for line in proc.stdout: emit(line)` without throttle

---

### 6. Anti-pattern audit across the codebase

```powershell
rg "QThread\(|\.wait\(|processEvents\(|time\.sleep\(" src/
rg "thread\.start\(|moveToThread\(" src/ --glob '!**/task_runner.py'
rg "threading\.Thread\(" src/
```

For every hit:

- long work → migrate to `TaskManager.submit`
- test/utility → ok, but document it
- on the GUI thread → move or eliminate

Consult the audit table in `.slim/deepwork/hardening-anti-freeze.md`
(Phase 1 section) for priorities.

---

### 7. Regression tests to add/strengthen

**File:** `tests/test_task_runner.py`, `tests/test_log_flood.py`

Add if missing:

- cancel during subprocess (`kill_hook` invoked)
- task with `finished` taking 2 args (duck-typed worker)
- re-entry guard on real names (`gmsh_volume`, `checkmesh`)
- shutdown with 2+ concurrent tasks
- log flood + active task together (GUI stays responsive)

**Test constraints:**

- `QT_QPA_PLATFORM=offscreen`
- no long sleeps; use timeouts and predicate loops
- do not test `terminate()` as the main path on Windows (GIL race),
  only cooperative cancel
- do **not** run heavy WSL/openfoam_runner tests locally if you know they time out
  (see notes in `hardening-anti-freeze.md`)

---

## Non-negotiable rules

- **DO NOT** introduce a second concurrency pattern
- **DO NOT** do `wait()` on the GUI thread in new code
- **DO NOT** connect plain Python callables with `QueuedConnection` to Qt worker
  signals
- **DO NOT** increase scope beyond hardening (no unrelated aesthetic refactors)
- Minimal diff; comments only where they explain real observed failure modes
- Unfixed bugs → `docs/found_while_hardening.md`, not mixed commits

---

## Definition of Done

- [ ] Zero manual QThread lifecycle outside `task_runner.py`
- [ ] All long-running jobs go through `_submit_task` / `TaskManager.submit`
- [ ] Re-entry guards uniform on canonical task names
- [ ] Log flood bounded + subprocess throttled
- [ ] Green tests:

  ```powershell
  python -m pytest tests/test_task_runner.py tests/test_log_flood.py -q
  ```

- [ ] No file in `.opencode/` committed
- [ ] `docs/concurrency.md` updated if the public contract changes

---

## Expected agent output

1. Focused diff with a short explanation for each change
2. List of anti-freeze patterns removed (file + line)
3. pytest result
4. Residual debts explicitly accepted (e.g. `wait()` only in shutdown
   last-resort)

---

## Useful commands

```powershell
# Repo state
git status
git diff

# Quick audit
rg "_cleanup_thread|_thread|isRunning|processEvents" src/cfmesh_autogui/gui/

# Hardening test subset
python -m pytest tests/test_task_runner.py tests/test_log_flood.py -q
```

---

## Notes for the orchestrator

If other agents are working in parallel on `main_window.py` or
`openfoam_runner.py`, re-read the file before editing and keep the
changes **additive**. Do not reformat shared files.
