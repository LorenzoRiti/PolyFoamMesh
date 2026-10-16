# Documentation index

A map of everything under `docs/`. Start with the two user-facing guides; the
rest is reference or internal material.

## User-facing guides

- **[INSTALL.md](INSTALL.md)** — install on Windows: prebuilt installer (recommended) or from source.
- **[INSTALL_LINUX.md](INSTALL_LINUX.md)** — native Linux install (no WSL): system libraries, Python, OpenFOAM, troubleshooting.
- **[USER_GUIDE.md](USER_GUIDE.md)** — how the app works, tab by tab, with the workflow and shortcuts.

## Reference

- **[ROADMAP.md](ROADMAP.md)** — where the project stands after 2.3.0 and the planned next steps, with the evidence and acceptance gate for each item.
- **[residual_risks.md](residual_risks.md)** — known limits and failure modes, with measured evidence; read before trusting a result.
- **[cfmesh_meshdict_reference.md](cfmesh_meshdict_reference.md)** — verified keys and syntax for the `system/meshDict` read by `cartesianMesh` (v2512).
- **[i18n.md](i18n.md)** — translation toolchain: extract, translate and compile Qt `.ts`/`.qm` files.

## Developer / maintainer

- **[dev/](dev/)** — internal engineering notes: distribution/rebuild steps (`DISTRIBUTION.md`), concurrency model, poly mesher status, handoffs and planning docs. Not needed to use the app.
- **[superpowers/](superpowers/)** — plans and specs for large workstreams (beat-snappy benchmarking, boundary-layer program, complex-geometry program).
- **[conf.py](conf.py)**, **[index.rst](index.rst)**, **[installation.rst](installation.rst)** — Sphinx build scaffolding for generated API docs.
- **[api/](api/)**, **[commercial/](commercial/)** — Sphinx autodoc stubs (`modules.rst`) for those packages.
