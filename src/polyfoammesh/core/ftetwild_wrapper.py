"""fTetWild tetrahedral meshing wrapper (experimental — issue #12).

Runs the whole experimental tet stage in ONE short-lived subprocess,
mirroring :mod:`core.gmsh_wrapper`'s CLI contract (one JSON result line
printed on stdout):

    python ftetwild_wrapper.py volume --step=<geom> --msh=<out.msh>
        --detail=<level> [--max-cell=<abs edge>] [--lr=<rel edge>]
        [--opt-iter=<n>]

Pipeline inside that subprocess:

1. GMSH surface mesh of the STEP/STL geometry
   (:func:`gmsh_wrapper.generate_surface_stl`) plus
   :func:`gmsh_wrapper.export_tagged_surface_npz` — the per-physical-group
   triangles that GMSH's own STL export loses (``gmsh.write`` emits a
   single "Created by Gmsh" solid, so patch names must be carried
   alongside the STL).
2. fTetWild tetrahedralization via the optional ``pytetwild`` package
   (``pip install polyfoammesh[ftetwild]``).
3. Boundary faces of the tet mesh assigned to the input patch names by
   nearest-centroid lookup (scipy cKDTree).
4. A Gmsh MSH 2.2 file with ``$PhysicalNames`` written for the existing,
   production gmshToFoam conversion (``gmsh_wrapper.py convert_to_foam``)
   — no custom polyMesh writer anywhere.

Motivation (issue #12): GMSH dominates the wall time of the tet stage;
fTetWild is a robust, fast alternative (see the feasibility spike:
venturi 16x faster than GMSH at better quality, robust on geometry GMSH
refuses to load).

Tunables (env): ``FTETWILD_LR`` overrides the relative edge length,
``FTETWILD_OPT_ITER`` the optimization-iteration budget.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: Detail level (GUI slider) -> target cells across the bounding box's
#: MIDDLE dimension (the physical cross-section on elongated parts). Same
#: philosophy and values as gmsh_wrapper._GMSH_DETAIL's ``cells_across`` —
#: the app's own resolution knob — so the Detail slider moves fTetWild the
#: same way it moves GMSH. Replaces an earlier bbox-DIAGONAL-relative
#: mapping (lr = diag/20): on a real elongated part (2 m long, 0.1 x 0.1
#: section) that gave ONE cell across the section — a target 20x coarser
#: than the input surface — and sent fTetWild's optimizer into an
#: unbounded edge-splitting runaway (measured: 884k splits in pass 0,
#: 5.1M tets, energy stuck at 31k instead of converging toward ~10).
DETAIL_CELLS_ACROSS: dict[str, int] = {
    "very_coarse": 8,
    "coarse": 13,
    "medium": 20,
    "fine": 32,
    "very_fine": 48,
}

DEFAULT_CELLS_ACROSS = 20

#: Bbox-DIAGONAL-relative edge length, used ONLY when explicitly requested
#: via the FTETWILD_LR env override (fTetWild's own paper default).
DEFAULT_LR = 0.05

#: Optimization-iteration budget (upstream default is 80). The spike
#: measured the upstream default on a geometry whose AMIPS energy stalls:
#: the mesh kept subdividing past 330k tets and never finished in 10 min,
#: while 15 iterations completed with better percentile quality than GMSH
#: on the same input. Keep this modest until a convergence-based budget
#: exists.
DEFAULT_OPT_ITER = 15

#: Tet face local vertex indices (VTK tetra ordering) and, for each face,
#: the index of the opposite vertex (used to orient faces outward).
_TET_FACES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),
    (0, 1, 3),
    (0, 2, 3),
    (1, 2, 3),
)
_TET_OPPOSITE: tuple[int, ...] = (3, 2, 1, 0)

#: Characters an OpenFOAM patch name may not contain (kept in sync with
#: boundary_reader._INVALID_PATCH_NAME_RE's structural class plus spaces).
_INVALID_NAME_RE = re.compile(r'[;{}"/\\\s]')


def sanitize_patch_names(names) -> list[str]:
    """Make patch names valid OpenFOAM words, preserving order, deduped.

    Physical names coming out of a STEP import can contain spaces or
    slashes ("Shape/Open CASCADE STEP translator 7.9 1"); gmshToFoam
    silently falls back to ``patch0`` for those. Runs of invalid
    characters collapse to a single ``_``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = _INVALID_NAME_RE.sub("_", str(raw)).strip("_") or "patch"
        base = name
        n = 2
        while name in seen:
            name = f"{base}_{n}"
            n += 1
        seen.add(name)
        out.append(name)
    return out


def boundary_faces(V: np.ndarray, T: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Faces used by exactly one tet, with their owner-tet index.

    Returns ``(faces, owner)`` where ``faces`` is ``(m, 3)`` int64 vertex
    ids (arbitrary winding) and ``owner`` the tet that references each
    face.
    """
    # allf is concatenated FACE-BLOCK-wise (all tets' face 0, then all
    # tets' face 1, ...) — so the per-row tet index is np.tile, NOT
    # np.repeat (repeat would pair each face with the wrong tet; caught by
    # the unit test, and the spike shipped the same slip, harmlessly only
    # because gmshToFoam re-orients from the volume elements anyway).
    allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
    tet_idx = np.tile(np.arange(len(T)), len(_TET_FACES))
    key = np.sort(allf, axis=1)
    _uniq, inv, counts = np.unique(
        key, axis=0, return_inverse=True, return_counts=True
    )
    inv = np.asarray(inv).reshape(-1)  # numpy 2.x inverse-shape churn
    # A key with count==1 belongs to exactly ONE row, so that row IS the
    # boundary face — no first-occurrence index lookup needed.
    rows = np.nonzero(counts[inv] == 1)[0]
    return allf[rows], tet_idx[rows]


def orient_faces_outward(
    V: np.ndarray, T: np.ndarray, faces: np.ndarray, owner: np.ndarray
) -> np.ndarray:
    """Return ``faces`` with winding flipped so normals point out of the solid.

    A face is outward when ``normal . (owner_tet_centroid - vertex) < 0``
    for the right-handed winding; gmshToFoam places each surface face
    according to its own winding, so this cannot be left arbitrary.
    """
    out = faces.copy()
    a, b, c = V[faces[:, 0]], V[faces[:, 1]], V[faces[:, 2]]
    n = np.cross(b - a, c - a)
    tet_c = V[np.asarray(T)[np.asarray(owner)]].mean(axis=1)
    flip = np.einsum("ij,ij->i", n, tet_c - a) > 0
    if np.any(flip):
        out[flip, 1], out[flip, 2] = faces[flip, 2], faces[flip, 1]
    return out


def assign_patches(
    V: np.ndarray,
    faces: np.ndarray,
    src_tris: np.ndarray,
    src_codes: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """Assign each output boundary face a patch code from the input surface.

    fTetWild output carries no physical groups, but its surface stays
    within the epsilon envelope of the input triangulation — so the
    nearest input triangle centroid is a reliable patch witness at the
    input's own sampling density (spike: mean assignment distance 5.6e-7
    vs 1.2e-6 mean centroid spacing on the same surface).

    Returns ``(codes, stats)``.
    """
    from scipy.spatial import cKDTree

    in_cent = src_tris.mean(axis=1)
    tree = cKDTree(in_cent)
    out_cent = V[faces].mean(axis=1)
    dist, nn = tree.query(out_cent)
    codes = src_codes[nn]
    dd, _ = tree.query(in_cent, k=2)
    stats = {
        "n_output_boundary_faces": int(len(faces)),
        "n_input_triangles": int(len(src_tris)),
        "input_mean_centroid_spacing": float(dd[:, 1].mean()),
        "assign_dist_mean": float(dist.mean()),
        "assign_dist_p99": float(np.percentile(dist, 99)),
        "assign_dist_max": float(dist.max()),
    }
    return codes, stats


def write_msh22(
    out_path: Path | str,
    V: np.ndarray,
    T: np.ndarray,
    faces: np.ndarray,
    face_phys: np.ndarray,
    patch_names: list[str],
) -> None:
    """Write a Gmsh MSH 2.2 file consumable by gmshToFoam.

    Layout mirrors what gmsh itself writes for the production path
    (verified in the spike): ``$PhysicalNames`` lists one dim-2 entry per
    patch, surface elements carry their patch's physical id in both tag
    slots, volume elements carry physical 1. MSH 2.2 — gmshToFoam
    segfaults on 4.x files with node-tag gaps (see gmsh_wrapper's
    MshFileVersion note), and the ids here are contiguous 1-based anyway.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="ascii") as fh:
        fh.write("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        fh.write(f"$PhysicalNames\n{len(patch_names)}\n")
        for i, name in enumerate(patch_names, 1):
            fh.write(f'2 {i} "{name}"\n')
        fh.write("$EndPhysicalNames\n")
        fh.write(f"$Nodes\n{len(V)}\n")
        for i, (x, y, z) in enumerate(V, 1):
            fh.write(f"{i} {x:.17g} {y:.17g} {z:.17g}\n")
        fh.write("$EndNodes\n")
        fh.write(f"$Elements\n{len(faces) + len(T)}\n")
        eid = 1
        for face, phys in zip(faces, face_phys):
            fh.write(
                f"{eid} 2 2 {int(phys)} {int(phys)} "
                f"{face[0] + 1} {face[1] + 1} {face[2] + 1}\n"
            )
            eid += 1
        for tet in T:
            fh.write(
                f"{eid} 4 2 1 1 "
                f"{tet[0] + 1} {tet[1] + 1} {tet[2] + 1} {tet[3] + 1}\n"
            )
            eid += 1
        fh.write("$EndElements\n")


def _dynamic_envelope(
    eps_frac: float | None, target_edge: float,
) -> float | None:
    """Absolute surface envelope from a fraction of the local cell edge.

    The dynamic (resolution-proportional) form requested 2026-10-19:
    15% of an 8 mm cell edge -> 1.2 mm envelope; 15% of a 1 mm edge ->
    0.15 mm.  Fidelity then scales with the slider and the part instead of
    being a fixed millimetre value.  Returns None when disabled.
    """
    if not eps_frac or eps_frac <= 0 or not target_edge or target_edge <= 0:
        return None
    return float(eps_frac) * float(target_edge)


def _relative_epsilon(bounds, epsilon_abs: float) -> float | None:
    """Convert an ABSOLUTE surface envelope (metres) to pytetwild's
    bbox-relative epsilon.

    pytetwild documents ``epsilon`` as "the maximum distance of the output
    surface from the input surface, relative to the bounding box size".
    The relative form is a trap on small parts (1e-4 on a 4 mm micromixer
    is 0.4 um and stalls), so the GUI exposes absolute millimetres and
    this helper derives the relative value per part.  Returns None for
    degenerate inputs (zero-size bbox or a non-positive envelope).
    """
    if epsilon_abs is None or epsilon_abs <= 0:
        return None
    b = np.asarray(bounds, dtype=np.float64)
    if b.size < 6:
        return None
    diag = float(np.linalg.norm([b[1] - b[0], b[3] - b[2], b[5] - b[4]]))
    if diag <= 0:
        return None
    return float(epsilon_abs) / diag


def tetrahedralize(
    stl_path: Path | str,
    *,
    lr: float | None = None,
    la: float | None = None,
    opt_iter: int = DEFAULT_OPT_ITER,
    decimate: float | None = None,
    subdivide: int | None = None,
    epsilon: float | None = None,
    epsilon_abs: float | None = None,
    stop_energy: float | None = None,
    disable_filtering: bool = False,
    simplify: bool = False,
    sizing_field=None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Run fTetWild on a surface STL. Returns ``(V, T, stats)``.

    ``lr`` is the relative edge length (bbox fraction), ``la`` an absolute
    one (the GUI's manual Max Cell Size); ``la`` wins when both are set.
    ``decimate`` (fraction of faces to keep) pre-simplifies the surface
    with quadric decimation — see :func:`_decimation_reduction` for why
    fTetWild needs this outside its own pipeline. ``subdivide`` applies
    that many linear subdivision rounds first (the under-dense mirror —
    see :func:`_refinement_subdivision`); it runs BEFORE the decimation,
    so a caller can subdivide to overshoot and decimate back to the
    target. Import of pytetwild is deferred so every other function in
    this module stays usable without the optional dependency.

    ``epsilon`` (relative surface envelope, default None = pytetwild's
    1e-3): how far the output surface may deviate from the input.  The
    default is loose by construction — measured on a real 3 m part: up
    to 5 mm deviation, 77% of points > 0.3 mm — and that deviation is
    what warps the dual boundary polygons and makes the poly conversion
    fail.  A tighter value (1e-4 = 0.3 mm on 3 m) keeps the surface
    GMSH-like.  ``epsilon_abs`` (metres) is the scale-safe form: it
    overrides ``epsilon`` and is converted to the bbox-relative value
    internally, so the same physical envelope is faithful on every part
    (the relative form stalls on small parts: 1e-4 on a 4 mm micromixer
    is 0.4 um).  ``stop_energy`` (default None = pytetwild's 10.0): the
    AMIPS energy early-stop; higher = stops sooner (faster, looser).
    ``disable_filtering`` (default False): keep small input features
    instead of letting fTetWild's filter remove them (another deviation
    source).  ``simplify`` (default False): fTetWild's internal edge-
    collapsing simplification.  Measured on the reference part: with it
    ON the output surface deviates up to 5 mm (77% of points > 0.3 mm)
    and the AMIPS energy explodes (max 140783 vs 12); with it OFF the
    deviation is 0.06 mm mean / 1.65 mm max (6% > 0.3 mm) and the mesh
    has HALF the cells (2.33M vs 4.47M) at comparable time.  OFF is the
    default; ``FTETWILD_SIMPLIFY=1`` re-enables it.
    """
    try:
        import pyvista as pv
        from pytetwild import tetrahedralize_pv
    except ImportError as exc:
        raise RuntimeError(
            "pytetwild non installato — opzionale sperimentale. "
            "Installarlo con: pip install pytetwild"
        ) from exc

    surf = pv.read(str(stl_path))
    if not isinstance(surf, pv.PolyData):
        surf = surf.extract_surface()
    surf = surf.triangulate()

    # Absolute surface envelope (metres) -> pytetwild's bbox-relative
    # epsilon.  The relative form is a trap on small parts: 1e-4 on a 3 m
    # part is 0.3 mm (faithful), but the same 1e-4 on a 4 mm micromixer
    # is 0.4 um and stalls the tetrahedralization.  Callers that want
    # "faithful geometry on every part" pass the envelope in metres.
    if epsilon_abs is not None and epsilon_abs > 0:
        rel = _relative_epsilon(surf.bounds, epsilon_abs)
        if rel is not None:
            epsilon = rel
            print(
                f"surface envelope: {epsilon_abs * 1000.0:.4g} mm absolute "
                f"-> {epsilon:.3e} relative (bbox diag "
                f"{np.linalg.norm(np.subtract(surf.bounds[1::2], surf.bounds[0::2])):.4g} m)",
                flush=True,
            )

    n_input_tris = int(surf.n_cells)
    n_decimated_tris = n_input_tris
    n_subdiv_tris = n_input_tris
    if subdivide is not None and int(subdivide) > 0:
        try:
            pts_np = np.asarray(surf.points, dtype=np.float64)
            tris_np = surf.faces.reshape(-1, 4)[:, 1:]
            for _ in range(int(subdivide)):
                pts_np, tris_np = _midpoint_subdivide(pts_np, tris_np)
            faces_pv = np.hstack([
                np.full((len(tris_np), 1), 3, dtype=np.int64), tris_np,
            ]).ravel()
            surf = pv.PolyData(pts_np, faces_pv)
            n_subdiv_tris = int(surf.n_cells)
        except Exception:
            # Refinement is an optimization; the original surface is still
            # valid input for fTetWild (only slower).
            logger.warning("surface subdivision failed - using the original", exc_info=True)
    if decimate is not None and 0.0 < decimate < 1.0:
        try:
            # Error-bounded decimation ONLY (2026-10-19, user report "lo
            # smoothing deve essere integrato correttamente"): the old
            # unbounded decimate_pro + Taubin(20, 0.1) pass moved surface
            # points by up to 60 mm on the reference part (mean 1.2 mm,
            # surface error 0.37 mm mean / 12.2 mm max) - pure geometry
            # destruction.  The bounded filter stops where the error
            # budget is reached; no post-smoothing.
            _err = 0.25 * float(epsilon_abs) if epsilon_abs else None
            _dec = _decimate_surface_bounded(surf, float(decimate), _err)
            if _dec is not None:
                surf = _dec
                n_decimated_tris = int(surf.n_cells)
        except Exception:
            # Decimation is an optimization; the original surface is still
            # valid input for fTetWild (only slower).
            logger.warning("surface decimation failed - using the original", exc_info=True)

    t0 = time.perf_counter()
    # Progress visibility (user report: "si ferma e non so perché"): with
    # quiet=True the whole tetrahedralization prints NOTHING (pyvista
    # confirmed: no progress callbacks, the GIL is held by the call), so
    # the stage looked frozen. loglevel=3 streams a handful of phase
    # lines (loading / collapsing / performed / timings) without the
    # per-pass debug spam. FTETWILD_QUIET=1 restores silence.
    _quiet_env = os.environ.get("FTETWILD_QUIET", "")
    quiet = _quiet_env.strip().lower() in ("1", "true", "yes")
    try:
        loglevel = int(os.environ.get("FTETWILD_LOGLEVEL", "3"))
    except ValueError:
        loglevel = 3
    extra_kwargs: dict = {}
    if epsilon:
        extra_kwargs["epsilon"] = float(epsilon)
    if stop_energy:
        extra_kwargs["stop_energy"] = float(stop_energy)
    if disable_filtering:
        extra_kwargs["disable_filtering"] = True
    if not simplify:
        extra_kwargs["simplify"] = False
    grid = tetrahedralize_pv(
        surf,
        edge_length_fac=DEFAULT_LR if lr is None else lr,
        edge_length_abs=la if la and la > 0 else None,
        quiet=quiet,
        loglevel=loglevel,
        num_opt_iter=int(opt_iter),
        sizing_field=sizing_field,
        sizing_field_scalars="target_edge" if sizing_field is not None else None,
        **extra_kwargs,
    )
    elapsed = time.perf_counter() - t0

    cells = grid.cells_dict
    if 10 not in cells:
        raise RuntimeError(
            f"fTetWild produced no tetrahedra (cell types: "
            f"{sorted(int(k) for k in cells)})"
        )
    T = np.asarray(cells[10], dtype=np.int64)
    V = np.asarray(grid.points, dtype=np.float64)
    stats = {
        "n_nodes": int(len(V)),
        "n_tets": int(len(T)),
        "tet_time_s": round(elapsed, 3),
        "lr": lr,
        "la": la,
        "opt_iter": int(opt_iter),
        "n_input_tris": n_input_tris,
        "n_decimated_tris": n_decimated_tris,
        "n_subdivided_tris": n_subdiv_tris,
        "subdivide_rounds": int(subdivide or 0),
    }
    return V, T, stats


def load_surface_solids(stl_path: Path | str) -> tuple[np.ndarray, list[str]]:
    """``(triangles, per-triangle patch names)`` of an STL surface.

    ASCII multi-solid exports keep their ``solid NAME`` patch names — the
    format core/stl_writer.py writes. Binary STL (and single-solid files
    without a usable name) carry no patch identity to recover, so they
    collapse to one patch named after the file.
    """
    path = Path(stl_path)
    text: str | None
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, UnicodeError):
        text = None
    if text and text.lstrip().startswith("solid") and "facet normal" in text:
        tris_list: list[np.ndarray] = []
        per_block: list[tuple[str, int]] = []
        for blk in re.split(r"\bsolid\b", text)[1:]:
            lines = blk.splitlines()
            nm = (lines[0].strip() if lines else "") or path.stem
            if nm.split()[0] == "endsolid":
                continue
            verts = [
                list(map(float, ln.split()[1:4]))
                for ln in lines
                if ln.strip().startswith("vertex")
            ]
            if verts:
                tris_list.append(
                    np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
                )
                per_block.append((nm, len(verts) // 3))
        if tris_list:
            names: list[str] = []
            for nm, n_tris in per_block:
                names.extend([nm] * n_tris)
            return np.concatenate(tris_list), names

    # Fallback: binary or name-less STL — one patch named after the file.
    import trimesh

    m = trimesh.load(str(path), force="mesh")
    tri = getattr(m, "triangles", None)
    tris = (
        np.asarray(tri, dtype=np.float64)
        if tri is not None
        else np.empty((0, 3, 3))
    )
    if tris.ndim != 3 or len(tris) == 0:
        raise RuntimeError(f"STL surface has no triangles: {path}")
    return tris, [path.stem] * len(tris)


def _median_edge_length(tris: np.ndarray) -> float:
    """Median triangle-edge length of a surface — the input's own sampling
    density, used as the sizing sanity reference in the run log.

    Shared edges appear twice (once per adjacent triangle); the median is
    robust to that and the duplicates only weight the distribution
    uniformly anyway."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    edges = np.concatenate(
        [
            np.linalg.norm(b - a, axis=1),
            np.linalg.norm(c - b, axis=1),
            np.linalg.norm(a - c, axis=1),
        ]
    )
    return float(np.median(edges))


def _default_target_edge(cross_scale: float, detail: str) -> float:
    """Default target edge from the Detail slider — the user's intent only.

    cells across the cross-section (DETAIL_CELLS_ACROSS), same resolution
    model as the GMSH path. This used to ALSO be clamped down to 2x the
    input surface density as an anti-runaway guard; that was wrong once
    surface decimation existed (measured: on a real 3 m part whose gmsh
    surface came out at ~1.4 mm median, the clamp dragged the target from
    12.75 mm to 2.85 mm — ~40M cells, a stall by construction). The
    density alignment belongs to _decimation_reduction, in the OTHER
    direction: the INPUT is adapted to the target, never the target to
    the input.
    """
    return cross_scale / DETAIL_CELLS_ACROSS.get(detail, DEFAULT_CELLS_ACROSS)


def _decimation_reduction(med_edge: float, target_edge: float) -> float | None:
    """Face-count fraction to keep when decimating an over-dense surface.

    fTetWild's own ``simplify`` removes detail only within the eps
    envelope — it does NOT decimate an over-dense input toward the target
    edge (fTetWild#93 / pytetwild#47; measured on a real 3 m part: a
    386k-triangle surface at ~1.4 mm against a 12.75 mm target had to be
    inserted and then collapsed element by element, blowing up the tet
    stage). Quadric decimation BEFORE the call brings the input to the
    density fTetWild is documented to work best with (target ~= input
    edge). This is the ONLY direction that may adapt densities: the
    INPUT goes to the target — never the target to the input (an earlier
    clamp that did the opposite dragged a 12.75 mm target down to 2.85 mm
    on that same part = ~40M cells, a stall by construction).

    Fires only when the input is >1.3x finer than the target; keeps a 1%
    floor so extreme inputs still leave a usable surface.
    """
    if med_edge <= 0 or target_edge <= 0:
        return None
    if med_edge * 1.3 > target_edge:
        return None
    return max((med_edge / target_edge) ** 2, 0.01)


def _snap_boundary_to_surface(V, T, surface, min_dist: float = 0.0):
    """Snap the tet boundary vertices onto the input surface.

    User bug 2026-10-19 ("fTetWild works worse than GMSH"): fTetWild moves
    the surface vertices inside the eps envelope (measured on the reference
    part: 1.03 mm mean / 17 mm max at the old 3 mm envelope), while GMSH is
    constrained to the exact surface (0.03 mm).  Projecting the boundary
    vertices back onto the ORIGINAL input surface restores GMSH-like
    fidelity while keeping fTetWild's interior tet quality and speed.

    Each move is volume-guarded: if snapping a vertex would make any
    incident tet non-positive, that vertex keeps its fTetWild position.
    Returns (V, n_snapped, max_move).
    """
    try:
        import vtk
        from pyvista import PolyData, wrap
    except Exception:  # noqa: BLE001 - optional dependency
        return V, 0, 0.0
    V = np.array(V, dtype=np.float64, copy=True)
    bf, _ = boundary_faces(V, T)
    vidx = np.unique(bf)
    if vidx.size == 0:
        return V, 0, 0.0
    surf = surface if isinstance(surface, PolyData) else wrap(surface)
    loc = vtk.vtkStaticCellLocator()
    loc.SetDataSet(surf)
    loc.BuildLocator()
    # vertex -> tet adjacency (for the volume guard)
    order = np.argsort(T.ravel(), kind="stable")
    tet_flat = np.repeat(np.arange(len(T), dtype=np.int64), 4)
    tet_of = tet_flat[order]
    corners = T.ravel()[order]
    counts = np.bincount(T.ravel(), minlength=len(V))
    starts = np.concatenate(([0], np.cumsum(counts)))
    def _tet_vol(ti):
        a, b, c, d = V[T[ti]]
        return float(np.dot(b - a, np.cross(c - a, d - a))) / 6.0
    n_snip = 0
    max_move = 0.0
    cp = [0.0, 0.0, 0.0]
    _cid = vtk.reference(0)
    _sid = vtk.reference(0)
    _d2 = vtk.reference(0.0)
    for v in vidx.tolist():
        ti = tet_of[starts[v]:starts[v + 1]]
        _d2.set(0.0)
        loc.FindClosestPoint(V[v], cp, _cid, _sid, _d2)
        if _d2.get() <= 0.0:
            continue
        target = np.asarray(cp, dtype=np.float64)
        dist = float(np.linalg.norm(target - V[v]))
        if dist <= min_dist:
            continue
        old = V[v].copy()
        V[v] = target
        if any(_tet_vol(t) <= 1e-18 for t in ti):
            V[v] = old
            continue
        n_snip += 1
        max_move = max(max_move, dist)
    return V, n_snip, max_move


def _build_sizing_field(surf, max_cell: float, min_cell: float | None,
                        cells_across: int = 8, n_samples: int = 3000):
    """cfMesh-like LOCAL sizing field for fTetWild (user request 2026-10-19:
    'recognise the geometry and refine where needed in the curvatures').

    cfMesh refines by surface curvature and passage thickness; GMSH does the
    same through its size callbacks.  pytetwild exposes a background-mesh
    sizing field (`sizing_field`, target edge length per point), so this
    builds one from the SAME validated ray-cast thickness sampler the GMSH
    path already uses:

      1. sample the local passage thickness on the surface (trimesh ray
         casting, ~n_samples points);
      2. target = thickness / cells_across, clamped to [min_cell, max_cell]
         (8 cells across a thin passage, cfMesh's surfaceThicknessRefinement);
      3. voxelize the bbox at ~max_cell (coarse: the field varies slowly),
         each grid point takes its nearest sample's target (else max_cell);
      4. emit an all-tetrahedral UnstructuredGrid with the point scalar
         'target_edge' for pytetwild.

    Returns (grid, stats) or (None, {}) when sampling is unavailable.
    """
    try:
        import numpy as _np
        import pyvista as _pv
        import trimesh
        from scipy.spatial import cKDTree
        from polyfoammesh.core.geometry import sample_thickness_field
    except Exception:  # noqa: BLE001 - optional deps
        return None, {}
    try:
        pts = _np.asarray(surf.points, dtype=_np.float64)
        tris = _np.asarray(surf.faces).reshape(-1, 4)[:, 1:]
        tri = trimesh.Trimesh(vertices=pts, faces=tris, process=False)
        lo = pts.min(axis=0)
        hi = pts.max(axis=0)
        ext = hi - lo
        bbox_max = float(ext.max())
        if bbox_max <= 0 or max_cell <= 0:
            return None, {}
        t_pts, t_thick = sample_thickness_field(
            tri, int(n_samples), bbox_max,
        )
        if len(t_pts) == 0:
            return None, {}
        floor = float(min_cell) if min_cell and min_cell > 0 else max_cell / 20.0
        tgt = _np.clip(
            t_thick / max(float(cells_across), 1.0),
            floor, float(max_cell),
        )
        # Curvature refinement (cfMesh surfaceCurvatureRefinement): the same
        # dihedral-angle radius estimate compute_curvature_sizing uses,
        # sampled at the curved face-adjacency edges, combined per grid
        # point with the thickness target (min wins).
        c_pts = None
        c_tgt = None
        try:
            ang = _np.asarray(tri.face_adjacency_angles, dtype=_np.float64)
            mask = (ang > _np.radians(1.0)) & (ang < _np.radians(30.0))
            if int(mask.sum()):
                ee = _np.asarray(tri.face_adjacency_edges, dtype=_np.int64)[mask]
                aa = ang[mask]
                vv = _np.asarray(tri.vertices, dtype=_np.float64)
                elen = _np.linalg.norm(vv[ee[:, 0]] - vv[ee[:, 1]], axis=1)
                radius = elen / (2.0 * _np.sin(aa / 2.0))
                c_pts = 0.5 * (vv[ee[:, 0]] + vv[ee[:, 1]])
                c_tgt = _np.clip(
                    radius / 8.0, floor, float(max_cell),
                )
        except Exception:  # noqa: BLE001
            c_pts = None
            c_tgt = None
        # coarse voxel grid (the field is smooth by construction)
        spacing = float(max_cell)
        n = _np.maximum(_np.ceil(ext / spacing).astype(_np.int64) + 1, 2)
        xs = lo[0] + _np.linspace(0.0, ext[0], int(n[0]))
        ys = lo[1] + _np.linspace(0.0, ext[1], int(n[1]))
        zs = lo[2] + _np.linspace(0.0, ext[2], int(n[2]))
        gx, gy, gz = _np.meshgrid(xs, ys, zs, indexing="ij")
        grid_pts = _np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
        tree = cKDTree(t_pts)
        dist, idx = tree.query(grid_pts, k=1, workers=-1)
        keep = dist <= 2.0 * spacing
        vals = _np.full(len(grid_pts), float(max_cell), dtype=_np.float64)
        vals[keep] = tgt[idx[keep]]
        if c_pts is not None and len(c_pts):
            ctree = cKDTree(c_pts)
            cdist, cidx = ctree.query(grid_pts, k=1, workers=-1)
            ckeep = cdist <= 2.0 * spacing
            vals[ckeep] = _np.minimum(vals[ckeep], c_tgt[cidx[ckeep]])
        # cubes -> 6 tets each (standard decomposition)
        nx, ny, nz = int(n[0]), int(n[1]), int(n[2])
        def _nid(i, j, k):
            return (i * ny + j) * nz + k
        cells = []
        for i in range(nx - 1):
            for j in range(ny - 1):
                for k in range(nz - 1):
                    v = [
                        _nid(i, j, k), _nid(i + 1, j, k),
                        _nid(i + 1, j + 1, k), _nid(i, j + 1, k),
                        _nid(i, j, k + 1), _nid(i + 1, j, k + 1),
                        _nid(i + 1, j + 1, k + 1), _nid(i, j + 1, k + 1),
                    ]
                    for t in (
                        (0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6),
                        (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6),
                    ):
                        cells.extend((4, v[t[0]], v[t[1]], v[t[2]], v[t[3]]))
        grid = _pv.UnstructuredGrid(
            _np.asarray(cells, dtype=_np.int64),
            _np.full(len(cells) // 5, 10, dtype=_np.uint8),  # VTK_TETRA
            grid_pts,
        )
        grid.point_data["target_edge"] = vals
        stats = {
            "n_samples": int(len(t_pts)),
            "min": float(vals.min()),
            "median": float(_np.median(vals)),
            "max": float(vals.max()),
            "n_refined": int((vals < float(max_cell) * 0.999).sum()),
            "n_points": int(len(grid_pts)),
        }
        return grid, stats
    except Exception:  # noqa: BLE001 - never break the tet stage
        logger.debug("sizing field build failed", exc_info=True)
        return None, {}


def _decimate_surface_bounded(surface, keep: float, max_error: float | None):
    """Decimate an over-dense surface toward the target density, then
    VERIFY the error budget.

    History (2026-10-19): pytetwild's own decimation is unchecked; the
    vtkDecimatePro/MaximumError variant is checked but far too
    conservative (it stopped at 14.5% keep with the same triangle count
    as the plain quadric filter, so no benefit, and on other inputs it
    keeps the surface dense = far more cells than the resolution slider
    asks for).  The plain quadric decimation measured 0.012 mm mean /
    0.14 mm max on the reference part - excellent.  So: decimate with the
    quadric filter, sample the ORIGINAL surface against the result, and
    only if the budget is exceeded keep more triangles (retry) or fall
    back to the untouched surface.

    ``keep`` is the fraction of triangles to keep; ``max_error`` absolute
    (None = no check).  Returns the decimated PolyData or None.
    """
    try:
        import numpy as _np
        import pyvista as _pv
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    keep = float(min(max(keep, 0.01), 1.0))
    if keep >= 0.999:
        return None
    try:
        cand = surface.decimate(1.0 - keep)
    except Exception:  # noqa: BLE001
        return None
    if cand is None or cand.n_cells < 4:
        return None
    if max_error is None:
        return cand

    def _worst(dec):
        n = min(surface.n_points, 20000)
        idx = _np.linspace(0, surface.n_points - 1, n).astype(_np.int64)
        d = _np.abs(_np.asarray(
            _pv.PolyData(surface.points[idx]).compute_implicit_distance(dec)[
                "implicit_distance"
            ]
        ))
        return float(d.max()) if d.size else 0.0

    worst = _worst(cand)
    if worst <= max_error:
        return cand
    # Budget exceeded: search upward geometrically (sqrt ladder).  A single
    # +50% retry was measured to jump from 1% to 50% keep on a real part
    # (320k triangles, far from the target): four sqrt steps bracket the
    # largest reduction that still fits the budget.
    k = keep
    for _ in range(4):
        k = float(_np.sqrt(k))
        if k >= 0.999:
            break
        try:
            alt = surface.decimate(1.0 - k)
        except Exception:  # noqa: BLE001
            break
        if alt is None or alt.n_cells < 4:
            break
        if _worst(alt) <= max_error:
            return alt
    return None


def _refinement_subdivision(med_edge: float, target_edge: float) -> int | None:
    """Number of linear subdivision rounds for an under-dense surface.

    The MIRROR of :func:`_decimation_reduction`: when the target edge is
    FINER than the input surface (target < input / 1.3), fTetWild has to
    subdivide the surface internally, element by element, inside its own
    pipeline — the same class of runaway as the over-dense case, and the
    one user-reported as "hours on a finer re-mesh".  A linear subdivision
    round (each triangle -> 4 midpoints, geometry unchanged) halves the
    median edge, so a couple of rounds bring the input to the target's
    density BEFORE the call.  Bounded at 3 rounds (64x triangles): beyond
    that the request is a different mesh class, not a density alignment.

    Returns the number of rounds, or None when the input is not
    significantly coarser than the target.
    """
    if med_edge <= 0 or target_edge <= 0:
        return None
    if med_edge <= target_edge * 1.3:
        return None
    rounds = int(np.ceil(np.log2(med_edge / (target_edge * 1.3))))
    return min(max(rounds, 1), 3)


def _midpoint_subdivide(
    points: np.ndarray, tris: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """One round of midpoint (linear) subdivision, manifold-free.

    Each triangle becomes 4 sub-triangles through the SHARED midpoints of
    its three edges (keyed by the sorted vertex pair), so the surface
    stays watertight even where the input is non-manifold.  VTK's
    subdivision filters refuse non-manifold input outright — measured on
    the real fTetWild input surface, which is non-manifold (tangent
    contact): "Dataset is non-manifold and cannot be subdivided" — while
    this split has no such requirement.  Geometry is unchanged: the new
    points lie exactly on the input edges.
    """
    e = np.concatenate([
        np.sort(tris[:, [0, 1]], axis=1),
        np.sort(tris[:, [1, 2]], axis=1),
        np.sort(tris[:, [2, 0]], axis=1),
    ], axis=0)
    uniq, inv = np.unique(e, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    n_pts = len(points)
    mids = (points[uniq[:, 0]] + points[uniq[:, 1]]) * 0.5
    new_points = np.vstack([points, mids])
    n_tri = len(tris)
    a0 = n_pts + inv[:n_tri]
    a1 = n_pts + inv[n_tri:2 * n_tri]
    a2 = n_pts + inv[2 * n_tri:]
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    new_tris = np.concatenate([
        np.stack([v0, a0, a2], axis=1),
        np.stack([a0, v1, a1], axis=1),
        np.stack([a2, a1, v2], axis=1),
        np.stack([a0, a1, a2], axis=1),
    ], axis=0)
    return new_points, new_tris


def boundary_defects(V: np.ndarray, T: np.ndarray) -> tuple[int, int]:
    """Count the two defect classes the dual cannot close a cell for.

    Returns ``(n_nonmanifold_edges, n_bowtie_vertices)``:
    * non-manifold boundary edges: edges with != 2 incident boundary faces;
    * bowtie vertices: vertices whose boundary fan is disconnected — the
      tet one-ring splits into >1 face-connected corner, so the star is
      not a ball. This includes pure vertex pinches that carry NO
      non-manifold edge (the class the edge-only check misses).
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
    key = np.sort(allf, axis=1)
    _u, inv, counts = np.unique(
        key, axis=0, return_inverse=True, return_counts=True
    )
    inv = np.asarray(inv).reshape(-1)
    bfaces = allf[counts[inv] == 1]
    if len(bfaces) == 0:
        return 0, 0
    m = len(bfaces)
    bedges = np.concatenate(
        [bfaces[:, [0, 1]], bfaces[:, [1, 2]], bfaces[:, [2, 0]]]
    )
    be_key = np.sort(bedges, axis=1)
    # canonical edge id via unique rows
    bu, binv, bc = np.unique(be_key, axis=0, return_inverse=True, return_counts=True)
    binv = np.asarray(binv).reshape(-1)
    n_nm = int((bc != 2).sum())

    # face adjacency through edges whose count is exactly 2: in the
    # edge-sorted order those two entries are adjacent
    face_of_row = np.tile(np.arange(m), 3)
    order = np.argsort(binv, kind="stable")
    s_inv = binv[order]
    s_face = face_of_row[order]
    starts = np.flatnonzero(np.r_[True, s_inv[1:] != s_inv[:-1]])
    rows_f: list[int] = []
    cols_f: list[int] = []
    for eid in np.flatnonzero(bc == 2):
        a = starts[eid]
        rows_f.append(int(s_face[a]))
        cols_f.append(int(s_face[a + 1]))
    if rows_f:
        g = coo_matrix(
            (np.ones(len(rows_f) * 2, dtype=np.int8),
             (np.array(rows_f + cols_f), np.array(cols_f + rows_f))),
            shape=(m, m),
        )
        _nc, comp = connected_components(g, directed=False)
    else:
        comp = np.arange(m)
    # a bowtie vertex has faces from >1 component
    pair_v = bfaces.reshape(-1)
    pair_c = np.repeat(comp, 3)
    uniq_pairs = np.unique(
        np.stack([pair_v, pair_c], axis=1), axis=0
    )
    v_ids, v_counts = np.unique(uniq_pairs[:, 0], return_counts=True)
    n_bow = int((v_counts > 1).sum())
    return n_nm, n_bow


def split_nonmanifold_vertices(
    V: np.ndarray, T: np.ndarray, max_iter: int = 12
) -> tuple[np.ndarray, np.ndarray, int]:
    """Desingularize the boundary so every vertex star is a ball.

    fTetWild's output may have a non-manifold boundary where two sheets
    touch (its documented ``--manifold-surface`` class — pytetwild does
    not expose that flag, so the equivalent post-pass lives here). Two
    defect shapes occur, both measured on a real 3 m duct: edges with 4
    boundary faces (a V-pinch of 3 vertices) and pure VERTEX bowties
    whose edges are all manifold. The barycentric dual needs every star
    to be a ball — its cells are otherwise combinatorially open — so a
    vertex whose tet one-ring splits into >1 face-connected corner gets
    duplicated per corner (one copy per extra corner), with a micro
    offset (1e-2 x local edge, ~1% tet distortion) so the duplicates are
    not numerically coincident.

    Iterates to a fixpoint: splitting one bowtie can reveal/leave
    others. Returns ``(V, T, n_splits)``; inputs are not modified. The
    caller SHOULD re-check with :func:`boundary_defects` and log any
    residual honestly.
    """
    V = np.asarray(V, dtype=np.float64).copy()
    T = np.asarray(T, dtype=np.int64).copy()
    added: list[np.ndarray] = []
    n_splits = 0

    def _pos(idx: int) -> np.ndarray:
        idx = int(idx)
        return V[idx] if idx < len(V) else added[idx - len(V)]

    for _ in range(max_iter):
        n_nm, n_bow = boundary_defects(V, T)
        if n_nm == 0 and n_bow == 0:
            break
        # candidates: endpoints of non-manifold edges + bowtie vertices
        allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
        key = np.sort(allf, axis=1)
        _u, inv, counts = np.unique(
            key, axis=0, return_inverse=True, return_counts=True
        )
        inv = np.asarray(inv).reshape(-1)
        bfaces = allf[counts[inv] == 1]
        bedges = np.sort(
            np.concatenate(
                [bfaces[:, [0, 1]], bfaces[:, [1, 2]], bfaces[:, [2, 0]]]
            ),
            axis=1,
        )
        eu, ec = np.unique(bedges, axis=0, return_counts=True)
        cand = set(int(x) for x in eu[ec != 2].ravel())
        # bowtie detection again locally (reuse boundary_defects' logic by
        # asking it for the count only would lose the ids; cheap enough to
        # recompute the pair list here)
        from scipy.sparse import coo_matrix as _coo
        from scipy.sparse.csgraph import connected_components as _cc

        m = len(bfaces)
        be_key = np.sort(
            np.concatenate(
                [bfaces[:, [0, 1]], bfaces[:, [1, 2]], bfaces[:, [2, 0]]]
            ),
            axis=1,
        )
        _bu, binv, bc = np.unique(
            be_key, axis=0, return_inverse=True, return_counts=True
        )
        binv = np.asarray(binv).reshape(-1)
        face_of_row = np.tile(np.arange(m), 3)
        order = np.argsort(binv, kind="stable")
        s_inv = binv[order]
        s_face = face_of_row[order]
        starts = np.flatnonzero(np.r_[True, s_inv[1:] != s_inv[:-1]])
        r_f, c_f = [], []
        for eid in np.flatnonzero(bc == 2):
            a = starts[eid]
            r_f.append(int(s_face[a]))
            c_f.append(int(s_face[a + 1]))
        if r_f:
            g = _coo(
                (np.ones(len(r_f) * 2, dtype=np.int8),
                 (np.array(r_f + c_f), np.array(c_f + r_f))),
                shape=(m, m),
            )
            _nc, comp = _cc(g, directed=False)
        else:
            comp = np.arange(m)
        pairs = np.unique(
            np.stack([bfaces.reshape(-1), np.repeat(comp, 3)], axis=1), axis=0
        )
        v_ids, v_counts = np.unique(pairs[:, 0], return_counts=True)
        cand.update(int(x) for x in v_ids[v_counts > 1])

        progress = 0
        for v in sorted(cand):
            local = np.nonzero((T == v).any(axis=1))[0]
            if len(local) == 0:
                continue
            parent = {int(t): int(t) for t in local}

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            face_map: dict[tuple, list[int]] = {}
            for t in local:
                tet = T[t]
                for f in _TET_FACES:
                    verts = tet[list(f)]
                    if v in verts:
                        k = tuple(sorted(int(x) for x in verts))
                        face_map.setdefault(k, []).append(int(t))
            for ts in face_map.values():
                root = find(ts[0])
                for t2 in ts[1:]:
                    parent[find(t2)] = root
            groups: dict[int, list[int]] = {}
            for t in local:
                groups.setdefault(find(int(t)), []).append(int(t))
            if len(groups) <= 1:
                continue

            local_verts = np.unique(T[local])
            others = local_verts[local_verts != v]
            ring_c = (
                np.array([_pos(i) for i in others]).mean(axis=0)
                if len(others)
                else _pos(v)
            )
            direction = _pos(v) - ring_c
            dn = float(np.linalg.norm(direction))
            direction = direction / dn if dn > 0 else np.array([1.0, 0.0, 0.0])
            tet_local = T[local]
            uniq_loc, inv_loc = np.unique(tet_local, return_inverse=True)
            pts_tab = np.array([_pos(i) for i in uniq_loc])
            tets_xyz = pts_tab[inv_loc].reshape(tet_local.shape + (3,))
            edge_len = np.linalg.norm(tets_xyz[:, 1] - tets_xyz[:, 0], axis=1)
            eps = 1e-2 * float(edge_len.mean()) if edge_len.size else 1e-9

            for root in sorted(groups)[1:]:
                new_id = len(V) + len(added)
                added.append(_pos(v) + direction * eps)
                for t in groups[root]:
                    T[t] = np.where(T[t] == v, new_id, T[t])
                n_splits += 1
                progress += 1
        if progress == 0:
            # no split achieved (a pathological configuration): stop and
            # let the caller report the residual defect honestly
            break

    if added:
        V = np.vstack([V, np.asarray(added, dtype=np.float64)])
    return V, T, n_splits


def drop_small_components(
    V: np.ndarray, T: np.ndarray, max_drop: int = 1024
) -> tuple[np.ndarray, np.ndarray, int]:
    """Drop tiny disconnected tet components from fTetWild's output.

    fTetWild can emit an outlier cell / micro-cluster near a contact
    (measured: "Number of regions: 2, region 1 with 1 cells" in the tet
    checkMesh of a real run; the dual then carries a 4-cell floating
    island and checkMesh reports 2 regions). Components are found by
    face-adjacency; only components of at most ``max_drop`` cells are
    removed — the measured artifacts are single cells / handfuls, while
    any legitimate multi-solid body in CFD parts is orders of magnitude
    larger. Dropping a disconnected closed complex keeps the main mesh
    closed. Returns ``(V, T, n_dropped_tets)``; vertex ids are compacted
    if anything was dropped.
    """
    V = np.asarray(V, dtype=np.float64)
    T = np.asarray(T, dtype=np.int64)
    if len(T) == 0:
        return V, T, 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    allf = np.concatenate([T[:, list(f)] for f in _TET_FACES], axis=0)
    tet_of_row = np.tile(np.arange(len(T)), len(_TET_FACES))
    key = np.sort(allf, axis=1)
    _u, inv, counts = np.unique(
        key, axis=0, return_inverse=True, return_counts=True
    )
    inv = np.asarray(inv).reshape(-1)
    # order rows by key so count==2 pairs are adjacent
    order = np.argsort(inv, kind="stable")
    s_inv = inv[order]
    s_tet = tet_of_row[order]
    starts = np.flatnonzero(np.r_[True, s_inv[1:] != s_inv[:-1]])
    r_l: list[int] = []
    c_l: list[int] = []
    for eid in np.flatnonzero(counts == 2):
        a = starts[eid]
        t1, t2 = int(s_tet[a]), int(s_tet[a + 1])
        if t1 != t2:
            r_l.append(t1)
            c_l.append(t2)
    if not r_l:
        return V, T, 0
    g = coo_matrix(
        (np.ones(2 * len(r_l), dtype=np.int8),
         (np.array(r_l + c_l), np.array(c_l + r_l))),
        shape=(len(T), len(T)),
    )
    _nc, lab = connected_components(g, directed=False)
    sizes = np.bincount(lab, minlength=lab.max() + 1)
    main = int(sizes.argmax())
    drop_mask = (lab != main) & (sizes[lab] <= max_drop)
    n_dropped = int(drop_mask.sum())
    if n_dropped == 0:
        return V, T, 0
    T2 = T[~drop_mask]
    used = np.unique(T2)
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    return V[used], remap[T2], n_dropped


def run_volume(
    step_path: Path | str,
    msh_path: Path | str,
    detail: str = "medium",
    max_cell: float = 0.0,
    lr: float | None = None,
    opt_iter: int | None = None,
    epsilon: float | None = None,
    epsilon_abs: float | None = None,
    eps_frac: float | None = None,
    adaptive: bool = True,
    scale: float = 1.0,
    stop_energy: float | None = None,
    disable_filtering: bool = False,
    simplify: bool = False,
) -> dict:
    """Full experimental tet stage: surface -> fTetWild -> patched .msh.

    Returns the CLI result dict (also printed as the final stdout JSON
    line by ``_cli_main``).
    """
    t_start = time.perf_counter()
    step_path = Path(step_path).resolve()
    msh_path = Path(msh_path).resolve()
    msh_path.parent.mkdir(parents=True, exist_ok=True)

    stl_path = msh_path.with_suffix(".surface.stl")
    tagged_path = msh_path.with_suffix(".surface.tagged.npz")

    if lr is None:
        env_lr = os.environ.get("FTETWILD_LR", "")
        try:
            lr = float(env_lr) if env_lr else None
        except ValueError:
            lr = None
    if opt_iter is None:
        env_iter = os.environ.get("FTETWILD_OPT_ITER", "")
        try:
            opt_iter = int(env_iter) if env_iter else None
        except ValueError:
            opt_iter = None
    if opt_iter is None:
        opt_iter = DEFAULT_OPT_ITER
    if epsilon is None:
        env_eps = os.environ.get("FTETWILD_EPS", "")
        try:
            epsilon = float(env_eps) if env_eps else None
        except ValueError:
            epsilon = None
    if epsilon_abs is None:
        env_eps_mm = os.environ.get("FTETWILD_EPS_MM", "")
        try:
            epsilon_abs = (
                float(env_eps_mm) / 1000.0 if env_eps_mm else None
            )
        except ValueError:
            epsilon_abs = None
    if eps_frac is None:
        env_frac = os.environ.get("FTETWILD_EPS_FRAC", "")
        try:
            eps_frac = float(env_frac) if env_frac else None
        except ValueError:
            eps_frac = None
    if epsilon_abs is not None:
        # Absolute envelope wins over the relative one (scale-safe form).
        epsilon = None
        eps_frac = None
    if os.environ.get("FTETWILD_ADAPTIVE", "").strip().lower() in (
        "0", "false", "no", "off",
    ):
        adaptive = False
    try:
        scale = float(scale) if scale else 1.0
    except (TypeError, ValueError):
        scale = 1.0
    if stop_energy is None:
        env_se = os.environ.get("FTETWILD_STOP_ENERGY", "")
        try:
            stop_energy = float(env_se) if env_se else None
        except ValueError:
            stop_energy = None
    if not disable_filtering:
        disable_filtering = os.environ.get(
            "FTETWILD_NO_FILTERING", "",
        ).strip().lower() in ("1", "true", "yes", "on")
    if os.environ.get("FTETWILD_SIMPLIFY", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        simplify = True

    t0 = time.perf_counter()
    if step_path.suffix.lower() == ".stl":
        # STL inputs skip GMSH entirely: its sizing probe clears the model
        # and an STL-imported (discretized) surface cannot be re-meshed
        # afterwards — generate(2) yields nothing and gmsh.write produces
        # an EMPTY file (verified directly). The input already IS a
        # surface mesh, so feed it to fTetWild as-is and recover patch
        # names from its solids (binary STLs carry none -> one patch).
        stl_source = step_path
        if abs(scale - 1.0) > 1e-12:
            # Scale the imported STL about the origin (GUI unit/scale): the
            # mesher and every sizing input must live in the same units.
            import pyvista as _pvs
            _sp = _pvs.read(str(step_path)).triangulate()
            _sp.points = _sp.points * float(scale)
            stl_source = msh_path.with_name("mesh.surface.scaled.stl")
            _sp.save(str(stl_source))
        src_tris, raw_names = load_surface_solids(stl_source)
    else:
        from polyfoammesh.core.gmsh_wrapper import (
            export_tagged_surface_npz,
            generate_surface_stl,
        )

        generate_surface_stl(step_path, stl_path, detail=detail, scale=scale)
        export_tagged_surface_npz(tagged_path)
        tagged = np.load(tagged_path, allow_pickle=True)
        src_tris = tagged["tris"]
        raw_names = list(tagged["names"])
        stl_source = stl_path
    surface_time_s = time.perf_counter() - t0

    # Sizing diagnostics BEFORE the run: fTetWild can legitimately take
    # minutes on complex parts (its optimizer has no wall-clock bound) and
    # the subprocess is otherwise silent — seeing the target edge vs the
    # input surface density up front is what tells the user whether the
    # numbers are in a sane regime (measured: a target edge 20x larger
    # than the surface features sends the optimizer into an unbounded
    # edge-splitting runaway — one real part did 884k splits in pass 0).
    lo = src_tris.reshape(-1, 3).min(axis=0)
    hi = src_tris.reshape(-1, 3).max(axis=0)
    dims = hi - lo
    bbox_diag = float(np.linalg.norm(dims))
    # Cross-section reference for the default target: middle bbox dimension
    # (same choice the app's adaptive sizing makes for its own resolution
    # model). An elongated part's diagonal would be dominated by its
    # LENGTH; the cross-section is what decides cells across the passage.
    cross_scale = float(np.median(dims))
    med_edge = _median_edge_length(src_tris)
    la_used = max_cell if max_cell and max_cell > 0 else None
    if la_used is None and lr is None:
        la_used = _default_target_edge(cross_scale, detail)
    # Sanity clamp: a target edge at/above the part size (a stale unit or
    # slider value) makes GMSH's surface sizing and fTetWild's optimizer
    # run away (measured: a 0.1 m part with an 8 m target -> MemoryError /
    # 'vector too long').  Same ceiling as the GMSH path (max_extent * 0.5).
    if la_used is not None:
        _ext_max = float(np.max(dims))
        if _ext_max > 0 and float(la_used) > 0.5 * _ext_max:
            print(
                f"[fTetWild] target edge {float(la_used):.4g} m is larger "
                f"than half the domain ({0.5 * _ext_max:.4g} m) - clamping; "
                "check the CAD unit / max cell size.",
                flush=True,
            )
            la_used = 0.5 * _ext_max
    target_edge = la_used if la_used else (lr or DEFAULT_LR) * bbox_diag
    # Dynamic surface envelope (user request 2026-10-19): a fraction of the
    # LOCAL target edge, so fidelity scales with the resolution and the part
    # instead of being a fixed millimetre value.  15% of the cell edge is
    # the default (8 mm cell -> 1.2 mm envelope; 1 mm cell -> 0.15 mm).
    _dyn = _dynamic_envelope(eps_frac, target_edge)
    if _dyn is not None:
        epsilon_abs = _dyn
        epsilon = None
    # Hard safety cap by the part's own size (2026-10-19, geometry battery):
    # a 15% envelope on a coarse target can exceed a thin part's thickness
    # (measured: a 10 mm plate with a 14.6 mm envelope -> fTetWild returns
    # ZERO tets; a 3 m pipe at a 250 mm target -> a pinched non-manifold
    # boundary the dual rejects).  Capping at 5% of the smallest bbox
    # dimension keeps thin features alive at any slider position.
    if epsilon_abs:
        _min_dim = float(np.min(dims))
        _cap = 0.05 * _min_dim
        if _cap > 0.0 and float(epsilon_abs) > _cap:
            print(
                f"[fTetWild] envelope capped to {_cap * 1e3:.3g} mm "
                "(5% of the smallest dimension) - a larger envelope would "
                "erase thin features.",
                flush=True,
            )
            epsilon_abs = _cap
    print(
        f"experimental tet stage: bbox_diag={bbox_diag:.4g} "
        f"cross_section={cross_scale:.4g} "
        f"target_edge={target_edge:.4g} input_median_edge={med_edge:.4g} "
        f"eps={f'{epsilon_abs * 1000.0:.4g}mm' if epsilon_abs else (epsilon if epsilon else 'default(1e-3)')}"
        f"{f' ({eps_frac:.0%} of the target edge)' if eps_frac else ''} "
        f"simplify={simplify} "
        f"({len(src_tris):,} input triangles, {len(set(raw_names))} "
        "patches) - long runs possible on complex parts; Cancel stays available",
        flush=True,
    )
    # Over-dense input -> decimate BEFORE fTetWild: its own simplify only
    # removes detail within the eps envelope, it never decimates toward the
    # target edge (research: fTetWild#93 / pytetwild#47; measured 386k-
    # triangle input vs a 12.75 mm target blew the stage up). This is the
    # density-alignment direction that works: input -> target.
    # Under-dense input -> subdivide BEFORE fTetWild: when the target is
    # finer than the surface, fTetWild subdivides it internally, element
    # by element, inside its own pipeline - the same runaway class as the
    # over-dense case and the user-reported "hours on a finer re-mesh".
    # Linear subdivision (geometry unchanged) brings the input to the
    # target's density up front; any overshoot is decimated back below.
    n_sub = _refinement_subdivision(med_edge, target_edge)
    if n_sub:
        print(
            f"input surface is {med_edge / target_edge:.1f}x coarser than "
            f"the target edge - subdividing it {n_sub} round(s) (linear, "
            "geometry unchanged) before fTetWild, which would otherwise "
            "subdivide internally element by element (the measured "
            "hours-long regime on finer re-meshes).",
            flush=True,
        )
        reduction = _decimation_reduction(
            med_edge / (2.0 ** n_sub), target_edge,
        )
    else:
        reduction = _decimation_reduction(med_edge, target_edge)
    decimated_surface = None
    if reduction is not None:
        print(
            f"input surface is {target_edge / med_edge:.1f}x finer than the "
            f"target edge - decimating it to ~{reduction:.1%} of its "
            f"{len(src_tris):,} triangles before fTetWild "
            "(it does not decimate toward the target itself).",
            flush=True,
        )
        # Bounded variant (2026-10-19): quadric decimation without an error
        # bound moved the reference surface by 1 mm mean / 17 mm max - the
        # user-visible "fTetWild worse than GMSH".  Budget 25% of the eps
        # envelope for the decimation, the rest stays for the mesher.
        if epsilon_abs:
            try:
                import pyvista as _pv
                _surf = _pv.read(str(stl_source)).triangulate()
                _dec = _decimate_surface_bounded(
                    _surf, float(reduction), 0.25 * float(epsilon_abs),
                )
                if _dec is not None:
                    decimated_surface = (
                        msh_path.parent / "mesh.surface.decimated.stl"
                    )
                    _dec.save(str(decimated_surface))
                    print(
                        f"[fTetWild] bounded decimation: "
                        f"{_surf.n_faces:,} -> {_dec.n_faces:,} triangles "
                        f"(max surface error <= 0.25 x envelope = "
                        f"{0.25 * float(epsilon_abs) * 1e3:.3g} mm)",
                        flush=True,
                    )
            except Exception as _exc:  # noqa: BLE001 - keep the old path
                print(
                    f"[fTetWild] bounded decimation unavailable ({_exc}); "
                    "using the engine's own decimation.",
                    flush=True,
                )
                decimated_surface = None
    # Adaptive local sizing (2026-10-19, cfMesh-like): build a background
    # sizing field from the validated ray-cast passage-thickness sampler so
    # thin passages get finer cells while the bulk stays at the target.
    sizing_field = None
    if adaptive and epsilon_abs:
        try:
            import pyvista as _pv3
            _field_surf = _pv3.read(
                str(decimated_surface or stl_source),
            ).triangulate()
            sizing_field, _fs = _build_sizing_field(
                _field_surf, target_edge, None, cells_across=8,
            )
            if sizing_field is not None:
                print(
                    f"[fTetWild] adaptive sizing field: "
                    f"{_fs['n_samples']:,} thickness samples, target edge "
                    f"{_fs['min'] * 1e3:.3g}-{_fs['max'] * 1e3:.3g} mm "
                    f"({_fs['n_refined']:,}/{_fs['n_points']:,} grid points "
                    "refined)",
                    flush=True,
                )
        except Exception as _exc:  # noqa: BLE001 - never break the tet stage
            print(
                f"[fTetWild] adaptive sizing unavailable ({_exc}); "
                "using the uniform target edge.",
                flush=True,
            )
            sizing_field = None
    print(
        "starting fTetWild tetrahedralization now - the .stl above is only "
        "the SURFACE (its input); fTetWild runs without output by design, so "
        "the GUI 'still running (... CPU%)' heartbeat is the liveness signal "
        "until the result appears.",
        flush=True,
    )

    # fTetWild drops a __tracked_surface.stl debug artifact into the
    # CURRENT WORKING DIRECTORY (verified: it landed in the repo root/src
    # during testing) — run it from the case dir so the junk stays with
    # the disposable run output instead of polluting the source tree.
    prev_cwd = os.getcwd()
    os.chdir(msh_path.parent)
    try:
        V, T, tet_stats = tetrahedralize(
            decimated_surface or stl_source, lr=lr, la=la_used,
            opt_iter=opt_iter,
            decimate=None if decimated_surface else reduction,
            subdivide=n_sub,
            epsilon=epsilon,
            epsilon_abs=epsilon_abs,
            stop_energy=stop_energy,
            disable_filtering=disable_filtering,
            simplify=simplify,
            sizing_field=sizing_field,
        )
    finally:
        os.chdir(prev_cwd)

    # fTetWild may emit a non-manifold boundary where two sheets touch
    # (its --manifold-surface class; pytetwild exposes no such flag) —
    # the barycentric dual rejects it outright ("not a closed
    # tetrahedral volume") or produces combinatorially open cells.
    # Repair before tagging/writing.
    V, T, n_splits = split_nonmanifold_vertices(V, T)
    # GMSH-like surface fidelity (2026-10-19): fTetWild moves the boundary
    # vertices inside the eps envelope (measured 0.68-1.0 mm mean at the
    # old envelope) while GMSH is constrained to the exact surface.  Snap
    # the boundary vertices back onto the ORIGINAL input surface (not the
    # decimated copy) with a per-move volume guard.  Measured on the
    # reference part: 0.677 -> 0.109 mm mean, better than GMSH at the same
    # resolution (0.287 mm).  Runs AFTER the manifold split so shared
    # vertices are not projected through a pinch.
    if epsilon_abs:
        try:
            import pyvista as _pv2
            _orig = _pv2.read(str(stl_source)).triangulate()
            _t0 = time.perf_counter()
            V, _n_snap, _snap_max = _snap_boundary_to_surface(V, T, _orig)
            print(
                f"[fTetWild] boundary snapped onto the input surface: "
                f"{_n_snap:,} vertices (max move {_snap_max * 1e3:.3g} mm) "
                f"[{time.perf_counter() - _t0:.1f}s]",
                flush=True,
            )
        except Exception as _exc:  # noqa: BLE001 - keep the tet output
            print(
                f"[fTetWild] boundary snap unavailable ({_exc}); keeping "
                "the engine surface.",
                flush=True,
            )
    if n_splits:
        _nm, _bow = boundary_defects(V, T)
        _residual = (
            f"residual: {_nm} non-manifold edges, {_bow} bowtie vertices"
            if (_nm or _bow)
            else "boundary now fully manifold (every vertex star is a ball)"
        )
        print(
            f"boundary pinch repaired: {n_splits} vertex corner(s) "
            f"duplicated at non-manifold contact (fTetWild --manifold-surface "
            f"equivalent, micro-offset); {_residual}.",
            flush=True,
        )
    # Outlier micro-clusters (measured: a 1-cell region in the tet mesh at
    # a contact) become floating islands in the dual and "2 regions" in
    # checkMesh — drop them while they are still tets.
    V, T, n_dropped = drop_small_components(V, T)
    if n_dropped:
        print(
            f"dropped {n_dropped} outlier tet(s) in disconnected micro-"
            "component(s) (contact artifact; main mesh untouched).",
            flush=True,
        )

    # One entry per PATCH: sanitize operates on the unique class list so
    # $PhysicalNames stays one-name-per-id even though raw_names repeats
    # per triangle. names[i] is classes[i]'s sanitized twin (order
    # preserved), keeping element tags aligned with $PhysicalNames even
    # when two raw names sanitize onto one word (dedupe appends _2, ...).
    classes = _unique_keep_order(raw_names)
    names = sanitize_patch_names(classes)
    cls_idx = {nm: i for i, nm in enumerate(classes)}
    src_codes = np.array([cls_idx[nm] for nm in raw_names])
    class2phys = {cls: i + 1 for i, cls in enumerate(classes)}

    faces, owner = boundary_faces(V, T)
    faces = orient_faces_outward(V, T, faces, owner)
    codes, assign_stats = assign_patches(V, faces, src_tris, src_codes)
    face_phys = np.array([class2phys[classes[c]] for c in codes], dtype=np.int64)

    write_msh22(msh_path, V, T, faces, face_phys, names)

    total_s = time.perf_counter() - t_start
    return {
        "success": True,
        "path": str(msh_path),
        "names": names,
        "surface_time_s": round(surface_time_s, 3),
        "total_time_s": round(total_s, 3),
        "n_nodes": tet_stats["n_nodes"],
        "n_tets": tet_stats["n_tets"],
        "tet_time_s": tet_stats["tet_time_s"],
        "lr": lr,
        "la": la_used,
        "opt_iter": opt_iter,
        "epsilon": epsilon,
        "epsilon_abs": epsilon_abs,
        "eps_frac": eps_frac,
        "stop_energy": stop_energy,
        "disable_filtering": bool(disable_filtering),
        "simplify": bool(simplify),
        "target_edge_m": target_edge,
        "input_median_edge_m": med_edge,
        "bbox_diag_m": bbox_diag,
        "decimate_fraction": reduction,
        "subdivide_rounds": tet_stats.get("subdivide_rounds"),
        "n_input_tris": tet_stats.get("n_input_tris"),
        "n_decimated_tris": tet_stats.get("n_decimated_tris"),
        "n_subdivided_tris": tet_stats.get("n_subdivided_tris"),
        "n_manifold_splits": n_splits,
        "tagging": assign_stats,
    }


def _unique_keep_order(items) -> list:
    seen: set = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _cli_main(argv) -> None:
    """CLI entry point (same contract as gmsh_wrapper._cli_main)."""
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "volume":
        flags: dict[str, str] = {}
        for a in argv[2:]:
            if a.startswith("--") and "=" in a:
                k, v = a[2:].split("=", 1)
                flags[k] = v
        try:
            result = run_volume(
                step_path=flags.get("step", ""),
                msh_path=flags.get("msh", ""),
                detail=flags.get("detail", "medium"),
                max_cell=float(flags.get("max-cell", 0) or 0),
                lr=float(flags["lr"]) if flags.get("lr") else None,
                opt_iter=int(flags["opt-iter"]) if flags.get("opt-iter") else None,
                epsilon=float(flags["epsilon"]) if flags.get("epsilon") else None,
                epsilon_abs=(
                    float(flags["eps-mm"]) / 1000.0
                    if flags.get("eps-mm") else None
                ),
                eps_frac=(
                    float(flags["eps-frac"])
                    if flags.get("eps-frac") else None
                ),
                adaptive=not bool(flags.get("no-adaptive")),
                scale=(
                    float(flags["scale"]) if flags.get("scale") else 1.0
                ),
                stop_energy=float(flags["stop-energy"]) if flags.get("stop-energy") else None,
                disable_filtering=bool(flags.get("no-filtering")),
                simplify=bool(flags.get("simplify")),
            )
        except Exception as exc:  # noqa: BLE001 - CLI reports, never raises
            print(json.dumps({"success": False, "error": str(exc)}))
            sys.exit(1)
        print(json.dumps(result))
        return
    print(
        json.dumps(
            {
                "success": False,
                "error": f"unknown subcommand {cmd!r} (expected: volume)",
            }
        )
    )
    sys.exit(1)


if __name__ == "__main__":
    _cli_main(sys.argv)
