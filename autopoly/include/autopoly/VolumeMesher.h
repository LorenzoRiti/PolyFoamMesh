#pragma once

#include "Types.h"
#include <vector>
#include <functional>

namespace autopoly {

// -----------------------------------------------------------------------------
// Volume meshing - CVT-based polyhedral meshing
// -----------------------------------------------------------------------------

struct VolumeMesh {
    std::vector<Vec3> vertices;           // all vertices (internal + boundary)
    std::vector<std::vector<int>> cells;  // cell -> vertex indices (polyhedron)
    std::vector<std::vector<int>> cell_faces;  // cell -> face indices
    std::vector<std::vector<int>> faces;       // face -> vertex indices (CCW from cell)
    std::vector<int> face_owner;        // face -> owning cell
    std::vector<int> face_neighbour;    // face -> neighbour cell (-1 = boundary)
    std::vector<int> face_patch;        // face -> boundary patch id
    std::vector<int> vertex_patch;      // vertex -> boundary patch id (-1 = internal)
    std::vector<Vec3> cell_centroids;
    std::vector<double> cell_volumes;
    BoundingBox bbox;
};

// Seed generation for CVT
struct SeedGenerator {
    enum class Method { JitteredGrid, PoissonDisk, CVTInitial };

    Method method = Method::JitteredGrid;
    int target_seed_count = 10000;
    double min_distance = 0.0;  // 0 = auto from size field
    int max_iterations = 30;
    int random_seed = 12345;
};

// Generate initial seed points inside domain
std::vector<Vec3> generateSeeds(
    const VolumeMesh& domain_boundary,
    const SizeField& size_field,
    const SeedGenerator& gen,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Check if point is inside domain (using winding number or ray casting)
bool pointInDomain(const Vec3& p, const VolumeMesh& boundary_mesh);

// Build AABB tree for fast point location
class AABBTree {
public:
    AABBTree() = default;
    AABBTree(const std::vector<Vec3>& vertices, const std::vector<std::array<int, 3>>& triangles);
    
    // Find closest point on surface
    Vec3 closestPoint(const Vec3& query) const;
    
    // Signed distance (negative inside)
    double signedDistance(const Vec3& query) const;
    
    // Ray intersection count (for inside test)
    int rayIntersections(const Vec3& origin, const Vec3& dir) const;
    
    // Find containing cell for a point
    int findContainingCell(const Vec3& p, const VolumeMesh& mesh) const;

private:
    struct Node;
    std::unique_ptr<Node> root_;
    std::vector<Vec3> vertices_;
    std::vector<std::array<int, 3>> triangles_;
};

// Lloyd relaxation for CVT
struct LloydRelaxation {
    int max_iterations = 30;
    double convergence_tolerance = 1e-4;
    double boundary_weight = 0.9;  // how strongly to pull boundary seeds to surface
    bool constrain_to_domain = true;
};

// Perform Lloyd iterations to compute CVT
void lloydRelaxation(
    std::vector<Vec3>& seeds,
    const AABBTree& domain_tree,
    const VolumeMesh& boundary_mesh,
    const SizeField& size_field,
    const LloydRelaxation& params,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Build Voronoi cells from seeds (half-space intersection)
struct VoronoiCell {
    std::vector<Vec3> vertices;
    std::vector<std::vector<int>> faces;  // face -> vertex indices
    std::vector<int> neighbor_seeds;      // adjacent seed indices
    int seed_index = -1;
    bool is_boundary = false;
};

std::vector<VoronoiCell> buildVoronoiCells(
    const std::vector<Vec3>& seeds,
    const AABBTree& domain_tree,
    const VolumeMesh& boundary_mesh,
    const SizeField& size_field,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Clip Voronoi cells to domain boundary
void clipCellsToBoundary(
    std::vector<VoronoiCell>& cells,
    const AABBTree& domain_tree,
    const VolumeMesh& boundary_mesh,
    double conformity = 0.9
);

// Snap boundary vertices to surface
void snapBoundaryVertices(
    std::vector<VoronoiCell>& cells,
    const AABBTree& domain_tree,
    const VolumeMesh& boundary_mesh,
    double tolerance = 1e-6
);

// Merge coplanar faces
void mergeCoplanarFaces(
    std::vector<VoronoiCell>& cells,
    double angle_tolerance_deg = 1.0
);

// Remove tiny faces/edges
void removeSmallFeatures(
    std::vector<VoronoiCell>& cells,
    double min_face_area = 1e-12,
    double min_edge_length = 1e-9
);

// Convert Voronoi cells to VolumeMesh
VolumeMesh voronoiToVolumeMesh(
    const std::vector<VoronoiCell>& cells,
    const std::vector<BoundaryPatch>& patches
);

// Main volume meshing entry point
VolumeMesh generateVolumeMesh(
    const SurfaceMesh& surface,
    const SizeField& size_field,
    const VolumeMeshOptions& options,
    const BoundaryLayerOptions& bl_options,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

} // namespace autopoly