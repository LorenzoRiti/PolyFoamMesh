"""Adaptive Mesh Refinement (AMR) — gradient-based cell refinement.

Offline, static-case refinement built on OpenFOAM's ``refineMesh``,
``postProcess``, and ``topoSet`` utilities: refines an existing meshed case
based on a field's gradient, remapping solution fields onto the refined
mesh in between iterations.

This is refinement only — OpenFOAM's standalone ``refineMesh`` utility has
no coarsening/unrefine capability (that requires solver-integrated
``dynamicRefineFvMesh``, a fundamentally different run-time architecture
from this module's refine-a-static-case design), so cells are never removed.

Pipeline:
  1. Compute mag(grad(field)) via postProcess on the existing field data
  2. Select cells above the gradient threshold into a cellSet via topoSet
  3. Execute refineMesh -overwrite (2:1 hanging-node constraint)
  4. Remap field values onto the refined mesh via refineMesh's own cellMap
  5. Loop until no more cells exceed the threshold, or max_cells is reached
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import validate_case_dir
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


@dataclass
class AMRParams:
    """Parameters controlling adaptive refinement."""
    field: str = "U"                # Field to base refinement on
    gradient_threshold: float = 0.1 # Refine where |gradient| > threshold
    max_cells: int = 2_000_000      # Maximum cell count
    max_iterations: int = 5         # Max refine→solve cycles
    refine_interval: int = 1        # Refinement interval in time-steps
    unrefine_coeff: float = 0.1     # Unused: standalone refineMesh cannot coarsen (see module docstring)
    n_buffer_layers: int = 1        # Buffer layers around refined cells


@dataclass
class AMRResult:
    success: bool = False
    case_dir: str = ""
    initial_cells: int = 0
    final_cells: int = 0
    iterations: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_time_s: float = 0.0


class AMREngine:
    """Adaptive mesh refinement using OpenFOAM refineMesh.

    Operates on an existing OpenFOAM case with a valid mesh and
    solution fields.

    Usage::

        amr = AMREngine(of_config)
        amr.set_case_dir("path/to/case")
        amr.set_params(AMRParams(field="U", gradient_threshold=0.05))
        result = amr.run()
    """

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._case_dir: Path | None = None
        self._params = AMRParams()
        self._result = AMRResult()

    def set_case_dir(self, case_dir: Path | str) -> None:
        result = validate_case_dir(case_dir)
        if not result.valid:
            raise ValueError(result.message)
        self._case_dir = Path(case_dir)

    def set_params(self, params: AMRParams) -> None:
        self._params = params

    def run(self) -> AMRResult:
        """Execute the AMR loop.

        Returns an ``AMRResult`` with iteration history and cell counts.
        """
        self._result = AMRResult(case_dir=str(self._case_dir))
        start = datetime.now()

        if not self._case_dir:
            self._result.errors.append("No case directory set.")
            return self._result

        octo.log_event("amr", "workflow_start", {
            "case_dir": str(self._case_dir),
            "field": self._params.field,
            "max_iterations": self._params.max_iterations,
        })

        try:
            # Count initial cells
            self._result.initial_cells = self._count_cells()
            logger.info("AMR start: %d cells", self._result.initial_cells)

            for i in range(self._params.max_iterations):
                iter_start = datetime.now()

                # 1. Compute refinement field
                refine_field = self._compute_refinement_field()

                # 2. Mark cells for refinement
                n_to_refine, n_to_unrefine = self._mark_cells(refine_field)

                if n_to_refine == 0 and n_to_unrefine == 0:
                    logger.info("AMR converged at iteration %d — no cells to refine", i)
                    self._result.iterations = i
                    break

                # 3. Execute refineMesh
                self._execute_refinement()

                # 4. Map solution to new mesh
                self._map_solution()

                cells_now = self._count_cells()
                elapsed = (datetime.now() - iter_start).total_seconds()

                self._result.history.append({
                    "iteration": i + 1,
                    "cells_before": cells_now,
                    "n_refined": n_to_refine,
                    "n_unrefined": n_to_unrefine,
                    "wall_time_s": round(elapsed, 1),
                })
                self._result.iterations = i + 1

                logger.info(
                    "AMR iter %d: refined=%d unrefined=%d cells=%d (%.1fs)",
                    i + 1, n_to_refine, n_to_unrefine, cells_now, elapsed,
                )

                if cells_now >= self._params.max_cells:
                    logger.info("AMR stopped: reached max cells (%d)", cells_now)
                    break

                octo.log_event("amr", "iteration", {
                    "iteration": i + 1,
                    "cells": cells_now,
                    "refined": n_to_refine,
                })

            self._result.final_cells = self._count_cells()
            self._result.success = True
            octo.log_event("amr", "workflow_complete", {
                "initial_cells": self._result.initial_cells,
                "final_cells": self._result.final_cells,
                "iterations": self._result.iterations,
            })

        except Exception as exc:
            self._result.errors.append(str(exc))
            logger.exception("AMR failed")
            octo.log_event("amr", "workflow_failed", {"error": str(exc)})

        self._result.wall_time_s = round((datetime.now() - start).total_seconds(), 1)
        return self._result

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------
    _REFINE_SET_NAME = "amrRefineCells"

    def _count_cells(self) -> int:
        """Count cells from polyMesh/owner."""
        if not self._case_dir:
            return 0
        from cfmesh_autogui.core.boundary_reader import count_cells
        return count_cells(self._case_dir)

    def _run_postprocess(self, func: str) -> None:
        """Run `postProcess -func <func>` at time 0, raising on failure."""
        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"postProcess -func '{func}' -time 0 2>&1 | tail -10"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"postProcess -func '{func}' failed:\n{(result.stdout + result.stderr)[-400:]}"
            )

    def _compute_refinement_field(self) -> str:
        """Compute mag(grad(field)) as a real scalar field via postProcess,
        so topoSet's fieldToCell has an actual per-cell value to threshold.

        This used to write a bespoke refineMeshDict with a `field`/
        `gradientThreshold` entry that OpenFOAM's `refineMesh` utility does
        not read at all — refineMesh only understands a `set` entry naming
        a pre-existing cellSet (verified against its source: it does
        `refineDict.get<word>("set")`, i.e. a single cellSet name, not the
        dict block `set {};` previously written here). The actual pipeline
        needs the field computed AND a cellSet selected from it; both are
        implemented now, split across this method and _mark_cells().

        Verified live: a single `postProcess -func 'mag(grad(U))'` call
        fails ("Field grad(U) not found") — grad(field) must be computed
        and written to disk in its own separate postProcess invocation
        first; mag() can then read it from disk in a second call.

        Returns:
            The name of the computed scalar field, e.g. ``"mag(grad(U))"``.
        """
        if not self._case_dir:
            raise RuntimeError("No case directory")
        field_name = self._params.field
        self._run_postprocess(f"grad({field_name})")
        self._run_postprocess(f"mag(grad({field_name}))")
        return f"mag(grad({field_name}))"

    def _mark_cells(self, refine_field: str) -> tuple[int, int]:
        """Select cells above the gradient threshold into a real cellSet.

        Uses topoSet's `fieldToCell` source (verified against real
        OpenFOAM 2512) to build an actual cellSet from the computed scalar
        field, rather than the previous `refineMesh -dry-run` call — that
        flag does not exist on `refineMesh` at all (confirmed via
        `refineMesh -help`: "Invalid option: -dry-run"), so every call
        silently failed and _mark_cells always returned (0, 0), reporting
        "AMR converged — no cells to refine" regardless of the actual mesh.

        OpenFOAM's refineMesh only refines; there is no standalone
        unrefine/coarsen utility (that requires solver-integrated
        dynamicRefineFvMesh, a fundamentally different architecture from
        this module's offline refine-a-static-case design), so
        n_to_unrefine is always 0 — this is a real limitation, not a bug to
        silently paper over.

        Returns:
            ``(n_to_refine, 0)``
        """
        if not self._case_dir:
            return 0, 0
        case_dir = self._case_dir
        system_dir = case_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        (system_dir / "topoSetDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object topoSetDict; }\n"
            "actions\n(\n"
            "    {\n"
            f"        name        {self._REFINE_SET_NAME};\n"
            "        type        cellSet;\n"
            "        action      new;\n"
            "        source      fieldToCell;\n"
            f'        field       "{refine_field}";\n'
            f"        min         {self._params.gradient_threshold};\n"
            "        max         1e30;\n"
            "    }\n);\n",
            encoding="ascii",
        )

        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"topoSet -time 0 2>&1 | tail -10"
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = result.stdout + result.stderr
            m = re.search(rf"{re.escape(self._REFINE_SET_NAME)}\s+now size\s+(\d+)", output)
            n_refine = int(m.group(1)) if m else 0
            logger.debug("Refine markers: refine=%d", n_refine)
            return n_refine, 0
        except Exception as exc:
            logger.warning("Marker computation failed: %s", exc)
            return 0, 0

    def _execute_refinement(self) -> None:
        """Run refineMesh to perform the actual refinement.

        `set` must be the cellSet's NAME (a word), not a dict block — the
        previous `set {};` was rejected by refineMesh's own dictionary
        parsing (`refineDict.get<word>("set")`). Also adds `-overwrite`:
        without it, refineMesh writes the refined mesh to a new time
        directory instead of constant/polyMesh, so cell counts read
        afterwards would silently reflect the pre-refinement mesh.
        """
        if not self._case_dir:
            return
        case_dir = self._case_dir
        system_dir = case_dir / "system"

        (system_dir / "refineMeshDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object refineMeshDict; }\n"
            f"\nset {self._REFINE_SET_NAME};\n"
            "coordinateSystem global;\n"
            "globalCoeffs\n{\n"
            "    tan1 (1 0 0);\n"
            "    tan2 (0 1 0);\n"
            "}\n"
            "directions (tan1 tan2 normal);\n"
            "useHexTopology  yes;\n"
            "geometricCut    no;\n"
            "writeMesh       no;\n",
            encoding="ascii",
        )

        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"refineMesh -dict system/refineMeshDict -overwrite 2>&1 | tail -15"
        )

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"refineMesh failed (exit {result.returncode}):\n{result.stderr[-300:]}"
            )
        logger.info("refineMesh executed OK")

    def _map_solution(self) -> None:
        """Remap field values from the pre-refinement mesh onto the refined one.

        refineMesh -overwrite only changes mesh topology; it does not touch
        field files at all, and OpenFOAM's own `mapFields` utility maps
        between two DIFFERENT case directories (`mapFields <sourceCase>`) —
        it has no way to map a case onto its own in-place refinement, since
        source and target would be identical. refineMesh does write
        `<time>/polyMesh/cellMap`, a plain labelList giving each new cell's
        parent old-cell index (verified live) — use that directly: every
        new cell inherits its parent's field value. This only handles the
        common `nonuniform List<scalar/vector>` internalField format;
        anything else (uniform fields need no remapping at all; unsupported
        formats are left untouched with a warning) is intentionally
        conservative rather than guessing.
        """
        if not self._case_dir:
            return
        case_dir = self._case_dir
        cell_map_path = case_dir / "0" / "polyMesh" / "cellMap"
        if not cell_map_path.exists():
            logger.warning("mapFields: no cellMap found at %s — fields left unmapped", cell_map_path)
            return

        try:
            from cfmesh_autogui.core.boundary_reader import read_label_list
            cell_map = read_label_list(cell_map_path)
        except Exception as exc:
            logger.warning("mapFields: could not read cellMap: %s", exc)
            return

        time0_dir = case_dir / "0"
        for field_path in time0_dir.iterdir():
            if not field_path.is_file() or field_path.name.startswith("polyMesh"):
                continue
            try:
                _remap_internal_field(field_path, cell_map)
            except Exception as exc:
                logger.warning("mapFields: could not remap %s: %s", field_path.name, exc)

    def export_report(self, path: Path | str) -> None:
        """Export the AMR result as JSON."""
        data = {
            "success": self._result.success,
            "case_dir": self._result.case_dir,
            "initial_cells": self._result.initial_cells,
            "final_cells": self._result.final_cells,
            "iterations": self._result.iterations,
            "history": self._result.history,
            "wall_time_s": self._result.wall_time_s,
            "errors": self._result.errors,
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        logger.info("AMR report exported: %s", path)


def _remap_internal_field(field_path: Path, cell_map: list[int]) -> None:
    """Remap a field's `nonuniform List<scalar|vector>` internalField using
    *cell_map* (new-cell-index -> old-cell-index), so each new cell inherits
    its parent cell's value. `uniform` internalFields need no remapping (a
    single value applies regardless of cell count) and are left untouched.
    """
    text = field_path.read_text(encoding="ascii", errors="replace")

    m = re.search(r"internalField\s+nonuniform\s+List<(scalar|vector)>\s*\n?\s*(\d+)\s*\n\s*\(", text)
    if not m:
        return  # uniform, or a type this remapper doesn't handle

    field_type = m.group(1)
    old_count = int(m.group(2))
    open_idx = m.end() - 1
    depth = 0
    close_idx = None
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                close_idx = i
                break
    if close_idx is None:
        return
    body = text[open_idx + 1:close_idx]

    if field_type == "scalar":
        old_values = [tok.strip() for tok in body.split() if tok.strip()]
    else:
        old_values = [f"({v.strip()})" for v in re.findall(r"\(([^)]*)\)", body)]

    if len(old_values) != old_count:
        return  # file doesn't match its own declared count; don't guess

    new_values = [old_values[old_idx] for old_idx in cell_map]
    new_body = "\n".join(new_values)
    new_text = (
        text[:open_idx + 1] + "\n" + new_body + "\n" + text[close_idx:]
    )
    # Update the declared count too.
    new_text = re.sub(
        r"(internalField\s+nonuniform\s+List<(?:scalar|vector)>\s*\n?\s*)\d+",
        rf"\g<1>{len(new_values)}",
        new_text,
        count=1,
    )
    field_path.write_text(new_text, encoding="ascii")
