from __future__ import annotations

from pathlib import Path

from cfmesh_autogui.core.boundary_reader import PatchInfo


CONTROL_DICT = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      controlDict;
}
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1000;
deltaT          1;
writeControl    timeStep;
writeInterval   100;
purgeWrite      0;
writeFormat     binary;
writePrecision  6;
writeCompression on;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
"""

FV_SCHEMES = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      fvSchemes;
}
ddtSchemes
{
    default         steadyState;
}
gradSchemes
{
    default         Gauss linear;
}
divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,omega)  bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes
{
    default         Gauss linear corrected;
}
interpolationSchemes
{
    default         linear;
}
snGradSchemes
{
    default         corrected;
}
"""

FV_SOLUTION = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      fvSolution;
}
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.01;
        smoother        GaussSeidel;
    }
    U
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.01;
    }
    k
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.01;
    }
    omega
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.01;
    }
}
SIMPLE
{
    nNonOrthogonalCorrectors 2;
    consistent      yes;
}
relaxationFactors
{
    fields
    {
        p               0.3;
    }
    equations
    {
        U               0.7;
        k               0.7;
        omega           0.7;
    }
}
"""


def _field_header(obj_name: str, dims: str, internal: str, field_class: str = "volScalarField") -> str:
    return f"""\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {field_class};
    object      {obj_name};
}}
dimensions      {dims};
internalField   {internal};
boundaryField
{{"""


FIELD_FOOTER = "}\n"


def _boundary_block(patches: list[PatchInfo], field_name: str) -> str:
    lines: list[str] = []
    for p in patches:
        name_lower = p.name.lower()
        if "inlet" in name_lower:
            if field_name == "U":
                bc_lines = ["type            fixedValue;", "value           uniform (0 0 0);"]
            else:
                bc_lines = ["type            zeroGradient;"]
        elif "outlet" in name_lower:
            if field_name == "U":
                bc_lines = ["type            zeroGradient;"]
            else:
                bc_lines = ["type            fixedValue;", "value           uniform 0;"]
        elif "wall" in name_lower:
            if field_name == "U":
                bc_lines = ["type            noSlip;"]
            else:
                bc_lines = ["type            zeroGradient;"]
        else:
            bc_lines = ["type            zeroGradient;"]
        lines.append(f"    {p.name}")
        lines.append("    {")
        for bl in bc_lines:
            lines.append(f"        {bl}")
        lines.append("    }")
    return "\n".join(lines)


def _write_system_file(case_dir: Path, filename: str, content: str):
    system_dir = case_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    (system_dir / filename).write_text(content, encoding="ascii")


def _write_field_file(
    case_dir: Path, time_dir: str, filename: str, dims: str, internal: str,
    patches: list[PatchInfo], field_class: str = "volScalarField",
):
    td = case_dir / time_dir
    td.mkdir(parents=True, exist_ok=True)
    header = _field_header(filename, dims, internal, field_class)
    bb = _boundary_block(patches, filename)
    (td / filename).write_text(header + "\n" + bb + "\n" + FIELD_FOOTER, encoding="ascii")


def setup_case(
    case_dir: Path | str,
    patches: list[PatchInfo],
    application: str = "simpleFoam",
    turbulence_model: str = "kOmegaSST",  # ✅ F-024
):
    case_dir = Path(case_dir).resolve()

    # system/controlDict
    ctrl = CONTROL_DICT.replace("application     simpleFoam;", f"application     {application};")
    _write_system_file(case_dir, "controlDict", ctrl)

    # system/fvSchemes
    _write_system_file(case_dir, "fvSchemes", FV_SCHEMES)

    # system/fvSolution
    _write_system_file(case_dir, "fvSolution", FV_SOLUTION)

    # 0/p
    _write_field_file(case_dir, "0", "p", "[0 2 -2 0 0 0 0]", "uniform 0", patches)

    # 0/U (vector field — must be declared volVectorField, not volScalarField,
    # or any strict reader — solvers, foamToVTK, BaramFlow — rejects it)
    _write_field_file(
        case_dir, "0", "U", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)", patches,
        field_class="volVectorField",
    )

    # 0/k
    _write_field_file(case_dir, "0", "k", "[0 2 -2 0 0 0 0]", "uniform 0.01", patches)

    # 0/omega
    _write_field_file(case_dir, "0", "omega", "[0 -2 0 0 0 0 0]", "uniform 10.0", patches)

    # constant/transportProperties
    transport_dir = case_dir / "constant"
    transport_dir.mkdir(parents=True, exist_ok=True)
    (transport_dir / "transportProperties").write_text(
        "FoamFile\n"
        "{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        "    class       dictionary;\n"
        "    object      transportProperties;\n"
        "}\n"
        "transportModel  Newtonian;\n"
        "nu              nu [0 2 -1 0 0 0 0] 1e-05;\n"
        "crossPowerLawCoeffs { nu0 1e-05; m 0; n 1; }\n"
        "BirdCarreauCoeffs { nu0 1e-05; nuInf 0; k 0; n 1; }\n"
        "ns 1e-05;\n",
        encoding="ascii",
    )

    # ✅ F-024: turbulence model parametrizzabile
    header = (
        "FoamFile\n"
        "{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        "    class       dictionary;\n"
        "    object      turbulenceProperties;\n"
        "}\n"
    )
    if turbulence_model.strip().lower() == "laminar":
        # "laminar" is not a registered RASModel (verified against OpenFOAM's
        # TurbulenceModels library — only kEpsilon/kOmegaSST/SpalartAllmaras/...
        # exist there) — `RAS { RASModel laminar; }` fails at solver startup
        # with "Unknown RASModel type". No-turbulence case files must set
        # simulationType directly, with no RAS sub-dictionary at all.
        turb_text = header + "simulationType  laminar;\n"
    else:
        turb_text = header + (
            "simulationType  RAS;\n"
            "RAS\n"
            "{\n"
            f"    RASModel        {turbulence_model};\n"
            "    turbulence      on;\n"
            "    printCoeffs     on;\n"
            "}\n"
        )
    (transport_dir / "turbulenceProperties").write_text(turb_text, encoding="ascii")
