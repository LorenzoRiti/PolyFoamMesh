"""Tests for mesh export factory."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("exporter")
ExportResult = _mod.ExportResult
MeshExporter = _mod.MeshExporter
EXPORT_FORMAT_REGISTRY = _mod.EXPORT_FORMAT_REGISTRY


def test_export_formats_available():
    assert "openfoam" in EXPORT_FORMAT_REGISTRY
    assert "cgns" in EXPORT_FORMAT_REGISTRY
    assert "vtu" in EXPORT_FORMAT_REGISTRY
    assert "stl" in EXPORT_FORMAT_REGISTRY
    assert len(EXPORT_FORMAT_REGISTRY) >= 6


def test_export_result_defaults():
    r = ExportResult()
    assert not r.success
    assert r.format == ""
    assert r.cell_count == 0
    assert r.file_size_bytes == 0
    assert r.error == ""


def test_export_result_with_data():
    r = ExportResult(success=True, format="vtu", cell_count=5000, file_size_bytes=1024000)
    assert r.success
    assert r.format == "vtu"
    assert r.cell_count == 5000


def test_exporter_init():
    ex = MeshExporter()
    assert ex is not None


def test_exporter_unsupported_format():
    ex = MeshExporter()
    result = ex.export(Path("."), fmt="unknown")
    assert not result.success
    assert "Unsupported" in result.error


def test_exporter_openfoam_nonexistent():
    ex = MeshExporter()
    result = ex.export(Path("/nonexistent"), fmt="openfoam")
    assert not result.success


def test_exporter_stl_nonexistent():
    ex = MeshExporter()
    result = ex.export(Path("/nonexistent"), fmt="stl")
    assert not result.success


def test_exporter_vtu_nonexistent():
    ex = MeshExporter()
    result = ex.export(Path("/nonexistent"), fmt="vtu")
    assert not result.success


def test_export_format_descriptions():
    for key, info in EXPORT_FORMAT_REGISTRY.items():
        assert "desc" in info
        assert "ext" in info
        assert "requires_wsl" in info
        assert isinstance(info["desc"], str)
        assert isinstance(info["requires_wsl"], bool)


def test_every_registered_format_has_handler():
    """The registry must never advertise a format with no handler.

    A key in EXPORT_FORMAT_REGISTRY without a matching ``_export_<fmt>``
    method would fail at runtime with "Export handler not implemented" —
    the registry is the single source of truth, so this invariant is
    enforced here.
    """
    for fmt in EXPORT_FORMAT_REGISTRY:
        assert hasattr(MeshExporter, f"_export_{fmt}"), (
            f"Registry advertises '{fmt}' but MeshExporter has no handler"
        )


def test_count_cells_no_polymesh():
    count = MeshExporter._count_cells(Path("/nonexistent"))
    assert count == 0


def test_export_result_str():
    r = ExportResult(success=True, format="vtu", cell_count=1000)
    assert "vtu" in str(r.__dict__)


if __name__ == "__main__":
    test_export_formats_available()
    test_export_result_defaults()
    test_export_result_with_data()
    test_exporter_init()
    test_exporter_unsupported_format()
    test_exporter_openfoam_nonexistent()
    test_exporter_stl_nonexistent()
    test_exporter_vtu_nonexistent()
    test_export_format_descriptions()
    test_count_cells_no_polymesh()
    test_export_result_str()
    print("ALL PASS")
