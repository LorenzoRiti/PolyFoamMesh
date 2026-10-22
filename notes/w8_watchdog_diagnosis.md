# W8 — Process robustness: watchdog/heartbeat diagnosis (2026-10-22)

Scope: tet (GMSH/fTetWild), dual, boundary layer, convert/checkMesh.
Sources: `openfoam_runner.py`, `gmsh_subprocess.py`, `tet_poly_dual.py`,
`bl_poly.py`, `gui/task_runner.py`, `tests/test_gmsh_watchdog.py`,
`notes/w3_tet_predictable_reasoning.md`, `notes/w5_bl_diagnosis.md`,
`docs/residual_risks.md`, the real R1 log
(`%TEMP%\opencode\ftetwild_spike\test_173334_e2e2.log`).

## 1. Phase map

### 1.1 Tet — GMSH volume (`GmshVolumeWorker`)

| Item | Current state |
|---|---|
| Progress signal | **none** (`log_line` only; GUI bar is indeterminate) |
| Heartbeat | `_stream_subprocess` synthetic line every 10 s while silent |
| No-output watchdog | yes: `stall_s=300` normal, size-scaled up to 900 s robust/manual |
| Hard timeout | `VOLUME_TIMEOUT_S=3600` |
| Content detector | `GmshSignatureStallDetector` (invalid-element magnitude / monotone split rounds) |
| Action | normal -> robust retry -> manual-equivalent fallback -> honest fail |
| Logs | `watchdog armed`, `watchdog fired`, `... still running`, one line per attempt |
| Orphan safety | `proc_holder` + `_kill_pid_tree` on cancel/stall/timeout |

This is the most mature phase, but its progress is only a log; ETA is absent.

### 1.2 Tet — fTetWild (`FtetWildVolumeWorker`)

| Item | Current state |
|---|---|
| Progress signal | **none** |
| Heartbeat | `_stream_subprocess` default 10 s synthetic line |
| No-output watchdog | **none** (`stall_s` not passed) |
| Hard timeout | `VOLUME_TIMEOUT_S=14400` |
| Content detector | none (`pass N`, `max_energy`, `#v/#t` are not parsed) |
| Action | single attempt, honest failure; GUI retries coarser detail |
| Orphan safety | `proc_holder` + kill tree |

W3 predicted a `pass N`-based ETA and a heartbeat; neither was implemented.
A legitimate 62-minute run is fine, but a silent 4-hour run is indistinguishable
from a hang except for the synthetic "still running" line.

### 1.3 Dual (`TetPolyDualConverter` / `DualPolyWorker`)

| Item | Current state |
|---|---|
| Progress signal | none (stage `_log` lines only) |
| Heartbeat | GUI `QTimer` `_on_polydual_heartbeat` every 8 s + TaskManager 600 s |
| No-output watchdog | **none** |
| Hard timeout | **none** |
| Loop bounds | split rounds, smoothing iterations, boundary-split/clearance/shift rounds (all finite) |
| Cancel | `cancel()` sets a flag; `_check_cancel()` raises `RuntimeError` |
| Orphan safety | in-process, no subprocess |
| Measured long stages | dual fine 294 s (baseline); R1 33.8 s; no infinite loop proven |

The loops are finite, but a single pass (`_shift_boundary_skew`,
`_split_boundary_defects`, `smooth_dual_mesh`, greedy merge) has no progress
callback and no watchdog: a pathological mesh can spend minutes without a
single liveness signal.

### 1.4 Boundary layer (`PolyBoundaryLayerEngine` / `DualPolyWorker`)

| Item | Current state |
|---|---|
| Progress signal | none (percentage lines in `_log`) |
| Heartbeat | same GUI/TaskManager timers as the dual |
| No-output watchdog | **none** |
| Hard timeout | **none** |
| Loop bounds | 5 height scales x 2 cover passes, exclusion rounds per scale (`local_max_rounds`), height-retry rebuilds |
| Cancel | `_cancel()` checked at scale/round and some loops |
| Orphan safety | in-process |

Measured long runs: valve `decoupled` 2,215 s / 15 builds; collapsed valve
892 s / 15 builds; R1 fine BL 57.8 s. The 892 s case is a documented
non-convergence plateau (373-385 pyramids vs 370 input), not a hang, but
there is no liveness/ETA signal between the `[bl]` percentage lines.

### 1.5 Convert / checkMesh

| Phase | Current state |
|---|---|
| `gmshToFoam` (`gmsh_subprocess.run_gmsh_to_foam`) | `_stream_subprocess`, 900 s hard timeout, 30 s heartbeat, no stall detector |
| `polyDualMesh` (`PolyDualWorker`) | `subprocess.run(..., timeout=300)`, **no stream, no heartbeat, no proc_holder**, cancel cannot reach the child |
| checkMesh (`CheckMeshWorker`) | `_stream_subprocess`, 600 s hard timeout, 10 s heartbeat, no stall detector |
| cfMesh (`MeshWorker`) | own loop, 4 h `MAX_RUNTIME_SECONDS`, ETA only from explicit `%` |
| GUI TaskManager | heartbeat timeout 600 s for polydual, 120 s for autopoly, default elsewhere; not phase-aware |

## 2. Duplications

- **Two subprocess execution models**: `_stream_subprocess` (stream + heartbeat
  + stall + kill tree) and `PolyDualWorker.run` (`subprocess.run(timeout=...)`).
  The second one is also the only convert phase without cancel/orphan safety.
- **Nine timeout literals** in the pipeline: 180 (surface), 300 (stall/GMSH +
  polyDualMesh), 600 (checkMesh), 900 (gmshToFoam), 3600 (GMSH volume),
  14400 (fTetWild), 4 h (cfMesh), 600/120 (TaskManager). Several are phase
  ceilings, but they are not expressed by one object.
- **Three ETA/progress styles**: `MeshWorker._emit_eta` (explicit %),
  `_stream_subprocess` heartbeat line, `[bl] precompute x%` log lines.
  GMSH/fTetWild/dual/BL expose no `progress` signal to `TaskManager`.
- **Four recovery styles**: GMSH attempt chain, fTetWild coarser-detail retry
  (GUI), BL scale/exclusion/height retry, quality-fix remesh loop. Each logs
  differently and none records a common action/evidence object.
- **CheckMesh**: `MeshQualityReport` is structured for metrics, but
  "Failed N" is only a regex on `raw_output`; defect types/counts/locations
  and the acceptance reason are not represented. `QualityFixWorker` acts on
  it without a documented acceptance model; the GUI only shows a status.

## 3. Proven stall / non-convergence causes

1. **GMSH MeshAdapt 2D runaway** (proven, GOAL 2026-10-09): invalid elements
   2 -> 6524, max silent gap 251 s under the 300 s watchdog; robust
   Delaunay converges in 6.4 s. The content detector (not a bigger timeout)
   is the fix. Keep it.
2. **GMSH orphan child** (proven, 2026-09): Cancel reset the UI but the child
   kept running; fixed by `proc_holder` + kill tree. Any new subprocess path
   must use the same object, or it reintroduces the bug (`PolyDualWorker`).
3. **BL non-convergence plateau** (measured): valve exclusion cascade
   stalls at 373-385 pyramids vs 370 input across scales; the loop is finite
   but spends 15 builds / 892 s. A watchdog cannot "fix" it; it must surface
   the plateau (no progress) and apply the declared action (stop + report)
   instead of running all scales blindly.
4. **fTetWild silence** (W3): the optimizer can be silent for long stretches;
   a hard 4 h timeout is the only current bound, and it cannot distinguish
   "working" from "dead". `pass N` output is the available evidence.
5. **Dual long passes** (not infinite, measured): smoothing/merge/shift can
   spend tens to hundreds of seconds inside one pass; no progress callback
   means the GUI cannot tell a slow pass from a hang.

No evidence of an infinite `while True` in `tet_poly_dual` or `bl_poly`:
the loops are bounded; the missing piece is observability + a declared
action when the bound is not reached in time.

## 4. Unified design

### 4.1 `PhaseWatchdog` (`core/phase_watchdog.py`)

One object, used by every phase:

- `phase`, `stall_s`, `timeout_s`, `heartbeat_s`, `policy`
  (`abort|retry|fallback|skip`);
- `beat(message="", progress=None)` updates the liveness clock;
- `progress(fraction, stage)` sets deterministic progress + ETA;
- `check()` returns a `WatchdogAction` (reason, evidence, elapsed,
  no-progress, progress, ETA) and fires `on_event` exactly once;
- `guard()` raises `PhaseStalled(action)` in pure-Python loops;
- `on_heartbeat(snapshot)` for GUI/log liveness, `on_progress`/`eta`
  callbacks; thread-safe (stream reader + GUI poller);
- `snapshot()`/`as_dict()` for the run JSON and tests.

The subprocess adapter keeps `_stream_subprocess`'s public signature and
behavior, but its stall/timeout/heartbeat logic moves into `PhaseWatchdog`.
`GmshSignatureStallDetector` remains the content-specific trigger.

### 4.2 Phase wiring

- **GMSH/fTetWild/surface/convert/checkMesh**: `_stream_subprocess` creates
  the watchdog with the phase's budget; workers retain their attempt policy.
- **fTetWild**: add a `FtetWildProgressDetector` (`pass N`, `max_energy`,
  `#v/#t`) feeding `PhaseWatchdog.progress` + `progress` signal; watch
  `stall_s` with the same size-scaled budget as GMSH.
- **Dual**: `TetPolyDualConverter(watchdog=...)`; `_check_cancel` also checks
  the watchdog; progress at read/build/quality/smooth/repair/validate/write;
  `DualPolyWorker` creates the object and emits `progress`/`heartbeat`.
- **BL**: `PolyBoundaryLayerEngine(watchdog=...)`; progress parsed from the
  existing `[bl] ... x%` lines plus stage boundaries; watchdog guard at
  scale/round; `DualPolyWorker` uses the same object around the BL stage.
- **Convert**: replace `PolyDualWorker`'s `subprocess.run(timeout=300)` with
  `_stream_subprocess` (heartbeat, kill tree, `proc_holder`).
- **GUI**: `TaskManager` consumes the worker `progress`/`heartbeat` signals;
  `WatchdogAction` is logged as one `[watchdog] <phase>: ...` line and, where
  the phase policy allows, mapped to the existing retry/fallback.

### 4.3 `CheckMeshReport`

New `core/checkmesh_report.py`:

- `CheckMeshDefect(kind, count, locations, evidence)`;
- `CheckMeshReport(metrics, defects, failed_checks, mesh_ok, fatal,
  acceptance, acceptance_reason, raw_output)`;
- parser for OpenFOAM v2512 + legacy phrasing; known defect classes
  (face pyramids, wrong orientation, negative volume, non-ortho > 70,
  skew > 4, aspect > 1000, multiply connected, boundary openness,
  zero/negative face areas, unused points);
- `accept(reason, policy)` only explicit: `passed` is true for `Mesh OK` or
  an explicitly accepted report; `raw_failed` remains true;
- `to_dict()`; `MeshQualityReport` carries it (`structured`) and its
  `to_dict()` includes it, so GUI/JSON/report consume one model;
- `QualityFixWorker`/`main_window` may accept a failure only by calling
  `accept()` with counts/types/cause; a bare `Failed` stays failed.

### 4.4 Internal-pyramid repair

`core/internal_pyramid_repair.py`, opt-in:

- load polyMesh; find internal faces failing `_pyramid_violations`;
- batch move the offending face vertices by a volume-guarded fraction of the
  measured deficit along the face normal (owner/neighbour side chosen from
  the failing margin);
- keep-best: accept a round only if total pyramids strictly decrease, no
  non-positive volume appears, and max non-ortho/skew do not worsen;
- stop after N rounds/time; never write a rejected candidate;
- A/B on R1 (`case_e2e_173334b`, 253,681 cells, 33 internal pyramids
  in-process / 39 checkMesh) and a synthetic one-face case; real checkMesh
  after the accepted move.

## 5. Reuse / remove / estimate

**Reuse**: `_stream_subprocess` (public contract), `GmshSignatureStallDetector`,
the existing attempt chains and `TaskManager`; `MeshQualityReport` remains the
compatibility surface.

**Remove/replace**:
- `PolyDualWorker`'s private `subprocess.run(timeout=300)` path;
- the duplicated stall/timeout loop inside `_stream_subprocess` (moved into
  `PhaseWatchdog`);
- the unwired fTetWild "no progress" path;
- any new arbitrary timeout is forbidden; each phase gets one declared budget
  and one action.

**Estimate**: 11-15 files, ~700-1,100 net lines (watchdog ~250, checkMesh
report ~250, repair ~200, phase wiring ~200, tests ~400, docs ~150).
Risks: preserving `test_gmsh_watchdog.py` behavior; not changing default mesh
output; keeping the repair keep-best; real R1 copy is in `%TEMP%` and may be
ephemeral (rebuild procedure in `REGRESSION_CASES.md`).
