#pragma once

#include <vector>
#include <string>
#include <array>
#include <optional>
#include <functional>
#include <filesystem>
#include <memory>
#include <cmath>
#include <cstdint>
#include <utility>
#include <algorithm>

namespace autopoly {

// Basic types
struct Vec3 {
    double x = 0.0, y = 0.0, z = 0.0;

    Vec3() = default;
    Vec3(double x_, double y_, double z_) : x(x_), y(y_), z(z_) {}

    Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    Vec3 operator*(double s) const { return {x * s, y * s, z * s}; }
    Vec3 operator/(double s) const { return {x / s, y / s, z / s}; }
    Vec3& operator+=(const Vec3& o) { x += o.x; y += o.y; z += o.z; return *this; }
    Vec3& operator-=(const Vec3& o) { x -= o.x; y -= o.y; z -= o.z; return *this; }
    Vec3& operator*=(double s) { x *= s; y *= s; z *= s; return *this; }
    bool operator==(const Vec3& o) const { return x == o.x && y == o.y && z == o.z; }
    bool operator!=(const Vec3& o) const { return !(*this == o); }
};

inline double dot(const Vec3& a, const Vec3& b) { return a.x*b.x + a.y*b.y + a.z*b.z; }
inline Vec3 cross(const Vec3& a, const Vec3& b) {
    return {a.y*b.z - a.z*b.y, a.z*b.x - a.x*b.z, a.x*b.y - a.y*b.x};
}
inline double norm(const Vec3& v) { return std::sqrt(dot(v, v)); }
inline Vec3 normalize(const Vec3& v) { double n = norm(v); return n > 0 ? v / n : Vec3{0,0,0}; }
inline double distance(const Vec3& a, const Vec3& b) { return norm(a - b); }
inline Vec3 lerp(const Vec3& a, const Vec3& b, double t) { return a + (b - a) * t; }

struct BoundingBox {
    Vec3 min{0,0,0}, max{0,0,0};
    bool empty() const { return min.x > max.x; }
    Vec3 center() const { return (min + max) * 0.5; }
    Vec3 size() const { return max - min; }
    double maxDim() const { auto s = size(); return std::max(s.x, std::max(s.y, s.z)); }
    double diagonal() const { return norm(max - min); }
    void expand(const Vec3& p) {
        if (empty()) { min = max = p; }
        else { min.x = std::min(min.x, p.x); min.y = std::min(min.y, p.y); min.z = std::min(min.z, p.z);
               max.x = std::max(max.x, p.x); max.y = std::max(max.y, p.y); max.z = std::max(max.z, p.z); }
    }
    void expand(const BoundingBox& bb) { expand(bb.min); expand(bb.max); }
    bool contains(const Vec3& p) const {
        return p.x >= min.x && p.x <= max.x && p.y >= min.y && p.y <= max.y && p.z >= min.z && p.z <= max.z;
    }
};

// Feature edge: a pair of vertex indices with a dihedral angle
struct FeatureEdge {
    int v0 = -1, v1 = -1;
    double dihedral_angle_deg = 0.0;
    bool is_sharp = false;
};

// Geometry options
struct GeometryOptions {
    double mergeTolerance = 1e-9;
    bool fillSmallHoles = true;
    double maxHoleArea = -1.0;  // -1 = auto
    bool orientNormals = true;
    bool repairSelfIntersections = false;
};

// Feature options
struct FeatureOptions {
    bool enable = true;
    double featureAngleDeg = 30.0;
    double curvatureSensitivity = 0.5;
};

// Size field options
struct SizeFieldOptions {
    double globalSize = 0.05;
    double minSize = 0.001;
    double maxSize = 1.0;
    double curvatureAdaptivity = 0.5;
    double proximityAdaptivity = 0.5;
    double maxGrowthRate = 1.2;
};

// Surface mesh options
struct SurfaceMeshOptions {
    double targetSize = 0.03;
    double minQuality = 0.2;
    int maxIterations = 20;
    bool preserveFeatures = true;
};

// Volume mesh options
struct VolumeMeshOptions {
    int seed = 12345;
    int lloydIterations = 30;
    double boundaryConformity = 0.9;
    bool mergeCoplanarFaces = true;
    double coplanarAngleToleranceDeg = 1.0;
    double smallFaceAreaTolerance = 1e-12;
    double minCellVolume = 1e-30;
};

// Boundary layer options
struct BoundaryLayerOptions {
    bool enable = false;
    int layers = 3;
    double firstHeight = 0.0005;
    double growthRate = 1.2;
    double maxThickness = 0.01;
    bool smoothNormals = true;
    bool detectCollisions = true;
};

// Quality options
struct QualityOptions {
    double maxNonOrthoWarn = 70.0;
    double maxNonOrthoFail = 85.0;
    double maxSkewnessWarn = 4.0;
    double maxSkewnessFail = 10.0;
    double maxAspectRatioWarn = 100.0;
    double maxAspectRatioFail = 1000.0;
};

// Export options
struct ExportOptions {
    bool writeVtk = true;
    bool writeOpenFoam = true;
    bool writeJsonReport = true;
    std::string outputDirectory = "output";
};

// Progress callback
struct ProgressInfo {
    int stagePercent = 0;
    std::string stageName;
    std::string message;
    bool cancellable = true;
};

using ProgressCallback = std::function<void(const ProgressInfo&)>;
using CancelToken = std::function<bool()>;

// All meshing parameters
struct MeshingParameters {
    GeometryOptions geometry;
    FeatureOptions features;
    SizeFieldOptions sizeField;
    SurfaceMeshOptions surface;
    VolumeMeshOptions volume;
    BoundaryLayerOptions boundaryLayer;
    QualityOptions quality;
    ExportOptions exportOptions;
};

// Meshing result
struct MeshingResult {
    bool success = false;
    std::string message;
    std::string outputDirectory;
    size_t pointCount = 0;
    size_t cellCount = 0;
    size_t faceCount = 0;
    size_t boundaryPatchCount = 0;
    double minVolume = 0.0;
    double maxNonOrthogonality = 0.0;
    double maxSkewness = 0.0;
    double maxAspectRatio = 0.0;
    std::vector<std::string> warnings;
    std::vector<std::string> errors;
};

// Mesh data structures
struct MeshPoint {
    Vec3 position;
    bool is_boundary = false;
    int patch_id = -1;
};

struct MeshFace {
    std::vector<int> vertices;  // vertex indices (CCW from outside)
    int owner_cell = -1;
    int neighbour_cell = -1;  // -1 = boundary
    int patch_id = -1;
    bool is_boundary() const { return neighbour_cell == -1; }
};

struct MeshCell {
    std::vector<int> faces;  // face indices
    Vec3 centroid;
    double volume = 0.0;
};

struct BoundaryPatch {
    std::string name;
    std::string type = "patch";  // patch, wall, inlet, outlet, symmetry, etc.
    std::vector<int> face_indices;
};

struct PolyMesh {
    std::vector<MeshPoint> points;
    std::vector<MeshFace> faces;
    std::vector<MeshCell> cells;
    std::vector<BoundaryPatch> patches;
    std::vector<FeatureEdge> feature_edges;

    // Validation
    bool validate() const;
    void compute_centroids();
    void compute_volumes();
};

// Forward declarations
class Mesher;
std::unique_ptr<Mesher> createMesher();

} // namespace autopoly