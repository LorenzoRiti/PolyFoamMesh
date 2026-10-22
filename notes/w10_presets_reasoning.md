# W10 — Preset configuration: reasoning and predictions (2026-10-22)

Predecessor: `notes/w10_presets_diagnosis.md`.  Written before the final
suite/E2E; no mesh default changes.

## Claim

A single `MeshConfig` (built on W7's `BLConfig`) can carry the full mesh
preset through GUI, project, CLI and worker without a second settings
language.  Fast/Balanced/Fine are snapshots of that object, each with an
explicit evidence string; an invalid user preset is reported/clamped and
never blocks startup.

## Predictions

1. `MeshConfig` round-trip preserves `detail_slider`, `mesher`, adaptive/
   fTetWild flags, nested `BLConfig` values and provenance; `schema_version`
   is 1 at both levels.
2. Legacy QSettings (`params/detail_slider`, `params/bl_checked`,
   `params/bl_config`) load into `MeshConfig`; the new
   `params/mesh_config` wins when both exist.  A project with only the
   detail key keeps BL **off**, because the pre-W7 GUI default was off.
3. `TemplatePreset` mapping: `coarse -> 4`, `medium -> 10`,
   `fine -> 16`, `very_fine -> 19` on the 0..20 slider; `max_cell_ratio`
   and `min_cell_ratio` are ignored with a warning (geometry-relative).
4. Tolerant loading clamps slider 99 -> 20, bad mesher -> default,
   fTetWild eps 2.0 -> 0.5, BL layers 999 -> 40; strict mode raises
   `MeshConfigError`.
5. `MeshPresetStore` round-trips a user preset, records last_used, rejects
   the reserved built-in names, and reports a `kind` mismatch instead of
   decoding the wrong object.
6. Built-ins: target cells 45,731 / 447,214 / 4,373,448 for Fast/Balanced/
   Fine (log slider mapping); Fast/Balanced have measured evidence and are
   not experimental; Fine is experimental because its exact E2E was not
   re-run.
7. GUI: applying Fast sets slider 4, BL 3 layers, and
   `get_bl_params()["nLayers"] == 3`; applying Fine shows
   `experimental`; an invalid preset loads with clamp and the project still
   opens.
8. Project save writes only `params/mesh_config`; restore gives back detail/
   mesher/fTetWild/BL; old legacy keys are read but not written.
9. `DualPolyWorker(mesh_config=...)` consumes the `MeshConfig.bl` submodel;
   the legacy `bl_params` path remains compatible.
10. CLI (`config_from_cli_args`) and GUI build the same `MeshConfig` for the
    same values; the CLI prints the JSON contract.
11. No default mesh value changes: a panel with untouched widgets produces
    the same effective BL/detail as before; applying a preset is explicit.
12. The full suite remains green; the preset tests add no WSL dependency.

## Falsification signals

- A preset cannot be reproduced from its JSON/CLI (values differ).
- A failed preset replaces the current project config or crashes startup.
- A built-in claims a measurement that is not in `evidence`.
- Fast/Balanced/Fine ordering is not monotonic in the shared target mapping.
- Any default GUI value changes without an explicit preset selection.
- The worker ignores the nested `BLConfig` when a `MeshConfig` is supplied.

## Measurement plan

- Unit: round-trip, legacy QSettings, template adapter, clamps, store
  round-trip/invalid/kind mismatch, built-in evidence/ordering, CLI/GUI
  equality (tests/test_mesh_config.py).
- GUI offscreen: apply Fast/Fine, user preset, reserved name, invalid
  preset, project round-trip, legacy migration, worker BL projection,
  CLI/GUI equality (tests/test_gui_mesh_presets.py).
- Preset values: target-cell table from the shared slider mapping; real
  evidence from the M2 R1/cube E2E and W1 fine tet stage.  No new
  full-corpus run is claimed.
- Full fast suite + ruff.

## Closing evidence

- `tests/test_mesh_config.py`: **12 passed** (round-trip, legacy QSettings,
  template adapter, clamps, tolerant load, CLI/GUI equality, shared target
  ordering, built-in evidence/experimental, user preset, invalid preset,
  kind mismatch).
- `tests/test_gui_mesh_presets.py`: **9 passed** (Fast/Fine apply, user
  preset, reserved name, invalid clamp, project save/restore, legacy
  migration, worker BL projection, CLI/GUI equality).
- Shared target cells: Fast 45,731 < Balanced 447,214 < Fine 4,373,448.
- Full fast suite: **1635 passed, 2 skipped, 20 deselected, 1 xfailed**;
  `ruff check src/ tests/` green.
- No default mesh value changed; preset application is explicit.  Fine is
  marked experimental because only its W1 fine tet stage is measured, not
  the exact full Fine E2E.

## Rollback

All preset application is opt-in.  If a built-in value proves wrong, only
the `BUILTIN_MESH_PRESETS` constant changes; user files and project JSON
stay valid because the schema is unchanged.  The legacy QSettings adapter
can remain read-only for one release; no second writer is introduced.
