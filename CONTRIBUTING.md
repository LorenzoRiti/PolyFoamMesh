# Contributing to PolyFoamMesh

Thanks for your interest. The project is young as open source: issues and PRs
are welcome, but keep in mind that it is maintained by a single person.

## Development environment setup

```bash
pip install -e ".[test]"
pip install ruff
```

Runtime requirements: Python 3.11+, GMSH (`pip install gmsh`), optionally
OpenFOAM v2512 in WSL2 for the cfMesh/checkMesh paths — see [README](README.md).

## Before opening a PR

```bash
ruff check src/ tests/
pytest tests/ -m "not slow and not wsl" -q
```

These two commands are the same gate that runs in CI
([.github/workflows/ci.yml](.github/workflows/ci.yml)). The tests marked `slow`
or `wsl` require a real Windows+WSL2+OpenFOAM environment and do not run in
CI: run them locally if your change touches those paths.

## Reporting a bug

Open an issue with: input geometry (or a minimal one that reproduces the
problem), command/path used in the GUI, log from `%APPDATA%\polyfoammesh\logs\app.log`.

## Known limitations

Before reporting a behaviour as a bug, check
[docs/residual_risks.md](docs/residual_risks.md) — it lists the already
known and documented limitations (timeouts on very large meshes, concave
geometry cases, etc.).

## Contribution license

The project is GPLv3 ([LICENSE](LICENSE)). By contributing you accept that your
code is distributed under the same license.
