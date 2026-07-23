from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Plugin(ABC):
    """Base class for all cfmesh-autogui plugins.

    Subclass this, implement the three abstract methods, and place
    the .py file in the ``plugins/`` directory. It will be auto-discovered
    at application startup.
    """

    @abstractmethod
    def on_install(self, app):
        """Called once when the plugin is loaded at app startup.

        Use this to register menu entries, connect signals, or
        inject custom widgets into the main window.
        """

    @abstractmethod
    def on_uninstall(self):
        """Called when the plugin is removed or the app shuts down.

        Clean up any registered resources (signals, menu items, etc.).
        """

    @abstractmethod
    def on_mesh_completed(self, case_dir: Path):
        """Called after every meshing job completes successfully.

        ``case_dir`` points to the finished OpenFOAM case directory
        containing constant/polyMesh/, 0/, system/, etc.
        """
