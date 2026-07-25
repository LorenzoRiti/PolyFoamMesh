from __future__ import annotations

import json
import math
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SharpEdge:
    point_a: tuple[float, float, float]
    point_b: tuple[float, float, float]
    angle: float
    length: float


@dataclass
class GapRegion:
    center: tuple[float, float, float]
    gap_width: float
    normal: tuple[float, float, float]


@dataclass
class FeatureMap:
    sharp_edges: list[SharpEdge] = field(default_factory=list)
    gap_regions: list[GapRegion] = field(default_factory=list)
    curvature_radius: float = 1.0
    suggested_min_cell: float = 0.01
    suggested_max_cell: float = 0.1


class FeatureDetector:
    _cache: dict[str, FeatureMap] = {}

    def __init__(self):
        self._gmsh_initialized = False

    def analyze_step(
        self, filepath: Path | str, detail: str = "medium", scale: float = 1.0,
    ) -> FeatureMap:
        """Analyse *filepath* and suggest cell sizes.

        *scale* converts the CAD file's own units into the units the rest
        of the app works in (metres) — the SAME factor MainWindow applies
        to the loaded geometry. Without it, this returns sizes in the CAD
        file's units while the caller treats them as metres: confirmed
        live on a 3 m model authored in mm, where GMSH's bbox is 3000 and
        the suggestion came back as 150 — applied as 150 METRES, i.e.
        50x the whole model, which then got clamped and logged as
        "maxCellSize clamped from 100.0000 to 1.5000".
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"STEP file not found: {filepath}")

        # Check cache: keyed by (resolved path + mtime + detail + scale)
        cache_key = f"{filepath.resolve()}::{filepath.stat().st_mtime}::{detail}::{scale}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        import gmsh

        if not self._gmsh_initialized:
            # interruptible=True (gmsh's default) calls signal.signal() to
            # let Ctrl+C abort a long operation — but signal handlers can
            # only be installed from the main thread, and this now runs
            # on a background QThread (FeatureDetectWorker) to keep the
            # GUI responsive. Confirmed live: every feature-detection call
            # failed with "signal only works in main thread of the main
            # interpreter" the moment it stopped running on the GUI
            # thread. interruptible=False skips that call entirely.
            gmsh.initialize(interruptible=False)
            self._gmsh_initialized = True

        gmsh.clear()
        gmsh.model.add("feature_analysis")
        gmsh.open(str(filepath))

        feature_map = FeatureMap()

        # Curvature analysis
        try:
            curvature = gmsh.model.getCurvature(-1, [])
            if curvature:
                feature_map.curvature_radius = max(
                    1.0 / max(abs(c) for c in curvature if abs(c) > 1e-12), 1e-6
                )
        except Exception as e:
            logger.debug("Curvature analysis skipped: %s", e)

        # Sharp edge detection via dihedral angles (mesh-based)
        try:
            gmsh.model.mesh.generate(2)

            # Get all node coordinates
            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            node_map = {}
            for k in range(len(node_tags)):
                node_map[int(node_tags[k])] = node_coords[k*3:(k+1)*3]

            # Get 2D elements and compute face normals
            face_types, face_elem_tags, face_elem_nodes = gmsh.model.mesh.getElements(2)
            face_normal_map = {}
            for etype, etags, enodes in zip(face_types, face_elem_tags, face_elem_nodes):
                npe = 3 if etype == 2 else 4
                enodes = enodes.reshape(-1, npe)
                for k in range(len(etags)):
                    n0 = node_map.get(int(enodes[k][0]))
                    n1 = node_map.get(int(enodes[k][1]))
                    n2 = node_map.get(int(enodes[k][2]))
                    if n0 is not None and n1 is not None and n2 is not None:
                        v1 = n1 - n0
                        v2 = n2 - n0
                        n = np.cross(v1, v2)
                        norm_n = np.linalg.norm(n)
                        if norm_n > 1e-12:
                            n = n / norm_n
                        face_normal_map[int(etags[k])] = n

            # Build face-to-edge mapping
            face_edge_map = {}
            for etype, etags, enodes in zip(face_types, face_elem_tags, face_elem_nodes):
                try:
                    _, elem_edges = gmsh.model.mesh.getElementEdges(etype, etags)
                except Exception:
                    continue
                if elem_edges.ndim < 2:
                    continue
                for k, ftag in enumerate(etags):
                    face_edge_map[int(ftag)] = [int(elem_edges[k][j]) for j in range(elem_edges.shape[1])]

            # Reverse: edge -> adjacent face elements
            edge_face_map = {}
            for ftag, e_list in face_edge_map.items():
                for etag in e_list:
                    edge_face_map.setdefault(etag, []).append(ftag)

            # Edge node pairs for position/length
            edge_tags, edge_nodes = gmsh.model.mesh.getEdges()
            edge_nodes = edge_nodes.reshape(-1, 2)
            edge_node_map = {int(edge_tags[k]): (int(edge_nodes[k][0]), int(edge_nodes[k][1])) for k in range(len(edge_tags))}

            for etag, ftags in edge_face_map.items():
                if len(ftags) < 2:
                    continue
                n1 = face_normal_map.get(ftags[0])
                n2 = face_normal_map.get(ftags[1])
                if n1 is None or n2 is None:
                    continue

                cos_angle = float(np.dot(n1, n2))
                cos_angle = max(-1.0, min(1.0, cos_angle))
                angle = math.degrees(math.acos(cos_angle))

                en = edge_node_map.get(etag)
                if en is None:
                    continue
                p1 = node_map.get(en[0])
                p2 = node_map.get(en[1])
                if p1 is None or p2 is None:
                    continue
                length = math.sqrt(np.sum((p2 - p1) ** 2))

                if angle > 30.0 and length > 0.001:
                    feature_map.sharp_edges.append(
                        SharpEdge(
                            point_a=tuple(p1),
                            point_b=tuple(p2),
                            angle=angle,
                            length=length,
                        )
                    )
        except Exception as e:
            logger.debug("Sharp edge detection skipped: %s", e)

        # Gap detection: placeholder — real implementation requires
        # proximity queries via GMSH's `mesh.getClosestPoint` on each
        # surface pair. For now an empty list avoids corrupting cell
        # sizes with dummy data (each dummy GapRegion with width 0.001
        # would force minCell ≤ 0.0002 regardless of actual geometry).
        try:
            gmsh.model.mesh.clear()
        except Exception:
            pass

        # getBoundingBox(dim, tag) needs both positional args (missing the
        # second one crashed every call: "missing 1 required positional
        # argument: 'tag'", confirmed live) and returns one flat 6-tuple
        # (xmin, ymin, zmin, xmax, ymax, zmax), not two 3-tuples.
        bbox = gmsh.model.getBoundingBox(-1, -1)
        bbox_dim = max(bbox[i + 3] - bbox[i] for i in range(3)) * scale

        # Every length gathered above (edge lengths, gap widths, curvature
        # radius) is in the CAD file's own units, and suggest_cell_sizes()
        # compares them against bbox_dim — so they all have to be converted
        # together, or the comparisons silently mix mm with metres.
        if scale != 1.0:
            for e in feature_map.sharp_edges:
                e.length *= scale
            for g in feature_map.gap_regions:
                g.gap_width *= scale
            feature_map.curvature_radius *= scale

        feature_map.suggested_min_cell, feature_map.suggested_max_cell = (
            self.suggest_cell_sizes(feature_map, bbox_dim)
        )

        logger.info(
            "Feature analysis complete: %d sharp edges, %d gaps, "
            "curvature=%.4f, cells: %.4f/%.4f",
            len(feature_map.sharp_edges),
            len(feature_map.gap_regions),
            feature_map.curvature_radius,
            feature_map.suggested_min_cell,
            feature_map.suggested_max_cell,
        )

        # Cache the result
        cache_key = f"{filepath.resolve()}::{filepath.stat().st_mtime}::{detail}"
        self._cache[cache_key] = feature_map
        # Limit cache size
        if len(self._cache) > 20:
            for k in list(self._cache.keys())[:-10]:
                del self._cache[k]

        return feature_map

    def suggest_cell_sizes(
        self, feature_map: FeatureMap, bbox_dim: float
    ) -> tuple[float, float]:
        base_min = bbox_dim * 0.005
        base_max = bbox_dim * 0.05

        if feature_map.sharp_edges:
            min_edge_len = min(e.length for e in feature_map.sharp_edges)
            base_min = min(base_min, min_edge_len * 0.3)

        if feature_map.gap_regions:
            min_gap = min(g.gap_width for g in feature_map.gap_regions)
            base_min = min(base_min, min_gap * 0.2)

        if feature_map.curvature_radius < bbox_dim * 0.1:
            curvature_cell = feature_map.curvature_radius * 0.1
            base_min = min(base_min, curvature_cell)

        base_min = max(base_min, 1e-6)
        base_max = max(base_max, base_min * 3)

        return base_min, base_max

    def shutdown(self):
        if self._gmsh_initialized:
            try:
                import gmsh
                gmsh.finalize()
            except Exception:
                pass
            self._gmsh_initialized = False


from PySide6.QtCore import QObject, Signal, Slot


class FeatureDetectWorker(QObject):
    """Runs feature detection in a SEPARATE PROCESS, driven from a
    background QThread.

    Two independent reasons this cannot run in-process on the GUI thread:

    1. Responsiveness: analyze_step() calls gmsh.model.mesh.generate(2), a
       full 2D surface remesh. On a complex or poorly-defeatured CAD model
       GMSH can spend a long time retrying ("Splitting those edges and
       trying again", "N elements remain invalid in surface M") — the app
       showed "Not Responding" for that whole time when it ran on the GUI
       thread.

    2. Crash isolation: gmsh bundles its OWN OpenCASCADE build, while the
       app separately loads cadquery/OCP's OpenCASCADE for STEP handling.
       Both live in one process, and driving gmsh's OCC STEP reader on a
       complex model took the entire application down with a hard native
       crash — no Python traceback, just a dead process (reported live on
       both the serial and parallel meshing paths, on a geometry where
       GMSH's log showed it fighting "3 intersections in the 1D mesh").
       Feature detection is a *nice-to-have* that only refines suggested
       cell sizes; it must never be able to kill a meshing run. Running it
       out-of-process means even a segfault is just a non-zero exit code
       here, and the caller falls back to the existing cell sizes.
    """

    finished = Signal(object, object)  # (FeatureMap | None, error_str | None)

    # Generous vs. a normal run (seconds), tight enough that a pathological
    # geometry can't stall meshing indefinitely.
    TIMEOUT_S = 120

    def __init__(self, step_path: str, detail: str, scale: float = 1.0, parent=None):
        super().__init__(parent)
        self._step_path = step_path
        self._detail = detail
        self._scale = scale

    @Slot()
    def run(self):
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "cfmesh_autogui.core.feature_detector",
                    self._step_path, self._detail, str(self._scale),
                ],
                capture_output=True, text=True, timeout=self.TIMEOUT_S,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
        except subprocess.TimeoutExpired:
            self.finished.emit(
                None,
                f"feature detection exceeded {self.TIMEOUT_S}s and was cancelled",
            )
            return
        except Exception as exc:
            self.finished.emit(None, str(exc))
            return

        # Parse stdout first even on a non-zero exit: a clean "couldn't
        # read this file" still prints its reason as JSON before exiting 1,
        # and that message is far more useful than "exit code 1".
        payload = None
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            payload = None

        if payload is None:
            if proc.returncode != 0:
                # No parseable output AND a bad exit status: this is the
                # native-crash case (segfault etc.) that running
                # out-of-process exists to contain.
                tail = (proc.stderr or "").strip().splitlines()
                detail = tail[-1] if tail else f"exit code {proc.returncode}"
                self.finished.emit(None, f"feature detection crashed ({detail})")
            else:
                self.finished.emit(None, "feature detection produced no usable output")
            return

        if not payload.get("ok"):
            self.finished.emit(None, payload.get("error", "unknown error"))
            return

        feature_map = FeatureMap(
            curvature_radius=payload["curvature_radius"],
            suggested_min_cell=payload["suggested_min_cell"],
            suggested_max_cell=payload["suggested_max_cell"],
        )
        # Only the counts matter downstream (they're logged); the full edge
        # geometry isn't used by the caller, so it isn't serialised.
        feature_map.sharp_edges = [None] * payload["n_sharp_edges"]
        feature_map.gap_regions = [None] * payload["n_gap_regions"]
        self.finished.emit(feature_map, None)


def _main() -> int:
    """CLI entry point: analyse a CAD file and print one line of JSON.

    Deliberately isolated in its own process — see FeatureDetectWorker.
    """
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: <file> <detail> [scale]"}))
        return 2

    path, detail = sys.argv[1], sys.argv[2]
    scale = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

    try:
        fm = FeatureDetector().analyze_step(path, detail=detail, scale=scale)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps({
        "ok": True,
        "n_sharp_edges": len(fm.sharp_edges),
        "n_gap_regions": len(fm.gap_regions),
        "curvature_radius": fm.curvature_radius,
        "suggested_min_cell": fm.suggested_min_cell,
        "suggested_max_cell": fm.suggested_max_cell,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
