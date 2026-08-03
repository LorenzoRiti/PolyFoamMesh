# Found while hardening — NOT fixed (business scope)

This file records issues discovered during the anti-freeze hardening pass
that are **out of scope** (business logic / pre-existing / uncommitted work).
They were intentionally not corrected; they are documented here for a future
business-logic session.

## Pre-existing test failures (unrelated to this pass)

The following tests fail in the working tree **before** this hardening pass;
they are caused by uncommitted changes to `src/cfmesh_autogui/core/geometry.py`
(and friends) that were already present when the pass started:

- `tests/test_sizing_resolves_features.py::test_thickness_measures_local_distance_not_model_extent`
- `tests/test_sizing_resolves_features.py::test_constriction_is_visible_in_the_thickness_distribution`
- `tests/test_sizing_resolves_features.py::test_constriction_drives_a_smaller_min_cell`
- `tests/test_watertight_meshdict.py::test_volume_mesh_and_quality_steps_do_not_need_a_qt_event_loop`

None of these import `gui/main_window.py` or `gui/task_runner.py`; they test
core sizing logic and a `commercial/openmp_accel` monkeypatch path that the
uncommitted geometry work affects. (The last one is order-dependent: it
passes in isolation.)

## Valve/cfMesh freeze incident (2026-08-03)

User report: starting a significant poly/cfMesh run (e.g. the valve) freezes
the app, the window goes white, then it "crashes". Investigation
(`.slim/deepwork/freeze-meshing-valve.md`):

- Windows WER records **AppHangB1** (hang) on python.exe; `log.meshing` shows
  cartesianMesh grinding through endless bad-face iterations (iteration 0:
  429471 bad faces, 411984 zero/negative tetrahedra) — the input mesh is
  garbage (inverted faces), so cfMesh never converges and emits tens of
  thousands of warnings.
- The garbage mesh correlates with the uncommitted `core/geometry.py`
  changes (same root as the failing sizing tests). **Not fixed here** —
  business/geometry scope.
- The white screen + hang mechanism WAS fixed (UI/concurrency scope):
  the streamed log flood saturated the GUI thread
  (`_stream_subprocess._pump` forwarded every line; `LogPanel` used an O(n)
  cursor-walk prune). Fixes: `LogPanel` uses Qt's O(1)
  `setMaximumBlockCount` + a cross-thread burst guard (drops beyond 2000
  queued appends, notes "N righe soppresse"); `_stream_subprocess` throttles
  live `on_line` forwarding (~50 lines/s) while still capturing every line,
  and caps the captured line lists (~100k lines) against memory blowup.

## Audit corrections

- `main_window.py:3654` (audit): `_make_fix_action` was flagged as running
  on the RetryRunner worker thread with cross-thread widget access. Verified:
  `_on_attempt_finished` is delivered queued to the `RetryRunner` object
  (created on the GUI thread), so `fix_action` already ran on the GUI thread.
  The *remaining* risk was the heavy retessellation itself freezing the GUI —
  that part was migrated to a background task under a nested event loop
  (contract preserved, UI responsive).

## Residuals worth a follow-up

- `commercial/quick_mesh.py:122` calls `self._of_config.validate()` inside
  `QuickMesh.run()`. Currently reachable only from worker threads
  (`adaptive_integration.py:279`); if a future flow calls `QuickMesh.run()`
  directly on the UI thread, the WSL probe will freeze it. Consider caching
  WSL availability like `main_window._wsl_available`.
- `_on_meshing_finished` still does seconds-scale post-processing on the GUI
  thread (error analysis, boundary parse, case setup) after the mesh worker
  finishes. Not a freeze for typical cases; revisit if multi-minute post steps
  appear.
- `RetryRunner` (cartesianMesh) keeps its own internal QThread lifecycle
  instead of being submitted through `TaskManager`. It follows the same
  worker-QObject-on-QThread pattern; unification would require touching
  `openfoam_runner.py` internals.
- Viewer `read_openfoam_mesh_stats` reads header bytes only (fast path); the
  full-file fallbacks were moved into workers. If stats ever grow to
  full-file reads, move them into a task too.
