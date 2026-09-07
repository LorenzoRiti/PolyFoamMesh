#pragma once

#include "Types.h"
#include "Geometry.h"
#include <vector>
#include <unordered_map>

namespace autopoly {

// -----------------------------------------------------------------------------
// Feature extraction
// -----------------------------------------------------------------------------

struct FeatureData {
    std::vector<FeatureEdge> sharp_edges;      // edges with dihedral angle > threshold
    std::vector<std::size_t> feature_vertices; // vertices on sharp edges
    std::vector<double> vertex_curvature;      // curvature at each vertex
    std::vector<std::size_t> ridge_vertices;   // vertices on curvature ridges
    std::vector<std::pair<std::size_t, std::size_t>> gap_edges; // thin gap edges
    double min_gap_width = 0.0;
    double max_gap_width = 0.0;
};

// Extract sharp features from surface mesh
FeatureData extractFeatures(const LoadedGeometry& geom, const FeatureOptions& options);

// Compute dihedral angles for all edges
std::vector<double> computeDihedralAngles(const LoadedGeometry& geom);

// Find edges with dihedral angle > threshold
std::vector<FeatureEdge> findSharpEdges(const LoadedGeometry& geom, double angle_threshold_deg);

// Compute vertex curvature (mean curvature approximation)
std::vector<double> computeVertexCurvature(const LoadedGeometry& geom);

// Detect curvature ridges
std::vector<std::size_t> detectCurvatureRidges(const LoadedGeometry& geom, double sensitivity);

// Detect thin gaps between nearby surface patches
std::vector<std::pair<std::size_t, std::size_t>> detectGaps(const LoadedGeometry& geom, double max_gap_ratio);

// Build edge-to-face adjacency
struct EdgeAdjacency {
    std::unordered_map<std::uint64_t, std::vector<std::size_t>> edge_to_faces;
    std::vector<std::array<std::size_t, 2>> edges;  // edge index -> (v0, v1)
};

EdgeAdjacency buildEdgeAdjacency(const LoadedGeometry& geom);

// Compute feature size field (smaller near features)
std::vector<double> computeFeatureSizeField(
    const LoadedGeometry& geom,
    const FeatureData& features,
    const SizeFieldOptions& options
);

} // namespace autopoly