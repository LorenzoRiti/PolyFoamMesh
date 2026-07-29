#include "autopoly/Types.h"

#include <vector>
#include <string>
#include <cmath>
#include <algorithm>
#include <limits>
#include <sstream>

namespace autopoly {

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

bool PolyMesh::validate() const
{
    static thread_local std::vector<std::string> issues;
    issues.clear();

    // 1. Face vertex indices
    const size_t nPoints = points.size();
    for (size_t fi = 0; fi < faces.size(); ++fi) {
        const auto& f = faces[fi];
        if (f.vertices.size() < 3) {
            std::ostringstream os;
            os << "Face " << fi << " has " << f.vertices.size() << " vertices (< 3)";
            issues.push_back(os.str());
        }
        for (int vi : f.vertices) {
            if (vi < 0 || static_cast<size_t>(vi) >= nPoints) {
                std::ostringstream os;
                os << "Face " << fi << " references invalid vertex " << vi
                   << " (points count = " << nPoints << ")";
                issues.push_back(os.str());
            }
        }
    }

    // 2. Cell face indices + owner/neighbour checks
    for (size_t ci = 0; ci < cells.size(); ++ci) {
        const auto& c = cells[ci];
        for (int fi : c.faces) {
            if (fi < 0 || static_cast<size_t>(fi) >= faces.size()) {
                std::ostringstream os;
                os << "Cell " << ci << " references invalid face " << fi
                   << " (faces count = " << faces.size() << ")";
                issues.push_back(os.str());
            }
        }
    }

    // 3. Owner / neighbour consistency
    for (size_t fi = 0; fi < faces.size(); ++fi) {
        const auto& f = faces[fi];
        if (f.owner_cell < 0) {
            std::ostringstream os;
            os << "Face " << fi << " has no owner cell (owner_cell = " << f.owner_cell << ")";
            issues.push_back(os.str());
        }
        if (f.is_boundary()) {
            if (f.neighbour_cell != -1) {
                std::ostringstream os;
                os << "Boundary face " << fi << " has neighbour_cell = " << f.neighbour_cell
                   << " (expected -1)";
                issues.push_back(os.str());
            }
        } else {
            // internal face
            if (f.neighbour_cell < 0) {
                std::ostringstream os;
                os << "Internal face " << fi << " has neighbour_cell = " << f.neighbour_cell
                   << " (expected >= 0)";
                issues.push_back(os.str());
            }
            if (f.owner_cell == f.neighbour_cell) {
                std::ostringstream os;
                os << "Internal face " << fi << " has owner == neighbour (both "
                   << f.owner_cell << ")";
                issues.push_back(os.str());
            }
        }
    }

    // 4. Patches reference valid face indices; no face in >1 patch
    if (!patches.empty()) {
        std::vector<int> facePatchCount(faces.size(), 0);
        for (size_t pi = 0; pi < patches.size(); ++pi) {
            const auto& patch = patches[pi];
            for (int fi : patch.face_indices) {
                if (fi < 0 || static_cast<size_t>(fi) >= faces.size()) {
                    std::ostringstream os;
                    os << "Patch \"" << patch.name << "\" references invalid face " << fi;
                    issues.push_back(os.str());
                } else {
                    facePatchCount[fi] += 1;
                }
            }
        }
        for (size_t fi = 0; fi < facePatchCount.size(); ++fi) {
            if (facePatchCount[fi] > 1) {
                std::ostringstream os;
                os << "Face " << fi << " is referenced by " << facePatchCount[fi] << " patches";
                issues.push_back(os.str());
            }
        }
    }

    return issues.empty();
}

// ---------------------------------------------------------------------------
// Centroid computation  (tetrahedral decomposition from reference point)
// ---------------------------------------------------------------------------

void PolyMesh::compute_centroids()
{
    cells.resize(cells.size());   // ensure allocated (no-op if already sized)

    for (auto& cell : cells) {
        if (cell.faces.empty()) {
            cell.centroid = Vec3{0, 0, 0};
            continue;
        }

        // Pick reference point: first vertex of first face
        Vec3 ref{0, 0, 0};
        bool refValid = false;
        for (int fi : cell.faces) {
            if (fi >= 0 && fi < static_cast<int>(faces.size())) {
                const auto& f = faces[fi];
                if (!f.vertices.empty()) {
                    ref = points[f.vertices[0]].position;
                    refValid = true;
                    break;
                }
            }
        }
        if (!refValid) {
            cell.centroid = Vec3{0, 0, 0};
            continue;
        }

        Vec3 centroidSum{0, 0, 0};
        double totalVol = 0.0;

        for (int fi : cell.faces) {
            if (fi < 0 || fi >= static_cast<int>(faces.size())) continue;
            const auto& f = faces[fi];
            if (f.vertices.size() < 3) continue;

            // Triangulate from face[0]
            int v0 = f.vertices[0];
            for (size_t j = 1; j + 1 < f.vertices.size(); ++j) {
                int v1 = f.vertices[j];
                int v2 = f.vertices[j + 1];

                const Vec3& a = points[v0].position;
                const Vec3& b = points[v1].position;
                const Vec3& c = points[v2].position;
                const Vec3& r = ref;

                // Signed tetrahedron volume
                Vec3 ab = b - a;
                Vec3 ac = c - a;
                Vec3 ar = r - a;
                double vol = std::abs(dot(cross(ab, ac), ar)) / 6.0;

                // Centroid of tetrahedron = average of its 4 vertices
                Vec3 tetCentroid = (a + b + c + r) / 4.0;

                centroidSum += tetCentroid * vol;
                totalVol += vol;
            }
        }

        if (totalVol > 0.0) {
            cell.centroid = centroidSum / totalVol;
        } else {
            cell.centroid = ref;
        }
    }
}

// ---------------------------------------------------------------------------
// Volume computation  (signed tetrahedra from cell centroid)
// ---------------------------------------------------------------------------

void PolyMesh::compute_volumes()
{
    // Ensure centroids are up to date
    compute_centroids();

    for (auto& cell : cells) {
        if (cell.faces.empty()) {
            cell.volume = 0.0;
            continue;
        }

        double totalVol = 0.0;

        for (int fi : cell.faces) {
            if (fi < 0 || fi >= static_cast<int>(faces.size())) continue;
            const auto& f = faces[fi];
            if (f.vertices.size() < 3) continue;

            // Triangulate from face[0]
            int v0 = f.vertices[0];
            for (size_t j = 1; j + 1 < f.vertices.size(); ++j) {
                int v1 = f.vertices[j];
                int v2 = f.vertices[j + 1];

                const Vec3& a = points[v0].position;
                const Vec3& b = points[v1].position;
                const Vec3& c = points[v2].position;
                const Vec3& o = cell.centroid;

                // Signed tetrahedron volume:  dot(cross(b-a, c-a), centroid-a) / 6
                Vec3 ab = b - a;
                Vec3 ac = c - a;
                Vec3 ao = o - a;
                double vol = dot(cross(ab, ac), ao) / 6.0;

                totalVol += vol;
            }
        }

        // Store absolute value (should already be positive with correct orientation)
        cell.volume = std::abs(totalVol);
    }
}

} // namespace autopoly
