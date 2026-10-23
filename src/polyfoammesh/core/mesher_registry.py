"""One lossless registry for the mesher keys used by the GUI, presets and CLI.

W13 keeps every historical pipeline key.  The visible UI has one ``Polymesh``
CTA; the advanced ``Engine`` selector and the CLI choose among these modes.
Nothing is removed here: deleting a key from the UI must never delete the
engine, its tests or its notes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MesherMode:
    """A selectable engine and the legacy pipeline key it dispatches to."""

    key: str
    label: str
    family: str
    description: str
    default: bool = False
    experimental: bool = False
    advanced_only: bool = False


#: Every key accepted by ``MeshConfig``, the GUI and the CLI.  The order is
#: the historical order plus the W13 opt-in engine at the end.
MESHERS: tuple[str, ...] = (
    "gmsh_direct_poly",
    "ftetwild_poly",
    "cfmesh",
    "gmsh_direct",
    "auto_tet",
    "bl_on_tets",
)

#: The measured default stays the legacy GMSH tet -> dual poly path.  W10.2
#: ``auto_tet`` remains opt-in because its complete corpus A/B is not green
#: yet; the default is evidence-driven, not the button hierarchy.
DEFAULT_MESHER_KEY = "gmsh_direct_poly"

MODES: tuple[MesherMode, ...] = (
    MesherMode(
        "gmsh_direct_poly",
        "Legacy tet → poly (GMSH)",
        "poly",
        "GMSH tetrahedra followed by the measured barycentric dual.",
        default=True,
    ),
    MesherMode(
        "auto_tet",
        "Auto (GMSH surface + fTetWild volume)",
        "poly",
        "W10.2 source policy: fTetWild volume, visible GMSH fallback.",
        experimental=True,
    ),
    MesherMode(
        "ftetwild_poly",
        "fTetWild tet → poly",
        "poly",
        "fTetWild tetrahedra followed by the barycentric dual.",
        experimental=True,
    ),
    MesherMode(
        "bl_on_tets",
        "BL on tets (prototype, opt-in)",
        "poly",
        "Prismatic boundary layers directly on the tet boundary; no dual.",
        experimental=True,
    ),
    MesherMode(
        "gmsh_direct",
        "GMSH (FEM tetra)",
        "fem",
        "Pure GMSH tetrahedra without poly conversion.",
        advanced_only=True,
    ),
    MesherMode(
        "cfmesh",
        "Cartesian cfMesh (hex)",
        "hex",
        "cfMesh hex-dominant mesh, optional polyDualMesh conversion.",
        advanced_only=True,
    ),
)

#: Backwards/description-friendly alias used by the W13 brief.
PolyMeshMode = MesherMode

MODE_BY_KEY: dict[str, MesherMode] = {m.key: m for m in MODES}

#: Old project/preset names that are not keys themselves.  They keep loading
#: through one explicit adapter; no second preset format is introduced.
LEGACY_ALIASES: dict[str, str] = {
    "autopoly": "gmsh_direct_poly",
    "poly": "gmsh_direct_poly",
    "polymesh": "gmsh_direct_poly",
}

#: Options shown inside ``Polymesh > Advanced > Engine``.  The last two are
#: advanced-only legacy engines; they are not separate CTAs.
ENGINE_KEYS: tuple[str, ...] = (
    "gmsh_direct_poly",
    "auto_tet",
    "ftetwild_poly",
    "bl_on_tets",
    "gmsh_direct",
    "cfmesh",
)


def normalize_mesher_key(key: object, default: str = DEFAULT_MESHER_KEY) -> str:
    """Map a stored/CLI key to a known key, preserving every legacy path."""
    raw = str(key or "").strip()
    if raw in MODE_BY_KEY:
        return raw
    if raw in LEGACY_ALIASES:
        return LEGACY_ALIASES[raw]
    return default


def mode_for(key: object) -> MesherMode:
    return MODE_BY_KEY[normalize_mesher_key(key)]


def default_mode() -> MesherMode:
    return MODE_BY_KEY[DEFAULT_MESHER_KEY]


def is_polymesh_key(key: object) -> bool:
    return mode_for(key).family == "poly"


__all__ = [
    "DEFAULT_MESHER_KEY", "ENGINE_KEYS", "LEGACY_ALIASES", "MESHERS",
    "MODE_BY_KEY", "MODES", "MesherMode", "PolyMeshMode", "default_mode",
    "is_polymesh_key", "mode_for", "normalize_mesher_key",
]
