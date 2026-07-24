"""Quick test: run meshing pipeline + VTK quality on pipe geometry."""
import sys, shutil, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from benchmarks.run_benchmarks import _make_case_dir, _to_wsl_path, _wsl_of_run
from benchmarks.run_benchmarks import _run_cartesian_mesh, _run_check_mesh, _vtk_quality_check
from cfmesh_autogui.core.geometry import load_stl, suggest_cell_sizes
from cfmesh_autogui.core.stl_writer import export_surface_file
from cfmesh_autogui.core.meshdict_gen import write_meshdict

stl = Path("benchmarks/geometries/pipe.stl")
meshes = load_stl(stl)

tmp_root = Path(tempfile.mkdtemp(dir="C:/cfmesh_bench"))
case_dir = _make_case_dir(tmp_root, "pipe_vtk_test")

export_surface_file(meshes, case_dir)
s_max, s_min = suggest_cell_sizes(meshes, detail="medium")
write_meshdict(case_dir, s_max, s_min)

print("Running cartesianMesh...")
rc, out, t = _run_cartesian_mesh(case_dir)
print(f"  rc={rc} wall={t:.1f}s")

print("Running checkMesh...")
rc2, out2, t2 = _run_check_mesh(case_dir)
print(f"  rc={rc2} wall={t2:.1f}s")

print("Running foamToVTK + VTK quality...")
vtk_result = _vtk_quality_check(case_dir, print)
if vtk_result:
    print(f"VTK OK: cells={vtk_result['vtk_cells']}")
    print(f"  skew_max={vtk_result['vtk_skew_max']}")
    print(f"  aspect_max={vtk_result['vtk_aspect_max']}")
    print(f"  jacobian_min={vtk_result['vtk_jacobian_min']}")
    print(f"  angles=[{vtk_result['vtk_min_angle']}, {vtk_result['vtk_max_angle']}]")
else:
    print("VTK failed - check if foamToVTK ran")

shutil.rmtree(tmp_root, ignore_errors=True)
