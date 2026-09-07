"""Solver setup engine — generates complete OpenFOAM case configuration.

Produces system/controlDict, fvSchemes, fvSolution, transportProperties,
turbulenceProperties, and constant/fields for multiple solver types
and turbulence models.

Supported:
  - Solvers: simpleFoam, pimpleFoam, pisoFoam, reactingFoam,
    chtMultiRegionFoam, overPimpleDyMFoam
  - Turbulence: laminar, kEpsilon, kOmegaSST, LES (Smagorinsky, WALE), DES
  - Schemes: Robusto (upwind), Bilanciato (blended), Accurato (linearUpwind)
  - Materials: fluid + solid property tables
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


class SolverType(Enum):
    SIMPLE_FOAM = "simpleFoam"
    PIMPLE_FOAM = "pimpleFoam"
    PISO_FOAM = "pisoFoam"
    REACTING_FOAM = "reactingFoam"
    CHT_MULTI_REGION = "chtMultiRegionFoam"
    OVER_PIMPLE = "overPimpleDyMFoam"


class TurbulenceModel(Enum):
    LAMINAR = "laminar"
    K_EPSILON = "kEpsilon"
    K_OMEGA_SST = "kOmegaSST"
    LES_SMAGORINSKY = "LES_Smagorinsky"
    LES_WALE = "LES_WALE"
    DES = "DES"


class SchemePreset(Enum):
    ROBUSTO = "robusto"
    BILANCIATO = "bilanciato"
    ACCURATO = "accurato"


@dataclass
class MaterialProperties:
    name: str = "air"
    density: float = 1.225        # kg/m³
    viscosity: float = 1.5e-5     # m²/s (kinematic)
    specific_heat: float = 1005.0  # J/(kg·K)
    thermal_conductivity: float = 0.0257  # W/(m·K)
    prandtl_number: float = 0.71


@dataclass
class SolverConfig:
    solver: SolverType = SolverType.SIMPLE_FOAM
    turbulence: TurbulenceModel = TurbulenceModel.K_OMEGA_SST
    schemes: SchemePreset = SchemePreset.BILANCIATO
    material: MaterialProperties = field(default_factory=MaterialProperties)
    n_cores: int = 1
    end_time: float = 1000.0
    delta_t: float = 1.0
    write_interval: int = 100
    p_ref_cell: int = 0  # Reference cell for pressure
    p_ref_value: float = 0.0  # Reference pressure value


class SolverSetup:
    """Generates complete OpenFOAM case configuration files.

    Usage::

        ss = SolverSetup()
        ss.configure(SolverConfig(solver=SolverType.PIMPLE_FOAM))
        ss.write_all(case_dir)
        print(f"Written: {', '.join(ss.files_written)}")
    """

    def __init__(self) -> None:
        self._config = SolverConfig()
        self.files_written: list[str] = []

    def configure(self, config: SolverConfig) -> None:
        self._config = config

    def write_all(self, case_dir: Path | str) -> list[str]:
        """Write all configuration files to the case directory.

        Args:
            case_dir: OpenFOAM case directory.

        Returns:
            List of relative paths written.
        """
        case_dir = Path(case_dir)
        self.files_written = []

        writers = [
            ("system/controlDict", self._control_dict),
            ("system/fvSchemes", self._fv_schemes),
            ("system/fvSolution", self._fv_solution),
            ("constant/transportProperties", self._transport_properties),
            ("constant/turbulenceProperties", self._turbulence_properties),
        ]

        for rel_path, writer_fn in writers:
            path = case_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            content = writer_fn()
            path.write_text(content, encoding="ascii")
            self.files_written.append(str(rel_path))

        # Write initial condition fields (0/)
        self._write_fields(case_dir)

        octo.log_event("solver_setup", "case_written", {
            "solver": self._config.solver.value,
            "turbulence": self._config.turbulence.value,
            "files": len(self.files_written),
        })
        return self.files_written

    # ------------------------------------------------------------------
    # system/controlDict
    # ------------------------------------------------------------------
    def _control_dict(self) -> str:
        c = self._config
        libs = ""
        if c.solver in (SolverType.OVER_PIMPLE,):
            libs = 'libs ("liboversetMesh.so");\n'

        return (
            "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
            f"application {c.solver.value};\n"
            f"startFrom startTime;\n"
            f"startTime 0;\n"
            f"stopAt endTime;\n"
            f"endTime {c.end_time:.0f};\n"
            f"deltaT {c.delta_t:.6f};\n"
            f"writeControl timeStep;\n"
            f"writeInterval {c.write_interval};\n"
            f"purgeWrite 0;\n"
            f"writeFormat binary;\n"
            f"writePrecision 6;\n"
            f"writeCompression on;\n"
            f"timeFormat general;\n"
            f"timePrecision 6;\n"
            f"runTimeModifiable true;\n"
            f"{libs}"
        )

    # ------------------------------------------------------------------
    # system/fvSchemes
    # ------------------------------------------------------------------
    def _fv_schemes(self) -> str:
        preset = self._config.schemes
        if preset == SchemePreset.ROBUSTO:
            grad = "Gauss linear"
            div = "Gauss upwind"
            laplacian = "Gauss linear limited 0.333"
            interp = "linear"
        elif preset == SchemePreset.ACCURATO:
            grad = "Gauss linear"
            div = "Gauss linearUpwind grad(U)"
            laplacian = "Gauss linear limited 0.5"
            interp = "linear"
        else:  # BILANCIATO (default)
            grad = "Gauss linear"
            div = "Gauss linearUpwind grad(U)"
            laplacian = "Gauss linear limited 0.333"
            interp = "linear"

        return (
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
            "\nddtSchemes { default Euler; }\n"
            f"\ngradSchemes {{ default {grad}; }}\n"
            f"\ndivSchemes {{\n"
            f"    default none;\n"
            f"    div(phi,U) {div};\n"
            f"    div(phi,k) {div};\n"
            f"    div(phi,omega) {div};\n"
            f"    div(phi,epsilon) {div};\n"
            f"    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n"
            f"}}\n"
            f"\nlaplacianSchemes {{ default {laplacian}; }}\n"
            f"\ninterpolationSchemes {{ default {interp}; }}\n"
            f"\nsnGradSchemes {{ default limited {0.333 if preset != SchemePreset.ACCURATO else 0.5}; }}\n"
        )

    # ------------------------------------------------------------------
    # system/fvSolution
    # ------------------------------------------------------------------
    def _fv_solution(self) -> str:
        c = self._config
        # Solver tolerances
        tol = "1e-6"
        rel_tol = "0.01"

        # PIMPLE parameters for transient solvers
        pimple = ""
        if c.solver in (SolverType.PIMPLE_FOAM, SolverType.PISO_FOAM, SolverType.OVER_PIMPLE):
            pimple = (
                "PIMPLE\n"
                "{\n"
                "    nOuterCorrectors 3;\n"
                "    nCorrectors 2;\n"
                "    nNonOrthogonalCorrectors 1;\n"
                "    pRefCell 0;\n"
                "    pRefValue 0;\n"
                "    consistent yes;\n"
                "}\n"
            )

        return (
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
            "\nsolvers\n"
            "{\n"
            "    p\n"
            "    {\n"
            "        solver GAMG;\n"
            "        smoother GaussSeidel;\n"
            f"        tolerance {tol};\n"
            f"        relTol {rel_tol};\n"
            "        maxIter 100;\n"
            "    }\n"
            "    U\n"
            "    {\n"
            "        solver smoothSolver;\n"
            "        smoother GaussSeidel;\n"
            f"        tolerance {tol};\n"
            f"        relTol {rel_tol};\n"
            "        nSweeps 1;\n"
            "    }\n"
            '    "k|omega|epsilon|nut"\n'
            "    {\n"
            "        solver smoothSolver;\n"
            "        smoother GaussSeidel;\n"
            f"        tolerance {tol};\n"
            f"        relTol {rel_tol};\n"
            "        nSweeps 1;\n"
            "    }\n"
            "}\n"
            f"\n{pimple}"
            "SIMPLE\n"
            "{\n"
            "    nNonOrthogonalCorrectors 1;\n"
            "    pRefCell 0;\n"
            "    pRefValue 0;\n"
            "}\n"
            "\nrelaxationFactors\n"
            "{\n"
            "    fields\n"
            "    {\n"
            "        p 0.3;\n"
            "    }\n"
            "    equations\n"
            "    {\n"
            '        U 0.7;\n'
            '        "k|omega|epsilon|nut" 0.7;\n'
            "    }\n"
            "}\n"
        )

    # ------------------------------------------------------------------
    # constant/transportProperties
    # ------------------------------------------------------------------
    def _transport_properties(self) -> str:
        mat = self._config.material
        return (
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object transportProperties; }\n"
            f"\ntransportModel  Newtonian;\n"
            f"\nnu              {mat.viscosity:.6e};\n"
            f"\nrho             {mat.density:.4f};\n"
        )

    # ------------------------------------------------------------------
    # constant/turbulenceProperties
    # ------------------------------------------------------------------
    def _turbulence_properties(self) -> str:
        turb = self._config.turbulence
        if turb == TurbulenceModel.LES_SMAGORINSKY:
            sim_type = "LES"
            les_model = "Smagorinsky"
            les_block = (
                "LES\n"
                "{\n"
                f"    LESModel {les_model};\n"
                "    delta cubeRootVol;\n"
                f"    {les_model}Coeffs\n"
                "    {\n"
                "        Ck 0.094;\n"
                "        Ce 1.048;\n"
                "    }\n"
                "}\n"
            )
        elif turb == TurbulenceModel.LES_WALE:
            sim_type = "LES"
            les_block = (
                "LES\n"
                "{\n"
                "    LESModel WALE;\n"
                "    delta cubeRootVol;\n"
                "    WALECoeffs\n"
                "    {\n"
                "        Ck 0.06;\n"
                "        Ce 1.048;\n"
                "    }\n"
                "}\n"
            )
        elif turb == TurbulenceModel.DES:
            sim_type = "DES"
            les_block = (
                "DES\n"
                "{\n"
                "    DESModel SpalartAllmarasDDES;\n"
                "}\n"
            )
        elif turb == TurbulenceModel.LAMINAR:
            # "laminar" is not a registered RASModel (verified against
            # OpenFOAM's TurbulenceModels library — only kEpsilon/kOmegaSST/
            # SpalartAllmaras/... are). This used to fall into the RAS branch
            # below, whose `{...}.get(turb, "kOmegaSST")` fallback (LAMINAR
            # isn't a dict key) silently wrote `RASModel kOmegaSST` — i.e.
            # requesting laminar flow silently turned turbulence modelling ON.
            sim_type = "laminar"
            les_block = ""
        else:
            sim_type = "RAS"
            ras_model = {
                TurbulenceModel.K_EPSILON: "kEpsilon",
                TurbulenceModel.K_OMEGA_SST: "kOmegaSST",
            }.get(turb, "kOmegaSST")
            les_block = (
                "RAS\n"
                "{\n"
                f"    RASModel {ras_model};\n"
                "    turbulence on;\n"
                "    printCoeffs on;\n"
                "}\n"
            )

        return (
            "FoamFile { version 2.0; format ascii; class dictionary; "
            "object turbulenceProperties; }\n"
            f"\nsimulationType {sim_type};\n"
            f"\n{les_block}"
        )

    # ------------------------------------------------------------------
    # 0/ fields
    # ------------------------------------------------------------------
    # (name, class, dimensions, internalField)
    _FIELD_SPECS = (
        ("U", "volVectorField", "[0 1 -1 0 0 0 0]", "uniform (1 0 0)"),
        ("p", "volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0"),
        ("k", "volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0.06"),
        ("omega", "volScalarField", "[0 0 -1 0 0 0 0]", "uniform 10"),
        ("epsilon", "volScalarField", "[0 2 -3 0 0 0 0]", "uniform 0.5"),
        ("nut", "volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0"),
    )

    def _write_fields(self, case_dir: Path) -> None:
        """Write initial condition fields (U, p, k, omega, etc.)

        Used to hardcode boundaryField blocks for exactly four patch names
        ("inlet"/"outlet"/"wall"/"symmetry") regardless of what the mesh's
        real patches are actually called. Any case whose patches are named
        anything else (e.g. a single auto-named "box" patch — the common
        case for a bare STL/STEP import) got NO boundary condition entry at
        all for its real patches: OpenFOAM (and BaramFlow, which opens
        native cases directly) then refuses to run with a "cannot find
        patchField entry" error. Verified live via
        baramflow_export.validate_case() on a real meshed case: it reported
        "no boundary condition for patch(es) box" for every field.

        Now reads the case's ACTUAL patches (same source BCEditor.
        read_boundary() uses) and writes a boundaryField entry for each one,
        classified by name the same way BCEditor does — reusing its
        BC_PRESETS rather than duplicating the same per-field defaults a
        second time (they were already identical).
        """
        dir_0 = case_dir / "0"
        dir_0.mkdir(parents=True, exist_ok=True)

        patches = self._read_case_patches(case_dir)

        for name, cls, dims, internal in self._FIELD_SPECS:
            content = self._render_field(name, cls, dims, internal, patches)
            (dir_0 / name).write_text(content, encoding="ascii")
            self.files_written.append(f"0/{name}")

    def _read_case_patches(self, case_dir: Path) -> list:
        """Real patches from the case's mesh, classified by name.

        Falls back to the legacy inlet/outlet/wall/symmetry set when no
        mesh exists yet (e.g. writing solver files ahead of meshing), so
        behaviour is unchanged for that case.
        """
        from cfmesh_autogui.commercial.bc_editor import BCEditor, BcPatchInfo as PatchInfo
        try:
            return BCEditor().read_boundary(case_dir)
        except Exception as exc:
            logger.warning("Could not read case patches: %s — using fallback defaults", exc)
            return [
                PatchInfo(name=n, orig_name=n, bc_type=n)
                for n in ("inlet", "outlet", "wall", "symmetry")
            ]

    @staticmethod
    def _render_field(name: str, cls: str, dims: str, internal: str, patches: list) -> str:
        from cfmesh_autogui.commercial.bc_editor import BC_PRESETS
        lines = [
            f"FoamFile {{ version 2.0; format ascii; class {cls}; object {name}; }}",
            f"dimensions {dims};",
            f"internalField {internal};",
            "boundaryField",
            "{",
        ]
        for p in patches:
            preset = BC_PRESETS.get(p.bc_type, BC_PRESETS["wall"])
            field_cfg = preset.get(name, {"type": "zeroGradient"})
            lines.append(f"    {p.name}")
            lines.append("    {")
            for key, val in field_cfg.items():
                lines.append(f"        {key} {val};")
            lines.append("    }")
        lines.append("}")
        return "\n".join(lines) + "\n"
