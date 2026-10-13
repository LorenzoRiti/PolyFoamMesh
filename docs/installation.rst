Installation
============

Requirements
------------

* Windows 10/11 with WSL2
* OpenFOAM v2512 (installed in WSL2 Ubuntu)
* Python 3.11+
* PySide6 6.7+

Quick Install
-------------

1. Install WSL2 and OpenFOAM v2512:

   .. code-block:: powershell

       wsl --install -d Ubuntu
       # Inside Ubuntu:
       sudo apt-get update
       sudo apt-get install openfoam2512

2. Install PolyFoamMesh:

   .. code-block:: powershell

       git clone https://github.com/LorenzoRiti/PolyFoamMesh.git
       cd PolyFoamMesh
       pip install -e ".[test]"

   Or use the standalone installer from the
   `Releases <https://github.com/LorenzoRiti/PolyFoamMesh/releases>`_ page —
   see `docs/INSTALL.md <https://github.com/LorenzoRiti/PolyFoamMesh/blob/main/docs/INSTALL.md>`_
   for the full guide.

3. Launch:

   .. code-block:: powershell

       polyfoammesh

Configuration
-------------

Mesh settings are stored in ``%APPDATA%/polyfoammesh/``:

* ``settings.ini`` — UI preferences and last-used parameters
* ``logs/app.log`` — Rotating application log (5 MB max, 3 backups)
* ``octopoda/events.jsonl`` — Audit trail events
* ``session/last_session.json`` — Auto-saved session state
