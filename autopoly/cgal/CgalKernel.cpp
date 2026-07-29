#include "CgalKernel.h"

#include <CGAL/Exact_predicates_inexact_constructions_kernel.h>
#include <CGAL/Surface_mesh.h>
#include <CGAL/AABB_tree.h>
#include <CGAL/AABB_traits.h>
#include <CGAL/AABB_triangle_primitive.h>
#include <CGAL/Polygon_mesh_processing/remesh.h>
#include <CGAL/Polygon_mesh_processing/triangulate_hole.h>
#include <CGAL/Polygon_mesh_processing/self_intersections.h>
#include <CGAL/Polygon_mesh_processing/normal.h>
#include <CGAL/Polygon_mesh_processing/orientation.h>
#include <CGAL/Polygon_mesh_processing/corefinement.h>
#include <CGAL/Polygon_mesh_processing/stitch_borders.h>
#include <CGAL/Polygon_mesh_processing/merge_duplicated_vertices_in_polygon_soup.h>
#include <CGAL/Polygon_mesh_processing/smooth_mesh.h>
#include <CGAL/Polygon_mesh_processing/measure.h>
#include <CGAL/Polygon_mesh_processing/border.h>
#include <CGAL/Polygon_mesh_processing/repair.h>
#include <CGAL/Side_of_triangle_mesh.h>

#include <CGAL/boost/graph/iterator.h>
#include <CGAL/boost/graph/Euler_operations.h>
#include <CGAL/boost/graph/properties.h>

#include <cmath>
#include <limits>
#include <stdexcept>
#include <unordered_set>
#include <unordered_map>

namespace autopoly {

// ---------------------------------------------------------------------------
// CGAL type aliases (hidden in .cpp)
// ---------------------------------------------------------------------------
typedef CGAL::Exact_predicates_inexact_constructions_kernel  Epick;
typedef Epick::Point_3                                       Point_3;
typedef Epick::Vector_3                                      Vector_3;
typedef Epick::Triangle_3                                    Triangle_3;
typedef Epick::Plane_3                                       Plane_3;
typedef Epick::Ray_3                                         Ray_3;

typedef CGAL::Surface_mesh<Point_3>                          Surface_mesh;
typedef Surface_mesh::Vertex_index                           Vertex_id;
typedef Surface_mesh::Face_index                             Face_id;
typedef Surface_mesh::Halfedge_index                         Halfedge_id;
typedef Surface_mesh::Edge_index                             Edge_id;

typedef std::vector<Triangle_3>::iterator                    Tri_iter;
typedef CGAL::AABB_triangle_primitive<Epick, Tri_iter>       AABB_primitive;
typedef CGAL::AABB_traits<Epick, AABB_primitive>             AABB_traits;
typedef CGAL::AABB_tree<AABB_traits>                         AABB_tree;

namespace PMP = CGAL::Polygon_mesh_processing;

// ---------------------------------------------------------------------------
// CGAL ↔ autopoly conversion helpers
// ---------------------------------------------------------------------------
static Point_3 toCgalPoint(const Vec3& v) {
    return Point_3(v.x, v.y, v.z);
}

static Vec3 fromCgalPoint(const Point_3& p) {
    return Vec3(p.x(), p.y(), p.z());
}

static Vector_3 toCgalVector(const Vec3& v) {
    return Vector_3(v.x, v.y, v.z);
}

static Vec3 fromCgalVector(const Vector_3& v) {
    return Vec3(v.x(), v.y(), v.z());
}

static Surface_mesh toSurfaceMesh(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) {
    Surface_mesh mesh;
    std::vector<Vertex_id> vmap(vertices.size());
    for (size_t i = 0; i < vertices.size(); ++i)
        vmap[i] = mesh.add_vertex(toCgalPoint(vertices[i]));
    for (const auto& tri : triangles) {
        mesh.add_face(vmap[tri[0]], vmap[tri[1]], vmap[tri[2]]);
    }
    return mesh;
}

static void fromSurfaceMesh(
    const Surface_mesh& mesh,
    std::vector<Vec3>& vertices,
    std::vector<std::array<int, 3>>& triangles
) {
    vertices.clear();
    triangles.clear();
    std::unordered_map<Vertex_id, int> vid_map;
    for (auto v : mesh.vertices()) {
        vid_map[v] = static_cast<int>(vertices.size());
        vertices.push_back(fromCgalPoint(mesh.point(v)));
    }
    for (auto f : mesh.faces()) {
        auto h = mesh.halfedge(f);
        std::array<int, 3> tri;
        int i = 0;
        for (auto v : CGAL::vertices_around_face(h, mesh)) {
            tri[i++] = vid_map.at(v);
            if (i == 3) break;
        }
        if (i == 3)
            triangles.push_back(tri);
    }
}

// ---------------------------------------------------------------------------
// CgalKernel::Impl – pimpl  (all CGAL types hidden here)
// ---------------------------------------------------------------------------
class CgalKernel::Impl {
public:
    // Stored data for AABB queries
    std::vector<Triangle_3>        aabb_triangles_;
    std::unique_ptr<AABB_tree>     aabb_tree_;

    // Build from a triangle soup
    void buildAABBTree(
        const std::vector<Vec3>& vertices,
        const std::vector<std::array<int, 3>>& triangles
    ) {
        if (vertices.empty() || triangles.empty())
            throw std::runtime_error("CgalKernel::buildAABBTree: empty input");

        aabb_triangles_.clear();
        aabb_triangles_.reserve(triangles.size());

        for (const auto& tri : triangles) {
            if (tri[0] < 0 || tri[0] >= static_cast<int>(vertices.size()) ||
                tri[1] < 0 || tri[1] >= static_cast<int>(vertices.size()) ||
                tri[2] < 0 || tri[2] >= static_cast<int>(vertices.size()))
                throw std::runtime_error("CgalKernel::buildAABBTree: vertex index out of range");
            aabb_triangles_.emplace_back(
                toCgalPoint(vertices[tri[0]]),
                toCgalPoint(vertices[tri[1]]),
                toCgalPoint(vertices[tri[2]])
            );
        }

        aabb_tree_ = std::make_unique<AABB_tree>(
            aabb_triangles_.begin(), aabb_triangles_.end()
        );
        aabb_tree_->build();
    }

    // Ensure AABB tree is built
    void requireTreeBuilt() const {
        if (!aabb_tree_)
            throw std::runtime_error("CgalKernel: AABB tree not built – call buildAABBTree first");
    }
};

// ---------------------------------------------------------------------------
// CgalKernel public implementation
// ---------------------------------------------------------------------------
CgalKernel::CgalKernel()
    : impl_(std::make_unique<Impl>())
{}

CgalKernel::~CgalKernel() = default;

CgalKernel::CgalKernel(CgalKernel&&) noexcept = default;
CgalKernel& CgalKernel::operator=(CgalKernel&&) noexcept = default;

// ---------------------------------------------------------------------------
// 1. buildAABBTree
// ---------------------------------------------------------------------------
void CgalKernel::buildAABBTree(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) {
    impl_->buildAABBTree(vertices, triangles);
}

// ---------------------------------------------------------------------------
// 2. closestPoint
// ---------------------------------------------------------------------------
CgalKernel::ClosestPointResult CgalKernel::closestPoint(const Vec3& query) const {
    impl_->requireTreeBuilt();

    Point_3 p = toCgalPoint(query);
    auto result = impl_->aabb_tree_->closest_point_and_primitive(p);

    const Point_3& closest = result.first;
    Tri_iter it = result.second;
    int tri_idx = static_cast<int>(std::distance(impl_->aabb_triangles_.begin(), it));

    ClosestPointResult out;
    out.point = fromCgalPoint(closest);
    out.triangle_index = tri_idx;
    out.distance_sq = CGAL::squared_distance(p, closest);
    return out;
}

// ---------------------------------------------------------------------------
// 3. signedDistance
// ---------------------------------------------------------------------------
double CgalKernel::signedDistance(const Vec3& query) const {
    impl_->requireTreeBuilt();

    // Build temporary surface mesh for winding-number query
    Surface_mesh mesh;
    for (const auto& t : impl_->aabb_triangles_) {
        auto v0 = mesh.add_vertex(t.vertex(0));
        auto v1 = mesh.add_vertex(t.vertex(1));
        auto v2 = mesh.add_vertex(t.vertex(2));
        mesh.add_face(v0, v1, v2);
    }

    if (mesh.is_empty())
        return std::numeric_limits<double>::max();

    CGAL::Side_of_triangle_mesh<Surface_mesh, Epick> inside_test(mesh);
    Point_3 p = toCgalPoint(query);

    // Compute closest point distance (unsigned)
    auto cp = impl_->aabb_tree_->closest_point(p);
    double dist = std::sqrt(CGAL::squared_distance(p, cp));

    // Determine sign
    auto result = inside_test(p);
    if (result == CGAL::ON_BOUNDARY)
        return 0.0;
    return (result == CGAL::ON_UNBOUNDED_SIDE) ? dist : -dist;
}

// ---------------------------------------------------------------------------
// 4. isInside
// ---------------------------------------------------------------------------
bool CgalKernel::isInside(const Vec3& query) const {
    impl_->requireTreeBuilt();

    Surface_mesh mesh;
    for (const auto& t : impl_->aabb_triangles_) {
        auto v0 = mesh.add_vertex(t.vertex(0));
        auto v1 = mesh.add_vertex(t.vertex(1));
        auto v2 = mesh.add_vertex(t.vertex(2));
        mesh.add_face(v0, v1, v2);
    }

    if (mesh.is_empty())
        return false;

    CGAL::Side_of_triangle_mesh<Surface_mesh, Epick> inside_test(mesh);
    auto result = inside_test(toCgalPoint(query));
    return (result == CGAL::ON_BOUNDED_SIDE);
}

// ---------------------------------------------------------------------------
// 5. rayIntersections
// ---------------------------------------------------------------------------
int CgalKernel::rayIntersections(const Vec3& origin, const Vec3& dir) const {
    impl_->requireTreeBuilt();

    Point_3 o = toCgalPoint(origin);
    Vector_3 d = toCgalVector(dir);
    Ray_3 ray(o, d);

    // Collect all intersections
    auto intersections = impl_->aabb_tree_->all_intersections(ray);
    return static_cast<int>(intersections.size());
}

// ---------------------------------------------------------------------------
// 6. isotropicRemesh
// ---------------------------------------------------------------------------
CgalKernel::RemeshResult CgalKernel::isotropicRemesh(
    const std::vector<Vec3>& input_vertices,
    const std::vector<std::array<int, 3>>& input_triangles,
    double target_edge_length,
    int num_iterations,
    bool protect_features
) const {
    if (input_vertices.empty() || input_triangles.empty())
        return {};

    Surface_mesh mesh = toSurfaceMesh(input_vertices, input_triangles);

    if (protect_features) {
        PMP::isotropic_remeshing(
            faces(mesh),
            target_edge_length,
            mesh,
            PMP::parameters::number_of_iterations(num_iterations)
                .protect_constraints(true)
        );
    } else {
        PMP::isotropic_remeshing(
            faces(mesh),
            target_edge_length,
            mesh,
            PMP::parameters::number_of_iterations(num_iterations)
        );
    }

    RemeshResult result;
    fromSurfaceMesh(mesh, result.vertices, result.triangles);
    // Patch IDs not tracked for isotropic remesh
    result.triangle_patch_ids.assign(result.triangles.size(), 0);
    return result;
}

// ---------------------------------------------------------------------------
// 7. adaptiveRemesh
// ---------------------------------------------------------------------------
CgalKernel::RemeshResult CgalKernel::adaptiveRemesh(
    const std::vector<Vec3>& input_vertices,
    const std::vector<std::array<int, 3>>& input_triangles,
    const std::vector<double>& target_sizes,
    int num_iterations,
    bool protect_features
) const {
    if (input_vertices.empty() || input_triangles.empty())
        return {};

    Surface_mesh mesh = toSurfaceMesh(input_vertices, input_triangles);

    // Compute default target edge length from average of target_sizes
    double base_length = 0.0;
    if (!target_sizes.empty()) {
        for (double s : target_sizes) base_length += s;
        base_length /= static_cast<double>(target_sizes.size());
    } else {
        base_length = 0.05;
    }

    // Attach per-vertex sizing field
    auto sizing = mesh.add_property_map<Vertex_id, double>("v:sizing", base_length).first;
    for (auto v : mesh.vertices()) {
        int idx = static_cast<int>(v);  // vertex index matches input order
        if (idx < static_cast<int>(target_sizes.size()))
            sizing[v] = target_sizes[idx];
    }

    PMP::isotropic_remeshing(
        faces(mesh),
        base_length,  // base length – per-vertex sizing will override
        mesh,
        PMP::parameters::number_of_iterations(num_iterations)
            .vertex_sizing_map(sizing)
            .protect_constraints(protect_features)
    );

    RemeshResult result;
    fromSurfaceMesh(mesh, result.vertices, result.triangles);
    result.triangle_patch_ids.assign(result.triangles.size(), 0);
    return result;
}

// ---------------------------------------------------------------------------
// 8. extractFeatureLines
// ---------------------------------------------------------------------------
std::vector<CgalKernel::FeatureLine> CgalKernel::extractFeatureLines(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles,
    double angle_threshold_deg
) const {
    if (vertices.empty() || triangles.empty())
        return {};

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    double cos_thresh = std::cos(angle_threshold_deg * CGAL_PI / 180.0);

    // Mark sharp edges via dihedral angle
    auto edge_is_sharp = mesh.add_property_map<Edge_id, bool>("e:sharp", false).first;
    for (auto e : mesh.edges()) {
        auto h = mesh.halfedge(e);
        if (is_border(h, mesh)) continue;

        auto f1 = mesh.face(h);
        auto f2 = mesh.face(mesh.opposite(h));
        if (f1 == Surface_mesh::null_face() || f2 == Surface_mesh::null_face())
            continue;

        auto n1 = PMP::compute_face_normal(f1, mesh);
        auto n2 = PMP::compute_face_normal(f2, mesh);
        double dot_prod = n1 * n2;
        // Clamp
        dot_prod = std::max(-1.0, std::min(1.0, dot_prod));
        edge_is_sharp[e] = (dot_prod <= cos_thresh);
    }

    // Build feature lines from sharp edges
    std::vector<FeatureLine> lines;
    auto edge_used = mesh.add_property_map<Edge_id, bool>("e:used", false).first;

    for (auto e : mesh.edges()) {
        if (!edge_is_sharp[e] || edge_used[e]) continue;

        // Start a new line
        FeatureLine line;

        // Walk one direction from this edge
        auto start_h = mesh.halfedge(e);
        // Find an endpoint: a vertex that has != 2 sharp incident edges
        auto walk = [&](Halfedge_id h) -> Halfedge_id {
            bool done = false;
            while (!done) {
                done = true;
                edge_used[mesh.edge(h)] = true;
                line.vertex_indices.push_back(static_cast<int>(mesh.source(h)));

                int count_sharp = 0;
                Halfedge_id next_h;
                for (auto circ : CGAL::halfedges_around_target(mesh.target(h), mesh)) {
                    Edge_id ce = mesh.edge(circ);
                    if (edge_is_sharp[ce] && !edge_used[ce]) {
                        ++count_sharp;
                        next_h = circ;
                    }
                }

                if (count_sharp == 1) {
                    h = mesh.opposite(next_h);
                    done = false;
                }
            }
            return h;
        };

        walk(start_h);
        // Reverse and walk other direction
        std::reverse(line.vertex_indices.begin(), line.vertex_indices.end());
        walk(mesh.opposite(start_h));

        if (!line.vertex_indices.empty())
            lines.push_back(std::move(line));
    }

    mesh.remove_property_map(edge_is_sharp);
    mesh.remove_property_map(edge_used);
    return lines;
}

// ---------------------------------------------------------------------------
// 9. fillHole
// ---------------------------------------------------------------------------
CgalKernel::HoleFillResult CgalKernel::fillHole(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles,
    int hole_boundary_vertex
) const {
    HoleFillResult result;

    if (vertices.empty() || triangles.empty())
        return result;

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);

    if (hole_boundary_vertex < 0 ||
        hole_boundary_vertex >= static_cast<int>(vertices.size()))
        return result;

    Vertex_id v_start(hole_boundary_vertex);
    if (!mesh.is_valid(v_start))
        return result;

    // Find boundary halfedge incident to the given vertex
    Halfedge_id border_h;
    bool found = false;
    for (auto h : CGAL::halfedges_around_target(v_start, mesh)) {
        if (is_border(h, mesh)) {
            border_h = h;
            found = true;
            break;
        }
    }
    if (!found) return result;

    std::vector<Face_id> new_faces;
    bool ok = PMP::triangulate_hole(
        mesh,
        border_h,
        std::back_inserter(new_faces)
    );

    if (!ok || new_faces.empty()) return result;

    result.success = true;
    for (auto f : new_faces) {
        auto h = mesh.halfedge(f);
        std::array<int, 3> tri;
        int i = 0;
        for (auto v : CGAL::vertices_around_face(h, mesh)) {
            tri[i++] = static_cast<int>(v);
            if (i == 3) break;
        }
        if (i == 3)
            result.new_triangles.push_back(tri);
    }

    return result;
}

// ---------------------------------------------------------------------------
// 10. detectBoundaryCycles
// ---------------------------------------------------------------------------
std::vector<std::vector<int>> CgalKernel::detectBoundaryCycles(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (vertices.empty() || triangles.empty())
        return {};

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);

    auto visited = mesh.add_property_map<Halfedge_id, bool>("h:visited", false).first;
    std::vector<std::vector<int>> cycles;

    for (auto h : mesh.halfedges()) {
        if (!is_border(h, mesh) || visited[h])
            continue;

        std::vector<int> cycle;
        auto curr = h;
        do {
            visited[curr] = true;
            cycle.push_back(static_cast<int>(mesh.source(curr)));
            curr = mesh.next(curr);
        } while (curr != h);

        if (!cycle.empty())
            cycles.push_back(std::move(cycle));
    }

    mesh.remove_property_map(visited);
    return cycles;
}

// ---------------------------------------------------------------------------
// 11. hasSelfIntersections
// ---------------------------------------------------------------------------
bool CgalKernel::hasSelfIntersections(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (triangles.empty()) return false;

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    return PMP::does_self_intersect(mesh);
}

// ---------------------------------------------------------------------------
// 12. computeVertexNormals
// ---------------------------------------------------------------------------
std::vector<Vec3> CgalKernel::computeVertexNormals(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (vertices.empty()) return {};

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);

    auto normals = mesh.add_property_map<Vertex_id, Vector_3>("v:normal", Vector_3(0,0,0)).first;
    PMP::compute_vertex_normals(mesh, normals);

    std::vector<Vec3> out(mesh.number_of_vertices());
    for (auto v : mesh.vertices())
        out[static_cast<size_t>(v)] = fromCgalVector(normals[v]);

    return out;
}

// ---------------------------------------------------------------------------
// 13. computeFaceNormals
// ---------------------------------------------------------------------------
std::vector<Vec3> CgalKernel::computeFaceNormals(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);

    auto normals = mesh.add_property_map<Face_id, Vector_3>("f:normal", Vector_3(0,0,0)).first;
    PMP::compute_face_normals(mesh, normals);

    std::vector<Vec3> out(mesh.number_of_faces());
    for (auto f : mesh.faces())
        out[static_cast<size_t>(f)] = fromCgalVector(normals[f]);

    return out;
}

// ---------------------------------------------------------------------------
// 14. computeDihedralAnglesDeg
// ---------------------------------------------------------------------------
std::vector<double> CgalKernel::computeDihedralAnglesDeg(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (triangles.empty()) return {};

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);

    std::vector<double> angles;
    angles.reserve(mesh.number_of_edges());

    for (auto e : mesh.edges()) {
        auto h = mesh.halfedge(e);
        auto f1 = mesh.face(h);
        auto f2 = mesh.face(mesh.opposite(h));

        if (f1 == Surface_mesh::null_face() || f2 == Surface_mesh::null_face()) {
            angles.push_back(180.0); // boundary edge – treat as flat
            continue;
        }

        Vector_3 n1 = PMP::compute_face_normal(f1, mesh);
        Vector_3 n2 = PMP::compute_face_normal(f2, mesh);

        // Compute signed dihedral angle
        auto edge_vec = toCgalVector(
            fromCgalPoint(mesh.point(mesh.source(h))) -
            fromCgalPoint(mesh.point(mesh.target(h)))
        );
        Vector_3 cross_n = CGAL::cross_product(n1, n2);
        double signed_dot = cross_n * (edge_vec / std::sqrt(edge_vec.squared_length()));
        double cos_angle = n1 * n2;
        cos_angle = std::max(-1.0, std::min(1.0, cos_angle));

        double angle_rad = std::acos(cos_angle);
        if (signed_dot < 0)
            angle_rad = -angle_rad;

        angles.push_back(angle_rad * 180.0 / CGAL_PI);
    }

    return angles;
}

// ---------------------------------------------------------------------------
// 15. computeMeanCurvature (cotangent Laplacian)
// ---------------------------------------------------------------------------
std::vector<double> CgalKernel::computeMeanCurvature(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (vertices.empty()) return {};

    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    std::vector<double> curvature(mesh.number_of_vertices(), 0.0);

    // Build adjacency: for each vertex, collect incident face normals and cotangent weights
    // We use the cotangent Laplacian:
    //   Δ(v_i) = (1 / (2*A_i)) * sum_j (cot α_ij + cot β_ij) * (v_j - v_i)
    //   H(v_i) = ||Δ(v_i)|| / 2

    auto vpoint = get(CGAL::vertex_point, mesh);

    // Precompute face areas and normals
    auto f_normal = mesh.add_property_map<Face_id, Vector_3>("f:n", Vector_3(0,0,0)).first;
    auto f_area   = mesh.add_property_map<Face_id, double>("f:area", 0.0).first;
    for (auto f : mesh.faces()) {
        auto h = mesh.halfedge(f);
        auto v0 = mesh.source(h);
        auto v1 = mesh.source(mesh.next(h));
        auto v2 = mesh.source(mesh.next(mesh.next(h)));
        Point_3 p0 = vpoint[v0], p1 = vpoint[v1], p2 = vpoint[v2];
        Vector_3 e1 = p1 - p0, e2 = p2 - p0;
        Vector_3 n = CGAL::cross_product(e1, e2);
        f_area[f] = std::sqrt(n.squared_length()) * 0.5;
        f_normal[f] = n / (2.0 * f_area[f] + 1e-60);
    }

    // Compute mixed Voronoi area per vertex and Laplacian
    auto v_area = mesh.add_property_map<Vertex_id, double>("v:area", 0.0).first;
    auto laplacian = mesh.add_property_map<Vertex_id, Vector_3>("v:lap", Vector_3(0,0,0)).first;

    for (auto f : mesh.faces()) {
        auto h = mesh.halfedge(f);
        auto v[3] = {
            mesh.source(h),
            mesh.source(mesh.next(h)),
            mesh.source(mesh.next(mesh.next(h)))
        };
        Point_3 p[3] = { vpoint[v[0]], vpoint[v[1]], vpoint[v[2]] };
        double a = f_area[f];

        // For each edge of the triangle, compute cotangent weights
        for (int i = 0; i < 3; ++i) {
            int j = (i + 1) % 3;
            int k = (i + 2) % 3;

            Vector_3 ejk = p[k] - p[j];  // edge opposite v[i]
            Vector_3 eji = p[i] - p[j];
            Vector_3 eki = p[i] - p[k];

            double cot_j = (eji * ejk) / std::sqrt(CGAL::cross_product(eji, ejk).squared_length() + 1e-60);
            double cot_k = (eki * (-ejk)) / std::sqrt(CGAL::cross_product(eki, -ejk).squared_length() + 1e-60);

            double w = (cot_j + cot_k) * 0.5;
            laplacian[v[i]] = laplacian[v[i]] + (p[j] - CGAL::ORIGIN) * w - (p[i] - CGAL::ORIGIN) * w;
            laplacian[v[i]] = laplacian[v[i]] + (p[k] - CGAL::ORIGIN) * w - (p[i] - CGAL::ORIGIN) * w;

            // Accumulate mixed area contribution
            // For simplicity, use barycentric cell area (1/3 of triangle area)
            v_area[v[i]] += a / 3.0;
        }
    }

    for (auto v : mesh.vertices()) {
        double A = v_area[v];
        if (A < 1e-60) {
            curvature[static_cast<size_t>(v)] = 0.0;
            continue;
        }
        Vector_3 lap = laplacian[v];
        double lap_norm = std::sqrt(lap.squared_length());
        curvature[static_cast<size_t>(v)] = lap_norm / (2.0 * A);
    }

    mesh.remove_property_map(f_normal);
    mesh.remove_property_map(f_area);
    mesh.remove_property_map(v_area);
    mesh.remove_property_map(laplacian);
    return curvature;
}

// ---------------------------------------------------------------------------
// 16. smoothSurface
// ---------------------------------------------------------------------------
void CgalKernel::smoothSurface(
    std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles,
    int iterations,
    double relaxation
) const {
    if (triangles.empty()) return;

    // Use explicit Laplacian smoothing with cotangent weights
    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    auto vpoint = get(CGAL::vertex_point, mesh);

    for (int iter = 0; iter < iterations; ++iter) {
        std::vector<Vector_3> deltas(mesh.number_of_vertices(), Vector_3(0,0,0));
        std::vector<double> weights(mesh.number_of_vertices(), 0.0);

        for (auto f : mesh.faces()) {
            auto h = mesh.halfedge(f);
            auto v = mesh.source(h);
            auto v_next = mesh.source(mesh.next(h));
            auto v_prev = mesh.source(mesh.next(mesh.next(h)));

            Point_3 p0 = vpoint[v];
            Point_3 p1 = vpoint[v_next];
            Point_3 p2 = vpoint[v_prev];

            // For each edge of this face, compute cotangent weight and apply
            // Edge (v, v_next) – opposite vertex is v_prev
            {
                Vector_3 e0 = p0 - p2;
                Vector_3 e1 = p1 - p2;
                double cot = (e0 * e1) / std::sqrt(
                    CGAL::cross_product(e0, e1).squared_length() + 1e-60);
                double w = cot * 0.5;
                deltas[static_cast<size_t>(v)] += (p1 - CGAL::ORIGIN) * w;
                weights[static_cast<size_t>(v)] += w;
                deltas[static_cast<size_t>(v_next)] += (p0 - CGAL::ORIGIN) * w;
                weights[static_cast<size_t>(v_next)] += w;
            }
            // Edge (v_next, v_prev) – opposite vertex is v
            {
                Vector_3 e0 = p1 - p0;
                Vector_3 e1 = p2 - p0;
                double cot = (e0 * e1) / std::sqrt(
                    CGAL::cross_product(e0, e1).squared_length() + 1e-60);
                double w = cot * 0.5;
                deltas[static_cast<size_t>(v_next)] += (p2 - CGAL::ORIGIN) * w;
                weights[static_cast<size_t>(v_next)] += w;
                deltas[static_cast<size_t>(v_prev)] += (p1 - CGAL::ORIGIN) * w;
                weights[static_cast<size_t>(v_prev)] += w;
            }
            // Edge (v_prev, v) – opposite vertex is v_next
            {
                Vector_3 e0 = p2 - p1;
                Vector_3 e1 = p0 - p1;
                double cot = (e0 * e1) / std::sqrt(
                    CGAL::cross_product(e0, e1).squared_length() + 1e-60);
                double w = cot * 0.5;
                deltas[static_cast<size_t>(v_prev)] += (p0 - CGAL::ORIGIN) * w;
                weights[static_cast<size_t>(v_prev)] += w;
                deltas[static_cast<size_t>(v)] += (p2 - CGAL::ORIGIN) * w;
                weights[static_cast<size_t>(v)] += w;
            }
        }

        // Apply smoothing
        for (auto v : mesh.vertices()) {
            size_t idx = static_cast<size_t>(v);
            double w = weights[idx];
            if (w < 1e-60) continue;
            Vector_3 centroid = deltas[idx] / w;
            Vector_3 diff = centroid - (vpoint[v] - CGAL::ORIGIN);
            vpoint[v] = vpoint[v] + diff * relaxation;
        }
    }

    // Write back
    for (auto v : mesh.vertices())
        vertices[static_cast<size_t>(v)] = fromCgalPoint(vpoint[v]);
}

// ---------------------------------------------------------------------------
// 17. booleanOp
// ---------------------------------------------------------------------------
std::optional<CgalKernel::RemeshResult> CgalKernel::booleanOp(
    const RemeshResult& mesh_a,
    const RemeshResult& mesh_b,
    BooleanOp op
) const {
    if (mesh_a.triangles.empty() || mesh_b.triangles.empty())
        return std::nullopt;

    Surface_mesh m_a = toSurfaceMesh(mesh_a.vertices, mesh_a.triangles);
    Surface_mesh m_b = toSurfaceMesh(mesh_b.vertices, mesh_b.triangles);
    Surface_mesh result;

    bool success = false;
    switch (op) {
    case BooleanOp::Union:
        success = PMP::corefine_and_compute_union(m_a, m_b, result);
        break;
    case BooleanOp::Intersection:
        success = PMP::corefine_and_compute_intersection(m_a, m_b, result);
        break;
    case BooleanOp::Difference:
        success = PMP::corefine_and_compute_difference(m_a, m_b, result);
        break;
    }

    if (!success || result.is_empty())
        return std::nullopt;

    RemeshResult out;
    fromSurfaceMesh(result, out.vertices, out.triangles);
    out.triangle_patch_ids.assign(out.triangles.size(), 0);
    return out;
}

// ---------------------------------------------------------------------------
// 18. isWatertight
// ---------------------------------------------------------------------------
bool CgalKernel::isWatertight(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (triangles.empty()) return false;
    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    return CGAL::is_closed(mesh);
}

// ---------------------------------------------------------------------------
// 19. isManifold
// ---------------------------------------------------------------------------
bool CgalKernel::isManifold(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (triangles.empty()) return false;
    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    return PMP::is_non_manifold_vertex(mesh) == false;
}

// ---------------------------------------------------------------------------
// 20. areNormalsConsistent
// ---------------------------------------------------------------------------
bool CgalKernel::areNormalsConsistent(
    const std::vector<Vec3>& vertices,
    const std::vector<std::array<int, 3>>& triangles
) const {
    if (triangles.empty()) return true;
    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);
    // Check edge-face consistency: for each edge, the two incident faces
    // should list the shared edge vertices in opposite order
    for (auto e : mesh.edges()) {
        auto h = mesh.halfedge(e);
        auto fo = mesh.face(h);
        auto fi = mesh.face(mesh.opposite(h));
        if (fo == Surface_mesh::null_face() || fi == Surface_mesh::null_face())
            continue;

        // Get vertices of the shared edge in the order of face fo
        auto v0 = mesh.source(h);
        auto v1 = mesh.target(h);

        // In face fi, the shared edge should be traversed in opposite order
        auto hop = mesh.opposite(h);
        auto sop = mesh.source(hop);
        auto top = mesh.target(hop);
        // consistent means: v0 == top && v1 == sop
        if (!(static_cast<int>(v0) == static_cast<int>(top) &&
              static_cast<int>(v1) == static_cast<int>(sop)))
            return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// 21. orientOutward
// ---------------------------------------------------------------------------
void CgalKernel::orientOutward(
    std::vector<Vec3>& vertices,
    std::vector<std::array<int, 3>>& triangles
) const {
    if (triangles.empty()) return;
    Surface_mesh mesh = toSurfaceMesh(vertices, triangles);

    // First make all faces consistent
    PMP::orient(mesh);

    // For closed meshes, check outward orientation
    if (CGAL::is_closed(mesh)) {
        if (!PMP::is_outward_oriented(mesh)) {
            PMP::reverse_face_orientations(mesh);
        }
    }

    vertices.clear();
    triangles.clear();
    fromSurfaceMesh(mesh, vertices, triangles);
}

// ---------------------------------------------------------------------------
// 22. mergeDuplicates
// ---------------------------------------------------------------------------
void CgalKernel::mergeDuplicates(
    std::vector<Vec3>& vertices,
    std::vector<std::array<int, 3>>& triangles,
    double tolerance
) const {
    if (vertices.empty() || triangles.empty()) return;

    // Convert to CGAL polygon soup with Epick for exact epsilon comparisons
    std::vector<Point_3> pts;
    pts.reserve(vertices.size());
    for (const auto& v : vertices)
        pts.push_back(toCgalPoint(v));

    std::vector<std::vector<std::size_t>> poly_soup;
    poly_soup.reserve(triangles.size());
    for (const auto& tri : triangles) {
        poly_soup.push_back({
            static_cast<std::size_t>(tri[0]),
            static_cast<std::size_t>(tri[1]),
            static_cast<std::size_t>(tri[2])
        });
    }

    PMP::merge_duplicated_vertices_in_polygon_soup(
        pts, poly_soup, CGAL::parameters::epsilon(tolerance)
    );

    // Write back
    vertices.resize(pts.size());
    for (size_t i = 0; i < pts.size(); ++i)
        vertices[i] = fromCgalPoint(pts[i]);

    triangles.resize(poly_soup.size());
    for (size_t i = 0; i < poly_soup.size(); ++i) {
        triangles[i] = {
            static_cast<int>(poly_soup[i][0]),
            static_cast<int>(poly_soup[i][1]),
            static_cast<int>(poly_soup[i][2])
        };
    }
}

// ---------------------------------------------------------------------------
// 23. removeDegenerateTriangles
// ---------------------------------------------------------------------------
void CgalKernel::removeDegenerateTriangles(
    const std::vector<Vec3>& vertices,
    std::vector<std::array<int, 3>>& triangles,
    std::vector<int>& face_patch_ids,
    double min_area
) const {
    if (triangles.empty()) return;

    std::vector<std::array<int, 3>> valid_tris;
    std::vector<int> valid_ids;
    valid_tris.reserve(triangles.size());
    valid_ids.reserve(face_patch_ids.size());

    for (size_t i = 0; i < triangles.size(); ++i) {
        const auto& tri = triangles[i];
        double area = triangleArea(
            vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
        );
        if (area >= min_area) {
            valid_tris.push_back(tri);
            if (i < face_patch_ids.size())
                valid_ids.push_back(face_patch_ids[i]);
        }
    }

    triangles.swap(valid_tris);
    if (!valid_ids.empty())
        face_patch_ids.swap(valid_ids);
    else
        face_patch_ids.clear();
}

// ---------------------------------------------------------------------------
// 24. triangleArea
// ---------------------------------------------------------------------------
double CgalKernel::triangleArea(
    const Vec3& a, const Vec3& b, const Vec3& c
) const {
    return 0.5 * norm(cross(b - a, c - a));
}

// ---------------------------------------------------------------------------
// 25. tetrahedronVolume
// ---------------------------------------------------------------------------
double CgalKernel::tetrahedronVolume(
    const Vec3& a, const Vec3& b, const Vec3& c, const Vec3& d
) const {
    Vec3 ab = b - a;
    Vec3 ac = c - a;
    Vec3 ad = d - a;
    return dot(ab, cross(ac, ad)) / 6.0;
}

// ---------------------------------------------------------------------------
// 26. pointInConvexPolyhedron
// ---------------------------------------------------------------------------
bool CgalKernel::pointInConvexPolyhedron(
    const Vec3& point,
    const std::vector<std::pair<Vec3, double>>& half_planes
) const {
    for (const auto& hp : half_planes) {
        const Vec3& n = hp.first;
        double d = hp.second;
        // dot(n, point) + d <= 0  → inside this half-plane
        double val = dot(n, point) + d;
        if (val > 1e-12)
            return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// 27. clipPolygonByHalfPlane
// ---------------------------------------------------------------------------
std::vector<Vec3> CgalKernel::clipPolygonByHalfPlane(
    const std::vector<Vec3>& polygon,
    const HalfPlane& plane
) const {
    if (polygon.empty()) return {};

    auto is_inside = [&](const Vec3& p) -> bool {
        return dot(plane.normal, p) + plane.d <= 0.0;
    };

    auto intersect = [&](const Vec3& a, const Vec3& b) -> Vec3 {
        Vec3 ab = b - a;
        double t = - (dot(plane.normal, a) + plane.d) / dot(plane.normal, ab);
        return a + ab * t;
    };

    std::vector<Vec3> output;
    size_t n = polygon.size();
    for (size_t i = 0; i < n; ++i) {
        const Vec3& curr = polygon[i];
        const Vec3& next = polygon[(i + 1) % n];

        bool curr_in = is_inside(curr);
        bool next_in = is_inside(next);

        if (curr_in)
            output.push_back(curr);

        if (curr_in != next_in)
            output.push_back(intersect(curr, next));
    }

    return output;
}

} // namespace autopoly