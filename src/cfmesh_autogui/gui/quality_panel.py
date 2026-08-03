from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QPushButton, QDialog, QPlainTextEdit,
    QApplication, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Signal, Qt

from cfmesh_autogui.gui.style import (
    COLOR_PASS, COLOR_WARN, COLOR_FAIL, COLOR_BG_DIM,
    COLOR_TEXT_DIM, FS_METRIC, FS_BTN_TINY, status_pill, metric_label,
)
from cfmesh_autogui.gui.design_tokens import SUCCESS, WARNING, ERROR, NEUTRAL_500, NEUTRAL_400, FONT_SIZE_MD, FONT_SIZE_SM  # ✅ F-019

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "nonortho_warn": 65.0,
    "nonortho_fail": 85.0,
    "skew_warn": 4.0,
    "skew_fail": 10.0,
    "aspect_warn": 1000.0,
    "aspect_fail": 5000.0,
}


def _load_thresholds(s=None) -> dict[str, float]:
    if s is None:
        from PySide6.QtCore import QSettings
        s = QSettings("cfmesh-autogui", "CFMesh-AutoGUI")
    out: dict[str, float] = {}
    for key, default in DEFAULT_THRESHOLDS.items():
        try:
            val = s.value(f"quality/{key}", default, type=float)
            out[key] = float(val) if val is not None else default
        except (TypeError, ValueError):
            out[key] = default
    return out


COLOR_DIM = COLOR_BG_DIM


class QualityPanel(QFrame):
    fix_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(80)  # ✅ F-019
        self.setMaximumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._status_color = COLOR_DIM
        self._status_text = "\u2014"
        self._metrics = {}
        self._raw_log = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(2)

        top = QHBoxLayout()
        self._status_label = QLabel("Mesh Quality: \u2014")
        self._status_label.setStyleSheet(status_pill(COLOR_DIM))
        top.addWidget(self._status_label)
        top.addStretch()
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        self._nonortho_label = QLabel("non-ortho: \u2014")
        self._nonortho_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._skew_label = QLabel("skewness: \u2014")
        self._skew_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._aspect_label = QLabel("aspect ratio: \u2014")
        self._aspect_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._cells_label = QLabel("cells: \u2014")
        self._cells_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        bottom.addWidget(self._nonortho_label)
        bottom.addWidget(self._skew_label)
        bottom.addWidget(self._aspect_label)
        bottom.addWidget(self._cells_label)

        self._show_log_btn = QPushButton("Show Log")
        self._show_log_btn.setFixedSize(70, 20)
        self._show_log_btn.setStyleSheet(f"font-size: {FS_BTN_TINY}px;")
        self._show_log_btn.clicked.connect(self._on_show_log)
        self._show_log_btn.setVisible(False)
        bottom.addWidget(self._show_log_btn)

        self._export_pdf_btn = QPushButton("Export PDF")
        self._export_pdf_btn.setFixedSize(80, 20)
        self._export_pdf_btn.setStyleSheet(f"font-size: {FS_BTN_TINY}px;")
        self._export_pdf_btn.clicked.connect(self._on_export_pdf)
        self._export_pdf_btn.setVisible(False)
        bottom.addWidget(self._export_pdf_btn)

        self._auto_fix_btn = QPushButton("Auto-Fix Quality")
        self._auto_fix_btn.setFixedSize(100, 20)
        self._auto_fix_btn.setStyleSheet(f"font-size: {FS_BTN_TINY}px;")
        self._auto_fix_btn.clicked.connect(self._on_auto_fix)
        self._auto_fix_btn.setVisible(False)
        bottom.addWidget(self._auto_fix_btn)

        bottom.addStretch()
        layout.addLayout(bottom)

        self.hide()

    def _color_style(self, value: float, warn: float, fail: float) -> str:
        if fail > 0 and value >= fail:
            return COLOR_FAIL
        if warn > 0 and value >= warn:
            return COLOR_WARN
        return COLOR_PASS

    def show_report(self, report, thresholds: dict[str, float] | None = None):
        if thresholds is None:
            thresholds = _load_thresholds()
        self._raw_log = report.get("raw_output", "")
        self._show_log_btn.setVisible(True)
        self._export_pdf_btn.setVisible(True)
        self._auto_fix_btn.setVisible(True)
        metrics = report["metrics"]
        passed = report["passed"]
        status = report["status"]

        if passed:
            self._status_text = "PASS"
            self._status_color = COLOR_PASS
        elif "FAIL" in status:
            self._status_text = f"FAIL {status}"
            self._status_color = COLOR_FAIL
        else:
            self._status_text = f"WARN {status}"
            self._status_color = COLOR_WARN

        self._metrics = metrics
        self._status_label.setText(f"Mesh Quality: {self._status_text}")
        self._status_label.setStyleSheet(status_pill(self._status_color))

        nonortho = metrics.get("max_non_ortho", 0)
        c = self._color_style(nonortho, thresholds["nonortho_warn"], thresholds["nonortho_fail"])
        self._nonortho_label.setText(f"non-ortho: {nonortho:.1f}\u00b0")
        self._nonortho_label.setStyleSheet(metric_label(c, FS_METRIC))

        skew = metrics.get("max_skewness", 0)
        c = self._color_style(skew, thresholds["skew_warn"], thresholds["skew_fail"])
        self._skew_label.setText(f"skewness: {skew:.2f}")
        self._skew_label.setStyleSheet(metric_label(c, FS_METRIC))

        aspect = metrics.get("max_aspect_ratio", 0)
        c = self._color_style(aspect, thresholds["aspect_warn"], thresholds["aspect_fail"])
        self._aspect_label.setText(f"aspect ratio: {aspect:.0f}")
        self._aspect_label.setStyleSheet(metric_label(c, FS_METRIC))

        cells = metrics.get("cells", 0)
        self._cells_label.setText(f"cells: {cells:,}")

        self.show()

    def clear_report(self):
        self._status_label.setText("Mesh Quality: \u2014")
        self._status_label.setStyleSheet(status_pill(COLOR_DIM))
        self._nonortho_label.setText("non-ortho: \u2014")
        self._nonortho_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._skew_label.setText("skewness: \u2014")
        self._skew_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._aspect_label.setText("aspect ratio: \u2014")
        self._aspect_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._cells_label.setText("cells: \u2014")
        self._cells_label.setStyleSheet(metric_label(COLOR_TEXT_DIM, FS_METRIC))
        self._show_log_btn.setVisible(False)
        self._export_pdf_btn.setVisible(False)
        self._auto_fix_btn.setVisible(False)
        self.hide()

    def set_raw_log(self, text: str):
        self._raw_log = text
        self._show_log_btn.setVisible(True)

    def _on_show_log(self):
        dlg = LogViewerDialog(self._raw_log, self)
        dlg.exec()

    def _on_export_pdf(self):
        if not self._metrics:
            QMessageBox.information(self, "No Data", "No quality report to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF Report", "mesh_quality_report.pdf",
            "PDF (*.pdf)",
        )
        if not path:
            return
        # PDF generation parses the full raw log — run it off the UI thread.
        from cfmesh_autogui.gui.task_runner import FunctionWorker, TaskManager

        raw_log = self._raw_log or ""
        metrics = self._metrics

        def work(worker):
            from cfmesh_autogui.gui.pdf_report import MeshReportPDF
            case_hint = ""
            if raw_log:
                for line in raw_log.splitlines():
                    if "Case:" in line:
                        case_hint = line.split("Case:")[-1].strip()
                        break
            report_gen = MeshReportPDF(case_hint or ".")
            report_gen.generate(Path(path), metrics)
            return path

        def on_done(_name, out_path):
            self._status_label.setText("Mesh Quality: PDF exported")
            logger.info("Quality PDF exported: %s", out_path)

        def on_failed(_name, msg):
            logger.error("PDF export failed: %s", msg)
            QMessageBox.warning(self, "Export Failed", str(msg))

        tasks = getattr(self, "_tasks", None)
        if tasks is None:
            tasks = TaskManager(self)
            self._tasks = tasks
        tasks.submit("pdf_export", FunctionWorker(work), on_finished=on_done, on_failed=on_failed)

    def _on_auto_fix(self):
        self.fix_requested.emit("auto_fix")


class LogViewerDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("cartesianMesh Full Log")
        self.resize(800, 600)

        layout = QVBoxLayout(self)
        self._edit = QPlainTextEdit(self)
        self._edit.setReadOnly(True)
        from PySide6.QtGui import QFont
        monospace = QFont("Courier New", 9)
        self._edit.setFont(monospace)
        self._edit.setPlainText(text or "[Log was empty]")

        btn_layout = QHBoxLayout()
        btn_copy = QPushButton("Copy All")
        btn_copy.clicked.connect(self._copy_all)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_copy)
        btn_layout.addWidget(btn_close)

        layout.addWidget(self._edit)
        layout.addLayout(btn_layout)

    def _copy_all(self):
        QApplication.clipboard().setText(self._edit.toPlainText())
