"""Commercial-grade mesh generation modules.

Provides advanced meshing capabilities inspired by commercial CFD
pre-processors (ANSYS Fluent Meshing, Star-CCM+, Pointwise) while
staying 100 % OpenFOAM-compatible.

Modules
-------
watertight      — Watertight (closed-volume) workflow.
fault_tolerant  — Fault-tolerant workflow for non-watertight geometry.
bl_engine       — Boundary Layer quality engine (y+, collision, params).
optimizer       — Mesh quality optimisation engine.
cad_healer      — CAD defeaturing, hole-filling, stitch, simplify.
"""

from __future__ import annotations

from cfmesh_autogui.commercial.watertight import WatertightWorkflow, WorkflowStep
from cfmesh_autogui.commercial.optimizer import MeshOptimizer, QualityReport
from cfmesh_autogui.commercial.cad_healer import CADHealer, HealReport
from cfmesh_autogui.commercial.fault_tolerant import (
    FaultTolerantWorkflow, FaultTolerantParams, FaultTolerantResult,
)
from cfmesh_autogui.commercial.bl_engine import (
    BLEngine, BLParameters, FlowConditions, BLCollisionReport,
)
from cfmesh_autogui.commercial.parallel_mesh import (
    ParallelMeshEngine, DecomposeParams, ParallelMeshResult,
)
from cfmesh_autogui.commercial.mosaic import (
    MosaicEngine, MosaicParams, MosaicResult,
)
from cfmesh_autogui.commercial.batch_mesh import (
    BatchMesher, BatchConfig, BatchReport, BatchEntry,
)
from cfmesh_autogui.commercial.exporter import (
    MeshExporter, ExportResult, EXPORT_FORMATS,
)
from cfmesh_autogui.commercial.monitor import (
    QualityMonitor, QualityMetric, QualitySnapshot, WorstCell,
)
from cfmesh_autogui.commercial.cloud_mesh import (
    CloudMesher, MeshJob, JobStatus, ShareLink,
)
from cfmesh_autogui.commercial.journal import (
    Journal, JournalEntry, JournalPlayer,
)
from cfmesh_autogui.commercial.amr import (
    AMREngine, AMRParams, AMRResult,
)
from cfmesh_autogui.commercial.solver_setup import (
    SolverSetup, SolverConfig, SolverType,
    TurbulenceModel, SchemePreset, MaterialProperties,
)
from cfmesh_autogui.commercial.template_engine import (
    TemplateEngine, TemplatePreset, TemplateMetadata,
)
from cfmesh_autogui.commercial.geometry_pipeline import (
    GeometryPipeline, GeometryInfo, GeometryPipelineResult,
)
from cfmesh_autogui.commercial.mesh_engine import (
    MeshEngine, MeshEngineParams, MeshEngineResult,
    MeshingAlgorithm, ALGORITHM_INFO,
)
from cfmesh_autogui.commercial.quick_mesh import (
    QuickMesh, QuickMeshResult,
)
from cfmesh_autogui.commercial.quality_engine import (
    QualityEngine, QualityReport, QualityMetrics,
)
from cfmesh_autogui.commercial.bc_editor import (
    BCEditor, PatchInfo,
)
from cfmesh_autogui.commercial.one_click_run import (
    FullAutoPipeline, FullAutoResult,
)

__all__ = [
    "WatertightWorkflow", "WorkflowStep",
    "MeshOptimizer", "QualityReport",
    "CADHealer", "HealReport",
    "FaultTolerantWorkflow", "FaultTolerantParams", "FaultTolerantResult",
    "BLEngine", "BLParameters", "FlowConditions", "BLCollisionReport",
    "ParallelMeshEngine", "DecomposeParams", "ParallelMeshResult",
    "MosaicEngine", "MosaicParams", "MosaicResult",
    "BatchMesher", "BatchConfig", "BatchReport", "BatchEntry",
    "MeshExporter", "ExportResult", "EXPORT_FORMATS",
    "QualityMonitor", "QualityMetric", "QualitySnapshot", "WorstCell",
    "CloudMesher", "MeshJob", "JobStatus", "ShareLink",
    "Journal", "JournalEntry", "JournalPlayer",
    "AMREngine", "AMRParams", "AMRResult",
    "SolverSetup", "SolverConfig", "SolverType",
    "TurbulenceModel", "SchemePreset", "MaterialProperties",
    "TemplateEngine", "TemplatePreset", "TemplateMetadata",
    "GeometryPipeline", "GeometryInfo", "GeometryPipelineResult",
    "MeshEngine", "MeshEngineParams", "MeshEngineResult",
    "MeshingAlgorithm", "ALGORITHM_INFO",
    "QuickMesh", "QuickMeshResult",
    "QualityEngine", "QualityReport", "QualityMetrics",
    "BCEditor", "PatchInfo",
    "FullAutoPipeline", "FullAutoResult",
]
