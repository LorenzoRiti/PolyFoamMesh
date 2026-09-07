"""Single source of truth for mesh-quality thresholds.

The three threshold dicts used to live in three places:

- ``commercial/mesh_engine.py``   → ``ADAPTIVE_THRESHOLDS``
- ``commercial/quality_engine.py`` → ``QUALITY_THRESHOLDS``
- ``commercial/optimizer.py``      → ``OPTIMIZER_THRESHOLDS``

They were identical in spirit but duplicated in code, so a threshold
change had to be applied in N places and silently drifted.  They now all
derive from one canonical ``THRESHOLDS`` dict below.

DECISION (2026-08-12, product owner): ``skewness_max`` is 4.0 on EVERY
path — the checkMesh-native bar.  Previously the analysis / auto-escalation
paths used 0.9 while the optimiser loop used 4.0, so a mesh with skewness
between 0.9 and 4.0 was flagged by analysis but declared "good enough" by
the auto-fix loop.  Now every consumer uses the same bar and the
acceptance check is consistently "max metric ≤ threshold" (accept-on-max).
The stricter 0.9 bar is still reachable per-call: every consumer accepts a
``thresholds`` override dict.

``non_ortho_max`` intentionally stays stricter in the optimiser loop
(65.0 vs 70.0 for analysis): the auto-fix loop pushes non-orthogonality
further below the checkMesh limit before declaring convergence.  That
divergence is deliberate and documented here, not drift.
"""

# Canonical threshold set — every consumer checks "max metric <= threshold".
THRESHOLDS: dict[str, float] = {
    "skewness_max": 4.0,        # checkMesh-native skewness bar (all paths)
    "non_ortho_max": 70.0,      # max non-orthogonality in degrees
    "aspect_ratio_max": 1000.0,  # max aspect ratio
}

# Analysis / auto-escalation thresholds (was mesh_engine.ADAPTIVE_THRESHOLDS).
ADAPTIVE_THRESHOLDS: dict[str, float] = dict(THRESHOLDS)

# Quality-engine thresholds (was quality_engine.THRESHOLDS).
QUALITY_THRESHOLDS: dict[str, float] = {
    **THRESHOLDS,
    "neg_vol_max": 0,
}

# Optimiser-loop thresholds (was optimizer.MeshOptimizer.THRESHOLDS).
# non_ortho_max is intentionally stricter than the canonical 70.0 — see the
# module docstring.
OPTIMIZER_THRESHOLDS: dict[str, float] = {
    **THRESHOLDS,
    "non_ortho_max": 65.0,
}
