# W7 — Boundary-layer GUI: reasoning and predictions (written before the tests)

Date: 2026-10-22.  Predecessor: `notes/w7_bl_gui_diagnosis.md`.

## Claim to establish

A single validated `BLConfig` is the only path from a GUI control (or a
preset, or the CLI) to the boundary-layer engine; every exposed control has
a measurable effect on `PolyBoundaryLayerEngine.run`, and every clamp or
automatic override is declared in the log and in `bl_config.json`.

Important failure modes: (a) the panel keeps a second hardcoded default that
silently wins; (b) the advanced widgets feed only `get_bl_params()` and the
worker ignores them; (c) an out-of-range preset corrupts startup; (d) the
new defaults change today's behaviour.

## Evidence path (cheapest decisive first)

1. **Unit-level contract** (`tests/test_bl_config.py`, pure Python):
   round-trip, provenance, validation, tolerant load, legacy migration,
   declared clamp.  This is the decisive test of the object; no Qt.
2. **GUI -> engine kwargs** (`tests/test_gui_bl_advanced.py`, offscreen):
   set each widget through its public API, build the config exactly as the
   panel does, compare with the kwargs the worker hands the engine.  This
   catches decorative bindings without running a mesh.
3. **Worker integration** (same file, monkeypatched engine): call the
   worker's BL-kwargs builder, then a fake engine `run(**kwargs)`; assert
   the values and that the JSON/log contain provenance.  No GMSH, no WSL.
4. **Preset store** (temp path): save, reload, corrupt/out-of-range entry,
   legacy project migration.  No real `%APPDATA%`.
5. **Full fast suite + ruff** as the regression gate.  Existing
   `test_bl_gui_wiring.py`, `test_bl_first_layer_absolute.py`,
   `test_meshdict_gen.py`, `test_session.py` are the compatibility net.

A real synthetic mesh run is *not* needed to prove the config binding: the
engine's own tests already cover meshing.  The synthetic run specified in
the phase prompt will be a mock-captured engine call (step 3), because a
real poly BL needs a multi-minute GMSH/dual pipeline and would not add
information about the GUI contract.

## Predictions (before running anything)

1. `BLConfig()` defaults reproduce the current effective GUI behaviour:
   `n_layers=3`, `first_layer_fraction=0.005`, `first_height=0`
   (fraction mode), `growth_rate=1.2`, `apply_to_all=False`,
   `min_base_edge_fraction=0.9`, `terminate_by_budget=True`,
   `edge_cap_percentile=0.0`, `local_termination="off"`,
   `local_height_retry=False`, `local_max_rounds=3`, `velocity=10`,
   `fluid="air"`, `wall_treatment="wall_functions"`, `enabled=True`.
2. `to_mapping()` -> `from_mapping()` returns an equal config and equal
   provenance.  The mapping always contains `schema_version: 1`.
3. `normalized()` clamps `n_layers=0` to 1, `n_layers=99` to 40,
   `growth_rate=3` to 2.0, `edge_cap_percentile=-1` to 0, and returns one
   warning per clamp with the old and new value; each clamped field's
   provenance becomes `"clamped"`.
4. `from_mapping(..., tolerant=True)` never raises: a string in a numeric
   field, an unknown enum, or a missing key falls back to the default and
   is reported.  `strict=True` raises `BLConfigError` with the field name.
5. Legacy migration: `bl_n_layers=4`, `bl_thickness_ratio=0.005`,
   `bl_expansion_ratio=1.3`, `max_cell=0.2` -> `n_layers=4`,
   `first_height = 0.001 m` (0.005 * 0.2, converted once),
   `first_layer_fraction=0.005`, `growth_rate=1.3`, provenance `"user"`.
   Legacy `firstLayerThickness` (already metres) wins over the fraction.
6. GUI: setting `_bl_min_base_edge` to 0.7, unchecking
   `_bl_terminate_budget`, setting `_bl_edge_cap_pct` to 50, choosing
   `decoupled_vertex`, checking height retry, and setting rounds to 5 makes
   `get_bl_config().engine_kwargs(max_cell)` contain exactly
   `min_base_edge_fraction=0.7`, `terminate_by_budget=False`,
   `edge_cap_percentile=50.0`, `local_termination="decoupled_vertex"`,
   `local_height_retry=True`, `local_max_rounds=5`.
7. Trying to check height retry while the mode is `off`/`decoupled` is
   refused by the panel (disabled, unchecked) and `BLConfig.validate()`
   also reports it; the engine never receives an invalid combination.
8. Preset store: saving a named preset and reloading it in a new store
   yields the same config; a file containing `nLayers: 999` loads with a
   warning and a clamped 40, and a malformed JSON starts empty with an
   error string instead of raising.  `last_used` survives the reload.
9. Worker: `bl_config.json` contains the schema version, the effective
   values and provenance; the log contains one `[poly] BL effective:`
   line; engine `result.warnings` are logged as `[poly] BL warning:`.
10. No existing test regresses.  The only expected friction is tests that
    assert on the exact set of `get_bl_params()` keys; a search shows the
    current tests only index known keys, so extra keys are safe.

## If a prediction is wrong

A wrong prediction is recorded in this file before changing the test.  In
particular, if the offscreen panel cannot be constructed without native
geometry, the GUI evidence path falls back to constructing `BLConfig` from
widget values only (no `_suggest_meshes` needed for the advanced tab).

### Measured deviation #1 (recorded after the first GUI run)

Prediction 1 said `first_layer_fraction = 0.005`.  The actual historical
value was **0.01**: `ParamsPanel.__init__` called `setValue(0.005)` BEFORE
`setDecimals(6)`, so Qt rounded the value to the then-active two decimals.
The panel has therefore always sent `0.01 * max_cell` to the engine.

Decision: preserve the measured effective default (`0.01`) in `BLConfig`,
set `setDecimals()` before `setValue()` (so the value is not rounded any
more) and initialise the widget from `BLConfig().first_layer_fraction`.
This is "measure first, default after": fixing the order to make 0.005
active would have silently halved the default first layer, which W7 is not
allowed to do without an A/B.  The config test and the GUI default test
were updated to 0.01 with this note; all other predictions held.

## Default-change declaration

No default changes in this phase.  The new advanced controls start at the
values the engine was already receiving (0.9 / True / 0 / off / false / 3),
so a W7 run with untouched widgets produces the same engine kwargs as the
pre-W7 tree.  This is pinned by a test comparing the default config's
`engine_kwargs()` with the historical hardcoded dict.

## Closing evidence (after implementation)

- Contract tests: 16 passed (`tests/test_bl_config.py`).
- GUI/worker/CLI tests: 12 passed (`tests/test_gui_bl_advanced.py`),
  including the mock-engine call, the real `PolyBoundaryLayerEngine.run`
  signature check, preset restart and the out-of-range preset status.
- Compatibility subset (existing BL/GUI/meshdict/session/settings tests):
  76 passed.
- Full fast suite (`pytest -m "not slow and not wsl"`): **1584 passed,
  2 skipped, 20 deselected, 1 xfailed**; `ruff check src/ tests/` green.
- The only existing expectation changed is
  `test_dual_poly_options.py`: the worker now passes
  `local_termination=False` explicitly (same engine semantics as omitting
  it) because the engine call is fully described by `BLConfig`.
- During the first full run, the worker's new warning loop raised on a
  test double without a `warnings` attribute and skipped the post-BL merge
  repair test.  Fixed with `getattr(bres, "warnings", None)`; the test now
  passes.
- The CLI smoke command
  `python -m polyfoammesh.core.bl_config --... --print engine` prints the
  exact engine kwargs (no import warning after the package-level export was
  left out of `core/__init__`).
