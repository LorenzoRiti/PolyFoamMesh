"""Guided meshing workflow — the "what should I do next" brain.

Commercial preprocessors (Fluent's Watertight Geometry Workflow, Star-CCM+'s
automated mesh) present meshing as a short, ordered checklist where every step
knows its prerequisites and tells the user exactly what to do next. This module
is that logic, kept free of any GUI so it can be tested directly; the workflow
dock is a thin view over it.

The point is "senza impazzimenti": the user is never left guessing which button
is safe to press, and never hits a cryptic cfMesh error because a prerequisite
(a loaded geometry, a watertight surface, a chosen cell size) was skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Step(Enum):
    GEOMETRY = "geometry"
    SIZING = "sizing"
    BOUNDARY_LAYERS = "boundary_layers"
    GENERATE = "generate"
    QUALITY = "quality"
    EXPORT = "export"


class Status(Enum):
    LOCKED = "locked"    # prerequisites not met — don't let the user start here
    READY = "ready"      # prerequisites met, this is a valid next action
    DONE = "done"        # completed successfully
    WARNING = "warning"  # completed, but with a caveat the user should see


# Fixed order the user is guided through. BOUNDARY_LAYERS is optional and never
# blocks GENERATE.
ORDER = [
    Step.GEOMETRY,
    Step.SIZING,
    Step.BOUNDARY_LAYERS,
    Step.GENERATE,
    Step.QUALITY,
    Step.EXPORT,
]


@dataclass
class MeshingWorkflow:
    """Tracks progress and derives the next action from a few state flags.

    Every field mirrors something the app already knows; nothing here computes
    geometry or meshes — it only decides ordering, gating, and guidance.
    """

    geometry_loaded: bool = False
    # None = not checked yet; True/False = watertight result.
    watertight: bool | None = None
    sizing_ready: bool = False
    bl_configured: bool = False
    mesh_generated: bool = False
    # None = not checked yet; True = passed all gates; False = failed.
    quality_passed: bool | None = None
    exported: bool = False

    # ------------------------------------------------------------------
    # Per-step status
    # ------------------------------------------------------------------
    def status_of(self, step: Step) -> Status:
        if step is Step.GEOMETRY:
            if not self.geometry_loaded:
                return Status.READY
            return Status.WARNING if self.watertight is False else Status.DONE

        if step is Step.SIZING:
            if not self.geometry_loaded:
                return Status.LOCKED
            return Status.DONE if self.sizing_ready else Status.READY

        if step is Step.BOUNDARY_LAYERS:
            # Optional: available once geometry is loaded, never blocks meshing.
            if not self.geometry_loaded:
                return Status.LOCKED
            return Status.DONE if self.bl_configured else Status.READY

        if step is Step.GENERATE:
            # Needs a geometry, a chosen size, and (for a usable mesh) a closed
            # surface. A non-watertight surface is allowed but flagged.
            if not (self.geometry_loaded and self.sizing_ready):
                return Status.LOCKED
            if self.mesh_generated:
                return Status.DONE
            return Status.WARNING if self.watertight is False else Status.READY

        if step is Step.QUALITY:
            if not self.mesh_generated:
                return Status.LOCKED
            if self.quality_passed is None:
                return Status.READY
            return Status.DONE if self.quality_passed else Status.WARNING

        if step is Step.EXPORT:
            if not self.mesh_generated:
                return Status.LOCKED
            return Status.DONE if self.exported else Status.READY

        raise ValueError(f"unknown step {step!r}")  # pragma: no cover

    def statuses(self) -> dict[Step, Status]:
        return {s: self.status_of(s) for s in ORDER}

    # ------------------------------------------------------------------
    # Guidance
    # ------------------------------------------------------------------
    def next_step(self) -> Step | None:
        """The single most important step to act on now, or None if finished.

        Walks the ordered list and returns the first step that is actionable
        (READY, or WARNING for a required step). The optional BOUNDARY_LAYERS
        step is skipped as a "next action" — it's offered, not demanded.
        """
        for step in ORDER:
            status = self.status_of(step)
            if step is Step.BOUNDARY_LAYERS:
                continue
            if status is Status.READY:
                return step
            if status is Status.WARNING and step in (Step.GEOMETRY, Step.GENERATE):
                # A caveat the user should resolve before moving on.
                return step
        return None

    def next_action(self) -> str:
        """A short, plain instruction for what to do now."""
        step = self.next_step()
        if step is None:
            return "Workflow complete — mesh generated, checked and exported."

        hints = {
            Step.GEOMETRY: (
                "Load a STEP or STL geometry to begin."
                if not self.geometry_loaded
                else "Geometry is not watertight — fix gaps/missing faces, or "
                "proceed knowing the mesh may leak."
            ),
            Step.SIZING: "Set cell sizes (or press Auto-Suggest) for the mesh.",
            Step.GENERATE: (
                "Ready to mesh — press Generate Mesh."
                if self.watertight is not False
                else "Geometry is not watertight; meshing may fail or leak. "
                "Fix it first, or generate anyway at your own risk."
            ),
            Step.QUALITY: "Run the quality check on the generated mesh.",
            Step.EXPORT: "Export the case (e.g. for BaramFlow).",
        }
        return hints.get(step, "")

    def progress(self) -> tuple[int, int]:
        """(#completed required steps, #total required steps) for a progress bar.

        BOUNDARY_LAYERS is optional and excluded from the denominator.
        """
        required = [s for s in ORDER if s is not Step.BOUNDARY_LAYERS]
        done = sum(1 for s in required if self.status_of(s) is Status.DONE)
        return done, len(required)
