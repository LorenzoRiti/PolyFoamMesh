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

from cfmesh_autogui.commercial.adaptive_integration import (
    AdaptiveIntegrationResult,
    OODAWorkflowAdapter,
)
from cfmesh_autogui.commercial.adaptive_loop import (
    AdaptiveLoopEngine,
    AdaptiveLoopParams,
    AdaptiveLoopResult,
    BadCellCluster,
    CellQualityStatus,
    ConformalStitcher,
    ErrorType,
    FallbackStrategy,
    InProcessQualityEvaluator,
    LocalRemediator,
    MeshCell,
    MeshEdge,
    MeshEntityType,
    MeshFace,
    MeshNode,
    MeshStatistics,
    OODAPhase,
    OODAStateMachine,
    ProgressiveRefinementParams,
    ProgressiveRefinementResult,
    ProximityAwareExtrusion,
    QualityEvaluation,
    RemediationAction,
    SizingField,
    UnifiedMeshGraph,
    compute_mesh_statistics,
    evaluate_cell_quality,
    export_quality_report_json,
    find_bad_cell_clusters,
    generate_heatmap_data,
    run_progressive_refinement,
    select_fallback,
)
from cfmesh_autogui.commercial.amr import (
    AMREngine,
    AMRParams,
    AMRResult,
)
from cfmesh_autogui.commercial.batch_mesh import (
    BatchConfig,
    BatchEntry,
    BatchMesher,
    BatchReport,
)
from cfmesh_autogui.commercial.bc_editor import (
    BCEditor,
    BcPatchInfo,
)
from cfmesh_autogui.commercial.bl_engine import (
    BLCollisionReport,
    BLEngine,
    BLParameters,
    FlowConditions,
)
from cfmesh_autogui.commercial.cad_healer import CADHealer, HealReport
from cfmesh_autogui.commercial.cloud_mesh import (
    CloudMesher,
    JobStatus,
    MeshJob,
    ShareLink,
)
from cfmesh_autogui.commercial.exporter import (
    EXPORT_FORMAT_REGISTRY,
    ExportResult,
    MeshExporter,
)
from cfmesh_autogui.commercial.fault_tolerant import (
    FaultTolerantParams,
    FaultTolerantResult,
    FaultTolerantWorkflow,
)
from cfmesh_autogui.commercial.geometry_pipeline import (
    GeometryInfo,
    GeometryPipeline,
    GeometryPipelineResult,
)
from cfmesh_autogui.commercial.journal import (
    Journal,
    JournalEntry,
    JournalPlayer,
)
from cfmesh_autogui.commercial.mesh_engine import (
    ALGORITHM_INFO,
    MeshEngine,
    MeshEngineParams,
    MeshEngineResult,
    MeshingAlgorithm,
)
from cfmesh_autogui.commercial.monitor import (
    QualityMetric,
    QualityMonitor,
    QualitySnapshot,
    WorstCell,
)
from cfmesh_autogui.commercial.mosaic import (
    MosaicEngine,
    MosaicParams,
    MosaicResult,
)
from cfmesh_autogui.commercial.one_click_run import (
    FullAutoPipeline,
    FullAutoResult,
)
from cfmesh_autogui.commercial.openmp_accel import (
    OMPConfig,
    OpenMPAccel,
    ThreadMode,
)
from cfmesh_autogui.commercial.optimizer import MeshOptimizer
from cfmesh_autogui.commercial.parallel_mesh import (
    DecomposeParams,
    ParallelMeshEngine,
    ParallelMeshResult,
)
from cfmesh_autogui.commercial.poly_aggregator import (
    AggregationParams,
    AggregationResult,
    PolyAggregator,
    main as poly_aggregator_main,
)
from cfmesh_autogui.commercial.quality_engine import (
    QualityEngine,
    QualityMetrics,
    QualityReport,
)
from cfmesh_autogui.commercial.quick_mesh import (
    QuickMesh,
    QuickMeshResult,
)
from cfmesh_autogui.commercial.solver_setup import (
    MaterialProperties,
    SchemePreset,
    SolverConfig,
    SolverSetup,
    SolverType,
    TurbulenceModel,
)
from cfmesh_autogui.commercial.template_engine import (
    TemplateEngine,
    TemplateMetadata,
    TemplatePreset,
)
from cfmesh_autogui.commercial.watertight import WatertightWorkflow, WorkflowStep

__all__ = [
    "ALGORITHM_INFO",
    "EXPORT_FORMATS",
    "EXPORT_FORMAT_REGISTRY",
    "AMREngine",
    "AMRParams",
    "AMRResult",
    "AdaptiveIntegrationResult",
    "AdaptiveLoopEngine",
    "AdaptiveLoopParams",
    "AdaptiveLoopResult",
    "AggregationParams",
    "AggregationResult",
    "BCEditor",
    "BLCollisionReport",
    "BLEngine",
    "BLParameters",
    "BadCellCluster",
    "BatchConfig",
    "BatchEntry",
    "BatchMesher",
    "BatchReport",
    "BcPatchInfo",
    "CADHealer",
    "CellQualityStatus",
    "CloudMesher",
    "ConformalStitcher",
    "DecomposeParams",
    "ErrorType",
    "ExportResult",
    "FallbackStrategy",
    "FaultTolerantParams",
    "FaultTolerantResult",
    "FaultTolerantWorkflow",
    "FlowConditions",
    "FullAutoPipeline",
    "FullAutoResult",
    "GeometryInfo",
    "GeometryPipeline",
    "GeometryPipelineResult",
    "HealReport",
    "InProcessQualityEvaluator",
    "JobStatus",
    "Journal",
    "JournalEntry",
    "JournalPlayer",
    "LocalRemediator",
    "MaterialProperties",
    "MeshCell",
    "MeshEdge",
    "MeshEngine",
    "MeshEngineParams",
    "MeshEngineResult",
    "MeshEntityType",
    "MeshExporter",
    "MeshFace",
    "MeshJob",
    "MeshNode",
    "MeshOptimizer",
    "MeshStatistics",
    "MeshingAlgorithm",
    "MosaicEngine",
    "MosaicParams",
    "MosaicResult",
    "OMPConfig",
    "OODAPhase",
    "OODAStateMachine",
    "OODAWorkflowAdapter",
    "OpenMPAccel",
    "ParallelMeshEngine",
    "ParallelMeshResult",
    "PolyAggregator",
    "ProgressiveRefinementParams",
    "ProgressiveRefinementResult",
    "ProximityAwareExtrusion",
    "QualityEngine",
    "QualityEvaluation",
    "QualityMetric",
    "QualityMetrics",
    "QualityMonitor",
    "QualityReport",
    "QualitySnapshot",
    "QuickMesh",
    "QuickMeshResult",
    "RemediationAction",
    "SchemePreset",
    "ShareLink",
    "SizingField",
    "SolverConfig",
    "SolverSetup",
    "SolverType",
    "TemplateEngine",
    "TemplateMetadata",
    "TemplatePreset",
    "ThreadMode",
    "TurbulenceModel",
    "UnifiedMeshGraph",
    "WatertightWorkflow",
    "WorkflowStep",
    "WorstCell",
    "compute_mesh_statistics",
    "evaluate_cell_quality",
    "export_quality_report_json",
    "find_bad_cell_clusters",
    "generate_heatmap_data",
    "poly_aggregator_main",
    "run_progressive_refinement",
    "select_fallback",
]
