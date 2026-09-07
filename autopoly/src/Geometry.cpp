#include <autopoly/Geometry.h>
#include <autopoly/cgal/CgalKernel.h>

#include <fstream>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include <queue>
#include <set>
#include <cctype>
#include <algorithm>
#include <numeric>
#include <array>
#include <functional>

namespace autopoly {
namespace {

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

struct EdgeKey {
    std::size_t v0, v1;
    bool operator==(const EdgeKey& o) const {
        return v0 == o.v0 && v1 == o.v1;
    }
};

struct EdgeHash {
    std::size_t operator()(const EdgeKey& k) const {
        return k.v0 ^ (k.v1 << 16) ^ (k.v1 >> 16);
    }
};

struct EdgeAdjInfo {
    int face_id[2] = {-1, -1};
    std::size_t count = 0;
};

using EdgeMap = std::unordered_map<EdgeKey, EdgeAdjInfo, EdgeHash>;

EdgeKey make_edge(std::size_t a, std::size_t b) {
    return (a < b) ? EdgeKey{a, b} : EdgeKey{b, a};
}

bool is_nan_or_inf(const Vec3& v) {
    return !std::isfinite(v.x) || !std::isfinite(v.y) || !std::isfinite(v.z);
}

// ---------------------------------------------------------------------------
// Spatial hash grid for vertex merging / intersection culling
// ---------------------------------------------------------------------------
class SpatialHashGrid {
public:
    explicit SpatialHashGrid(double cell_size) : cell_size_(cell_size) {}

    void insert(std::size_t idx, const Vec3& pos) {
        auto key = hash_key(pos);
        grid_[key].push_back({idx, pos});
    }

    // Find vertex within tolerance of pos, returns index or -1
    int find_nearby(const Vec3& pos, double tolerance) const {
        double tol_sq = tolerance * tolerance;
        int cx = static_cast<int>(std::floor(pos.x / cell_size_));
        int cy = static_cast<int>(std::floor(pos.y / cell_size_));
        int cz = static_cast<int>(std::floor(pos.z / cell_size_));

        for (int dx = -1; dx <= 1; ++dx) {
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dz = -1; dz <= 1; ++dz) {
                    auto it = grid_.find(std::make_tuple(cx + dx, cy + dy, cz + dz));
                    if (it == grid_.end()) continue;
                    for (const auto& entry : it->second) {
                        if (distance_sq(entry.pos, pos) <= tol_sq) {
                            return static_cast<int>(entry.idx);
                        }
                    }
                }
            }
        }
        return -1;
    }

    // Assign indices to stored vertices, return new index for each
    std::vector<std::size_t> compact_unique(const std::vector<Vec3>& vertices,
                                            double tolerance,
                                            std::size_t& removed_out) {
        std::vector<std::size_t> remap(vertices.size());
        std::vector<Vec3> unique;
        double tol_sq = tolerance * tolerance;
        removed_out = 0;

        for (std::size_t i = 0; i < vertices.size(); ++i) {
            const Vec3& p = vertices[i];
            int cx = static_cast<int>(std::floor(p.x / cell_size_));
            int cy = static_cast<int>(std::floor(p.y / cell_size_));
            int cz = static_cast<int>(std::floor(p.z / cell_size_));

            int found = -1;
            for (int dx = -1; dx <= 1; ++dx) {
                for (int dy = -1; dy <= 1; ++dy) {
                    for (int dz = -1; dz <= 1; ++dz) {
                        auto it = grid_.find(std::make_tuple(cx + dx, cy + dy, cz + dz));
                        if (it == grid_.end()) continue;
                        for (const auto& entry : it->second) {
                            if (distance_sq(entry.pos, p) <= tol_sq) {
                                found = static_cast<int>(entry.idx);
                                goto found;
                            }
                        }
                    }
                }
            }
        found:
            if (found >= 0) {
                remap[i] = static_cast<std::size_t>(found);
                ++removed_out;
            } else {
                std::size_t new_idx = unique.size();
                unique.push_back(p);
                remap[i] = new_idx;
                grid_[std::make_tuple(cx, cy, cz)].push_back({new_idx, p});
            }
        }

        // Write back unique vertices
        const_cast<std::vector<Vec3>&>(vertices).swap(unique);  // Not ideal, caller handles it
        return remap;
    }

    // Return all vertex indices in cells that could intersect the triangle
    std::vector<std::size_t> query_triangle(const Vec3& a, const Vec3& b, const Vec3& c) const {
        std::unordered_set<std::size_t> result;
        // Compute bounding box of triangle
        double min_x = std::min({a.x, b.x, c.x});
        double max_x = std::max({a.x, b.x, c.x});
        double min_y = std::min({a.y, b.y, c.y});
        double max_y = std::max({a.y, b.y, c.y});
        double min_z = std::min({a.z, b.z, c.z});
        double max_z = std::max({a.z, b.z, c.z});

        int cx0 = static_cast<int>(std::floor(min_x / cell_size_));
        int cx1 = static_cast<int>(std::floor(max_x / cell_size_));
        int cy0 = static_cast<int>(std::floor(min_y / cell_size_));
        int cy1 = static_cast<int>(std::floor(max_y / cell_size_));
        int cz0 = static_cast<int>(std::floor(min_z / cell_size_));
        int cz1 = static_cast<int>(std::floor(max_z / cell_size_));

        for (int cx = cx0; cx <= cx1; ++cx) {
            for (int cy = cy0; cy <= cy1; ++cy) {
                for (int cz = cz0; cz <= cz1; ++cz) {
                    auto it = grid_.find(std::make_tuple(cx, cy, cz));
                    if (it == grid_.end()) continue;
                    for (const auto& entry : it->second) {
                        result.insert(entry.idx);
                    }
                }
            }
        }
        return {result.begin(), result.end()};
    }

private:
    double distance_sq(const Vec3& a, const Vec3& b) const {
        double dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
        return dx * dx + dy * dy + dz * dz;
    }

    using HashKey = std::tuple<int, int, int>;
    struct Entry { std::size_t idx; Vec3 pos; };

    double cell_size_;
    std::unordered_map<HashKey, std::vector<Entry>> grid_;

    HashKey hash_key(const Vec3& p) const {
        return {static_cast<int>(std::floor(p.x / cell_size_)),
                static_cast<int>(std::floor(p.y / cell_size_)),
                static_cast<int>(std::floor(p.z / cell_size_))};
    }
};

// ---------------------------------------------------------------------------
// Ear-clipping triangulation for a planar polygon (projected to 2D)
// ---------------------------------------------------------------------------
bool triangulate_polygon(const std::vector<Vec3>& polygon,
                         std::vector<std::array<std::size_t, 3>>& out_triangles,
                         std::size_t vertex_offset) {
    std::size_t n = polygon.size();
    if (n < 3) return false;
    if (n == 3) {
        out_triangles.push_back({vertex_offset, vertex_offset + 1, vertex_offset + 2});
        return true;
    }

    // Compute best-fit plane normal
    Vec3 normal{0, 0, 0};
    for (std::size_t i = 0; i < n; ++i) {
        const Vec3& a = polygon[i];
        const Vec3& b = polygon[(i + 1) % n];
        normal.x += (a.y - b.y) * (a.z + b.z);
        normal.y += (a.z - b.z) * (a.x + b.x);
        normal.z += (a.x - b.x) * (a.y + b.y);
    }
    double nlen = norm(normal);
    if (nlen < 1e-30) return false;
    normal = normal / nlen;

    // Choose projection axes (drop the dominant component of normal)
    int u_axis = 0, v_axis = 1;
    {
        double ax = std::abs(normal.x), ay = std::abs(normal.y), az = std::abs(normal.z);
        if (ax > ay && ax > az) { u_axis = 1; v_axis = 2; }
        else if (ay > az)       { u_axis = 0; v_axis = 2; }
        else                    { u_axis = 0; v_axis = 1; }
    }

    // Project polygon to 2D
    std::vector<std::array<double, 2>> pts(n);
    for (std::size_t i = 0; i < n; ++i) {
        pts[i][0] = (u_axis == 0) ? polygon[i].x : (u_axis == 1 ? polygon[i].y : polygon[i].z);
        pts[i][1] = (v_axis == 0) ? polygon[i].x : (v_axis == 1 ? polygon[i].y : polygon[i].z);
    }

    // Ear-clipping
    std::vector<int> indices(n);
    std::iota(indices.begin(), indices.end(), 0);

    auto area2 = [&](int i, int j, int k) -> double {
        const auto& a = pts[i];
        const auto& b = pts[j];
        const auto& c = pts[k];
        return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]);
    };

    auto is_convex = [&](int i, int j, int k) -> bool {
        return area2(i, j, k) > 1e-15;
    };

    auto is_ear = [&](int i, int j, int k, const std::vector<int>& list) -> bool {
        if (area2(i, j, k) <= 1e-15) return false;
        for (int idx : list) {
            if (idx == i || idx == j || idx == k) continue;
            // Check if point is inside triangle (i,j,k) using barycentric
            double a = area2(idx, i, j);
            double b = area2(idx, j, k);
            double c = area2(idx, k, i);
            if (a >= -1e-15 && b >= -1e-15 && c >= -1e-15) return false;
        }
        return true;
    };

    while (indices.size() > 3) {
        bool clipped = false;
        std::size_t m = indices.size();
        for (std::size_t i = 0; i < m; ++i) {
            std::size_t j = (i + 1) % m;
            std::size_t k = (i + 2) % m;
            int pi = indices[i], pj = indices[j], pk = indices[k];
            if (is_ear(pi, pj, pk, indices)) {
                out_triangles.push_back({vertex_offset + static_cast<std::size_t>(pi),
                                         vertex_offset + static_cast<std::size_t>(pj),
                                         vertex_offset + static_cast<std::size_t>(pk)});
                indices.erase(indices.begin() + static_cast<std::ptrdiff_t>(j));
                clipped = true;
                break;
            }
        }
        if (!clipped) {
            // Fallback: force-clip the first convex triple
            for (std::size_t i = 0; i < indices.size(); ++i) {
                std::size_t j = (i + 1) % indices.size();
                std::size_t k = (i + 2) % indices.size();
                int pi = indices[i], pj = indices[j], pk = indices[k];
                if (is_convex(pi, pj, pk)) {
                    out_triangles.push_back({vertex_offset + static_cast<std::size_t>(pi),
                                             vertex_offset + static_cast<std::size_t>(pj),
                                             vertex_offset + static_cast<std::size_t>(pk)});
                    indices.erase(indices.begin() + static_cast<std::ptrdiff_t>(j));
                    clipped = true;
                    break;
                }
            }
        }
        if (!clipped) {
            return false;  // Cannot triangulate
        }
    }
    // Final triangle
    out_triangles.push_back({vertex_offset + static_cast<std::size_t>(indices[0]),
                             vertex_offset + static_cast<std::size_t>(indices[1]),
                             vertex_offset + static_cast<std::size_t>(indices[2])});
    return true;
}

// ---------------------------------------------------------------------------
// Möller–Trumbore ray-triangle intersection test
// ---------------------------------------------------------------------------
bool moller_trumbore(const Vec3& orig, const Vec3& dir,
                     const Vec3& v0, const Vec3& v1, const Vec3& v2,
                     double& t_out, double& u_out, double& v_out) {
    constexpr double eps = 1e-12;
    Vec3 e1 = v1 - v0;
    Vec3 e2 = v2 - v0;
    Vec3 pvec = cross(dir, e2);
    double det = dot(e1, pvec);
    if (std::abs(det) < eps) return false;
    double inv_det = 1.0 / det;
    Vec3 tvec = orig - v0;
    double u = dot(tvec, pvec) * inv_det;
    if (u < 0.0 || u > 1.0) return false;
    Vec3 qvec = cross(tvec, e1);
    double v = dot(dir, qvec) * inv_det;
    if (v < 0.0 || u + v > 1.0) return false;
    t_out = dot(e2, qvec) * inv_det;
    u_out = u;
    v_out = v;
    return true;
}

// Check if two triangles intersect (using SAT + Möller-Trumbore for robustness)
bool triangles_intersect(const Vec3& a0, const Vec3& a1, const Vec3& a2,
                         const Vec3& b0, const Vec3& b1, const Vec3& b2) {
    // Quick AABB rejection
    auto overlap = [](double amin, double amax, double bmin, double bmax) {
        return amax >= bmin && bmax >= amin;
    };
    if (!overlap(std::min({a0.x, a1.x, a2.x}), std::max({a0.x, a1.x, a2.x}),
                 std::min({b0.x, b1.x, b2.x}), std::max({b0.x, b1.x, b2.x}))) return false;
    if (!overlap(std::min({a0.y, a1.y, a2.y}), std::max({a0.y, a1.y, a2.y}),
                 std::min({b0.y, b1.y, b2.y}), std::max({b0.y, b1.y, b2.y}))) return false;
    if (!overlap(std::min({a0.z, a1.z, a2.z}), std::max({a0.z, a1.z, a2.z}),
                 std::min({b0.z, b1.z, b2.z}), std::max({b0.z, b1.z, b2.z}))) return false;

    // Triangle A normal
    Vec3 n_a = cross(a1 - a0, a2 - a0);
    double n_a_len = norm(n_a);

    // Check if B vertices are all on same side of triangle A plane
    if (n_a_len > 1e-30) {
        Vec3 n_au = n_a / n_a_len;
        double d0 = dot(n_au, b0 - a0);
        double d1 = dot(n_au, b1 - a0);
        double d2 = dot(n_au, b2 - a0);
        // All on same side -> no intersection
        if ((d0 > 1e-12 && d1 > 1e-12 && d2 > 1e-12) ||
            (d0 < -1e-12 && d1 < -1e-12 && d2 < -1e-12)) {
            // Check coplanar case
            if (std::abs(d0) < 1e-12 && std::abs(d1) < 1e-12 && std::abs(d2) < 1e-12) {
                // Coplanar: do 2D overlap test
                goto edge_test;
            }
            return false;
        }
    }

    // Triangle B normal
    {
        Vec3 n_b = cross(b1 - b0, b2 - b0);
        double n_b_len = norm(n_b);
        if (n_b_len > 1e-30) {
            Vec3 n_bu = n_b / n_b_len;
            double d0 = dot(n_bu, a0 - b0);
            double d1 = dot(n_bu, a1 - b0);
            double d2 = dot(n_bu, a2 - b0);
            if ((d0 > 1e-12 && d1 > 1e-12 && d2 > 1e-12) ||
                (d0 < -1e-12 && d1 < -1e-12 && d2 < -1e-12)) {
                return false;
            }
        }
    }

edge_test:
    // Edge-edge intersection tests: check if any edge of A crosses B
    // Ray from one edge endpoint, direction along edge, intersect with B
    auto edge_intersects_tri = [&](const Vec3& e0, const Vec3& e1, const Vec3& t0, const Vec3& t1, const Vec3& t2) -> bool {
        Vec3 dir = e1 - e0;
        double dir_len = norm(dir);
        if (dir_len < 1e-30) return false;
        dir = dir / dir_len;
        double t, u, v;
        if (!moller_trumbore(e0, dir, t0, t1, t2, t, u, v)) return false;
        return t >= 0.0 && t <= dir_len;
    };

    // Check edges of A against triangle B
    if (edge_intersects_tri(a0, a1, b0, b1, b2)) return true;
    if (edge_intersects_tri(a1, a2, b0, b1, b2)) return true;
    if (edge_intersects_tri(a2, a0, b0, b1, b2)) return true;
    // Check edges of B against triangle A
    if (edge_intersects_tri(b0, b1, a0, a1, a2)) return true;
    if (edge_intersects_tri(b1, b2, a0, a1, a2)) return true;
    if (edge_intersects_tri(b2, b0, a0, a1, a2)) return true;

    return false;
}

// ---------------------------------------------------------------------------
// Edge-adjacency builder
// ---------------------------------------------------------------------------
EdgeMap build_edge_adjacency(const std::vector<std::array<std::size_t, 3>>& faces) {
    EdgeMap emap;
    for (std::size_t fi = 0; fi < faces.size(); ++fi) {
        const auto& f = faces[fi];
        for (int k = 0; k < 3; ++k) {
            std::size_t a = f[k];
            std::size_t b = f[(k + 1) % 3];
            EdgeKey ek = make_edge(a, b);
            auto& info = emap[ek];
            if (info.count < 2) {
                info.face_id[info.count] = static_cast<int>(fi);
            }
            ++info.count;
        }
    }
    return emap;
}

// ---------------------------------------------------------------------------
// Trace boundary cycles from edge adjacency
// ---------------------------------------------------------------------------
std::vector<std::vector<std::size_t>>
trace_boundary_cycles(const LoadedGeometry& geom, const EdgeMap& emap) {
    // Collect all boundary edges (count == 1) and build boundary-vertex adjacency
    std::unordered_map<std::size_t, std::vector<std::size_t>> bdry_adj;
    for (const auto& kv : emap) {
        if (kv.second.count == 1) {
            bdry_adj[kv.first.v0].push_back(kv.first.v1);
            bdry_adj[kv.first.v1].push_back(kv.first.v0);
        }
    }

    std::vector<std::vector<std::size_t>> cycles;
    std::unordered_set<std::size_t> visited;

    for (const auto& kv : bdry_adj) {
        std::size_t start = kv.first;
        if (visited.count(start)) continue;

        // Walk the boundary cycle
        std::vector<std::size_t> cycle;
        std::size_t current = start;
        std::size_t prev = static_cast<std::size_t>(-1);

        while (true) {
            cycle.push_back(current);
            visited.insert(current);
            const auto& neighbors = bdry_adj[current];
            std::size_t next = static_cast<std::size_t>(-1);
            for (auto n : neighbors) {
                if (n != prev) {
                    next = n;
                    break;
                }
            }
            if (next == static_cast<std::size_t>(-1) || next == start) break;
            prev = current;
            current = next;
        }
        if (!cycle.empty()) cycles.push_back(std::move(cycle));
    }
    return cycles;
}

// ---------------------------------------------------------------------------
// Compute polygon area projected along a plane normal
// ---------------------------------------------------------------------------
double polygon_area_projected(const std::vector<Vec3>& polygon, const Vec3& normal) {
    if (polygon.size() < 3) return 0.0;
    // Project polygon area using the normal direction
    Vec3 ref{0, 0, 0};
    // Use first vertex as reference, accumulate area via cross product of adjacent edges
    for (std::size_t i = 1; i < polygon.size() - 1; ++i) {
        Vec3 e1 = polygon[i] - polygon[0];
        Vec3 e2 = polygon[i + 1] - polygon[0];
        Vec3 c = cross(e1, e2);
        ref.x += c.x; ref.y += c.y; ref.z += c.z;
    }
    return std::abs(dot(ref, normal)) * 0.5;
}

// ---------------------------------------------------------------------------
// File format detection helpers
// ---------------------------------------------------------------------------
enum class FileFormat { Unknown, STL, OBJ, PLY, VTK, VTP };

FileFormat detect_format(const std::filesystem::path& path) {
    auto ext = path.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (ext == ".stl")  return FileFormat::STL;
    if (ext == ".obj")  return FileFormat::OBJ;
    if (ext == ".ply")  return FileFormat::PLY;
    if (ext == ".vtk")  return FileFormat::VTK;
    if (ext == ".vtp")  return FileFormat::VTP;
    return FileFormat::Unknown;
}

// ---------------------------------------------------------------------------
// STL parser
// ---------------------------------------------------------------------------
LoadedGeometry load_stl(const std::filesystem::path& path, const GeometryOptions& options) {
    LoadedGeometry geom;
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) {
        geom.health.errors.push_back("Cannot open STL file: " + path.string());
        return geom;
    }

    // Read first 80 bytes header + 4 byte triangle count to detect binary STL
    char header[84] = {0};
    ifs.read(header, 84);
    if (!ifs) {
        geom.health.errors.push_back("Cannot read STL header: " + path.string());
        return geom;
    }

    // Detect binary vs ASCII: check if starts with "solid"
    bool is_ascii = (std::strncmp(header, "solid", 5) == 0);

    ifs.seekg(0, std::ios::beg);

    if (is_ascii) {
        // Probably ASCII (but some binary STLs also start with solid)
        // Try reading a line to see if it looks like ASCII
        std::string first_line;
        std::getline(ifs, first_line);
        // Binary detection heuristic: ASCII STL has facet normal / vertex lines
        std::string second_line;
        std::streampos pos = ifs.tellg();
        std::getline(ifs, second_line);
        ifs.seekg(pos);

        bool looks_binary = true;
        if (first_line.find("facet") != std::string::npos ||
            second_line.find("facet") != std::string::npos) {
            looks_binary = false;
        }

        if (looks_binary) {
            // It's a binary STL that happens to start with "solid"
            is_ascii = false;
            ifs.seekg(0, std::ios::beg);
            ifs.read(header, 84);
        } else {
            ifs.seekg(0, std::ios::beg);
            // Already read first line, seek back
            ifs.clear();
            ifs.seekg(0, std::ios::beg);
        }
    }

    if (!is_ascii) {
        // Binary STL
        char hdr[80] = {0};
        std::memcpy(hdr, header, 80);
        std::uint32_t num_tris;
        std::memcpy(&num_tris, header + 80, 4);

        geom.vertices.reserve(num_tris * 3);
        geom.faces.reserve(num_tris);
        geom.face_patch_ids.reserve(num_tris);

        std::array<char, 50> tri_data;
        std::size_t tri_idx = 0;

        for (std::uint32_t i = 0; i < num_tris; ++i) {
            ifs.read(tri_data.data(), 50);
            if (!ifs) {
                geom.health.warnings.push_back("Unexpected end of binary STL data");
                break;
            }

            // Skip normal (12 bytes)
            float v[9];
            std::memcpy(v, tri_data.data() + 12, 36);
            std::size_t base = geom.vertices.size();
            for (int j = 0; j < 3; ++j) {
                Vec3 p{static_cast<double>(v[j * 3]),
                       static_cast<double>(v[j * 3 + 1]),
                       static_cast<double>(v[j * 3 + 2])};
                if (is_nan_or_inf(p)) {
                    geom.health.warnings.push_back("Skipping NaN/Inf vertex in binary STL");
                    goto skip_stl_tri;
                }
                geom.vertices.push_back(p);
            }
            geom.faces.push_back({base, base + 1, base + 2});
            geom.face_patch_ids.push_back(0);
            ++tri_idx;

        skip_stl_tri:;
        }
    } else {
        // ASCII STL
        std::string line;
        std::size_t tri_idx = 0;
        bool in_facet = false;
        bool in_loop = false;
        Vec3 current_vert;

        while (std::getline(ifs, line)) {
            // Trim
            auto trim = [](std::string& s) {
                s.erase(s.begin(), std::find_if(s.begin(), s.end(),
                    [](unsigned char c) { return !std::isspace(c); }));
                s.erase(std::find_if(s.rbegin(), s.rend(),
                    [](unsigned char c) { return !std::isspace(c); }).base(), s.end());
            };
            trim(line);

            if (line.empty()) continue;

            if (line.substr(0, 6) == "facet ") {
                in_facet = true;
            } else if (line.substr(0, 5) == "outer") {
                in_loop = true;
            } else if (line.substr(0, 6) == "vertex" && in_loop) {
                std::istringstream iss(line);
                std::string tok;
                iss >> tok; // "vertex"
                double vals[3];
                for (int j = 0; j < 3; ++j) {
                    if (!(iss >> vals[j])) {
                        geom.health.warnings.push_back("Malformed vertex in ASCII STL");
                        goto next_stl_line;
                    }
                }
                current_vert = Vec3{vals[0], vals[1], vals[2]};
                if (is_nan_or_inf(current_vert)) {
                    geom.health.warnings.push_back("Skipping NaN/Inf vertex in ASCII STL");
                    goto next_stl_line;
                }
                geom.vertices.push_back(current_vert);
            } else if (line.substr(0, 8) == "endloop") {
                in_loop = false;
            } else if (line.substr(0, 9) == "endfacet") {
                // Should have 3 new vertices added
                std::size_t nv = geom.vertices.size();
                if (nv >= 3) {
                    geom.faces.push_back({nv - 3, nv - 2, nv - 1});
                    geom.face_patch_ids.push_back(0);
                }
                in_facet = false;
            } else if (line.substr(0, 5) == "endsolid") {
                break;
            }
        next_stl_line:;
        }
    }

    geom.health.original_vertices = geom.vertices.size();
    geom.health.original_faces = geom.faces.size();
    geom.patch_names.push_back("default");
    return geom;
}

// ---------------------------------------------------------------------------
// OBJ parser
// ---------------------------------------------------------------------------
LoadedGeometry load_obj(const std::filesystem::path& path, const GeometryOptions& options) {
    LoadedGeometry geom;
    std::ifstream ifs(path);
    if (!ifs) {
        geom.health.errors.push_back("Cannot open OBJ file: " + path.string());
        return geom;
    }

    std::string line;
    std::vector<Vec3> tmp_vertices;
    std::unordered_map<std::string, int> patch_map;
    int next_patch_id = 0;

    // OBJ uses 1-based indexing
    auto add_face = [&](const std::vector<int>& indices, int patch_id) {
        // Convert from 1-based to 0-based
        std::vector<int> idx;
        idx.reserve(indices.size());
        for (int v : indices) {
            if (v > 0 && static_cast<std::size_t>(v) <= tmp_vertices.size()) {
                idx.push_back(v - 1);
            } else {
                geom.health.warnings.push_back("OBJ face index out of range, skipping face");
                return;
            }
        }
        if (idx.size() < 3) return;

        // Triangulate quads (and n-gons via fan triangulation)
        if (idx.size() == 3) {
            geom.faces.push_back({static_cast<std::size_t>(idx[0]),
                                  static_cast<std::size_t>(idx[1]),
                                  static_cast<std::size_t>(idx[2])});
            geom.face_patch_ids.push_back(static_cast<std::size_t>(patch_id));
        } else {
            // Fan triangulation from first vertex
            for (std::size_t i = 1; i + 1 < idx.size(); ++i) {
                geom.faces.push_back({static_cast<std::size_t>(idx[0]),
                                      static_cast<std::size_t>(idx[i]),
                                      static_cast<std::size_t>(idx[i + 1])});
                geom.face_patch_ids.push_back(static_cast<std::size_t>(patch_id));
            }
        }
    };

    while (std::getline(ifs, line)) {
        auto trim = [](std::string& s) {
            s.erase(s.begin(), std::find_if(s.begin(), s.end(),
                [](unsigned char c) { return !std::isspace(c); }));
            s.erase(std::find_if(s.rbegin(), s.rend(),
                [](unsigned char c) { return !std::isspace(c); }).base(), s.end());
        };
        trim(line);
        if (line.empty() || line[0] == '#') continue;

        std::istringstream iss(line);
        std::string type;
        iss >> type;

        if (type == "v") {
            double x, y, z;
            if (iss >> x >> y >> z) {
                Vec3 p{x, y, z};
                if (!is_nan_or_inf(p)) {
                    tmp_vertices.push_back(p);
                } else {
                    geom.health.warnings.push_back("Skipping NaN/Inf vertex in OBJ");
                }
            }
        } else if (type == "vt" || type == "vn") {
            // Skip texture coordinates and normals
        } else if (type == "usemtl" || type == "g") {
            std::string name;
            if (iss >> name) {
                if (patch_map.find(name) == patch_map.end()) {
                    patch_map[name] = next_patch_id++;
                    geom.patch_names.push_back(name);
                }
            }
        } else if (type == "f") {
            int patch_id = 0;
            if (!geom.patch_names.empty()) patch_id = next_patch_id - 1;

            std::vector<int> face_indices;
            std::string token;
            while (iss >> token) {
                // Parse "v/vt/vn" or "v//vn" or just "v"
                auto slash1 = token.find('/');
                if (slash1 != std::string::npos) {
                    auto v_str = token.substr(0, slash1);
                    face_indices.push_back(std::stoi(v_str));
                } else {
                    face_indices.push_back(std::stoi(token));
                }
            }
            add_face(face_indices, patch_id);
        }
    }

    // Copy vertices
    geom.vertices = std::move(tmp_vertices);

    geom.health.original_vertices = geom.vertices.size();
    geom.health.original_faces = geom.faces.size();

    if (geom.patch_names.empty()) geom.patch_names.push_back("default");

    return geom;
}

// ---------------------------------------------------------------------------
// PLY parser (ASCII)
// ---------------------------------------------------------------------------
LoadedGeometry load_ply(const std::filesystem::path& path, const GeometryOptions& options) {
    LoadedGeometry geom;
    std::ifstream ifs(path);
    if (!ifs) {
        geom.health.errors.push_back("Cannot open PLY file: " + path.string());
        return geom;
    }

    std::string line;
    bool is_ascii = false;
    std::size_t vertex_count = 0;
    std::size_t face_count = 0;
    bool header_done = false;
    bool has_nx = false, has_ny = false, has_nz = false;

    // Parse header
    while (std::getline(ifs, line)) {
        auto trim = [](std::string& s) {
            s.erase(s.begin(), std::find_if(s.begin(), s.end(),
                [](unsigned char c) { return !std::isspace(c); }));
            s.erase(std::find_if(s.rbegin(), s.rend(),
                [](unsigned char c) { return !std::isspace(c); }).base(), s.end());
        };
        trim(line);
        if (line.empty()) continue;

        if (line.substr(0, 10) == "format ") {
            is_ascii = (line.find("ascii") != std::string::npos);
            if (!is_ascii) {
                geom.health.errors.push_back("Binary PLY format not supported, only ASCII PLY");
                return geom;
            }
        } else if (line.substr(0, 14) == "element vertex") {
            std::istringstream iss(line);
            std::string tok;
            iss >> tok >> tok; vertex_count = 0;
            iss >> vertex_count;
        } else if (line.substr(0, 12) == "element face") {
            std::istringstream iss(line);
            std::string tok;
            iss >> tok >> tok; face_count = 0;
            iss >> face_count;
        } else if (line.substr(0, 12) == "property float nx") has_nx = true;
        else if (line.substr(0, 12) == "property float ny") has_ny = true;
        else if (line.substr(0, 12) == "property float nz") has_nz = true;
        else if (line.substr(0, 10) == "end_header") {
            header_done = true;
            break;
        }
    }

    if (!header_done) {
        geom.health.errors.push_back("Invalid PLY header");
        return geom;
    }

    geom.vertices.reserve(vertex_count);
    geom.faces.reserve(face_count);
    geom.face_patch_ids.reserve(face_count);

    // Read vertices
    for (std::size_t i = 0; i < vertex_count; ++i) {
        if (!std::getline(ifs, line)) break;
        std::istringstream iss(line);
        double x, y, z;
        if (!(iss >> x >> y >> z)) {
            geom.health.warnings.push_back("Malformed PLY vertex at line " + std::to_string(i));
            continue;
        }
        Vec3 p{x, y, z};
        if (!is_nan_or_inf(p)) {
            geom.vertices.push_back(p);
        }
    }

    // Read faces
    for (std::size_t i = 0; i < face_count; ++i) {
        if (!std::getline(ifs, line)) break;
        std::istringstream iss(line);
        int nverts;
        if (!(iss >> nverts)) continue;
        if (nverts == 3) {
            int v0, v1, v2;
            if (iss >> v0 >> v1 >> v2) {
                geom.faces.push_back({static_cast<std::size_t>(v0),
                                      static_cast<std::size_t>(v1),
                                      static_cast<std::size_t>(v2)});
                geom.face_patch_ids.push_back(0);
            }
        } else if (nverts == 4) {
            int v0, v1, v2, v3;
            if (iss >> v0 >> v1 >> v2 >> v3) {
                geom.faces.push_back({static_cast<std::size_t>(v0),
                                      static_cast<std::size_t>(v1),
                                      static_cast<std::size_t>(v2)});
                geom.faces.push_back({static_cast<std::size_t>(v0),
                                      static_cast<std::size_t>(v2),
                                      static_cast<std::size_t>(v3)});
                geom.face_patch_ids.push_back(0);
                geom.face_patch_ids.push_back(0);
            }
        } else if (nverts > 4) {
            // Fan triangulation
            std::vector<int> verts(nverts);
            for (int j = 0; j < nverts; ++j) {
                if (!(iss >> verts[j])) break;
            }
            for (int j = 1; j + 1 < nverts; ++j) {
                geom.faces.push_back({static_cast<std::size_t>(verts[0]),
                                      static_cast<std::size_t>(verts[j]),
                                      static_cast<std::size_t>(verts[j + 1])});
                geom.face_patch_ids.push_back(0);
            }
        }
    }

    geom.health.original_vertices = geom.vertices.size();
    geom.health.original_faces = geom.faces.size();
    geom.patch_names.push_back("default");
    return geom;
}

// ---------------------------------------------------------------------------
// VTK legacy parser
// ---------------------------------------------------------------------------
LoadedGeometry load_vtk(const std::filesystem::path& path, const GeometryOptions& options) {
    LoadedGeometry geom;
    std::ifstream ifs(path);
    if (!ifs) {
        geom.health.errors.push_back("Cannot open VTK file: " + path.string());
        return geom;
    }

    std::string line;
    // Header line 1: # vtk DataFile Version x.x
    std::getline(ifs, line);
    // Line 2: comment
    std::getline(ifs, line);
    // Line 3: ASCII or BINARY
    std::getline(ifs, line);
    bool is_ascii = (line.find("ASCII") != std::string::npos);
    if (!is_ascii) {
        geom.health.errors.push_back("Binary VTK not supported, only ASCII VTK legacy");
        return geom;
    }

    std::unordered_map<std::string, int> patch_map;
    int next_patch_id = 0;

    auto read_data_section = [&]() -> bool {
        // Read until we find DATASET
        while (std::getline(ifs, line)) {
            auto trim = [](std::string& s) {
                s.erase(s.begin(), std::find_if(s.begin(), s.end(),
                    [](unsigned char c) { return !std::isspace(c); }));
                s.erase(std::find_if(s.rbegin(), s.rend(),
                    [](unsigned char c) { return !std::isspace(c); }).base(), s.end());
            };
            trim(line);
            if (line.empty()) continue;

            if (line.substr(0, 8) == "DATASET ") {
                std::string dataset_type = line.substr(8);
                if (dataset_type.find("POLYDATA") != std::string::npos) {
                    // Parse POLYDATA
                    while (std::getline(ifs, line)) {
                        trim(line);
                        if (line.empty()) continue;

                        if (line.substr(0, 7) == "POINTS ") {
                            std::istringstream iss(line);
                            std::string tok;
                            iss >> tok; // POINTS
                            std::size_t npts = 0;
                            iss >> npts;
                            std::string type;
                            iss >> type;

                            geom.vertices.reserve(npts);
                            for (std::size_t i = 0; i < npts; ++i) {
                                if (!std::getline(ifs, line)) break;
                                std::istringstream lis(line);
                                double x, y, z;
                                if (lis >> x >> y >> z) {
                                    Vec3 p{x, y, z};
                                    if (!is_nan_or_inf(p)) geom.vertices.push_back(p);
                                }
                            }
                        } else if (line.substr(0, 10) == "POLYGONS ") {
                            std::istringstream iss(line);
                            std::string tok;
                            iss >> tok; // POLYGONS
                            std::size_t npolys = 0, nvals = 0;
                            iss >> npolys >> nvals;

                            geom.faces.reserve(npolys);
                            geom.face_patch_ids.reserve(npolys);
                            for (std::size_t i = 0; i < npolys; ++i) {
                                if (!std::getline(ifs, line)) break;
                                std::istringstream lis(line);
                                int nv;
                                lis >> nv;
                                if (nv == 3) {
                                    std::size_t v0, v1, v2;
                                    if (lis >> v0 >> v1 >> v2) {
                                        geom.faces.push_back({v0, v1, v2});
                                        geom.face_patch_ids.push_back(0);
                                    }
                                } else if (nv == 4) {
                                    std::size_t v0, v1, v2, v3;
                                    if (lis >> v0 >> v1 >> v2 >> v3) {
                                        geom.faces.push_back({v0, v1, v2});
                                        geom.faces.push_back({v0, v2, v3});
                                        geom.face_patch_ids.push_back(0);
                                        geom.face_patch_ids.push_back(0);
                                    }
                                } else {
                                    std::vector<int> verts(nv);
                                    for (int j = 0; j < nv; ++j) lis >> verts[j];
                                    for (int j = 1; j + 1 < nv; ++j) {
                                        geom.faces.push_back({static_cast<std::size_t>(verts[0]),
                                                              static_cast<std::size_t>(verts[j]),
                                                              static_cast<std::size_t>(verts[j + 1])});
                                        geom.face_patch_ids.push_back(0);
                                    }
                                }
                            }
                        } else if (line.substr(0, 12) == "TRIANGLE_STRIPS") {
                            // Skip for now
                        }
                    }
                    return true;
                } else if (dataset_type.find("UNSTRUCTURED_GRID") != std::string::npos) {
                    while (std::getline(ifs, line)) {
                        trim(line);
                        if (line.empty()) continue;

                        if (line.substr(0, 7) == "POINTS ") {
                            std::istringstream iss(line);
                            std::string tok;
                            iss >> tok;
                            std::size_t npts = 0;
                            iss >> npts;
                            std::string type;
                            iss >> type;

                            geom.vertices.reserve(npts);
                            for (std::size_t i = 0; i < npts; ++i) {
                                if (!std::getline(ifs, line)) break;
                                std::istringstream lis(line);
                                double x, y, z;
                                if (lis >> x >> y >> z) {
                                    geom.vertices.push_back({x, y, z});
                                }
                            }
                        } else if (line.substr(0, 12) == "CELL_TYPES ") {
                            // Already handled by CELLS
                        } else if (line.substr(0, 6) == "CELLS ") {
                            std::istringstream iss(line);
                            std::string tok;
                            iss >> tok;
                            std::size_t ncells = 0, nvals = 0;
                            iss >> ncells >> nvals;

                            for (std::size_t i = 0; i < ncells; ++i) {
                                if (!std::getline(ifs, line)) break;
                                std::istringstream lis(line);
                                int nv;
                                lis >> nv;
                                if (nv == 3) {
                                    std::size_t v0, v1, v2;
                                    if (lis >> v0 >> v1 >> v2) {
                                        geom.faces.push_back({v0, v1, v2});
                                        geom.face_patch_ids.push_back(0);
                                    }
                                } else if (nv == 4) {
                                    std::size_t v0, v1, v2, v3;
                                    if (lis >> v0 >> v1 >> v2 >> v3) {
                                        geom.faces.push_back({v0, v1, v2});
                                        geom.faces.push_back({v0, v2, v3});
                                        geom.face_patch_ids.push_back(0);
                                        geom.face_patch_ids.push_back(0);
                                    }
                                } else {
                                    std::vector<int> verts(nv);
                                    for (int j = 0; j < nv; ++j) lis >> verts[j];
                                    for (int j = 1; j + 1 < nv; ++j) {
                                        geom.faces.push_back({static_cast<std::size_t>(verts[0]),
                                                              static_cast<std::size_t>(verts[j]),
                                                              static_cast<std::size_t>(verts[j + 1])});
                                        geom.face_patch_ids.push_back(0);
                                    }
                                }
                            }
                        } else if (line.substr(0, 10) == "CELL_DATA ") {
                            // Read cell-based scalars potentially for patches
                        }
                    }
                    return true;
                }
            }
        }
        return false;
    };

    read_data_section();

    geom.health.original_vertices = geom.vertices.size();
    geom.health.original_faces = geom.faces.size();
    geom.patch_names.push_back("default");
    return geom;
}

} // anonymous namespace

// ============================================================================
// Public API implementation
// ============================================================================

LoadedGeometry loadGeometry(const std::filesystem::path& path, const GeometryOptions& options) {
    LoadedGeometry geom;
    auto fmt = detect_format(path);
    if (fmt == FileFormat::Unknown) {
        geom.health.errors.push_back("Unsupported file format: " + path.extension().string());
        return geom;
    }

    try {
        switch (fmt) {
        case FileFormat::STL:
            geom = load_stl(path, options);
            break;
        case FileFormat::OBJ:
            geom = load_obj(path, options);
            break;
        case FileFormat::PLY:
            geom = load_ply(path, options);
            break;
        case FileFormat::VTK:
            geom = load_vtk(path, options);
            break;
        default:
            geom.health.errors.push_back("Format not implemented: " + path.extension().string());
            return geom;
        }
    } catch (const std::exception& e) {
        geom.health.errors.push_back(std::string("Exception during load: ") + e.what());
        return geom;
    }

    if (geom.vertices.empty()) {
        geom.health.warnings.push_back("No vertices loaded from file");
    }
    if (geom.faces.empty()) {
        geom.health.warnings.push_back("No faces loaded from file");
    }

    // Run repair pass
    return repairGeometry(std::move(geom), options);
}

LoadedGeometry repairGeometry(LoadedGeometry&& geom, const GeometryOptions& options) {
    if (geom.vertices.empty() || geom.faces.empty()) {
        geom.health.warnings.push_back("Empty geometry, skipping repair");
        return std::move(geom);
    }

    // 1. Merge duplicates
    mergeDuplicateVertices(geom, options.mergeTolerance);

    // 2. Remove degenerates
    removeDegenerateFaces(geom);

    // 3. Orient normals
    if (options.orientNormals) {
        orientNormalsConsistently(geom);
        geom.health.normals_consistent = true;
    }

    // 4. Fill holes
    if (options.fillSmallHoles) {
        double max_area = options.maxHoleArea;
        if (max_area < 0) {
            // Auto: 0.1% of bounding box diagonal squared (approx area scale)
            auto bbox = computeBBox(geom);
            double diag = bbox.diagonal();
            max_area = 0.001 * diag * diag;
        }
        fillSmallHoles(geom, max_area);
    }

    // 5. Self-intersection detection
    if (options.repairSelfIntersections) {
        geom.health.self_intersections = detectSelfIntersections(geom);
        if (geom.health.self_intersections > 0) {
            geom.health.warnings.push_back(
                "Detected " + std::to_string(geom.health.self_intersections) +
                " self-intersecting triangle pairs (not repaired automatically)");
        }
    }

    // 6. Final analysis
    geom.health.final_vertices = geom.vertices.size();
    geom.health.final_faces = geom.faces.size();
    geom.health.bbox = computeBBox(geom);
    geom.health.is_watertight = isWatertight(geom);
    geom.health.is_manifold = isManifold(geom);

    if (!geom.health.is_watertight) {
        geom.health.warnings.push_back("Mesh is not watertight (has boundary edges)");
    }
    if (!geom.health.is_manifold) {
        geom.health.warnings.push_back("Mesh is not manifold (has non-manifold edges)");
    }

    return std::move(geom);
}

void mergeDuplicateVertices(LoadedGeometry& geom, double tolerance) {
    if (geom.vertices.empty()) return;

    // Compute bounding box for adaptive cell size
    BoundingBox bbox;
    for (const auto& v : geom.vertices) bbox.expand(v);
    double diag = bbox.diagonal();
    double cell_size = std::max(tolerance * 10.0, diag * 1e-8);

    SpatialHashGrid grid(cell_size);
    std::vector<std::size_t> remap(geom.vertices.size());
    std::vector<Vec3> merged;
    std::size_t removed = 0;

    for (std::size_t i = 0; i < geom.vertices.size(); ++i) {
        const Vec3& p = geom.vertices[i];
        if (is_nan_or_inf(p)) {
            // Fix NaN/Inf vertices to origin (or skip)
            geom.health.warnings.push_back("Found NaN/Inf vertex at index " + std::to_string(i));
            Vec3 fixed = {0, 0, 0};
            int nearby = grid.find_nearby(fixed, tolerance);
            if (nearby >= 0) {
                remap[i] = static_cast<std::size_t>(nearby);
                ++removed;
            } else {
                std::size_t new_idx = merged.size();
                merged.push_back(fixed);
                grid.insert(new_idx, fixed);
                remap[i] = new_idx;
            }
            continue;
        }

        int nearby = grid.find_nearby(p, tolerance);
        if (nearby >= 0) {
            remap[i] = static_cast<std::size_t>(nearby);
            ++removed;
        } else {
            std::size_t new_idx = merged.size();
            merged.push_back(p);
            grid.insert(new_idx, p);
            remap[i] = new_idx;
        }
    }

    // Remap face indices
    for (auto& tri : geom.faces) {
        for (int j = 0; j < 3; ++j) {
            if (tri[j] < remap.size()) {
                tri[j] = remap[tri[j]];
            }
        }
    }

    geom.vertices = std::move(merged);
    geom.health.duplicate_vertices_removed = removed;
}

void removeDegenerateFaces(LoadedGeometry& geom) {
    if (geom.faces.empty()) return;

    std::size_t removed = 0;
    std::vector<std::array<std::size_t, 3>> good_faces;
    std::vector<std::size_t> good_patch_ids;
    good_faces.reserve(geom.faces.size());
    good_patch_ids.reserve(geom.face_patch_ids.size());

    for (std::size_t i = 0; i < geom.faces.size(); ++i) {
        const auto& tri = geom.faces[i];
        if (tri[0] >= geom.vertices.size() || tri[1] >= geom.vertices.size() || tri[2] >= geom.vertices.size()) {
            geom.health.warnings.push_back("Face " + std::to_string(i) + " references invalid vertex");
            ++removed;
            continue;
        }

        const Vec3& a = geom.vertices[tri[0]];
        const Vec3& b = geom.vertices[tri[1]];
        const Vec3& c = geom.vertices[tri[2]];

        // Check for degenerate vertices (zero-length edges)
        Vec3 e1 = b - a;
        Vec3 e2 = c - a;
        Vec3 cr = cross(e1, e2);
        double area2 = dot(cr, cr);
        double area = std::sqrt(area2) * 0.5;

        if (area < 1e-30 || !std::isfinite(area)) {
            ++removed;
            continue;
        }

        good_faces.push_back(tri);
        good_patch_ids.push_back(
            i < geom.face_patch_ids.size() ? geom.face_patch_ids[i] : 0);
    }

    std::size_t removed_count = geom.faces.size() - good_faces.size();
    geom.faces = std::move(good_faces);
    geom.face_patch_ids = std::move(good_patch_ids);
    geom.health.degenerate_faces_removed = removed;
}

void orientNormalsConsistently(LoadedGeometry& geom) {
    if (geom.faces.empty()) return;

    auto emap = build_edge_adjacency(geom.faces);

    // Build face adjacency from edge map
    std::vector<std::vector<int>> face_adj(geom.faces.size());
    for (const auto& kv : emap) {
        const auto& info = kv.second;
        if (info.count >= 2) {
            int f0 = info.face_id[0];
            int f1 = info.face_id[1];
            if (f0 >= 0 && f1 >= 0) {
                face_adj[f0].push_back(f1);
                face_adj[f1].push_back(f0);
            }
        }
    }

    std::vector<int> visited(geom.faces.size(), 0); // 0=unvisited, 1=correct, -1=flip

    auto flip_face = [](std::array<std::size_t, 3>& tri) {
        std::swap(tri[1], tri[2]);
    };

    auto compute_face_normal = [&](const std::array<std::size_t, 3>& tri) -> Vec3 {
        if (tri[0] >= geom.vertices.size() || tri[1] >= geom.vertices.size() || tri[2] >= geom.vertices.size())
            return {0,0,0};
        const Vec3& a = geom.vertices[tri[0]];
        const Vec3& b = geom.vertices[tri[1]];
        const Vec3& c = geom.vertices[tri[2]];
        Vec3 cr = cross(b - a, c - a);
        double n = norm(cr);
        return n > 1e-30 ? cr / n : Vec3{0,0,0};
    };

    // BFS per connected component
    for (std::size_t start = 0; start < geom.faces.size(); ++start) {
        if (visited[start] != 0) continue;
        visited[start] = 1;

        std::queue<int> q;
        q.push(static_cast<int>(start));

        while (!q.empty()) {
            int fi = q.front(); q.pop();
            const auto& tri = geom.faces[fi];

            for (int nb : face_adj[fi]) {
                if (visited[nb] != 0) continue;

                // Check winding consistency: shared edge should have opposite vertex order
                // Find common edge
                const auto& nb_tri = geom.faces[nb];
                std::size_t common[2];
                int cc = 0;
                for (int k = 0; k < 3 && cc < 2; ++k) {
                    for (int l = 0; l < 3 && cc < 2; ++l) {
                        if (tri[k] == nb_tri[l]) {
                            common[cc++] = tri[k];
                            break;
                        }
                    }
                }

                if (cc == 2) {
                    // In the current face, the edge direction from common[0] to common[1]
                    // For correct winding, the neighboring face should have common[1]->common[0]
                    // Find the edge direction in face fi
                    int fi_dir = 0; // 0 = unknown, 1 = forward, -1 = reverse
                    for (int k = 0; k < 3; ++k) {
                        if (tri[k] == common[0] && tri[(k+1)%3] == common[1]) { fi_dir = 1; break; }
                        if (tri[k] == common[1] && tri[(k+1)%3] == common[0]) { fi_dir = -1; break; }
                    }

                    int nb_dir = 0;
                    for (int k = 0; k < 3; ++k) {
                        if (nb_tri[k] == common[0] && nb_tri[(k+1)%3] == common[1]) { nb_dir = 1; break; }
                        if (nb_tri[k] == common[1] && nb_tri[(k+1)%3] == common[0]) { nb_dir = -1; break; }
                    }

                    // Correct orientation: fi_dir and nb_dir should be opposite
                    if (fi_dir * nb_dir > 0) {
                        // Same direction -> need to flip neighbor
                        visited[nb] = -visited[fi];
                    } else {
                        visited[nb] = visited[fi];
                    }
                } else {
                    // Degenerate edge, just propagate same orientation
                    visited[nb] = visited[fi];
                }

                q.push(nb);
            }
        }
    }

    // Apply flips
    for (std::size_t i = 0; i < geom.faces.size(); ++i) {
        if (visited[i] < 0) {
            flip_face(geom.faces[i]);
        }
    }

    // For closed components, ensure outward normals
    // Compute total signed volume to determine inward/outward
    auto compute_signed_volume = [&](const std::vector<std::size_t>& component_faces) -> double {
        double vol = 0.0;
        Vec3 ref{0, 0, 0};
        for (auto fi : component_faces) {
            const auto& tri = geom.faces[fi];
            if (tri[0] >= geom.vertices.size() || tri[1] >= geom.vertices.size() || tri[2] >= geom.vertices.size())
                continue;
            const Vec3& a = geom.vertices[tri[0]];
            const Vec3& b = geom.vertices[tri[1]];
            const Vec3& c = geom.vertices[tri[2]];
            // Tetrahedron volume w.r.t. origin
            Vec3 cr = cross(b - ref, c - ref);
            vol += dot(a - ref, cr);
        }
        return vol / 6.0;
    };

    // Find connected components
    std::vector<std::vector<std::size_t>> components;
    std::vector<int> comp_visited(geom.faces.size(), 0);
    for (std::size_t i = 0; i < geom.faces.size(); ++i) {
        if (comp_visited[i]) continue;
        std::vector<std::size_t> comp;
        std::queue<std::size_t> q;
        q.push(i);
        comp_visited[i] = 1;
        while (!q.empty()) {
            std::size_t fi = q.front(); q.pop();
            comp.push_back(fi);
            for (int nb : face_adj[fi]) {
                if (!comp_visited[nb]) {
                    comp_visited[nb] = 1;
                    q.push(static_cast<std::size_t>(nb));
                }
            }
        }
        components.push_back(std::move(comp));
    }

    // Check each closed component
    for (const auto& comp : components) {
        // Check if component is closed (all edges have 2 faces within component)
        bool closed = true;
        for (auto fi : comp) {
            const auto& tri = geom.faces[fi];
            for (int k = 0; k < 3; ++k) {
                auto ek = make_edge(tri[k], tri[(k+1)%3]);
                auto it = emap.find(ek);
                if (it == emap.end() || it->second.count != 2) {
                    closed = false;
                    goto check_outward_done;
                }
            }
        }
    check_outward_done:
        if (closed) {
            double sv = compute_signed_volume(comp);
            // If signed volume is positive, normals point inward (for typical CCW outward convention)
            // Positive signed volume with our winding means inward normals -> flip all
            if (sv > 0) {
                for (auto fi : comp) {
                    flip_face(geom.faces[fi]);
                }
            }
        }
    }
}

void fillSmallHoles(LoadedGeometry& geom, double max_hole_area) {
    if (geom.faces.empty()) return;

    auto emap = build_edge_adjacency(geom.faces);
    auto cycles = trace_boundary_cycles(geom, emap);
    if (cycles.empty()) return;

    std::size_t filled = 0;

    for (const auto& cycle : cycles) {
        if (cycle.size() < 3) continue;

        // Collect polygon vertices
        std::vector<Vec3> polygon;
        polygon.reserve(cycle.size());
        for (auto vi : cycle) {
            if (vi < geom.vertices.size()) {
                polygon.push_back(geom.vertices[vi]);
            }
        }

        if (polygon.size() < 3) continue;

        // Compute approximate area (projected along best-fit plane)
        Vec3 normal{0, 0, 0};
        for (std::size_t i = 0; i < polygon.size(); ++i) {
            const Vec3& a = polygon[i];
            const Vec3& b = polygon[(i + 1) % polygon.size()];
            normal.x += (a.y - b.y) * (a.z + b.z);
            normal.y += (a.z - b.z) * (a.x + b.x);
            normal.z += (a.x - b.x) * (a.y + b.y);
        }
        double nlen = norm(normal);
        if (nlen < 1e-30) continue;
        normal = normal / nlen;

        double area = polygon_area_projected(polygon, normal);
        if (area > max_hole_area) continue;

        // Fill the hole using ear-clipping
        std::size_t vert_offset = geom.vertices.size();

        // We need to add new vertices for the hole fill
        // Actually, the vertices already exist in geom.vertices — we just need to add triangles
        // Map cycle vertex indices to the polygons
        // The polygon uses geom.vertices indices directly

        // Create a temporary polygon with actual indices
        std::vector<Vec3> hole_verts;
        std::vector<std::size_t> hole_indices;
        hole_verts.reserve(cycle.size());
        hole_indices.reserve(cycle.size());
        for (auto vi : cycle) {
            if (vi < geom.vertices.size()) {
                hole_verts.push_back(geom.vertices[vi]);
                hole_indices.push_back(vi);
            }
        }

        if (hole_verts.size() < 3) continue;

        // Triangulate using ear-clipping on the hole polygon
        std::vector<std::array<std::size_t, 3>> new_tris;

        // Build 2D projection
        Vec3 h_normal{0, 0, 0};
        for (std::size_t i = 0; i < hole_verts.size(); ++i) {
            const Vec3& a = hole_verts[i];
            const Vec3& b = hole_verts[(i + 1) % hole_verts.size()];
            h_normal.x += (a.y - b.y) * (a.z + b.z);
            h_normal.y += (a.z - b.z) * (a.x + b.x);
            h_normal.z += (a.x - b.x) * (a.y + b.y);
        }
        double hn = norm(h_normal);
        if (hn < 1e-30) continue;
        h_normal = h_normal / hn;

        int u_axis = 0, v_axis = 1;
        {
            double ax = std::abs(h_normal.x), ay = std::abs(h_normal.y), az = std::abs(h_normal.z);
            if (ax > ay && ax > az) { u_axis = 1; v_axis = 2; }
            else if (ay > az)       { u_axis = 0; v_axis = 2; }
            else                    { u_axis = 0; v_axis = 1; }
        }

        std::vector<std::array<double, 2>> pts(hole_verts.size());
        for (std::size_t i = 0; i < hole_verts.size(); ++i) {
            pts[i][0] = (u_axis == 0) ? hole_verts[i].x : (u_axis == 1 ? hole_verts[i].y : hole_verts[i].z);
            pts[i][1] = (v_axis == 0) ? hole_verts[i].x : (v_axis == 1 ? hole_verts[i].y : hole_verts[i].z);
        }

        // Ear-clipping
        std::vector<int> indices(hole_verts.size());
        std::iota(indices.begin(), indices.end(), 0);

        auto area2d = [&](int i, int j, int k) -> double {
            const auto& a = pts[i];
            const auto& b = pts[j];
            const auto& c = pts[k];
            return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]);
        };

        auto is_convex_h = [&](int i, int j, int k) -> bool {
            return area2d(i, j, k) > 1e-15;
        };

        auto is_ear_h = [&](int i, int j, int k, const std::vector<int>& list) -> bool {
            if (area2d(i, j, k) <= 1e-15) return false;
            for (int idx : list) {
                if (idx == i || idx == j || idx == k) continue;
                double a = area2d(idx, i, j);
                double b = area2d(idx, j, k);
                double c = area2d(idx, k, i);
                if (a >= -1e-15 && b >= -1e-15 && c >= -1e-15) return false;
            }
            return true;
        };

        bool success = true;
        while (indices.size() > 3) {
            bool clipped = false;
            std::size_t m = indices.size();
            for (std::size_t i = 0; i < m; ++i) {
                std::size_t j = (i + 1) % m;
                std::size_t k = (i + 2) % m;
                int pi = indices[i], pj = indices[j], pk = indices[k];
                if (is_ear_h(pi, pj, pk, indices)) {
                    new_tris.push_back({hole_indices[pi], hole_indices[pj], hole_indices[pk]});
                    indices.erase(indices.begin() + static_cast<std::ptrdiff_t>(j));
                    clipped = true;
                    break;
                }
            }
            if (!clipped) {
                // Force clip first convex
                for (std::size_t i = 0; i < indices.size(); ++i) {
                    std::size_t j = (i + 1) % indices.size();
                    std::size_t k = (i + 2) % indices.size();
                    int pi = indices[i], pj = indices[j], pk = indices[k];
                    if (is_convex_h(pi, pj, pk)) {
                        new_tris.push_back({hole_indices[pi], hole_indices[pj], hole_indices[pk]});
                        indices.erase(indices.begin() + static_cast<std::ptrdiff_t>(j));
                        clipped = true;
                        break;
                    }
                }
            }
            if (!clipped) {
                success = false;
                break;
            }
        }
        if (success && indices.size() == 3) {
            new_tris.push_back({hole_indices[indices[0]],
                                hole_indices[indices[1]],
                                hole_indices[indices[2]]});
        }

        if (success && !new_tris.empty()) {
            // Orient new triangles consistently with surrounding mesh
            // Find a neighboring face to determine orientation
            int neighbor_fi = -1;
            for (const auto& kv : emap) {
                if (kv.second.count == 1) {
                    auto e0 = kv.first.v0;
                    auto e1 = kv.first.v1;
                    // Check if this edge is part of the hole boundary
                    bool in_hole = false;
                    for (auto vi : cycle) {
                        if (vi == e0 || vi == e1) { in_hole = true; break; }
                    }
                    if (in_hole) {
                        neighbor_fi = kv.second.face_id[0];
                        break;
                    }
                }
            }

            // Compute normal of boundary edge's face
            Vec3 ref_normal{0, 0, 1};
            if (neighbor_fi >= 0) {
                const auto& ntri = geom.faces[neighbor_fi];
                const Vec3& na = geom.vertices[ntri[0]];
                const Vec3& nb = geom.vertices[ntri[1]];
                const Vec3& nc = geom.vertices[ntri[2]];
                Vec3 cr = cross(nb - na, nc - na);
                double nn = norm(cr);
                if (nn > 1e-30) ref_normal = cr / nn;
            }

            // Compute normal of first new triangle
            const auto& ft = new_tris[0];
            const Vec3& fa = geom.vertices[ft[0]];
            const Vec3& fb = geom.vertices[ft[1]];
            const Vec3& fc = geom.vertices[ft[2]];
            Vec3 ft_normal = cross(fb - fa, fc - fa);
            double ftn = norm(ft_normal);
            if (ftn > 1e-30) {
                ft_normal = ft_normal / ftn;
                // If triangle normal points opposite to reference, flip all new triangles
                if (dot(ft_normal, ref_normal) < 0) {
                    for (auto& nt : new_tris) {
                        std::swap(nt[1], nt[2]);
                    }
                }
            }

            // Add new triangles
            std::size_t patch_id = 0;
            if (!geom.face_patch_ids.empty()) {
                patch_id = geom.face_patch_ids.back();
            }
            for (const auto& nt : new_tris) {
                geom.faces.push_back(nt);
                geom.face_patch_ids.push_back(patch_id);
            }

            ++filled;
        }
    }

    geom.health.holes_filled = filled;
}

std::size_t detectSelfIntersections(const LoadedGeometry& geom) {
    if (geom.faces.size() < 2) return 0;

    // Build spatial hash for triangle culling
    BoundingBox bbox;
    for (const auto& v : geom.vertices) bbox.expand(v);
    double diag = bbox.diagonal();
    double cell_size = diag / std::max(1.0, std::cbrt(static_cast<double>(geom.vertices.size())));

    SpatialHashGrid grid(cell_size);
    for (std::size_t i = 0; i < geom.vertices.size(); ++i) {
        grid.insert(i, geom.vertices[i]);
    }

    std::size_t count = 0;
    // Only check non-adjacent triangles (no shared vertex)
    // Build adjacency
    EdgeMap emap;
    for (std::size_t fi = 0; fi < geom.faces.size(); ++fi) {
        const auto& tri = geom.faces[fi];
        for (int k = 0; k < 3; ++k) {
            auto ek = make_edge(tri[k], tri[(k+1)%3]);
            auto& info = emap[ek];
            if (info.count < 2) info.face_id[info.count] = static_cast<int>(fi);
            ++info.count;
        }
    }

    // For each triangle, find potentially intersecting triangles via spatial hash
    std::unordered_set<std::size_t> checked_pairs;

    for (std::size_t fi = 0; fi < geom.faces.size(); ++fi) {
        const auto& tri = geom.faces[fi];
        const Vec3& a = geom.vertices[tri[0]];
        const Vec3& b = geom.vertices[tri[1]];
        const Vec3& c = geom.vertices[tri[2]];

        auto candidates = grid.query_triangle(a, b, c);
        std::unordered_set<std::size_t> candidate_faces;

        for (auto vi : candidates) {
            // Find all faces that use this vertex
            for (std::size_t fj = 0; fj < geom.faces.size(); ++fj) {
                if (fj == fi) continue;
                const auto& t2 = geom.faces[fj];
                if (t2[0] == vi || t2[1] == vi || t2[2] == vi) {
                    // Check that they don't share an edge
                    bool shares_edge = false;
                    for (int k = 0; k < 3; ++k) {
                        auto ek = make_edge(tri[k], tri[(k+1)%3]);
                        for (int l = 0; l < 3; ++l) {
                            auto ek2 = make_edge(t2[l], t2[(l+1)%3]);
                            if (ek.v0 == ek2.v0 && ek.v1 == ek2.v1) {
                                shares_edge = true;
                                break;
                            }
                        }
                        if (shares_edge) break;
                    }
                    if (!shares_edge) {
                        std::size_t pair_id = fi < fj ? (fi * geom.faces.size() + fj) : (fj * geom.faces.size() + fi);
                        if (checked_pairs.insert(pair_id).second) {
                            candidate_faces.insert(fj);
                        }
                    }
                }
            }
        }

        for (auto fj : candidate_faces) {
            const auto& t2 = geom.faces[fj];
            const Vec3& d = geom.vertices[t2[0]];
            const Vec3& e = geom.vertices[t2[1]];
            const Vec3& f = geom.vertices[t2[2]];
            if (triangles_intersect(a, b, c, d, e, f)) {
                ++count;
            }
        }
    }

    return count;
}

bool isWatertight(const LoadedGeometry& geom) {
    if (geom.faces.empty()) return false;

    auto emap = build_edge_adjacency(geom.faces);

    // Every edge must have exactly 2 incident faces
    for (const auto& kv : emap) {
        if (kv.second.count != 2) return false;
    }
    return true;
}

bool isManifold(const LoadedGeometry& geom) {
    if (geom.faces.empty()) return true;

    auto emap = build_edge_adjacency(geom.faces);

    // Every edge must have at most 2 incident faces
    for (const auto& kv : emap) {
        if (kv.second.count > 2) return false;
    }

    // Check vertex neighborhoods for bow-tie configurations
    // Build vertex-to-face adjacency
    std::vector<std::vector<std::size_t>> vert_faces(geom.vertices.size());
    for (std::size_t fi = 0; fi < geom.faces.size(); ++fi) {
        const auto& tri = geom.faces[fi];
        for (int k = 0; k < 3; ++k) {
            if (tri[k] < vert_faces.size()) {
                vert_faces[tri[k]].push_back(fi);
            }
        }
    }

    // For each vertex, check that its incident faces form a single fan
    for (std::size_t vi = 0; vi < geom.vertices.size(); ++vi) {
        const auto& faces = vert_faces[vi];
        if (faces.size() <= 2) continue;

        // Build adjacency among faces sharing this vertex
        // Two faces are adjacent if they share an edge containing vi
        std::unordered_map<std::size_t, std::vector<std::size_t>> adj;
        for (std::size_t i = 0; i < faces.size(); ++i) {
            for (std::size_t j = i + 1; j < faces.size(); ++j) {
                const auto& t1 = geom.faces[faces[i]];
                const auto& t2 = geom.faces[faces[j]];
                // Check if they share an edge: find vertex opposite to vi in each triangle
                auto get_opposite_edge = [&](const std::array<std::size_t, 3>& tri, std::size_t v) -> std::pair<std::size_t, std::size_t> {
                    for (int k = 0; k < 3; ++k) {
                        if (tri[k] == v) {
                            return {tri[(k+1)%3], tri[(k+2)%3]};
                        }
                    }
                    return {static_cast<std::size_t>(-1), static_cast<std::size_t>(-1)};
                };
                auto e1 = get_opposite_edge(t1, vi);
                auto e2 = get_opposite_edge(t2, vi);
                // They are adjacent if e1 == e2 (same opposite edge)
                // Not directly, but they share the vertex and are ordered around it
                // Instead, check if they share another vertex (the edge opposite vi in one face
                // is present in the other)
                bool share_other = false;
                for (int k = 0; k < 3; ++k) {
                    if (t2[k] != vi && (t2[k] == e1.first || t2[k] == e1.second)) {
                        share_other = true;
                        break;
                    }
                }
                if (share_other) {
                    adj[i].push_back(j);
                    adj[j].push_back(i);
                }
            }
        }

        // Check that the face adjacency graph is connected (single fan)
        if (faces.empty()) continue;
        std::queue<std::size_t> q;
        std::vector<bool> visited(faces.size(), false);
        q.push(0);
        visited[0] = true;
        std::size_t visited_count = 1;
        while (!q.empty()) {
            std::size_t cur = q.front(); q.pop();
            for (auto nb : adj[cur]) {
                if (!visited[nb]) {
                    visited[nb] = true;
                    ++visited_count;
                    q.push(nb);
                }
            }
        }
        if (visited_count != faces.size()) {
            // Bow-tie configuration (non-manifold vertex)
            geom.health.non_manifold_edges++;
            return false;
        }
    }

    auto cycles = trace_boundary_cycles(geom, emap);
    geom.health.non_manifold_edges = 0;
    for (const auto& kv : emap) {
        if (kv.second.count > 2) ++geom.health.non_manifold_edges;
    }

    return cycles.empty();
}

BoundingBox computeBBox(const LoadedGeometry& geom) {
    BoundingBox bbox;
    for (const auto& v : geom.vertices) {
        bbox.expand(v);
    }
    return bbox;
}

std::string detectUnits(const BoundingBox& bbox) {
    if (bbox.empty()) return "m";
    double d = bbox.maxDim();
    if (d < 0.01) return "mm";
    if (d < 0.1)  return "cm";
    if (d < 1.0)  return "m";
    if (d < 100)  return "in";
    return "m";
}

void scaleToMeters(LoadedGeometry& geom, const std::string& from_unit) {
    double factor = 1.0;
    std::string unit = from_unit;
    std::transform(unit.begin(), unit.end(), unit.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });

    if (unit == "mm" || unit == "millimeter" || unit == "millimeters") {
        factor = 0.001;
    } else if (unit == "cm" || unit == "centimeter" || unit == "centimeters") {
        factor = 0.01;
    } else if (unit == "in" || unit == "inch" || unit == "inches") {
        factor = 0.0254;
    } else if (unit == "ft" || unit == "foot" || unit == "feet") {
        factor = 0.3048;
    } else if (unit == "m" || unit == "meter" || unit == "meters") {
        factor = 1.0;
    } else {
        geom.health.warnings.push_back("Unknown unit '" + from_unit + "', assuming meters");
        return;
    }

    if (factor != 1.0) {
        for (auto& v : geom.vertices) {
            v.x *= factor;
            v.y *= factor;
            v.z *= factor;
        }
    }

    // Update bounding box
    geom.health.bbox = computeBBox(geom);
}

} // namespace autopoly
