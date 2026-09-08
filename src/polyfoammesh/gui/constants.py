"""Project-wide constants for the GUI layer."""
from __future__ import annotations

# Maximum geometry file size accepted via File > Open or drag-and-drop (500 MB).
# Applies to both STEP and STL files. Larger files are rejected to avoid
# loading the whole geometry into memory before failing.
MAX_GEOMETRY_FILE_BYTES: int = 500 * 1024 * 1024

# Legacy alias — many call sites still reference this name.
MAX_STEP_FILE_BYTES: int = MAX_GEOMETRY_FILE_BYTES

# Maximum number of recent geometry files remembered in QSettings.
MAX_RECENT_GEOMETRY_FILES: int = 5

# Legacy alias for the recent-files list key.
MAX_RECENT_STEP_FILES: int = MAX_RECENT_GEOMETRY_FILES
