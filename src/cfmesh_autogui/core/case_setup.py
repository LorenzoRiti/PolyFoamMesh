from __future__ import annotations

import re
import struct
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
wallDist
{
    // kOmegaSST (and other RAS models measuring y+) requires this entry
    // in OpenFOAM 2512 - without it simpleFoam aborts at startup:
    // "Entry 'method' not found in dictionary system/fvSchemes/wallDist".
    method          meshWave;
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


def _patch_role(patch_name: str) -> str:
    """Classify a patch as inlet / outlet / wall / other by name."""
    n = patch_name.lower()
    if "inlet" in n:
        return "inlet"
    if "outlet" in n:
        return "outlet"
    if "wall" in n:
        return "wall"
    return "other"


class _PolyMeshGeometry:
    """Thin geometric view over an OpenFOAM constant/polyMesh (ASCII or
    binary, possibly gzipped — gmshToFoam writes binary by default).

    Parses points/faces at import so per-patch centroid and outward-normal
    estimates can be computed without PyVista (which would need an extra
    foamToVTK pass) or hand-rolled VTU parsing. See ``infer_patch_roles``.
    """

    def __init__(self, case_dir: Path | str) -> None:
        from cfmesh_autogui.core.boundary_reader import parse_boundary

        poly = Path(case_dir) / "constant" / "polyMesh"
        self.points = self._read_points(poly / "points")
        self.faces = self._read_faces(poly / "faces")
        self.boundary = parse_boundary(poly / "boundary")

    @staticmethod
    def _strip_header(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"//[^\n]*", "", text)
        header_end = text.find("}")
        return text[header_end + 1:] if header_end != -1 else text

    @staticmethod
    def _header_size(raw: bytes) -> int:
        """Byte offset just past the FoamFile header block."""
        text = raw.decode("ascii", errors="replace")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        header_end = text.find("}")
        return header_end + 1 if header_end != -1 else 0

    def _read_bytes(self, path: Path) -> tuple[bytes, bool]:
        """(raw bytes, is_binary) — transparently decompresses gzip."""
        from cfmesh_autogui.core.of_reader import _is_binary_format, _read_of_bytes
        return _read_of_bytes(path), _is_binary_format(path)

    def _read_points(self, path: Path) -> list[list[float]]:
        raw, is_binary = self._read_bytes(path)
        if not raw:
            return []
        if is_binary:
            start = self._header_size(raw)
            m = re.search(rb"(\d+)\s*\(", raw[start:])
            if not m:
                return []
            count = int(m.group(1))
            data = raw[start + m.end():start + m.end() + count * 3 * 8]
            vals = struct.unpack(f"<{count * 3}d", data[:count * 3 * 8])
            return [list(vals[i * 3:(i + 1) * 3]) for i in range(count)]
        text = self._strip_header(raw.decode("ascii", errors="replace"))
        body = text[text.find("(") + 1:text.rfind(")")]
        out: list[list[float]] = []
        for tok in re.findall(r"\(([^()\n]*)\)", body):
            parts = [float(x) for x in tok.split()]
            if len(parts) == 3:
                out.append(parts)
        return out

    def _read_faces(self, path: Path) -> list[list[int]]:
        raw, is_binary = self._read_bytes(path)
        if not raw:
            return []
        if is_binary:
            # OpenFOAM 2512 binary faces come in two layouts:
            #  - new compact: `count ( offsets[count] ) nverts ( verts )`
            #    where offsets[i] is the START of face i's vertices in the
            #    shared vertex pool (GMSH-to-Foam writes this).
            #  - legacy per-face: `count ( nPts(v0..) nPts(v0..) ... )`.
            # Detect by looking for the `)\n<n>\n(` right after `count`
            # int32s of cumulative offsets.
            start = self._header_size(raw)
            m = re.search(rb"(\d+)\s*\(", raw[start:])
            if not m:
                return []
            count = int(m.group(1))
            data = raw[start + m.end():]
            if len(data) >= (count + 1) * 4:
                second = re.match(rb"\)\s*(\d+)\s*\(", data[count * 4:])
                if second:
                    nverts = int(second.group(1))
                    pool_pos = count * 4 + second.end()
                    offsets = struct.unpack_from(f"<{count}i", data, 0)
                    pool = struct.unpack_from(f"<{nverts}i", data, pool_pos)
                    return [
                        list(pool[offsets[i]:offsets[i + 1]
                             if i + 1 < count else nverts])
                        for i in range(count)
                    ]
            # Legacy per-face stream.
            out: list[list[int]] = []
            pos = 0
            for _ in range(count):
                if pos + 4 > len(data):
                    break
                np_ = struct.unpack_from("<i", data, pos)[0]
                if not (0 <= np_ <= 1000):
                    break
                pos += 4
                verts = struct.unpack_from(f"<{np_}i", data, pos)
                pos += np_ * 4
                out.append(list(verts))
            return out
        text = self._strip_header(raw.decode("ascii", errors="replace"))
        body = text[text.find("(") + 1:text.rfind(")")]
        out = []
        for tok in re.findall(r"(\d+)\(([^()]*)\)", body):
            out.append([int(x) for x in tok[1].split()])
        return out

    def patch_centroids_normals(self) -> dict[str, tuple[list[float], list[float]]]:
        """Mean centroid and mean outward normal per patch."""
        result: dict[str, tuple[list[float], list[float]]] = {}
        for p in self.boundary:
            start = p.start_face
            end = start + p.n_faces
            if start < 0 or end > len(self.faces) or self.faces[start:end] == []:
                result[p.name] = ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
                continue
            c_accum = [0.0, 0.0, 0.0]
            n_accum = [0.0, 0.0, 0.0]
            for face in self.faces[start:end]:
                verts = [self.points[v] for v in face if 0 <= v < len(self.points)]
                if len(verts) < 3:
                    continue
                for v in verts:
                    for k in range(3):
                        c_accum[k] += v[k]
                # Newell's method -> area-weighted normal, sign follows the
                # boundary winding (outward, by OpenFOAM's convention).
                nx = ny = nz = 0.0
                m = len(verts)
                for i in range(m):
                    v0, v1 = verts[i], verts[(i + 1) % m]
                    nx += (v0[1] - v1[1]) * (v0[2] + v1[2])
                    ny += (v0[2] - v1[2]) * (v0[0] + v1[0])
                    nz += (v0[0] - v1[0]) * (v0[1] + v1[1])
                n_accum[0] += nx
                n_accum[1] += ny
                n_accum[2] += nz
            nf = len(self.faces[start:end])
            if nf:
                for k in range(3):
                    c_accum[k] /= nf
            norm = (n_accum[0] ** 2 + n_accum[1] ** 2 + n_accum[2] ** 2) ** 0.5
            if norm > 1e-30:
                for k in range(3):
                    n_accum[k] /= norm
            result[p.name] = (c_accum, n_accum)
        return result


def infer_patch_roles(
    case_dir: Path | str,
    patches: list[PatchInfo],
    flow_direction: tuple[float, float, float] = (1.0, 0.0, 0.0),
) -> dict[str, str]:
    """Assign inlet/outlet/wall roles from mesh geometry when patch names
    carry none (GMSH physical surfaces are ``surface_0`` … ``surface_N``).

    The inlet and outlet are the patches sitting at the two extremes of the
    dominant flow axis whose outward normals are most aligned with that axis;
    the one whose normal opposes *flow_direction* is the inlet (flow enters
    where the wall normal points back at the oncoming stream). Everything
    else is a wall. Patches whose name already encodes a role are respected.

    Returns a ``{patch_name: role}`` map covering every boundary patch.
    """
    roles: dict[str, str] = {}
    for p in patches:
        r = _patch_role(p.name)
        if r != "other":
            roles[p.name] = r

    missing = [p for p in patches if p.name not in roles]
    if not missing:
        return roles

    geo = _PolyMeshGeometry(case_dir)
    info = geo.patch_centroids_normals()

    # Domain centre = mean over all boundary-face centroids.
    centre = [0.0, 0.0, 0.0]
    counts = 0
    for name, (c, _n) in info.items():
        for k in range(3):
            centre[k] += c[k]
        counts += 1
    if counts:
        for k in range(3):
            centre[k] /= counts

    # Dominant axis = the flow_direction axis if it is reasonably aligned
    # with the largest bounding extent, else the largest-extent axis.
    bounds: list[list[float]] = [[1e300, -1e300] for _ in range(3)]
    for name, (c, _n) in info.items():
        for k in range(3):
            bounds[k][0] = min(bounds[k][0], c[k])
            bounds[k][1] = max(bounds[k][1], c[k])
    ext = [b[1] - b[0] for b in bounds]
    axis = int(max(range(3), key=lambda k: ext[k]))
    fd = list(flow_direction)
    fdn = sum(x * x for x in fd) ** 0.5
    flow_unit = [x / fdn for x in fd] if fdn > 1e-30 else [1.0, 0.0, 0.0]
    if abs(flow_unit[axis]) >= 0.5:
        axis_unit = flow_unit
    else:
        axis_unit = [0.0, 0.0, 0.0]
        axis_unit[axis] = 1.0 if bounds[axis][1] - centre[axis] >= 0 else -1.0

    half_ext = ext[axis] * 0.25
    low_side: list[tuple[str, float]] = []
    high_side: list[tuple[str, float]] = []
    for name, (c, n) in info.items():
        along = sum(axis_unit[k] * (c[k] - centre[k]) for k in range(3))
        alignment = abs(sum(axis_unit[k] * n[k] for k in range(3)))
        if along < -half_ext:
            low_side.append((name, alignment))
        elif along > half_ext:
            high_side.append((name, alignment))

    def _pick(cands: list[tuple[str, float]]) -> str | None:
        if not cands:
            return None
        cands.sort(key=lambda t: -t[1])
        return cands[0][0]

    low = _pick(low_side)
    high = _pick(high_side)
    if low is None or high is None:
        raise RuntimeError(
            "Could not identify inlet/outlet patches geometrically: patches "
            "at the flow-axis extremes not found. Rename patches containing "
            "'inlet'/'outlet' or check the mesh boundary. Patch centroids: "
            + ", ".join(sorted(info))
        )

    # inlet = extreme patch whose outward normal opposes the flow.
    low_n = info[low][1]
    low_opposes = sum(flow_unit[k] * low_n[k] for k in range(3)) < 0
    if low_opposes:
        roles[low] = "inlet"
        roles[high] = "outlet"
    else:
        roles[high] = "inlet"
        roles[low] = "outlet"

    for p in missing:
        if p.name not in roles:
            roles[p.name] = "wall"
    return roles


def mesh_bounds(case_dir: Path | str) -> tuple[float, float, float, float, float, float]:
    """(xmin, xmax, ymin, ymax, zmin, zmax) of the polyMesh points (metres)."""
    geo = _PolyMeshGeometry(case_dir)
    pts = geo.points
    if not pts:
        raise RuntimeError(f"No points in {case_dir}/constant/polyMesh")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def suggest_flow_direction(case_dir: Path | str) -> tuple[float, float, float]:
    """Unit vector along the dominant (largest-extent) mesh axis — the
    obvious default flow direction for an internal-flow passage."""
    bounds = mesh_bounds(case_dir)
    ext = (bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
    axis = int(max(range(3), key=lambda k: ext[k]))
    u = [0.0, 0.0, 0.0]
    if ext[axis] > 0:
        u[axis] = 1.0
    return (u[0], u[1], u[2])


def _boundary_block(
    patches: list[PatchInfo],
    field_name: str,
    overrides: dict[str, list[str]] | None = None,
    patch_roles: dict[str, str] | None = None,
) -> str:
    """Build the boundaryField block for *field_name*.

    *overrides* maps a patch role ("inlet"/"outlet"/"wall"/"other") to the
    literal BC lines to write for it, replacing the defaults below. Callers
    that intend to actually RUN the case must supply them: the defaults are
    placeholders for a case the user is expected to edit first (notably a zero
    inlet velocity, which solves to a dead, motionless flow), and they omit the
    wall functions kOmegaSST requires.

    *patch_roles* overrides the name-based role classification per patch,
    e.g. from ``infer_patch_roles`` when GMSH named everything ``surface_N``.
    """
    overrides = overrides or {}
    patch_roles = patch_roles or {}
    lines: list[str] = []
    for p in patches:
        role = patch_roles.get(p.name, _patch_role(p.name))
        if role in overrides:
            bc_lines = list(overrides[role])
        elif role == "inlet":
            if field_name == "U":
                bc_lines = ["type            fixedValue;", "value           uniform (0 0 0);"]
            else:
                bc_lines = ["type            zeroGradient;"]
        elif role == "outlet":
            if field_name == "U":
                bc_lines = ["type            zeroGradient;"]
            else:
                bc_lines = ["type            fixedValue;", "value           uniform 0;"]
        elif role == "wall":
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
    overrides: dict[str, list[str]] | None = None,
    patch_roles: dict[str, str] | None = None,
):
    td = case_dir / time_dir
    td.mkdir(parents=True, exist_ok=True)
    header = _field_header(filename, dims, internal, field_class)
    bb = _boundary_block(patches, filename, overrides, patch_roles)
    (td / filename).write_text(header + "\n" + bb + "\n" + FIELD_FOOTER, encoding="ascii")


def _residual_control_block(tol: float) -> str:
    """SIMPLE residualControl entries, so the solver stops the moment it has
    converged instead of grinding out every remaining endTime iteration."""
    return (
        "    residualControl\n"
        "    {\n"
        f"        p               {tol:g};\n"
        f"        U               {tol:g};\n"
        f"        \"(k|omega)\"     {tol:g};\n"
        "    }\n"
    )


def set_wall_patch_types(case_dir: Path | str, wall_names: list[str],
                         boundary_type: str = "wall") -> None:
    """Rewrite ``constant/polyMesh/boundary`` so the named patches get
    ``type wall`` instead of the generic ``patch`` type gmshToFoam writes.

    Wall-function boundary conditions (``nutkWallFunction``,
    ``kqRWallFunction``, ``omegaWallFunction``) abort at construction time
    from simpleFoam startup if their patch is typed ``patch`` — the
    wall-function code requires the mesh boundary to say ``wall``. Seen
    live on the venturi first run: exit 134 in
    ``nutkWallFunctionFvPatchScalarField``'s constructor.
    """
    path = Path(case_dir) / "constant" / "polyMesh" / "boundary"
    text = path.read_text(encoding="ascii", errors="replace")
    for name in wall_names:
        text = re.sub(
            rf"({re.escape(name)}\s*\{{[^}}]*?type\s+(?:wall|patch)\s*;)",
            lambda m: re.sub(r"\bpatch\s*;\s*$", f"{boundary_type};", m.group(1)),
            text,
        )
    path.write_text(text, encoding="ascii")


def setup_case(
    case_dir: Path | str,
    patches: list[PatchInfo],
    application: str = "simpleFoam",
    turbulence_model: str = "kOmegaSST",  # ✅ F-024
    inlet_velocity: tuple[float, float, float] | None = None,
    end_time: int | None = None,
    residual_control: float | None = None,
    write_interval: int | None = None,
    patch_roles: dict[str, str] | None = None,
):
    """Write a runnable OpenFOAM case into *case_dir*.

    Args:
        inlet_velocity: velocity vector imposed on every patch whose name
            contains "inlet". Leave as None for the historical placeholder
            (zero velocity — a case that is set up but not meant to be run).
        end_time: SIMPLE iteration ceiling (controlDict endTime).
        residual_control: if given, adds a SIMPLE residualControl block so the
            run stops on convergence rather than always running to end_time.
        write_interval: how often to write a time directory. Solution-adaptive
            refinement only ever reads the last one, so it passes a large value
            to avoid writing gigabytes of intermediate fields on a big mesh.
        patch_roles: per-patch role overrides (see ``_boundary_block``). When
            meshing with GMSH the patches are named ``surface_N`` and the
            name-based roles are useless — pass ``infer_patch_roles()`` output.
    """
    case_dir = Path(case_dir).resolve()

    # system/controlDict
    ctrl = CONTROL_DICT.replace("application     simpleFoam;", f"application     {application};")
    if end_time is not None:
        ctrl = re.sub(r"^endTime\s+\d+;", f"endTime         {int(end_time)};",
                      ctrl, flags=re.MULTILINE)
    if write_interval is not None:
        wi = int(write_interval)
        # writeControl is timeStep, so a writeInterval LARGER than endTime means
        # the run never reaches a write step and finishes having written no time
        # directory at all — no error, no output, just an empty case. Confirmed
        # live: endTime 300 with writeInterval 1000 ran all 300 iterations in
        # 38s and produced nothing, so the adaptive loop's next step failed with
        # "the solver produced no solution to compute an indicator from".
        # (A converged run escapes this because residualControl triggers
        # writeAndEnd(), which writes regardless — so the bug only shows up
        # when the iteration limit is hit, which is the normal case for the
        # deliberately-short intermediate solves.)
        # Clamping to endTime keeps the "don't write gigabytes of intermediate
        # fields" intent while guaranteeing the final state lands on disk.
        if end_time is not None and wi > int(end_time):
            wi = int(end_time)
        ctrl = re.sub(r"^writeInterval\s+\d+;",
                      f"writeInterval   {wi};",
                      ctrl, flags=re.MULTILINE)
    _write_system_file(case_dir, "controlDict", ctrl)

    # system/fvSchemes
    _write_system_file(case_dir, "fvSchemes", FV_SCHEMES)

    # system/fvSolution
    fv_solution = FV_SOLUTION
    if residual_control is not None:
        fv_solution = fv_solution.replace(
            "    consistent      yes;\n",
            "    consistent      yes;\n" + _residual_control_block(residual_control),
        )
    _write_system_file(case_dir, "fvSolution", fv_solution)

    runnable = inlet_velocity is not None
    turbulent = turbulence_model.strip().lower() != "laminar"

    # 0/p
    _write_field_file(case_dir, "0", "p", "[0 2 -2 0 0 0 0]", "uniform 0", patches,
                      patch_roles=patch_roles)

    # 0/U (vector field — must be declared volVectorField, not volScalarField,
    # or any strict reader — solvers, foamToVTK, BaramFlow — rejects it)
    u_overrides = None
    if runnable:
        ux, uy, uz = inlet_velocity
        u_overrides = {
            "inlet": ["type            fixedValue;",
                      f"value           uniform ({ux:g} {uy:g} {uz:g});"],
            # inletOutlet, not plain zeroGradient: a constriction can drive
            # transient recirculation back through the outlet plane during the
            # SIMPLE march, and zeroGradient there lets that reverse flow
            # convect unbounded turbulence in and diverge the run.
            "outlet": ["type            inletOutlet;",
                       "inletValue      uniform (0 0 0);",
                       "value           uniform (0 0 0);"],
        }
    _write_field_file(
        case_dir, "0", "U", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)", patches,
        field_class="volVectorField", overrides=u_overrides,
        patch_roles=patch_roles,
    )

    # 0/k, 0/omega, 0/nut. Seeded from standard turbulence estimates (5%
    # intensity, mixing length 10% of a unit scale) rather than fixed
    # placeholders: kOmegaSST started from values orders of magnitude off the
    # flow's actual scale converges far more slowly and can stall outright.
    #
    # nut is written unconditionally for a RAS model — simpleFoam refuses to
    # start without it ("cannot find file nut"), so the previous template,
    # which wrote k and omega but no nut and gave walls plain zeroGradient
    # instead of wall functions, produced a case that could be set up but
    # never actually run.
    k_internal, omega_internal = "uniform 0.01", "uniform 10.0"
    k_over = omega_over = None
    if runnable:
        u_mag = max((ux ** 2 + uy ** 2 + uz ** 2) ** 0.5, 1e-9)
        k_val = 1.5 * (u_mag * 0.05) ** 2
        omega_val = (k_val ** 0.5) / (0.09 ** 0.25 * 0.1)
        k_internal = f"uniform {k_val:g}"
        omega_internal = f"uniform {omega_val:g}"
        k_over = {
            "inlet": ["type            fixedValue;", f"value           uniform {k_val:g};"],
            "outlet": ["type            inletOutlet;",
                       f"inletValue      uniform {k_val:g};",
                       f"value           uniform {k_val:g};"],
            "wall": ["type            kqRWallFunction;", f"value           uniform {k_val:g};"],
        }
        omega_over = {
            "inlet": ["type            fixedValue;", f"value           uniform {omega_val:g};"],
            "outlet": ["type            inletOutlet;",
                       f"inletValue      uniform {omega_val:g};",
                       f"value           uniform {omega_val:g};"],
            "wall": ["type            omegaWallFunction;", f"value           uniform {omega_val:g};"],
        }

    _write_field_file(case_dir, "0", "k", "[0 2 -2 0 0 0 0]", k_internal, patches,
                      overrides=k_over, patch_roles=patch_roles)
    _write_field_file(case_dir, "0", "omega", "[0 0 -1 0 0 0 0]", omega_internal, patches,
                      overrides=omega_over, patch_roles=patch_roles)
    if turbulent:
        _write_field_file(
            case_dir, "0", "nut", "[0 2 -1 0 0 0 0]", "uniform 0", patches,
            overrides={
                "inlet": ["type            calculated;", "value           uniform 0;"],
                "outlet": ["type            calculated;", "value           uniform 0;"],
                "wall": ["type            nutkWallFunction;", "value           uniform 0;"],
                "other": ["type            calculated;", "value           uniform 0;"],
            },
            patch_roles=patch_roles,
        )

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
