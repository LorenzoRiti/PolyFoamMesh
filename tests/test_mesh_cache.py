"""Mesh-cache: fingerprint stability/sensitivity, store/lookup, prune, clear.

The cache must be keyed on TET-mesh inputs only, so boundary-layer changes
(y+/layers) reuse the same mesh; every operation is fail-soft.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from polyfoammesh.core import mesh_cache  # noqa: E402


BASE_INPUTS = {
    "geometry_path": "C:/geom/part.step",
    "geometry_size": 1000,
    "geometry_mtime_ns": 111,
    "mesher": "gmsh_direct_poly",
    "detail": "medium",
    "adaptive": True,
    "max_cell": 0.0,
    "min_cell": 0.0,
    "max_cells_target": 500_000,
    "surface_resolution": "Medium",
    "feature_angle": 30.0,
    "refinement_zones": [{"centre": [0.0, 0.0, 0.0], "radius": 0.1, "cell_size": 0.01}],
    "size_field_file": None,
}

MUTATIONS = {
    "geometry_path": "C:/geom/other.step",
    "geometry_size": 2000,
    "geometry_mtime_ns": 222,
    "mesher": "gmsh_direct",
    "detail": "fine",
    "adaptive": False,
    "max_cell": 0.1,
    "min_cell": 0.01,
    "max_cells_target": 1000,
    "surface_resolution": "Fine",
    "feature_angle": 45.0,
    "refinement_zones": [{"centre": [1.0, 1.0, 1.0], "radius": 0.2, "cell_size": 0.02}],
    "size_field_file": "C:/geom/size.field",
}

#: BL inputs that SHAPE the .msh itself: the GMSH stage creates a
#: BoundaryLayer field from n_layers/bl_thickness/bl_expansion (the
#: near-wall tet layout the dual is built on) and a wall-size floor field.
#: These MUST be part of the fingerprint (a stale .msh after a BL change
#: was a real correctness bug found in review).
GMSH_SHAPING_BL_KEYS = (
    "n_layers",
    "bl_thickness",
    "bl_expansion",
    "bl_wall_size_floor",
)

#: BL settings that never touch the GMSH stage: excluded by the allowlist.
NON_GMSH_BL_KEYS = (
    "firstLayerThickness",
    "thicknessRatio",
    "applyToAll",
    "minBaseEdgeFraction",
    "terminateByBudget",
    "robust",
)


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    """Redirect the cache to a per-test temporary directory."""
    monkeypatch.setattr(mesh_cache, "cache_root", lambda: tmp_path)
    return tmp_path


def _make_msh(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _sources(tmp_path: Path) -> Path:
    """A directory kept OUT of the (monkeypatched) cache root."""
    return tmp_path / "sources"


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_stable():
    assert mesh_cache.mesh_fingerprint(BASE_INPUTS) == mesh_cache.mesh_fingerprint(
        dict(BASE_INPUTS)
    )


def test_fingerprint_ignores_dict_order():
    reordered = dict(reversed(list(BASE_INPUTS.items())))
    assert mesh_cache.mesh_fingerprint(reordered) == mesh_cache.mesh_fingerprint(
        BASE_INPUTS
    )


@pytest.mark.parametrize("key", list(BASE_INPUTS))
def test_fingerprint_sensitive_to_every_canonical_key(key):
    changed = dict(BASE_INPUTS)
    changed[key] = MUTATIONS[key]
    assert mesh_cache.mesh_fingerprint(changed) != mesh_cache.mesh_fingerprint(
        BASE_INPUTS
    )


def test_fingerprint_refinement_zone_order_is_irrelevant():
    a = {"centre": [0, 0, 0], "radius": 1.0, "cell_size": 0.1}
    b = {"centre": [1, 1, 1], "radius": 2.0, "cell_size": 0.2}
    forward = dict(BASE_INPUTS, refinement_zones=[a, b])
    reverse = dict(BASE_INPUTS, refinement_zones=[b, a])
    assert mesh_cache.mesh_fingerprint(forward) == mesh_cache.mesh_fingerprint(reverse)


@pytest.mark.parametrize("key", NON_GMSH_BL_KEYS)
def test_fingerprint_ignores_non_gmsh_boundary_layer_keys(key):
    """BL settings that never touch GMSH must NOT change the fingerprint."""
    assert mesh_cache.mesh_fingerprint({**BASE_INPUTS, key: 12345}) == (
        mesh_cache.mesh_fingerprint(BASE_INPUTS)
    )


def test_non_gmsh_boundary_layer_keys_are_not_canonical():
    for key in NON_GMSH_BL_KEYS:
        assert key not in mesh_cache.CANONICAL_KEYS


@pytest.mark.parametrize("key", GMSH_SHAPING_BL_KEYS)
def test_fingerprint_sensitive_to_gmsh_shaping_bl_keys(key):
    """The BL inputs baked into the .msh by GMSH MUST invalidate the cache.

    Regression: excluding them made the cache return a stale tet mesh (old
    near-wall layers / wall floor) after a BL change.
    """
    changed = {**BASE_INPUTS, key: 98765}
    assert mesh_cache.mesh_fingerprint(changed) != mesh_cache.mesh_fingerprint(
        BASE_INPUTS
    )


def test_gmsh_shaping_bl_keys_are_canonical():
    for key in GMSH_SHAPING_BL_KEYS:
        assert key in mesh_cache.CANONICAL_KEYS


# ---------------------------------------------------------------------------
# store / lookup
# ---------------------------------------------------------------------------

def test_store_lookup_roundtrip(cache_dir, tmp_path):
    fingerprint = mesh_cache.mesh_fingerprint(BASE_INPUTS)
    source = _make_msh(_sources(tmp_path) / "source.msh", 1024)

    assert mesh_cache.store(fingerprint, source, {"names": ["wall", "inlet"]}) is True

    found = mesh_cache.lookup(fingerprint)
    assert found is not None and found.is_file()
    assert found.read_bytes() == source.read_bytes()

    meta = mesh_cache.metadata(fingerprint)
    assert meta["fingerprint"] == fingerprint
    assert meta["names"] == ["wall", "inlet"]
    assert meta["msh_size"] == 1024
    assert meta["cache_version"] == mesh_cache.CACHE_VERSION
    assert "created" in meta


def test_lookup_missing_entry_returns_none(cache_dir):
    assert mesh_cache.lookup("deadbeef" * 8) is None


def test_lookup_rejects_missing_sidecar(cache_dir, tmp_path):
    fingerprint = mesh_cache.mesh_fingerprint(BASE_INPUTS)
    source = _make_msh(_sources(tmp_path) / "source.msh")
    assert mesh_cache.store(fingerprint, source, {}) is True
    (cache_dir / f"{fingerprint}.json").unlink()
    assert mesh_cache.lookup(fingerprint) is None


def test_lookup_rejects_empty_msh(cache_dir, tmp_path):
    fingerprint = mesh_cache.mesh_fingerprint(BASE_INPUTS)
    source = _make_msh(_sources(tmp_path) / "source.msh")
    assert mesh_cache.store(fingerprint, source, {}) is True
    (cache_dir / f"{fingerprint}.msh").write_bytes(b"")
    assert mesh_cache.lookup(fingerprint) is None


def test_store_rejects_empty_source(cache_dir, tmp_path):
    fingerprint = mesh_cache.mesh_fingerprint(BASE_INPUTS)
    empty = _make_msh(_sources(tmp_path) / "empty.msh", size=0)
    assert mesh_cache.store(fingerprint, empty, {}) is False
    assert mesh_cache.lookup(fingerprint) is None


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def _store_entries(cache_dir, tmp_path, count: int, size: int) -> list[str]:
    fingerprints = []
    for i in range(count):
        inputs = dict(BASE_INPUTS, geometry_mtime_ns=1000 + i)
        fp = mesh_cache.mesh_fingerprint(inputs)
        fingerprints.append(fp)
        source = _make_msh(_sources(tmp_path) / f"src_{i}.msh", size)
        assert mesh_cache.store(fp, source, {"index": i}) is True
        # Deterministic, increasing mtimes: i=0 oldest .. i=count-1 newest.
        # One full second apart: Windows' 100 ns timestamp resolution makes
        # sub-microsecond differences collapse into ties.
        stamp = (1_700_000_000 + i) * 1_000_000_000
        os.utime(cache_dir / f"{fp}.msh", ns=(stamp, stamp))
    return fingerprints


def test_prune_by_entries_keeps_newest(cache_dir, tmp_path):
    fingerprints = _store_entries(cache_dir, tmp_path, 4, size=100)
    mesh_cache.prune(max_entries=2, max_bytes=10**12)

    remaining = sorted(p.stem for p in cache_dir.glob("*.msh"))
    assert remaining == sorted(fingerprints[-2:])
    # Pruned entries lose their sidecar too.
    assert len(list(cache_dir.glob("*.json"))) == 2


def test_prune_by_bytes_keeps_newest(cache_dir, tmp_path):
    fingerprints = _store_entries(cache_dir, tmp_path, 3, size=1000)
    mesh_cache.prune(max_entries=100, max_bytes=1500)

    remaining = sorted(p.stem for p in cache_dir.glob("*.msh"))
    assert remaining == [fingerprints[-1]]


def test_prune_never_raises_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mesh_cache, "cache_root", lambda: tmp_path / "nope")
    mesh_cache.prune()  # must not raise


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

def test_clear_removes_all_entries(cache_dir, tmp_path):
    fingerprints = _store_entries(cache_dir, tmp_path, 3, size=100)
    removed = mesh_cache.clear()
    assert removed == 3
    for fp in fingerprints:
        assert mesh_cache.lookup(fp) is None
    assert list(cache_dir.glob("*.msh")) == []
    assert list(cache_dir.glob("*.json")) == []


# ---------------------------------------------------------------------------
# GUI wiring
# ---------------------------------------------------------------------------

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from polyfoammesh.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_panel_reuse_checkbox_defaults_on(app):
    panel = ParamsPanel()
    assert panel._reuse_cache_check.isChecked() is True
    assert panel.get_reuse_cached_mesh() is True


def test_panel_reuse_getter_tracks_checkbox(app):
    panel = ParamsPanel()
    panel._reuse_cache_check.setChecked(False)
    assert panel.get_reuse_cached_mesh() is False
    panel._reuse_cache_check.setChecked(True)
    assert panel.get_reuse_cached_mesh() is True


def test_panel_reuse_setting_persists(app, tmp_path):
    panel = ParamsPanel()
    panel._reuse_cache_check.setChecked(False)

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    panel.save_params(store)
    store.sync()

    fresh = ParamsPanel()
    fresh.restore_params(store)
    assert fresh.get_reuse_cached_mesh() is False
