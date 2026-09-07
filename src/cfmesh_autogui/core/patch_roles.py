"""Canonical patch-role classification — the project's single source of truth.

Before this module the same question ("is this patch a wall?") was answered
independently, and differently, in five places:

- ``gui/main_window.py::_is_wall_patch``      (non-wall keyword blocklist)
- ``commercial/bl_engine.py::detect_wall_patches`` (wall keyword allowlist +
  a fallback that treats EVERY patch as a wall when nothing matches)
- ``commercial/bc_editor.py::_name_to_type``  (exact-match lookup tables)
- ``core/case_setup.py::_patch_role``         (inlet/outlet/wall only)
- ``core/meshdict_gen.py::infer_patch_type``  (OpenFOAM boundary type)

They disagreed. ``bl_engine`` classified ``body`` as a wall via its allowlist
while ``bc_editor`` used an exact match on the same word; ``main_window``
blocklisted the substring ``pressure`` (which also excludes the *pressure
side* of a blade — a genuine wall), while ``bl_engine`` had no such rule.

The divergence matters because the answer decides where prismatic boundary
layers are extruded. Extruding prisms into an inlet or an outlet produces a
mesh that checkMesh may still accept but that is physically wrong: the
inlet profile is applied on a stack of 1-micron cells, and the extrusion
distorts the boundary the solver reads. Every one of the five call sites
therefore now delegates here.

Precedence rule: a known NON-wall role always wins over a wall keyword. A
name containing both (``inlet_wall``) is treated as non-wall, because the
failure mode of skipping a real wall is a slightly under-resolved boundary
layer, while the failure mode of extruding into an inlet is a wrong solution.
The project already made this choice in ``_is_wall_patch``; this module just
makes it explicit and shared.
"""

from __future__ import annotations

ROLE_WALL = "wall"
ROLE_INLET = "inlet"
ROLE_OUTLET = "outlet"
ROLE_SYMMETRY = "symmetry"
ROLE_EMPTY = "empty"
ROLE_UNKNOWN = "unknown"

#: Roles that must never receive prismatic boundary layers.
NON_WALL_ROLES = frozenset({ROLE_INLET, ROLE_OUTLET, ROLE_SYMMETRY, ROLE_EMPTY})

# Ordered (role, keywords) rules — first match wins, so the more specific
# spellings must come before the generic ones ("pressure_outlet" before
# "outlet", "velocity_inlet" before "inlet").
#
# Deliberately NOT included:
#   * bare "pressure" / "velocity" — they match the *pressure side* and
#     *suction side* of a blade, which are walls. The compound spellings
#     (below) cover the OpenFOAM boundary conditions they were meant to catch.
#   * bare "in" / "out" — too short; "in" is a substring of countless names.
#   * "surface" / "body" / "boundary" — not evidence of a non-wall, and they
#     are the default names GMSH and most STL exporters produce.
# Positive wall signal. Only words that unambiguously name a solid surface.
# Deliberately NOT here: "surface", "boundary", "solid", "patch", "default",
# "sphere", "cylinder" — these are what GMSH and most STL/BREP exporters
# emit for *every* patch, including the inlet. Treating them as walls is
# what made the geometric inference unreachable for the unnamed cases it
# exists to solve.
_WALL_KEYWORDS: tuple[str, ...] = (
    "wall", "blade", "vane", "foil", "hull", "wing", "stator", "rotor",
    "casing", "housing", "shroud", "nacelle", "body", "skin", "shell",
    "piston", "valve", "duct", "pipe",
)

_NON_WALL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ROLE_OUTLET, (
        "pressure_outlet", "pressureoutlet", "pressure-outlet",
        "mass_flow_outlet", "massflowoutlet",
        "outflow", "outlet", "uscita", "exit", "opening",
        "farfield", "far_field", "far-field", "freestream", "free_stream",
    )),
    (ROLE_INLET, (
        "velocity_inlet", "velocityinlet", "velocity-inlet",
        "pressure_inlet", "pressureinlet", "pressure-inlet",
        "mass_flow_inlet", "massflowinlet",
        "inflow", "inlet", "ingresso", "entrance",
    )),
    (ROLE_SYMMETRY, (
        "symmetryplane", "symmetry_plane", "symmetry-plane", "symmetry",
        "periodic", "cyclic", "wedge", "interface", "porous", "porousbaffle",
    )),
    (ROLE_EMPTY, ("empty",)),
)


def _normalise(name: str) -> str:
    """Lower-case, unify separators, drop a trailing numeric suffix.

    ``"Inlet-02"`` -> ``"inlet"``, ``"patch.wall_1"`` -> ``"patch_wall"``,
    so the keyword rules do not have to enumerate every exporter's dialect.
    """
    n = str(name).lower().replace("-", "_").replace(".", "_").replace(" ", "_")
    return n.rstrip("_0123456789")


def match_role(name: str) -> str | None:
    """The role a *keyword* implies, or ``None`` when no rule matched.

    This is the primitive. It returns ``None`` — not a guess — for a name
    carrying no role signal, which is what a caller with a better source of
    truth needs: ``BCEditor._detect_type`` and ``case_setup._patch_role``
    both fall through to geometric inference when the name says nothing
    (GMSH names every patch ``surface_N``, so treating "no keyword" as "is
    a wall" would pre-empt geometry on exactly the cases that need it).

    Non-wall rules are matched first: a name containing both (``inlet_wall``)
    is reported as an inlet, because extruding prisms into an inlet is a
    worse failure than under-resolving a wall.
    """
    n = _normalise(name)
    for role, keywords in _NON_WALL_RULES:
        if any(kw in n for kw in keywords):
            return role
    if any(kw in n for kw in _WALL_KEYWORDS):
        return ROLE_WALL
    return None


def classify_patch(name: str) -> str:
    """Map a patch name to one of the ``ROLE_*`` constants, defaulting to wall.

    Used where a decision is mandatory and there is no fallback — notably
    choosing where prismatic boundary layers may be extruded. The majority
    of boundary patches in a CFD model are walls, so the default is the
    safe majority.

    If a geometric inference is available downstream, use ``match_role``
    instead and let geometry decide when the name is silent.
    """
    return match_role(name) or ROLE_WALL


def is_wall_patch(name: str) -> bool:
    """True when prisms may legitimately be extruded from this patch."""
    return classify_patch(name) not in NON_WALL_ROLES


def is_non_wall_patch(name: str) -> bool:
    """True for inlet / outlet / symmetry / empty — never a BL candidate."""
    return classify_patch(name) in NON_WALL_ROLES


def split_wall_patches(
    names,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Partition patch names into (walls, excluded).

    ``excluded`` carries ``(name, role)`` pairs so callers can tell the user
    *why* a patch was left out rather than silently dropping it — which is
    exactly how the old ``bl_engine`` fallback hid the problem.
    """
    walls: list[str] = []
    excluded: list[tuple[str, str]] = []
    for name in names:
        role = classify_patch(name)
        if role in NON_WALL_ROLES:
            excluded.append((name, role))
        else:
            walls.append(name)
    return walls, excluded
