"""Journal / Macro Recorder — action recording and playback.

Every user action can be recorded into a replayable journal and
played back against different geometry files for automation.

Concepts:
  - ``JournalEntry``: a single recorded action with timestamp and params.
  - ``Journal``: a list of entries that can be saved/loaded as JSON.
  - ``Recorder``: context manager that captures actions.
  - ``Player``: replays a journal against new geometry.

Usage::

    journal = Journal()
    with journal.recording():
        # ... user performs actions ...
        journal.record("set_cell_sizes", max_cell=0.05, min_cell=0.01)
        journal.record("set_boundary_layers", n_layers=5)
        journal.record("run_mesh")

    journal.save("macro.json")

    # Playback on different geometry
    player = JournalPlayer(journal)
    player.play("new_geometry.step")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class JournalEntry:
    """A single recorded action."""
    action: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "params": self.params,
            "timestamp": self.timestamp,
            "duration_s": self.duration_s,
        }


class Journal:
    """A recordable and replayable macro journal.

    Usage::

        journal = Journal()
        journal.record("load_geometry", path="model.step")
        journal.record("set_cell_sizes", max_cell=0.05, min_cell=0.01)
        journal.save("macro.json")
    """

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []
        self._recording: bool = False
        self._version: str = "1.0"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        """Start recording actions."""
        self._recording = True
        self._entries.clear()
        octo.log_event("journal", "recording_started", {})

    def stop_recording(self) -> list[JournalEntry]:
        """Stop recording and return captured entries."""
        self._recording = False
        count = len(self._entries)
        octo.log_event("journal", "recording_stopped", {"entries": count})
        return self._entries

    def record(self, action: str, **params: Any) -> JournalEntry:
        """Record a single action with its parameters.

        Args:
            action: Action name (e.g. ``"set_cell_sizes"``).
            **params: Key-value parameters for the action.

        Returns:
            The created ``JournalEntry``.
        """
        entry = JournalEntry(
            action=action,
            params=params,
            timestamp=datetime.now().isoformat(),
        )
        if self._recording:
            self._entries.append(entry)
        return entry

    def __enter__(self) -> Journal:
        self.start_recording()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop_recording()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    @property
    def entries(self) -> list[JournalEntry]:
        return list(self._entries)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "created_at": datetime.now().isoformat(),
            "entry_count": self.entry_count,
            "entries": [e.to_dict() for e in self._entries],
        }

    def save(self, path: Path | str) -> None:
        """Save the journal to a JSON file."""
        data = self.to_dict()
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        logger.info("Journal saved: %s (%d entries)", path, self.entry_count)

    @classmethod
    def load(cls, path: Path | str) -> Journal:
        """Load a journal from a JSON file."""
        data = json.loads(Path(path).read_text())
        journal = cls()
        journal._version = data.get("version", "1.0")
        for e in data.get("entries", []):
            journal._entries.append(JournalEntry(
                action=e.get("action", ""),
                params=e.get("params", {}),
                timestamp=e.get("timestamp", ""),
                duration_s=e.get("duration_s", 0.0),
            ))
        logger.info("Journal loaded: %s (%d entries)", path, journal.entry_count)
        return journal

    # ------------------------------------------------------------------
    # Script generation
    # ------------------------------------------------------------------
    def to_python_script(self, target_geometry: str = "") -> str:
        """Generate a Python script from the journal entries.

        The script uses the ``cfmesh_autogui.commercial.watertight``
        API to replay the recorded actions.

        Args:
            target_geometry: Path to the geometry file to use during playback.

        Returns:
            A string containing the complete Python script.
        """
        lines = [
            "#!/usr/bin/env python3",
            '"""Auto-generated mesh script from journal."""',
            "from pathlib import Path",
            "from cfmesh_autogui.commercial.watertight import WatertightWorkflow",
            "",
            "",
            "def main():",
            f'    wf = WatertightWorkflow()',
        ]

        if target_geometry:
            lines.append(f'    wf.set_geometry("{target_geometry}")')

        for entry in self._entries:
            action = entry.action
            params = entry.params

            if action == "set_geometry":
                lines.append(f'    wf.set_geometry("{params.get("path", "")}")')
            elif action == "set_cell_sizes":
                lines.append(
                    f'    wf.set_cell_sizes('
                    f'max_cell={params.get("max_cell", 0.05)}, '
                    f'min_cell={params.get("min_cell", 0.01)})'
                )
            elif action == "set_boundary_layers":
                lines.append(
                    f'    wf.set_boundary_layers('
                    f'n_layers={params.get("n_layers", 3)}, '
                    f'thickness_ratio={params.get("thickness_ratio", 0.005)}, '
                    f'expansion_ratio={params.get("expansion_ratio", 1.2)})'
                )
            elif action == "set_detail":
                lines.append(f'    wf.set_detail("{params.get("level", "medium")}")')
            elif action == "run_mesh":
                lines.append(f'    result = wf.run()')
                lines.append(f'    print(f"Mesh: {{result.cell_count}} cells, '
                             f'success={{result.success}}")')
            else:
                # Generic fallback
                params_str = ", ".join(
                    f'{k}={v!r}' for k, v in params.items()
                )
                lines.append(f'    # {action}({params_str})')

        lines.append("")
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append("    main()")
        lines.append("")

        return "\n".join(lines)


class JournalPlayer:
    """Replays a journal against one or more geometry files.

    Usage::

        journal = Journal.load("macro.json")
        player = JournalPlayer(journal)
        report = player.play("geometry.step")
        print(f"Result: {report.cell_count} cells")
    """

    def __init__(self, journal: Journal) -> None:
        self._journal = journal

    def play(self, geometry_path: str, **overrides: Any) -> Any:
        """Execute the journal against the given geometry.

        Args:
            geometry_path: Path to the geometry file to mesh.
            **overrides: Override parameters for specific actions
                (e.g. ``max_cell=0.02``).

        Returns:
            The ``WorkflowResult`` from :class:`WatertightWorkflow`.
        """
        from cfmesh_autogui.commercial.watertight import WatertightWorkflow

        wf = WatertightWorkflow()
        wf.set_geometry(geometry_path)

        for entry in self._journal.entries:
            action = entry.action
            params = {**entry.params, **overrides}

            if action == "set_cell_sizes":
                wf.set_cell_sizes(
                    params.get("max_cell", 0.05),
                    params.get("min_cell", 0.01),
                )
            elif action == "set_boundary_layers":
                wf.set_boundary_layers(
                    params.get("n_layers", 3),
                    params.get("thickness_ratio", 0.005),
                    params.get("expansion_ratio", 1.2),
                )
            elif action == "set_detail":
                wf.set_detail(params.get("level", "medium"))
            elif action == "run_mesh":
                return wf.run()

        # If no explicit run_mesh entry, run anyway
        return wf.run()
