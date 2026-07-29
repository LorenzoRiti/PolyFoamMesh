#pragma once

#include "Types.h"
#include <vector>
#include <string>
#include <functional>

namespace autopoly {

// -----------------------------------------------------------------------------
// Quality metrics and validation
// -----------------------------------------------------------------------------

struct CellQuality {
    double volume = 0.0;
    double min_face_area = 0.0;
    double max_face_area = 0.0;
    double non_orthogonality = 0.0;  // degrees
    double skewness = 0.0;
    double aspect_ratio = 0.0;
    double face_warpage = 0.0;
    bool valid = true;
    std::vector<std::string> issues;
};

struct MeshQualityReport {
    size_t total_cells = 0;
    size_t total_faces = 0;
    size_t total_points = 0;
    size_t boundary_faces = 0;
    size_t boundary_patches = 0;

    double min_volume = 1e30;
    double max_volume = 0.0;
    double avg_volume = 0.0;

    double max_non_orthogonality = 0.0;
    double avg_non_orthogonality = 0.0;
    double max_skewness = 0.0;
    double avg_skewness = 0.0;
    double max_aspect_ratio = 0.0;
    double avg_aspect_ratio = 0.0;
    double max_face_warpage = 0.0;

    size_t negative_volume_cells = 0;
    size_t high_non_ortho_cells = 0;
    size_t high_skewness_cells = 0;
    size_t high_aspect_ratio_cells = 0;
    size_t non_manifold_faces = 0;
    size_t non_manifold_edges = 0;

    std::vector<CellQuality> cell_qualities;
    std::vector<std::string> warnings;
    std::vector<std::string> errors;

    bool passed = false;

    void computeSummary(const QualityOptions& opts);
    std::string summary() const;
};

// Compute quality for a single polyhedral cell
CellQuality computeCellQuality(
    const std::vector<Vec3>& vertices,
    const std::vector<int>& cell_faces,
    const std::vector<std::vector<int>>& faces,
    const std::vector<int>& face_owner,
    const std::vector<int>& face_neighbour,
    int cell_index
);

// Compute full mesh quality report
MeshQualityReport computeMeshQuality(
    const VolumeMesh& mesh,
    const QualityOptions& options,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Validate mesh topology
struct TopologyValidation {
    bool valid = true;
    size_t non_manifold_faces = 0;
    size_t non_manifold_edges = 0;
    size_t dangling_edges = 0;
    size_t unreferenced_vertices = 0;
    size_t inverted_cells = 0;
    std::vector<std::string> errors;
    std::vector<std::string> warnings;
};

TopologyValidation validateTopology(const VolumeMesh& mesh);

// Repair common mesh issues
struct RepairResult {
    bool modified = false;
    size_t removed_cells = 0;
    size_t removed_faces = 0;
    size_t merged_vertices = 0;
    std::vector<std::string> actions;
};

RepairResult repairMesh(
    VolumeMesh& mesh,
    const QualityOptions& options,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Smooth mesh (Laplacian with constraints)
void smoothMesh(
    VolumeMesh& mesh,
    const std::vector<int>& fixed_vertices,
    int iterations = 10,
    double relaxation = 0.5
);

// Feature-preserving smoothing
void featurePreservingSmooth(
    VolumeMesh& mesh,
    const std::vector<std::pair<int, int>>& feature_edges,
    int iterations = 10,
    double relaxation = 0.3
);

// Planarize boundary faces
void planarizeBoundaryFaces(
    VolumeMesh& mesh,
    double tolerance = 1e-6
);

} // namespace autopoly