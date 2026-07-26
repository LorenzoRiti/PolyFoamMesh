from __future__ import annotations

import re
import logging
import struct

import numpy as np
import pyvista as pv
import trimesh
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QCheckBox, QApplication,
)
from PySide6.QtCore import QTimer, Signal, QProcess

from cfmesh_autogui.core.boundary_reader import parse_boundary as _core_parse_boundary  # ✅ F-012
from cfmesh_autogui.core.of_reader import read_of_text, of_list_count, _read_of_bytes

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

VIEW_MODES = ["CAD Surfaces", "Volume Mesh"]

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
    """Check if an OpenFOAM file is in binary format."""
    raw = _read_of_bytes(path)
    return b'format      binary;' in raw[:512]


def _read_of_block(path: Path) -> str:
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
    from cfmesh_autogui.core.of_reader import _is_binary_format
    text_part = raw.decode("ascii", errors="replace")
    text_part = _strip_of_comments(text_part)
    header_end = text_part.find("}")
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
    # Default: points, vectorField etc. — flat doubles
    return _binary_doubles_to_ascii(body_bytes)


def _binary_doubles_to_ascii(data: bytes) -> str:
    """Convert binary double array to space-separated ASCII string."""
    n_floats = len(data) // 8
    if n_floats == 0:
        return ""
    values = struct.unpack(f"<{n_floats}d", data[:n_floats * 8])
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
    arr = np.fromstring(text, sep=" ", dtype=np.float64)
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


# ✅ F-012: delegato a boundary_reader.parse_boundary (elimina duplicazione)
def _parse_boundary(path: Path) -> list[dict]:
    from cfmesh_autogui.core.boundary_reader import parse_boundary
    patches = parse_boundary(path)
    return [{"name": p.name, "nFaces": p.n_faces, "startFace": p.start_face} for p in patches]


def _triangulate_face(indices: list[int]) -> list[tuple[int, int, int]]:
    tris = []
    n = len(indices)
    if n == 3:
        tris.append((indices[0], indices[1], indices[2]))
    elif n == 4:
        tris.append((indices[0], indices[1], indices[2]))
        tris.append((indices[0], indices[2], indices[3]))
    elif n > 4:
        for j in range(1, n - 1):
            tris.append((indices[0], indices[j], indices[j + 1]))
    return tris


# ✅ F-013: LRU cache con max 5 entries per evitare memory leak
_MESH_CACHE: dict[tuple[str, float], dict[str, pv.PolyData]] = {}
_MAX_CACHE_SIZE = 20


def _cache_set(key: tuple[str, float], value: dict[str, pv.PolyData]) -> None:
    if len(_MESH_CACHE) >= _MAX_CACHE_SIZE:
        oldest = next(iter(_MESH_CACHE))
        del _MESH_CACHE[oldest]
    _MESH_CACHE[key] = value


def _poly_dir_state(poly_dir: Path) -> float | None:
    files = ("points", "faces", "boundary")
    mtimes: list[float] = []
    for name in files:
        f = poly_dir / name
        if not f.exists():
            return None
        mtimes.append(f.stat().st_mtime)
    return max(mtimes)


def _run_foamtovtk_async(case_dir: Path, vtk_subdir: str, state: float) -> Path | None:
    """Run foamToVTK via Popen with polling loop to keep UI responsive."""
    import subprocess, shlex, time as _time
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
    deadline = _time.monotonic() + 60
    while proc.poll() is None:
        QApplication.processEvents()
        if _time.monotonic() > deadline:
            proc.kill()
            proc.wait(5)
            logger.error("foamToVTK timed out after 60s")
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
    (constant/polyMesh/boundary) — the outer surface, never the actual
    internal cells — because that's all `read_openfoam_mesh_patches` reads.
    cfMesh/cartesianMesh cells are polyhedral; meshio can't parse OpenFOAM
    or polyhedral VTU at all, but PyVista (built on VTK directly) reads
    them fine, so foamToVTK -> PyVista is the reliable path here.

    Cached under <case_dir>/VTK_view, regenerated only when polyMesh changed.
    Uses Popen with a polling loop so QApplication.processEvents() keeps
    the UI responsive during the 30-60s WSL2 call.
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

    Used to have `len(lines) - 2` on each file as a count proxy — but the
    FoamFile header block is variable-length (comment banner, arch/note
    lines, etc.), so that silently showed the wrong points/faces/cells
    counts in the viewer's stats label on every real mesh. Verified against
    a real cfMesh mesh: reported values didn't match the mesh's own
    self-reported nPoints/nCells/nFaces at all.
    """
    from cfmesh_autogui.core.boundary_reader import count_cells, count_faces, count_points
    return {
        "points": count_points(case_dir),
        "faces": count_faces(case_dir),
        "cells": count_cells(case_dir),
    }


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
    for i, vtu in enumerate(sorted(boundary_dir.glob("*.vtu"))):
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
        return None
    cache_key = (str(case_dir), state)
    cached = _MESH_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Fast path: try loading from foamToVTK-generated VTU files
    vtk_patches = _load_vtk_patches(case_dir, "VTK_view")
    if vtk_patches is not None:
        _cache_set(cache_key, vtk_patches)
        return vtk_patches

    # Slow path: manual parsing of OpenFOAM text files (for small meshes
    # where foamToVTK hasn't been run yet).
    try:
        points = _parse_of_points(poly_dir / "points")
        all_faces = _parse_of_faces(poly_dir / "faces")
        patches = _parse_boundary(poly_dir / "boundary")
    except (ValueError, IndexError, OSError) as exc:
        logger.warning("Manual mesh parsing failed for %s: %s", poly_dir, exc)
        return None
    result: dict[str, pv.PolyData] = {}
    for i, p in enumerate(patches):
        start = p["startFace"]
        end = start + p["nFaces"]
        patch_faces = all_faces[start:end]
        total_verts = sum(len(f) for f in patch_faces)
        total_tris = sum(max(1, len(f) - 2) for f in patch_faces)
        if total_tris == 0:
            continue
        verts_arr = np.empty((total_verts, 3), dtype=np.float64)
        tris_arr = np.empty((total_tris, 3), dtype=np.int32)
        v_offset = 0
        t_offset = 0
        for fi, face_indices in enumerate(patch_faces):
            if fi > 0 and fi % 500 == 0:
                QApplication.processEvents()
            n = len(face_indices)
            verts_arr[v_offset:v_offset + n] = points[face_indices]
            if n == 3:
                tris_arr[t_offset] = [v_offset, v_offset + 1, v_offset + 2]
                t_offset += 1
            elif n == 4:
                tris_arr[t_offset] = [v_offset, v_offset + 1, v_offset + 2]
                tris_arr[t_offset + 1] = [v_offset, v_offset + 2, v_offset + 3]
                t_offset += 2
            elif n > 4:
                for j in range(1, n - 1):
                    tris_arr[t_offset] = [v_offset, v_offset + j, v_offset + j + 1]
                    t_offset += 1
            v_offset += n
        faces_pv = np.hstack([np.full((total_tris, 1), 3), tris_arr]).astype(np.int32)
        pd = pv.PolyData(verts_arr, faces_pv)
        color = PATCH_COLORS[i % len(PATCH_COLORS)]
        pd.cell_data["color"] = np.tile(color, (pd.n_cells, 1))
        result[p["name"]] = pd
    _cache_set(cache_key, result)  # ✅ F-013
    return result


class ViewerWidget(QWidget):
    face_picked = Signal(str)
    distance_measured = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cad_meshes: list[trimesh.Trimesh] = []
        self._mesh_case_dir: str = ""
        self._bg_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._text_color: str = "black"
        self._plotter = None
        self._plotter_ready: bool = False
        self._pvdata_to_name: dict[str, str] = {}
        self._section_enabled: bool = False
        self._measure_points: list = []
        self._measure_actors: list = []
        self._highlighted_actor = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)

        toolbar.addWidget(QLabel("Display:"))
        self._view_selector = QComboBox()
        self._view_selector.addItems(VIEW_MODES)
        self._view_selector.setCurrentIndex(-1)
        self._view_selector.setEnabled(False)
        self._view_selector.setToolTip("Switch between CAD surfaces and volume mesh views.")
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
        from pyvistaqt import QtInteractor
        self._plotter = QtInteractor(parent=self, auto_update=30.0)
        self._layout.addWidget(self._plotter)
        self._plotter_ready = True
        self._apply_background(self._bg_selector.currentText())

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
            pv.Sphere(radius=(getattr(self._cad_meshes[0], 'scale', 1.0) if self._cad_meshes else 1.0) * 0.01,  # ✅ F-010
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

    def _on_mesh_picked(self, actor):
        # use_actor=True (set in _on_pick_toggled) means `actor` is the
        # picked vtkActor. Matching by the *mesh's* memory_address used to
        # be used here, but PyVista internally copies/repacks the polydata
        # when rendering with `scalars=..., rgb=True` (as _display_cad and
        # highlight_patch always do) — the mapper's input dataset ends up
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
                # `bounds` kwarg — passing one (as this used to) raises a
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
        """Kill any running foamToVTK QProcess and its timeout timer."""
        if hasattr(self, "_vtk_timeout_timer") and self._vtk_timeout_timer:
            self._vtk_timeout_timer.stop()
            self._vtk_timeout_timer.deleteLater()
            self._vtk_timeout_timer = None
        if hasattr(self, "_vtk_process") and self._vtk_process:
            if self._vtk_process.state() != QProcess.NotRunning:
                self._vtk_process.kill()
                self._vtk_process.waitForFinished(3000)
            self._vtk_process.deleteLater()
            self._vtk_process = None

    def _start_vtk_qprocess(self, case_dir: Path, vtk_subdir: str,
                             on_finished: callable) -> None:
        """Start foamToVTK via QProcess. Calls ``on_finished(ok)`` when done."""
        self._cancel_vtk_process()
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
        self._vtk_process.setProcessChannelMode(QProcess.MergedChannels)
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
        self._vtk_timeout_timer.start(70000)

    def _on_vtk_timeout(self):
        logger.error("foamToVTK QProcess timed out after 70s")
        self._cancel_vtk_process()

    def _on_vtk_finished(self, exit_code: int, case_dir: Path,
                          vtk_subdir: str, on_finished: callable) -> None:
        if self._vtk_timeout_timer:
            self._vtk_timeout_timer.stop()
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
            stderr = bytes(self._vtk_process.readAllStandardError()).decode("utf-8", errors="replace")[-300:] if self._vtk_process else ""
            logger.warning("foamToVTK QProcess failed (rc=%d): %s", exit_code, stderr)
        on_finished(exit_code == 0)

    def _display_mesh(self):
        if self._plotter is None or not self._mesh_case_dir:
            return
        if self._section_enabled:
            self._display_mesh_section()
            return
        # Show "Loading..." immediately, then chain async operations
        self._plotter.clear()
        self._plotter.add_text(
            "Loading mesh...",
            color=self._text_color, font_size=14,
        )
        self._plotter.render()
        QApplication.processEvents()
        QTimer.singleShot(0, self._step_vtu_for_mesh)

    def _display_mesh_section(self):
        self._plotter.clear()
        self._plotter.add_text(
            "Loading mesh for section cut...",
            color=self._text_color, font_size=12,
        )
        self._plotter.render()
        QApplication.processEvents()
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
        """Check VTU cache; if stale/missing, start QProcess, else callback immediately."""
        if self._plotter is None or not self._mesh_case_dir:
            return
        case_dir = Path(self._mesh_case_dir)
        poly_dir = case_dir / "constant" / "polyMesh"
        state = _poly_dir_state(poly_dir)
        if state is None:
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
        ec = self._edge_color()
        grid = self._get_internal_volume_grid()
        self._plotter.clear()
        if grid is not None:
            self._plotter.add_mesh_clip_box(
                grid, show_edges=True, edge_color=ec, crinkle=False,
            )
        else:
            self._plotter.add_text(
                "Internal mesh unavailable (foamToVTK failed — see log)",
                color=self._text_color, font_size=10,
            )
        self._plotter.view_isometric()
        self._plotter.render()

    def _do_load_patches(self, _unused: bool = False):
        """Load and display mesh patches (already deferred via QTimer)."""
        patches = read_openfoam_mesh_patches(self._mesh_case_dir)
        if not patches:
            self._plotter.clear()
            self._plotter.add_text(
                "No mesh data to display.",
                color=self._text_color, font_size=12,
            )
            self._plotter.render()
            return
        total_faces = sum(pd.n_cells for pd in patches.values())
        show_decimated = total_faces > self.DECIMATE_THRESHOLD
        self._plotter.clear()
        ec = self._edge_color()
        for i, (name, pd) in enumerate(patches.items()):
            color = PATCH_COLORS[i % len(PATCH_COLORS)]
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

    def _get_internal_volume_grid(self):
        """Load the real internal volume cells (pv.UnstructuredGrid), building
        them via foamToVTK on first use / whenever polyMesh changed."""
        try:
            vtu_path = build_internal_volume_vtu(Path(self._mesh_case_dir))
            if vtu_path is None:
                return None
            return pv.read(str(vtu_path))
        except Exception as exc:
            logger.warning("Failed to load internal volume mesh: %s", exc)
            return None

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

    # Keep this high (10M) — meshes under 10M cells load fine via
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
        stats = read_openfoam_mesh_stats(case_dir)
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
            self._view_selector.setEnabled(False)
            self._view_selector.blockSignals(True)
            self._view_selector.setCurrentIndex(-1)
            self._view_selector.blockSignals(False)
            self._plotter.clear()
            self._plotter.add_text(
                f"Mesh too large for viewer ({stats['cells']:,} cells, "
                f"max={self.MAX_VIEWER_CELLS:,}).\n"
                "Use ParaView or another external tool for visualization.",
                color=self._text_color, font_size=12,
            )
            self._plotter.show_axes()
            self._plotter.render()
            return

        self._view_selector.setEnabled(True)
        self._view_selector.blockSignals(True)
        self._view_selector.setCurrentIndex(-1)
        self._view_selector.blockSignals(False)
        self._view_selector.setCurrentIndex(1)

    def show_mesh(self, case_dir: Path | str):
        self._mesh_case_dir = str(case_dir)

        # Show loading text immediately, then load stats + mesh deferred
        if self._plotter_ready:
            self._plotter.clear()
            self._plotter.add_text(
                "Loading mesh...",
                color=self._text_color, font_size=14,
            )
            self._plotter.show_axes()
            self._plotter.render()
            QApplication.processEvents()

        QTimer.singleShot(0, lambda: self._load_stats_async(Path(case_dir)))
        QTimer.singleShot(50, self._display_mesh)  # let stats load first

    def clear(self):
        self._cad_meshes = []
        self._mesh_case_dir = ""
        self._pvdata_to_name.clear()
        self._stats_label.setText("")
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

    # ✅ F-011: rimosso set_clip_plane() (mai chiamato, logica unificata in _on_section_toggled)

    @property
    def plotter(self):
        return self._plotter
