from __future__ import annotations

import logging
import re
import struct
from pathlib import Path

import numpy as np
import pyvista as pv
import trimesh
from PySide6.QtCore import QProcess, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from cfmesh_autogui.gui.task_runner import FunctionWorker, TaskManager

logger = logging.getLogger(__name__)

PATCH_COLORS = [
    (0.91, 0.30, 0.24),
    (0.18, 0.80, 0.44),
    (0.20, 0.60, 0.86),
    (0.95, 0.61, 0.07),
    (0.61, 0.35, 0.71),
    (0.10, 0.74, 0.80),
    (0.90, 0.40, 0.13),
    (0.16, 0.50, 0.73),
]

VIEW_MODES = ["CAD Surfaces", "Volume Mesh", "Surface + Edges"]

_MAX_CLIP_FACES = 50_000

BACKGROUNDS = {
    "White": ((1.0, 1.0, 1.0), "black"),
    "Dark": ((0.18, 0.18, 0.18), "white"),
    "Black": ((0.05, 0.05, 0.05), "white"),
    "ParaView": ((0.32, 0.34, 0.43), "white"),
}


def _trimesh_to_pydata(mesh: trimesh.Trimesh, color: tuple[float, float, float]) -> pv.PolyData:
    faces = np.hstack([np.full((len(mesh.faces), 1), 3), mesh.faces]).astype(np.int32)
    pd = pv.PolyData(mesh.vertices.astype(np.float64), faces)
    pd.cell_data["color"] = np.tile(color, (pd.n_cells, 1))
    return pd


def _strip_of_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _is_binary_of(path: Path) -> bool:
    """Check if an OpenFOAM file is in binary format (reads only header)."""
    from cfmesh_autogui.core.of_reader import _read_header_bytes
    raw = _read_header_bytes(path)
    return bool(re.search(rb"format\s+ binary\s*;", raw[:1024]))


def _read_of_block(path: Path) -> str:
    from cfmesh_autogui.core.of_reader import _read_of_bytes
    raw = _read_of_bytes(path)
    if _is_binary_of(path):
        return _read_of_block_binary(path, raw)
    text = _strip_of_comments(raw.decode("ascii", errors="replace"))
    match = re.search(r"\(\s*(.*)\s*\)", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Cannot parse OF block in {path}")
    return match.group(1)


def _read_of_block_binary(path: Path, raw: bytes) -> str:
    """Return ASCII representation of the data block in a binary OF file.
    
    For points: returns 'x1 y1 z1  x2 y2 z2 ...'
    For faces: returns 'n1 (v0 v1 ...)  n2 (v0 v1 ...) ...'
    """
    text_part = raw.decode("ascii", errors="replace")
    text_part = _strip_of_comments(text_part)
    # Binary data can contain stray byte 0x7D ('}'), so text_part.find("}")
    # is unreliable. Limit the search to the first 512 chars (safe header size).
    header_end = text_part[:512].rfind("}")
    if header_end < 0:
        header_end = text_part.find("}")
        if header_end < 0:
            raise ValueError(f"Cannot find FoamFile header end in {path}")
    body = raw[header_end + 1:] if header_end != -1 else raw
    m = re.search(rb"(\d+)\s*\(", body)
    if not m:
        raise ValueError(f"Cannot parse binary OF block header count in {path}")
    body_start = header_end + 1 + m.end()
    body_bytes = raw[body_start:] if body_start < len(raw) else b""
    # Determine type from class in header
    cls_match = re.search(r"class\s+(\w+);", text_part)
    cls = cls_match.group(1) if cls_match else ""
    if cls == "faceList":
        return _binary_faces_to_ascii(body_bytes)
    # Default: points, vectorField etc. - flat doubles. The element count
    # from the header is what bounds the stream; see _binary_doubles_to_ascii.
    return _binary_doubles_to_ascii(body_bytes, int(m.group(1)) * _OF_FIELD_WIDTH.get(cls, 1))


# Components per element, by FoamFile class. Anything unlisted is treated as
# scalar (1), which at worst reads fewer values than are present - never more.
_OF_FIELD_WIDTH = {
    "vectorField": 3,
    "pointField": 3,
    "scalarField": 1,
    "labelField": 1,
    "sphericalTensorField": 1,
    "symmTensorField": 6,
    "tensorField": 9,
}


def _binary_doubles_to_ascii(data: bytes, n_values: int | None = None) -> str:
    """Convert a binary float64 array to a space-separated ASCII string.

    ``n_values`` is how many doubles to read, taken from the element count in
    the FoamFile header. It is not a nicety: this function used to delimit the
    stream by scanning for the closing ``)``, which cannot work. Raw float64
    bytes are arbitrary, so 0x29 (the byte for ')') occurs constantly inside
    legitimate coordinates, and the data was truncated at the first one.

    Measured on a 16415-point cfMesh mesh (cartesianMesh, writeFormat binary):
    the scan cut the stream after 38 doubles, so _parse_of_points() raised
    "cannot reshape array of size 38 into shape (3)" and the caller turned
    that into a silent None - i.e. the viewer showed nothing at all for any
    binary mesh, which is the format the app writes by default.
    """
    pos = 0
    while pos < len(data) and data[pos:pos + 1] in (b'\n', b' ', b'\r'):
        pos += 1
    data = data[pos:]

    available = len(data) // 8
    if n_values is None or n_values <= 0 or n_values > available:
        # Header count missing or inconsistent with the file: fall back to
        # whatever is actually there rather than raising. Still never scans
        # for ')' - a short read beats a truncated one.
        if n_values:
            logger.debug(
                "Binary field claims %d values but only %d fit in %d bytes; "
                "reading what is present.", n_values, available, len(data),
            )
        n_values = available
    if n_values == 0:
        return ""
    values = struct.unpack(f"<{n_values}d", data[:n_values * 8])
    return " ".join(f"{v:.10g}" for v in values)


def _binary_faces_to_ascii(data: bytes) -> str:
    """Convert binary faceList to ASCII 'n (v0 v1 ...)' string.
    
    Binary face format: for each face, nVertices (int32) followed by
    nVertices vertex indices (int32). The first ( after the header
    is already consumed by the caller.
    """
    parts = []
    pos = 0
    while pos < len(data):
        # Skip whitespace/newlines
        while pos < len(data) and data[pos:pos+1] in (b'\n', b' ', b'\r'):
            pos += 1
        if pos >= len(data) or data[pos:pos+1] in (b')',):
            break
        # Check if it's an ASCII digit (mixed format)
        if 48 <= data[pos] <= 57:  # '0'-'9'
            end = pos
            while end < len(data) and 48 <= data[end] <= 57:
                end += 1
            n_verts = int(data[pos:end].decode("ascii"))
            pos = end
        else:
            # Binary int32 for face size
            n_verts = struct.unpack('<i', data[pos:pos+4])[0]
            pos += 4
        # Skip to '('
        while pos < len(data) and data[pos:pos+1] in (b'\n', b' ', b'\r'):
            pos += 1
        if pos < len(data) and data[pos:pos+1] == b'(':
            pos += 1
        # Read n_verts int32 indices
        n_bytes = n_verts * 4
        if pos + n_bytes > len(data):
            break
        verts = struct.unpack(f"<{n_verts}i", data[pos:pos+n_bytes])
        pos += n_bytes
        # Skip ')'
        while pos < len(data) and data[pos:pos+1] not in (b')',):
            pos += 1
        if pos < len(data):
            pos += 1
        parts.append(f"{n_verts} (" + " ".join(str(v) for v in verts) + ")")
    return "\n".join(parts)


def _parse_of_points(path: Path) -> np.ndarray:
    text = _read_of_block(path)
    # Each point is written "(x y z)" â€” np.fromstring can't tokenize the
    # parens (raises "unmatched data" on modern numpy), so strip them.
    # cfMesh meshes are normally too large for this manual-parser fallback
    # (they require foamToVTK instead), which is why this only surfaces on
    # small meshes such as autopoly's.
    text = text.replace("(", " ").replace(")", " ")
    arr = np.array(text.split(), dtype=np.float64)  # np.fromstring is deprecated
    return arr.reshape(-1, 3)


def _parse_of_faces(path: Path) -> list[list[int]]:
    text = _read_of_block(path)
    faces = []
    entry_re = re.compile(r"(\d+)\s*\(([^)]*)\)")
    for entry in entry_re.finditer(text):
        idx_str = entry.group(2)
        indices = list(map(int, idx_str.split()))
        faces.append(indices)
    return faces


# âœ… F-012: delegato a boundary_reader.parse_boundary (elimina duplicazione)
def _parse_boundary(path: Path) -> list[dict]:
    from cfmesh_autogui.core.boundary_reader import parse_boundary
    patches = parse_boundary(path)
    return [{"name": p.name, "nFaces": p.n_faces, "startFace": p.start_face} for p in patches]


# (_triangulate_face removed: its only caller was the manual boundary
# parser, which now emits the real polygons instead of fanning them into
# triangles — see read_openfoam_mesh_patches.)


# âœ… F-013: LRU cache con max 5 entries per evitare memory leak
_MESH_CACHE: dict[tuple[str, float], dict[str, pv.PolyData]] = {}
_MAX_CACHE_SIZE = 20

# Skip the slow manual parser when faces file exceeds this size (5 MB).
# Large polyhedral meshes crash/hang the GUI thread; foamToVTK is mandatory.
_MANUAL_PARSE_MAX_SIZE_BYTES = 5_000_000


def _cache_set(key: tuple[str, float], value: dict[str, pv.PolyData]) -> None:
    if len(_MESH_CACHE) >= _MAX_CACHE_SIZE:
        oldest = next(iter(_MESH_CACHE))
        del _MESH_CACHE[oldest]
    _MESH_CACHE[key] = value


def _human_size(n_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f}{unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f}TB"


def _poly_dir_state(poly_dir: Path, max_retries: int = 3, delay_ms: int = 500) -> float | None:
    """Check that all required polyMesh files exist and return newest mtime.

    Never sleeps on the calling thread: callers that need the WSL2 9P
    sync-delay retry (files written by cartesianMesh inside WSL may not be
    visible from Windows for a few hundred ms) schedule their own retries
    via QTimer â€” this function is a single non-blocking check.
    """
    files = ("points", "faces", "boundary")
    mtimes: list[float] = []
    all_exist = True
    for name in files:
        f = poly_dir / name
        if not f.exists():
            all_exist = False
            break
        try:
            mtimes.append(f.stat().st_mtime)
        except OSError:
            all_exist = False
            break
    if all_exist:
        return max(mtimes)
    return None


def _run_foamtovtk_async(case_dir: Path, vtk_subdir: str, state: float) -> Path | None:
    """Run foamToVTK via Popen with polling loop to keep UI responsive.

    Sleeps 100ms between polls so this function does NOT spin at 100% CPU,
    which froze the main thread on meshes where foamToVTK takes >30s.

    Shows a progress message in the viewer text during conversion.
    """
    import shlex
    import subprocess
    import time as _time

    from cfmesh_autogui.config import OFConfig

    cfg = OFConfig()
    linux_case = cfg._quoted_linux_path(case_dir)
    env_quoted = shlex.quote(cfg.env_script)
    cmd = cfg._build_wsl_cmd(
        f"source {env_quoted} 2>/dev/null; cd {linux_case} && "
        f"foamToVTK -constant -noZero -no-fields -overwrite -name {vtk_subdir}"
    )
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=4096,
    )
    deadline = _time.monotonic() + 180
    last_progress = 0.0
    while proc.poll() is None:
        elapsed = _time.monotonic() - (deadline - 180)
        if elapsed - last_progress > 5.0:
            logger.info("foamToVTK running for %.0fs...", elapsed)
            last_progress = elapsed
        if _time.monotonic() > deadline:
            proc.kill()
            proc.wait(5)
            logger.error("foamToVTK timed out after 180s")
            return None
        _time.sleep(0.1)
    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        stderr_detail = (stderr or stdout or "")[-500:]
        logger.warning("foamToVTK failed (rc=%d): %s", proc.returncode, stderr_detail)
        return None
    matches = list((case_dir / vtk_subdir).glob("*_0/internal.vtu"))
    if matches:
        try:
            (case_dir / vtk_subdir / ".source_mtime").write_text(str(state))
        except OSError:
            pass
        return matches[0]
    return None


def build_internal_volume_vtu(case_dir: Path) -> Path | None:
    """Run OpenFOAM's own `foamToVTK` to get the real internal volume cells.

    The "Volume Mesh" view used to only ever show the boundary *patches*
    (constant/polyMesh/boundary) â€” the outer surface, never the actual
    internal cells â€” because that's all `read_openfoam_mesh_patches` reads.
    cfMesh/cartesianMesh cells are polyhedral; meshio can't parse OpenFOAM
    or polyhedral VTU at all, but PyVista (built on VTK directly) reads
    them fine, so foamToVTK -> PyVista is the reliable path here.

    Cached under <case_dir>/VTK_view, regenerated only when polyMesh changed.
    Runs in a background task (the WSL2 call takes 30-60s).
    """
    case_dir = Path(case_dir)
    poly_dir = case_dir / "constant" / "polyMesh"
    state = _poly_dir_state(poly_dir)
    if state is None:
        return None

    vtk_subdir = "VTK_view"
    marker = case_dir / vtk_subdir / ".source_mtime"
    existing = list((case_dir / vtk_subdir).glob("*_0/internal.vtu")) if (case_dir / vtk_subdir).exists() else []
    if existing and marker.exists():
        try:
            if float(marker.read_text().strip()) == state:
                return existing[0]
        except (ValueError, OSError):
            pass

    return _run_foamtovtk_async(case_dir, vtk_subdir, state)


def read_openfoam_mesh_stats(case_dir: Path) -> dict:
    """Read mesh statistics from an OpenFOAM polyMesh directory.

    Uses nCells/nPoints/nFaces from the FoamFile header where available
    (fast path, reads only header bytes). Falls back to file-size-based
    estimation to avoid loading the full data on the GUI thread.
    """
    poly_dir = Path(case_dir) / "constant" / "polyMesh"
    stats = {"points": 0, "faces": 0, "cells": 0}

    # Fast path: read nCells from owner file header
    from cfmesh_autogui.core.of_reader import of_list_count, of_ncells_from_header
    owner_path = poly_dir / "owner"
    if owner_path.exists():
        n = of_ncells_from_header(owner_path)
        if n is not None:
            stats["cells"] = n

    # Use of_list_count which reads only the header
    points_path = poly_dir / "points"
    if points_path.exists():
        stats["points"] = of_list_count(points_path)

    faces_path = poly_dir / "faces"
    if faces_path.exists():
        stats["faces"] = of_list_count(faces_path)

    return stats


def _load_vtk_patches(case_dir: Path, vtk_subdir: str) -> dict[str, pv.PolyData] | None:
    """Load boundary patches from foamToVTK-generated VTU files (fast path)."""
    vtk_dir = case_dir / vtk_subdir
    time_dirs = sorted(vtk_dir.glob("*_0")) if vtk_dir.exists() else []
    if not time_dirs:
        return None
    boundary_dir = time_dirs[0] / "boundary"
    if not boundary_dir.is_dir():
        return None
    result: dict[str, pv.PolyData] = {}
    # foamToVTK writes each boundary patch as 2D surface data (.vtp,
    # PolyData) â€” searching for "*.vtu" (volumetric UnstructuredGrid) never
    # matched anything, so this fast path silently always returned None and
    # every mesh fell through to the manual ASCII parser, which bails with
    # "too large" on anything but tiny cases (root cause of the integrated
    # viewer showing "mesh too large, use ParaView" instead of the mesh).
    boundary_files = sorted(boundary_dir.glob("*.vtp")) + sorted(boundary_dir.glob("*.vtu"))
    for i, vtu in enumerate(boundary_files):
        try:
            pd = pv.read(str(vtu))
            name = vtu.stem
            color = PATCH_COLORS[i % len(PATCH_COLORS)]
            pd.cell_data["color"] = np.tile(color, (pd.n_cells, 1))
            result[name] = pd
        except Exception:
            continue
    return result or None


def read_openfoam_mesh_patches(case_dir: Path | str) -> dict[str, pv.PolyData] | None:
    case_dir = Path(case_dir)
    poly_dir = case_dir / "constant" / "polyMesh"
    state = _poly_dir_state(poly_dir)
    if state is None:
        logger.warning(
            "read_openfoam_mesh_patches: polyMesh state is None for %s "
            "(files may not have synced from WSL2 yet)",
            poly_dir,
        )
        return None
    cache_key = (str(case_dir), state)
    cached = _MESH_CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Mesh patches loaded from cache (%d patches)", len(cached))
        return cached

    # Fast path: try loading from foamToVTK-generated VTU files
    vtk_patches = _load_vtk_patches(case_dir, "VTK_view")
    if vtk_patches is not None:
        logger.info("Loaded %d mesh patches from foamToVTK VTU", len(vtk_patches))
        _cache_set(cache_key, vtk_patches)
        return vtk_patches

    # Slow path: manual parsing of OpenFOAM binary files (for small meshes
    # where foamToVTK hasn't been run yet â€” cfMesh polyhedral meshes with
    # 3.5M+ cells are NOT reliably parsed here; foamToVTK is required).
    faces_path = poly_dir / "faces"
    if faces_path.exists() and faces_path.stat().st_size > _MANUAL_PARSE_MAX_SIZE_BYTES:
        logger.info(
            "Faces file too large (%s), skipping manual parser â€” foamToVTK required",
            _human_size(faces_path.stat().st_size),
        )
        return None

    logger.info("VTU not found, falling back to manual OF parser for %s", poly_dir)
    try:
        points = _parse_of_points(poly_dir / "points")
        all_faces = _parse_of_faces(poly_dir / "faces")
        patches = _parse_boundary(poly_dir / "boundary")
    except (ValueError, IndexError, OSError, MemoryError) as exc:
        # This used to say the failure was "normal for large binary cfMesh
        # meshes". It was not: size was never the discriminator. Measured on
        # one mesh written both ways, the 938 KB BINARY faces file failed
        # while the 1.0 MB ASCII one parsed fine - the larger file was the
        # one that worked, and both were far below the 5 MB cutoff above.
        # The real cause was the ')'-scan truncation fixed in
        # _binary_doubles_to_ascii. Blaming size sent anyone reading this log
        # looking in the wrong place, so say only what is actually known.
        logger.warning(
            "Manual mesh parsing failed for %s: %s. Falling back to "
            "foamToVTK; run it for this case if the viewer stays empty.",
            poly_dir, exc,
        )
        return None
    result: dict[str, pv.PolyData] = {}
    for i, p in enumerate(patches):
        start = p["startFace"]
        end = start + p["nFaces"]
        patch_faces = all_faces[start:end]
        total_verts = sum(len(f) for f in patch_faces)
        if total_verts == 0:
            continue
        # Emit each boundary face as the REAL polygon it is, not as a fan of
        # triangles. This used to triangulate everything (a quad -> 2 tris,
        # an n-gon -> n-2 tris), which is exactly what made a 100%
        # polyhedral mesh look like a tetrahedral one in the viewer: the
        # barycentric dual's boundary is quads (three per original boundary
        # triangle), and after the fan split the user saw only their
        # diagonals. PyVista/VTK take a variable-size face stream —
        # [n, v0..vn-1, m, w0..wm-1, ...] — so no triangulation is needed at
        # all, and `show_edges` then outlines the true cell boundaries.
        verts_arr = np.empty((total_verts, 3), dtype=np.float64)
        # one size prefix per face + one index per vertex
        faces_pv = np.empty(len(patch_faces) + total_verts, dtype=np.int32)
        v_offset = 0
        f_offset = 0
        for fi, face_indices in enumerate(patch_faces):
            n = len(face_indices)
            try:
                verts_arr[v_offset:v_offset + n] = points[face_indices]
            except IndexError:
                logger.warning("Patch '%s' face %d: index out of bounds (points=%d, max_idx=%d). Mesh parsing incomplete.",
                               p["name"], fi, len(points), max(face_indices))
                return None
            faces_pv[f_offset] = n
            faces_pv[f_offset + 1:f_offset + 1 + n] = np.arange(
                v_offset, v_offset + n, dtype=np.int32,
            )
            f_offset += n + 1
            v_offset += n
        pd = pv.PolyData(verts_arr, faces_pv)
        color = PATCH_COLORS[i % len(PATCH_COLORS)]
        pd.cell_data["color"] = np.tile(color, (pd.n_cells, 1))
        result[p["name"]] = pd
    _cache_set(cache_key, result)  # âœ… F-013
    return result


class ViewerWidget(QWidget):
    face_picked = Signal(str)
    distance_measured = Signal(str)
    refinement_point_picked = Signal(float, float, float)
    refinement_boxes_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cad_meshes: list[trimesh.Trimesh] = []
        self._mesh_case_dir: str = ""
        self._bg_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._refinement_pick_active: bool = False
        self._text_color: str = "black"
        self._plotter = None
        self._plotter_ready: bool = False
        self._pvdata_to_name: dict[str, str] = {}
        self._section_enabled: bool = False
        self._measure_points: list = []
        self._measure_actors: list = []
        self._highlighted_actor = None
        self._mesh_display_in_progress: bool = False
        self._mesh_retry_count: int = 0
        self._mesh_retry_max: int = 3
        self._foam_to_vtk_attempted: bool = False
        self._display_timeout_timer: QTimer | None = None
        # Background tasks for heavy loads (foamToVTK, VTU reads) so the
        # UI never freezes while the mesh display pipeline runs.
        self._vtk_tasks = TaskManager(self)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)

        toolbar.addWidget(QLabel("Display:"))
        self._view_selector = QComboBox()
        self._view_selector.addItems(VIEW_MODES)
        self._view_selector.setCurrentIndex(-1)
        self._view_selector.setEnabled(False)
        self._view_selector.setToolTip("Switch between CAD surfaces, volume mesh (wireframe), or surface with edges.")
        self._view_selector.currentIndexChanged.connect(self._on_view_changed)
        toolbar.addWidget(self._view_selector)

        toolbar.addSpacing(12)

        toolbar.addWidget(QLabel("Background:"))
        self._bg_selector = QComboBox()
        self._bg_selector.addItems(list(BACKGROUNDS.keys()))
        self._bg_selector.setCurrentText("Dark")
        self._bg_selector.setToolTip("Change the viewer background color.")
        self._bg_selector.currentTextChanged.connect(self._on_bg_changed)
        toolbar.addWidget(self._bg_selector)

        toolbar.addSpacing(12)

        self._section_check = QCheckBox("Section Cut")
        self._section_check.setToolTip("Toggle interactive clipping plane")
        self._section_check.toggled.connect(self._on_section_toggled)
        toolbar.addWidget(self._section_check)

        toolbar.addSpacing(8)

        self._pick_check = QCheckBox("Select Patch")
        self._pick_check.setToolTip("Toggle face selection mode. When ON, click a patch to rename it.")
        self._pick_check.toggled.connect(self._on_pick_toggled)
        toolbar.addWidget(self._pick_check)

        self._measure_check = QCheckBox("Measure")
        self._measure_check.setToolTip("Click two points to measure distance between them.")
        self._measure_check.toggled.connect(self._on_measure_toggled)
        toolbar.addWidget(self._measure_check)

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "font-size: 11px; padding: 2px 8px; background: rgba(0,0,0,30); border-radius: 3px;"
        )
        toolbar.addWidget(self._stats_label)

        toolbar.addStretch()
        self._layout.addLayout(toolbar)

        QTimer.singleShot(0, self._init_vtk)

    def _init_vtk(self):
        if self._plotter is not None:
            return

        # Quick pre-check: try importing VTK rendering modules.
        # If the import fails, skip QtInteractor entirely.
        try:
            import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
        except ImportError as exc:
            logger.warning("VTK/OpenGL not available, 3D viewer disabled: %s", exc)
            self._add_no_viewer_label()
            return

        from pyvistaqt import QtInteractor
        try:
            self._plotter = QtInteractor(parent=self, auto_update=30.0)
            self._layout.addWidget(self._plotter)
            self._plotter_ready = True
            self._apply_background(self._bg_selector.currentText())
        except Exception as exc:
            logger.error("Failed to initialize VTK plotter: %s", exc)
            self._add_no_viewer_label(f"Errore: {exc}")

    def _add_no_viewer_label(self, msg: str = ""):
        """Add a label explaining that 3D viewer is unavailable."""
        self._plotter = None
        self._plotter_ready = False
        if not msg:
            msg = "3D viewer non disponibile.\nUsa Strumenti > Launch ParaView."
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(lbl)

    def _apply_background(self, name: str):
        if self._plotter is None:
            return
        bg, _ = BACKGROUNDS[name]
        self._bg_color = bg
        self._plotter.set_background(bg)
        self._plotter.show_axes()
        self._plotter.render()
        self._redisplay_current()

    def _on_bg_changed(self, text: str):
        self._apply_background(text)
        from PySide6.QtCore import QSettings
        QSettings("cfmesh-autogui", "CFMesh-AutoGUI").setValue("viewer/background", text)

    def save_background(self, s):
        s.setValue("viewer/background", self._bg_selector.currentText())

    def restore_background(self, s):
        bg = s.value("viewer/background", "Dark")
        if bg in BACKGROUNDS:
            self._bg_selector.setCurrentText(bg)
            self._apply_background(bg)

    def _is_dark_bg(self) -> bool:
        return not all(c > 0.5 for c in self._bg_color)

    def _edge_color(self) -> str:
        return "white" if self._is_dark_bg() else "gray"

    def highlight_patch(self, patch_name: str):
        """Highlight a picked patch by changing its color in the display."""
        if self._plotter is None:
            return
        self._plotter.clear()
        ec = self._edge_color()
        self._pvdata_to_name.clear()
        for i, mesh in enumerate(self._cad_meshes):
            name = mesh.metadata.get("name", f"patch_{i}")
            color = PATCH_COLORS[i % len(PATCH_COLORS)]
            if name == patch_name:
                color = (1.0, 0.84, 0.0)
            pd = _trimesh_to_pydata(mesh, color)
            actor = self._plotter.add_mesh(
                pd, scalars="color", rgb=True, show_edges=True,
                edge_color=ec, label=name,
            )
            self._pvdata_to_name[actor.GetAddressAsString("")] = name
        self._plotter.view_isometric()
        self._plotter.render()

    def _on_pick_toggled(self, checked: bool):
        if not self._plotter_ready:
            return
        if checked:
            self._measure_check.blockSignals(True)
            self._measure_check.setChecked(False)
            self._measure_check.blockSignals(False)
            self._clear_measure_actors()
            try:
                self._plotter.enable_mesh_picking(
                    callback=self._on_mesh_picked,
                    show=False,
                    show_message=False,
                    left_clicking=True,
                    use_actor=True,
                )
            except Exception as exc:
                logger.warning("enable_mesh_picking failed: %s", exc)
        else:
            try:
                self._plotter.disable_picking()
            except Exception:
                pass

    def _on_measure_toggled(self, checked: bool):
        if not self._plotter_ready:
            return
        if checked:
            self._pick_check.blockSignals(True)
            self._pick_check.setChecked(False)
            self._pick_check.blockSignals(False)
            self._measure_points = []
            self._clear_measure_actors()
            try:
                self._plotter.enable_point_picking(
                    callback=self._on_point_picked,
                    show_message=False,
                    color="red",
                    point_size=10,
                    use_marker=True,
                    left_clicking=True,
                )
            except Exception as exc:
                logger.warning("enable_point_picking failed: %s", exc)
                self._measure_check.blockSignals(True)
                self._measure_check.setChecked(False)
                self._measure_check.blockSignals(False)
        else:
            self._measure_points = []
            self._clear_measure_actors()
            try:
                self._plotter.disable_picking()
            except Exception:
                pass

    def _on_point_picked(self, point):
        import numpy as np
        pt = np.array(point[:3])
        self._measure_points.append(pt)
        if self._plotter is None:
            return
        sphere = self._plotter.add_mesh(
            pv.Sphere(radius=(getattr(self._cad_meshes[0], 'scale', 1.0) if self._cad_meshes else 1.0) * 0.01,  # âœ… F-010
                      center=pt),
            color="red",
        )
        self._measure_actors.append(sphere)
        if len(self._measure_points) == 2:
            p1, p2 = self._measure_points
            dist = float(np.linalg.norm(p2 - p1))
            line = self._plotter.add_lines(
                np.array([p1, p2]), color="yellow", width=2,
            )
            self._measure_actors.append(line)
            label = self._plotter.add_text(
                f"{dist:.4f} m",
                position="upper_right",
                font_size=14,
                color="yellow",
            )
            self._measure_actors.append(label)
            self.distance_measured.emit(f"Distance: {dist:.4f} m")
            self._measure_points = []

    def _clear_measure_actors(self):
        if self._plotter:
            for actor in self._measure_actors:
                try:
                    self._plotter.remove_actor(actor)
                except Exception:
                    pass
        self._measure_actors = []

    def start_refinement_pick(self):
        """Enter refinement-pick mode: click a point on the mesh to set
        the centre of a manual refinement zone.  Emits
        ``refinement_point_picked(x, y, z)`` on first pick, then
        automatically exits pick mode."""
        if self._plotter is None or not self._plotter_ready:
            return
        self._refinement_pick_active = True
        # Disable other pick modes
        self._pick_check.blockSignals(True)
        self._pick_check.setChecked(False)
        self._pick_check.blockSignals(False)
        self._measure_check.blockSignals(True)
        self._measure_check.setChecked(False)
        self._measure_check.blockSignals(False)
        self._clear_measure_actors()
        try:
            self._plotter.enable_point_picking(
                callback=self._on_refinement_point_picked,
                show_message=False,
                color="cyan",
                point_size=12,
                use_marker=True,
                picker="point",
            )
        except Exception as exc:
            logger.warning("start_refinement_pick failed: %s", exc)
            self._refinement_pick_active = False

    def _on_refinement_point_picked(self, point):
        """Handle a point pick in refinement mode: emit coordinates and exit."""
        import numpy as np
        pt = np.array(point[:3])
        # Show a visual marker at the picked point
        if self._plotter and self._cad_meshes:
            r = max(getattr(self._cad_meshes[0], 'scale', 1.0) * 0.02, 0.005)
            self._plotter.add_mesh(
                pv.Sphere(radius=r, center=pt),
                color="cyan", opacity=0.7,
            )
            self._plotter.render()
        self._refinement_pick_active = False
        try:
            self._plotter.disable_picking()
        except Exception:
            pass
        self.refinement_point_picked.emit(float(pt[0]), float(pt[1]), float(pt[2]))

    def show_refinement_zones(self, zones: list):
        """Display detected refinement zones as semi-transparent cyan spheres."""
        if self._plotter is None or not self._cad_meshes:
            return
        # Remove previous refinement zone actors
        for attr in ("_refinement_zone_actors",):
            for actor in getattr(self, attr, []):
                try:
                    self._plotter.remove_actor(actor)
                except Exception:
                    pass
        self._refinement_zone_actors = []
        for z in zones:
            cx, cy, cz = z.centre
            r = z.radius
            sphere = self._plotter.add_mesh(
                pv.Sphere(radius=r, center=(cx, cy, cz)),
                color="cyan", opacity=0.25, style="wireframe",
            )
            self._refinement_zone_actors.append(sphere)
        if zones:
            self._plotter.render()

    def _on_mesh_picked(self, actor):
        # use_actor=True (set in _on_pick_toggled) means `actor` is the
        # picked vtkActor. Matching by the *mesh's* memory_address used to
        # be used here, but PyVista internally copies/repacks the polydata
        # when rendering with `scalars=..., rgb=True` (as _display_cad and
        # highlight_patch always do) â€” the mapper's input dataset ends up
        # with a DIFFERENT address than the pv.PolyData we built, so that
        # lookup never matched and Select Patch silently did nothing.
        # The actor object itself is never copied, so match on that instead.
        if actor is None:
            actor = self._plotter.picked_actor if self._plotter else None
        if actor is None:
            return
        addr = actor.GetAddressAsString("")
        name = self._pvdata_to_name.get(addr)
        if name is not None:
            logger.info("Picked patch '%s'", name)
            self.face_picked.emit(name)
        else:
            logger.debug("Picked actor addr=%s not in mapping", addr)

    def _on_view_changed(self, index: int):
        if index < 0 or not self._plotter_ready:
            return
        try:
            self._plotter.disable_picking()
            self._pick_check.blockSignals(True)
            self._pick_check.setChecked(False)
            self._pick_check.blockSignals(False)
        except Exception:
            pass
        self._view_selector.blockSignals(True)
        try:
            mode = VIEW_MODES[index]
            if mode == "CAD Surfaces":
                self._display_cad()
            elif mode == "Volume Mesh":
                self._display_mesh()
            elif mode == "Surface + Edges":
                self._display_surface_edges()
        finally:
            self._view_selector.blockSignals(False)

    def _redisplay_current(self):
        if self._plotter is None:
            return
        idx = self._view_selector.currentIndex()
        if idx >= 0:
            self._on_view_changed(idx)

    def _on_section_toggled(self, checked: bool):
        self._section_enabled = checked
        if self._plotter is None or not self._plotter_ready:
            return
        total_faces = sum(len(m.faces) for m in self._cad_meshes)
        if self._section_enabled and total_faces > _MAX_CLIP_FACES:
            self._log_clip_too_big(total_faces)
            self._section_check.blockSignals(True)
            self._section_check.setChecked(False)
            self._section_check.blockSignals(False)
            self._section_enabled = False
            return
        self._redisplay_current()

    def _log_clip_too_big(self, n_faces: int) -> None:
        logger.warning(
            "Section Cut disabled: geometry has %d faces (cap=%d). "
            "Decimate the CAD or remove internal patches to enable.",
            n_faces, _MAX_CLIP_FACES,
        )

    def _build_combined_pvdata(self) -> pv.PolyData | None:
        if not self._cad_meshes:
            return None
        all_verts: list[np.ndarray] = []
        all_faces: list[np.ndarray] = []
        all_colors: list[np.ndarray] = []
        v_offset = 0
        for i, mesh in enumerate(self._cad_meshes):
            color = PATCH_COLORS[i % len(PATCH_COLORS)]
            nv = len(mesh.vertices)
            nf = len(mesh.faces)
            all_verts.append(mesh.vertices)
            all_faces.append(mesh.faces + v_offset)
            v_offset += nv
            all_colors.append(np.tile(color, (nf, 1)))
        if not all_verts:
            return None
        verts_arr = np.vstack(all_verts).astype(np.float64)
        faces_arr = np.vstack(all_faces).astype(np.int32)
        colors_arr = np.vstack(all_colors).astype(np.float64)
        faces_pv = np.hstack([np.full((len(faces_arr), 1), 3), faces_arr]).astype(np.int32)
        pd = pv.PolyData(verts_arr, faces_pv)
        pd.cell_data["color"] = colors_arr
        return pd

    def _display_cad(self):
        if self._plotter is None or not self._cad_meshes:
            return
        self._plotter.clear()
        ec = self._edge_color()

        if self._section_enabled:
            combined = self._build_combined_pvdata()
            if combined is not None:
                # add_mesh_clip_box() in the installed PyVista version has no
                # `bounds` kwarg â€” passing one (as this used to) raises a
                # TypeError on every toggle, silently killing Section Cut.
                # Omit it: this places a full, draggable box widget the user
                # can resize/move to clip interactively, matching the
                # "interactive clipping plane" tooltip.
                self._plotter.add_mesh_clip_box(
                    combined,
                    scalars="color",
                    rgb=True,
                    show_edges=True,
                    edge_color=ec,
                    crinkle=False,
                )
            self._plotter.view_isometric()
            self._plotter.render()
            return

        self._pvdata_to_name.clear()
        for i, mesh in enumerate(self._cad_meshes):
            name = mesh.metadata.get("name", f"patch_{i}")
            color = PATCH_COLORS[i % len(PATCH_COLORS)]
            pd = _trimesh_to_pydata(mesh, color)
            actor = self._plotter.add_mesh(
                pd, scalars="color", rgb=True, show_edges=True,
                edge_color=ec, label=name,
            )
            self._pvdata_to_name[actor.GetAddressAsString("")] = name
        self._plotter.view_isometric()
        self._plotter.render()

    def _cancel_vtk_process(self):
        """Kill any running foamToVTK QProcess and its timeout timer.

        Uses a short timeout to avoid blocking the main thread.
        """
        if hasattr(self, "_vtk_timeout_timer") and self._vtk_timeout_timer:
            self._vtk_timeout_timer.stop()
            self._vtk_timeout_timer.deleteLater()
            self._vtk_timeout_timer = None
        if hasattr(self, "_vtk_process") and self._vtk_process:
            if self._vtk_process.state() != QProcess.NotRunning:
                self._vtk_process.kill()
                self._vtk_process.waitForFinished(500)
            self._vtk_process.deleteLater()
            self._vtk_process = None

    def cancel_vtk_process(self):
        """Public alias used by the main window's Cancel handler."""
        self._cancel_vtk_process()

    def _start_vtk_qprocess(self, case_dir: Path, vtk_subdir: str,
                             on_finished: callable) -> None:
        """Start foamToVTK via QProcess. Calls ``on_finished(ok)`` when done."""
        self._cancel_vtk_process()
        # Show status in label instead of rendering to the plotter
        self._stats_label.setText("Generating foamToVTK... (attendi 1-3 min)")
        from cfmesh_autogui.config import OFConfig
        cfg = OFConfig()
        linux_case = cfg._quoted_linux_path(case_dir)
        import shlex
        env_quoted = shlex.quote(cfg.env_script)
        cmd = cfg._build_wsl_cmd(
            f"source {env_quoted} 2>/dev/null; cd {linux_case} && "
            f"foamToVTK -constant -noZero -no-fields -overwrite -name {vtk_subdir}"
        )
        self._vtk_process = QProcess(self)
        # Use separate channels so stderr is readable independently
        self._vtk_process.setProcessChannelMode(QProcess.SeparateChannels)
        self._vtk_timeout_fired = False
        self._vtk_process.finished.connect(
            lambda ec, _exit_status: self._on_vtk_finished(
                ec, case_dir, vtk_subdir, on_finished,
            ),
        )
        self._vtk_process.started.connect(lambda: logger.debug("foamToVTK QProcess started"))
        program = cmd[0]
        args = cmd[1:]
        self._vtk_process.start(program, args)
        self._vtk_timeout_timer = QTimer(self)
        self._vtk_timeout_timer.setSingleShot(True)
        self._vtk_timeout_timer.timeout.connect(self._on_vtk_timeout)
        self._vtk_timeout_timer.start(180000)

    def _on_vtk_timeout(self):
        logger.error("foamToVTK QProcess timed out after 180s")
        self._vtk_timeout_fired = True
        self._mesh_display_in_progress = False
        self._cancel_vtk_process()
        self._show_mesh_too_large("foamToVTK timed out (>180s)")

    def _on_vtk_finished(self, exit_code: int, case_dir: Path,
                          vtk_subdir: str, on_finished: callable) -> None:
        if self._vtk_timeout_timer:
            self._vtk_timeout_timer.stop()
        if getattr(self, '_vtk_timeout_fired', False):
            # Timeout already handled cleanup â€” skip stale callback
            return
        if exit_code == 0:
            matches = list((case_dir / vtk_subdir).glob("*_0/internal.vtu"))
            if matches:
                poly_dir = case_dir / "constant" / "polyMesh"
                state = _poly_dir_state(poly_dir)
                if state is not None:
                    try:
                        (case_dir / vtk_subdir / ".source_mtime").write_text(str(state))
                    except OSError:
                        pass
        else:
            try:
                stderr = self._vtk_process.readAllStandardError() if self._vtk_process else b""
                stderr_text = stderr.decode("utf-8", errors="replace")[-300:] if stderr else ""
            except RuntimeError:
                stderr_text = "(process already deleted)"
            logger.warning("foamToVTK QProcess failed (rc=%d): %s", exit_code, stderr_text)
        on_finished(exit_code == 0)

    def _cancel_display_timeout(self):
        """Stop the display timeout timer if running."""
        if self._display_timeout_timer is not None:
            self._display_timeout_timer.stop()
            self._display_timeout_timer.deleteLater()
            self._display_timeout_timer = None

    def _start_display_timeout(self, seconds: int = 90):
        """Start a timeout that shows the 'use ParaView' message if the
        mesh display pipeline takes longer than ``seconds``."""
        self._cancel_display_timeout()
        self._display_timeout_timer = QTimer(self)
        self._display_timeout_timer.setSingleShot(True)
        self._display_timeout_timer.timeout.connect(
            lambda: self._on_display_timeout(),
        )
        self._display_timeout_timer.start(seconds * 1000)

    def _on_display_timeout(self):
        """Called when the mesh display pipeline times out."""
        logger.warning("Mesh display timed out after 90s â€” showing fallback message")
        self._mesh_display_in_progress = False
        self._view_selector.setEnabled(False)
        self._cancel_vtk_process()
        self._show_mesh_too_large("Display pipeline timed out (>90s)")

    def _display_mesh(self):
        """Render the mesh (foamToVTK + PyVista), guarded by a 90s safety
        timeout that falls back to the 'use ParaView' message if the
        pipeline hangs (e.g. OpenGL issues on this machine)."""
        if self._plotter is None or not self._mesh_case_dir:
            return
        if getattr(self, "_mesh_display_in_progress", False):
            logger.debug("_display_mesh already in progress â€” skipping duplicate call")
            return
        self._mesh_display_in_progress = True
        if self._section_enabled:
            self._display_mesh_section()
            self._mesh_display_in_progress = False
            return
        # Show "Loading..." immediately, then chain async operations
        try:
            self._plotter.clear()
            self._plotter.add_text(
                "Loading mesh...",
                color=self._text_color, font_size=14,
            )
            self._plotter.render()
        except Exception as exc:
            logger.error("Plotter failed to show loading message: %s", exc)

        self._start_display_timeout(90)
        QTimer.singleShot(0, self._step_vtu_for_mesh)

    def _display_surface_edges(self):
        """Render the mesh with filled surfaces and visible edges (like
        ParaView 'Surface with Edges'). Uses the internal volume VTU
        with style='surface' and show_edges=True."""
        if self._plotter is None or not self._mesh_case_dir:
            return
        if getattr(self, "_mesh_display_in_progress", False):
            logger.debug("_display_surface_edges already in progress â€” skipping")
            return
        self._mesh_display_in_progress = True
        # Show "Loading..." immediately
        try:
            self._plotter.clear()
            self._plotter.add_text(
                "Loading Surface + Edges...",
                color=self._text_color, font_size=14,
            )
            self._plotter.render()
        except Exception as exc:
            logger.error("Plotter failed to show loading message: %s", exc)

        self._start_display_timeout(90)
        QTimer.singleShot(0, self._step_vtu_for_surface_edges)

    def _step_vtu_for_surface_edges(self):
        """Step 1: ensure VTU exists, then load with surface+edges style."""
        self._do_step_vtu(
            on_vtu_ready=lambda ok: QTimer.singleShot(0, self._do_load_surface_edges),
        )

    def _do_load_surface_edges(self, _unused: bool = False):
        """Load the internal volume mesh and render as surface with edges."""
        self._mesh_display_in_progress = False
        self._cancel_display_timeout()
        case_dir = Path(self._mesh_case_dir) if self._mesh_case_dir else None
        if case_dir is None:
            return

        # Try internal volume VTU first
        vtk_dir = case_dir / "VTK_view"
        internal_vtu = None
        if vtk_dir.exists():
            candidates = sorted(vtk_dir.glob("*_0/internal.vtu"))
            if candidates:
                internal_vtu = candidates[0]

        if internal_vtu is not None:
            # Heavy read + decimate in a background task; render on GUI thread.
            self._load_internal_vtu_async(internal_vtu, "surface_edges")
            return

        # Fallback: boundary patches with edges
        self._load_patches_async()

    def _display_mesh_section(self):
        self._plotter.clear()
        self._plotter.add_text(
            "Loading mesh for section cut...",
            color=self._text_color, font_size=12,
        )
        self._plotter.render()

        QTimer.singleShot(0, self._step_vtu_for_section)

    def _step_vtu_for_mesh(self):
        """Step 1: ensure VTU exists (async QProcess if needed), then load patches."""
        self._do_step_vtu(
            on_vtu_ready=lambda ok: QTimer.singleShot(0, self._step_mesh_patches),
        )

    def _step_vtu_for_section(self):
        """Step 1: ensure VTU exists (async QProcess if needed), then show section."""
        self._do_step_vtu(
            on_vtu_ready=lambda ok: QTimer.singleShot(0, self._step_section_display),
        )

    def _do_step_vtu(self, on_vtu_ready: callable) -> None:
        """Check VTU cache; if stale/missing, start QProcess, else callback immediately.

        If polyMesh files aren't visible yet (WSL2 9P sync delay), retries
        with increasing delay up to 30s before giving up.
        """
        if self._plotter is None or not self._mesh_case_dir:
            return
        case_dir = Path(self._mesh_case_dir)
        poly_dir = case_dir / "constant" / "polyMesh"
        # Use a shorter timeout here to avoid hanging the display pipeline
        state = _poly_dir_state(poly_dir, max_retries=3, delay_ms=2000)
        if state is None:
            # Poly files not found yet; try again later or show loading message
            logger.info("polyMesh files not found yet in %s â€” will retry via _do_load_patches", poly_dir)
            on_vtu_ready(False)
            return
        vtk_subdir = "VTK_view"
        marker = case_dir / vtk_subdir / ".source_mtime"
        existing = list((case_dir / vtk_subdir).glob("*_0/internal.vtu")) if (case_dir / vtk_subdir).exists() else []
        if existing and marker.exists():
            try:
                if float(marker.read_text().strip()) == state:
                    on_vtu_ready(True)
                    return
            except (ValueError, OSError):
                pass
        # If foamToVTK is already running, just attach the callback â€” don't
        # kill and restart (that would waste 30-60s of progress).
        vtk_proc = getattr(self, "_vtk_process", None)
        if vtk_proc and vtk_proc.state() != QProcess.NotRunning:
            logger.debug("foamToVTK already running â€” attaching callback to existing process")
            # Disconnect old finished signal and reconnect with new callback
            try:
                vtk_proc.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            vtk_proc.finished.connect(
                lambda ec, _exit_status: self._on_vtk_finished(
                    ec, case_dir, vtk_subdir, on_vtu_ready,
                ),
            )
            return
        logger.info("Starting foamToVTK for %s", case_dir)
        self._start_vtk_qprocess(case_dir, vtk_subdir, on_vtu_ready)

    def _step_mesh_patches(self):
        """Step 2: load mesh patches via fast path (VTU already generated) and display."""
        if self._plotter is None or not self._mesh_case_dir:
            return
        QTimer.singleShot(0, lambda: self._do_load_patches(False))

    def _step_section_display(self):
        """Step 2: load internal volume grid and show section cut."""
        if self._plotter is None:
            return
        QTimer.singleShot(0, self._do_show_section)

    def _do_show_section(self):
        if self._plotter is None:
            return
        # Heavy load (foamToVTK + VTU read) runs in a background task.
        self._load_section_async()

    @staticmethod
    def _prepare_internal_vtu_grid(grid, mode: str):
        """Reduce the full-volume grid read from internal.vtu to what the
        given view mode should actually render.

        "surface_edges" ("Surface + Edges") must show ONLY the outer
        boundary of the poly mesh — not every internal cell edge of the
        whole volume. Before this, both "Volume Mesh" and "Surface + Edges"
        loaded the SAME full internal.vtu and wireframed the entire volume,
        so a poly mesh with any real cell count looked like solid static
        from outside: every internal edge of every cell was superimposed in
        screen space, regardless of how well-graded the mesh actually was.
        extract_surface() on an UnstructuredGrid is VTK's vtkGeometryFilter
        — it keeps the true outer polygon faces (a poly cell's pentagon
        face stays a pentagon) without triangulating them, so the
        "style=wireframe to avoid looking like tet" rendering trick still
        applies, now on just the boundary.

        "internal" ("Volume Mesh") is returned unchanged — it exists
        specifically to inspect internal structure.

        algorithm="dataset_surface" is pinned explicitly: PyVista warns
        that the default will change in a future release, and a silent
        behaviour change here would be exactly the kind of "why does the
        mesh look different now" regression this fix exists to prevent.
        """
        if mode == "surface_edges":
            return grid.extract_surface(algorithm="dataset_surface")
        return grid

    def _load_internal_vtu_async(self, vtu_path: Path, mode: str) -> None:
        """Read + optionally decimate a (possibly huge) internal.vtu in a
        background task; render on the GUI thread when ready."""
        def work(worker):
            grid = pv.read(str(vtu_path))
            grid = self._prepare_internal_vtu_grid(grid, mode)
            n_cells = grid.n_cells
            show_dec = n_cells > self.DECIMATE_THRESHOLD
            if show_dec:
                # Cap the rendered surface so the GPU/OpenGL render on the
                # GUI thread can never hang the whole app: extremely large
                # grids decimate to ~1M cells instead of the 20% target.
                target = self.DECIMATE_TARGET
                if n_cells > 10_000_000:
                    target = min(target, 1_000_000 / n_cells)
                try:
                    grid = grid.decimate_pro(target)
                except Exception:
                    pass
            return {"grid": grid, "n": n_cells, "show_dec": show_dec}

        def on_done(_name, payload):
            self._mesh_display_in_progress = False
            self._cancel_display_timeout()
            grid = payload["grid"]
            n_cells = payload["n"]
            show_dec = payload["show_dec"]
            ec = self._edge_color()
            try:
                self._plotter.clear()
                # "internal" (Volume Mesh) renders every internal cell edge
                # of the WHOLE volume on purpose — that view exists to see
                # through the mesh. A filled/shaded surface there would hide
                # the interior it's meant to show, so it stays a bare
                # wireframe (lines only, no fill).
                if mode == "internal":
                    self._plotter.add_mesh(
                        grid, style="wireframe", color="lightgray",
                        show_edges=True, edge_color=ec,
                        line_width=0.5 if not show_dec else 0.7,
                        opacity=1.0,
                    )
                else:  # surface_edges: ParaView's "Surface With Edges"
                    # A bare wireframe (lines only, no shading) of a boundary
                    # mesh with any real face count reads as unreadable
                    # static — there is no fill to give depth/orientation
                    # cues, just overlapping line segments. style="surface"
                    # with show_edges=True is the actual ParaView "Surface
                    # With Edges" representation: a shaded fill with the
                    # TRUE polygon edges outlined on top. This does NOT
                    # reintroduce the "looks like tet" problem the wireframe
                    # was originally chosen to avoid: `grid` here is already
                    # the boundary-only PolyData from _prepare_internal_vtu_
                    # grid's extract_surface(), whose cells are the real
                    # n-gon faces (a poly cell's pentagon face stays a
                    # pentagon — verified in test_viewer_surface_only.py).
                    # VTK's edge overlay is drawn from that real polygon
                    # boundary; the GPU fanning it triangulates internally
                    # to rasterize the fill does not add visible diagonal
                    # edges — only the actual polygon outline is drawn.
                    self._plotter.add_mesh(
                        grid, style="surface", show_edges=True,
                        edge_color=ec, color="lightgray",
                        line_width=0.5 if not show_dec else 0.7,
                        opacity=1.0, lighting=True,
                    )
                if show_dec:
                    self._plotter.add_text(
                        f"Visualizzazione semplificata ({n_cells:,} celle). "
                        "Il file di mesh reale \u00e8 invariato.",
                        color=self._text_color, font_size=10,
                    )
                self._plotter.view_isometric()
                self._plotter.render()
            except Exception as exc:
                logger.error("Plotter display failed (internal VTU): %s", exc)
                self._show_mesh_too_large(f"Errore display: {exc}")

        def on_failed(_name, msg):
            self._mesh_display_in_progress = False
            self._cancel_display_timeout()
            logger.warning("Failed to load internal VTU %s: %s", vtu_path, msg)
            # Fall back to the boundary patches path.
            QTimer.singleShot(0, self._do_load_patches)

        self._vtk_tasks.submit(
            "vtk_display", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

    def _load_patches_async(self, mode: str = "patches") -> None:
        """Read boundary patches (foamToVTK VTP files or manual parser) in
        a background task; render on the GUI thread when ready."""
        case_dir = self._mesh_case_dir

        def work(worker):
            return read_openfoam_mesh_patches(case_dir)

        def on_done(_name, patches):
            self._mesh_retry_count = 0
            self._cancel_display_timeout()
            self._mesh_display_in_progress = False
            if not patches:
                self._handle_no_patches()
                return
            total_faces = sum(pd.n_cells for pd in patches.values())
            logger.info(
                "Loaded %d mesh patches (%d total faces) for %s",
                len(patches), total_faces, case_dir,
            )
            show_decimated = total_faces > self.DECIMATE_THRESHOLD
            try:
                self._plotter.clear()
                ec = self._edge_color()
                for _i, (name, pd) in enumerate(patches.items()):
                    if show_decimated:
                        try:
                            pd = pd.decimate_pro(self.DECIMATE_TARGET)
                        except Exception:
                            pass
                    self._plotter.add_mesh(
                        pd, scalars="color", rgb=True,
                        show_edges=not show_decimated, edge_color=ec, label=name,
                    )
                if show_decimated:
                    self._plotter.add_text(
                        f"Visualizzazione semplificata ({total_faces:,} \u2192 ~{int(total_faces*self.DECIMATE_TARGET):,} facce). "
                        "Il file di mesh reale \u00e8 invariato.",
                        color=self._text_color, font_size=10,
                    )
                self._plotter.view_isometric()
                self._plotter.render()
            except Exception as exc:
                logger.error("Plotter display failed (mesh patches): %s", exc)
                self._show_mesh_too_large(f"Errore display: {exc}")

        def on_failed(_name, msg):
            self._mesh_display_in_progress = False
            self._cancel_display_timeout()
            logger.warning("Mesh patches load failed: %s", msg)
            self._show_mesh_too_large(f"Errore caricamento: {msg}")

        self._vtk_tasks.submit(
            "vtk_display", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

    def _load_section_async(self) -> None:
        """Build the internal volume VTU (foamToVTK) + read it in a
        background task, then show the section cut on the GUI thread."""
        case_dir = Path(self._mesh_case_dir) if self._mesh_case_dir else None
        if case_dir is None:
            return

        def work(worker):
            vtu_path = build_internal_volume_vtu(case_dir)
            if vtu_path is None:
                return None
            return pv.read(str(vtu_path))

        def on_done(_name, grid):
            self._mesh_display_in_progress = False
            self._cancel_display_timeout()
            ec = self._edge_color()
            self._plotter.clear()
            if grid is not None:
                self._plotter.add_mesh_clip_box(
                    grid, show_edges=True, edge_color=ec, crinkle=False,
                )
            else:
                self._plotter.add_text(
                    "Internal mesh unavailable (foamToVTK failed â€” see log)",
                    color=self._text_color, font_size=10,
                )
            self._plotter.view_isometric()
            self._plotter.render()

        def on_failed(_name, msg):
            self._mesh_display_in_progress = False
            self._cancel_display_timeout()
            logger.warning("Section load failed: %s", msg)

        self._vtk_tasks.submit(
            "vtk_display", FunctionWorker(work),
            on_finished=on_done, on_failed=on_failed,
            heartbeat_timeout_s=600.0,
        )

    def _do_load_patches(self, _unused: bool = False):
        """Load and display the mesh.

        Preference: the internal volume cells (internal.vtu â€” the actual
        3D polyhedral cells after tetâ†’poly) rendered as a full wireframe,
        which makes the polyhedral structure visible with no tetrahedra.
        Falls back to the boundary surface patches (2D, look identical
        for tet and poly) only when the volume VTU isn't available.
        """
        self._mesh_display_in_progress = False
        case_dir = Path(self._mesh_case_dir) if self._mesh_case_dir else None
        if case_dir is None:
            return

        # --- Preferred: internal 3D cells (polyhedral wireframe) ----------
        vtk_dir = case_dir / "VTK_view"
        internal_vtu = None
        if vtk_dir.exists():
            candidates = sorted(vtk_dir.glob("*_0/internal.vtu"))
            if candidates:
                internal_vtu = candidates[0]
        if internal_vtu is not None:
            # Reading + decimating a big internal.vtu runs in a background
            # task; the plotter (GUI thread only) renders when it's ready.
            self._load_internal_vtu_async(internal_vtu, "internal")
            return

        # --- Fallback: boundary surface patches ----------------------------
        self._load_patches_async()

    def _handle_no_patches(self) -> None:
        """Called when the patch load came back empty: decide whether to
        trigger foamToVTK or show the 'use ParaView' message."""
        if self._foam_to_vtk_attempted:
            self._foam_to_vtk_attempted = False
            self._show_mesh_too_large("foamToVTK non disponibile (usa ParaView)")
            return

        if self._mesh_case_dir and self._mesh_retry_count < self._mesh_retry_max:
            poly_dir = Path(self._mesh_case_dir) / "constant" / "polyMesh"
            if poly_dir.is_dir():
                self._mesh_retry_count += 1
                logger.debug(
                    "Mesh load retry %d/%d (polyDir=%s)",
                    self._mesh_retry_count, self._mesh_retry_max, poly_dir,
                )
                # Trigger foamToVTK on first retry â€” but only once.
                case_dir = Path(self._mesh_case_dir)
                vtk_dir = case_dir / "VTK_view"
                has_vtu = bool(list(vtk_dir.glob("*_0/internal.vtu")) if vtk_dir.exists() else [])
                if self._mesh_retry_count == 1 and not has_vtu and not self._foam_to_vtk_attempted:
                    logger.info("VTU not found â€” starting foamToVTK before next retry")
                    self._foam_to_vtk_attempted = True
                    self._do_step_vtu(
                        on_vtu_ready=lambda ok: QTimer.singleShot(
                            2000 if ok else 500,
                            lambda: self._do_load_patches(False),
                        ),
                    )
                    return
                QTimer.singleShot(
                    2000, lambda: self._do_load_patches(False),
                )
                return
        self._mesh_retry_count = 0
        self._foam_to_vtk_attempted = False
        self._show_mesh_too_large("VTU non disponibile (usa ParaView)")

    def _show_mesh_too_large(self, reason: str = "") -> None:
        """Display 'mesh too large' message via label instead of plotter text.

        Avoids ``render()`` in the plotter which can hang with OpenGL issues.
        """
        self._mesh_display_in_progress = False
        self._view_selector.setEnabled(False)
        self._view_selector.blockSignals(True)
        self._view_selector.setCurrentIndex(-1)
        self._view_selector.blockSignals(False)
        msg = "Usa ParaView per visualizzare la mesh"
        if reason:
            msg += f" ({reason})"
        self._stats_label.setText(msg)
        logger.info("Mesh display unavailable%s", f" ({reason})" if reason else "")

    def show_cad(self, meshes: list[trimesh.Trimesh]):
        self._cad_meshes = list(meshes)
        if not self._plotter_ready:
            return
        self._view_selector.setEnabled(True)
        self._view_selector.blockSignals(True)
        self._view_selector.setCurrentIndex(-1)
        self._view_selector.blockSignals(False)
        self._view_selector.setCurrentIndex(0)
        if self._plotter:
            self._plotter.reset_camera()

    # Keep this high (10M) â€” meshes under 10M cells load fine via
    # foamToVTK + cached VTU reading.  The manual parser fallback
    # is only used when foamToVTK hasn't run yet (first view).
    MAX_VIEWER_CELLS = 10_000_000
    # When cells > this threshold, show a decimated surface mesh instead
    # of the full volume.  The real mesh file is never touched.
    DECIMATE_THRESHOLD = 2_000_000
    # Target fraction of original cells after decimation
    DECIMATE_TARGET = 0.2

    def _load_stats_async(self, case_dir: Path):
        """Load mesh stats in the background (deferred via timer)."""
        try:
            stats = read_openfoam_mesh_stats(case_dir)
        except Exception as exc:
            logger.warning("Failed to read mesh stats: %s", exc)
            stats = {"points": 0, "faces": 0, "cells": 0}
        total = stats["points"] + stats["faces"] + stats["cells"]
        if total > 0:
            self._stats_label.setText(
                f"P:{stats['points']:,}  F:{stats['faces']:,}  C:{stats['cells']:,}"
            )
        else:
            self._stats_label.setText("")
        if not self._plotter_ready:
            return
        if stats["cells"] > self.MAX_VIEWER_CELLS:
            self._show_mesh_too_large(
                f"{stats['cells']:,} cells (max={self.MAX_VIEWER_CELLS:,})"
            )
            return

        self._view_selector.setEnabled(True)
        # Do NOT set the view selector here â€” _display_mesh() is already
        # scheduled via QTimer from show_mesh(). Setting it here triggers
        # _on_view_changed â†’ _display_mesh, duplicating the foamToVTK launch
        # and causing the first process to be killed (race condition).

    def _delayed_display_mesh(self):
        """Deferred mesh rendering - no-ops if view selector was disabled
        (e.g. mesh too large).  Called via QTimer.singleShot from show_mesh()
        so the stats label paints before the heavy VTK pipeline starts.

        Dispatches on the selector's CURRENT index instead of always
        calling _display_mesh(). This used to be hardcoded to
        _display_mesh() (the full internal-volume wireframe, "Volume
        Mesh"), even though show_mesh() sets the selector to "Surface +
        Edges" right before scheduling this call. The dropdown showed
        "Surface + Edges" while the mesh actually rendered was always
        the full internal wireframe - every internal cell edge of the
        whole volume, tets/poly cells all visible through the surface -
        exactly the "internal tets still visible" symptom: fixing
        _display_surface_edges()'s OWN rendering (extract_surface +
        style=surface) had no effect because that method was never
        being called on the initial post-meshing display, only on a
        manual dropdown switch. Called directly (not via
        _on_view_changed) to keep the existing "do not double-trigger"
        guarantee from the comment above show_mesh()'s setCurrentIndex
        call.
        """
        if not self._view_selector.isEnabled():
            return
        idx = self._view_selector.currentIndex()
        mode = VIEW_MODES[idx] if idx >= 0 else "Volume Mesh"
        if mode == "Surface + Edges":
            self._display_surface_edges()
        elif mode == "CAD Surfaces":
            self._display_cad()
        else:
            self._display_mesh()

    def show_mesh(self, case_dir: Path | str):
        """Show mesh â€” always regenerates VTU from current polyMesh.

        Invalidates the VTU cache marker so foamToVTK always runs after
        meshing/conversion, ensuring the viewer shows the CURRENT mesh
        (poly after tetâ†’poly conversion) and not a stale cached version.
        """
        try:
            self._mesh_case_dir = str(case_dir)
            self._cancel_display_timeout()
            self._mesh_display_in_progress = False
            self._mesh_retry_count = 0
            self._foam_to_vtk_attempted = False
            # Kill any foamToVTK still running from a previous display so the
            # new mesh (e.g. the poly result after tet->poly conversion) is
            # generated from scratch instead of racing the old process.
            self._cancel_vtk_process()

            # Invalidate VTU cache: delete entire VTK_view directory so
            # foamToVTK regenerates from current polyMesh.  Just deleting
            # the marker is not enough â€” the old tet boundary VTP files
            # remain alongside new poly ones, causing the viewer to show
            # BOTH tet and poly patches simultaneously.
            try:
                import shutil
                vtk_dir = Path(case_dir) / "VTK_view"
                if vtk_dir.exists():
                    shutil.rmtree(vtk_dir, ignore_errors=True)
                    logger.debug("Deleted VTK_view cache for %s", case_dir)
            except Exception:
                pass

            # Read stats for the label (fast, header-only 16KB reads)
            try:
                stats = read_openfoam_mesh_stats(Path(case_dir))
            except Exception:
                stats = {"points": 0, "faces": 0, "cells": 0}
            total = stats["points"] + stats["faces"] + stats["cells"]
            if total > 0:
                self._stats_label.setText(
                    f"P:{stats['points']:,}  F:{stats['faces']:,}  C:{stats['cells']:,}"
                )
            else:
                self._stats_label.setText("Mesh ready â€” usa Strumenti > Launch ParaView")
            self._view_selector.setEnabled(True)
            # A completed meshing run must switch away from the CAD surface
            # view to the ParaView-style "Surface + Edges" mesh view â€” filled
            # surfaces with visible edges, the standard readable mesh display.
            # Leaving index 0 selected made the finished mesh look like an
            # opaque/translucent CAD overlay even though the mesh existed.
            # Block the signal here because the deferred render below is the
            # single display request for this new case.
            self._view_selector.blockSignals(True)
            self._view_selector.setCurrentIndex(VIEW_MODES.index("Surface + Edges"))
            self._view_selector.blockSignals(False)
            # Actually render the mesh (deferred so the stats label above
            # paints first). _delayed_display_mesh no-ops if the view
            # selector got disabled above (mesh too large).
            QTimer.singleShot(50, self._delayed_display_mesh)
        except Exception as exc:
            logger.exception("show_mesh failed: %s", exc)

    def clear(self):
        self._cad_meshes = []
        self._mesh_case_dir = ""
        self._pvdata_to_name.clear()
        self._stats_label.setText("")
        self._mesh_display_in_progress = False
        self._section_enabled = False
        self._section_check.blockSignals(True)
        self._section_check.setChecked(False)
        self._section_check.blockSignals(False)
        self._pick_check.blockSignals(True)
        self._pick_check.setChecked(False)
        self._pick_check.blockSignals(False)
        self._measure_check.blockSignals(True)
        self._measure_check.setChecked(False)
        self._measure_check.blockSignals(False)
        self._measure_points = []
        self._clear_measure_actors()
        if not self._plotter_ready:
            return
        try:
            self._plotter.disable_picking()
        except Exception:
            pass
        self._view_selector.setEnabled(False)
        self._plotter.clear()
        self._plotter.show_axes()
        self._plotter.render()

    # âœ… F-011: rimosso set_clip_plane() (mai chiamato, logica unificata in _on_section_toggled)

    # ------------------------------------------------------------------
    # Manual refinement BOXES: translucent cuboids with a 3D box gizmo.
    # Each box is a vtk BoxWidget (PyVista add_box_widget) - the standard VTK
    # 3D widget with handles on every face/edge to drag-size the cuboid along
    # each axis, plus translation. It cooperates with the camera interaction
    # natively (a hand-rolled drag handler on top of the camera style was
    # jittery/unreliable - seen live). The level selector in the panel is the
    # only other user input; cell_size derives from it.
    # ------------------------------------------------------------------

    def __init_ref_box_state(self):
        if not hasattr(self, "_ref_box_actors"):
            self._ref_box_actors = []
        if not hasattr(self, "_ref_box_widgets"):
            self._ref_box_widgets = []
        if not hasattr(self, "_ref_box_data"):
            self._ref_box_data = []

    def show_refinement_boxes(self, boxes: list):
        """Render refinement boxes as translucent cuboids.

        *boxes* is a list of ``{type:'box', xmin..zmax, level, cell_size}``.
        Subsequent calls replace the previous rendering. Drag-sizing is
        entered separately via start_refinement_box_drag().
        """
        self.__init_ref_box_state()
        self.stop_refinement_box_drag()
        if self._plotter is None:
            return
        try:
            self._plotter.disable_picking()
        except Exception:
            pass
        for actor in self._ref_box_actors:
            try:
                self._plotter.remove_actor(actor)
            except Exception:
                pass
        self._ref_box_actors = []
        self._ref_box_data = list(boxes)
        for b in boxes:
            x0, x1 = b["xmin"], b["xmax"]
            y0, y1 = b["ymin"], b["ymax"]
            z0, z1 = b["zmin"], b["zmax"]
            mesh = pv.Box(bounds=(x0, x1, y0, y1, z0, z1))
            actor = self._plotter.add_mesh(
                mesh, color="orange", opacity=0.35, style="surface",
                pickable=False, show_edges=True, edge_color="red",
                line_width=2,
            )
            self._ref_box_actors.append(actor)
        if boxes and self._plotter:
            self._plotter.render()

    def start_refinement_box_drag(self):
        """Enable drag-sizing of the boxes with the vtk BoxWidget gizmo."""
        self.__init_ref_box_state()
        if self._plotter is None:
            return
        self.stop_refinement_box_drag()
        widgets = []
        for i, b in enumerate(self._ref_box_data):
            bounds = (b["xmin"], b["xmax"], b["ymin"], b["ymax"],
                      b["zmin"], b["zmax"])
            try:
                w = self._plotter.add_box_widget(
                    callback=self._make_box_widget_cb(i),
                    bounds=bounds, color="lime",
                    rotation_enabled=False, use_planes=True,
                    interaction_event="end",
                )
                widgets.append(w)
            except Exception as exc:
                logger.exception("BoxWidget %d failed: %s", i, exc)
        self._ref_box_widgets = widgets
        if widgets and self._plotter:
            self._plotter.render()

    def _make_box_widget_cb(self, i: int):
        def cb(arg):
            try:
                # pyvista's add_box_widget hands the callback the widget's
                # vtkPlanes on interaction, not a bounds tuple â€” translate.
                if hasattr(arg, "GetNormals"):
                    bounds = self._bounds_from_vtk_planes(arg)
                else:
                    bounds = [float(v) for v in arg][:6]
                if len(bounds) != 6:
                    return
            except Exception:
                return
            if i >= len(self._ref_box_data):
                return
            b = self._ref_box_data[i]
            (b["xmin"], b["xmax"], b["ymin"], b["ymax"],
             b["zmin"], b["zmax"]) = bounds
            self._sync_box_actor(i)
            self.refinement_boxes_changed.emit(
                [dict(x) for x in self._ref_box_data]
            )
        return cb

    @staticmethod
    def _bounds_from_vtk_planes(planes) -> list:
        """Axis-aligned bounds (xmin,xmax,ymin,ymax,zmin,zmax) from the 6 box
        planes: for a unit normal n, a plane's origin o has n.dot(o) = d, and
        d = +/- that axis bound depending on the normal's sign."""
        import numpy as _np
        pts = _np.asarray(planes.GetPoints().GetData(), dtype=float).reshape(-1, 3)
        nrm = _np.asarray(planes.GetNormals().GetData(), dtype=float).reshape(-1, 3)
        bounds = [None] * 6
        for k in range(3):
            for o, n in zip(pts, nrm):
                d = float(o @ n)
                if n[k] > 0.5:
                    bounds[2 * k + 1] = d
                elif n[k] < -0.5:
                    bounds[2 * k] = -d
        for _idx, v in enumerate(bounds):
            if v is None:
                return (0.0, 1.0, 0.0, 1.0, 0.0, 1.0)
        return [float(v) for v in bounds]

    def _sync_box_actor(self, i: int) -> None:
        """Move the translucent box actor to match the widget bounds."""
        if self._plotter is None or i >= len(self._ref_box_actors):
            return
        b = self._ref_box_data[i]
        mesh = pv.Box(bounds=(b["xmin"], b["xmax"], b["ymin"], b["ymax"],
                              b["zmin"], b["zmax"]))
        try:
            self._ref_box_actors[i].mapper.SetInputData(mesh)
            self._ref_box_actors[i].Render()
        except Exception:
            pass
        if self._plotter:
            self._plotter.render()

    def stop_refinement_box_drag(self):
        self.__init_ref_box_state()
        for w in self._ref_box_widgets:
            try:
                self._plotter.remove_box_widget(w)
            except Exception:
                try:
                    w.off()
                except Exception:
                    pass
        self._ref_box_widgets = []


    @property
    def plotter(self):
        return self._plotter
