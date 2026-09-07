#pragma once

#include <autopoly/Types.h>
#include <vector>
#include <array>
#include <memory>
#include <optional>

namespace autopoly {

// CGAL kernel adapter - isolates CGAL types behind a stable API
// This allows the rest of autopoly to compile without direct CGAL headers

class CgalKernel {
public:
    CgalKernel();
    ~CgalKernel();

    CgalKernel(const CgalKernel&) = delete;
    CgalKernel& operator=(const CgalKernel&) = delete;
    CgalKernel(CgalKernel&&) noexcept;
    CgalKernel& operator=(CgalKernel&&) noexcept;

    // Build AABB tree from surface mesh for fast closest-point / ray-intersection queries
    void buildAABBTree(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    );

    // Find closest point on surface, returns projected point and triangle index
    struct ClosestPointResult {
        Vec3 point;
        int triangle_index = -1;
        double distance_sq = 0.0;
    };
    ClosestPointResult closestPoint(const Vec3& query) const;

    // Signed distance (negative = inside, positive = outside)
    double signedDistance(const Vec3& query) const;

    // Boolean inside/outside test (winding number based)
    bool isInside(const Vec3& query) const;

    // Ray intersection count
    int rayIntersections(const Vec3& origin, const Vec3& dir) const;

    // Surface remeshing
    struct RemeshResult {
        std::vector<Vec3> vertices;
        std::vector<std::array<int, 3>> triangles;
        std::vector<int> triangle_patch_ids;
    };

    // Isotropic remeshing with target edge length
    RemeshResult isotropicRemesh(
        const std::vector<Vec3>& input_vertices,
        const std::vector<std::array<int, 3>>& input_triangles,
        double target_edge_length,
        int num_iterations = 10,
        bool protect_features = true
    ) const;

    // Adaptive remeshing with per-vertex target sizes
    RemeshResult adaptiveRemesh(
        const std::vector<Vec3>& input_vertices,
        const std::vector<std::array<int, 3>>& input_triangles,
        const std::vector<double>& target_sizes,
        int num_iterations = 10,
        bool protect_features = true
    ) const;

    // Feature preservation
    struct FeatureLine {
        std::vector<int> vertex_indices;
    };
    std::vector<FeatureLine> extractFeatureLines(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles,
        double angle_threshold_deg
    ) const;

    // Hole filling
    struct HoleFillResult {
        std::vector<std::array<int, 3>> new_triangles;
        bool success = false;
    };
    HoleFillResult fillHole(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles,
        int hole_boundary_vertex
    ) const;

    // Detect holes in surface mesh
    std::vector<std::vector<int>> detectBoundaryCycles(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Self-intersection detection
    bool hasSelfIntersections(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Compute vertex normals (area-weighted average of incident face normals)
    std::vector<Vec3> computeVertexNormals(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Compute face normals
    std::vector<Vec3> computeFaceNormals(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Compute dihedral angles for all edges
    std::vector<double> computeDihedralAnglesDeg(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Compute mean curvature at vertices
    std::vector<double> computeMeanCurvature(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Smooth surface (Laplacian with cotangent weights)
    void smoothSurface(
        std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles,
        int iterations = 10,
        double relaxation = 0.1
    ) const;

    // Boolean operations (union, intersection, difference)
    enum class BooleanOp { Union, Intersection, Difference };
    std::optional<RemeshResult> booleanOp(
        const RemeshResult& mesh_a,
        const RemeshResult& mesh_b,
        BooleanOp op
    ) const;

    // Check watertight (closed manifold)
    bool isWatertight(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Check manifold
    bool isManifold(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Check normals consistent
    bool areNormalsConsistent(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) const;

    // Orient normals outward
    void orientOutward(
        std::vector<Vec3>& vertices,
        std::vector<std::array<int, 3>>& triangles
    ) const;

    // Merge duplicate vertices within tolerance
    void mergeDuplicates(
        std::vector<Vec3>& vertices,
        std::vector<std::array<int, 3>>& triangles,
        double tolerance
    ) const;

    // Remove degenerate triangles
    void removeDegenerateTriangles(
        const std::vector<Vec3>& vertices,
        std::vector<std::array<int, 3>>& triangles,
        std::vector<int>& face_patch_ids,
        double min_area = 1e-30
    ) const;

    // Compute triangle area
    double triangleArea(
        const Vec3& a, const Vec3& b, const Vec3& c
    ) const;

    // Compute tetrahedron signed volume
    double tetrahedronVolume(
        const Vec3& a, const Vec3& b, const Vec3& c, const Vec3& d
    ) const;

    // Check if point is inside convex polyhedron defined by half-planes
    // Each half-plane: dot(normal, x) + d <= 0
    bool pointInConvexPolyhedron(
        const Vec3& point,
        const std::vector<std::pair<Vec3, double>>& half_planes
    ) const;

    // Intersect half-plane with polygon (2D cross-section)
    struct HalfPlane {
        Vec3 normal;  // unit normal pointing outward
        double d;     // dot(normal, x) + d = 0 defines the plane
    };
    std::vector<Vec3> clipPolygonByHalfPlane(
        const std::vector<Vec3>& polygon,
        const HalfPlane& plane
    ) const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace autopoly