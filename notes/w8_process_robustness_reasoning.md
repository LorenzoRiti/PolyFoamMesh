# W8 — Process robustness: predictions before the implementation

Date: 2026-10-22. Diagnosis: `notes/w8_watchdog_diagnosis.md`.
No default mesh setting is changed by W8.

## Claims

1. One `PhaseWatchdog` can replace the duplicated stall/timeout/heartbeat
   logic without changing the documented GMSH behavior (11 existing
   watchdog tests, including real subprocess kills and one-shot robust
   retries).
2. Dual/BL become observable and bounded: a synthetic loop that stops
   calling `beat()` fires exactly one action, raises `PhaseStalled` from
   `guard()`, and never leaves a process behind (in-process: no process;
   subprocess: existing kill-tree test).
3. fTetWild exposes real progress/ETA from `pass N` / `max_energy`, so a
   62-minute run is visibly alive and a silent run fires the same watchdog
   policy instead of waiting for the 4 h hard ceiling.
4. `CheckMeshReport` makes every `Failed` explainable: R1 must yield
   `failed_checks=3`, defects `face_pyramids x39`, `skewness x5`,
   `non_ortho` max 128.044, and `accepted=False`; a healthy v2512 output
   must yield `Mesh OK`, zero defects, `accepted/passed=True`.
5. The internal-pyramid repair may or may not land. The first measured
   candidate (batch move of all bad-face vertices) is already known from the
   probe to make things worse (33 -> 41 defects, negative volumes), so it is
   rejected. The sequential, volume-guarded keep-best variant is the only
   candidate; it lands only if R1 drops below 33 internal pyramids with no
   negative volume and no worse non-ortho/skew. Otherwise the final answer is
   a measured declaration, not a forced repair.

## Evidence path

- Unit: fake monotonic clock; deterministic, no sleeps.
  - stall fires only after `stall_s` without `beat`;
  - `progress()` resets liveness and gives ETA/monotonic fraction;
  - heartbeat does NOT reset stall;
  - timeout is independent of stall and yields `timed_out`;
  - `on_event` once; `guard()` raises with the action;
  - thread-safety: concurrent `beat`/`check` under a lock (smoke).
- Integration (no WSL):
  - `_stream_subprocess` existing real-child tests (stall, healthy stream,
    signature) must stay green;
  - fake-clock `TetPolyDualConverter`/`PolyBoundaryLayerEngine` loops;
  - `PolyDualWorker` uses `_stream_subprocess` (mock `Popen`/stream) and
    kill-tree on cancel;
  - fTetWild progress detector on synthetic lines; paced output never fires
    the stall.
- CheckMesh: pure parser tests with the real R1 tail and the healthy
  `REAL_V2512` fixture; `MeshQualityReport.to_dict()` includes the structured
  model; GUI payload test (offscreen) reads `structured`.
- Repair: synthetic two-cell/one-internal-face fixture + R1 copy in `%TEMP%`;
  in-process pyramid count, exact volume guard, real checkMesh only on the
  accepted candidate (WSL available; otherwise declared).
- Full fast suite + ruff.

## Predictions

1. `PhaseWatchdog` with `stall_s=1.0`, fake clock: `beat()` at 0.5 s does
   not fire; at 1.01 s after the last beat `check()` returns `reason="stall"`;
   a second `check()` returns the same action without calling `on_event`
   twice. `guard()` raises `PhaseStalled`.
2. `progress(0.25, "x")` -> `eta = elapsed * 3`; `progress(0.5)` halfs it;
   a non-finite/negative fraction is clamped to `[0,1]` with a warning.
3. Heartbeat: 0.3 s interval, fake clock; `check()` at 0.31 s emits exactly
   one heartbeat, and the stall timer remains anchored to the last `beat`.
4. `_stream_subprocess` keeps every existing log phrase and result tuple;
   all 11 watchdog tests pass unchanged.
5. fTetWild detector: `"pass 3"` -> 3, `max_energy 7.5` recorded; with
   `opt_iter=10` progress = 0.3; ETA finite after pass >= 1; unrelated
   lines ignored.
6. Dual watchdog: a converter with a fake clock advanced past `stall_s`
   inside `_run_inner` raises `PhaseStalled` (not `RuntimeError("cancelled")`),
   and `result.success` stays false with the action in `errors`.
7. BL watchdog: a fake-clock `PolyBoundaryLayerEngine` run with a loop that
   never beats raises `PhaseStalled` at the next scale/round check; no mesh
   is written (the existing `_validate`/write-only-on-success contract).
8. CheckMesh R1: `failed_checks=3`, `defects["face_pyramids"].count=39`,
   `defects["skewness"].count=5`, `defects["non_orthogonality"].max` close
   to 128.044; `passed=False`. Healthy: `passed=True`, `defects={}`.
9. Repair sequential candidate: if it works, R1 internal pyramids 33 -> <33
   with min volume > 0; checkMesh `Failed` may remain because skewness is a
   separate class. Accepting a pyramid-only improvement must not hide the
   remaining skew; the report states both.
10. No default change: a normal GUI/worker run with no watchdog action
    produces the same engine kwargs and mesh bytes as before; the watchdog
    only adds liveness/action behavior.

## Falsification signals

- Any existing watchdog test changes behavior (phrase, retry count, kill).
- A phase reports progress/ETA that is not backed by a real counter.
- A repair candidate is written despite a worse pyramid count or a negative
  volume.
- `CheckMeshReport` marks a failed mesh as passed without an explicit
  `accept(reason)` call.
- A timeout fires on a healthy but slow run because progress was not wired;
  the fix is progress wiring, not a larger timeout.

## Rollback

`PhaseWatchdog` is additive; each phase switch is a small call site. If a
phase integration regresses, keep the object for the other phases and revert
only that adapter. The repair is opt-in and never changes default bytes; if
R1 is not improved it stays off with the measured declaration in
`docs/residual_risks.md`.

## Closing evidence (after implementation)

- `tests/test_phase_watchdog.py`: 9 passed (stall/timeout/heartbeat/ETA,
  progress clamps, external abort, fTetWild parser).
- `tests/test_phase_watchdog_integration.py`: 5 passed (dual guard, BL guard
  + progress mapping, GUI progress signal, PolyDualWorker streaming/cancel,
  fTetWild real progress path).
- `tests/test_checkmesh_report.py`: 8 passed; the existing
  `test_checkmesh_parsing.py` 6 passed and `test_gmsh_watchdog.py` 28 passed
  unchanged after the `_stream_subprocess` refactor.
- **R1 real-case measurement** (`case_e2e_173334b`, 253,681 cells;
  real checkMesh log: 39 face pyramids, 5 highly skew, non-ortho 128.044,
  `Failed 3`):
  - in-process base: 41 total pyramid violations (32 owner-side + 9
    neighbour-side) across 33 internal faces, 0 negative volumes,
    min volume 3.745e-11;
  - batch move (131 vertices): 1.0x -> 365 violations / 40 negative;
    0.5x -> 200 / 16; 0.25x -> 69 / 2: **rejected**;
  - sequential volume-guarded keep-best: 3 accepted moves, 41 -> 38,
    0 negative volumes, min volume unchanged, ~250 s: **not promoted**
    (no skew improvement, still `Failed 3`, floor not proven, cost high).
- `_stream_subprocess` behavior is unchanged for the existing 28 GMSH
  watchdog tests (same log phrases, retry counts and kill-tree path).
- **Real GMSH smoke** (`GmshVolumeWorker`, cylinder STL, coarse): success in
  7.0 s, `watchdog armed: kill after 300s`, no false stall.  The full corpus
  benchmark was not re-run in this session; it remains the W9 milestone
  validation (the W8 evidence is the real R1 checkMesh + this tet smoke +
  the 28 subprocess watchdog tests).
- Defaults: no mesh parameter changed; the repair code did not land, and the
  watchdog only adds liveness/action behavior.  `POLYFOAMMESH_BL_SERIAL=1`
  remains an explicit way to force serial BL for A/B or debugging.

## What remains open

- cfMesh `MeshWorker` still has its own 4 h loop/ETA (outside the W8
  tet/dual/BL/convert scope); migrating it is a follow-up.
- Internal-pyramid repair needs a transition-cell topology, not a vertex
  nudge; the R1 numbers are the baseline for that work.
- CheckMesh acceptance has the API and report model but no GUI "accept"
  action (product decision; a failed poly mesh is shown as failed).
