"""Commercial-grade About dialog for PolyFoamMesh.

Shows app name, version, vendor, copyright, license, links, and a
credits section. Uses design tokens for all colors and spacing.
"""
from __future__ import annotations

import platform

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
)

from polyfoammesh.gui.design_tokens import (
    APP_NAME,
    APP_VERSION,
    APP_DESCRIPTION,
    APP_VENDOR,
    APP_COPYRIGHT,
    APP_LICENSE,
    APP_WEBSITE,
    PRIMARY_600,
    ORANGE_500,
    NEUTRAL_800,
    NEUTRAL_600,
    NEUTRAL_400,
)


def _make_logo(size: int = 96) -> QPixmap:
    """Render the app logo as a QPixmap (a stylised 'C' in a blue rounded square).

    No external image file required — fully vector via QPainter so the EXE
    has zero asset dependencies.
    """
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    # Background rounded square
    p.setBrush(QColor(PRIMARY_600))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.20, size * 0.20)
    # "C" letter
    p.setBrush(QColor("#ffffff"))
    p.setPen(Qt.NoPen)
    margin = size * 0.22
    p.drawRoundedRect(
        int(margin), int(margin), int(size - 2 * margin), int(size - 2 * margin),
        int(size * 0.35), int(size * 0.35),
    )
    # Cut a C shape by overlaying background
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.setBrush(Qt.transparent)
    p.setPen(Qt.NoPen)
    arc_margin = size * 0.34
    p.drawEllipse(
        int(arc_margin), int(arc_margin),
        int(size - 2 * arc_margin), int(size - 2 * arc_margin),
    )
    p.end()
    return pix


class AboutDialog(QDialog):
    """Commercial-grade About dialog — ANSYS Workbench inspired."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMaximumWidth(480)
        self.setStyleSheet("""
            QDialog { background:#f8f9fb; }
            QLabel { color:#1e293b; }
            QPushButton { min-width:80px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top accent bar (orange — ANSYS Workbench style)
        accent_bar = QFrame()
        accent_bar.setFixedHeight(3)
        accent_bar.setStyleSheet(f"background:{ORANGE_500}; border:none;")
        layout.addWidget(accent_bar)

        # Content area
        content = QVBoxLayout()
        content.setContentsMargins(24, 20, 24, 20)
        content.setSpacing(12)

        # Header: logo + name
        header = QHBoxLayout()
        header.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(_make_logo(80))
        logo.setFixedSize(80, 80)
        header.addWidget(logo, 0, Qt.AlignTop)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        name = QLabel(APP_NAME)
        name_font = QFont()
        name_font.setPointSize(20)
        name_font.setBold(True)
        name.setFont(name_font)
        title_box.addWidget(name)

        version = QLabel(f"Version {APP_VERSION}")
        ver_font = QFont()
        ver_font.setPointSize(10)
        version.setFont(ver_font)
        version.setStyleSheet(f"color: {NEUTRAL_600};")
        title_box.addWidget(version)

        desc = QLabel(APP_DESCRIPTION)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {NEUTRAL_800}; font-size:12px; margin-top:2px;")
        title_box.addWidget(desc)
        title_box.addStretch(1)
        header.addLayout(title_box, 1)
        content.addLayout(header)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#d4d8dd;")
        content.addWidget(sep)

        # Build info grid
        info_layout = QVBoxLayout()
        info_layout.setSpacing(3)
        info_layout.addWidget(self._kv("Vendor", APP_VENDOR))
        info_layout.addWidget(self._kv("License", APP_LICENSE))
        info_layout.addWidget(self._kv("Copyright", APP_COPYRIGHT))
        info_layout.addWidget(self._kv("Website", APP_WEBSITE))
        content.addLayout(info_layout)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color:#d4d8dd;")
        content.addWidget(sep2)

        # Tech stack
        tech = QLabel(
            "<b>Tech Stack:</b> PySide6 · pyVista · cadquery · "
            "trimesh · meshio · reportlab · OpenFOAM v2512"
        )
        tech.setWordWrap(True)
        tech.setStyleSheet(f"color: {NEUTRAL_600}; font-size:11px;")
        content.addWidget(tech)

        # System
        sys_info = QLabel(
            f"<b>Python:</b> {platform.python_version()} &nbsp;|&nbsp; "
            f"<b>Qt:</b> {self._qt_version()} &nbsp;|&nbsp; "
            f"<b>OS:</b> {platform.system()} {platform.release()}"
        )
        sys_info.setTextFormat(Qt.RichText)
        sys_info.setStyleSheet(f"color: {NEUTRAL_400}; font-size:10px;")
        content.addWidget(sys_info)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        content.addLayout(btn_row)

        layout.addLayout(content)

    @staticmethod
    def _qt_version() -> str:
        try:
            from PySide6 import __version__ as v
            return f"PySide6 {v}"
        except Exception:
            return "PySide6 (unknown)"

    @staticmethod
    def _kv(key: str, value: str) -> QLabel:
        lbl = QLabel(f"<b>{key}:</b> {value}")
        lbl.setTextFormat(Qt.RichText)
        lbl.setStyleSheet("color: #1e293b; font-size: 12px;")
        return lbl
