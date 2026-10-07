"""Recover the real boundary-layer first layer from a SOLVED OpenFOAM case.

The physics first layer comes from a flat-plate correlation, which can be
off 2-4x on real flows.  After a solve the case contains the REAL wall data
(``yPlus`` or, failing that, ``wallShearStress``), so the exact first-layer
height for a target y+ can be recovered without any WSL/``foamToVTK`` pass:
this module reads the OpenFOAM field and polyMesh files directly in Python.

Design notes
------------
* Wall patches are found with :func:`polyfoammesh.core.case_setup.infer_patch_roles`
  (geometric inference for ``surface_N`` meshes) — semantic names are never
  assumed.
* Every statistic is AREA-weighted over the wall faces (computed from the
  polyMesh points/faces via Newell's method), because a few small faces on a
  fillet must not outweigh a large flat wall.
* ``proposed_first_layer`` scales the first layer linearly: for the same flow,
  y+ is proportional to the first-cell height, so
  ``h1_new = h1_used * target_yplus / mean_yplus``.
* Nothing here ever raises: a failed calibration returns a result with
  ``error`` set.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from polyfoammesh.core.boundary_reader import PatchInfo, parse_boundary
from polyfoammesh.core.case_setup import infer_patch_roles
from polyfoammesh.core.foam_mesh_io import read_polymesh
from polyfoammesh.core.of_reader import _read_of_bytes

logger = logging.getLogger(__name__)


#: Fluid densities (kg/m^3) used to turn wallShearStress into u_tau.
FLUID_RHO: dict[str, float] = {"water": 998.0, "air": 1.2}
#: Kinematic viscosities (m^2/s), for converting u_tau <-> y+ when needed.
FLUID_NU: dict[str, float] = {"water": 1.0e-6, "air": 1.5e-5}

#: A single first layer cannot hit the same y+ everywhere when the wall y+
#: varies by more than this factor.
SINGLE_H1_SPREAD_LIMIT = 3.0


@dataclass
class PatchStat:
    """Area-weighted y+ statistics for one wall patch."""

    name: str
    n_faces: int
    area: float
    mean_yplus: float
    p10: float
    p90: float


@dataclass
class CalibrationResult:
    """Outcome of :func:`calibrate_from_case` (``error`` empty == success)."""

    case_dir: str = ""
    time_name: str = ""
    field: str = ""  # "yPlus" | "wallShearStress"
    patches: list[PatchStat] = dataclasses.field(default_factory=list)
    mean_yplus: float = 0.0
    p10: float = 0.0
    p90: float = 0.0
    u_tau: float = 0.0
    spread_ratio: float = 0.0
    single_h1_reaches_all: bool = True
    bbox: float = 0.0
    rho: float = 0.0
    error: str = ""
    # Informational notes (assumptions taken, fallbacks used).  Shown in the
    # calibration dialog; never an error.
    notes: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

def _next_token(raw: bytes, pos: int):
    """Read one OpenFOAM token from *raw* at *pos* (bytes-aware).

    Returns ``(token, new_pos)`` where a structural char (``{}();``) is
    returned as a one-character string and ``None`` marks end-of-file.
    Comments (``//`` and ``/* */``) are skipped.
    """
    n = len(raw)
    while pos < n:
        c = raw[pos]
        if c in b" \t\r\n":
            pos += 1
            continue
        if c == 0x2F and pos + 1 < n and raw[pos + 1] == 0x2F:  # //
            nl = raw.find(b"\n", pos)
            pos = n if nl < 0 else nl + 1
            continue
        if c == 0x2F and pos + 1 < n and raw[pos + 1] == 0x2A:  # /*
            end = raw.find(b"*/", pos)
            pos = n if end < 0 else end + 2
            continue
        break
    if pos >= n:
        return None, pos
    c = raw[pos]
    if c in b"{}();":
        return chr(c), pos + 1
    start = pos
    while pos < n and raw[pos] not in b" \t\r\n{}();":
        pos += 1
    return raw[start:pos].decode("latin-1"), pos


def _parse_fields_binary(raw: bytes) -> dict[str, tuple[str, object]]:
    """Parse ``boundaryField`` out of a binary-format OpenFOAM field.

    Only the ASCII dictionary skeleton is tokenised; the payload of every
    ``nonuniform`` list is read as raw little-endian doubles (``scalar=64``,
    as OpenFOAM 2412 writes: see the ``arch "LSB;...;scalar=64"`` header).
    """
    idx = raw.find(b"boundaryField")
    if idx < 0:
        raise ValueError("no boundaryField in field file")
    brace = raw.find(b"{", idx)
    if brace < 0:
        raise ValueError("malformed boundaryField")
    pos = brace + 1
    out: dict[str, tuple[str, object]] = {}
    while True:
        name, pos = _next_token(raw, pos)
        if name is None or name == "}":
            break
        if name == ";":
            continue
        opening, pos = _next_token(raw, pos)
        if opening != "{":
            continue
        block: dict[str, tuple[str, object]] = {}
        while True:
            key, pos = _next_token(raw, pos)
            if key is None or key == "}":
                break
            if key in (";", "{", "("):
                continue
            if key != "value":
                continue
            kind, pos = _next_token(raw, pos)
            if kind == "uniform":
                first, pos = _next_token(raw, pos)
                if first == "(":
                    comps = []
                    while True:
                        tok, pos = _next_token(raw, pos)
                        if tok in (None, ")"):
                            break
                        comps.append(float(tok))
                    block["value"] = ("uniform", tuple(comps))
                elif first is not None:
                    block["value"] = ("uniform", float(first))
            elif kind == "nonuniform":
                ltype, pos = _next_token(raw, pos)
                count, pos = _next_token(raw, pos)
                nv = int(count)  # type: ignore[arg-type]
                _open, pos = _next_token(raw, pos)  # '('
                is_vec = "vector" in (ltype or "")
                ncomp = 3 if is_vec else 1
                nbytes = nv * ncomp * 8
                data = raw[pos:pos + nbytes]
                if len(data) < nbytes:
                    raise ValueError("truncated nonuniform list")
                flat = struct.unpack(f"<{nv * ncomp}d", data)
                pos += nbytes
                if is_vec:
                    block["value"] = (
                        "nonuniform",
                        [tuple(flat[i * 3:i * 3 + 3]) for i in range(nv)],
                    )
                else:
                    block["value"] = ("nonuniform", list(flat))
            else:
                # A bare value with no uniform/nonuniform keyword: ignore.
                continue
        if "value" in block:
            out[name.strip('"')] = block["value"]
    return out


def _boundary_block_text(text: str) -> str:
    """Text inside the outermost ``boundaryField { ... }`` braces."""
    m = re.search(r"boundaryField\s*", text)
    if not m:
        raise ValueError("no boundaryField in field file")
    brace = text.find("{", m.end())
    if brace < 0:
        raise ValueError("malformed boundaryField")
    depth = 0
    for j in range(brace, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:j]
    return text[brace + 1:]


def _parse_fields_ascii(text: str) -> dict[str, tuple[str, object]]:
    """Parse ``boundaryField`` out of an ASCII OpenFOAM field."""
    block = _boundary_block_text(text)
    out: dict[str, tuple[str, object]] = {}
    for m in re.finditer(r"(\S+)\s*\{([^{}]*)\}", block, re.DOTALL):
        name = m.group(1).strip('"')
        content = m.group(2)
        vm = re.search(r"\bvalue\s+(.*?);", content, re.DOTALL)
        if not vm:
            continue
        value = vm.group(1).strip()
        if value.startswith("uniform"):
            rest = value[len("uniform"):].strip()
            if rest.startswith("("):
                nums = [float(t) for t in re.findall(r"[-+0-9.eE]+", rest)]
                out[name] = ("uniform", tuple(nums))
            else:
                out[name] = ("uniform", float(rest))
        elif value.startswith("nonuniform"):
            lm = re.search(r"List<\s*(scalar|vector)\s*>\s*(\d+)\s*\(", value)
            if not lm:
                continue
            kind, nv = lm.group(1), int(lm.group(2))
            body = value[lm.end():]
            close = body.rfind(")")
            if close >= 0:
                body = body[:close]
            if kind == "scalar":
                nums = [float(t) for t in re.findall(r"[-+0-9.eE]+", body)]
                out[name] = ("nonuniform", nums[:nv])
            else:
                vecs = []
                for vm2 in re.finditer(r"\(([^()]*)\)", body):
                    comps = [float(t) for t in vm2.group(1).split()]
                    if len(comps) == 3:
                        vecs.append(tuple(comps))
                out[name] = ("nonuniform", vecs[:nv])
    return out


def _read_field_values(path: Path) -> dict[str, tuple[str, object]]:
    """Parse a field file (ASCII or binary, gzip-transparent)."""
    raw = _read_of_bytes(path)
    if not raw:
        raise ValueError(f"empty field file: {path}")
    is_binary = bool(re.search(rb"format\s+binary\s*;", raw[:2000]))
    if is_binary:
        return _parse_fields_binary(raw)
    text = raw.decode("ascii", errors="replace")
    return _parse_fields_ascii(text)


# ---------------------------------------------------------------------------
# Case discovery
# ---------------------------------------------------------------------------

_NUMERIC_TIME_RE = re.compile(r"^\d+(?:\.\d+)?$")


def latest_time_with_field(case_dir: Path | str) -> tuple[str, str, Path]:
    """Newest numeric time directory holding a ``yPlus`` (preferred) or
    ``wallShearStress`` file.

    Returns ``(time_name, field_name, file_path)``.  ``0`` is skipped when a
    later time exists.  Raises ``FileNotFoundError`` when none is found.
    """
    case = Path(case_dir)
    times: list[tuple[float, str, Path]] = []
    for child in case.iterdir():
        if not child.is_dir() or not _NUMERIC_TIME_RE.match(child.name):
            continue
        times.append((float(child.name), child.name, child))
    if not times:
        raise FileNotFoundError(f"no numeric time directories in {case}")
    times.sort(key=lambda t: t[0], reverse=True)
    nonzero = [t for t in times if t[0] != 0.0]
    ordered = nonzero or times

    for _num, name, path in ordered:
        for fname in ("yPlus", "wallShearStress"):
            for candidate in (path / fname, path / (fname + ".gz")):
                if candidate.exists() and candidate.is_file():
                    return name, fname, candidate
    raise FileNotFoundError(
        f"no yPlus or wallShearStress in any time directory of {case}"
    )


# ---------------------------------------------------------------------------
# Geometry / statistics
# ---------------------------------------------------------------------------

def _newell_area(points: np.ndarray, face: list[int]) -> float:
    """Area of one polygonal face via Newell's method."""
    if len(face) < 3:
        return 0.0
    idx = [v for v in face if 0 <= v < len(points)]
    if len(idx) < 3:
        return 0.0
    verts = points[idx]
    nxt = np.roll(verts, -1, axis=0)
    nx = float(np.sum((verts[:, 1] - nxt[:, 1]) * (verts[:, 2] + nxt[:, 2])))
    ny = float(np.sum((verts[:, 2] - nxt[:, 2]) * (verts[:, 0] + nxt[:, 0])))
    nz = float(np.sum((verts[:, 0] - nxt[:, 0]) * (verts[:, 1] + nxt[:, 1])))
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    total = float(weights.sum())
    if total <= 0:
        return float(values.mean()) if len(values) else 0.0
    return float((values * weights).sum() / total)


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    if len(values) == 0:
        return 0.0
    total = float(weights.sum())
    if total <= 0:
        return float(np.percentile(values, q * 100.0))
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    idx = int(np.searchsorted(cum, q * total))
    idx = min(max(idx, 0), len(v) - 1)
    return float(v[idx])


def _resolve_fluid(fluid: str, rho: float | None, nu: float | None):
    key = (fluid or "").strip().lower()
    if "water" in key:
        default_rho, default_nu = FLUID_RHO["water"], FLUID_NU["water"]
    else:
        default_rho, default_nu = FLUID_RHO["air"], FLUID_NU["air"]
    return (
        float(rho) if rho is not None else default_rho,
        float(nu) if nu is not None else default_nu,
    )


def _failed(case_dir, error: str, rho: float = 0.0) -> CalibrationResult:
    return CalibrationResult(case_dir=str(case_dir), rho=rho, error=error)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _roles_from_types(patch_infos: list[PatchInfo]) -> dict[str, str]:
    """Role map when geometric inference is unavailable.

    Closed domains and external-aerodynamics geometries have no inlet/outlet
    extremes, so :func:`infer_patch_roles` cannot place caps there.  The
    calibration must still work on those: patches that are explicitly
    non-wall by boundary TYPE (symmetry/symmetryPlane/wedge/empty) or by a
    NAME the project classifier recognises (inlet/outlet/symmetry) keep
    their role; everything else is a wall - the only safe default.
    """
    from polyfoammesh.core.patch_roles import match_role

    non_wall_types = {"symmetry", "symmetryplane", "wedge", "empty"}
    roles: dict[str, str] = {}
    for p in patch_infos:
        role = match_role(p.name)
        if role in ("inlet", "outlet", "symmetry"):
            roles[p.name] = role
        elif str(getattr(p, "patch_type", "")).lower() in non_wall_types:
            roles[p.name] = "symmetry"
        else:
            roles[p.name] = "wall"
    return roles


def calibrate_from_case(
    case_dir: Path | str,
    *,
    h1_used: float | None = None,
    nu: float | None = None,
    rho: float | None = None,
    fluid: str = "air",
    kinematic_shear: bool = True,
) -> CalibrationResult:
    """Recover area-weighted wall y+ from a solved case and propose a new h1.

    *h1_used* is the first-layer height (m) the current solve used and *nu*
    the fluid kinematic viscosity; both are needed to turn ``wallShearStress``
    into y+ (and to report the implied u_tau on the ``yPlus`` path).  *rho* /
    *fluid* select the density for ``wallShearStress``.

    *kinematic_shear*: OpenFOAM's INCOMPRESSIBLE solvers write
    ``wallShearStress`` as tau_w/rho (kinematic, m^2/s^2), so the default
    ``True`` computes ``u_tau = sqrt(|tau_w|)`` directly.  Set ``False``
    for the (rare) dynamic convention where the field is tau_w in Pa and
    ``u_tau = sqrt(|tau_w|/rho)``.

    Never raises: on any failure the result carries a non-empty ``error``.
    """
    rho_val, nu_val = _resolve_fluid(fluid, rho, nu)
    try:
        case = Path(case_dir)
        if not case.exists():
            return _failed(case_dir, f"case directory not found: {case}", rho_val)

        time_name, field_name, field_path = latest_time_with_field(case)
        values = _read_field_values(field_path)
        if not values:
            return _failed(case_dir, f"{field_name} has no boundary values", rho_val)

        # Boundary patches + geometric roles (never semantic-name assumptions).
        patch_infos: list[PatchInfo] = parse_boundary(
            case / "constant" / "polyMesh" / "boundary"
        )
        notes: list[str] = []
        try:
            roles = infer_patch_roles(case, patch_infos)
        except Exception as exc:  # noqa: BLE001 - closed/external geometries
            logger.info(
                "calibration: geometric role inference unavailable (%s); "
                "falling back to type/name roles", exc,
            )
            notes.append(
                "No inlet/outlet could be inferred (closed or external "
                "geometry): every patch that is not explicitly typed or "
                "named inlet/outlet/symmetry is treated as a wall."
            )
            roles = _roles_from_types(patch_infos)
        wall_names = [p.name for p in patch_infos if roles.get(p.name) == "wall"]
        if not wall_names:
            return _failed(case_dir, "no wall patches identified", rho_val)
        if field_name == "wallShearStress":
            notes.append(
                "wallShearStress assumed KINEMATIC (tau_w/rho, the "
                "incompressible convention): u_tau = sqrt(|tau_w|). "
                "Pass kinematic_shear=False for a dynamic field."
                if kinematic_shear else
                "wallShearStress treated as DYNAMIC (Pa): "
                "u_tau = sqrt(|tau_w|/rho)."
            )

        points, faces, _owner, _neighbour, _raw_patches = read_polymesh(
            case / "constant" / "polyMesh"
        )
        bbox = 0.0
        if len(points):
            ext = points.max(axis=0) - points.min(axis=0)
            bbox = float(max(ext))

        all_vals: list[float] = []
        all_areas: list[float] = []
        stats: list[PatchStat] = []
        for info in patch_infos:
            if info.name not in wall_names:
                continue
            entry = values.get(info.name)
            if entry is None:
                continue
            kind, payload = entry
            start = info.start_face
            end = start + info.n_faces
            patch_faces = faces[start:end] if start >= 0 else []
            areas = np.array(
                [_newell_area(points, f) for f in patch_faces], dtype=float
            )
            if len(areas) == 0:
                area_total = 0.0
            else:
                area_total = float(areas.sum())

            face_vals = _values_for_patch(kind, payload, info.n_faces)
            if face_vals is None or len(face_vals) == 0:
                continue
            vals = _to_yplus(
                field_name, face_vals, rho_val, nu_val, h1_used,
                kinematic_shear=kinematic_shear,
            )
            if vals is None:
                return _failed(
                    case_dir,
                    "wallShearStress calibration needs h1_used and nu",
                    rho_val,
                )
            weights = _match_weights(areas, len(vals), area_total)
            v = np.asarray(vals, dtype=float)
            w = np.asarray(weights, dtype=float)
            stats.append(
                PatchStat(
                    name=info.name,
                    n_faces=len(v),
                    area=float(w.sum()),
                    mean_yplus=_weighted_mean(v, w),
                    p10=_weighted_percentile(v, w, 0.10),
                    p90=_weighted_percentile(v, w, 0.90),
                )
            )
            all_vals.extend(v.tolist())
            all_areas.extend(w.tolist())

        if not stats:
            return _failed(case_dir, "no wall values parsed", rho_val)

        v_all = np.asarray(all_vals, dtype=float)
        w_all = np.asarray(all_areas, dtype=float)
        mean = _weighted_mean(v_all, w_all)
        p10 = _weighted_percentile(v_all, w_all, 0.10)
        p90 = _weighted_percentile(v_all, w_all, 0.90)
        if p10 > 0:
            spread = p90 / p10
        elif p90 > 0:
            spread = float("inf")
        else:
            spread = 1.0

        # Implied u_tau: on the yPlus path it is inferred from h1/nu; on the
        # wallShearStress path it comes straight from tau_w.
        if field_name == "yPlus" and h1_used and nu_val and h1_used > 0:
            u_tau = mean * nu_val / h1_used
        elif field_name == "wallShearStress":
            u_tau = _wall_shear_utau_mean(
                values, wall_names, patch_infos, rho_val,
                kinematic_shear=kinematic_shear,
            )
        else:
            u_tau = 0.0

        return CalibrationResult(
            case_dir=str(case),
            time_name=time_name,
            field=field_name,
            patches=stats,
            mean_yplus=mean,
            p10=p10,
            p90=p90,
            u_tau=u_tau,
            spread_ratio=spread,
            single_h1_reaches_all=spread <= SINGLE_H1_SPREAD_LIMIT,
            bbox=bbox,
            rho=rho_val,
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 - calibration must never raise
        logger.debug("calibration failed for %s", case_dir, exc_info=True)
        return _failed(case_dir, f"{type(exc).__name__}: {exc}", rho_val)


def _values_for_patch(kind: str, payload: object, n_faces: int):
    """Normalise a parsed patch value to a per-face list (uniform -> repeat)."""
    if kind == "nonuniform":
        return list(payload) if isinstance(payload, list) else None
    if kind == "uniform":
        if isinstance(payload, tuple):
            return [payload] * max(n_faces, 1)
        return [float(payload)] * max(n_faces, 1)  # type: ignore[arg-type]
    return None


def _to_yplus(field_name, face_vals, rho_val, nu_val, h1_used,
              kinematic_shear: bool = True):
    """Per-face y+ values (list) or None when the inputs are insufficient."""
    if field_name == "yPlus":
        try:
            return [float(v) for v in face_vals]
        except (TypeError, ValueError):
            return None
    # wallShearStress: |tau_w| -> u_tau -> y+.
    if not h1_used or not nu_val or h1_used <= 0:
        return None
    out = []
    for tau in face_vals:
        if isinstance(tau, (tuple, list)):
            try:
                tx, ty, tz = (float(c) for c in tau)
            except (TypeError, ValueError):
                return None
        else:
            # A uniform scalar wallShearStress is meaningless as a vector;
            # treat it as a 1-component magnitude.
            tx, ty, tz = float(tau), 0.0, 0.0
        mag = math.sqrt(tx * tx + ty * ty + tz * tz)
        if kinematic_shear:
            # Incompressible convention: the field is tau_w/rho.
            u_tau = math.sqrt(mag)
        else:
            u_tau = math.sqrt(mag / rho_val) if rho_val > 0 else 0.0
        out.append(u_tau * h1_used / nu_val)
    return out


def _wall_shear_utau_mean(values, wall_names, patch_infos, rho_val,
                          kinematic_shear: bool = True) -> float:
    """Area-weighted mean u_tau directly from wallShearStress (diagnostic)."""
    tau_means = []
    weights = []
    for info in patch_infos:
        if info.name not in wall_names:
            continue
        entry = values.get(info.name)
        if not entry:
            continue
        kind, payload = entry
        face_vals = _values_for_patch(kind, payload, info.n_faces)
        if not face_vals:
            continue
        mags = []
        for tau in face_vals:
            if isinstance(tau, (tuple, list)):
                tx, ty, tz = (float(c) for c in tau)
            else:
                tx, ty, tz = float(tau), 0.0, 0.0
            mags.append(math.sqrt(tx * tx + ty * ty + tz * tz))
        tau_means.append(float(np.mean(mags)))
        weights.append(float(info.n_faces))
    if not tau_means:
        return 0.0
    tau = float(np.average(tau_means, weights=weights))
    if kinematic_shear:
        return math.sqrt(tau)
    return math.sqrt(tau / rho_val) if rho_val > 0 else 0.0


def _match_weights(areas: np.ndarray, n_vals: int, area_total: float) -> np.ndarray:
    """Face weights matching *n_vals* entries (falls back to equal shares)."""
    if len(areas) == n_vals:
        return areas
    if len(areas) > 0 and area_total > 0:
        return np.full(n_vals, area_total / n_vals)
    return np.ones(n_vals)


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------

def proposed_first_layer(
    target_yplus: float,
    h1_used: float,
    result: CalibrationResult,
    nu: float | None = None,
) -> float | None:
    """New first-layer height for *target_yplus*, from the measured y+.

    y+ scales linearly with the first-cell height for the same flow, so
    ``h1_new = h1_used * target_yplus / mean_yplus``.  The result is clamped
    to ``1e-9 .. 0.1 * bbox``.  Returns ``None`` when it cannot be computed
    (calibration error / non-positive inputs).  *nu* is accepted for symmetry
    with the wallShearStress path and is not otherwise needed here.
    """
    del nu  # linear y+ scaling does not depend on viscosity
    try:
        target = float(target_yplus)
        h1 = float(h1_used)
        if (
            result is None
            or result.error
            or result.mean_yplus <= 0
            or target <= 0
            or h1 <= 0
        ):
            return None
        proposed = h1 * target / result.mean_yplus
        hi = 0.1 * float(result.bbox) if result.bbox and result.bbox > 0 else 0.1
        return float(min(max(proposed, 1e-9), hi))
    except (TypeError, ValueError):
        return None
