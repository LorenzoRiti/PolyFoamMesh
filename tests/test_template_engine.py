"""Tests for template engine."""
from __future__ import annotations
import sys, json, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("template_engine")
TemplateMetadata = _mod.TemplateMetadata
TemplatePreset = _mod.TemplatePreset
TemplateEngine = _mod.TemplateEngine


def test_template_metadata_defaults():
    m = TemplateMetadata()
    assert m.name == ""
    assert m.icon == "default"
    assert m.tags == []


def test_template_metadata_custom():
    m = TemplateMetadata(name="Test", category="internal_flow", solver="pimpleFoam")
    assert m.name == "Test"
    assert m.category == "internal_flow"
    assert m.solver == "pimpleFoam"


def test_template_preset_defaults():
    p = TemplatePreset()
    assert p.max_cell_ratio == 0.05
    assert p.min_cell_ratio == 0.005
    assert p.detail == "medium"
    assert p.bl_enabled


def test_template_preset_to_dict():
    p = TemplatePreset(
        metadata=TemplateMetadata(name="Test Template", category="test"),
        max_cell_ratio=0.02,
        bl_n_layers=8,
    )
    d = p.to_dict()
    assert d["metadata"]["name"] == "Test Template"
    assert d["max_cell_ratio"] == 0.02
    assert d["bl_n_layers"] == 8


def test_template_preset_roundtrip():
    p1 = TemplatePreset(
        metadata=TemplateMetadata(name="Roundtrip", category="test", tags=["a", "b"]),
        max_cell_ratio=0.01,
        end_time=500,
    )
    d = p1.to_dict()
    p2 = TemplatePreset.from_dict(d)
    assert p2.metadata.name == "Roundtrip"
    assert p2.max_cell_ratio == 0.01
    assert p2.end_time == 500
    assert p2.metadata.tags == ["a", "b"]


def test_engine_init():
    te = TemplateEngine()
    assert te._user_dir.exists()


def test_engine_list_categories():
    te = TemplateEngine()
    cats = te.list_categories()
    assert "internal_flow" in cats
    assert "external_aero" in cats
    assert "cht" in cats


def test_engine_list_templates():
    te = TemplateEngine()
    templates = te.list_templates()
    assert len(templates) >= 7, f"Expected >=7 built-in templates, got {len(templates)}"
    names = [t.metadata.name for t in templates]
    assert "Internal Flow" in names


def test_engine_list_templates_filtered():
    te = TemplateEngine()
    filtered = te.list_templates(category="cht")
    assert all(t.metadata.category == "cht" for t in filtered)
    assert len(filtered) >= 1


def test_engine_get_template():
    te = TemplateEngine()
    t = te.get_template("External Aerodynamics")
    assert t is not None
    assert t.metadata.solver == "simpleFoam"


def test_engine_get_template_not_found():
    te = TemplateEngine()
    t = te.get_template("Nonexistent")
    assert t is None


def test_save_and_load_user_template():
    te = TemplateEngine()
    p = TemplatePreset(
        metadata=TemplateMetadata(name="My Custom", category="user"),
        max_cell_ratio=0.03,
    )
    path = te.save_user_template(p)
    assert path.exists()

    loaded = te.load_user_template(path)
    assert loaded.metadata.name == "My Custom"
    assert loaded.max_cell_ratio == 0.03
    path.unlink(missing_ok=True)


def test_delete_user_template():
    te = TemplateEngine()
    p = TemplatePreset(
        metadata=TemplateMetadata(name="ToDelete", category="user"),
    )
    te.save_user_template(p)
    assert te.delete_user_template("ToDelete")
    assert not te.delete_user_template("ToDelete")  # second call should fail


def test_apply_template_creates_files():
    import shutil
    tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TEMP", "/tmp")))
    te = TemplateEngine()
    t = te.get_template("Internal Flow")
    assert t is not None
    files = te.apply_template(t, tmp)
    assert len(files) > 5
    assert (tmp / "system/controlDict").exists()
    assert (tmp / "0/U").exists()
    assert (tmp / "constant/transportProperties").exists()
    shutil.rmtree(tmp, ignore_errors=True)


def test_user_template_appears_in_list():
    te = TemplateEngine()
    p = TemplatePreset(
        metadata=TemplateMetadata(name="UserTest", category="user"),
    )
    te.save_user_template(p)
    all_t = te.list_templates()
    names = [t.metadata.name for t in all_t]
    assert "UserTest" in names
    te.delete_user_template("UserTest")


def test_builtin_templates_have_categories():
    te = TemplateEngine()
    for t in te.list_templates():
        assert t.metadata.category, f"Template '{t.metadata.name}' missing category"


if __name__ == "__main__":
    import os, shutil
    test_template_metadata_defaults()
    test_template_metadata_custom()
    test_template_preset_defaults()
    test_template_preset_to_dict()
    test_template_preset_roundtrip()
    test_engine_init()
    test_engine_list_categories()
    test_engine_list_templates()
    test_engine_list_templates_filtered()
    test_engine_get_template()
    test_engine_get_template_not_found()
    test_save_and_load_user_template()
    test_delete_user_template()
    test_apply_template_creates_files()
    test_user_template_appears_in_list()
    test_builtin_templates_have_categories()
    # Cleanup TEMP dir
    tmp = Path(os.environ.get("TEMP", "/tmp"))
    for d in tmp.glob("tmp*"):
        if d.is_dir(): shutil.rmtree(d, ignore_errors=True)
    print("ALL PASS")
