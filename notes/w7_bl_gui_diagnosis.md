# W7 — Boundary-layer GUI: diagnosis (measured from the tree, 2026-10-22)

Scope: every boundary-layer parameter the user can (or should be able to)
control, where its value is born, where it is read, where it is clamped,
and whether it reaches the real engine.  No code was changed for this
document.

## 1. GUI -> config -> engine map

Legend for **State**:

- **wired** — the GUI value reaches the engine/subprocess unchanged.
- **hardcoded** — the key exists but the GUI cannot change it.
- **UI-only** — the widget exists but its value never reaches the engine.
- **dead** — a reader exists for a key nobody writes (or vice versa).
- **missing** — the engine supports it, nothing in the GUI/worker uses it.

| Parameter | GUI origin (file:line) | Project/worker key | Engine read | Validation / clamp | State |
|---|---|---|---|---|---|
| BL enabled | `params_panel.py:347` checkbox | `get_bl_params()` returns `None` when off | — (presence gates the stage) | none | wired |
| `nLayers` | `params_panel.py:352-357`, 1..40, default 3 | `nLayers` | `bl_poly.py:1425` `n_layers`; cfMesh `meshDict` | engine `max(1,min(n,40))` **silent** (`bl_poly.py:1618`) | wired, silent clamp |
| first layer | `params_panel.py:358-368` fraction 1e-6..1.0 default 0.005; absolute override `_bl_first_layer_abs` (physics) | `firstLayerThickness` (metres) | `bl_poly.py:1426` `first_height`; cfMesh `maxFirstLayerThickness` | spin-box range; absolute value has no validation | wired, two representations |
| growth ratio | `params_panel.py:369-373`, 1.0..2.0 default 1.2 | `thicknessRatio` | `bl_poly.py:1427` `growth_rate`; cfMesh `thicknessRatio` | engine clamps >2.0 with a result warning (`bl_poly.py:1637-1642`) that the worker never logs; cfMesh falls back to 1.2 if <=1 | wired, clamp not surfaced |
| apply to all | `params_panel.py:374` | `applyToAll` | `openfoam_runner.py:2315` -> patch selection | none | wired |
| velocity | `params_panel.py:395-401` | **none** | only inputs `BLEngine` in `_on_bl_auto_compute` | 0.001..1000 m/s | UI-only (derivation input, not persisted) |
| fluid | `params_panel.py:403-406` | **none** | same | combo | UI-only |
| wall treatment (y+ 30/1) | `params_panel.py:408-417` | **none** | same | combo | UI-only |
| `minBaseEdgeFraction` | **no widget** | hardcoded `0.9` (`params_panel.py:1416`) | `openfoam_runner.py:2332` -> `bl_poly.py:1440` | engine validates >=0; value 0.9 | hardcoded |
| `terminateByBudget` | **no widget** | hardcoded `True` (`params_panel.py:1417`) | `openfoam_runner.py:2335` -> `bl_poly.py:1444` | bool | hardcoded |
| `edgeCapPercentile` | **no widget** | **no key** | `bl_poly.py:1443` supports 0..100 | engine validates 0..100 | **missing / dead end** |
| local termination | **no widget** (automatic retry only) | not written by GUI; worker injects `decoupled_vertex` on failure (`openfoam_runner.py:2485,2513`) | `bl_poly.py:1433` | engine validates mode | hidden automatic override |
| local height retry | **no widget** (automatic only on fTetWild thin-inversion failure) | worker injects `local_height_retry=True` (`openfoam_runner.py:2486`) | `bl_poly.py:1438` | only valid with `decoupled_vertex` | hidden automatic override |
| local max rounds | **no widget** | absent | `bl_poly.py:1435` default 3 | 1..10 | missing (not required by W7) |
| `enabled` in autopoly | — | `main_window.py:3908` reads `bl_params["enabled"]` | `AutopolyParams.bl_enabled` | — | **dead**: `get_bl_params()` never writes `enabled`, so autopoly always sees False |
| `maxThickness` in autopoly | — | `main_window.py:3912` reads `bl_params["maxThickness"]` | `AutopolyParams.bl_max_thickness` | — | **dead**: never written |
| BL numeric persistence | `params_panel.py:2054-2069` saves only `params/bl_checked` | — | — | — | **missing**: nLayers/h1/growth/advanced are lost on restart |
| `SessionSnapshot` BL fields | `core/session.py:40-43` | `bl_n_layers`, `bl_thickness_ratio`, `bl_expansion_ratio` | — | none | **dead**: no GUI caller; `bl_thickness_ratio` is a fraction of max cell, not a ratio |
| presets | — | — | — | — | **missing** |
| effective-parameter JSON | — | — | `bres.stats` only in memory | — | **missing** |

The engine already supports the requested knobs (`bl_poly.py:1440-1445`);
the gap is the GUI/worker contract, not the mesher.

## 2. Debt found

1. **Defaults live in at least four places**: panel (`3`, `0.005`, `1.2`,
   `0.9`, `True`), `DualPolyWorker` (`5`, `0.0`, `1.2`, `0.0`, `False`),
   `PolyBoundaryLayerEngine.run` (`5`, `0.005`, `1.2`), `meshDict` writer
   (`1.2`).  Changing one does not move the others.
2. **Two first-layer representations** (fraction of max cell and absolute
   metres) with the precedence rule only in `ParamsPanel._bl_first_layer`;
   `SessionSnapshot.bl_thickness_ratio` names a fraction as a ratio.
   Conversion is duplicated in `main_window.py:2864-2865` (throat clamp).
3. **No validation boundary**: `get_bl_params()` returns a raw dict; the
   worker reads keys with `dict.get` and its own fallbacks.  An invalid
   preset/legacy project would either silently use different values or fail
   inside the engine after the mesh work is already done.
4. **Silent overrides**: `n_layers` clamp to 40; `growth_rate` clamp to 2.0
   (warning stored in `result.warnings` but not logged); the worker's
   automatic `decoupled_vertex`/`local_height_retry` retry is logged but its
   effective parameters are not recorded.
5. **Dead bindings**: `enabled` and `maxThickness` read by the autopoly
   path and never emitted; the whole `SessionSnapshot` BL block is unused.
6. **No provenance**: nothing records whether a value came from a preset,
   the user, or a default.
7. **Tests pin only 3 keys** (`test_bl_gui_wiring.py`:
   nLayers/growth/first layer/applyToAll/minBaseEdgeFraction/terminateByBudget).
   No test covers `edgeCapPercentile`, local termination, height retry,
   presets, migration, or "the value arrives at the engine".

## 3. Proposed contract

One validated object, `polyfoammesh.core.bl_config.BLConfig`:

- canonical units: first layer in **metres** (`first_height`), plus
  `first_layer_fraction` for the GUI's fraction mode; precedence is
  explicit (`first_height > 0` wins) and implemented once;
- fields: `enabled`, `n_layers`, `first_height`, `first_layer_fraction`,
  `growth_rate`, `apply_to_all`, `min_base_edge_fraction`,
  `terminate_by_budget`, `edge_cap_percentile`, `local_termination`,
  `local_height_retry`, `local_max_rounds`, `velocity`, `fluid`,
  `wall_treatment`;
- `validate()` returns structured problems; `normalized()` clamps to the
  documented ranges and returns one message per clamp;
- per-field `provenance` (`preset` | `user` | `default` | `clamped`),
  serialized with the config;
- `schema_version = 1` in every serialized object;
- `from_mapping()` is tolerant for project/preset loading: bad values fall
  back to the default with a message, it never raises into the GUI;
- `migrate_legacy_mapping()` reads the old keys (`nLayers`,
  `thicknessRatio`, `firstLayerThickness`, `applyToAll`, and the unused
  session keys `bl_n_layers`, `bl_thickness_ratio`, `bl_expansion_ratio`);
  a legacy fraction is converted to metres once, with the max cell supplied
  by the caller;
- `engine_kwargs(max_cell)` is the only translation to
  `PolyBoundaryLayerEngine.run`; `legacy_meshdict_dict(max_cell)` is the
  only translation to cfMesh's `meshDict`;
- `BLPresetStore` persists named snapshots (`schema_version`, config,
  last used) atomically; invalid entries are reported and skipped/clamped,
  never crash the project;
- the run writes `bl_config.json` (effective values + provenance + warnings)
  next to the case, and logs the same summary plus every engine warning.

## 4. Reuse / refactor / remove

**Reuse**: `bl_poly.PolyBoundaryLayerEngine` is untouched; it remains the
executor.  cfMesh `meshDict` keys stay identical.

**Refactor**:
- `ParamsPanel.get_bl_params()` delegates to `BLConfig`;
- `DualPolyWorker` builds the engine kwargs from `BLConfig`, logs effective
  values and provenance, records them in `result.stats`, writes the JSON;
- the Advanced tab exposes the five missing controls (min base edge
  fraction, budget termination, edge cap percentile, local termination,
  height retry) plus local rounds;
- presets + current config persisted through `AppSettings`.

**Remove / fix**:
- remove the duplicated hardcoded values in the panel and the worker;
- make `enabled` / `maxThickness` real in the legacy dict (so the autopoly
  binding stops being dead), or remove the reads.  Emitting them is the
  smaller, useful fix;
- keep `SessionSnapshot` as-is for now; the migration adapter reads its
  old fields, so no second format is introduced.

## 5. Load estimate and risks

Estimated: 11-13 files, ~700-900 net lines (config ~330, store ~120,
GUI ~220, worker ~70, tests ~350, docs ~120).  Risks:

- GUI tests are offscreen and must not touch the real `%APPDATA%`; the
  preset store takes an explicit path and the panel exposes its store for
  tests.
- Existing defaults must not move: `3` layers, `0.005` fraction, `1.2`
  growth, `0.9` min edge, budget termination on, local termination off,
  edge cap `0`.  Every default is pinned by a test.
- `local_height_retry` is invalid without `decoupled_vertex`; the GUI must
  enforce that visibly (disable + uncheck), not let the engine reject it.
- cfMesh ignores the new keys, so the shared dict stays backward
  compatible.
- `edgeCapPercentile` has a measured failure mode on the venturi
  (`docs/residual_risks.md` FASE 4); exposing it is honest only with the
  tooltip and default `0` (historical behaviour).  It is an advanced knob,
  not a recommendation.

## 6. What will be measured (exit evidence)

1. Round-trip of `BLConfig` (values + provenance + schema) is byte-stable.
2. A GUI value set in the Advanced tab appears in
   `BLConfig.engine_kwargs()` — the exact dict handed to
   `PolyBoundaryLayerEngine.run`.
3. The worker logs the effective config and writes `bl_config.json`; engine
   warnings are logged.
4. An out-of-range preset loads with a declared clamp, not an exception.
5. A legacy session mapping converts the old fraction to metres once.
6. Defaults reproduce the pre-W7 behaviour (no default changed).
