"""Tests for batch meshing engine."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module, space_free_tmp_root

_mod = load_commercial_module("batch_mesh")
BatchEntry = _mod.BatchEntry
BatchConfig = _mod.BatchConfig
BatchReport = _mod.BatchReport
BatchMesher = _mod.BatchMesher

# A path that does not exist on any host and, on POSIX, cannot be created
# by an unprivileged test process either (no 'Z:' drive semantics there).
NONEXISTENT = (
    "Z:\\nonexistent" if os.name == "nt"
    else "/nonexistent_cfmesh_batch_dir"
)


def test_batch_entry_defaults():
    e = BatchEntry(geometry_path="/tmp/test.step")
    assert e.status == "pending"
    assert e.cell_count == 0
    assert e.error == ""


def test_batch_entry_with_label():
    e = BatchEntry(geometry_path="/tmp/test.step", label="test_model")
    assert e.label == "test_model"
    assert e.geometry_path == "/tmp/test.step"


def test_batch_config_defaults():
    c = BatchConfig()
    assert c.max_cell == 0.05
    assert c.min_cell == 0.01
    assert c.detail == "medium"
    assert not c.bl_enabled
    assert not c.parallel


def test_batch_config_custom():
    c = BatchConfig(max_cell=0.02, min_cell=0.005, bl_enabled=True, n_cores=4)
    assert c.max_cell == 0.02
    assert c.min_cell == 0.005
    assert c.bl_enabled
    assert c.n_cores == 4


def test_batch_report_defaults():
    r = BatchReport()
    assert r.total == 0
    assert r.succeeded == 0
    assert r.failed == 0
    assert r.entries == []
    assert r.wall_time_s == 0.0


def test_batch_report_with_data():
    r = BatchReport(total=10, succeeded=8, failed=2, wall_time_s=120.5)
    assert r.total == 10
    assert r.succeeded == 8
    assert r.failed == 2
    assert r.wall_time_s == 120.5


def test_batch_mesher_init():
    bm = BatchMesher()
    assert bm._queue == []
    assert bm._report.total == 0


def test_batch_mesher_add_file_invalid():
    bm = BatchMesher()
    try:
        bm.add_file(NONEXISTENT + ".step")
        assert False, "Should have raised"
    except ValueError:
        pass


def test_batch_mesher_add_directory_nonexistent():
    bm = BatchMesher()
    try:
        bm.add_directory(NONEXISTENT)
        assert False, "Should have raised"
    except FileNotFoundError:
        pass


def test_batch_mesher_add_directory_empty():
    import tempfile
    with tempfile.TemporaryDirectory(dir=str(space_free_tmp_root())) as tmp:
        bm = BatchMesher()
        count = bm.add_directory(tmp, "*.step")
        assert count == 0, "Empty dir should yield 0 files"


def test_batch_mesher_empty_queue():
    bm = BatchMesher()
    report = bm.run_all()
    assert report.total == 0
    assert report.succeeded == 0


def test_batch_mesher_configure():
    bm = BatchMesher()
    config = BatchConfig(max_cell=0.02, min_cell=0.005)
    bm.configure(config)
    assert bm._config.max_cell == 0.02
    assert bm._config.min_cell == 0.005


def test_batch_report_export():
    import os as _os
    r = BatchReport(total=5, succeeded=3, failed=2, wall_time_s=60.0)
    r.entries = [
        BatchEntry(geometry_path="a.step", status="done", cell_count=1000),
        BatchEntry(geometry_path="b.step", status="failed", error="err"),
    ]
    bm = BatchMesher()
    bm._report = r
    out = Path(_os.environ.get("TEMP", "/tmp")) / "test_batch_report.json"
    bm.export_report(out)
    data = Path(out).read_text()
    assert '"succeeded": 3' in data
    assert '"failed": 2' in data
    out.unlink(missing_ok=True)


if __name__ == "__main__":
    test_batch_entry_defaults()
    test_batch_entry_with_label()
    test_batch_config_defaults()
    test_batch_config_custom()
    test_batch_report_defaults()
    test_batch_report_with_data()
    test_batch_mesher_init()
    test_batch_mesher_add_file_invalid()
    test_batch_mesher_add_directory_nonexistent()
    test_batch_mesher_add_directory_empty()
    test_batch_mesher_empty_queue()
    test_batch_mesher_configure()
    test_batch_report_export()
    print("ALL PASS")
