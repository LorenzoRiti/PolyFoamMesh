# Third-Party Licenses

PolyFoamMesh è GPLv3 ([LICENSE](LICENSE)) e usa le seguenti dipendenze.
Nessuna di queste è ridistribuita modificata; le versioni sono quelle
dichiarate in [pyproject.toml](pyproject.toml).

| Componente | Licenza | Note |
|---|---|---|
| [GMSH](https://gmsh.info/) | GPLv2+ | Eseguito sempre in subprocess separato (CLI + JSON), mai linkato nello stesso processo — vedi `core/gmsh_subprocess.py` |
| [cfMesh](https://cfmesh.com/) / [OpenFOAM](https://openfoam.org/) | GPLv3 | Non ridistribuito: gira in WSL2, installato separatamente dall'utente (`installer/setup_wsl_openfoam.ps1`) |
| [PySide6](https://www.qt.io/qt-for-python) (Qt for Python) | LGPLv3 | Compatibile con GPLv3 |
| [CadQuery](https://cadquery.readthedocs.io/) | Apache-2.0 | |
| [PyVista](https://pyvista.org/) / pyvistaqt | MIT | |
| [trimesh](https://trimesh.org/) | MIT | |
| [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) | BSD-3-Clause | |
| [meshio](https://github.com/nschloe/meshio) | MIT | |
| [ReportLab](https://www.reportlab.com/) | BSD-style | Genera i report PDF di qualità mesh |
| [PyMeshFix](https://github.com/pyvista/pymeshfix) | GPLv3 (via MeshFix) | |

Se noti una licenza mancante o non aggiornata, apri una issue —
vedi [CONTRIBUTING.md](CONTRIBUTING.md).
