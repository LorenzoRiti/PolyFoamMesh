"""core/case_setup.py's turbulenceProperties writer must handle "laminar"
correctly, not as an invalid RAS submodel.

"laminar" is not a registered RASModel (verified against OpenFOAM's own
TurbulenceModels library — only kEpsilon/kOmegaSST/SpalartAllmaras/... exist
there). `simulationType RAS; RAS { RASModel laminar; }` fails at solver
startup with "Unknown RASModel type". The correct form has no RAS
sub-dictionary at all: `simulationType laminar;`.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.boundary_reader import PatchInfo
from polyfoammesh.core.case_setup import setup_case

_PATCHES = [PatchInfo(name="wall", patch_type="wall", n_faces=1, start_face=0)]


def _turbulence_text(turbulence_model: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp)
        setup_case(case, _PATCHES, turbulence_model=turbulence_model)
        return (case / "constant" / "turbulenceProperties").read_text()


def test_laminar_has_no_ras_block():
    content = _turbulence_text("laminar")
    assert "simulationType  laminar;" in content
    assert "RAS" not in content


def test_kOmegaSST_still_writes_a_valid_ras_block():
    content = _turbulence_text("kOmegaSST")
    assert "simulationType  RAS;" in content
    assert "RASModel        kOmegaSST;" in content


def test_kEpsilon_still_writes_a_valid_ras_block():
    content = _turbulence_text("kEpsilon")
    assert "RASModel        kEpsilon;" in content


def test_laminar_is_case_insensitive():
    content = _turbulence_text("Laminar")
    assert "simulationType  laminar;" in content
    assert "RAS" not in content
