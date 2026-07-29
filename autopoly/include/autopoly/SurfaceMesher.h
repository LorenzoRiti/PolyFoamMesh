#pragma once

#include "Types.h"
#include <vector>
#include <string>
#include <functional>

namespace autopoly {

// -----------------------------------------------------------------------------
// Surface meshing
// -----------------------------------------------------------------------------

struct SurfaceMesh {
    std::vector<Vec3> vertices;
    std::vector<std::array<int, 3>> triangles;  // CCW from outside
    std::vector<int> vertex_patch_id;
    std::vector<int> triangle_patch_id;
    std::vector<std::pair<int, int>> feature_edges;  // vertex pairs
    BoundingBox bbox;
};

// Generate high-quality surface mesh from input geometry
SurfaceMesh generateSurfaceMesh(
    const std::vector<Vec3>& input_vertices,
    const std::vector<std::array<int, 3>>& input_triangles,
    const std::vector<int>& input_patch_ids,
    const SurfaceMeshOptions& options,
    const FeatureOptions& feature_options,
    const SizeField& size_field,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Remesh surface with curvature adaptation
SurfaceMesh remeshSurface(
    const SurfaceMesh& input,
    const SurfaceMeshOptions& options,
    const SizeField& size_field,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Improve surface mesh quality (smoothing, edge flips)
SurfaceMesh improveSurfaceQuality(
    SurfaceMesh&& mesh,
    const SurfaceMeshOptions& options,
    int iterations = 10
);

// Project vertices to original CAD surface (if available)
void projectToCAD(SurfaceMesh& mesh, const std::function<Vec3(const Vec3&)>& closest_point);

// Snap feature vertices to feature edges
void snapFeatureVertices(SurfaceMesh& mesh, double tolerance);

// Check surface mesh validity
bool validateSurfaceMesh(const SurfaceMesh& mesh, std::vector<std::string>& errors);

// Compute surface mesh quality metrics
struct SurfaceQuality {
    double min_angle = 180.0;
    double max_angle = 0.0;
    double min_area = 1e30;
    double max_area = 0.0;
    double avg_aspect_ratio = 0.0;
    int inverted = 0;
    int non_manifold_edges = 0;
};

SurfaceQuality computeSurfaceQuality(const SurfaceMesh& mesh);

} // namespace autopoly