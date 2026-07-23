import json, sys

plan = {
    "metagpt_arch_v2": {
        "title": "CFMesh-AutoGUI — Architectural Plan v2 (post-review fixes)",
        "files": [
            # ---------------------------------------------------------------
            # 1. pyproject.toml — add gmsh + meshio + reportlab/fpdf deps
            # ---------------------------------------------------------------
            {
                "file": "pyproject.toml",
                "changes": [
                    {
                        "location": "dependencies list (line 13-19)",
                        "action": "Add gmsh, meshio, and reportlab (or alternative) to [project] dependencies",
                        "detail": "Add three new entries:\n  \"gmsh>=4.13.0\",\n  \"meshio>=5.3.0\",\n  \"reportlab>=4.2.0\"\nReportlab is used for F14 PDF report generation. Alternative route (QPrinter/QtPDF) is documented in risks but reportlab is the primary choice for cross-platform consistency."
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 2. i18n: toolchain + _init_i18n() with fallback
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/app.py",
                "changes": [
                    {
                        "location": "new function _init_i18n() called before MainWindow()",
                        "action": "Add _init_i18n() that initializes Qt translations with fallback path",
                        "detail": (
                            "def _init_i18n():\n"
                            "    from PySide6.QtCore import QTranslator, QLibraryInfo, QLocale\n"
                            "    from pathlib import Path\n"
                            "    qt_path = Path(QLibraryInfo.location(QLibraryInfo.TranslationsPath))\n"
                            "    local_path = Path(__file__).resolve().parent / 'locale'\n"
                            "    translator = QTranslator()\n"
                            "    # Try system Qt path first, fallback to local locale/\n"
                            "    for base in [qt_path, local_path]:\n"
                            "        if translator.load(QLocale(), 'cfmesh_autogui', '_', str(base)):\n"
                            "            break\n"
                            "    QApplication.instance().installTranslator(translator)\n"
                            "Call _init_i18n() right after splash.show() before apply_theme(app)."
                        )
                    }
                ]
            },
            {
                "file": "docs/i18n.md (new, optional)",
                "changes": [
                    {
                        "location": "new file",
                        "action": "Document i18n toolchain: pylupdate5 -> .ts -> lrelease -> .qm",
                        "detail": (
                            "Toolchain:\n"
                            "1. pylupdate5 src/ -ts locale/cfmesh_autogui_it.ts\n"
                            "2. Edit locale/cfmesh_autogui_it.ts with translations\n"
                            "3. lrelease locale/cfmesh_autogui_it.ts → generates .qm\n"
                            "4. Place .qm in locale/ or Qt system TranslationsPath"
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 3. _on_export_mesh(fmt) in MainWindow
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/gui/main_window.py",
                "changes": [
                    {
                        "location": "new method after _on_meshing_finished",
                        "action": "Add _on_export_mesh(self, fmt: str) for CGNS/VTU export via meshio",
                        "detail": (
                            "def _on_export_mesh(self, fmt: str):\n"
                            "    if not self._case_dir:\n"
                            "        return\n"
                            "    fmt = fmt.lower()\n"
                            "    if fmt not in ('cgns', 'vtu'):\n"
                            "        self._log.append_log(f'{Tag.ERROR} Unsupported export format: {fmt}')\n"
                            "        return\n"
                            "    from pathlib import Path\n"
                            "    import meshio\n"
                            "    poly_dir = self._case_dir / 'constant' / 'polyMesh'\n"
                            "    if not poly_dir.exists():\n"
                            "        self._log.append_log(f'{Tag.ERROR} No polyMesh found in case dir')\n"
                            "        return\n"
                            "    # Use meshio to read OpenFOAM mesh and write CGNS/VTU\n"
                            "    try:\n"
                            "        msh = meshio.read(str(poly_dir), file_format='openfoam')\n"
                            "        out_path = self._case_dir / f'mesh.{fmt}'\n"
                            "        meshio.write(str(out_path), msh, file_format=fmt)\n"
                            "        self._log.append_log(f'{Tag.EXPORT} Exported mesh to {out_path}')\n"
                            "    except Exception as e:\n"
                            "        self._log.append_log(f'{Tag.ERROR} Export failed: {e}')\n"
                            "Connect to File > Export Mesh submenu with CGNS/VTU actions."
                        )
                    },
                    {
                        "location": "_setup_menu() (line ~126)",
                        "action": "Add File > Export submenu with CGNS and VTU actions",
                        "detail": (
                            "export_menu = fm.addMenu('Export Mesh')\n"
                            "export_menu.addAction('CGNS...', lambda: self._on_export_mesh('cgns'))\n"
                            "export_menu.addAction('VTU...', lambda: self._on_export_mesh('vtu'))"
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 4. Undo/Redo via QUndoCommand + param_changed signal
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/gui/params_panel.py",
                "changes": [
                    {
                        "location": "imports + __init__",
                        "action": "Add QUndoStack, emit param_changed signal on every value change",
                        "detail": (
                            "Add imports:\n"
                            "  from PySide6.QtGui import QUndoStack, QUndoCommand\n\n"
                            "In __init__ after _setup_ui():\n"
                            "  self._undo_stack = QUndoStack(self)\n\n"
                            "Define ParamChangeCommand(QUndoCommand) class:\n"
                            "  class ParamChangeCommand(QUndoCommand):\n"
                            "      def __init__(self, panel, key, old_val, new_val):\n"
                            "          super().__init__(f'Change {key}')\n"
                            "          self._panel = panel\n"
                            "          self._key = key\n"
                            "          self._old = old_val\n"
                            "          self._new = new_val\n"
                            "      def undo(self): self._panel._apply_param(self._key, self._old)\n"
                            "      def redo(self): self._panel._apply_param(self._key, self._new)\n\n"
                            "Wire every valueChanged signal (max_cell, min_cell, BL params, etc.)\n"
                            "to emit param_changed via a slot that pushes a ParamChangeCommand:\n"
                            "  def _on_param_changed(self, key, new_val):\n"
                            "      old = getattr(self, f'_{key}').value()\n"
                            "      self._undo_stack.push(ParamChangeCommand(self, key, old, new_val))\n"
                            "      self.mesh_params_changed.emit(self.get_mesh_params())\n\n"
                            "Add Ctrl+Z / Ctrl+Y shortcuts via QShortcut in MainWindow."
                        )
                    }
                ]
            },
            {
                "file": "src/cfmesh_autogui/gui/main_window.py",
                "changes": [
                    {
                        "location": "_setup_menu() File section",
                        "action": "Add Edit > Undo/Redo menu items",
                        "detail": (
                            "edit_menu = self.menuBar().addMenu('&Edit')\n"
                            "edit_menu.addAction('Undo\\tCtrl+Z', self._params.undo)\n"
                            "edit_menu.addAction('Redo\\tCtrl+Y', self._params.redo)\n"
                            "QShortcut('Ctrl+Z', self, activated=self._params.undo)\n"
                            "QShortcut('Ctrl+Y', self, activated=self._params.redo)"
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 5. Template JSON presets
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/templates/internal_flow.json (new)",
                "changes": [
                    {
                        "location": "new file",
                        "action": "Create internal_flow.json template preset",
                        "detail": (
                            "{\n"
                            "  \"name\": \"Internal Flow\",\n"
                            "  \"description\": \"Pipe/duct internal flow with BL on all walls\",\n"
                            "  \"max_cell_size\": 0.02,\n"
                            "  \"min_cell_size\": 0.002,\n"
                            "  \"bl_enabled\": true,\n"
                            "  \"bl_n_layers\": 5,\n"
                            "  \"bl_thickness_ratio\": 0.005,\n"
                            "  \"bl_expansion_ratio\": 1.2,\n"
                            "  \"detail_level\": \"medium\",\n"
                            "  \"unit\": \"m\"\n"
                            "}"
                        )
                    }
                ]
            },
            {
                "file": "src/cfmesh_autogui/templates/external_aero.json (new)",
                "changes": [
                    {
                        "location": "new file",
                        "action": "Create external_aero.json template preset",
                        "detail": (
                            "{\n"
                            "  \"name\": \"External Aero\",\n"
                            "  \"description\": \"External aerodynamics around a body\",\n"
                            "  \"max_cell_size\": 0.1,\n"
                            "  \"min_cell_size\": 0.005,\n"
                            "  \"bl_enabled\": true,\n"
                            "  \"bl_n_layers\": 8,\n"
                            "  \"bl_thickness_ratio\": 0.002,\n"
                            "  \"bl_expansion_ratio\": 1.15,\n"
                            "  \"detail_level\": \"fine\",\n"
                            "  \"unit\": \"m\"\n"
                            "}"
                        )
                    }
                ]
            },
            {
                "file": "src/cfmesh_autogui/templates/cht.json (new)",
                "changes": [
                    {
                        "location": "new file",
                        "action": "Create cht.json template preset for Conjugate Heat Transfer",
                        "detail": (
                            "{\n"
                            "  \"name\": \"CHT (Conjugate Heat Transfer)\",\n"
                            "  \"description\": \"Solid-fluid CHT with volumetric refinement at interface\",\n"
                            "  \"max_cell_size\": 0.03,\n"
                            "  \"min_cell_size\": 0.001,\n"
                            "  \"bl_enabled\": true,\n"
                            "  \"bl_n_layers\": 3,\n"
                            "  \"bl_thickness_ratio\": 0.003,\n"
                            "  \"bl_expansion_ratio\": 1.3,\n"
                            "  \"detail_level\": \"fine\",\n"
                            "  \"unit\": \"mm\",\n"
                            "  \"boundary_refinement\": {\n"
                            "    \"thickness\": 0.02,\n"
                            "    \"cell_size\": 0.002\n"
                            "  }\n"
                            "}"
                        )
                    }
                ]
            },
            {
                "file": "src/cfmesh_autogui/gui/params_panel.py",
                "changes": [
                    {
                        "location": "_setup_ui() after CAD group",
                        "action": "Add QComboBox for template selection + load button",
                        "detail": (
                            "self._template_combo = QComboBox()\n"
                            "self._template_combo.addItems(['Custom', 'Internal Flow', 'External Aero', 'CHT'])\n"
                            "self._template_combo.currentTextChanged.connect(self._on_template_selected)\n"
                            "template_form = QFormLayout()\n"
                            "template_form.addRow('Template:', self._template_combo)\n"
                            "cad_layout.addLayout(template_form)\n\n"
                            "def _on_template_selected(self, name: str):\n"
                            "    if name == 'Custom': return\n"
                            "    path = Path(__file__).resolve().parent.parent / 'templates' / f'{name.lower().replace(\" \", \"_\")}.json'\n"
                            "    if not path.exists(): return\n"
                            "    data = json.loads(path.read_text())\n"
                            "    self._max_cell.setValue(data['max_cell_size'])\n"
                            "    self._min_cell.setValue(data['min_cell_size'])\n"
                            "    self._bl_checkbox.setChecked(data['bl_enabled'])\n"
                            "    self._bl_n_layers.setValue(data['bl_n_layers'])\n"
                            "    self._bl_thick.setValue(data['bl_thickness_ratio'])\n"
                            "    self._bl_exp.setValue(data['bl_expansion_ratio'])\n"
                            "    self._detail_combo.setCurrentText(data['detail_level'].capitalize())\n"
                            "    self._unit_selector.setCurrentText(data['unit'])"
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 6. set_clip_axis / set_clip_position → unified in _on_section_toggled
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/gui/viewer_widget.py",
                "changes": [
                    {
                        "location": "remove any separate set_clip_axis/set_clip_position methods",
                        "action": "Unify all clipping logic inside _on_section_toggled()",
                        "detail": (
                            "The existing _on_section_toggled() already handles:\n"
                            "  - enabling/disabling the clipping plane\n"
                            "  - total_face cap check (50k) to prevent UI hangs\n"
                            "  - delegating to _build_combined_pvdata() + add_mesh_clip_box()\n"
                            "  - position at bbox centre + Z offset for a clean cross-section\n\n"
                            "If any standalone set_clip_axis() or set_clip_position() methods\n"
                            "exist from earlier drafts, DELETE them. All clipping control must\n"
                            "flow through _on_section_toggled(). The section_check QCheckBox\n"
                            "is the single source of truth.\n\n"
                            "NO code change needed if they already don't exist — just document\n"
                            "that _on_section_toggled is the single entry point."
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 7. QualityFixWorker extends RetryRunner
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/core/openfoam_runner.py",
                "changes": [
                    {
                        "location": "new class after RetryRunner (line ~526)",
                        "action": "Add QualityFixWorker that extends RetryRunner with quality-driven retries",
                        "detail": (
                            "class QualityFixWorker(RetryRunner):\n"
                            "    \"\"\"Extends RetryRunner with quality-check retry logic.\n\n"
                            "    After a successful mesh, runs checkMesh. If quality fails\n"
                            "    (non-ortho > threshold, skewness > threshold, negative cells),\n"
                            "    it regenerates meshDict with more conservative cell sizes\n"
                            "    and retries. Max 2 quality-driven retries.\n\"\"\"\n\n"
                            "    MAX_QUALITY_RETRIES = 2\n\n"
                            "    def __init__(self, of_config, parent=None):\n"
                            "        super().__init__(of_config, parent)\n"
                            "        self._quality_retries = 0\n"
                            "        self._quality_thresholds = {\n"
                            "            'max_non_ortho': 70.0,\n"
                            "            'max_skewness': 5.0,\n"
                            "        }\n\n"
                            "    DOES NOT replace RetryRunner — it inherits and extends it.\n"
                            "    The original RetryRunner remains the base class for all\n"
                            "    meshing workflows. MainWindow may optionally instantiate\n"
                            "    QualityFixWorker instead of RetryRunner when quality mode\n"
                            "    is enabled (checkbox in params_panel)."
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 8. Toolchain i18n documented (already in item 2 above)
            # ---------------------------------------------------------------
            # Already covered in the i18n section above.
            # ---------------------------------------------------------------
            # 9. Quick Mesh button + Ctrl+M shortcut
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/gui/params_panel.py",
                "changes": [
                    {
                        "location": "_setup_ui() action buttons area (line ~253)",
                        "action": "Add Quick Mesh QPushButton before _btn_run",
                        "detail": (
                            "self._btn_quick = QPushButton('Quick Mesh (Ctrl+M)')\n"
                            "self._btn_quick.setMinimumHeight(34)\n"
                            "self._btn_quick.setToolTip('Quick mesh with auto-suggested sizes + medium detail. Shortcut Ctrl+M.')\n"
                            "self._btn_quick.clicked.connect(self._on_quick_mesh)\n"
                            "layout.addWidget(self._btn_quick)\n\n"
                            "def _on_quick_mesh(self):\n"
                            "    # Auto-suggest cell sizes then immediately run\n"
                            "    if self._suggest_meshes:\n"
                            "        self._on_suggest_sizes()\n"
                            "    self._on_run()"
                        )
                    },
                    {
                        "location": "set_all_enabled() line ~426",
                        "action": "Add self._btn_quick to the enabled/disabled list",
                        "detail": (
                            "Add: self._btn_quick.setEnabled(enabled)"
                        )
                    }
                ]
            },
            {
                "file": "src/cfmesh_autogui/gui/main_window.py",
                "changes": [
                    {
                        "location": "_setup_menu() shortcuts (line ~128)",
                        "action": "Add Ctrl+M shortcut for Quick Mesh",
                        "detail": (
                            "QShortcut('Ctrl+M', self, activated=self._params._on_quick_mesh)"
                        )
                    }
                ]
            },
            # ---------------------------------------------------------------
            # 10. F14 PDF report — reportlab dependency (item 1 above)
            # ---------------------------------------------------------------
            {
                "file": "src/cfmesh_autogui/gui/pdf_report.py (new)",
                "changes": [
                    {
                        "location": "new file",
                        "action": "Add PDF report generator using reportlab",
                        "detail": (
                            "class MeshReportPDF:\n"
                            "    def __init__(self, case_dir: Path, quality_report: dict):\n"
                            "        self._case_dir = case_dir\n"
                            "        self._report = quality_report\n\n"
                            "    def generate(self, path: Path | None = None) -> Path:\n"
                            "        from reportlab.lib.pagesizes import A4\n"
                            "        from reportlab.pdfgen import canvas\n"
                            "        # Write a single-page PDF with:\n"
                            "        # - Case directory, date\n"
                            "        # - Cell count, cell type\n"
                            "        # - Non-orthogonality, skewness, aspect ratio\n"
                            "        # - PASS/FAIL verdict\n"
                            "        # - Raw checkMesh output (optional)\n\n"
                            "Add menu action File > Export PDF in MainWindow that collects\n"
                            "current quality report from QualityPanel and calls MeshReportPDF."
                        )
                    }
                ]
            },
            {
                "file": "src/cfmesh_autogui/gui/main_window.py",
                "changes": [
                    {
                        "location": "_setup_menu() Export submenu",
                        "action": "Add Export PDF action",
                        "detail": (
                            "export_menu.addAction('PDF Report...', self._on_export_pdf)\n\n"
                            "def _on_export_pdf(self):\n"
                            "    from cfmesh_autogui.gui.pdf_report import MeshReportPDF\n"
                            "    path, _ = QFileDialog.getSaveFileName(self, 'Save PDF', '', 'PDF (*.pdf)')\n"
                            "    if not path: return\n"
                            "    gen = MeshReportPDF(self._case_dir, self._quality.get_report_data())\n"
                            "    out = gen.generate(Path(path))\n"
                            "    self._log.append_log(f'{Tag.EXPORT} PDF report: {out}')"
                        )
                    }
                ]
            },
        ],
        "dependencies": [
            "pyproject.toml must be updated first so gmsh/meshio/reportlab are installable",
            "templates/ directory must exist before params_panel references it",
            "locale/ directory must exist for i18n fallback"
        ],
        "estimated_effort": "medium (10 files touched, ~250 lines added)"
    }
}

print(json.dumps(plan, indent=2))
