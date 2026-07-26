from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QGridLayout, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from cfmesh_autogui.gui.design_tokens import COLOR_SURFACE, COLOR_BORDER
from cfmesh_autogui.gui.style import COLOR_ACCENT, COLOR_PASS, COLOR_TEXT_DIM

logger = logging.getLogger(__name__)

_CATEGORY_ICONS = {
    "internal_flow": "\U0001f4a7",
    "external_aero": "\u2708\ufe0f",
    "cht": "\U0001f525",
    "multiphase": "\U0001f30a",
    "moving_body": "\U0001f6e1\ufe0f",
    "conjugate_ht": "\U0001f321\ufe0f",
    "combustion": "\U0001f525",
}

_CATEGORY_LABELS = {
    "internal_flow": "Internal Flow",
    "external_aero": "External Aero",
    "cht": "Conjugate Heat Transfer",
    "multiphase": "Multi-Phase",
    "moving_body": "Moving Body",
    "conjugate_ht": "Conjugate HT",
    "combustion": "Combustion",
}


class TemplateCard(QFrame):
    clicked = Signal(object)

    def __init__(self, preset, parent=None):
        super().__init__(parent)
        self._preset = preset
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"TemplateCard {{ background:{COLOR_SURFACE}; border:1px solid {COLOR_BORDER}; "
            "border-radius:8px; padding:12px; }"
            "TemplateCard:hover { border:2px solid " + COLOR_ACCENT + "; }"
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        icon = _CATEGORY_ICONS.get(preset.metadata.category, "\U0001f4c4")
        self._icon_label = QLabel(icon)
        self._icon_label.setFont(QFont("Segoe UI", 24))
        layout.addWidget(self._icon_label)
        self._name_label = QLabel(preset.metadata.name)
        self._name_label.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        layout.addWidget(self._name_label)
        self._desc_label = QLabel(preset.metadata.description)
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(f"color:{COLOR_TEXT_DIM}; font-size:10px;")
        layout.addWidget(self._desc_label)
        self._detail_label = QLabel(
            f"{preset.metadata.solver} / {preset.metadata.turbulence}"
        )
        self._detail_label.setStyleSheet(f"color:{COLOR_PASS}; font-size:9px;")
        layout.addWidget(self._detail_label)
        layout.addStretch()

    def mousePressEvent(self, event):
        self.clicked.emit(self._preset)
        super().mousePressEvent(event)


class TemplateSelectorDialog(QDialog):
    template_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Case from Template")
        self.setMinimumSize(640, 500)

        layout = QVBoxLayout(self)
        header = QLabel("Choose a case template")
        header.setFont(QFont("Segoe UI", 16, QFont.DemiBold))
        layout.addWidget(header)

        sub = QLabel("Each template pre-configures geometry, mesh, BC, and solver for a specific case type.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{COLOR_TEXT_DIM};")
        layout.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        self._grid = QGridLayout(container)
        self._grid.setSpacing(12)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        self._populate_templates()

    def _populate_templates(self):
        from cfmesh_autogui.commercial.template_engine import TemplateEngine
        engine = TemplateEngine()
        templates = engine.list_templates()
        row = col = 0
        for preset in templates:
            card = TemplateCard(preset, self)
            card.clicked.connect(self._on_card_clicked)
            self._grid.addWidget(card, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1

    def _on_card_clicked(self, preset):
        self._selected = preset
        self.template_selected.emit(preset)
        self.accept()

    def get_selected_template(self):
        return getattr(self, "_selected", None)
