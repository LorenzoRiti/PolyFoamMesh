#pragma once

#include "Types.h"
#include "Geometry.h"
#include "Feature.h"
#include <vector>
#include <functional>

namespace autopoly {

// -----------------------------------------------------------------------------
// Size field generation
// -----------------------------------------------------------------------------

struct SizeField {
    // Size field defined at mesh vertices
    std::vector<double> vertex_sizes;  // target edge length at each vertex

    // Optional: background grid for fast evaluation
    struct BackgroundGrid {
        BoundingBox bbox;
        std::array<int, 3> resolution{32, 32, 32};
        std::vector<double> cell_sizes;  // size per grid cell
    };
    std::optional<BackgroundGrid> grid;

    // Evaluate size at arbitrary point (trilinear interpolation from grid)
    double evaluate(const Vec3& p) const;

    // Evaluate size at vertex index
    double at_vertex(std::size_t i) const {
        return (i < vertex_sizes.size()) ? vertex_sizes[i] : 0.0;
    }
};

// Generate adaptive size field based on geometry, curvature, features, and proximity
SizeField generateSizeField(
    const LoadedGeometry& geom,
    const FeatureData& features,
    const SizeFieldOptions& options
);

// Generate size field from surface mesh with curvature adaptation
SizeField generateSurfaceSizeField(
    const LoadedGeometry& geom,
    const SizeFieldOptions& options
);

// Compute curvature-based size field
std::vector<double> computeCurvatureSizeField(
    const LoadedGeometry& geom,
    const std::vector<double>& curvature,
    double global_size,
    double adaptivity
);

// Compute proximity-based size field (smaller near features/walls)
std::vector<double> computeProximitySizeField(
    const LoadedGeometry& geom,
    const FeatureData& features,
    double global_size,
    double adaptivity
);

// Smooth size field to avoid abrupt transitions
void smoothSizeField(SizeField& field, int iterations = 3);

// Clamp size field to min/max bounds
void clampSizeField(SizeField& field, double min_size, double max_size);

// Build background grid for fast evaluation
void buildBackgroundGrid(SizeField& field, const BoundingBox& bbox, int resolution = 32);

// Interpolate size field onto volume seed points
std::vector<double> interpolateSizeField(
    const SizeField& field,
    const std::vector<Vec3>& seed_points
);

// Compute grading-limited size field (limit size change between neighbors)
void applyGradingLimit(SizeField& field, const LoadedGeometry& geom, double max_growth_rate);

} // namespace autopoly