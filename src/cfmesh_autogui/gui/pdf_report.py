from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import (
    HexColor, white, black, grey,
)
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak,
)


COLOR_PASS = HexColor("#27ae60")
COLOR_WARN = HexColor("#f39c12")
COLOR_FAIL = HexColor("#e74c3c")


class MeshReportPDF:
    def __init__(self, case_dir: Path | str):
        self.case_dir = Path(case_dir)

    def generate(self, output_path: Path, quality_data: dict) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ReportTitle", parent=styles["Title"],
            fontSize=20, spaceAfter=12,
        )
        heading_style = ParagraphStyle(
            "ReportHeading", parent=styles["Heading2"],
            fontSize=14, spaceAfter=8,
        )
        normal = styles["Normal"]

        elements = []

        elements.append(Paragraph("Mesh Quality Report", title_style))
        elements.append(Paragraph(
            f"Case: {self.case_dir}", normal
        ))
        elements.append(Spacer(1, 12 * mm))

        metrics = quality_data.get("metrics", quality_data)
        rows = [["Metric", "Value", "Status"]]

        def _status(value, warn, fail):
            if value >= fail:
                return "FAIL"
            if value >= warn:
                return "WARN"
            return "PASS"

        nonortho = metrics.get("max_non_ortho", 0)
        rows.append([
            "Max Non-Orthogonality",
            f"{nonortho:.1f} deg",
            _status(nonortho, 65, 85),
        ])

        skew = metrics.get("max_skewness", 0)
        rows.append([
            "Max Skewness",
            f"{skew:.2f}",
            _status(skew, 4, 10),
        ])

        aspect = metrics.get("max_aspect_ratio", 0)
        rows.append([
            "Max Aspect Ratio",
            f"{aspect:.0f}",
            _status(aspect, 1000, 5000),
        ])

        cells = metrics.get("cells", 0)
        rows.append(["Cell Count", f"{cells:,}", ""])

        avg_nonortho = metrics.get("avg_non_ortho", 0)
        rows.append(["Avg Non-Orthogonality", f"{avg_nonortho:.1f} deg", ""])

        avg_skew = metrics.get("avg_skewness", 0)
        rows.append(["Avg Skewness", f"{avg_skew:.2f}", ""])

        neg_cells = metrics.get("neg_cells", 0)
        rows.append(["Negative Volume Cells", str(neg_cells),
                     "FAIL" if neg_cells > 0 else "PASS"])

        min_vol = metrics.get("min_volume", 0)
        rows.append(["Min Volume", f"{min_vol:.6e}", ""])

        table = Table(rows, colWidths=[140, 100, 60])
        _header_bg = HexColor("#2c3e50")
        _alt_bg = HexColor("#f5f6fa")
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), _header_bg),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, -1), 10),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, _alt_bg]),
        ]
        for i, row in enumerate(rows[1:], 1):
            status = row[2]
            if status == "PASS":
                style_cmds.append(("TEXTCOLOR", (2, i), (2, i), COLOR_PASS))
            elif status == "WARN":
                style_cmds.append(("TEXTCOLOR", (2, i), (2, i), COLOR_WARN))
            elif status == "FAIL":
                style_cmds.append(("TEXTCOLOR", (2, i), (2, i), COLOR_FAIL))

        table.setStyle(TableStyle(style_cmds))
        elements.append(table)

        elements.append(Spacer(1, 12 * mm))

        has_fail = any(row[2] == "FAIL" for row in rows[1:])
        if has_fail:
            elements.append(Paragraph(
                "<b>Verdict:</b> <font color='red'>FAIL</font> - "
                "One or more quality criteria are not met.",
                normal,
            ))
        else:
            has_warn = any(row[2] == "WARN" for row in rows[1:])
            if has_warn:
                elements.append(Paragraph(
                    "<b>Verdict:</b> <font color='orange'>WARNING</font> - "
                    "Mesh passes but has some warnings.",
                    normal,
                ))
            else:
                elements.append(Paragraph(
                    "<b>Verdict:</b> <font color='green'>PASS</font> - "
                    "All quality criteria met.",
                    normal,
                ))

        doc.build(elements)
        return output_path
