# -*- coding: utf-8 -*-
"""One tet-source contract and the opt-in GMSH/fTetWild policy (W10.2).

The hybrid already exists: for STEP files ``ftetwild_wrapper.run_volume``
builds a GMSH surface, carries the physical groups in a tagged NPZ, and runs
fTetWild on that surface.  This module gives that composition a result
schema, a deterministic preflight/fallback policy and provenance, without
merging two volume meshes (a union would have a non-conforming interface).

``auto`` is opt-in.  Explicit ``gmsh`` / ``ftetwild`` never change.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Mapping

logger = logging.getLogger(__name__)

SOURCE_GMSH = "gmsh"
SOURCE_FTETWILD = "ftetwild"
SOURCE_AUTO = "auto"
SOURCES = (SOURCE_GMSH, SOURCE_FTETWILD, SOURCE_AUTO)
STEP_SUFFIXES = frozenset({".step", ".stp", ".iges", ".igs"})
STL_SUFFIXES = frozenset({".stl"})


def ftetwild_available() -> bool:
    """True when the optional pytetwild package can be imported."""
    try:
        return importlib.util.find_spec("pytetwild") is not None
    except (ImportError, ValueError):
        return False


@dataclass(frozen=True)
class TetSourceSettings:
    """Portable settings for either source (serializable, source-neutral)."""

    source: str = SOURCE_GMSH
    detail: str = "medium"
    max_cell: float = 0.0
    min_cell: float = 0.0
    max_cells_target: int = 0
    surface_resolution: str = "medium"
    refinement_zones: tuple[dict, ...] = ()
    feature_angle: float | None = None
    n_layers: int = 0
    bl_thickness: float | None = None
    bl_expansion: float = 1.2
    ftet_eps_frac: float = 0.15
    ftet_simplify: bool = False
    ftet_adaptive: bool = True
    ftet_opt_iter: int | None = None
    scale: float = 1.0

    def to_mapping(self) -> dict:
        return {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class TetSourcePreflight:
    geometry_path: str
    suffix: str
    detail: str
    fTetWild_available: bool
    refinement_requested: bool
    feature_angle: float | None
    scale: float
    size_bytes: int | None = None
    mtime_ns: int | None = None
    # W18 geometry-auto signals (None = not measured, rule skips them).
    # watertight is probed cheaply for STL via trimesh; STEP skips it
    # (a GMSH surface pass costs ~1 min — declared limit, not silence).
    watertight: bool | None = None
    patch_count: int | None = None
    bbox_diag_m: float | None = None
    # W18.1: STEP solid count (None = not probed/failed). A solid-less
    # STEP (open shells, no volumes) cannot feed a GMSH volume mesh at
    # all — the policy must not waste a full GMSH run on it.
    solid_count: int | None = None

    def to_mapping(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class TetSourcePlan:
    requested: str
    primary: str
    fallback: str | None
    reason: str

    def to_mapping(self) -> dict:
        return dict(self.__dict__)


@dataclass
class TetSourceAttempt:
    source: str
    success: bool = False
    path: str | None = None
    names: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    stage_times: dict = field(default_factory=dict)
    error: str = ""
    n_tets: int | None = None
    wall_seconds: float = 0.0

    def to_mapping(self) -> dict:
        return {
            "source": self.source,
            "success": self.success,
            "path": self.path,
            "names": list(self.names),
            "stats": dict(self.stats),
            "stage_times": dict(self.stage_times),
            "error": self.error,
            "n_tets": self.n_tets,
            "wall_seconds": round(self.wall_seconds, 3),
        }


@dataclass
class TetSourceResult:
    settings: TetSourceSettings
    plan: TetSourcePlan
    attempts: list[TetSourceAttempt]
    effective_source: str = ""
    path: str | None = None
    names: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    stage_times: dict = field(default_factory=dict)
    failure_reason: str = ""
    fallback_from: str = ""
    fingerprint: str = ""

    @property
    def success(self) -> bool:
        return bool(self.path)

    def to_mapping(self) -> dict:
        return {
            "success": self.success,
            "requested_source": self.settings.source,
            "effective_source": self.effective_source,
            "primary": self.plan.primary,
            "fallback": self.plan.fallback,
            "plan_reason": self.plan.reason,
            "fallback_from": self.fallback_from,
            "path": self.path,
            "names": list(self.names),
            "n_tets": self.stats.get("n_tets"),
            "stats": dict(self.stats),
            "stage_times": dict(self.stage_times),
            "failure_reason": self.failure_reason,
            "fingerprint": self.fingerprint,
            "settings": self.settings.to_mapping(),
            "attempts": [a.to_mapping() for a in self.attempts],
        }


def preflight_tet_source(
    geometry_path: Path | str,
    *,
    detail: str = "medium",
    refinement_zones: tuple[dict, ...] | list[dict] | None = None,
    feature_angle: float | None = None,
    scale: float = 1.0,
    fTetWild_available: bool | None = None,
    probe_surface: bool = True,
) -> TetSourcePreflight:
    path = Path(geometry_path)
    try:
        st = path.stat()
        size, mtime = st.st_size, st.st_mtime_ns
    except OSError:
        size, mtime = None, None
    suffix = path.suffix.lower()
    watertight: bool | None = None
    patch_count: int | None = None
    bbox_diag: float | None = None
    if probe_surface and suffix in STL_SUFFIXES and path.is_file():
        # Cheap trimesh probe (seconds on typical STLs, fail-soft):
        # watertightness decides open-input routing.  STEP skips this —
        # probing would need a full GMSH surface pass (declared, not hidden).
        try:
            import numpy as _np
            import trimesh as _tm

            _mesh = _tm.load(str(path), force="mesh")
            watertight = bool(_mesh.is_watertight)
            _box = _np.asarray(_mesh.bounds, dtype=float)
            if _box.shape == (2, 3) and _np.all(_np.isfinite(_box)):
                bbox_diag = float(_np.linalg.norm(_box[1] - _box[0]))
        except Exception:
            watertight, bbox_diag = None, None
    solid_count: int | None = None
    if probe_surface and suffix in STEP_SUFFIXES and path.is_file():
        # Cheap OCC solid count (fail-soft): a solid-less STEP (open
        # shells, measured on Parte4 30/09: 0 solids / 89 surfaces) can
        # never feed a GMSH volume mesh — the policy must route it to
        # fTetWild (envelope fills) instead of wasting a full GMSH run
        # that yields a 2D-only mesh and dies at convert.
        try:
            from polyfoammesh.core.gmsh_wrapper import _ensure_gmsh

            _gmsh = _ensure_gmsh()
            _gmsh.clear()
            _gmsh.open(str(path.resolve()))
            solid_count = len(_gmsh.model.getEntities(3))
        except Exception:
            solid_count = None
    return TetSourcePreflight(
        geometry_path=str(path.resolve()) if path.exists() else str(path),
        suffix=suffix,
        detail=str(detail),
        fTetWild_available=(
            ftetwild_available()
            if fTetWild_available is None else bool(fTetWild_available)
        ),
        refinement_requested=bool(refinement_zones),
        feature_angle=feature_angle,
        scale=float(scale or 1.0),
        size_bytes=size,
        mtime_ns=mtime,
        watertight=watertight,
        patch_count=patch_count,
        bbox_diag_m=bbox_diag,
        solid_count=solid_count,
    )


def choose_tet_source(
    preflight: TetSourcePreflight, requested: str = SOURCE_AUTO,
) -> TetSourcePlan:
    """Deterministic source plan from measured preflight facts.

    STEP/IGES + fTetWild -> hybrid (GMSH surface + fTetWild volume) because
    the measured volume/quality advantage is real and physical groups travel
    in the tagged NPZ.  STL -> fTetWild because GMSH cannot rebuild an
    imported discrete surface (verified empty output).  If the optional
    package is absent -> GMSH, never a crash.  The other source is the
    single fallback.
    """
    requested = str(requested or SOURCE_GMSH).strip().lower()
    if requested not in SOURCES:
        raise ValueError(
            f"unknown tet source {requested!r}; expected {', '.join(SOURCES)}"
        )
    if requested != SOURCE_AUTO:
        fallback = (
            SOURCE_FTETWILD if requested == SOURCE_GMSH
            else SOURCE_GMSH
        )
        if fallback == SOURCE_FTETWILD and not preflight.fTetWild_available:
            fallback = None
        return TetSourcePlan(
            requested=requested, primary=requested, fallback=fallback,
            reason=f"explicit {requested}",
        )
    if not preflight.fTetWild_available:
        return TetSourcePlan(
            requested=SOURCE_AUTO, primary=SOURCE_GMSH, fallback=None,
            reason="pytetwild unavailable -> GMSH",
        )
    if preflight.suffix in STL_SUFFIXES:
        return TetSourcePlan(
            requested=SOURCE_AUTO, primary=SOURCE_FTETWILD, fallback=SOURCE_GMSH,
            reason="STL input: GMSH surface remesh impossible -> fTetWild",
        )
    if preflight.suffix in STEP_SUFFIXES:
        detail = (
            " with GMSH surface/refinement" if preflight.refinement_requested
            else ""
        )
        return TetSourcePlan(
            requested=SOURCE_AUTO, primary=SOURCE_FTETWILD, fallback=SOURCE_GMSH,
            reason=f"CAD input: measured fTetWild volume{detail}"
            " -> GMSH fallback",
        )
    # Unknown extension: keep the robust volume engine as primary if present.
    return TetSourcePlan(
        requested=SOURCE_AUTO, primary=SOURCE_FTETWILD, fallback=SOURCE_GMSH,
        reason="unknown geometry suffix: fTetWild primary, GMSH fallback",
    )


#: Mesher keys the W18 geometry-auto may select.  ``bl_on_tets`` is
#: deliberately absent (W13: opt-in prototype, never promoted to auto).
AUTO_MESHER_KEYS = ("gmsh_direct_poly", "ftetwild_poly")


@dataclass(frozen=True)
class PolymeshEnginePlan:
    """Geometry-driven engine choice for the ``auto`` Polymesh CTA.

    ``primary`` runs first; on tet-stage failure or an unaccepted
    checkMesh FAIL the runner tries ``fallback1`` then ``fallback2``.
    ``reason`` names the measured features behind the choice and is
    logged/reported verbatim — no silent routing.
    """

    primary: str
    fallback1: str | None
    fallback2: str | None
    reason: str

    def chain(self) -> list[str]:
        """Ordered attempt list, no empties, no repeats."""
        out: list[str] = []
        for key in (self.primary, self.fallback1, self.fallback2):
            if key and key not in out:
                out.append(key)
        return out

    def to_mapping(self) -> dict:
        return dict(self.__dict__)


def choose_polymesh_engine(
    preflight: TetSourcePreflight,
) -> PolymeshEnginePlan:
    """Deterministic engine policy from measured preflight features.

    Table-driven on the W18 winners table (``notes/w18_*``), never on
    geometry names — overfitting on names is forbidden:
    - open/non-manifold input (measured, STL probe) -> fTetWild: GMSH
      cannot rebuild an imported discrete surface (verified empty output),
      and rejects open solids outright (WALL-03);
    - pytetwild absent -> GMSH, never a crash;
    - default -> GMSH tet->dual (9/10 Lane A) with fTetWild fallback;
      the fallback chain plus the checkMesh gate is what wins the cases
      where GMSH loses (mini_valve), not an oracle.
    ``bl_on_tets`` is never selected (W13 opt-in verdict stands).
    """
    forced = (os.environ.get("POLYFOAMMESH_TET_ENGINE") or "").strip().lower()
    if forced in ("gmsh", "gmsh_direct_poly"):
        return PolymeshEnginePlan(
            primary="gmsh_direct_poly",
            fallback1="ftetwild_poly" if preflight.fTetWild_available else None,
            fallback2=None,
            reason="POLYFOAMMESH_TET_ENGINE=gmsh override (debug hatch, logged)",
        )
    if forced in ("ftetwild", "ftetwild_poly", "ftet", "tetwild"):
        if not preflight.fTetWild_available:
            raise ValueError(
                "POLYFOAMMESH_TET_ENGINE=ftetwild but pytetwild is not installed"
            )
        return PolymeshEnginePlan(
            primary="ftetwild_poly",
            fallback1="gmsh_direct_poly",
            fallback2=None,
            reason="POLYFOAMMESH_TET_ENGINE=ftetwild override (debug hatch, logged)",
        )
    if forced:
        raise ValueError(
            f"unknown POLYFOAMMESH_TET_ENGINE={forced!r}; expected gmsh|ftetwild"
        )
    ftet: str | None = "ftetwild_poly" if preflight.fTetWild_available else None
    if preflight.watertight is False:
        if not preflight.fTetWild_available:
            # No engine can handle an open input without pytetwild:
            # GMSH cannot rebuild it, fTetWild is absent.  Fail loudly
            # with a named cause instead of picking a doomed primary.
            return PolymeshEnginePlan(
                primary="gmsh_direct_poly",
                fallback1=None,
                fallback2=None,
                reason=(
                    "open/non-manifold input but pytetwild is missing: "
                    "no engine can mesh it (GMSH cannot rebuild open "
                    "solids); install pytetwild for the fTetWild path"
                ),
            )
        return PolymeshEnginePlan(
            primary="ftetwild_poly",
            fallback1="gmsh_direct_poly",
            fallback2=None,
            reason=(
                f"open/non-manifold {preflight.suffix or 'input'} "
                "(measured watertight=false): GMSH cannot rebuild it -> "
                "fTetWild primary, GMSH fallback"
            ),
        )
    if not preflight.fTetWild_available:
        return PolymeshEnginePlan(
            primary="gmsh_direct_poly",
            fallback1=None,
            fallback2=None,
            reason="pytetwild unavailable -> GMSH only, no fallback",
        )
    if preflight.solid_count == 0:
        return PolymeshEnginePlan(
            primary="ftetwild_poly",
            fallback1=None,
            fallback2=None,
            reason=(
                f"STEP with no solids ({preflight.solid_count} volumes, "
                "open shells — measured): GMSH cannot volume-mesh it "
                "(2D-only output dies at convert); fTetWild envelope only"
            ),
        )
    return PolymeshEnginePlan(
        primary="gmsh_direct_poly",
        fallback1=ftet,
        fallback2=None,
        reason=(
            "default: measured GMSH tet->dual wins 9/10 Lane A; "
            "fTetWild fallback on failure/invalid mesh"
        ),
    )


def _has_volume_elements(msh: Path, _scan_lines: int = 200_000) -> bool:
    """True when a GMSH MSH file contains at least one 3D element.

    A surface-only mesh (e.g. from a solid-less STEP: GMSH meshes the
    2D shells and stops) is NOT a valid tet stage — without this check
    the auto chain would "succeed" on 2D output and die later at convert
    with no fallback.  Supports MSH 2.2 ($Elements id/type lines, type
    4/5/6/7/11 = tet/hex/prism/pyramid/tet10) and MSH 4.x (block header
    ``entityDim==3`` with nonzero count).  Unknown formats fail OPEN
    (treated as valid) so an exotic-but-good mesh is never rejected.
    """
    try:
        with open(msh, encoding="utf-8", errors="replace") as fh:
            header = ""
            for _ in range(6):
                header += fh.readline()
            if "$MeshFormat" not in header:
                return True
            m = re.search(r"\$MeshFormat\n(\d+)\.", header)
            major = int(m.group(1)) if m else 0
            if major == 2:
                for i, line in enumerate(fh):
                    if i > _scan_lines:
                        break
                    s = line.strip()
                    if not s or not s[0].isdigit():
                        continue
                    parts = s.split()
                    if len(parts) >= 2 and parts[1] in ("4", "5", "6", "7", "11"):
                        return True
                return False
            if major == 4:
                for i, line in enumerate(fh):
                    if i > _scan_lines:
                        break
                    s = line.strip()
                    parts = s.split()
                    if (len(parts) == 4 and parts[0] == "3"
                            and parts[3].isdigit() and int(parts[3]) > 0):
                        return True
                return False
            return True
    except OSError:
        return True


def auto_direct_bl_next(
    auto_active: bool,
    remaining: list,
    direct_done: bool,
    bl_enabled: bool,
    last_tet_ok: str | None,
) -> str | None:
    """Direct-BL fallback leg for Polymesh auto (pure decision, tested).

    When the mesher chain is exhausted and a tet mesh validated earlier,
    restart with that tet engine and build the BL directly (no dual).
    Requires BL enabled in the panel (no silent BL).  Returns the tet
    engine key to re-run, or None (stay on the standard error path).
    """
    if not auto_active:
        return None
    if remaining or direct_done or not bl_enabled or not last_tet_ok:
        return None
    return last_tet_ok


def _fingerprint(settings: TetSourceSettings, geometry: Path) -> str:
    payload = {
        "settings": settings.to_mapping(),
        "geometry": str(geometry.resolve()),
    }
    try:
        st = geometry.stat()
        payload["size"], payload["mtime_ns"] = st.st_size, st.st_mtime_ns
    except OSError:
        payload["size"] = payload["mtime_ns"] = None
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload = {
        "settings": settings.to_mapping(),
        "geometry": str(geometry.resolve()),
    }
    try:
        st = geometry.stat()
        payload["size"], payload["mtime_ns"] = st.st_size, st.st_mtime_ns
    except OSError:
        payload["size"] = payload["mtime_ns"] = None
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _default_gmsh_runner(
    settings: TetSourceSettings, geometry: Path, msh_path: Path,
) -> tuple[str, list[str], dict, dict]:
    from polyfoammesh.core.gmsh_wrapper import generate_volume_mesh

    t0 = time.perf_counter()
    path, names = generate_volume_mesh(
        geometry, msh_path, detail=settings.detail,
        user_lc=settings.max_cell or None,
        n_layers=settings.n_layers,
        bl_thickness=settings.bl_thickness,
        bl_expansion=settings.bl_expansion,
        refinement_zones=list(settings.refinement_zones) or None,
        min_cell_size=settings.min_cell or None,
        max_cells_override=settings.max_cells_target or None,
        surface_resolution=settings.surface_resolution,
        scale=settings.scale,
    )
    return str(path), list(names), {}, {
        "total": round(time.perf_counter() - t0, 3),
    }


def _default_ftetwild_runner(
    settings: TetSourceSettings, geometry: Path, msh_path: Path,
) -> tuple[str, list[str], dict, dict]:
    from polyfoammesh.core.ftetwild_wrapper import run_volume

    payload = run_volume(
        geometry, msh_path, detail=settings.detail,
        max_cell=settings.max_cell, opt_iter=settings.ftet_opt_iter,
        eps_frac=settings.ftet_eps_frac, adaptive=settings.ftet_adaptive,
        scale=settings.scale, simplify=settings.ftet_simplify,
        refinement_zones=list(settings.refinement_zones) or None,
        surface_resolution=settings.surface_resolution,
        feature_angle=settings.feature_angle,
    )
    if not payload.get("success"):
        raise RuntimeError(payload.get("error") or "fTetWild failed")
    stats = {
        "n_nodes": payload.get("n_nodes"),
        "n_tets": payload.get("n_tets"),
        "tagging": payload.get("tagging", {}),
        "epsilon_abs": payload.get("epsilon_abs"),
        "surface_refinement_applied": payload.get("surface_refinement_applied"),
        "surface_refinement_stats": payload.get("surface_refinement_stats", {}),
        "feature_angle_applied": payload.get("feature_angle_applied", False),
    }
    stage = {
        "surface": payload.get("surface_time_s", 0.0),
        "tet": payload.get("tet_time_s", 0.0),
        "total": payload.get("total_time_s", 0.0),
    }
    return str(payload["path"]), list(payload.get("names") or []), stats, stage


def run_tet_source(
    geometry_path: Path | str,
    msh_path: Path | str,
    settings: TetSourceSettings | None = None,
    *,
    requested: str | None = None,
    runners: Mapping[str, Callable] | None = None,
    log: Callable[[str], None] | None = None,
    run_both: bool = False,
) -> TetSourceResult:
    """Run the selected source, fall back once, and keep the valid result.

    *runners* is injectable for tests; the default runners call the real
    GMSH/fTetWild wrappers.  A result is successful only when the MSH exists,
    is non-empty and has at least one patch name.  A failed/empty attempt can
    never replace a valid one.
    """
    settings = settings or TetSourceSettings()
    requested = requested or settings.source
    if requested != settings.source:
        settings = replace(settings, source=requested)
    geometry = Path(geometry_path)
    msh = Path(msh_path)
    preflight = preflight_tet_source(
        geometry, detail=settings.detail,
        refinement_zones=settings.refinement_zones,
        feature_angle=settings.feature_angle,
        scale=settings.scale,
    )
    plan = choose_tet_source(preflight, requested)
    runners = dict(runners or {
        SOURCE_GMSH: _default_gmsh_runner,
        SOURCE_FTETWILD: _default_ftetwild_runner,
    })
    sources = [plan.primary]
    if plan.fallback:
        sources.append(plan.fallback)
    attempts: list[TetSourceAttempt] = []
    best: TetSourceAttempt | None = None
    emit = log or (lambda _m: None)
    for source in sources:
        emit(
            f"[tet-source] {source}: detail={settings.detail} "
            f"surface={settings.surface_resolution} "
            f"refinement={len(settings.refinement_zones)} zone(s)"
        )
        t0 = time.perf_counter()
        try:
            runner = runners.get(source)
            if runner is None:
                raise RuntimeError(f"no runner for source {source!r}")
            path, names, stats, stages = runner(settings, geometry, msh)
            wall = time.perf_counter() - t0
            ok = bool(path) and Path(path).is_file() and Path(path).stat().st_size > 0
            ok = ok and bool(names)
            if ok and not _has_volume_elements(Path(path)):
                # Surface-only output (e.g. GMSH on a solid-less STEP):
                # not a valid tet stage — fail fast so the fallback runs
                # instead of dying later at convert with no chain left.
                ok = False
                _vol_err = "MSH has no 3D elements (surface-only mesh)"
            else:
                _vol_err = ""
            attempt = TetSourceAttempt(
                source=source, success=ok, path=str(path) if path else None,
                names=list(names), stats=dict(stats or {}),
                stage_times=dict(stages or {}), wall_seconds=wall,
                n_tets=(stats or {}).get("n_tets"),
                error="" if ok else (_vol_err or "empty MSH or no patch names"),
            )
        except Exception as exc:  # noqa: BLE001 - one source must not crash
            attempt = TetSourceAttempt(
                source=source, error=f"{type(exc).__name__}: {exc}",
                wall_seconds=time.perf_counter() - t0,
            )
            emit(f"[tet-source] {source} failed: {attempt.error}")
        attempts.append(attempt)
        if best is None or _score(attempt) > _score(best):
            best = attempt
        if attempt.success and not run_both:
            break
    if best is None or not best.success:
        return TetSourceResult(
            settings=settings, plan=plan, attempts=attempts,
            failure_reason="; ".join(a.error for a in attempts if a.error)
            or "all sources failed",
            fingerprint=_fingerprint(settings, geometry),
        )
    return TetSourceResult(
        settings=settings, plan=plan, attempts=attempts,
        effective_source=best.source, path=best.path, names=best.names,
        stats=best.stats, stage_times=best.stage_times,
        fallback_from=(
            plan.primary if best.source != plan.primary else ""
        ),
        fingerprint=_fingerprint(settings, geometry),
    )


def _score(attempt: TetSourceAttempt) -> tuple:
    return (
        int(attempt.success),
        len(attempt.names),
        -(attempt.wall_seconds or 0.0),
    )


def union_is_conforming() -> bool:
    """W10.2 guard: no proven conforming interface, so no mesh union.

    Kept as an explicit, testable rejection instead of a silent merge path.
    """
    return False


# ---------------------------------------------------------------------------
# CLI (same contract as the workers; useful for a source A/B)
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m polyfoammesh.core.tet_source",
        description="Run one tet source (or auto) and print TetSourceResult JSON.",
    )
    parser.add_argument("geometry")
    parser.add_argument("msh")
    parser.add_argument("--source", choices=SOURCES, default=SOURCE_AUTO)
    parser.add_argument("--detail", default="medium")
    parser.add_argument("--max-cell", type=float, default=0.0)
    parser.add_argument("--surface-resolution", default="medium")
    parser.add_argument("--refinement-zones", default="")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args(argv)
    zones = []
    if args.refinement_zones:
        zones = json.loads(args.refinement_zones)
    result = run_tet_source(
        args.geometry, args.msh,
        TetSourceSettings(
            source=args.source, detail=args.detail,
            max_cell=args.max_cell,
            surface_resolution=args.surface_resolution,
            refinement_zones=tuple(zones),
            scale=args.scale,
        ),
        requested=args.source,
    )
    print(json.dumps(result.to_mapping(), indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
