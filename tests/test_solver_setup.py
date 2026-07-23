"""Tests for solver setup module."""
from __future__ import annotations
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import load_commercial_module

_mod = load_commercial_module("solver_setup")
SolverType = _mod.SolverType
TurbulenceModel = _mod.TurbulenceModel
SchemePreset = _mod.SchemePreset
MaterialProperties = _mod.MaterialProperties
SolverConfig = _mod.SolverConfig
SolverSetup = _mod.SolverSetup


def test_solver_type_enum():
    assert SolverType.SIMPLE_FOAM.value == "simpleFoam"
    assert SolverType.PIMPLE_FOAM.value == "pimpleFoam"


def test_turbulence_model_values():
    assert TurbulenceModel.K_OMEGA_SST.value == "kOmegaSST"
    assert TurbulenceModel.K_EPSILON.value == "kEpsilon"


def test_scheme_preset_values():
    assert SchemePreset.ROBUSTO.value == "robusto"
    assert SchemePreset.ACCURATO.value == "accurato"


def test_material_properties_defaults():
    m = MaterialProperties()
    assert m.name == "air"
    assert m.density == 1.225
    assert m.viscosity == 1.5e-5


def test_material_properties_water():
    m = MaterialProperties(name="water", density=1000, viscosity=1e-6)
    assert m.name == "water"
    assert m.density == 1000


def test_solver_config_defaults():
    c = SolverConfig()
    assert c.solver == SolverType.SIMPLE_FOAM
    assert c.turbulence == TurbulenceModel.K_OMEGA_SST
    assert c.schemes == SchemePreset.BILANCIATO


def test_solver_config_custom():
    c = SolverConfig(
        solver=SolverType.PIMPLE_FOAM,
        turbulence=TurbulenceModel.LES_WALE,
        schemes=SchemePreset.ACCURATO,
        end_time=500,
    )
    assert c.solver == SolverType.PIMPLE_FOAM
    assert c.turbulence == TurbulenceModel.LES_WALE
    assert c.end_time == 500


def test_solver_setup_init():
    ss = SolverSetup()
    assert ss.files_written == []


def test_solver_setup_configure():
    ss = SolverSetup()
    c = SolverConfig(solver=SolverType.PISO_FOAM)
    ss.configure(c)
    assert ss._config.solver == SolverType.PISO_FOAM


def test_write_all_creates_files():
    tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TEMP", "/tmp")))
    ss = SolverSetup()
    files = ss.write_all(tmp)
    assert len(files) > 10, f"Expected >10 files, got {len(files)}"
    assert (tmp / "system/controlDict").exists()
    assert (tmp / "system/fvSchemes").exists()
    assert (tmp / "system/fvSolution").exists()
    assert (tmp / "constant/transportProperties").exists()
    assert (tmp / "constant/turbulenceProperties").exists()
    assert (tmp / "0/U").exists()
    assert (tmp / "0/p").exists()


def test_control_dict_content():
    ss = SolverSetup()
    ss.configure(SolverConfig(solver=SolverType.PIMPLE_FOAM, end_time=500))
    content = ss._control_dict()
    assert "pimpleFoam" in content
    assert "endTime 500" in content


def test_turbulence_properties_laminar():
    ss = SolverSetup()
    ss.configure(SolverConfig(turbulence=TurbulenceModel.LAMINAR))
    content = ss._turbulence_properties()
    # Laminar still uses RAS simulationType with laminar RASModel
    assert "simulationType" in content


def test_turbulence_properties_les():
    ss = SolverSetup()
    ss.configure(SolverConfig(turbulence=TurbulenceModel.LES_WALE))
    content = ss._turbulence_properties()
    assert "WALE" in content


def test_scheme_preset_robusto():
    ss = SolverSetup()
    ss.configure(SolverConfig(schemes=SchemePreset.ROBUSTO))
    content = ss._fv_schemes()
    assert "upwind" in content


def test_scheme_preset_accurato():
    ss = SolverSetup()
    ss.configure(SolverConfig(schemes=SchemePreset.ACCURATO))
    content = ss._fv_schemes()
    assert "linearUpwind" in content


def test_fv_solution_pimple():
    ss = SolverSetup()
    ss.configure(SolverConfig(solver=SolverType.PIMPLE_FOAM))
    content = ss._fv_solution()
    assert "PIMPLE" in content
    assert "nOuterCorrectors 3" in content


def test_transport_properties():
    ss = SolverSetup()
    ss.configure(SolverConfig(material=MaterialProperties(viscosity=1e-6)))
    content = ss._transport_properties()
    assert "1.000000e-06" in content


if __name__ == "__main__":
    import shutil
    test_solver_type_enum()
    test_turbulence_model_values()
    test_scheme_preset_values()
    test_material_properties_defaults()
    test_material_properties_water()
    test_solver_config_defaults()
    test_solver_config_custom()
    test_solver_setup_init()
    test_solver_setup_configure()
    test_write_all_creates_files()
    test_control_dict_content()
    test_turbulence_properties_laminar()
    test_turbulence_properties_les()
    test_scheme_preset_robusto()
    test_scheme_preset_accurato()
    test_fv_solution_pimple()
    test_transport_properties()
    # Cleanup
    tmp = Path(os.environ.get("TEMP", "/tmp"))
    for d in tmp.glob("tmp*"):
        if d.is_dir(): shutil.rmtree(d, ignore_errors=True)
    print("ALL PASS")
