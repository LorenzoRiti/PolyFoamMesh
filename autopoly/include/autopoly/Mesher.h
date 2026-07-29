#pragma once

#include "Types.h"
#include "Geometry.h"
#include "Feature.h"
#include "SizeField.h"
#include "SurfaceMesher.h"
#include "VolumeMesher.h"
#include "BoundaryLayer.h"
#include "Quality.h"
#include "Exporter.h"

namespace autopoly {

class Mesher {
public:
    virtual ~Mesher() = default;

    virtual MeshingResult run(
        const std::filesystem::path& inputGeometry,
        const MeshingParameters& params,
        ProgressCallback progress = {},
        CancelToken cancel = {}) = 0;

    virtual MeshingResult runFromSurfaceMesh(
        const SurfaceMesh& surface,
        const MeshingParameters& params,
        ProgressCallback progress = {},
        CancelToken cancel = {}) = 0;

    virtual MeshingResult runFromLoadedGeometry(
        const LoadedGeometry& geometry,
        const MeshingParameters& params,
        ProgressCallback progress = {},
        CancelToken cancel = {}) = 0;
};

} // namespace autopoly