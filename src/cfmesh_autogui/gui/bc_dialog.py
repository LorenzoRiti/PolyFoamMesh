from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QFormLayout, QGroupBox, QMessageBox, QFileDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont

from cfmesh_autogui.gui.style import COLOR_ACCENT, COLOR_DANGER, COLOR_PASS, COLOR_TEXT_DIM

logger = logging.getLogger(__name__)

BC_TYPE_COLORS = {
    "inlet": QColor(51, 153, 255),
    "outlet": QColor(255, 77, 51),
    "wall": QColor(153, 153, 153),
    "symmetry": QColor(51, 204, 102),
    "patch": QColor(204, 153, 51),
}


class BCEditorDialog(QDialog):
    bc_applied = Signal()

    def __init__(self, case_dir: Path, parent=None):
        super().__init__(parent)
        self._case_dir = Path(case_dir)
        self._patches: list = []
        self._editor = None
        self.setWindowTitle("Boundary Conditions Editor")
        self.setMinimumSize(650, 450)

        layout = QVBoxLayout(self)

        header = QLabel("Boundary Conditions")
        header.setFont(QFont("Segoe UI", 14, QFont.DemiBold))
        layout.addWidget(header)

        desc = QLabel("Review and edit boundary patches. Auto-detected types are shown below.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{COLOR_TEXT_DIM};")
        layout.addWidget(desc)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Patch Name", "Type", "nFaces", "Detected By"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self._table, 1)

        btn_layout = QHBoxLayout()
        btn_detect = QPushButton("Auto-Detect Types")
        btn_detect.clicked.connect(self._on_auto_detect)
        btn_detect.setStyleSheet(f"background:{COLOR_ACCENT};color:white;")
        btn_layout.addWidget(btn_detect)

        btn_layout.addStretch()

        btn_export = QPushButton("Export 0/ Fields")
        btn_export.clicked.connect(self._on_export)
        btn_export.setStyleSheet(f"background:{COLOR_PASS};color:white;")
        btn_layout.addWidget(btn_export)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        self._load_boundary()

    def _load_boundary(self):
        from cfmesh_autogui.commercial.bc_editor import BCEditor
        self._editor = BCEditor()
        try:
            self._patches = self._editor.read_boundary(self._case_dir)
        except FileNotFoundError:
            QMessageBox.warning(
                self, "No Mesh",
                "No boundary file found. Generate a mesh first."
            )
            return
        self._populate_table()

    def _populate_table(self):
        self._table.setRowCount(len(self._patches))
        for i, p in enumerate(self._patches):
            name_item = QTableWidgetItem(p.name)
            name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
            self._table.setItem(i, 0, name_item)

            type_combo = QComboBox()
            type_combo.addItems(["inlet", "outlet", "wall", "symmetry", "patch"])
            type_combo.setCurrentText(p.bc_type)
            color = BC_TYPE_COLORS.get(p.bc_type, QColor(128, 128, 128))
            type_combo.setStyleSheet(
                f"QComboBox {{ background:{color.name()}; color:white; font-weight:bold; }}"
            )
            type_combo.currentTextChanged.connect(
                lambda t, idx=i: self._on_type_changed(idx, t)
            )
            self._table.setCellWidget(i, 1, type_combo)

            faces_item = QTableWidgetItem(str(p.n_faces))
            faces_item.setFlags(faces_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(i, 2, faces_item)

            detected_item = QTableWidgetItem(p.detected_by or "—")
            detected_item.setFlags(detected_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(i, 3, detected_item)

    def _on_type_changed(self, idx: int, new_type: str):
        if 0 <= idx < len(self._patches):
            self._patches[idx].bc_type = new_type
            self._patches[idx].detected_by = "user"

    def _on_auto_detect(self):
        if not self._editor or not self._patches:
            return
        changed = self._editor.auto_detect_types(self._patches)
        self._populate_table()
        logger.info("BC auto-detect: %d patches changed", changed)

    def _on_export(self):
        if not self._editor or not self._patches:
            QMessageBox.warning(self, "No Patches", "No boundary patches to export.")
            return
        self._editor.export_fields(self._case_dir, self._patches)
        self._editor.export_boundary_file(self._case_dir, self._patches)
        self.bc_applied.emit()
        target = self._case_dir / "0"
        QMessageBox.information(
            self, "Exported",
            f"Boundary conditions exported to {target}"
        )
