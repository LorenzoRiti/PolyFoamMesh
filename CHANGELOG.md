# Changelog

All notable changes to PolyFoamMesh are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased] - 2026-09-17

### Fixed

- **RAM-exhaustion crash on large meshes**: a real run at ~5M cells with
  "concave closure" enabled exhausted 32GB of RAM and crashed the
  system. Cause: Python structures (dicts/lists) built over the entire
  mesh instead of only the cells/faces actually involved in the
  boundary layer and the post-BL merge. Fixed with chunked geometry, a
  CSR adjacency graph, and maps restricted to wall vertices — a real
  5M-cell / 15M-face run now peaks at ~13GB. The safety threshold moved
  from a rough eyeballed limit (1.5M cells) to a measured one (8M
  faces); see [#9](https://github.com/LorenzoRiti/PolyFoamMesh/issues/9)
  for the validation still missing on the real polyhedral path.
- Real bug in the skewness formula (summed two normalisation terms
  instead of taking the max, as OpenFOAM itself does) and a bug that
  silently disabled the quality pass on already-healthy meshes.

### Added (experimental, opt-in, defaults unchanged)

- Three boundary-layer extrusion-normal strategies, evaluated with real
  checkMesh on concave geometries: "most visible" normal (minimises the
  maximum angle), Laplacian smoothing, guided bilateral filter. Only the
  first has a real measurable effect on its own; combined with the
  per-vertex local-height retry (below) it beats both individual levers
  on boundary-layer coverage.
- Per-vertex local-height retry: instead of excluding a wall vertex
  whose prism fails at full height, retry with a much shorter height
  before giving up.
- GETMe-inspired mesh smoothing, aspect-ratio-aware.

### Not promoted (tried, measured, honestly discarded)

- Guided bilateral normal filter (Zhang 2015): no effect — the dual's
  analytic normals are not noisy the way that filter's target case is.
- Direct cell merging to fix the cylinder's aspect ratio: improved the
  in-process metric but was rejected by real checkMesh
  (non-orthogonality +80%, skewness +233%). The cylinder aspect-ratio
  gap (4.49 vs snappyHexMesh's 3.02) remains open — see
  [#8](https://github.com/LorenzoRiti/PolyFoamMesh/issues/8) for the
  actual work needed (a directional split operator).

## [2.2.0] - 2026-09-08

### Breaking changes

- **Data path renamed**: application data moved from
  `%APPDATA%\cfmesh-autogui\` to `%APPDATA%\polyfoammesh\` (logs,
  sessions, octopoda telemetry). On first run the new folder is
  populated automatically by copying the old one's contents, if
  present. Anyone with a previous install loses nothing, but the old
  path is no longer written to. Application settings (theme, recent
  parameters) live in the Windows registry and do not move: they stay
  where they were, so user preferences survive the package rename
  (`cfmesh_autogui` → `polyfoammesh`).

### Added

- **Release pipeline**: on a `v*` tag push, GitHub Actions builds the
  PyInstaller executable + the Inno Setup installer on Windows and
  attaches them to the tag's GitHub Release (with a changelog excerpt
  from this file). Removed obsolete installer scripts (duplicate NSIS
  and Inno); fixed `UninstallDisplayName` in `inno_setup.iss` (directive
  split across two lines).
- **Algorithm-substitution notice in the GUI**: when the engine silently
  substitutes a meshing path (boundary layer disabled, parallel→serial,
  coarser GMSH retry, quality retry), the log panel now shows a
  highlighted `[SUBSTITUTED]` line — no more silence.
- **Bilingual documentation**: `docs/INSTALL.md` and
  `docs/USER_GUIDE.md` rewritten in English (primary), Italian versions
  in `docs/INSTALL.it.md` / `docs/USER_GUIDE.it.md`, cross-links at the
  top.
- **Community**: `CODE_OF_CONDUCT.md`, PR template in
  `.github/PULL_REQUEST_TEMPLATE.md`, 5 "good first issue" issues on the
  public repo.
- `_paths.py`: single source of truth for the application data
  directory and a one-shot migration from the legacy path.

## [2.1.0] - 2026-09-08

### Breaking changes

- **Python package rename**: `cfmesh_autogui` → `polyfoammesh`. Imports
  across the code, tests, and tools were updated; the CLI entry-point
  modules are now `polyfoammesh`, `polyfoammesh-mesh`, and
  `polyfoammesh-batch`. The PyInstaller executable is named
  `PolyFoamMesh`.

### Added

- CI brought back to green on GitHub Actions (it was red on every
  push): `pytest-qt` in the extras, POSIX-compatible paths in
  `config.py`/`validation.py`, `networkx` and `rtree` dependencies
  declared.

### Fixed

- Manual refinement zones: `setAsBackgroundMesh` was discarding the
  adaptive geometric field; now MIN-combined (`gmsh_wrapper.py`).
- Closed curved bodies (e.g. a sphere) tessellated non-watertight:
  coincident vertices at seams/poles are now merged and degenerate
  triangles discarded (`geometry.py::tessellate_patches`).
- Tests that overwrote the tracked `sample_cad/`; an always-skip stub
  replaced with a real integration test.
- Git history: a ~278 MB blob (`installer/output/*.exe`) removed from
  `master`'s history with `filter-branch`.

### Note

- The prebuilt executable and installer are not committed to the repo:
  they live in `dist/` and `installer/output/` respectively (both
  ignored by `.gitignore`) and are generated locally or by the release
  pipeline.

[2.2.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.2.0
[2.1.0]: https://github.com/LorenzoRiti/PolyFoamMesh/releases/tag/v2.1.0
