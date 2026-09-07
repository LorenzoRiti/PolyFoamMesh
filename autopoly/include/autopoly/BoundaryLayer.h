#pragma once

#include "Types.h"
#include <vector>
#include <string>
#include <functional>

namespace autopoly {

// -----------------------------------------------------------------------------
// Boundary layer generation
// -----------------------------------------------------------------------------

struct BoundaryLayerMesh {
    std::vector<Vec3> vertices;           // all vertices (base + layers)
    std::vector<std::vector<int>> prisms; // prism cells (base face + top face + side faces)
    std::vector<int> base_face_indices;   // indices into original boundary faces
    std::vector<int> patch_ids;           // patch id for each prism column
    std::vector<double> layer_heights;    // height of each layer
    std::vector<Vec3> layer_normals;      // normal at each layer vertex
};

// Generate boundary layers on wall patches
BoundaryLayerMesh generateBoundaryLayers(
    const VolumeMesh& volume_mesh,
    const std::vector<BoundaryPatch>& patches,
    const BoundaryLayerOptions& options,
    const SizeField& size_field,
    ProgressCallback progress = {},
    CancelToken cancel = {}
);

// Smooth layer normals to avoid kinks
void smoothLayerNormals(
    BoundaryLayerMesh& bl_mesh,
    int iterations = 5
);

// Detect and resolve layer collisions
void resolveLayerCollisions(
    BoundaryLayerMesh& bl_mesh,
    const VolumeMesh& volume_mesh,
    double min_gap = 1e-6
);

// Transition from prism layer to polyhedral core
void transitionPrismToPolyhedral(
    VolumeMesh& volume_mesh,
    BoundaryLayerMesh& bl_mesh,
    const std::vector<BoundaryPatch>& patches
);

// Merge boundary layer mesh into volume mesh
void mergeBoundaryLayers(
    VolumeMesh& volume_mesh,
    BoundaryLayerMesh&& bl_mesh,
    const std::vector<BoundaryPatch>& patches
);

// Compute first layer height from y+ target
double computeFirstLayerHeight(
    double y_plus_target,
    double reynolds_number,
    double reference_length,
    double kinematic_viscosity = 1.5e-5  // air at STP
);

// Estimate boundary layer thickness
double estimateBoundaryLayerThickness(
    double reynolds_number,
    double reference_length,
    double kinematic_viscosity = 1.5e-5
);

} // namespace autopoly