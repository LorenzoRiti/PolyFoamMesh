# W10 — Preset configuration: diagnosis (2026-10-22)

Scope: what a "preset" means today, where mesh configuration lives, which
paths reach the engine, and what is missing for Fast / Balanced / Fine.
Inputs: `params_panel.py`, `main_window.py`, `settings_migration.py`,
`core/bl_config.py`, `core/bl_presets.py`, `commercial/template_engine.py`,
`gmsh_subprocess.py`, W7/W8 notes and the M2 audit.

## 1. Current configuration map

| Parameter | Where it lives today | Persistence | Reaches engine? |
|---|---|---|---|
| Detail (0..20 slider) | `ParamsPanel._detail_slider`; target cells via `_slider_to_cells` (log 10K..20M) | `params/detail_slider` (QSettings) | yes: target cells / derived max-min / GMSH detail |
| Adaptive refinement | `_adaptive_sizing_check` | **not persisted** | yes: GMSH worker flag |
| Mesher | three checkable buttons (`gmsh_direct_poly`, `ftetwild_poly`, `cfmesh`) | **not persisted** | yes: main-window dispatch |
| fTetWild eps/adaptive/simplify | `_ftet_eps_frac`, `_ftet_adaptive`, `_ftet_simplify` | **not persisted** | yes: fTetWild worker |
| BL (layers/first/growth/advanced) | `BLConfig` via panel (W7) | `params/bl_config` JSON | yes: worker/engine |
| Max/min cell | derived from slider/geometry, read-only | not persisted | yes |
| Unit / scale | `_unit_selector`, scale button | `params/unit` only | yes |

W7 already gives a versioned BL contract; there is **no full mesh/project
config**.  Mesher, adaptive and fTetWild settings are lost on restart, and a
preset cannot capture them.

## 2. Presets today

### 2.1 BL presets (W7)

`BLPresetStore` / `bl_presets.json`: real, versioned, tolerant, user-named.
They only contain `BLConfig`; changing the detail/mesher/fTetWild switches
is outside the snapshot.

### 2.2 Template presets (commercial)

`TemplatePreset` has `detail`, `bl_enabled`, `bl_n_layers`,
`max_cell_ratio`, `min_cell_ratio`, solver fields.  `TemplateEngine` uses
the ratios when it builds a case (real path), but the **GUI**
`_apply_template_preset` had a broken mapping:

- `coarse/medium/fine/very_fine -> 1/2/3/4` on a 0..20 slider, so four of
  the five detail levels landed in the "coarse" bucket;
- it ignored `max_cell_ratio`/`min_cell_ratio` and all BL values except
  `n_layers`;
- it set an `_algorithm_combo` that does not carry these values.
This is a decorative/partial preset path, not a full mesh preset.

### 2.3 Fast / Balanced / Fine

No such presets exist.  The roadmap's required values are not in code.

## 3. Duplication / dead paths

1. **Two sizing scales**: `_slider_to_cells` in the GUI only; a preset
   cannot compute the target count without the widget.
2. **Two preset files/ideas**: BL presets exist; template presets exist; a
   full mesh preset does not.  Adding a third file/format would violate
   reuse.
3. **Old QSettings keys**: `params/detail_slider`, `params/bl_checked`,
   `params/bl_config` are the only project state; mesher/adaptive/ftet are
   missing.  `params/bl_checked` can disagree with the BLConfig `enabled`.
4. **Template detail map** wrong (above), and ratios ignored by the GUI.
5. **CLI** has no mesh-config contract; `gmsh_wrapper` receives individual
   flags, so a preset cannot be reproduced headless.
6. **Worker** receives a legacy BL dict/N layers, not the full config.
7. **`SessionSnapshot`** now holds `bl_config` (M2) but is still unused by
   the GUI; project persistence is QSettings only.

## 4. Proposed model

### 4.1 `MeshConfig` (one portable object)

Fields:

- `detail_slider` 0..20 (the portable sizing input; no absolute cell sizes,
  which are geometry-specific);
- `mesher` (`gmsh_direct_poly` default, `ftetwild_poly`, `cfmesh`,
  `gmsh_direct`);
- `adaptive_sizing`;
- `ftet_eps_frac` (0.01..0.5), `ftet_adaptive`, `ftet_simplify`;
- nested `BLConfig`;
- per-field provenance (`default|user|preset|clamped`);
- `schema_version = 1` in serialization.

Adapters:

- new QSettings key `params/mesh_config` (JSON);
- legacy QSettings (`detail_slider`, `bl_checked`, `bl_config`) read once;
- old `TemplatePreset` (`detail`, `bl_enabled`, `bl_n_layers`) read with a
  warning that cell ratios are geometry-relative and deliberately ignored;
- CLI parser (`--detail-slider`, `--mesher`, fTetWild, BL flags).

The slider -> target-count mapping moves to `core.mesh_config` (one copy)
and the GUI delegates to it.

### 4.2 Presets

`MeshPreset(name, config, origin, evidence, experimental)`.

`MeshPresetStore` uses the **same generic store** as `BLPresetStore`
(`core/preset_store.py`, schema 1, atomic write, kind, last_used, tolerant
decode).  Built-ins are code constants; user presets are files.  No second
preset language.

### 4.3 Built-in values and evidence

| Preset | Detail | Target cells | BL | Evidence | Status |
|---|---|---:|---|---|---|
| Fast | 4 (coarse) | 45,731 | 3 layers, 0.01 fraction, 1.2 growth | cube_duct full E2E: 334.4 s, 256,767 cells, BL 100%, checkMesh Failed 1 (non-ortho 80.48°) | measured profile |
| Balanced | 10 (medium) | 447,214 | 5 layers, 0.01 fraction, 1.2 growth | R1 173334 E2E: 110.0 s, 253,681 cells, 174,265 prisms, 100% coverage, Failed 3 | measured profile |
| Fine | 16 (fine) | 4,373,448 | 5 layers, 0.01 fraction, 1.2 growth | W1 fTetWild fine tet: 2.37M tets, 3738 s, max_energy 7.55; exact Fine E2E not re-run | **experimental** |

The evidence is a measured *profile*, not a promise that every geometry
hits the same time.  Fine is marked experimental because the exact full
preset was not re-run on the current tree; it is not advertised as
recommended.

## 5. Reuse / refactor / remove

**Reuse**: `BLConfig`, `BLPresetStore` behavior, `TemplatePreset` solver
fields, `estimate_cell_count`/target mapping.

**Refactor**:
- extract `core.preset_store.PresetStore`; make `BLPresetStore` and
  `MeshPresetStore` thin codecs over it;
- extract slider mapping to `MeshConfig`;
- add GUI mesh-preset group and `get_mesh_config`/`set_mesh_config`;
- persist one `params/mesh_config`; `MeshConfig.from_settings` migrates old
  keys;
- `DualPolyWorker` accepts the full `MeshConfig` and uses its BL submodel;
- fix `_apply_template_preset` to use `MeshConfig.from_template_preset`.

**Remove/deprecate**:
- stop writing `params/detail_slider`, `params/bl_checked`,
  `params/bl_config` (keep read-only adapter for one migration window);
- remove the GUI's private `_CELL_TARGET_*` constants (delegated);
- old template GUI detail map (replaced by the adapter);
- no new parallel preset file.

## 6. Load estimate and risks

Files: new `mesh_config.py`, `preset_store.py`, `mesh_presets.py`; refactor
`bl_presets.py`, `params_panel.py`, `main_window.py`, `settings_migration.py`,
`validation.py`; tests 3 files; docs.  ~8-12 files, ~600-900 net lines.
Risks:

- presets must not change defaults: applying one is opt-in;
- absolute cell sizes must not enter a preset (geometry-specific);
- old project loading must keep BL off when it was off;
- preset file kind mismatch must not decode a BL config as a mesh config;
- Fine must stay experimental until measured.
