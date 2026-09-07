#pragma once

#include "Types.h"
#include <vector>
#include <string>
#include <filesystem>
#include <optional>

namespace autopoly {

// -----------------------------------------------------------------------------
// Geometry loading and repair
// -----------------------------------------------------------------------------

struct GeometryHealthReport {
    std::size_t original_vertices = 0;
    std::size_t original_faces = 0;
    std::size_t final_vertices = 0;
    std::size_t final_faces = 0;
    std::size_t duplicate_vertices_removed = 0;
    std::size_t degenerate_faces_removed = 0;
    std::size_t holes_filled = 0;
    std::size_t non_manifold_edges = 0;
    std::size_t self_intersections = 0;
    bool is_watertight = false;
    bool is_manifold = false;
    bool normals_consistent = false;
    BoundingBox bbox;
    std::vector<std::string> warnings;
    std::vector<std::string> errors;
};

struct LoadedGeometry {
    std::vector<Vec3> vertices;
    std::vector<std::array<std::size_t, 3>> faces;  // triangle indices
    std::vector<std::size_t> face_patch_ids;       // patch index per face
    std::vector<std::string> patch_names;
    GeometryHealthReport health;
};

// Load geometry from file (STL, OBJ, PLY, VTK)
LoadedGeometry loadGeometry(const std::filesystem::path& path, const GeometryOptions& options);

// Repair and clean geometry
LoadedGeometry repairGeometry(LoadedGeometry&& geom, const GeometryOptions& options);

// Merge duplicate vertices within tolerance
void mergeDuplicateVertices(LoadedGeometry& geom, double tolerance);

// Remove degenerate triangles (zero area, zero normal)
void removeDegenerateFaces(LoadedGeometry& geom);

// Orient faces consistently (outward normals for closed surfaces)
void orientNormalsConsistently(LoadedGeometry& geom);

// Fill small holes in the surface
void fillSmallHoles(LoadedGeometry& geom, double max_hole_area);

// Detect and report self-intersections
std::size_t detectSelfIntersections(const LoadedGeometry& geom);

// Check if mesh is watertight (closed, manifold)
bool isWatertight(const LoadedGeometry& geom);

// Check if mesh is manifold
bool isManifold(const LoadedGeometry& geom);

// Compute bounding box
BoundingBox computeBBox(const LoadedGeometry& geom);

// Auto-detect units from bounding box size
std::string detectUnits(const BoundingBox& bbox);

// Scale geometry to meters
void scaleToMeters(LoadedGeometry& geom, const std::string& from_unit);

} // namespace autopoly