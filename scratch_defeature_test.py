import sys, math, time
sys.path.insert(0, "src")
from pathlib import Path
from cfmesh_autogui.core.gmsh_wrapper import (
    _ensure_gmsh, _hardware_budget, _scan_feature_sizes, _configure_adaptive_sizing,
)

gmsh = _ensure_gmsh()
gmsh.clear()
gmsh.open(r"C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp")

surfaces = gmsh.model.getEntities(2)
areas = []
for dim, tag in surfaces:
    try:
        a = gmsh.model.occ.getMass(dim, tag)
    except Exception:
        a = 0
    areas.append((tag, a))
areas.sort(key=lambda x: x[1])

# Defeature more aggressively this time: smallest 30 surfaces
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
small_tags = [tag for tag, a in areas[:N]]
vols = gmsh.model.getEntities(3)
t0 = time.time()
gmsh.model.occ.defeature([v[1] for v in vols], small_tags, removeVolume=True)
gmsh.model.occ.synchronize()
print(f"defeature({N}) took {time.time()-t0:.2f}s, surfaces: {len(surfaces)} -> {len(gmsh.model.getEntities(2))}")

t0 = time.time()
gmsh.model.occ.healShapes(fixSmallFaces=True, sewFaces=True, makeSolids=True)
gmsh.model.occ.synchronize()
print(f"healShapes took {time.time()-t0:.2f}s, surfaces now: {len(gmsh.model.getEntities(2))}, volumes: {gmsh.model.getEntities(3)}")

bbox = gmsh.model.getBoundingBox(-1, -1)
dx, dy, dz = abs(bbox[3]-bbox[0]), abs(bbox[4]-bbox[1]), abs(bbox[5]-bbox[2])
max_extent = max(dx, dy, dz)
cross_scale = sorted([dx, dy, dz])[1]
vol = dx*dy*dz
hw = _hardware_budget()
feat = _scan_feature_sizes(gmsh, max_extent)
info = _configure_adaptive_sizing(gmsh, "medium", max_extent, feat, hw, cross_scale, domain_volume=vol)
print("sizing:", info)

gmsh.option.setNumber("Mesh.Algorithm3D", 10)
gmsh.option.setNumber("Mesh.Algorithm", 6)
gmsh.option.setNumber("Mesh.Optimize", 1)
gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
t0 = time.time()
gmsh.model.mesh.generate(3)
print(f"generate(3) took {time.time()-t0:.2f}s")

for dim, tag in gmsh.model.getEntities(3):
    gmsh.model.addPhysicalGroup(3, [tag], tag)
gmsh.model.mesh.renumberNodes()
gmsh.model.mesh.renumberElements()
gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
out_path = Path(rf"C:\cfmesh_real_flow_test\defeature_{N}_test.msh")
gmsh.write(str(out_path))
print("wrote", out_path)
