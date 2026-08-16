"""OODA live-progress panel — shown during adaptive mesh runs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar, QGridLayout
from PySide6.QtGui import QFont

from cfmesh_autogui.gui.style import COLOR_PASS, COLOR_DANGER
from cfmesh_autogui.gui.design_tokens import ORANGE_500


class OODAPanel(QFrame):
    """Live progress display for the OODA adaptive loop.

    Shows: current phase, iteration, live quality metrics, progress bar.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        title = QLabel("OODA Adaptive Loop")
        tf = QFont()
        tf.setBold(True)
        tf.setPointSize(10)
        title.setFont(tf)
        layout.addWidget(title)

        # Phase
        self._phase_label = QLabel("Phase: —")
        self._phase_label.setStyleSheet(f"color: {ORANGE_500};")
        layout.addWidget(self._phase_label)

        # Iteration
        self._iter_label = QLabel("Iteration: 0 / 0")
        layout.addWidget(self._iter_label)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)

        # Quality metrics grid
        grid = QGridLayout()
        grid.setSpacing(2)

        grid.addWidget(QLabel("Skewness:"), 0, 0)
        self._skew_label = QLabel("—")
        self._skew_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self._skew_label, 0, 1)

        grid.addWidget(QLabel("Non-ortho:"), 1, 0)
        self._northo_label = QLabel("—")
        self._northo_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self._northo_label, 1, 1)

        grid.addWidget(QLabel("Aspect ratio:"), 2, 0)
        self._aspect_label = QLabel("—")
        self._aspect_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self._aspect_label, 2, 1)

        grid.addWidget(QLabel("Cells:"), 3, 0)
        self._cells_label = QLabel("—")
        self._cells_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self._cells_label, 3, 1)

        layout.addLayout(grid)
        layout.addStretch()

        self.setVisible(False)

    def update_phase(self, phase: str, iteration: int, max_iter: int) -> None:
        self._phase_label.setText(f"Phase: {phase}")
        self._iter_label.setText(f"Iteration: {iteration} / {max_iter}")
        self._progress.setValue(int(iteration / max(max_iter, 1) * 100))

    def update_progress(self, message: str, fraction: float) -> None:
        self._phase_label.setText(f"Phase: {message}")
        self._progress.setValue(int(fraction * 100))

    def update_quality(
        self, skewness: float, non_ortho: float,
        aspect_ratio: float, cell_count: int,
    ) -> None:
        self._skew_label.setText(f"{skewness:.3f}")
        self._skew_label.setStyleSheet(
            f"color: {COLOR_PASS if skewness < 0.8 else COLOR_DANGER};"
        )
        self._northo_label.setText(f"{non_ortho:.1f}°")
        self._northo_label.setStyleSheet(
            f"color: {COLOR_PASS if non_ortho < 60 else COLOR_DANGER};"
        )
        self._aspect_label.setText(f"{aspect_ratio:.0f}")
        self._cells_label.setText(f"{cell_count:,}")

    def show_running(self) -> None:
        self.setVisible(True)
        self._progress.setValue(0)

    def show_done(self, success: bool) -> None:
        self._progress.setValue(100)
        color = COLOR_PASS if success else COLOR_DANGER
        self._phase_label.setStyleSheet(f"color: {color};")
        self._phase_label.setText("Done" if success else "FAILED")
        self._phase_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def reset(self) -> None:
        self._phase_label.setText("Phase: —")
        self._iter_label.setText("Iteration: 0 / 0")
        self._progress.setValue(0)
        self._skew_label.setText("—")
        self._northo_label.setText("—")
        self._aspect_label.setText("—")
        self._cells_label.setText("—")
        self._phase_label.setStyleSheet("")
        self.setVisible(False)
