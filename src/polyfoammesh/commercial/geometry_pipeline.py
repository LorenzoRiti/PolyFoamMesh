"""Geometry pipeline — unified import, healing, and feature extraction.

Supports STEP/STL/IGES/BREP with auto-detect format, unit auto-detection
(mm/cm/m/inch/ft), automatic healing (gap stitch, hole fill, sliver remove),
feature extraction (sharp edges, curvature, thin gaps), and auto-classify
patches (wall/inlet/outlet/symmetry based on normal angle and bounding box).

Output: healed and classified trimesh meshes ready for meshDict generation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from polyfoammesh.core.validation import validate_geometry_path
from polyfoammesh.octopoda_local import octo

logger = logging.getLogger(__name__)

# Unit scale factors (m)
_UNIT_SCALES = {
    "mm": 0.001, "cm": 0.01, "m": 1.0, "inch": 0.0254, "in": 0.0254, "ft": 0.3048,
}

# Healing thresholds (m)
_GAP_STITCH = 0.0001    # 0.1 mm
_HOLE_FILL = 0.005      # 5 mm
_SLIVER_AREA = 1e-6     # 1 mm²


@dataclass
class GeometryInfo:
    """Information about loaded geometry."""
    file_path: str = ""
    format: str = ""           # step, stl, iges, brep
    detected_unit: str = "m"   # auto-detected unit
    n_patches: int = 0
    patch_names: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bbox_max: float = 0.0
    volume: float = 0.0
    n_watertight: int = 0
    n_non_watertight: int = 0
    watertight: bool = True


@dataclass
class HealReport:
    """Summary of healing operations performed."""
    original_faces: int = 0
    final_faces: int = 0
    holes_filled: int = 0
    gaps_stitched: int = 0
    slivers_removed: int = 0
    watertight: bool = False
    operations: list[str] = field(default_factory=list)


@dataclass
class FeatureReport:
    """Summary of extracted features."""
    n_sharp_edges: int = 0
    min_curvature_radius: float = 0.0
    n_gap_regions: int = 0
    min_gap: float = 0.0
    patch_classification: dict[str, str] = field(default_factory=dict)


@dataclass
class GeometryPipelineResult:
    """Complete result from the geometry pipeline."""
    success: bool = False
    geometry: GeometryInfo | None = None
    healing: HealReport | None = None
    features: FeatureReport | None = None
    meshes: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class GeometryPipeline:
    """End-to-end geometry import, healing, and feature extraction.

    Usage::

        gp = GeometryPipeline()
        result = gp.run("model.step")
        if result.success:
            print(f"Loaded: {result.geometry.n_patches} patches")
            print(f"Healed: {result.healing.holes_filled} holes filled")
            print(f"Features: {result.features.n_sharp_edges} sharp edges")
            # result.meshes contains healed, classified trimesh meshes
    """

    # Healing thresholds
    MAX_GAP: float = _GAP_STITCH
    MAX_HOLE: float = _HOLE_FILL
    MIN_SLIVER_AREA: float = _SLIVER_AREA

    def run(
        self, file_path: str | Path,
        unit: str = "auto",
        heal: bool = True,
        extract_features: bool = True,
        classify_patches: bool = True,
    ) -> GeometryPipelineResult:
        """Execute the full geometry pipeline.

        Args:
            file_path: Path to geometry file (.step/.stp/.stl/.iges/.brep).
            unit: CAD unit (``"auto"``, ``"m"``, ``"mm"``, ``"cm"``, etc.).
            heal: Apply automatic healing.
            extract_features: Extract sharp edges, curvature, gaps.
            classify_patches: Auto-classify patches by type.

        Returns:
            ``GeometryPipelineResult`` with meshes and reports.
        """
        result = GeometryPipelineResult()
        octo.log_event("geometry_pipeline", "start", {"file": str(file_path)})

        try:
            # 1. Validate
            validate_result = validate_geometry_path(file_path)
            if not validate_result.valid:
                raise ValueError(validate_result.message)

            path = Path(file_path)
            ext = path.suffix.lower()
            fmt_map = {".step": "step", ".stp": "step", ".stl": "stl",
                       ".iges": "iges", ".igs": "iges", ".brep": "brep"}
            fmt = fmt_map.get(ext, ext.lstrip("."))

            # 2. Load
            meshes, geo_info = self._import_geometry(path, fmt, unit)
            result.geometry = geo_info
            result.meshes = meshes

            # 3. Heal
            if heal and meshes:
                heal_report = self._heal_meshes(meshes)
                result.healing = heal_report
                # Update watertight count after healing
                geo_info.n_watertight = sum(1 for m in meshes if m.is_watertight)
                geo_info.n_non_watertight = len(meshes) - geo_info.n_watertight

            # 4. Feature extraction
            if extract_features and meshes:
                feat_report = self._extract_features(meshes)
                result.features = feat_report

            # 5. Classify patches
            if classify_patches and meshes:
                self._classify_patches(meshes, geo_info)

            # 6. Update final patch names in geo_info
            geo_info.patch_names = [m.metadata.get("name", f"patch_{i}")
                                    for i, m in enumerate(meshes)]
            geo_info.n_patches = len(meshes)

            result.success = True
            octo.log_event("geometry_pipeline", "complete", {
                "patches": geo_info.n_patches,
                "watertight": geo_info.watertight,
                "features": result.features.n_sharp_edges if result.features else 0,
            })

        except Exception as exc:
            result.errors.append(str(exc))
            logger.exception("Geometry pipeline failed")

        return result

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    def _import_geometry(
        self, path: Path, fmt: str, unit: str,
    ) -> tuple[list, GeometryInfo]:
        """Load geometry and return (meshes, info)."""

        if fmt in ("step", "stp"):
            meshes = self._import_step(path)
            fmt = "step"
        elif fmt == "stl":
            meshes = self._import_stl(path)
        elif fmt in ("iges", "igs"):
            meshes = self._import_iges(path)
            fmt = "iges"
        elif fmt == "brep":
            meshes = self._import_step(path)  # BREP via cadquery
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        if not meshes:
            raise RuntimeError(f"No geometry loaded from {path}")

        # Detect unit
        detected_unit = self._detect_unit(meshes) if unit == "auto" else unit
        scale = _UNIT_SCALES.get(detected_unit, 1.0)
        if abs(scale - 1.0) > 1e-9:
            for m in meshes:
                m.vertices *= scale

        # Bounding box
        all_v = np.vstack([m.vertices for m in meshes])
        bbox = tuple(float(x) for x in (all_v.max(axis=0) - all_v.min(axis=0)))

        n_wt = sum(1 for m in meshes if m.is_watertight)
        geo_info = GeometryInfo(
            file_path=str(path),
            format=fmt,
            detected_unit=detected_unit,
            n_patches=len(meshes),
            patch_names=[m.metadata.get("name", f"patch_{i}")
                         for i, m in enumerate(meshes)],
            bbox=bbox,
            bbox_max=max(bbox),
            watertight=n_wt == len(meshes),
            n_watertight=n_wt,
            n_non_watertight=len(meshes) - n_wt,
        )

        return meshes, geo_info

    def _import_step(self, path: Path) -> list:
        """Import STEP using cadquery, tessellate to trimesh."""
        import cadquery as cq
        from polyfoammesh.core.geometry import classify_faces, tessellate_patches
        shape = cq.importers.importStep(str(path))
        shape = shape.val() if isinstance(shape, cq.Workplane) else shape
        patches = classify_faces(shape)
        return tessellate_patches(patches)

    def _import_stl(self, path: Path) -> list:
        """Import STL with multi-solid support."""
        import trimesh
        from polyfoammesh.core.stl_writer import heal_mesh
        scene = trimesh.load_mesh(str(path), force="mesh")
        if isinstance(scene, trimesh.Scene):
            meshes = list(scene.geometry.values())
        else:
            meshes = [scene]
        for m in meshes:
            m.metadata.setdefault("name", path.stem)
            heal_mesh(m)
        return meshes

    def _import_iges(self, path: Path) -> list:
        """Import IGES via cadquery (importIges)."""
        import cadquery as cq
        from polyfoammesh.core.geometry import classify_faces, tessellate_patches
        shape = cq.importers.importIges(str(path))
        shape = shape.val() if isinstance(shape, cq.Workplane) else shape
        patches = classify_faces(shape)
        return tessellate_patches(patches)

    # ------------------------------------------------------------------
    # Unit detection
    # ------------------------------------------------------------------
    def _detect_unit(self, meshes: list) -> str:
        """Heuristic unit detection based on mesh extent.
        
        If the bounding box is very small (< 0.01 m), assume mm.
        If very large (> 100 m), assume cm. Otherwise m.
        """
        if not meshes:
            return "m"
        all_v = np.vstack([m.vertices for m in meshes])
        bbox_max = float(np.max(all_v.max(axis=0) - all_v.min(axis=0)))
        if bbox_max < 0.01:
            return "mm"
        if bbox_max > 100:
            return "cm"
        return "m"

    # ------------------------------------------------------------------
    # Healing
    # ------------------------------------------------------------------
    def _heal_meshes(self, meshes: list) -> HealReport:
        """Apply healing to all meshes."""
        from polyfoammesh.core.stl_writer import heal_mesh

        total_orig = sum(len(m.faces) for m in meshes)
        total_holes = 0
        total_slivers = 0
        ops: list[str] = []

        for mesh in meshes:
            mesh_name = mesh.metadata.get("name", "?")
            try:
                areas = mesh.area_faces
                sliver_mask = areas > self.MIN_SLIVER_AREA
                n_slivers = int((~sliver_mask).sum())
                if n_slivers > 0:
                    mesh.update_faces(sliver_mask)
                    total_slivers += n_slivers
                    ops.append(f"removed {n_slivers} sliver faces")
            except Exception as exc:
                logger.debug("Sliver removal failed for patch '%s': %s", mesh_name, exc)

            # Fill holes
            try:
                before_holes = len(mesh.faces)
                mesh.fill_holes()
                filled = len(mesh.faces) - before_holes
                if filled > 0:
                    total_holes += filled
                    ops.append(f"filled {filled} holes")
            except Exception as exc:
                logger.debug("fill_holes failed for patch '%s': %s", mesh_name, exc)

            # Standard healing
            heal_mesh(mesh)

        wf = sum(1 for m in meshes if m.is_watertight)
        return HealReport(
            original_faces=total_orig,
            final_faces=sum(len(m.faces) for m in meshes),
            holes_filled=total_holes,
            slivers_removed=total_slivers,
            watertight=wf == len(meshes),
            operations=ops,
        )

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------
    def _extract_features(self, meshes: list) -> FeatureReport:
        """Extract sharp edges, curvature, and gap regions."""
        import trimesh

        sharp_edges = 0
        curv_radius = float("inf")
        gap_count = 0
        min_gap = float("inf")

        for mesh in meshes:
            # Sharp edges via edge angle
            try:
                if hasattr(mesh, 'face_adjacency'):
                    edges = mesh.face_adjacency
                    normals = mesh.face_normals
                    if len(edges) > 0:
                        dot_prods = np.sum(
                            normals[edges[:, 0]] * normals[edges[:, 1]], axis=1
                        )
                        angles = np.degrees(np.arccos(np.clip(dot_prods, -1, 1)))
                        sharp_edges += int((angles > 30).sum())
            except Exception:
                # feature analysis is best-effort: a bad mesh keeps the defaults
                logger.debug("geometry_pipeline: sharp-edge scan failed", exc_info=True)

            # Curvature radius estimate (via edge lengths)
            try:
                if hasattr(mesh, 'edges_unique') and len(mesh.edges_unique) > 0:
                    verts = mesh.vertices
                    edges = mesh.edges_unique
                    lengths = np.linalg.norm(
                        verts[edges[:, 0]] - verts[edges[:, 1]], axis=1
                    )
                    if len(lengths) > 0:
                        # Minimum edge length approximates curvature
                        e_min = float(lengths.min()) / 2.0
                        curv_radius = min(curv_radius, e_min)
            except Exception:
                # curvature estimate is best-effort
                logger.debug("geometry_pipeline: curvature scan failed", exc_info=True)

            # Gap detection (non-watertight boundary edges). face_adjacency_edges
            # holds vertex-index pairs, not a boolean mask — `~` on it produced
            # garbage indices, always caught by the except below, so this
            # silently reported zero gaps for every non-watertight mesh.
            # Boundary edges are edges that appear only once (not shared by
            # two faces).
            if not mesh.is_watertight:
                try:
                    boundary_idx = trimesh.grouping.group_rows(
                        mesh.edges_sorted, require_count=1,
                    )
                    boundary_edges = mesh.edges[boundary_idx]
                    if len(boundary_edges) > 0:
                        verts = mesh.vertices
                        gap_lens = np.linalg.norm(
                            verts[boundary_edges[:, 0]] -
                            verts[boundary_edges[:, 1]],
                            axis=1,
                        )
                        if len(gap_lens) > 0:
                            gap_count += 1
                            min_gap = min(min_gap, float(gap_lens.min()))
                except Exception:
                    # gap detection is best-effort (a known historical bug here
                    # silently reported zero gaps — now at least traceable)
                    logger.debug("geometry_pipeline: gap scan failed", exc_info=True)

        if curv_radius == float("inf"):
            curv_radius = 0.0
        if min_gap == float("inf"):
            min_gap = 0.0

        return FeatureReport(
            n_sharp_edges=sharp_edges,
            min_curvature_radius=curv_radius,
            n_gap_regions=gap_count,
            min_gap=min_gap,
        )

    # ------------------------------------------------------------------
    # Patch classification
    # ------------------------------------------------------------------
    def _classify_patches(
        self, meshes: list, geo_info: GeometryInfo,
    ) -> None:
        """Auto-classify patches as inlet/outlet/wall/symmetry."""
        bbox = geo_info.bbox
        bbox_max = max(bbox) or 1.0
        tol = bbox_max * 0.01

        for i, mesh in enumerate(meshes):
            name = mesh.metadata.get("name", f"patch_{i}")

            # Already named? Keep it.
            lower = name.lower()
            if lower in ("inlet", "outlet", "wall", "symmetry"):
                continue
            if any(kw in lower for kw in ("wall", "blade", "body", "hull")):
                mesh.metadata["name"] = "wall"
                continue

            # Classify by centroid position on bounding box
            centroid = mesh.centroid
            cx, cy, cz = centroid

            # Check if face is on a bbox face
            near_x_min = abs(cx - 0) < tol
            near_x_max = abs(cx - bbox[0]) < tol
            near_y_min = abs(cy - 0) < tol
            near_y_max = abs(cy - bbox[1]) < tol

            if near_x_min:
                mesh.metadata["name"] = "inlet"
            elif near_x_max:
                mesh.metadata["name"] = "outlet"
            elif near_y_min or near_y_max:
                mesh.metadata["name"] = "symmetry"
            else:
                mesh.metadata["name"] = "wall"
