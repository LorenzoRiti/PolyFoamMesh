# Third-Party Licenses

PolyFoamMesh is GPLv3 ([LICENSE](LICENSE)) and uses the following
dependencies. None of these is redistributed modified; versions are
those declared in [pyproject.toml](pyproject.toml).

| Component | License | Notes |
|---|---|---|
| [GMSH](https://gmsh.info/) | GPLv2+ | Always run in a separate subprocess (CLI + JSON), never linked into the same process — see `core/gmsh_subprocess.py` |
| [cfMesh](https://cfmesh.com/) / [OpenFOAM](https://openfoam.org/) | GPLv3 | Not redistributed: runs in WSL2, installed separately by the user (`installer/setup_wsl_openfoam.ps1`) |
| [PySide6](https://www.qt.io/qt-for-python) (Qt for Python) | LGPLv3 | Compatible with GPLv3 |
| [CadQuery](https://cadquery.readthedocs.io/) | Apache-2.0 | |
| [PyVista](https://pyvista.org/) / pyvistaqt | MIT | |
| [trimesh](https://trimesh.org/) | MIT | |
| [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) | BSD-3-Clause | |
| [meshio](https://github.com/nschloe/meshio) | MIT | |
| [ReportLab](https://www.reportlab.com/) | BSD-style | Generates the mesh-quality PDF reports |
| [PyMeshFix](https://github.com/pyvista/pymeshfix) | GPLv3 (via MeshFix) | |

If you notice a missing or outdated license, open an issue — see
[CONTRIBUTING.md](CONTRIBUTING.md).
