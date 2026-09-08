"""Single source of truth for the PolyFoamMesh version.

Imported by both `polyfoammesh/__init__.py` (package `__version__`)
and `polyfoammesh/gui/design_tokens.py` (UI `APP_VERSION`), so the
two can never drift apart again.

NOTE on the installer: `installer/inno_setup.iss` carries its own
`MyAppVersion` (2.1.0) which is the version of the *standalone build*,
not of the Python package — it was historically one minor ahead and is
not wired to this file. Bump it manually when producing a new installer.
"""

__version__ = "2.1.0"
