#pragma once

#include "Types.h"
#include "SurfaceMesher.h"
#include "VolumeMesher.h"

namespace autopoly {

enum class ExportFormat {
    AutoDetect,
    VTK,
    OpenFOAM,
    JSON,
    STL,
    OBJ,
    PLY
};

struct ExportReport {
    bool success = false;
    std::vector<std::filesystem::path> written_files;
    std::vector<std::string> warnings;
    std::vector<std::string> errors;
};

// Export poly mesh to VTK (.vtu)
ExportReport exportVTK(
    const VolumeMesh& mesh,
    const std::filesystem::path& output_path,
    const std::string& title = "autopoly_mesh"
);

// Export poly mesh to OpenFOAM polyMesh format
ExportReport exportOpenFOAM(
    const VolumeMesh& mesh,
    const std::vector<BoundaryPatch>& patches,
    const std::filesystem::path& output_dir
);

// Export mesh quality report as JSON
ExportReport exportJSONReport(
    const MeshQualityReport& quality,
    const std::filesystem::path& output_path
);

// Export surface mesh to STL
ExportReport exportSTL(
    const SurfaceMesh& mesh,
    const std::filesystem::path& output_path
);

// Export surface mesh to OBJ
ExportReport exportOBJ(
    const SurfaceMesh& mesh,
    const std::filesystem::path& output_path
);

// Export surface mesh to PLY
ExportReport exportPLY(
    const SurfaceMesh& mesh,
    const std::filesystem::path& output_path
);

// Auto-detect format from file extension and export
ExportReport exportMesh(
    const VolumeMesh& mesh,
    const std::vector<BoundaryPatch>& patches,
    const std::filesystem::path& output_path,
    ExportFormat format = ExportFormat::AutoDetect
);

// Export all formats as configured in ExportOptions
ExportReport exportAll(
    const VolumeMesh& mesh,
    const std::vector<BoundaryPatch>& patches,
    const MeshQualityReport& quality,
    const ExportOptions& options
);

} // namespace autopoly