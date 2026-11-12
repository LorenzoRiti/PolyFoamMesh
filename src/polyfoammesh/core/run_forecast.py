"""Plain-language forecast of a meshing run, shown before it starts.

Why: the tet stage (and parts of the dual and boundary layer) print nothing
for minutes, which looks like a freeze.  The user is told up front which
stages will run, roughly how long they take and that silence is normal.

Calibration (measured 2026-11-12, NACA wind tunnel on the fTetWild path):
~4.5 ms per estimated cell for tet + dual (see geometry._SECONDS_PER_ESTIMATED_CELL);
whole runs landed between 0.7x (no boxes, 4.7 min) and 1.9x (boxes, 11.7 min) of that
figure once the boundary layer was included.
"""
from __future__ import annotations

_SECONDS_PER_CELL = 4.5e-3
_LOW, _HIGH = 0.7, 1.9


def _fmt(seconds: float) -> str:
    if seconds < 90:
        return f"{int(round(seconds / 5.0) * 5) or 5} s"
    return f"{int(round(seconds / 60.0))} min"


def forecast_text(est_cells: int | None, engine: str = "",
                  boundary_layer: bool = False, has_boxes: bool = False) -> str:
    """One paragraph: stages, expected duration range, silence warning."""
    stages = ["tet mesh", "polyhedral conversion"]
    if boundary_layer:
        stages.append("boundary layer")
    stages.append("checkMesh")
    plan = " > ".join(f"{i + 1}) {s}" for i, s in enumerate(stages))
    msg = f"Run plan: {plan}."
    if est_cells and est_cells > 0:
        base = est_cells * _SECONDS_PER_CELL
        lo, hi = base * _LOW, base * _HIGH
        msg += (f" About {est_cells:,} cells: expect {_fmt(lo)} to {_fmt(hi)} "
                "in total.")
        if hi > 600:
            msg += (" For a quicker first look, raise Max cell size or move "
                    "the fineness slider towards coarse.")
    if engine:
        msg += f" Engine: {engine}."
    msg += (" Some stages print nothing for minutes (the tet stage most of "
            "all): a 'still running' line every 30-60 s means it is working, "
            "not frozen. Cancel is always available.")
    return msg
