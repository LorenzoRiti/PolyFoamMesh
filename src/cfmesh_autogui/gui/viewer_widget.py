from __future__ import annotations

import re
import logging

import numpy as np
import pyvista as pv
import trimesh
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QCheckBox,
)
from PySide6.QtCore import QTimer, Signal

from cfmesh_autogui.core.boundary_reader import parse_boundary as _core_parse_boundary  # ✅ F-012

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


def _read_of_block(path: Path) -> str:
    text = _strip_of_comments(path.read_text(encoding="ascii", errors="replace"))
    match = re.search(r"\(\s*(.*)\s*\)", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Cannot parse OF block in {path}")
    return match.group(1)


def _parse_of_points(path: Path) -> np.ndarray:
    text = _read_of_block(path)
    text = re.sub(r"^\d+\s*", "", text, count=1)
    points = []
    for line in text.strip().split("\n"):
        line = line.strip().strip("()")
        parts = line.replace("(", "").replace(")", "").split()
        if len(parts) >= 3:
            points.append((float(parts[0]), float(parts[1]), float(parts[2])))
    return np.array(points, dtype=np.float64)


def _parse_of_faces(path: Path) -> list[list[int]]:
    text = _read_of_block(path)
    text = re.sub(r"^\d+\s*", "", text, count=1)
    faces = []
    entry_re = re.compile(r"(\d+)\s*\(([^)]*)\)")
    for entry in entry_re.finditer(text):
        idx_str = entry.group(2)
        indices = [int(x) for x in idx_str.split() if x.strip()]
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
_MAX_CACHE_SIZE = 5


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


def build_internal_volume_vtu(case_dir: Path) -> Path | None:
    """Run OpenFOAM's own `foamToVTK` to get the real internal volume cells.

    The "Volume Mesh" view used to only ever show the boundary *patches*
    (constant/polyMesh/boundary) — the outer surface, never the actual
    internal cells — because that's all `read_openfoam_mesh_patches` reads.
    cfMesh/cartesianMesh cells are polyhedral; meshio can't parse OpenFOAM
    or polyhedral VTU at all, but PyVista (built on VTK directly) reads
    them fine, so foamToVTK -> PyVista is the reliable path here.

    Cached under <case_dir>/VTK_view, regenerated only when polyMesh changed.
    """
    import subprocess
    import shlex
    from cfmesh_autogui.config import OFConfig

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

    cfg = OFConfig()
    try:
        linux_case = cfg._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(cfg.env_script)
        cmd = cfg._build_wsl_cmd(
            f"source {env_quoted} 2>/dev/null; cd {linux_case} && "
            f"foamToVTK -constant -noZero -no-fields -overwrite -name {vtk_subdir}"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("foamToVTK failed: %s", (result.stderr or result.stdout)[-300:])
            return None
    except Exception as exc:
        logger.warning("foamToVTK invocation failed: %s", exc)
        return None

    matches = list((case_dir / vtk_subdir).glob("*_0/internal.vtu"))
    if not matches:
        return None
    try:
        marker.write_text(str(state))
    except OSError:
        pass
    return matches[0]


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

    try:
        points = _parse_of_points(poly_dir / "points")
        all_faces = _parse_of_faces(poly_dir / "faces")
        patches = _parse_boundary(poly_dir / "boundary")
    except (ValueError, IndexError, OSError):
        return None
    result: dict[str, pv.PolyData] = {}
    for i, p in enumerate(patches):
        start = p["startFace"]
        end = start + p["nFaces"]
        patch_faces = all_faces[start:end]
        verts_out = []
        tris_idx = []
        v_offset = 0
        for face_indices in patch_faces:
            for idx in face_indices:
                pt = points[idx]
                verts_out.append([pt[0], pt[1], pt[2]])
            for tri in _triangulate_face(list(range(len(face_indices)))):
                tris_idx.append([v_offset + tri[0], v_offset + tri[1], v_offset + tri[2]])
            v_offset += len(face_indices)
        if not tris_idx:
            continue
        verts_arr = np.array(verts_out, dtype=np.float64)
        tris_arr = np.array(tris_idx, dtype=np.int32)
        faces_pv = np.hstack([np.full((len(tris_arr), 1), 3), tris_arr]).astype(np.int32)
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

    def _display_mesh(self):
        if self._plotter is None or not self._mesh_case_dir:
            return

        if self._section_enabled:
            # Boundary patches (constant/polyMesh/boundary) are the outer
            # surface only — clipping them shows nothing "inside" because
            # there IS nothing inside that data. Seeing the actual internal
            # cells needs the real volumetric grid, built via foamToVTK.
            self._plotter.clear()
            ec = self._edge_color()
            grid = self._get_internal_volume_grid()
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
            return

        patches = read_openfoam_mesh_patches(self._mesh_case_dir)
        if not patches:
            return
        self._plotter.clear()
        ec = self._edge_color()
        for i, (name, pd) in enumerate(patches.items()):
            color = PATCH_COLORS[i % len(PATCH_COLORS)]
            self._plotter.add_mesh(
                pd, scalars="color", rgb=True,
                show_edges=True, edge_color=ec, label=name,
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

    def show_mesh(self, case_dir: Path | str):
        self._mesh_case_dir = str(case_dir)
        stats = read_openfoam_mesh_stats(Path(case_dir))
        total = stats["points"] + stats["faces"] + stats["cells"]
        if total > 0:
            self._stats_label.setText(
                f"P:{stats['points']:,}  F:{stats['faces']:,}  C:{stats['cells']:,}"
            )
        else:
            self._stats_label.setText("")
        if not self._plotter_ready:
            return
        self._view_selector.setEnabled(True)
        self._view_selector.blockSignals(True)
        self._view_selector.setCurrentIndex(-1)
        self._view_selector.blockSignals(False)
        self._view_selector.setCurrentIndex(1)
        if self._plotter:
            self._plotter.reset_camera()

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
