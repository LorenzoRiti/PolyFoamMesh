"""Single source of truth for mesh-quality thresholds (Fase 1 lane A).

The three threshold dicts used to live in three places:

- ``commercial/mesh_engine.py``   → ``ADAPTIVE_THRESHOLDS``
- ``commercial/quality_engine.py`` → ``THRESHOLDS``
- ``commercial/optimizer.py``      → ``MeshOptimizer.THRESHOLDS``

They were identical in spirit but duplicated in code, so a threshold
change had to be applied in N places and silently drifted. They now live
here with UNCHANGED values — this commit only moves the location.

NOTE — decision under review, do not silently change: ``skewness_max``
differs between the analysis path (0.9) and the optimiser loop (4.0).
``MeshOptimizer._passes_quality`` deliberately uses 4.0 so the auto-fix
loop does not declare success at the first mildly-skewed mesh and keeps
iterating until skewness is actually tamed; the analysis / auto-
escalation path (mesh_engine, quality_engine) treats 0.9 as the quality
bar. Whether one value should win for both paths is a product decision,
not something this consolidation should decide.
"""

# Analysis / auto-escalation thresholds (was mesh_engine.ADAPTIVE_THRESHOLDS).
ADAPTIVE_THRESHOLDS: dict[str, float] = {
    "non_ortho_max": 70.0,      # max non-orthogonality in degrees
    "skewness_max": 0.9,        # max skewness
    "aspect_ratio_max": 1000.0,  # max aspect ratio
}

# Quality-engine thresholds (was quality_engine.THRESHOLDS).
QUALITY_THRESHOLDS: dict[str, float] = {
    "skewness_max": 0.9,
    "non_ortho_max": 70.0,
    "aspect_ratio_max": 1000.0,
    "neg_vol_max": 0,
}

# Optimiser-loop thresholds (was optimizer.MeshOptimizer.THRESHOLDS).
OPTIMIZER_THRESHOLDS: dict[str, float] = {
    "skewness_max": 4.0,
    "non_ortho_max": 65.0,
    "aspect_ratio_max": 1000.0,
}
