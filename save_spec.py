"""Save MetaGPT-Spec to Octopoda."""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from cfmesh_autogui.octopoda_local import octo

SPEC = {
    "project": "cfmesh-autogui",
    "version_target": "2.0.0",
    "objective": (
        "Evolve the v1.x codebase into a commercial-grade v2.0 mesh generator "
        "combining GMSH + cfMesh with professional PySide6 GUI"
    ),
    "files_involved": {
        "core/geometry.py": "Feature-aware cell sizing via local thickness sampling (EXISTS). Needs gap detection integration with FeatureDetector.",
        "core/gmsh_wrapper.py": "GMSH hybrid (surface STL) + direct (tetra+BL). All 3 modes working. Needs curvature threshold config.",
        "core/feature_detector.py": "Sharp edge, gap, curvature detection via GMSH (EXISTS but disconnected). WIRE to meshing flow.",
        "core/meshdict_gen.py": "meshDict writer for cfMesh v2512 (EXISTS). Add quality-driven auto-tuning.",
        "core/openfoam_runner.py": "RetryRunner, CheckMeshWorker, QualityFixWorker, BatchRunner (ALL EXIST). Wire QualityFixWorker + BatchRunner to GUI.",
        "core/mesh_converter.py": "MSH to OpenFOAM polyMesh (EXISTS). Add CGNS/VTU export improvements.",
        "core/stl_writer.py": "Multi-solid STL export with healing (EXISTS).",
        "core/case_setup.py": "Case files generator (EXISTS). Add template-based BC presets.",
        "core/boundary_reader.py": "boundary file parser (EXISTS).",
        "gui/main_window.py": "Central orchestrator. UndoStack (EXISTS but empty). Wire Quick Mesh, Batch, ParaView launch.",
        "gui/params_panel.py": "Tabbed params (Geometry/Mesh/Advanced/Quality). Update Quality tab with live checkMesh data.",
        "gui/viewer_widget.py": "PyVistaQt 3D viewer. Section cut (EXISTS). Add interactive clipping plane controls.",
        "gui/quality_panel.py": "Quality metrics display. Wire Auto-Fix button to QualityFixWorker.",
        "gui/log_panel.py": "Log display (EXISTS).",
        "gui/new_case_wizard.py": "3-step wizard (Geometry/Mesh/Quality) (EXISTS).",
        "gui/pdf_report.py": "PDF quality report via reportlab (EXISTS).",
        "gui/about_dialog.py": "About dialog (EXISTS).",
        "gui/design_tokens.py": "Full design token system (EXISTS).",
        "gui/branding.py": "Programmatic branding/splash (EXISTS).",
        "gui/theme.py": "Light/Dark/System theme switch (EXISTS).",
        "gui/style.py": "Style helpers (EXISTS).",
        "gui/constants.py": "Constants (EXISTS).",
        "gui/log_tags.py": "Tag definitions (EXISTS).",
        "installer/inno_setup.iss": "Inno Setup (EXISTS).",
        "installer/nsis_installer.nsi": "NSIS installer (EXISTS).",
        "templates/internal_flow.json": "Case template (EXISTS).",
        "templates/external_aero.json": "Case template (EXISTS).",
        "templates/cht.json": "Case template (EXISTS).",
    },
    "features": {
        "P0_GMSH_HYBRID": {
            "status": "EXISTS in gmsh_wrapper.py (generate_surface_stl + compute_sizing)",
            "acceptance": "Surface STL with curvature-aware refinement -> cfMesh fills volume -> checkMesh passes",
            "test": "tests/test_gmsh_hybrid.py"
        },
        "P0_GMSH_DIRECT": {
            "status": "EXISTS in gmsh_wrapper.py (generate_volume_mesh)",
            "acceptance": "Tetra+BL mesh generated without WSL -> converted to OpenFOAM polyMesh via meshio",
            "test": "tests/test_gmsh_direct.py"
        },
        "P0_FEATURE_AWARE_SIZING": {
            "status": "PARTIAL: geometry.py has analyze_local_thickness; feature_detector.py has FeatureDetector. NEEDS WIRING.",
            "acceptance": "Cell sizes adapt to curvature/sharp-edges/gaps via GMSH analysis + local thickness sampling",
            "fix": "Wire FeatureDetector into main_window._on_run_meshing to compute FeatureMap before meshdict gen"
        },
        "P0_NEW_CASE_WIZARD": {
            "status": "EXISTS in gui/new_case_wizard.py (3 pages: Geometry->Mesh->Quality)",
            "acceptance": "Step-by-step wizard creates complete case with geometry, mesh params, and quality preview",
            "fix": "Add wizard launcher to File menu (Ctrl+Shift+N) and wire to meshing pipeline"
        },
        "P0_TABBED_PARAMS": {
            "status": "EXISTS in gui/params_panel.py (Geometry/Mesh/Advanced/Quality tabs)",
            "acceptance": "All 4 tabs functional with live param updates and validation",
            "fix": "Quality tab shows live checkMesh data after meshing completes"
        },
        "P0_QUALITY_PIPELINE": {
            "status": "PARTIAL: QualityFixWorker EXISTS in openfoam_runner.py. Auto-Fix button in quality_panel.py NOT WIRED.",
            "acceptance": "checkMesh -> auto-fix (relax sizes, disable BL) -> re-run with max 3 iterations",
            "fix": "Wire quality_panel._on_auto_fix -> QualityFixWorker.run() on current case_dir"
        },
        "P0_QUICK_MESH": {
            "status": "EXISTS: Ctrl+M shortcut + Quick Mesh button in params_panel.py",
            "acceptance": "One-click autosuggests cell sizes and runs meshing with current params",
            "fix": "Ensure Quick Mesh triggers FeatureDetector if no manual sizing done"
        },
        "P1_EXPORT_CGNS_VTU": {
            "status": "EXISTS in main_window.py (_on_export_mesh handles cgns/vtu via meshio)",
            "acceptance": "Exports OpenFOAM polyMesh to CGNS and VTU format via meshio",
            "fix": "Improve meshio export robustness for large meshes"
        },
        "P1_CLIPPING_PLANE": {
            "status": "EXISTS in viewer_widget.py (section cut toggle with clip box)",
            "acceptance": "Interactive clip plane slices mesh for internal view",
            "fix": "Add interactive plane manipulation (move/rotate via mouse)"
        },
        "P1_UNDO_REDO": {
            "status": "STUB: QUndoStack initialized in main_window but NO commands pushed",
            "acceptance": "Ctrl+Z/Y undo/redo cell size changes, BL toggles, patch renames",
            "fix": "Create QUndoCommand subclasses for each param change + patch rename"
        },
        "P1_CASE_TEMPLATES": {
            "status": "EXISTS: 3 JSON templates + _on_template_selected in params_panel.py",
            "acceptance": "Loading Internal Flow/External Aero/CHT presets applies defaults",
            "fix": "Add more templates + mesher_type preset"
        },
        "P1_WINDOWS_INSTALLER": {
            "status": "EXISTS: NSIS + Inno Setup scripts in installer/",
            "acceptance": "Installer bundles Python, deps, and launches app",
            "fix": "Update version to 2.0.0, test on clean Windows"
        },
        "P1_LOCALIZATION_IT": {
            "status": "EXISTS: _init_i18n in app.py loads QTranslator",
            "acceptance": "GUI strings translated to Italian when system locale is Italian",
            "fix": "Generate actual .ts/.qm translation files"
        },
        "P1_PARAVIEW_INTEGRATION": {
            "status": "MISSING",
            "acceptance": "Launch ParaView from app with current case directory loaded",
            "fix": "Add File -> Open in ParaView action that spawns paraview.exe"
        },
        "P2_PDF_REPORT": {
            "status": "EXISTS in gui/pdf_report.py + File -> Export -> PDF action",
            "acceptance": "Generates A4 PDF with metrics table, pass/fail verdict, case info",
            "fix": "Add mesh stats charts (cell count, skewness histogram)"
        },
        "P2_BATCH_PROCESSING": {
            "status": "STUB: BatchRunner EXISTS in openfoam_runner.py but not in GUI",
            "acceptance": "Process multiple cases sequentially with progress tracking",
            "fix": "Add Batch dialog + wire to BatchRunner with JSON job list"
        },
        "P2_PLUGIN_SYSTEM": {
            "status": "MISSING",
            "acceptance": "Third-party plugins can register custom meshers, exporters, or quality metrics",
            "fix": "Design plugin API with importlib entry_points discovery + ABC base classes for Mesher/Exporter/QualityCheck"
        },
    },
    "acceptance_criteria": [
        "All P0 features pass manual QA: load STEP -> configure -> mesh -> checkMesh passes",
        "GMSH direct mode generates valid polyMesh WITHOUT WSL dependency",
        "Feature-aware sizing reduces cell count vs uniform sizing by >=30% on multi-scale geometries",
        "Quality pipeline auto-fixes bad meshes in <=3 iterations",
        "Quick Mesh (Ctrl+M) works with zero manual param adjustment",
        "Case templates produce correct meshDict settings per flow type",
        "CGNS and VTU exports produce files readable by ParaView 5.15+",
        "Undo/Redo correctly restores previous param states",
        "Batch processing handles 10+ cases without crash",
        "Windows installer installs and runs on clean Win10/Win11",
        "PDF report contains all quality metrics with pass/warn/fail status",
    ],
    "architecture_decisions": [
        "GMSH runs natively on Windows (no WSL) for direct and hybrid surface mesh",
        "cfMesh runs via WSL2 Ubuntu for hexa-dominant volume fill",
        "PyVistaQt for 3D rendering (no OpenFOAM native viewer dependency)",
        "QUndoStack for undo/redo (no custom command history)",
        "meshio for all format conversions (MSH->polyMesh, CGNS, VTU)",
        "QSettings for persistent UI state (window size, theme, recent files)",
        "reportlab for PDF generation (no LaTeX dependency)",
        "Plugin system via importlib.metadata entry_points + ABC base classes",
    ],
}

octo.remember("metagpt_spec", SPEC)
print("SPEC saved to Octopoda")

total = len(SPEC["features"])
complete = sum(1 for f in SPEC["features"].values() if f["status"].startswith("EXISTS"))
missing = sum(1 for f in SPEC["features"].values() if f["status"].startswith("MISSING"))
partial = sum(1 for f in SPEC["features"].values() if f["status"].startswith("PARTIAL") or f["status"].startswith("STUB"))
print(f"Features: {total} total ({complete} complete, {missing} missing, {partial} partial/stub)")
print(f"Acceptance criteria: {len(SPEC['acceptance_criteria'])}")
