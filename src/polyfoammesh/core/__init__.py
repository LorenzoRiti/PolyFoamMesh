from polyfoammesh.core.geometry import (
    load_step, load_geometry, classify_faces, tessellate_patches,
    create_test_cylinder, compute_bbox_dim, compute_bbox_full,
    validate_cell_sizes, compute_volume, estimate_cell_count,
    estimate_cell_count_geometric,
    suggest_cell_sizes, compute_patch_cell_sizes,
    scale_meshes, unit_to_scale, analyze_local_thickness,
)
from polyfoammesh.core.stl_writer import export_surface_file, export_multisolid_stl
from polyfoammesh.core.meshdict_gen import write_meshdict
from polyfoammesh.core.gmsh_wrapper import compute_sizing, generate_surface_stl, generate_volume_mesh
from polyfoammesh.core.boundary_reader import parse_boundary, PatchInfo
from polyfoammesh.core.validation import (
    validate_cell_size, validate_bl_params, validate_detail_level,
    validate_geometry_path, validate_case_dir, validate_settings,
    sanitise_patch_name, ValidationResult,
)
from polyfoammesh.core.session import save_snapshot, load_snapshot, clear_snapshot, SessionSnapshot

__all__ = [
    "load_step", "load_geometry", "classify_faces", "tessellate_patches",
    "create_test_cylinder", "compute_bbox_dim", "compute_bbox_full",
    "validate_cell_sizes", "compute_volume", "estimate_cell_count",
    "estimate_cell_count_geometric",
    "suggest_cell_sizes", "compute_patch_cell_sizes",
    "scale_meshes", "unit_to_scale", "analyze_local_thickness",
    "export_surface_file", "export_multisolid_stl",
    "write_meshdict",
    "compute_sizing", "generate_surface_stl", "generate_volume_mesh",
    "parse_boundary", "PatchInfo",
    "validate_cell_size", "validate_bl_params", "validate_detail_level",
    "validate_geometry_path", "validate_case_dir", "validate_settings",
    "sanitise_patch_name", "ValidationResult",
    "save_snapshot", "load_snapshot", "clear_snapshot", "SessionSnapshot",
]
