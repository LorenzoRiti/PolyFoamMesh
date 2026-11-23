#!/usr/bin/env python3
"""Generate the PolyFoamMesh technical report (English, print-ready).

The report is assembled with ReportLab; equations are rendered with
Matplotlib's STIX mathtext at 300 dpi and embedded as vector-like images.
Run from the repository root:

    python tools/gen_report_pdf_v2.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from PIL import Image as PILImage

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def _font_path(
    family: str,
    weight: str = "normal",
    style: Literal["normal", "italic", "oblique"] = "normal",
) -> str:
    return fm.findfont(fm.FontProperties(family=family, weight=weight, style=style))


_FONT_DIR = Path(_font_path("DejaVu Serif")).parent
pdfmetrics.registerFont(TTFont("Body", str(_FONT_DIR / "DejaVuSerif.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", str(_FONT_DIR / "DejaVuSerif-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Body-Italic", str(_FONT_DIR / "DejaVuSerif-Italic.ttf")))
pdfmetrics.registerFont(TTFont("Body-BoldItalic", str(_FONT_DIR / "DejaVuSerif-BoldItalic.ttf")))
pdfmetrics.registerFont(TTFont("Sans", str(_FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Sans-Bold", str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Sans-Oblique", str(_FONT_DIR / "DejaVuSans-Oblique.ttf")))
pdfmetrics.registerFont(TTFont("Mono", str(_FONT_DIR / "DejaVuSansMono.ttf")))
pdfmetrics.registerFont(TTFont("Mono-Bold", str(_FONT_DIR / "DejaVuSansMono-Bold.ttf")))
pdfmetrics.registerFontFamily(
    "Body", normal="Body", bold="Body-Bold", italic="Body-Italic",
    boldItalic="Body-BoldItalic",
)
pdfmetrics.registerFontFamily(
    "Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique",
)

# ---------------------------------------------------------------------------
# Palette and page geometry
# ---------------------------------------------------------------------------

NAVY = colors.HexColor("#14213d")
BLUE = colors.HexColor("#1d3557")
ACCENT = colors.HexColor("#2a6f97")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#cbd5e1")
LIGHT = colors.HexColor("#f3f4f6")
PALE_BLUE = colors.HexColor("#eef4fa")
PALE_GREEN = colors.HexColor("#eefaf3")
PALE_AMBER = colors.HexColor("#fff8eb")

PAGE_W, PAGE_H = A4
LEFT = 21 * mm
RIGHT = 21 * mm
TOP = 24 * mm
BOTTOM = 21 * mm
CONTENT_W = PAGE_W - LEFT - RIGHT

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

S = {}
S["title"] = ParagraphStyle(
    "Title", fontName="Sans-Bold", fontSize=23, leading=29, textColor=NAVY,
    alignment=TA_LEFT, spaceAfter=6,
)
S["subtitle"] = ParagraphStyle(
    "Subtitle", fontName="Body-Italic", fontSize=12.5, leading=18,
    textColor=BLUE, alignment=TA_LEFT, spaceAfter=10,
)
S["meta"] = ParagraphStyle(
    "Meta", fontName="Sans", fontSize=8.8, leading=12.5, textColor=MUTED,
    alignment=TA_LEFT,
)
S["H1"] = ParagraphStyle(
    "H1", fontName="Sans-Bold", fontSize=16.5, leading=21, textColor=NAVY,
    spaceBefore=16, spaceAfter=7, keepWithNext=True,
)
S["H2"] = ParagraphStyle(
    "H2", fontName="Sans-Bold", fontSize=11.6, leading=15.5, textColor=BLUE,
    spaceBefore=11, spaceAfter=4, keepWithNext=True,
)
S["H3"] = ParagraphStyle(
    "H3", fontName="Body-BoldItalic", fontSize=9.9, leading=13.5, textColor=INK,
    spaceBefore=7, spaceAfter=2, keepWithNext=True,
)
S["body"] = ParagraphStyle(
    "Body", fontName="Body", fontSize=9.35, leading=13.7, textColor=INK,
    alignment=TA_JUSTIFY, spaceAfter=5.2,
)
S["bodytight"] = ParagraphStyle(
    "BodyTight", parent=S["body"], spaceAfter=2.5,
)
S["bullet"] = ParagraphStyle(
    "Bullet", parent=S["body"], leftIndent=11, bulletIndent=2,
    spaceAfter=2.6, bulletFontName="Body", bulletFontSize=9.35,
)
S["quote"] = ParagraphStyle(
    "Quote", parent=S["body"], fontName="Body-Italic", leftIndent=10,
    rightIndent=10, textColor=colors.HexColor("#374151"), spaceBefore=4,
    spaceAfter=6,
)
S["caption"] = ParagraphStyle(
    "Caption", fontName="Sans", fontSize=8.1, leading=11.2, textColor=MUTED,
    alignment=TA_LEFT, spaceBefore=3, spaceAfter=8,
)
S["table"] = ParagraphStyle(
    "Table", fontName="Body", fontSize=7.8, leading=10.2, textColor=INK,
)
S["tablehead"] = ParagraphStyle(
    "TableHead", fontName="Sans-Bold", fontSize=7.6, leading=10,
    textColor=colors.white,
)
S["tableheadleft"] = ParagraphStyle(
    "TableHeadLeft", parent=S["tablehead"], alignment=TA_LEFT,
)
S["tableright"] = ParagraphStyle(
    "TableRight", parent=S["table"], alignment=TA_RIGHT,
)
S["code"] = ParagraphStyle(
    "Code", fontName="Mono", fontSize=7.4, leading=10.0, textColor=INK,
)
S["eqnum"] = ParagraphStyle(
    "EqNum", fontName="Body", fontSize=8.8, leading=11, textColor=MUTED,
    alignment=TA_RIGHT,
)
S["callout"] = ParagraphStyle(
    "Callout", fontName="Body", fontSize=8.55, leading=12.2, textColor=INK,
    alignment=TA_LEFT,
)
S["callouttitle"] = ParagraphStyle(
    "CalloutTitle", fontName="Sans-Bold", fontSize=8.4, leading=11,
    textColor=NAVY, spaceAfter=2,
)
S["toc0"] = ParagraphStyle(
    "TOC0", fontName="Sans-Bold", fontSize=9.6, leading=13.2, textColor=NAVY,
    spaceBefore=4,
)
S["toc1"] = ParagraphStyle(
    "TOC1", fontName="Body", fontSize=8.8, leading=11.1, textColor=INK,
    leftIndent=14,
)

# ---------------------------------------------------------------------------
# Equation rendering
# ---------------------------------------------------------------------------

plt.rcParams["mathtext.fontset"] = "stix"
plt.rcParams["font.family"] = "STIXGeneral"
plt.rcParams["mathtext.default"] = "regular"

_EQ_DIR = Path(tempfile.mkdtemp(prefix="pfm_eq_"))
_EQ_COUNTER = [0]


def _render_eq(tex: str, fontsize: float = 11.4,
               max_width: float = CONTENT_W) -> Image:
    fig = plt.figure(figsize=(0.01, 0.01))
    t = fig.text(0.0, 0.0, f"${tex}$", fontsize=fontsize, color="#111827")
    fig.canvas.draw()
    bbox = t.get_window_extent()
    w_in = bbox.width / fig.dpi
    h_in = bbox.height / fig.dpi
    pad = 0.05
    fw = max(w_in + 2 * pad, 0.2)
    fh = max(h_in + 2 * pad, 0.2)
    fig.set_size_inches(fw, fh)
    t.set_position((pad / fw, pad / fh))
    path = _EQ_DIR / f"eq_{_EQ_COUNTER[0]}.png"
    _EQ_COUNTER[0] += 1
    fig.savefig(
        path, dpi=300, transparent=False, facecolor="white",
        bbox_inches="tight", pad_inches=0.02,
    )
    plt.close(fig)
    with PILImage.open(path) as im:
        px_w, px_h = im.size
    w_pt = px_w / 300.0 * 72.0
    h_pt = px_h / 300.0 * 72.0
    if w_pt > max_width:
        scale = max_width / w_pt
        w_pt *= scale
        h_pt *= scale
    return Image(str(path), width=w_pt, height=h_pt)


def EQ(tex: str, number: str | None = None, fontsize: float = 11.4):
    max_w = CONTENT_W - (20 * mm if number else 0)
    img = _render_eq(tex, fontsize, max_w)
    if number is None:
        return _emit(KeepTogether([Spacer(1, 3), img, Spacer(1, 5)]))
    num = Paragraph(f"({number})", S["eqnum"])
    tbl = Table([[img, num]], colWidths=[CONTENT_W - 17 * mm, 17 * mm])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return _emit(KeepTogether([Spacer(1, 3), tbl, Spacer(1, 5)]))


def EQM(lines: list[str], number: str | None = None,
        fontsize: float = 11.2):
    max_w = CONTENT_W - (20 * mm if number else 0)
    imgs = [_render_eq(line, fontsize, max_w) for line in lines]
    inner = Table([[im] for im in imgs],
                  colWidths=[CONTENT_W - (17 * mm if number else 0)])
    inner.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    if number is None:
        return _emit(KeepTogether([Spacer(1, 3), inner, Spacer(1, 5)]))
    num = Paragraph(f"({number})", S["eqnum"])
    tbl = Table([[inner, num]], colWidths=[CONTENT_W - 17 * mm, 17 * mm])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return _emit(KeepTogether([Spacer(1, 3), tbl, Spacer(1, 5)]))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

AUTO = [False]


def _emit(flowable):
    if AUTO[0]:
        story.append(flowable)
    return flowable


def P(text: str, style: str = "body") -> Paragraph:
    return _emit(Paragraph(text, S[style]))


def H1(text: str) -> Paragraph:
    return _emit(Paragraph(text, S["H1"]))


def H2(text: str) -> Paragraph:
    return _emit(Paragraph(text, S["H2"]))


def H3(text: str) -> Paragraph:
    return _emit(Paragraph(text, S["H3"]))


def B(items: list[str], style: str = "bullet") -> list[Paragraph]:
    out = [Paragraph(f"•&nbsp;&nbsp;{x}", S[style]) for x in items]
    if AUTO[0]:
        story.extend(out)
    return out


def SP(h: float = 4) -> Spacer:
    return Spacer(1, h)


def CAPTION(text: str) -> Paragraph:
    return Paragraph(text, S["caption"])


def CALLOUT(title: str, body: str, kind: str = "note"):
    bg, bar = {
        "note": (PALE_BLUE, ACCENT),
        "measured": (PALE_GREEN, colors.HexColor("#047857")),
        "caveat": (PALE_AMBER, colors.HexColor("#b45309")),
    }[kind]
    inner = [
        Paragraph(title, S["callouttitle"]),
        Paragraph(body, S["callout"]),
    ]
    tbl = Table([[inner]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, bar),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return _emit(KeepTogether([Spacer(1, 4), tbl, Spacer(1, 6)]))


def CODE(text: str):
    pre = Preformatted(text, S["code"])
    tbl = Table([[pre]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return _emit(KeepTogether([Spacer(1, 3), tbl, Spacer(1, 6)]))


def TBL(rows: list[list], widths: list[float], header: bool = True,
        align_right: list[int] | None = None, font_size: float = 7.8,
        caption: str | None = None, repeat: bool = True):
    align_right = align_right or []
    cell_style = ParagraphStyle("cell", parent=S["table"], fontSize=font_size,
                                leading=font_size * 1.27)
    head_style = ParagraphStyle("head", parent=S["tablehead"],
                                fontSize=font_size - 0.1,
                                leading=font_size * 1.25)
    head_left = ParagraphStyle("headleft", parent=head_style,
                               alignment=TA_LEFT)
    data = []
    for r, row in enumerate(rows):
        out = []
        for c, cell in enumerate(row):
            if isinstance(cell, str):
                st = head_style if (header and r == 0) else cell_style
                if align_right and c in align_right and not (header and r == 0):
                    st = ParagraphStyle("cellr", parent=st, alignment=TA_RIGHT)
                if header and r == 0:
                    st = head_left
                out.append(Paragraph(cell, st))
            else:
                out.append(cell)
        data.append(out)
    tbl = Table(data, colWidths=widths, repeatRows=1 if (header and repeat) else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, NAVY),
        ]
        for r in range(1, len(data)):
            if r % 2 == 0:
                cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f8fafc")))
    tbl.setStyle(TableStyle(cmds))
    parts = [Spacer(1, 3), tbl]
    if caption:
        parts.append(CAPTION(caption))
    else:
        parts.append(Spacer(1, 5))
    return _emit(KeepTogether(parts))


# ---------------------------------------------------------------------------
# Document with header/footer and TOC
# ---------------------------------------------------------------------------

class ReportDoc(BaseDocTemplate):
    def __init__(self, filename: str, **kw):
        super().__init__(filename, **kw)
        body_frame = Frame(
            LEFT, BOTTOM, CONTENT_W, PAGE_H - TOP - BOTTOM,
            id="body", leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0,
        )
        title_frame = Frame(
            LEFT, BOTTOM, CONTENT_W, PAGE_H - TOP - BOTTOM,
            id="title", leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0,
        )
        self.addPageTemplates([
            PageTemplate(id="title", frames=[title_frame], onPage=self._title_page),
            PageTemplate(id="body", frames=[body_frame], onPage=self._body_page),
        ])

    def _title_page(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 9 * mm, PAGE_W, 9 * mm, stroke=0, fill=1)
        canvas.setFillColor(ACCENT)
        canvas.rect(0, PAGE_H - 10.2 * mm, PAGE_W, 1.2 * mm, stroke=0, fill=1)
        canvas.restoreState()

    def _body_page(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Sans", 7.2)
        canvas.setFillColor(MUTED)
        canvas.drawString(LEFT, PAGE_H - 13.5 * mm,
                          "PolyFoamMesh — polyhedral meshing for cell-centered FVM")
        canvas.drawRightString(PAGE_W - RIGHT, PAGE_H - 13.5 * mm,
                               "Technical Report · October 2026")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.35)
        canvas.line(LEFT, PAGE_H - 15.2 * mm, PAGE_W - RIGHT, PAGE_H - 15.2 * mm)
        canvas.setFont("Sans", 7.6)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PAGE_W / 2, 11 * mm, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            if flowable.style.name == "H1":
                self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))
            elif flowable.style.name == "H2":
                self.notify("TOCEntry", (1, flowable.getPlainText(), self.page))


# ---------------------------------------------------------------------------
# Story
# ---------------------------------------------------------------------------

story: list = []

# ============================== TITLE PAGE ================================

story += [
    NextPageTemplate("title"),
    SP(26),
    P("TECHNICAL REPORT", "meta"),
    SP(10),
    P("PolyFoamMesh", "title"),
    P("Polyhedral Mesh Generation, Geometric Invariants, and Quality Control "
      "for Cell-Centered Finite Volumes", "subtitle"),
    SP(14),
    Table([[P(
        "A technical report on the barycentric-dual mesher, its boundary-layer "
        "extrusion, and the geometric hypotheses behind elliptic FVM "
        "convergence. Prepared for discussion with researchers in the "
        "numerical analysis of finite volume methods.", "body")]],
        colWidths=[CONTENT_W - 34 * mm],
        style=TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])),
    SP(26),
    P("Version 2.3.0 &nbsp;·&nbsp; October 2026", "meta"),
    SP(4),
    P("Lorenzo Riti &nbsp;·&nbsp; design and development of PolyFoamMesh",
      "meta"),
    SP(4),
    P("Internal technical documentation", "meta"),
    SP(18),
    Table([[P(
        "<b>Scope and status.</b> This document describes the algorithms as "
        "implemented and the behavior measured in the project's benchmark "
        "logs. It is not a peer-reviewed numerical analysis and it does not "
        "claim a convergence proof. Where a statement is a theorem, a "
        "derivation, a measurement, or an expectation, the text says so "
        "explicitly. The measurements were obtained with OpenFOAM 2512 on "
        "WSL2; they are reported with their provenance and, where relevant, "
        "with the mismatches that remain unresolved.", "callout")]],
        colWidths=[CONTENT_W - 20 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
            ("LINEBEFORE", (0, 0), (0, -1), 2.6, ACCENT),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])),
    NextPageTemplate("body"),
    PageBreak(),
]

# ============================== ABSTRACT ==================================

story += [
    H1("Abstract"),
    P(
        "PolyFoamMesh is a desktop mesh generator for OpenFOAM that converts "
        "an imported CAD model into a tetrahedral mesh, rebuilds it as a "
        "polyhedral mesh through the barycentric dual of the tetrahedral "
        "complex, optionally repairs the result, and extrudes a conforming "
        "boundary layer of prismatic cells. This report documents the "
        "numerical and geometric core of that pipeline. It is written for a "
        "reader interested in why the construction is geometrically valid and "
        "which properties of the resulting mesh are relevant to the "
        "convergence of cell-centered finite volume methods for elliptic "
        "equations."
    ),
    P(
        "The central construction is exact in a precise sense: every primal "
        "tetrahedron is consumed, so polyhedral coverage is 100% by "
        "construction; every internal face is dual to a primal edge and is "
        "shared by exactly two cells; and the default boundary is an exact "
        "subdivision of the primal boundary triangulation into three planar "
        "quads per triangle. Consequently the total volume is conserved to "
        "machine precision (measured drift 0.0–1.1×10<super>−16</super>), the "
        "mesh is watertight, and orientation is determined by the primal "
        "edge, not by a winding heuristic. The cell count falls by roughly a "
        "factor of 5.5–6 relative to the tetrahedral mesh. A second mode, "
        "which reproduces the one-boundary-polygon-per-vertex topology of "
        "OpenFOAM's <i>polyDualMesh</i>, deliberately gives up exact tiling "
        "in exchange for approximately three times fewer boundary faces; its "
        "surface offset is second order in the mesh size (measured: "
        "−12.2% at three cells across a sphere, −1.3% at ten)."
    ),
    P(
        "The report then derives the geometric metrics that the mesher "
        "computes and controls: face area vectors and centroids through the "
        "Newell/centre-point decomposition; cell volumes and centroids "
        "through a signed pyramid decomposition; the face-pyramid test and "
        "its relation to star-shapedness; non-orthogonality; OpenFOAM's "
        "skewness normalization; and the directional/compactness aspect "
        "ratio. It shows which of these identities are exact for planar "
        "faces, which remain exact for non-planar faces when the validation "
        "uses a fan triangulation, and which are necessarily numerical "
        "proxies. The repair loops are described as constrained, keep-best "
        "processes: weighted Laplacian smoothing of interior vertices, a "
        "continuous penalty for tie-breaking, a concave-cell merge with a "
        "virtual-union test, and measured negative results for feature "
        "splitting. The boundary layer is presented as a conforming prism "
        "stack driven by per-vertex normals, a geometric height series, a "
        "gradient limiter, a constrained harmonic reflow of the interior, "
        "and an exact global parity solve for face windings."
    ),
    P(
        "Finally, the report connects these constructions to the theory of "
        "cell-centered finite volumes. It recalls the two-point flux and the "
        "over-relaxed transmissibility, derives the non-orthogonal "
        "correction and its consistency error, states the M-matrix and "
        "coercivity conditions, and lists the geometric quantities that "
        "appear in convergence hypotheses for polyhedral meshes. The "
        "measured evidence is presented without embellishment: a reference "
        "block with a cylindrical bore reaches <i>Mesh OK</i> with 100% "
        "polyhedral cells, while an industrial valve with concave feature "
        "edges fails three checkMesh checks and is a natural candidate for "
        "testing whether star-shapedness is necessary or only sufficient "
        "for convergence. The report closes with a concrete experimental "
        "program that PolyFoamMesh can support: exported mesh certificates, "
        "manufactured-solution convergence studies, controlled mesh "
        "families, and tests of non-planar faces and anisotropic boundary "
        "layers."
    ),
    SP(4),
    P(
        "<b>Keywords:</b> polyhedral mesh; barycentric dual; cell-centered "
        "finite volume; two-point flux approximation; non-orthogonality; "
        "skewness; star-shaped cell; boundary layer; OpenFOAM; cfMesh.",
        "bodytight",
    ),
    PageBreak(),
]

# ============================== TOC =======================================

toc = TableOfContents()
toc.levelStyles = [S["toc0"], S["toc1"]]
toc.dotsMinLevel = 0
story += [
    H1("Contents"),
    SP(2),
    toc,
    PageBreak(),
]

# ============================== PART I ====================================

story += [
    H1("Part I — Mathematical setting"),
    H1("1. Introduction and scope"),
    H2("1.1 The problem this report addresses"),
    P(
        "A cell-centered finite volume (FV) method stores one unknown per "
        "cell and balances fluxes across the faces of a polyhedral mesh. On "
        "an orthogonal mesh of convex cells the construction is classical "
        "and the two-point flux is consistent. On a general polyhedral mesh "
        "the cell centers are not aligned with face normals, faces may be "
        "non-planar, and cells may be non-convex. These are not cosmetic "
        "defects: they change the consistency error of the flux, the "
        "M-matrix structure of the discrete operator, and the constant in "
        "the discrete Poincaré inequality. A mesh generator that claims to "
        "produce polyhedral meshes for FV solvers should therefore state "
        "which geometric properties it guarantees, which it merely measures, "
        "and which it cannot guarantee at all."
    ),
    P(
        "This report is organized around that distinction. Part I fixes the "
        "notation and recalls the FV identities that the rest of the "
        "document uses. Part II describes the PolyFoamMesh pipeline and its "
        "algorithms. Part III states the geometric guarantees that can be "
        "proved for the construction, presents the measured evidence, and "
        "connects the mesh metrics to the convergence hypotheses used in "
        "the numerical analysis of elliptic FV schemes."
    ),
    H2("1.2 What is and is not claimed"),
    P(
        "The report distinguishes four kinds of statement:"
    ),
    *B([
        "<b>Theorems and propositions</b> about the construction, with "
        "proofs or proof sketches. These are independent of the quality of "
        "the input mesh, except where explicitly stated.",
        "<b>Derivations</b> of the metric formulas and of the flux "
        "decomposition. These show why the implemented formula has the form "
        "it has, not that the mesh is good.",
        "<b>Measurements</b> from the project's benchmark logs. They are "
        "reported with the case, the mesh size, and the OpenFOAM version "
        "where available. They are not independently reproduced for this "
        "report.",
        "<b>Expectations and open questions</b> about convergence. These are "
        "flagged as such and are intended as a basis for discussion, not as "
        "results.",
    ]),
    P(
        "In particular, the report does not claim that PolyFoamMesh meshes "
        "satisfy any particular convergence theorem, including the "
        "polyhedral mesh condition currently being developed by the "
        "reader's group. It does claim that the mesher computes, and can "
        "export, the geometric quantities that such a condition is likely "
        "to involve, and that it can generate controlled mesh families on "
        "which the condition can be tested."
    ),
    H2("1.3 Provenance of the numbers"),
    P(
        "The measured values quoted in this report come from the project's "
        "internal benchmark records and checkMesh logs. Unless stated "
        "otherwise, checkMesh means OpenFOAM 2512 running under WSL2, and "
        "the comparison is against the same primal tetrahedral mesh, not "
        "against a re-meshed variant. Some numbers were collected on "
        "different revisions of the converter; the text says which variant "
        "a table refers to. Where the in-process replica of checkMesh and "
        "the real checkMesh disagree, both values are reported. This is "
        "deliberate: a technical report that hides its unresolved "
        "discrepancies is less useful than one that exposes them."
    ),
    CALLOUT(
        "A note on terminology",
        "In this report, <i>dual</i> means the cell complex combinatorially "
        "dual to the primal tetrahedral mesh: one dual cell per primal "
        "vertex, one dual face per primal edge, one dual edge per primal "
        "triangle. <i>Median dual</i> and <i>barycentric dual</i> refer to "
        "the geometric realization whose vertices are primal tet centroids "
        "and, optionally, primal face centroids and edge midpoints. The "
        "production converter uses the centroid-ring realization by "
        "default; the fully median realization is an option. The "
        "<i>collapse mode</i> is a different boundary walk that emits one "
        "boundary polygon per primal boundary vertex, following "
        "polyDualMesh semantics.",
        "note",
    ),

    H2("1.4 Scope, reading guide, and the code snapshot"),
    P(
        "This report documents the numerical-geometric core of "
        "PolyFoamMesh: the path from a watertight surface to an OpenFOAM "
        "polyMesh, through the default Gmsh tet stage or the experimental "
        "fTetWild stage, the barycentric dual (for tetrahedral and "
        "hex/cut-cell input), the in-process checkMesh replica, the repair "
        "loops, and the boundary-layer extrusion, together with the "
        "geometric quantities that cell-centered FV theory uses. Every "
        "construction described is the one implemented; the report is not a "
        "survey of alternative meshing methods."
    ),
    P(
        "The following are deliberately out of scope: the PySide6 GUI, the "
        "cfMesh and cartesianMesh backends, the adaptive/SAMR loop, solver "
        "setup and post-processing, the commercial wrappers, and the "
        "STAR-CCM+-style tetrahedron aggregator. They are engineering "
        "surfaces around the core (the aggregator is an alternative path, "
        "not the default dual). Appendix C lists the modules that implement "
        "the core and Appendix E the regression coverage."
    ),
    P(
        "The code snapshot is the working tree at Version 2.3.0 (October "
        "2026), including the changes recorded under Unreleased; the last "
        "GitHub release is v2.3.0. Development is active; defaults and "
        "module behavior can change. Appendix C is the map to the code, and "
        "the exact value should be checked there before it is used in a "
        "production configuration."
    ),
    TBL([
        ["Reader", "Recommended path"],
        ["Mathematician (FVM, convergence)",
         "Sections 2, 4, 8, 10; notation in Appendix F; open questions in 10.7"],
        ["Physicist / CFD engineer",
         "Sections 3–7, 9; pipeline, metrics, repair loops, boundary layer, measured quality"],
        ["Reproducing the study",
         "Sections 5.8, 9, 11; Appendices C and E"],
        ["Experimental stage",
         "Sections 3.6, 7.8; Appendices D–E"],
    ], [52 * mm, 116 * mm],
       caption="Table 1.1. Reading guide. The mathematical and the "
               "engineering parts can be read independently."),
    H1("2. Polyhedral meshes and discrete operators"),
    H2("2.1 Cells, faces, and the owner/neighbour convention"),
    P(
        "Let Ω ⊂ ℝ<super>3</super> be a bounded connected polyhedral domain. "
        "A polyhedral mesh is a finite cell complex M = (K, F, E, V) "
        "consisting of closed cells K, faces σ, edges e, "
        "and vertices v such that the union of the cells is Ω, the interiors "
        "of distinct cells are disjoint, and the intersection of two cells "
        "is either empty, a common face, a common edge, or a common vertex. "
        "Each face is a polygon; in the intended application it is either "
        "planar or represented by a planar approximation. An internal face "
        "σ is shared by exactly two cells; a boundary face belongs to one "
        "cell and to ∂Ω."
    ),
    P(
        "OpenFOAM stores each face once, with an <i>owner</i> cell and, for "
        "an internal face, a <i>neighbour</i> cell. The area vector S<sub>σ</sub> "
        "is defined by the right-hand rule from the face vertex ordering and "
        "is oriented from the owner to the neighbour for an internal face, "
        "and outward from the domain for a boundary face. The convention "
        "owner &lt; neighbour is used throughout; it makes the internal "
        "face block upper-triangular and simplifies the assembly. The face "
        "centroid x<sub>σ</sub> and the cell centroid x<sub>K</sub> are "
        "computed by the formulas in Section 5."
    ),
    P(
        "For an internal face σ = K|L we write"
    ),
    EQ(r"d_\sigma = x_L - x_K, \qquad n_\sigma = \frac{S_\sigma}{|S_\sigma|}, "
       r"\qquad \cos\theta_\sigma = \frac{d_\sigma\cdot n_\sigma}{|d_\sigma|}.",
       "2.1"),
    P(
        "The angle θ<sub>σ</sub> ∈ [0, π] is the <i>non-orthogonality</i> of "
        "the face: θ<sub>σ</sub> = 0 means that the line joining the two "
        "cell centers is normal to the face. For a boundary face, d<sub>σ</sub> "
        "is usually taken as x<sub>σ</sub> − x<sub>K</sub>, and the "
        "non-orthogonality is not used by checkMesh; the boundary skewness "
        "has its own normalization (Section 5.5)."
    ),
    H2("2.2 Geometric identities"),
    P(
        "The following identities hold for every closed polyhedral cell with "
        "outward area vectors. They are the reason the dual construction can "
        "be validated without reference to the primal mesh."
    ),
    P("<b>Closure.</b> The area vectors of the faces of a cell sum to zero:"),
    EQ(r"\sum_{\sigma\in\mathcal{F}_K} S_\sigma = 0.", "2.2"),
    P(
        "This is the divergence theorem applied to the constant vector field. "
        "It is exact for any closed surface, planar or not, provided S<sub>σ</sub> "
        "is the area vector of the triangulated face. The mesher checks the "
        "relative closure residual"
    ),
    EQ(r"\varepsilon_K = \frac{\left\|\sum_{\sigma\in\mathcal{F}_K} S_\sigma\right\|}"
       r"{\sum_{\sigma\in\mathcal{F}_K}|S_\sigma|}", "2.3"),
    P(
        "and requires ε<sub>K</sub> &lt; 10<super>−8</super> for every cell. "
        "This is a numerical certificate of watertightness: an open cell, a "
        "lost face, or a duplicated face changes the sum at the level of the "
        "missing area, not at the level of round-off."
    ),
    P("<b>Volume.</b> For a closed polyhedral surface with planar faces,"),
    EQ(r"|K| = \frac{1}{3}\sum_{\sigma\in\mathcal{F}_K} S_\sigma\cdot x_\sigma,"
       r"\qquad x_K = \frac{1}{4|K|}\sum_{\sigma\in\mathcal{F}_K} "
       r"(S_\sigma\cdot x_\sigma)\,x_\sigma.", "2.4"),
    P(
        "The first identity is the divergence theorem applied to the vector "
        "field x; the second is the same theorem applied to x ⊗ x. Both are "
        "independent of the choice of origin. They remain exact for a "
        "non-planar face if the face is triangulated and S<sub>σ</sub>, "
        "x<sub>σ</sub> are replaced by the sum over its triangles of the "
        "triangle area vector and the triangle centroid weighted by "
        "S<sub>t</sub>·x<sub>t</sub>. This is the form used by the exact "
        "volume routine in the code."
    ),
    P("<b>Pyramid decomposition.</b> Choose a point x<sub>0</sub> (not "
      "necessarily inside K) and define the signed pyramid volume over face σ as"),
    EQ(r"V_\sigma(x_0) = \frac{1}{3}\,S_\sigma\cdot(x_\sigma-x_0),"
       r"\qquad c_\sigma(x_0) = \frac{3}{4}x_\sigma+\frac{1}{4}x_0.", "2.5"),
    P(
        "Then, for planar faces,"
    ),
    EQ(r"\sum_{\sigma\in\mathcal{F}_K} V_\sigma(x_0) = |K|, \qquad "
       r"x_K = \frac{\sum_\sigma V_\sigma(x_0)\,c_\sigma(x_0)}"
       r"{\sum_\sigma V_\sigma(x_0)}.", "2.6"),
    P(
        "The identities are exact for any x<sub>0</sub>; the individual "
        "V<sub>σ</sub> may be negative if x<sub>0</sub> is outside the "
        "kernel of K. This distinction matters: the <i>volume and centroid</i> "
        "formulas are exact for any closed polyhedron with planar faces, "
        "while the <i>positivity</i> of every V<sub>σ</sub> is a statement "
        "about star-shapedness, not about volume correctness. Section 5.3 "
        "exploits this distinction."
    ),
    H2("2.3 The model elliptic problem"),
    P(
        "The reference problem is the diffusion equation with a symmetric "
        "positive definite tensor κ ∈ ℝ<super>3×3</super>,"
    ),
    EQ(r"-\nabla\cdot(\kappa\nabla u) = f \ \ \mathrm{in}\ \Omega, \qquad "
       r"u = g_D \ \ \mathrm{on}\ \Gamma_D, \qquad "
       r"-\kappa\nabla u\cdot n = g_N \ \ \mathrm{on}\ \Gamma_N,", "2.7"),
    P(
        "with Γ<sub>D</sub> ∪ Γ<sub>N</sub> = ∂Ω. The weak form is: find "
        "u ∈ H<super>1</super><sub>D</sub>(Ω) such that"
    ),
    EQ(r"\int_\Omega \kappa\nabla u\cdot\nabla v\,dx = \int_\Omega f v\,dx "
       r"+ \int_{\Gamma_N} g_N v\,ds \quad \forall v\in H^1_{D,0}(\Omega).",
       "2.8"),
    P(
        "For κ uniformly positive definite and f ∈ L<super>2</super>(Ω), the "
        "problem is well posed by the Lax–Milgram lemma. The discrete "
        "problem is well posed if the discrete bilinear form is coercive on "
        "the discrete space and the right-hand side is bounded. The mesh "
        "enters through the coercivity constant, the consistency error, and "
        "the discrete Poincaré constant."
    ),
    P(
        "A remark on the tensor case, since the flux analysis of the next "
        "subsections is written for scalar κ. The over-relaxed "
        "transmissibility of Section 2.5 generalizes with the normal "
        "component κ<sub>n,σ</sub> = n<sub>σ</sub>·κn<sub>σ</sub> in place "
        "of κ: the two-point coefficient becomes "
        "|S<sub>σ</sub>| κ<sub>n,σ</sub>/(d<sub>σ</sub>·n<sub>σ</sub>), "
        "and the remaining part of κn<sub>σ</sub> — its component "
        "tangential to the face — is what the non-orthogonal correction "
        "must carry. That term is a cross-diffusion: it couples the "
        "tangential gradient of u to the normal flux, a coupling that a "
        "scalar κ never produces. Non-orthogonality and anisotropy "
        "therefore compound in the correction rather than in the sign: "
        "the implicit part keeps the sign condition θ &lt; 90° as long as "
        "κ is positive definite, but the correction grows with the "
        "geometric tilt and with the tangential part of κn<sub>σ</sub> at "
        "the same time. Whether a κ-weighted formulation — one that "
        "measures the angle between d<sub>σ</sub> and κn<sub>σ</sub> "
        "instead of between d<sub>σ</sub> and n<sub>σ</sub> — gives a "
        "better condition is an open question; the mesher measures the "
        "geometric θ with κ = I, because it does not see the equation, "
        "and Section 10.7 returns to it."
    ),
    H2("2.4 Cell-centered finite volumes"),
    P(
        "Integrating (2.7) over a cell K and applying the divergence theorem "
        "gives the exact balance"
    ),
    EQ(r"\sum_{\sigma\in\mathcal{F}_K} F_{K,\sigma} = \int_K f\,dx, \qquad "
       r"F_{K,\sigma} = -\int_\sigma \kappa\nabla u\cdot n_\sigma\,ds,", "2.9"),
    P(
        "where n<sub>σ</sub> is the outward normal of K on σ. For an "
        "internal face shared with L, the two one-sided fluxes satisfy "
        "F<sub>K,σ</sub> + F<sub>L,σ</sub> = 0, so the scheme is conservative "
        "by construction. The discrete unknown u<sub>K</sub> is associated "
        "with x<sub>K</sub>; the face flux must be expressed in terms of "
        "u<sub>K</sub>, u<sub>L</sub>, and possibly a reconstructed "
        "gradient."
    ),
    P(
        "For a scalar κ and a face σ, the exact flux is "
        "F<sub>K,σ</sub> = −κ|S<sub>σ</sub>| n<sub>σ</sub>·∇u. The "
        "two-point approximation replaces the normal derivative by the "
        "difference of the two cell values divided by a normal distance "
        "extracted from d<sub>σ</sub>. The resulting transmissibility is "
        "the object of the next subsection."
    ),
    H2("2.5 The over-relaxed two-point flux"),
    P(
        "Let θ<sub>σ</sub> be the angle between d<sub>σ</sub> and "
        "n<sub>σ</sub>, and decompose the center-to-center vector into a "
        "normal part and a tangential remainder:"
    ),
    EQ(r"d_\sigma = d_{n,\sigma}\,n_\sigma + d_{t,\sigma}, \qquad "
       r"d_{n,\sigma} = d_\sigma\cdot n_\sigma = |d_\sigma|\cos\theta_\sigma, "
       r"\qquad d_{t,\sigma}\cdot n_\sigma = 0.", "2.10"),
    P(
        "Since u<sub>L</sub> − u<sub>K</sub> = ∇u·d<sub>σ</sub> to the "
        "order of the approximation, the normal derivative can be split "
        "exactly as"
    ),
    EQ(r"\nabla u\cdot n_\sigma = \frac{u_L-u_K}{d_{n,\sigma}} "
       r"- \frac{\nabla u\cdot d_{t,\sigma}}{d_{n,\sigma}}.", "2.11"),
    P(
        "Approximating the face gradient in the last term by a "
        "reconstruction ∇u<sub>σ</sub> and multiplying by "
        "−κ|S<sub>σ</sub>| gives"
    ),
    EQ(r"F_{K,\sigma} \approx -\kappa\,|S_\sigma|\left["
       r"\frac{u_L-u_K}{d_{n,\sigma}} - "
       r"\frac{\nabla u_\sigma\cdot d_{t,\sigma}}{d_{n,\sigma}}\right].",
       "2.12"),
    P("The first term defines the <i>over-relaxed transmissibility</i>"),
    EQ(r"\tau_\sigma = \frac{|S_\sigma|}{d_{n,\sigma}} "
       r"= \frac{|S_\sigma|}{d_\sigma\cdot n_\sigma} "
       r"= \frac{|S_\sigma|}{|d_\sigma|\cos\theta_\sigma}.", "2.13"),
    P(
        "The name is standard: τ<sub>σ</sub> is larger than the orthogonal "
        "value |S<sub>σ</sub>|/|d<sub>σ</sub>| by the factor 1/cos θ<sub>σ</sub>, "
        "which compensates for the fact that the two-point difference is "
        "divided by the normal distance between the cells rather than by "
        "the length of the center-to-center line. The second term in (2.12) "
        "is the <i>non-orthogonal correction</i>. Neglecting it gives the "
        "two-point flux approximation (TPFA)."
    ),
    P("The discrete balance for cell K is therefore"),
    EQ(r"\sum_{\sigma\in\mathcal{F}_K} \kappa\,\tau_\sigma\,(u_K-u_L) "
       r"= |K|f_K + \mathrm{boundary\ terms} "
       r"+ \mathrm{correction\ terms}.", "2.14"),
    P(
        "The matrix of the two-point part has diagonal entries "
        "Σ<sub>σ</sub> κτ<sub>σ</sub> and off-diagonal entries −κτ<sub>σ</sub>. "
        "It is a symmetric M-matrix if every τ<sub>σ</sub> is positive, i.e. "
        "if cos θ<sub>σ</sub> &gt; 0, or θ<sub>σ</sub> &lt; 90°. This is the "
        "mathematical reason for the practical non-orthogonality limits: "
        "checkMesh warns above 70°, and the PolyFoamMesh smoother targets "
        "65°, both as safety margins below the M-matrix boundary. With a "
        "tensor κ the criterion is unchanged — n<sub>σ</sub>·κn<sub>σ</sub> "
        "is positive for a positive-definite κ, so τ<sub>σ</sub> keeps the "
        "sign of d<sub>σ</sub>·n<sub>σ</sub> — but the correction term "
        "grows, as the remark closing Section 2.3 notes."
    ),
    H2("2.6 Consistency and the non-orthogonal correction"),
    P(
        "The truncation error of the two-point part is the omitted term in "
        "(2.12). If the face gradient ∇u<sub>σ</sub> is available, the "
        "corrected flux is"
    ),
    EQ(r"F_{K,\sigma} = -\kappa\,\tau_\sigma\,(u_L-u_K) "
       r"+ \kappa\,|S_\sigma|\,"
       r"\frac{\nabla u_\sigma\cdot d_{t,\sigma}}{d_{n,\sigma}}.", "2.15"),
    P(
        "The consistency of the corrected scheme depends on the accuracy of "
        "∇u<sub>σ</sub>. If ∇u<sub>σ</sub> is reconstructed to O(h<super>p</super>) "
        "from the cell values, the correction error is "
        "O(|S<sub>σ</sub>| |d<sub>t,σ</sub>| h<super>p</super>/"
        "|d<sub>n,σ</sub>|) = O(h<super>2+p</super> tan θ<sub>σ</sub>). "
        "Dividing by the cell volume |K| = O(h<super>3</super>) gives a "
        "consistency error of order h<super>p−1</super> tan θ<sub>σ</sub>. "
        "Thus:"
    ),
    *B([
        "For <b>orthogonal</b> faces (θ<sub>σ</sub> = 0), the two-point "
        "scheme is consistent and second-order accurate for smooth solutions "
        "with a second-order flux approximation; the classical TPFA error is "
        "O(h) in the discrete H<super>1</super> norm and O(h<super>2</super>) "
        "in L<super>2</super> under additional regularity.",
        "For <b>non-orthogonal</b> faces with θ<sub>σ</sub> bounded away from "
        "zero, the uncorrected scheme has a flux error proportional to "
        "tan θ<sub>σ</sub> times the tangential gradient. It does not vanish "
        "under refinement unless θ<sub>σ</sub> → 0; the scheme is then "
        "inconsistent, even though it may be stable.",
        "With a <b>first-order</b> face gradient (p = 1), the corrected "
        "residual is O(tan θ<sub>σ</sub>); with a <b>second-order</b> "
        "reconstruction (p = 2), it is O(h tan θ<sub>σ</sub>). A consistent "
        "first-order scheme on a general non-orthogonal mesh therefore needs "
        "a second-order face-gradient reconstruction, or a discretization "
        "whose consistency does not rely on the two-point decomposition "
        "(mixed finite elements, mimetic finite differences, gradient "
        "discretization methods, or multi-point flux approximations).",
    ]),
    P(
        "This is the precise sense in which non-orthogonality is a numerical "
        "issue and not a cosmetic one. It is also why the report treats the "
        "non-orthogonality distribution, not only its maximum, as a "
        "first-class mesh property."
    ),
    CALLOUT(
        "What the mesher does and does not control",
        "PolyFoamMesh controls the <i>mesh</i>: it measures θ<sub>σ</sub>, "
        "the skewness, the pyramid margins, and the closure residual, and it "
        "moves interior vertices to reduce them. It does not choose the "
        "flux discretization: the OpenFOAM solver applies its own "
        "non-orthogonal correction. A mesh with a small maximum θ but a "
        "poorly reconstructed gradient can still produce a poor solution; a "
        "mesh with a few extreme θ faces can still converge if those faces "
        "carry negligible flux. The report keeps these two layers separate.",
        "note",
    ),
]
AUTO[0] = True

H1("Part II — The PolyFoamMesh pipeline"),

H1("3. From CAD to a primal tetrahedral mesh"),
H2("3.1 Pipeline overview"),
P(
    "The production path has six stages. Each stage is independent enough "
    "to be inspected and, where necessary, replaced:"
),
*B([
    "<b>Surface ingestion.</b> A STEP or STL model is imported, healed, "
    "and split into named patches. The surface is the reference geometry: "
    "the mesher never clips a dual cell against it, and the boundary of the "
    "final mesh is required to reproduce it.",
    "<b>Primal tetrahedralization.</b> The healed surface is meshed with "
    "Gmsh (the primary backend) or with fTetWild (an optional robust "
    "backend). Gmsh can also bake a prismatic boundary layer into the "
    "primal mesh; the dual converter accepts such a mixed tet/prism input.",
    "<b>OpenFOAM conversion.</b> The primal mesh is written as an "
    "OpenFOAM <i>polyMesh</i>: points, faces, owner, neighbour, and "
    "boundary patches. Patch names and feature edges are preserved.",
    "<b>Dual rebuild.</b> The tetrahedral complex is replaced by its "
    "barycentric/centroid dual. One output cell is created per primal "
    "vertex, one internal face per primal edge, and the boundary is "
    "subdivided exactly (or walked per vertex in collapse mode).",
    "<b>Repair and quality control.</b> Interior vertices are smoothed, "
    "concave cells may be merged, and non-planar faces may be triangulated "
    "or planarized. Every pass is gated by an in-process replica of "
    "checkMesh and a keep-best acceptance rule.",
    "<b>Boundary layer and validation.</b> A conforming prism stack is "
    "extruded on the selected wall patches, the interior is reflowed, the "
    "face windings are solved globally, and the result is validated for "
    "closure, positive volume, manifoldness, and checkMesh-style metrics.",
]),
P(
    "A parallel path exists for hex-dominant and cut-cell input: "
    "<i>hex_poly_dual.py</i> applies the same dual idea to a hexahedral "
    "mesh, producing a polyhedral complex whose cells are dual to the hex "
    "vertices. The mathematics of Section 4 is the same; the primal "
    "complex is different. The tetrahedral path is the one with the exact "
    "boundary-tiling theorem; the hex/cut-cell path is summarized in "
    "Section 4.9. The default Gmsh stage is described in Section 3.2 and "
    "the experimental fTetWild alternative in Section 3.6."
),
H2("3.2 The default Gmsh tet stage"),
P(
    "The default primal stage runs Gmsh through the project's wrapper. In "
    "the GUI the wrapper runs in a short-lived subprocess, so the bundled "
    "OpenCASCADE cannot clash with cadquery/OCP in the same process; the "
    "wrapper itself uses the Gmsh Python API. Gmsh runs natively (no WSL), "
    "reads STEP with OpenCASCADE, computes curvature and proximity fields, "
    "and writes a tetrahedral mesh that is converted to an OpenFOAM "
    "polyMesh; an optional prismatic boundary layer can be baked into the "
    "primal and is carried through the dual (Section 4.6). The dual never "
    "reads the CAD: it reads the polyMesh, so the tet stage and the dual "
    "are independent."
),
P(
    "Resolution is controlled by five presets, each defined by the "
    "number of cells across the domain's cross-section and by the "
    "curvature angular error: very coarse 8 / 45°, coarse 13 / 30°, "
    "medium 20 / 18°, fine 32 / 10°, very fine 48 / 6°. The code records "
    "the physical reading: fewer than ten cells across a passage cannot "
    "resolve a developing profile, about twenty is a usable RANS bulk, "
    "and thirty to fifty is a hand-built production mesh. The Gmsh "
    "baseline of Table 3.1 is the medium preset."
),
P(
    "The app's sizing analysis also samples per-patch local thickness by "
    "ray casting and aggregates the percentiles by area, so a small "
    "constriction is not drowned by a large wall patch; those values "
    "inform the min/max size hints used by the presets. An independent "
    "STEP feature analysis extracts sharp edges and narrow-gap regions "
    "and returns a curvature radius plus suggested minimum and maximum "
    "cell sizes for the size field."
),
P(
    "The boundary-layer floor is geometric, not a tuning parameter: the "
    "requested stack must fit in about three wall cells (the inversion "
    "budget is 0.34 of the local wall cell), so the wall cell is floored "
    "at stack/3, and that floor is capped at 10% of the largest "
    "bounding-box extent. A request finer than the geometry can support "
    "is therefore prevented at the sizing stage, not repaired later."
),
P(
    "Real Gmsh output can contain degenerate throats and thin-feature "
    "vertices; the converter's cleanup pass (Section 3.4) handles them "
    "before the dual is built. The measured Gmsh failure modes and the "
    "primal-versus-dual trade-off are discussed in Sections 3.4, 3.5, "
    "and 9."
),
H2("3.3 Surface, patches, and feature edges"),
P(
    "The surface is loaded as STEP through cadquery/OCC, or as STL. An "
    "ASCII STL is split at its solid boundaries so each named solid "
    "becomes a patch; a binary STL is loaded as one mesh and named from "
    "the file stem. Independently tessellated patches can leave "
    "near-coincident vertices at a tiny floating-point distance, so the "
    "repair pass first snaps vertices at a scale-aware tolerance "
    "(preserving patch identity) and falls back to PyMeshFix only for "
    "genuinely broken geometry, re-attributing faces to the nearest "
    "original patch and reporting the fallback."
),
P(
    "The dual construction does not require a CAD kernel at mesh time. It "
    "requires only a valid OpenFOAM polyMesh whose boundary is a "
    "2-manifold and whose patches are consistently oriented. This is a "
    "deliberate design choice: the geometric difficulty of clipping a "
    "Voronoi dual against a B-Rep surface is avoided entirely by using a "
    "median dual whose boundary points already lie on the surface."
),
P(
    "Patch identity is carried through the conversion. Each boundary "
    "triangle belongs to a patch; the three per-corner quads inherit that "
    "patch. In collapse mode, the boundary polygon around a vertex may "
    "span several triangles; the code assigns the polygon to the patch of "
    "the walk's start and splits the walk at patch seams. A patch seam is "
    "always a feature edge, regardless of its dihedral angle, as is a "
    "non-manifold edge. The feature-angle threshold is a separate "
    "parameter (default 90°) used only by the collapse walk."
),
P(
    "Feature edges matter for two reasons. First, the collapsed boundary "
    "must not smooth over a sharp CAD edge: a midpoint is inserted at "
    "every feature edge so that the polygon can turn the corner. Second, "
    "the dual cell of a vertex on a concave feature edge wraps around the "
    "feature; this is the origin of the non-convex boundary cells "
    "discussed in Sections 5 and 9. The converter does not hide this "
    "effect; it measures it and, if requested, merges the offending cells."
),
H2("3.4 Primal cleanup and topological self-healing"),
P(
    "The dual walk assumes that the primal mesh is topologically valid: "
    "every interior edge has a closed fan of tetrahedra, every boundary "
    "edge has an open fan terminated by exactly two boundary triangles, "
    "and no tetrahedron is degenerate. Real Gmsh output occasionally "
    "violates these assumptions at thin throats and near-collapsed "
    "vertices. The converter therefore runs a cleanup pass before the "
    "dual is built:"
),
*B([
    "Detect vertices whose incident tetrahedra form a degenerate throat "
    "(the fan around an incident edge is not a proper ring).",
    "Collapse the offending vertex onto a neighboring vertex, remove the "
    "degenerate tetrahedra, and rebuild the local connectivity.",
    "Retry the dual build. If the walk still fails, the conversion is "
    "refused with a diagnostic rather than writing a broken mesh.",
]),
P(
    "This is the primal-level part of the self-healing story. The "
    "dual-level part is the validation suite: closure at 10<super>−8</super>, "
    "positive exact volumes, owner &lt; neighbour, patch tiling, and "
    "boundary manifoldness. A conversion that fails any of these checks "
    "is not written. The important point for a numerical analyst is that "
    "the mesher distinguishes <i>topological validity</i> (the cell "
    "complex is closed and orientable) from <i>geometric quality</i> (the "
    "cells are star-shaped, non-orthogonal, and well-shaped). The former "
    "is a hard invariant; the latter is measured and improved but not "
    "guaranteed on arbitrary geometry."
),
H2("3.5 Why a dual rebuild rather than tetrahedron merging"),
P(
    "An earlier approach merged adjacent tetrahedra into polyhedra. Three "
    "variants were measured (mutual agreement, disjoint-pair matching, and "
    "guarded leftover sweeps) and all hit the same wall: coverage above "
    "about 80% could only be bought with proportionally worse cell shapes, "
    "because <i>these two tetrahedra share a large face</i> says nothing "
    "about <i>the union is well shaped</i>. On the reference block with a "
    "cylindrical bore, the merging path produced 139,227 cells with 80.3% "
    "poly coverage, over a thousand misoriented faces, skewness near 5, "
    "and two failed checkMesh checks. The dual path produced 47,528 cells "
    "with 100% coverage, zero misoriented faces, and <i>Mesh OK</i>."
),
P(
    "The dual has a different trade-off. Every primal tet is consumed, so "
    "there is no residual-tet concept. The output cells have more faces "
    "and more neighbors than merged tets, which enlarges the FV stencil. "
    "At concave feature edges the boundary cells can be genuinely "
    "non-convex, which is the failure mode of the industrial valve. These "
    "are real costs, and they are the reason the report spends as much "
    "space on quality metrics and repair as on the construction itself."
),
H2("3.6 The experimental fTetWild stage"),
P(
    "An experimental alternative to Gmsh, selectable in the GUI as "
    "<i>Polymesh sperimentale (fTetWild)</i>, replaces the tet stage with "
    "fTetWild through the pytetwild bindings. The downstream pipeline — "
    "OpenFOAM conversion, barycentric dual, boundary layers, checkMesh — "
    "is unchanged. The option is labelled experimental, and the dependency "
    "is optional (<font name='Mono'>pip install pytetwild</font>). The "
    "measurements below were recorded on the reference parts in the "
    "project's benchmark logs."
),
TBL([
    ["Metric", "Gmsh (baseline)", "fTetWild (experimental)", "Ratio"],
    ["Tet stage time (3 m part)", "649 s", "150 s", "4.3× faster"],
    ["Tet count", "6.94 M", "0.59 M", "11.8× fewer"],
    ["Minimum dihedral angle", "0.07°", "12.6°", "180× larger"],
    ["First-percentile dihedral", "20.0°", "35.3°", "+76%"],
    ["Venturi benchmark", "9.6 s", "0.58 s", "16× faster"],
    ["Small cylinder", "16 s / 13.9°", "9 s / 20.6°", "1.8× faster"],
], [46 * mm, 42 * mm, 50 * mm, 30 * mm],
   align_right=[1, 2, 3],
   caption="Table 3.1. The experimental fTetWild tet stage against the Gmsh "
           "baseline. The first four rows are measured on the 3 m part; the "
           "last two are separate benchmarks. Dihedral angles describe the "
           "primal tetrahedra, not the dual cells."),
P(
    "Surface fidelity is the critical fix. The internal edge-collapsing "
    "simplification of fTetWild deviated from the input surface by up to "
    "5 mm on the 3 m part (77% of boundary points beyond 0.3 mm) and drove "
    "the AMIPS energy (the mesh-optimization objective used by fTetWild) "
    "far above its baseline. With simplification off, the deviation falls "
    "to 0.06 mm mean and 1.65 mm maximum (6% beyond 0.3 mm), and the mesh "
    "has half the cells at comparable time. Simplification is "
    "therefore off by default; the GUI exposes it in the Advanced tab with "
    "an explicit warning, together with the surface envelope (ε), the stop "
    "energy, and filtering."
),
P(
    "The dual residual on the reference part is of order 30 defective "
    "faces out of 2.9 M, concentrated at feature edges; "
    "the historical Gmsh failure rate on the same part was about an order "
    "of magnitude higher (219 pyramid faces in one logged run). The "
    "opt-in boundary-pyramid clearance post-pass "
    "(<font name='Mono'>_clear_boundary_pyramids</font>) removes the "
    "remaining pyramid violations by moving each offending boundary "
    "polygon along its normal; it is volume-safe and, in the measured "
    "run, adds no new skew or non-orthogonality violations (measured "
    "7 → 0 on the reference part). "
    "The boundary-layer behaviour of this experimental path is analysed in "
    "Section 7.8."
),

H1("4. The barycentric dual"),
H2("4.1 Combinatorial definition"),
P(
    "Let T be a tetrahedral mesh of Ω with vertex set V, edge set E, face "
    "set F, and tet set T. The dual complex T* is defined combinatorially "
    "by reversing the incidence dimensions:"
),
*B([
    "one dual cell K<sub>v</sub>* for each primal vertex v ∈ V;",
    "one dual face σ<sub>e</sub>* for each primal edge e ∈ E;",
    "one dual edge for each primal triangle;",
    "one dual vertex for each primal tetrahedron.",
]),
P(
    "The dual cell of a boundary vertex is cut by the boundary; its "
    "boundary faces are the per-corner quads of Section 4.3. The dual of "
    "an interior edge is a closed polygon; the dual of a boundary edge is "
    "an open fan closed by the surface. The construction is a cell "
    "complex, not a Voronoi tessellation: the dual vertices are "
    "barycenters (tet centroids, optionally face centroids and edge "
    "midpoints), not circumcenters. This is what makes the boundary exact "
    "and the orientation unambiguous."
),
P(
    "The production realization uses the tet centroids as the dual "
    "vertices of the interior faces. With <i>median_faces=True</i>, the "
    "polygon also passes through the centroid of each primal face between "
    "consecutive tets, which is the full median dual. Both realizations "
    "are combinatorially identical; they differ in the number of vertices "
    "per face and in the geometric approximation of the dual surface. The "
    "default is the centroid-ring realization because it produces simpler "
    "faces and is cheaper; the median realization is available for "
    "experiments that need the exact barycentric subdivision."
),
H2("4.2 Interior faces from primal edges"),
P(
    "Fix an interior primal edge e = (a,b). The tetrahedra incident to e "
    "form a closed ring in rotational order. The dual face σ<sub>e</sub>* "
    "is the polygon whose vertices are the centroids of the tets in that "
    "ring, in the same rotational order. With <i>median_faces=True</i>, "
    "the centroid of the primal face between two consecutive tets is "
    "inserted between their tet centroids. The polygon is closed because "
    "the ring is closed."
),
P(
    "The face separates exactly the dual cells of a and b. Its orientation "
    "is fixed geometrically: the Newell vector of the polygon is dotted "
    "with the primal edge vector x<sub>b</sub> − x<sub>a</sub>; if the dot "
    "product is negative, the polygon is reversed. The owner is the "
    "smaller of the two cell indices, and the neighbour is the larger. "
    "There is no winding heuristic to get wrong: the primal edge tells "
    "the dual face which two cells it separates and which way it must "
    "point. This is the structural reason the dual orientation is robust "
    "on complex geometry."
),
P(
    "A dual edge (dual to a primal triangle abc) is shared by the three "
    "dual faces of edges ab, bc, and ca. Only two of those faces touch any "
    "one dual cell, so every cell's surface is a closed 2-manifold. This "
    "is a purely combinatorial statement; it does not depend on the "
    "geometry of the centroids. The geometric realization can still be "
    "non-convex, but it cannot be open or non-manifold unless the primal "
    "mesh is."
),
H2("4.3 Boundary faces and exact tiling"),
P(
    "Let (a,b,c) be a boundary triangle with edge midpoints "
    "m<sub>ab</sub>, m<sub>bc</sub>, m<sub>ca</sub> and centroid "
    "g = (a+b+c)/3. The three per-corner quads are"
),
EQM([
    r"Q_a = (a,\ m_{ab},\ g,\ m_{ca}),",
    r"Q_b = (b,\ m_{bc},\ g,\ m_{ab}),",
    r"Q_c = (c,\ m_{ca},\ g,\ m_{bc}).",
], "4.1"),
P(
    "<b>Proposition 4.1 (exact boundary tiling).</b> The quads "
    "Q<sub>a</sub>, Q<sub>b</sub>, Q<sub>c</sub> are planar, have disjoint "
    "interiors, and tile the triangle (a,b,c). Each has area |T|/3."
),
P(
    "<i>Proof.</i> All points lie in the plane of T, so the quads are "
    "planar. The medial triangle (m<sub>ab</sub>, m<sub>bc</sub>, "
    "m<sub>ca</sub>) has area |T|/4 and is divided by g into three "
    "triangles of area |T|/12. The quad Q<sub>a</sub> is the union of the "
    "corner triangle (a, m<sub>ab</sub>, m<sub>ca</sub>), of area |T|/4, "
    "and the medial triangle (m<sub>ab</sub>, g, m<sub>ca</sub>), of area "
    "|T|/12; hence its area is |T|/3. The same argument applies to "
    "Q<sub>b</sub> and Q<sub>c</sub>. The three quads meet only along "
    "the segments (m<sub>ab</sub>,g), (m<sub>bc</sub>,g), and "
    "(m<sub>ca</sub>,g), and their union is T. Q.E.D."
),
P(
    "The proposition is the reason the default dual has no clipping step. "
    "A Voronoi/circumcentric dual would extend beyond the CAD surface and "
    "would have to be cut back against it, with all the tolerance and "
    "feature-reconstruction problems that entails. The median dual uses "
    "points that are already on the surface: the primal vertex itself, the "
    "edge midpoints, and the triangle centroid. The output boundary is "
    "geometrically identical to the input boundary, patch for patch. Sharp "
    "features survive because the original surface points are still mesh "
    "points."
),
P(
    "For a boundary edge (a,b), the two incident boundary triangles "
    "contribute the segments (m<sub>ab</sub>, g<sub>1</sub>) and "
    "(m<sub>ab</sub>, g<sub>2</sub>), and the interior tet ring contributes "
    "the rest of the dual face. The two adjacent boundary cells share this "
    "face; the surface is watertight. This is the mechanism that keeps the "
    "boundary dual cells closed without any special boundary repair."
),
H2("4.4 Volume, closure, and the telescoping identity"),
P(
    "The total volume of the dual mesh can be computed from the boundary "
    "alone. Summing the exact fan-triangulated volumes of all cells and "
    "using the divergence theorem, every internal face appears twice with "
    "opposite orientation and cancels:"
),
EQ(r"\sum_{K\in\mathcal{K}} |K| = \frac{1}{6}\sum_{\tau\in\partial\Omega} "
   r"a_\tau\cdot(b_\tau\times c_\tau),", "4.2"),
P(
    "where the sum runs over the triangles of the boundary surface. Since "
    "the default dual boundary is an exact subdivision of the primal "
    "boundary, the right-hand side is the primal volume. The identity is "
    "independent of the interior dual vertices; it depends only on the "
    "boundary triangulation and its orientation. This is why the measured "
    "volume drift is at the level of floating-point summation "
    "(0.0–1.1×10<super>−16</super>) rather than at the level of the "
    "geometric approximation."
),
P(
    "Per cell, the code computes the centroid and volume with the "
    "OpenFOAM-compatible pyramid formula of Section 5.2. For planar faces "
    "this formula is exact; for non-planar faces it is a consistent "
    "quadrature. The validation routine uses a separate fan triangulation "
    "for every volume-based check (positivity, conservation, minimum "
    "volume, BL/core ratio), so a valid build is not rejected by the "
    "quadrature error of the centroid formula. The two measures agree to "
    "round-off on planar-faced cells and differ by the expected O(h²) "
    "warping term on non-planar ones."
),
H2("4.5 Collapse mode: polyDualMesh semantics"),
P(
    "The default dual emits three boundary quads per primal boundary "
    "triangle. OpenFOAM's <i>polyDualMesh</i> and several commercial "
    "meshers instead emit one boundary polygon per primal boundary "
    "vertex. PolyFoamMesh supports this topology through "
    "<i>collapse_smooth_edges=True</i>. The boundary face of a vertex v is "
    "the rotational walk around v through the centroids of its incident "
    "boundary triangles. A midpoint is inserted only where the walk "
    "crosses a feature edge (dihedral angle at or above "
    "<i>boundary_feature_angle</i>, default 90°); smooth edges are "
    "collapsed, so no seam vertex is created."
),
P(
    "The trade-off is explicit. The boundary face count drops by roughly a "
    "factor of three (one polygon per vertex instead of three quads per "
    "triangle), and the boundary layer can then extrude one prism stack "
    "per face, which is the commercial topology. The price is that the "
    "boundary runs through the triangle centroids and no longer tiles the "
    "primal surface exactly. The dual volume differs from the primal by a "
    "surface offset. The offset is second order in the mesh size: on a "
    "cube lattice mapped to a sphere, the measured drift is −12.2% at "
    "three cells across, −7.3% at four, −3.5% at six, −2.0% at eight, and "
    "−1.3% at ten; the product drift × n<super>2</super> is constant to "
    "within about 20%. On a flat-faced surface the drift is exactly zero, "
    "because the centroid dual tiles a plane."
),
P(
    "The default mode therefore checks volume against 10<super>−6</super>; "
    "the collapse mode checks it against a relaxed tolerance (default 5%) "
    "and reports the drift. Closure, positive volume, owner &lt; "
    "neighbour, and boundary manifoldness are still hard requirements. "
    "The exact-tiling contract is deliberately not claimed in this mode. "
    "Collapse mode is mutually exclusive with wedge cells and vertex-star "
    "splitting, because both subdivide the per-corner boundary quads that "
    "the collapsed walk replaces; the code disables them with a log line "
    "rather than silently ignoring the conflict."
),
H2("4.6 Mixed tetrahedron/prism input"),
P(
    "When the primal mesh already contains a prismatic boundary layer "
    "(for example, a layer baked by Gmsh), the converter carries the "
    "prisms through verbatim and dualizes only the tetrahedral region. The "
    "interface between the two regions is a set of primal faces; the dual "
    "cells on the tet side have faces on that interface, and the prism "
    "cells on the other side share them. The combined mesh is closed by "
    "the same winding solve that closes the pure dual. This path avoids "
    "re-extruding a boundary layer that already exists and preserves the "
    "prism quality of the primal mesher. It is also the reason the "
    "converter's input is an OpenFOAM polyMesh, not a tet list: the "
    "polyMesh can represent the mixed complex directly."
),
H2("4.7 Cell counts and coverage"),
P(
    "The reduction factor is approximately the number of tetrahedra per "
    "vertex, which is about 5.5–6 for a well-shaped tet mesh. The measured "
    "values are:"
),
TBL([
    ["Case", "Primal tets", "Dual cells", "Ratio", "Coverage"],
    ["Block with cylindrical bore", "251,048", "47,528", "5.28×", "100%"],
    ["Industrial valve (Parte4)", "824,661", "152,086", "5.42×", "100%"],
    ["Earlier terminal-face merge", "251,048", "139,227", "1.80×", "80.3%"],
], [52 * mm, 28 * mm, 28 * mm, 20 * mm, 30 * mm],
   align_right=[1, 2, 3, 4],
   caption="Table 4.1. Cell-count reduction. The dual consumes every primal "
           "tetrahedron; the earlier merge path left 19.7% of the tets "
           "unmerged on the same input."),
P(
    "Coverage is 100% by construction, not by a sweep. There is no "
    "residual-tet concept and no threshold that can leave part of the "
    "domain unmeshed. This is the strongest structural advantage of the "
    "dual over merging."
),
H2("4.8 Implementation notes"),
P(
    "The converter materializes dual points lazily: a point is created only "
    "when a face references it. This matters because checkMesh treats "
    "unused points as a topology error. Face geometry is computed in "
    "buckets of equal vertex count and in chunks of 250,000 faces; the "
    "chunking bounds the dense temporary arrays (an unchunked 16-million "
    "quad call peaked above 7 GB) while preserving the per-face operation "
    "order, so the output is bitwise identical to the unchunked version. "
    "The converter sorts owner/neighbour and uses deterministic traversal "
    "orders, which makes the output reproducible and independent of thread "
    "scheduling."
),
P(
    "The polyMesh layer is a single reader/writer shared by the tet and "
    "hex converters. It reads and writes ASCII and binary points, owner, "
    "and neighbour, and supports faces in both the legacy faceList and "
    "the faceCompactList default used by OpenFOAM 2512. The binary header "
    "parser deliberately takes the first '}\\n' in the first 4000 bytes: a "
    "large binary payload can contain the same bytes, and taking the last "
    "match would land inside the data."
),
P(
    "The stages log their progress and check a cancellation callback. The "
    "conversion of a million-tet mesh takes roughly 35–40 seconds on the "
    "reference machine; the dominant cost is the Python-level walk around "
    "primal edges and the face assembly, not the numerical geometry."
),

H2("4.9 Hex-dominant and cut-cell input"),
P(
    "The same dual construction is available for a hex(-dominant) or "
    "cut-cell OpenFOAM polyMesh (<i>hex_poly_dual.py</i>). The "
    "combinatorial map is unchanged: one output cell per primal vertex, "
    "one internal face per primal edge, and boundary faces that "
    "subdivide the primal boundary faces exactly. The edge-fan walk only "
    "needs every cell to touch an edge through exactly two of its faces, "
    "which is true for any valid polyhedral cell; the engine therefore "
    "does not depend on the primal cells being tetrahedra. It reuses the "
    "face geometry, cell centres, and defect detector of the tet "
    "converter, writes <i>constant/polyMesh_dual</i>, and never touches "
    "the input, so an A/B against OpenFOAM's <i>polyDualMesh</i> on the "
    "same hex mesh is a pure quality comparison."
),
P(
    "The native cut-cell generator (<i>native_mesher.py</i>) produces an "
    "input for this path without Gmsh. A uniform Cartesian grid is laid "
    "over the surface bounding box, each grid point is classified inside "
    "or outside the fluid by ray casting, mixed cells are rebuilt as the "
    "convex hull of the inside corners and the surface crossings on the "
    "twelve box edges (found by bisection on the signed distance), and "
    "the cap facets become boundary triangles on the surface. The output "
    "is a watertight hex+poly mesh whose boundary is a chord "
    "approximation of the surface; it then feeds the hex dual for a 100% "
    "polyhedral mesh."
),
P(
    "Two limitations are explicit in the implementation. Cut cells must "
    "be single-sheet: a cell whose fluid region is thinner than one cell "
    "or disjoint falls back to corner-majority with a warning. The grid "
    "is uniform (no octree refinement yet), so a small feature requires "
    "a small global cell size. Boundary conformity is within one cell "
    "size on curvature; this is the castellated phase, without snapping."
),

H1("5. Geometric predicates and quality metrics"),
H2("5.1 Face geometry: area vector, centroid, planarity"),
P(
    "For a polygon with vertices p<sub>1</sub>, …, p<sub>m</sub>, the "
    "Newell area vector is"
),
EQ(r"S = \frac{1}{2}\sum_{i=1}^{m} p_i\times p_{i+1}, \qquad p_{m+1}=p_1.",
   "5.1"),
P(
    "For a planar polygon, |S| is the area and S/|S| is the unit normal "
    "given by the right-hand rule; the sign flips if the vertex order is "
    "reversed. For a non-planar polygon, S is the area vector of the "
    "triangulated surface obtained by fanning the polygon from any point; "
    "the result is independent of the fan point because the internal "
    "triangles cancel."
),
P(
    "The face centroid is computed with OpenFOAM's <i>centre-point "
    "decomposition</i>. Let c<sub>0</sub> = (1/m)Σp<sub>i</sub> be the "
    "vertex average. For each edge (p<sub>i</sub>, p<sub>i+1</sub>), form "
    "the triangle (c<sub>0</sub>, p<sub>i</sub>, p<sub>i+1</sub>) with "
    "area vector"
),
EQ(r"t_i = \frac{1}{2}(p_i-c_0)\times(p_{i+1}-c_0), \qquad w_i=|t_i|.",
   "5.2"),
P("Then"),
EQ(r"S = \sum_{i=1}^{m} t_i, \qquad "
   r"x_\sigma = \frac{\sum_i w_i\,(c_0+p_i+p_{i+1})/3}{\sum_i w_i}.",
   "5.3"),
P(
    "For a planar polygon this is the exact area vector and the exact "
    "area-weighted centroid. For a non-planar polygon it is a specific "
    "quadrature: the polygon is replaced by a fan of triangles from the "
    "vertex average, each triangle is weighted by its area, and the "
    "centroid is the weighted average of the triangle centroids. OpenFOAM "
    "uses the same construction for <i>faceCentres</i> and "
    "<i>faceAreas</i>; the PolyFoamMesh implementation is bitwise "
    "identical (same bucketing, same operation order, same chunking). This "
    "matters because the quality metrics must agree with checkMesh, and "
    "checkMesh uses OpenFOAM's face geometry, not an idealized planar "
    "one."
),
P(
    "Planarity is measured against the Newell plane. The plane is defined "
    "by the centroid x<sub>σ</sub> and the unit normal n<sub>σ</sub>; the "
    "deviation of vertex p is |(p − x<sub>σ</sub>)·n<sub>σ</sub>|. The "
    "face is flagged non-planar when the maximum deviation exceeds "
    "<i>planarity_rel_tol</i> times the bounding-box diagonal (default "
    "10<super>−6</super>). The dual's internal faces are rings of tet "
    "centroids around a primal edge and are generally non-planar; the "
    "boundary quads are planar by construction. This asymmetry is "
    "important and is discussed in Section 6.7."
),
H2("5.2 Cell geometry: centroid and volume"),
P(
    "Let c<sub>est</sub> = (1/|F<sub>K</sub>|)Σ<sub>σ</sub>x<sub>σ</sub> be "
    "the average of the face centroids. For each face define"
),
EQ(r"w_\sigma = S_\sigma\cdot(x_\sigma-c_{\mathrm{est}}), \qquad "
   r"V_\sigma = \frac{1}{3}w_\sigma, \qquad "
   r"c_\sigma = \frac{3}{4}x_\sigma+\frac{1}{4}c_{\mathrm{est}}.",
   "5.4"),
P("Then the cell volume and centroid are"),
EQ(r"|K| = \sum_{\sigma\in\mathcal{F}_K} V_\sigma, \qquad "
   r"x_K = \frac{\sum_\sigma V_\sigma\,c_\sigma}"
   r"{\sum_\sigma V_\sigma}.", "5.5"),
P(
    "This is the OpenFOAM <i>primitiveMeshCellCentresAndVols</i> "
    "algorithm. For planar faces it is exact, by the divergence theorem "
    "applied to x and x ⊗ x (Section 2.2). It is exact for any choice of "
    "c<sub>est</sub>: the dependence on c<sub>est</sub> cancels "
    "algebraically, because the face terms telescope. The individual "
    "V<sub>σ</sub> are signed; a negative V<sub>σ</sub> means that the "
    "pyramid over σ with apex c<sub>est</sub> has negative signed volume, "
    "not that the cell volume is wrong. The total |K| is still the "
    "enclosed volume, and the centroid formula is still correct, as long "
    "as the surface is closed and the faces are planar."
),
P(
    "For non-planar faces the centroid formula is a quadrature. The "
    "validation does not rely on it: the exact volume is recomputed by "
    "fan-triangulating every face and summing the signed tetrahedron "
    "volumes,"
),
EQ(r"|K|_{\mathrm{exact}} = \frac{1}{6}\sum_{\tau\in\mathcal{T}_K} "
   r"a_\tau\cdot(b_\tau\times c_\tau),", "5.6"),
P(
    "where the sum runs over the triangles of the triangulated cell "
    "boundary. This measure is exact for the triangulated surface, "
    "including non-planar faces. The two measures agree to round-off on "
    "planar-faced cells; on warped faces they differ by the quadrature "
    "error. The code uses the exact measure for every volume-based check "
    "and the centroid formula for the geometric center used by the "
    "metrics and the smoother."
),
H2("5.3 Star-shapedness and the face-pyramid test"),
P(
    "A cell K is <i>star-shaped</i> with respect to a point x<sub>0</sub> "
    "if, for every y ∈ K, the segment [x<sub>0</sub>, y] lies in K. The "
    "set of such points is the <i>kernel</i> of K. For a closed polyhedron "
    "with planar faces, the kernel is the intersection of the inner "
    "half-spaces of its faces: a point sees the whole polyhedron exactly "
    "when it lies on the inner side of every face plane."
),
P(
    "The signed pyramid volume over face σ with apex x<sub>0</sub> is "
    "V<sub>σ</sub>(x<sub>0</sub>) = (1/3)S<sub>σ</sub>·(x<sub>σ</sub> − "
    "x<sub>0</sub>). Since x<sub>σ</sub> lies in the face plane and "
    "S<sub>σ</sub> is the outward normal, the sign of V<sub>σ</sub> is the "
    "sign of the signed distance from x<sub>0</sub> to the face plane, "
    "with the inner side positive. Therefore:"
),
P(
    "<b>Proposition 5.1 (pyramid test = star-shapedness).</b> Let K be a "
    "closed polyhedron with planar faces and outward area vectors "
    "S<sub>σ</sub>. For a point x<sub>0</sub> not on any face plane, "
    "V<sub>σ</sub>(x<sub>0</sub>) &gt; 0 for every face σ if and only if "
    "x<sub>0</sub> lies in the interior of the kernel of K; that is, K is "
    "star-shaped with respect to x<sub>0</sub>."
),
P(
    "<i>Proof.</i> V<sub>σ</sub>(x<sub>0</sub>) &gt; 0 iff x<sub>0</sub> "
    "is on the inner side of the plane of σ. The kernel is the "
    "intersection of the inner half-spaces of all faces. Hence all "
    "pyramid volumes are positive iff x<sub>0</sub> belongs to that "
    "intersection, which is the kernel. Q.E.D."
),
P(
    "checkMesh applies this test with x<sub>0</sub> = x<sub>K</sub>, the "
    "computed cell centroid: a face whose pyramid volume is non-positive "
    "is reported as an <i>incorrectly oriented face</i> or a <i>face "
    "pyramid</i> failure. The PolyFoamMesh detector uses the same "
    "arithmetic:"
),
EQ(r"p_{\mathrm{own}} = S_\sigma\cdot(x_\sigma-x_K), \qquad "
   r"p_{\mathrm{nb}} = -S_\sigma\cdot(x_\sigma-x_L),", "5.7"),
P(
    "and flags the face if either is ≤ 0. The test is a star-shapedness "
    "certificate for planar faces. For non-planar faces it is a numerical "
    "proxy: the Newell plane and the centre-point centroid replace the "
    "exact plane and centroid, and the sign test is applied to that "
    "approximation. The code reports the number of violations and refuses "
    "to claim star-shapedness for a cell that has any."
),
P(
    "The distinction between <i>valid</i> and <i>star-shaped</i> is "
    "central to this report. A cell can be closed, positively oriented, "
    "and have the correct volume while failing the pyramid test; the "
    "industrial valve contains hundreds of such cells. They are valid "
    "polyhedra but not star-shaped with respect to their centroids. "
    "Whether a convergence theorem needs star-shapedness, or only a weaker "
    "condition, is exactly the kind of question the valve can help answer."
),
H2("5.4 Non-orthogonality"),
P(
    "For an internal face σ = K|L, the non-orthogonality is the angle "
    "between the center-to-center vector d<sub>σ</sub> = x<sub>L</sub> − "
    "x<sub>K</sub> and the face normal n<sub>σ</sub> = "
    "S<sub>σ</sub>/|S<sub>σ</sub>|:"
),
EQ(r"\theta_\sigma = \arccos\left(\frac{d_\sigma\cdot S_\sigma}"
   r"{|d_\sigma|\,|S_\sigma|}\right) \in [0,\pi].", "5.8"),
P(
    "θ<sub>σ</sub> = 0 means the line joining the cell centers is normal "
    "to the face. θ<sub>σ</sub> = 90° is the M-matrix boundary: the "
    "over-relaxed transmissibility changes sign there. checkMesh warns "
    "above 70°; the PolyFoamMesh smoother targets 65°, deliberately "
    "stricter, so that a mesh accepted by the smoother has a margin "
    "against the checkMesh limit and a larger margin against the "
    "mathematical boundary. The choice is a safety policy, not a theorem: "
    "a face at 80° still has a positive transmissibility, but its "
    "two-point flux is a poor approximation of the normal derivative."
),
P(
    "The non-orthogonality is a property of a face pair, not of a cell. "
    "The same cell can have well-aligned faces on one side and badly "
    "aligned faces on another. The distribution matters as much as the "
    "maximum: a single face at 89° carrying a negligible flux is less "
    "harmful than a large patch at 75° carrying a significant flux. The "
    "code reports both the maximum and the average, and the smoother's "
    "continuous objective penalizes the excess over 65° quadratically, "
    "which weights severe faces more than mild ones."
),
P(
    "The measured effect of the boundary layer illustrates why the maximum "
    "can be misleading. On three benchmark geometries, the dual-only "
    "maximum and the dual-plus-BL maximum are:"
),
TBL([
    ["Geometry", "Dual only (max θ)", "Dual + BL (max θ)", "Δ"],
    ["cube_duct", "51.87° (PASS)", "80.64° (FAIL)", "+28.8°"],
    ["venturi", "74.00° (FAIL)", "146.41° (FAIL)", "+72.4°"],
    ["concave_bracket", "62.66° + 36 pyramids", "105.41° (FAIL)", "+42.8°"],
], [42 * mm, 42 * mm, 42 * mm, 24 * mm],
   align_right=[1, 2, 3],
   caption="Table 5.1. Non-orthogonality before and after boundary-layer "
           "extrusion. The average non-orthogonality is essentially flat "
           "(+0.0° to +0.6°); the damage is concentrated on a few extreme "
           "faces."),
P(
    "The extreme faces are tiny. On the venturi, a small fraction of the "
    "boundary faces have area below 10<super>−6</super> m<super>2</super>; "
    "the faces above 70° are shared by a sliver cell and a larger cell, "
    "with a center-to-center distance much larger than the face size. A "
    "face of area a on a pair of cells at distance L has "
    "a normal separation of order a/L<super>2</super>; when a is tiny and "
    "L is not, the angle can approach 90° even though the geometry is "
    "locally smooth. This is the mechanism behind the BL degradation, and "
    "it is the reason the report treats the <i>area-weighted</i> "
    "distribution, not just the maximum, as a research quantity."
),
H2("5.5 Skewness"),
P(
    "Skewness measures the tangential offset between the face centroid and "
    "the point where the line joining the two cell centers crosses the "
    "face plane. The two-point flux assumes that the face value is "
    "interpolated at x<sub>σ</sub>; if the line crosses the face far from "
    "x<sub>σ</sub>, the interpolation is geometrically biased. For an "
    "internal face, with C<sub>O</sub> = x<sub>K</sub>, C<sub>N</sub> = "
    "x<sub>L</sub>, d = C<sub>N</sub> − C<sub>O</sub>, and c<sub>pf</sub> "
    "= x<sub>σ</sub> − C<sub>O</sub>, define"
),
EQ(r"\alpha = \frac{S_\sigma\cdot c_{pf}}{S_\sigma\cdot d}, \qquad "
   r"s_v = c_{pf} - \alpha\,d.", "5.9"),
P(
    "The point C<sub>O</sub> + αd is the intersection of the line with the "
    "plane through x<sub>σ</sub> normal to S<sub>σ</sub>; s<sub>v</sub> is "
    "the tangential offset. The normalization uses the larger of a "
    "fraction of the center distance and the largest vertex projection:"
),
EQ(r"f_d = \max\left(0.2\,|d|,\ \max_{v\in\sigma}"
   r"\left|\hat{s}_v\cdot(x_v-x_\sigma)\right|\right), \qquad "
   r"\mathrm{skew}_\sigma = \frac{|s_v|}{f_d},", "5.10"),
P(
    "where ŝ<sub>v</sub> = s<sub>v</sub>/|s<sub>v</sub>|. The first term "
    "prevents division by zero on small faces; the second accounts for the "
    "face extent, so that a large face is allowed a proportionally larger "
    "absolute offset. This is OpenFOAM's <i>primitiveMeshTools::"
    "faceSkewness</i> formula. For a boundary face the code follows "
    "<i>boundaryFaceSkewness</i>: the normal distance is "
    "d<sub>b</sub> = n(n·c<sub>pf</sub>), the scale is "
    "(S·c<sub>pf</sub>)/(S·d<sub>b</sub>), and the base term is "
    "0.4|d<sub>b</sub>| instead of 0.2|d|."
),
P(
    "The implementation contains a correction that is worth recording. "
    "The original detector computed f<sub>d</sub> as the <i>sum</i> of the "
    "base term and the vertex extent. OpenFOAM takes the <i>maximum</i>. "
    "Since max(a,b) ≤ a+b, the sum inflated the denominator and "
    "systematically under-reported skewness; on one mesh the in-process "
    "detector reported zero skewed faces where checkMesh found three. The "
    "fix changed both call sites to the maximum. The verification is "
    "strong on two production meshes:"
),
TBL([
    ["Mesh", "In-process (fixed)", "Real checkMesh"],
    ["Valve, un-repaired BL baseline", "22.16451", "22.1645"],
    ["Valve, merge-repaired BL", "22.16451", "22.1645"],
    ["slot1, un-repaired BL baseline", "2.93131", "2.93131"],
    ["slot1, post-BL merge-repaired", "3.32939", "4.55171"],
    ["valve_dual fixture (float32 points)", "13.01 (6 faces &gt; 4)", "14.5848 (98 faces &gt; 4)"],
], [68 * mm, 45 * mm, 45 * mm],
   align_right=[1, 2],
   caption="Table 5.2. Skewness before and after the max-not-sum fix. Three "
           "of five independent comparisons agree to four or five decimals. "
           "The two mismatches are reported, not hidden: the fixture stores "
           "points as float32, and the post-BL mismatch has not been "
           "reconfirmed with a fresh rebuild."),
P(
    "The practical lesson is that an in-process replica is a filter, not an "
    "oracle. It is fast enough to drive the smoother and the merge, and it "
    "agrees exactly with checkMesh on the face-pyramid count (895/895 on "
    "the reference valve). It is not a substitute for a real checkMesh run "
    "on a mesh that will be used for production."
),
H2("5.6 Aspect ratio"),
P(
    "PolyFoamMesh uses two aspect-ratio measures and takes the maximum. "
    "The <i>directional</i> measure compares the total projected face area "
    "along the three coordinate axes:"
),
EQ(r"A_x(K)=\sum_{\sigma\in\mathcal{F}_K}|S_\sigma\cdot e_x|, \quad "
   r"A_y(K)=\sum_{\sigma}|S_\sigma\cdot e_y|, \quad "
   r"A_z(K)=\sum_{\sigma}|S_\sigma\cdot e_z|,", "5.11"),
EQ(r"r_{\mathrm{dir}}(K) = \frac{\max(A_x,A_y,A_z)}"
   r"{\min(A_x,A_y,A_z)}.", "5.12"),
P(
    "For a cube all three sums are equal and r<sub>dir</sub> = 1. For a "
    "needle aligned with the x axis, A<sub>x</sub> is large and "
    "A<sub>y</sub>, A<sub>z</sub> are small, so r<sub>dir</sub> is large. "
    "The measure is cheap and catches axis-aligned anisotropy, which is "
    "common in boundary layers."
),
P(
    "The <i>compactness</i> measure normalizes the total surface area by "
    "the volume:"
),
EQ(r"r_{\mathrm{comp}}(K) = \frac{1}{6}\frac{A_{\mathrm{tot}}(K)}"
   r"{|K|^{2/3}}, \qquad A_{\mathrm{tot}}=\sum_\sigma |S_\sigma|.",
   "5.13"),
P(
    "The factor 1/6 is chosen so that a cube has r<sub>comp</sub> = 1 "
    "(A<sub>tot</sub> = 6a<super>2</super>, |K|<super>2/3</super> = "
    "a<super>2</super>). A flat slab or a general needle has "
    "r<sub>comp</sub> &gt; 1. The directional measure misses a slab whose "
    "projected areas happen to balance; the compactness measure misses a "
    "needle whose surface area is not extreme. Taking the maximum covers "
    "both. The checkMesh-compatible threshold is 1000; a cell above it is "
    "flagged."
),
P(
    "The measured values show the expected pattern. The reference block "
    "passes with a small maximum aspect; the valve passes at 779. The "
    "boundary layer is the main source of high aspect ratios, raising the "
    "three benchmarks to hundreds. The local-termination option can "
    "improve non-orthogonality but worsen aspect on the concave case above "
    "1,000, which is a reminder that these metrics are coupled and a "
    "single scalar score cannot capture the trade-off."
),
H2("5.7 Closure, volume, manifoldness, and orientability"),
P(
    "The validation suite is a conjunction of hard checks. A mesh is "
    "written only if all of them hold (with the stated tolerance in "
    "collapse mode):"
),
*B([
    "<b>Closure:</b> ε<sub>K</sub> &lt; 10<super>−8</super> for every "
    "cell, where ε<sub>K</sub> is the relative area-vector residual (2.3).",
    "<b>Positive exact volume:</b> |K|<sub>exact</sub> &gt; 0 for every "
    "cell, using the fan-triangulated measure (5.6).",
    "<b>Owner/neighbour:</b> owner &lt; neighbour for every internal face; "
    "owner and neighbour indices in range.",
    "<b>Patch tiling:</b> the boundary patches cover every boundary face "
    "exactly once, with the first patch starting at n<sub>int</sub>.",
    "<b>Boundary manifoldness:</b> every boundary edge is shared by "
    "exactly two boundary faces. A BL terminator strip that coincides with "
    "the original wall band can leave an edge shared by four faces; the "
    "build is refused rather than written broken.",
    "<b>Orientability:</b> the global winding solve finds a consistent "
    "orientation of every cell boundary; a parity conflict is reported as "
    "a non-orientable mesh, not silently repaired.",
]),
P(
    "These checks separate topology from geometry. Closure and "
    "manifoldness are topological; positive volume and star-shapedness are "
    "geometric. The valve passes the topological checks and fails the "
    "geometric ones. That is exactly the distinction a convergence theory "
    "needs to make."
),
H2("5.8 The in-process checkMesh replica"),
P(
    "The converter and the smoother need a fast, deterministic estimate of "
    "the checkMesh criteria. The replica implements the face-pyramid test, "
    "non-orthogonality, skewness, and aspect ratio with the formulas above. "
    "It uses the same face geometry as OpenFOAM (centre-point "
    "decomposition) and the same cell geometry (pyramid formula), and it "
    "sorts owner/neighbour identically. On the recorded 2026-08-01 valve "
    "fixture it predicts 895 face-pyramid failures where checkMesh "
    "reports 895; the regenerated 2026-09-09 fixture gives 863 of 863 "
    "(Appendix E). The skewness "
    "matches to four or five decimals on two production meshes (Table "
    "5.2)."
),
P(
    "The replica has two known limitations. First, the internal skewness "
    "formula only sees internal faces; boundary-face skewness is opt-in "
    "because the historical detector did not count it, and enabling it "
    "changes the counts. Second, the replica is not a full checkMesh: it "
    "does not check cell openness, face weights, or all the mesh-quality "
    "warnings. It is used as a control signal for the repair loops, with a "
    "real checkMesh as the final arbiter."
),
H2("5.9 Thresholds"),
P(
    "The canonical thresholds live in one module, "
    "<i>quality_thresholds.py</i>, so that the converter, the smoother, "
    "the merge, and the reports cannot drift apart:"
),
TBL([
    ["Quantity", "Threshold", "Where used", "Rationale"],
    ["Face pyramid", "V<sub>σ</sub> ≤ 0", "checkMesh replica", "star-shapedness certificate"],
    ["Non-orthogonality", "70.0°", "checkMesh limit", "safety margin below 90° M-matrix boundary"],
    ["Non-orthogonality", "65.0°", "smoother objective", "deliberately stricter than checkMesh"],
    ["Skewness", "4.0", "checkMesh limit", "OpenFOAM-compatible normalization"],
    ["Aspect ratio", "1000.0", "checkMesh limit", "directional/compactness maximum"],
    ["Relative closure", "10<super>−8</super>", "validation", "watertightness"],
    ["Volume drift", "10<super>−6</super> / 5%", "default / collapse", "exact tiling vs surface offset"],
    ["Planarity", "10<super>−6</super>·diag", "planarize/report", "bbox-relative warping"],
    ["BL height slope", "0.5", "gradient limiter", "cfMesh-style relThicknessTol"],
    ["BL relative inversion", "0.9", "reflow guard", "no edge crossing"],
], [30 * mm, 24 * mm, 42 * mm, 62 * mm],
   caption="Table 5.3. Canonical thresholds. The optimizer's 65° target is "
           "not a checkMesh threshold; it is an internal safety margin."),
P(
    "The choice of 70° rather than 90° is a practical one. The M-matrix "
    "property survives up to 90°, but the two-point flux error grows with "
    "tan θ, and a mesh with faces near 90° is fragile: a small perturbation "
    "of the cell centers can flip the sign of the transmissibility. The "
    "10° margin absorbs that fragility. The smoother's 65° target adds "
    "another 5° because the smoother can only move interior vertices; a "
    "mesh that is exactly at 70° after smoothing has no room for the "
    "boundary-layer extrusion, which will add non-orthogonality at the "
    "BL/core interface."
),

H1("6. Repair and quality loops"),
H2("6.1 The defect detector and the keep-best contract"),
P(
    "The repair loops are driven by the in-process checkMesh replica. A "
    "configuration is scored by a pair (D, J): D is the total number of "
    "defect violations (face-pyramid + non-orthogonality + skewness), "
    "reported as its three components; J is a continuous penalty. A "
    "candidate is accepted only if"
),
*B([
    "no cell volume becomes non-positive;",
    "closure remains below 10<super>−8</super> and the boundary remains "
    "manifold;",
    "D strictly decreases, or, when D is unchanged, J strictly decreases.",
]),
P(
    "The best configuration seen across all passes is the one written, not "
    "the last one. This is the <i>keep-best</i> contract. Its scope is "
    "precise: enabling a pass can never make the written mesh worse "
    "<i>under the metrics the detector measures</i>. It is not a proof of "
    "global optimality, it does not compare area-weighted norms, and it "
    "does not guarantee that the mesh satisfies a convergence condition. "
    "It is a safety property, and it is the reason the opt-in passes can "
    "be left in the code with their measured negative results without "
    "risking a regression."
),
H2("6.2 Weighted Laplacian smoothing"),
P(
    "By default, only interior dual vertices move. A vertex that belongs "
    "to at least one boundary face is pinned, because the boundary is an "
    "exact subdivision of the input surface; moving it would break the "
    "surface contract. The fTetWild path may opt into free-boundary "
    "smoothing, which relaxes that constraint under an edge-length guard "
    "(Appendix D). The update is a weighted Laplacian:"
),
EQ(r"p_i' = p_i + \omega\,\frac{\sum_{j\in\mathcal{N}(i)} w_{ij}"
   r"(p_j-p_i)}{\sum_{j\in\mathcal{N}(i)} w_{ij}}, \qquad "
   r"w_{ij} = \frac{1}{|p_i-p_j|},", "6.1"),
P(
    "with relaxation ω = 0.5 by default. The inverse-distance weighting "
    "preserves the local shape better than the unweighted mean on graded "
    "meshes. The fixed points of the iteration are weighted harmonic "
    "maps: each free vertex is the weighted average of its neighbors. "
    "Topology is unchanged; only coordinates move."
),
P(
    "A naive Laplacian pass can worsen the metrics. The implementation "
    "therefore uses backtracking: if a pass would increase the defect "
    "count or the objective, the relaxation is halved and the pass is "
    "retried, up to five levels. The accepted configuration is the best "
    "one seen. The smoothing is a constrained descent in the total defect "
    "count, with J as a tie-break, not a gradient descent on a single "
    "smooth functional. This "
    "distinction matters for the analysis: there is no guarantee that the "
    "iteration reaches a local minimum of a scalar objective, only that "
    "the written mesh is not worse than the input under the measured "
    "criteria."
),
P(
    "The smoother is effective on interior-dominated defects. It cannot "
    "fix a boundary-dominated defect, because the boundary vertices are "
    "pinned and the cell centroid of a boundary cell is dominated by its "
    "boundary polygons. This was measured directly on the valve: moving "
    "the interior vertices of the failing boundary cells changed the "
    "OpenFOAM centroid too little to flip a boundary-face pyramid. An "
    "unguarded plane push lowered the continuous inversion penalty by only "
    "13% while the pyramid count rose from 110 to 177 and the maximum "
    "non-orthogonality rose from 97.2° to 169.1°. The production smoother "
    "post-merge cannot reach the bar either: its best result on the merged "
    "venturi is about 97° non-orthogonality at skewness about 34 "
    "(unguarded) or about 18 (guarded). The conclusion is "
    "structural, not a tuning failure: boundary pyramids need a "
    "cell-level repair, which is the merge."
),
H2("6.3 The continuous objective"),
P(
    "The defect counts are integers and are coarse: two configurations "
    "with the same counts can have very different worst faces. The "
    "smoother therefore uses a continuous penalty to break ties:"
),
EQ(r"J = \sum_{\sigma}\left[\max(0,\theta_\sigma-65^\circ)\right]^2 "
   r"+ \lambda\sum_{\sigma}\mathrm{skew}_\sigma^2, \qquad \lambda=1.",
   "6.2"),
P(
    "The first term penalizes faces above 65°, not 70°, so the smoother "
    "keeps pushing well inside the checkMesh limit. The second penalizes "
    "skewness quadratically, which weights severe faces more than mild "
    "ones. The objective is not area-weighted: a tiny face at 89° "
    "contributes as much as a large face at 89°. This is deliberate for "
    "the current use case (the metrics are defined per face, and "
    "checkMesh reports counts and maxima), but it is also a limitation. "
    "An area-weighted objective would be a natural alternative if the "
    "convergence condition turns out to be flux-weighted rather than "
    "face-counted. The code exposes the objective and the threshold, so "
    "the experiment is easy to run."
),
H2("6.4 Compactness, axis balance, and zonal aspect"),
P(
    "Three optional terms target cell shape rather than face alignment. "
    "They are off by default because each was measured to trade one "
    "metric for another."
),
P(
    "<b>Compactness.</b> The GETMe-inspired quality of a cell is "
    "q<sub>3</sub> = V − C A<super>3/2</super>, where A is the total "
    "surface area and C is a normalization constant. At fixed volume, "
    "increasing q<sub>3</sub> means reducing A, i.e. making the cell more "
    "compact. The implementation uses the exact gradient of the face area "
    "with respect to a vertex. For a polygon with vertices p<sub>k</sub> "
    "and area vector S,"
),
EQ(r"\frac{\partial S}{\partial p_k} = \frac{1}{2}"
   r"\left[p_{k-1}-p_{k+1}\right]_\times, \qquad "
   r"\frac{\partial |S|}{\partial p_k} = \frac{1}{2}\,"
   r"\hat{n}\times(p_{k-1}-p_{k+1}),", "6.3"),
P(
    "where [a]<sub>×</sub> is the skew-symmetric matrix of the cross "
    "product and n̂ = S/|S|. The first identity follows by "
    "differentiating S = (1/2)Σp<sub>i</sub>×p<sub>i+1</sub>; the second "
    "follows from ∂|S| = n̂·∂S. This is a closed-form gradient, not a "
    "finite difference, which makes the compactness pass cheap and "
    "deterministic."
),
P(
    "<b>Axis balance.</b> The directional sums A<sub>x</sub>, A<sub>y</sub>, "
    "A<sub>z</sub> of (5.11) are driven toward equality by a smooth "
    "surrogate of their variance. The gradient is again computed through "
    "the face-area Jacobian. This targets the axis-aligned anisotropy that "
    "the compactness term does not see."
),
P(
    "<b>Zonal aspect.</b> Cells whose aspect ratio exceeds a threshold "
    "receive a Freitag-style worst-aspect penalty, which concentrates the "
    "smoothing on the worst cells instead of averaging over the mesh."
),
P(
    "The measured trade-offs are real. On cube_duct, enabling the "
    "compactness and axis-balance terms reduces the maximum "
    "non-orthogonality from 51.87° to 47.29°, but the maximum skewness "
    "rises from 3.930 to 4.588 and crosses the 4.0 gate. On venturi and "
    "concave_bracket the same terms leave the maximum unchanged; with ten "
    "iterations the venturi worsens. The terms are "
    "therefore opt-in, and the default path is byte-identical to the "
    "smoother without them. The lesson is not that the terms are useless; "
    "it is that a single scalar quality score is the wrong abstraction "
    "when the acceptance criteria are a vector of maxima."
),
H2("6.5 Concave-cell merge"),
P(
    "The merge is the only cell-level repair that was measured to fix the "
    "wrapped boundary cells. For each cell that fails the face-pyramid "
    "test, the algorithm tries its interior neighbors in decreasing "
    "shared-face-area order and keeps the first one whose virtual union "
    "passes. The virtual union removes the shared face and checks the "
    "result: positive volume and no non-positive face pyramids. If the "
    "union passes, the cells are merged and the face loops are combined. "
    "A union-find structure keeps the merges of one round disjoint; up "
    "to two rounds are run because a cell can become concave only after "
    "its neighbor has been merged."
),
P(
    "The acceptance is a max-severity keep-best: a round is kept only if "
    "the pyramid count strictly falls, every cell volume stays positive, "
    "and the maximum non-orthogonality and maximum skewness do not rise. "
    "This is stricter than the "
    "count-based smoother and is necessary because merging changes the "
    "cell size and can worsen local resolution. The measured results on "
    "the valve are:"
),
TBL([
    ["Valve dual-only", "Cells", "Pyramids", "max θ", "avg θ", "max skew", "Failed checks"],
    ["Baseline", "152,086", "340", "95.62°", "10.57°", "22.16", "3"],
    ["Merge (A, keep-best)", "151,763", "46", "94.30°", "10.70°", "21.17", "3"],
], [38 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 20 * mm],
   align_right=[1, 2, 3, 4, 5, 6],
   caption="Table 6.1. Concave-cell merge on the collapsed-boundary "
           "production valve dual, in-process detector. The merge "
           "fixes 302 of 348 concave cells (86.8%) but does not reach PASS. "
           "The remaining failures are boundary-dominated feature cells."),
P(
    "The merge is a substantial improvement and a clear demonstration that "
    "cell-level repair can address what smoothing cannot. It is still not "
    "a solution to the valve: dozens of pyramid violations remain in the "
    "in-process detector (more in a real checkMesh run), and the three "
    "checkMesh failures persist. "
    "The merge also enlarges cells, which can "
    "be undesirable in a boundary layer or in a region where local "
    "resolution matters. It is opt-in for that reason."
),
P(
    "A more aggressive variant that merges entire connected components of "
    "failing cells was measured and rejected. On the venturi it produced "
    "hundreds of pyramids against a baseline of 224, with skewness in the "
    "hundreds; on the valve it produced more than a thousand pyramids, "
    "with skewness above a thousand. Merging groups makes larger "
    "non-convex cells; the local pairwise merge is the only variant that "
    "pays."
),
H2("6.6 Feature splitting and wedge cells: measured negative results"),
P(
    "The defect at a concave feature vertex is geometric: the dual cell "
    "wraps around the feature and its centroid falls outside one of its "
    "boundary quads. The obvious topological fix is to split the vertex "
    "star into one cell per smooth surface region. The construction is "
    "correct (watertight, volume-conserving, all invariants pass), but it "
    "does not pay:"
),
TBL([
    ["Variant (valve, 824,661 tets)", "Inverted pyramids"],
    ["Baseline dual", "895"],
    ["Full-star split, round 1", "1,215"],
    ["Full-star split, round 2", "1,218"],
    ["Concave-only split (signed concavity gate)", "1,215"],
    ["Boundary-layer-only split", "1,103"],
], [95 * mm, 45 * mm],
   align_right=[1],
   caption="Table 6.2. Feature splitting. The split cells are thin wedges that "
           "invert in their own right; the total defect count rises. The "
           "pass is off by default and the run loop keeps the best round, so "
           "it cannot make the written mesh worse."),
P(
    "The wedge-cell construction is a more local version of the same idea: "
    "insert a wedge cell that owns a concave boundary edge and the four "
    "surface quads around it, so the endpoint cells no longer wrap the "
    "corner. It is implemented as an off-by-default option; the measured "
    "split results above are the reason the default remains the plain "
    "dual. The general lesson is that a topology change does not fix a "
    "geometry problem: splitting one non-convex cell into several thin "
    "cells trades one violation for several."
),
H2("6.7 Non-planar faces"),
P(
    "The internal dual faces are rings of tet centroids around a primal "
    "edge. They are generally not planar. OpenFOAM assumes planar faces: "
    "it computes one area vector and one centroid per face, and the flux "
    "uses one normal. For a warped face this is a quadrature, and the "
    "error is second order in the warping amplitude. The dual's boundary "
    "faces are planar by construction, so the issue is internal."
),
P(
    "The converter offers three policies. <i>none</i> is the default and "
    "is byte-identical to the unfixed converter; it is the right choice "
    "when the solver tolerates warped faces and the face count matters. "
    "<i>triangulate</i> replaces every non-planar face by a fan of "
    "triangles from its centroid. The fan tiles the face's area vector "
    "exactly, so the total volume, closure, and orientation are preserved "
    "to machine precision; per-cell volumes shift only where a warped face "
    "is split. The cost is a larger face count and a normal per triangle, "
    "which can increase the non-orthogonality of the individual flux "
    "faces. <i>planarize</i> projects the interior vertices of non-planar "
    "faces onto their Newell planes. Boundary vertices are pinned. The "
    "pass is keep-best: it is accepted only if the defect count does not "
    "increase and planarity improves."
),
P(
    "A face is considered non-planar when the maximum vertex deviation "
    "from the Newell plane exceeds <i>planarity_rel_tol</i> times the "
    "bounding-box diagonal (default 10<super>−6</super>). The same "
    "criterion is used by the measurement tool and the repair, so the "
    "report and the fix cannot disagree about what counts as warped. The "
    "choice between triangulation and planarization is a genuine "
    "trade-off, and it is one of the open questions in Section 10: a "
    "convergence condition that assumes planar faces would force "
    "triangulation or planarization; a condition that allows a consistent "
    "quadrature would not."
),
H2("6.8 Limits of the repair strategy"),
P(
    "The repair strategy has three structural limits. First, it cannot "
    "move boundary vertices without breaking the exact surface contract. "
    "Any defect whose cause is a boundary polygon is therefore outside the "
    "smoother's reach; the merge can sometimes remove the cell, but it "
    "cannot make the boundary cell convex. Second, the metrics are "
    "coupled: reducing non-orthogonality can increase skewness, and "
    "increasing compactness can increase aspect ratio. A single scalar "
    "score hides these trade-offs, which is why the code uses a "
    "total-defect acceptance with a continuous tie-break and reports the "
    "vector. Third, the "
    "keep-best contract is local and metric-specific. It guarantees "
    "non-regression under the detector, not convergence, and it says "
    "nothing about the error constant of the FV scheme."
),
P(
    "These limits are not defects of the implementation; they are "
    "properties of the problem. A polyhedral mesh generator that claims "
    "to fix every concave feature without moving the surface is claiming "
    "too much. The honest position, and the one this report takes, is to "
    "state what is guaranteed (topological validity, exact boundary in "
    "default mode, volume conservation), what is measured (star-shapedness, "
    "non-orthogonality, skewness, aspect, planarity), and what is not "
    "guaranteed (a convergence condition on arbitrary geometry)."
),

story.append(CondPageBreak(140 * mm))
H2("6.9 Inventory of repair and conditioning passes"),
P(
    "The converter exposes the passes below. The table is the operational "
    "summary: it records the constructor defaults, what each pass acts on, "
    "and its acceptance rule or measured status. The detailed derivations "
    "and measurements are in Sections 6.1–6.7 and Appendix D; the defaults "
    "are the code snapshot of Section 1.4."
),
TBL([
    ["Pass", "Default", "Acts on", "Acceptance or measured status"],
    ["Laplacian smoothing", "on (3 iterations, ω = 0.5)", "interior vertices", "total defects ↓, J tie-break; keep-best"],
    ["Compactness", "off", "vertices", "opt-in; can lower NO and raise skew (§6.4)"],
    ["Axis balance", "off", "vertices", "opt-in; targets directional aspect (§6.4)"],
    ["Zonal aspect", "off", "vertices", "opt-in; worst-cell penalty (§6.4)"],
    ["Severity guard", "off", "acceptance", "opt-in; rejects count wins that raise max NO/skew"],
    ["Concave merge", "off", "cells", "max-severity keep-best; 302/348 fixed; 340 → 46 in-process, 58 real"],
    ["Feature split", "off (0 rounds)", "topology", "measured worse: 895 → 1,215 / 1,218 (§6.6)"],
    ["Wedge cells", "off", "topology", "local alternative to split; opt-in (§6.6)"],
    ["Non-planar fix", "none", "faces", "triangulate or planarize; opt-in (§6.7)"],
    ["Collapse mode", "off", "boundary walk", "alternate topology; volume tolerance 5% (§4.5)"],
    ["Free-boundary smoothing", "off (auto on fTetWild)", "boundary vertices", "edge guard 0.5; 121 → 90 total, skew 59 → 69 (D.2)"],
    ["Boundary defects", "off (fTetWild path)", "boundary faces", "clearance 7 → 0; skew shift keep-best (§3.6, D.2)"],
], [34 * mm, 30 * mm, 28 * mm, 76 * mm],
   caption="Table 6.3. Repair and conditioning passes as implemented in the "
           "converter. Defaults are the constructor defaults at the report "
           "snapshot; the acceptance rules are those of Sections 6.1–6.7 "
           "and Appendix D."),

H1("7. Boundary-layer extrusion"),
H2("7.1 Wall selection and the normal field"),
P(
    "The boundary layer is extruded on a user-selected set of wall "
    "patches. The selected boundary faces are the <i>base faces</i>; their "
    "vertices are the <i>wall vertices</i>. The extrusion direction at a "
    "wall vertex is a single unit normal n<sub>w</sub>, shared by all "
    "prism stacks incident to that vertex. This is what makes the stacks "
    "conforming: two adjacent base faces use the same normal at their "
    "common vertices, so their side faces match exactly."
),
P(
    "Patch selection is informed by a shared role classifier: wall, "
    "inlet, outlet, symmetry, empty, unknown. A known non-wall role wins "
    "over a wall keyword (a patch named inlet_wall is treated as an "
    "inlet), because extruding prisms into an inlet is a worse failure "
    "than under-resolving a wall; a name with no role signal falls "
    "through to geometric inference."
),
P(
    "The normal field is the part of the BL algorithm where the geometry "
    "is least local. Four methods are implemented:"
),
*B([
    "<b>Area-weighted average.</b> n<sub>w</sub> is the normalized sum of "
    "the area vectors of the incident wall faces. This is cheap and robust "
    "on smooth surfaces; it can collapse at sharp creases, where the "
    "incident normals nearly cancel.",
    "<b>Most-visible normal (Alauzet).</b> Choose the unit vector that "
    "maximizes the minimum dot product with the incident face normals. "
    "This is a min–max problem on the sphere; the implementation solves it "
    "by 200 iterations of a simple update. It avoids the cancellation of "
    "the area-weighted average and is the default on feature-rich "
    "surfaces.",
    "<b>Laplacian smoothing.</b> Smooth the per-vertex normal field over "
    "the wall-vertex adjacency, with the boundary of the wall region "
    "pinned. This removes high-frequency noise but can round a genuine "
    "crease.",
    "<b>Guided bilateral filter (Zhang et al., 2015).</b> Filter the "
    "wall-face normals with a joint bilateral kernel guided by the face "
    "centroids and normals, then average to vertices. The guidance "
    "preserves creases while smoothing noise; it is the most expensive "
    "option and is opt-in.",
]),
P(
    "A feature fade reduces the extrusion height at concave corners, where "
    "the normals of the incident faces diverge. The fade is an angular "
    "factor α<sub>w</sub> ∈ (0,1] that multiplies the local height budget. "
    "It is a heuristic, not a geometric theorem: it prevents the most "
    "obvious fold-overs, and the final validation catches the rest."
),
H2("7.2 Layer heights and the geometric series"),
P(
    "Let h<sub>1</sub> be the first-layer height, r ≥ 1 the growth ratio, "
    "and n the number of layers. The cumulative height after k layers is"
),
EQ(r"H_k = h_1\,\frac{r^k-1}{r-1} \ \ (r>1), \qquad H_k = k\,h_1 "
   r"\ \ (r=1), \qquad H = H_n.", "7.1"),
P(
    "The total stack height is H. The first layer is h<sub>1</sub>, the "
    "second is r h<sub>1</sub>, and so on; the series is geometric. If the "
    "user specifies the total height H and the growth ratio, the first "
    "height is h<sub>1</sub> = H(r−1)/(r<sup>n</sup>−1); if the user "
    "specifies h<sub>1</sub>, the total is H<sub>n</sub>. The code uses "
    "the cumulative array both for the geometry and for the sliver guard, "
    "so the two cannot drift apart."
),
P(
    "The local height is not always the requested total. Each wall vertex "
    "has a budget"
),
EQ(r"B_w = \alpha_w\,\min\left(c\,d_w,\ \frac{1}{2}e_w\right),", "7.2"),
P(
    "where c is the clamp factor (default 0.5), d<sub>w</sub> is the "
    "distance from the wall vertex to the nearest non-wall vertex of its "
    "incident cells (the <i>support distance</i>), and e<sub>w</sub> is "
    "the smallest edge of the incident wall faces (the <i>edge cap</i>). "
    "The support distance bounds how far the wall can move before the "
    "adjacent core cell inverts; the edge cap bounds the stack height by "
    "the local surface resolution. The factor 1/2 is a safety margin. The "
    "edge cap can be made robust by using a percentile instead of the "
    "minimum, which prevents a single tiny edge from crushing the whole "
    "neighborhood; this is opt-in."
),
P(
    "The actual per-vertex height is h<sub>w</sub> = min(H, B<sub>w</sub>) "
    "in the simple path. In the local-termination path, the number of "
    "layers n<sub>v</sub> is reduced until the cumulative height fits the "
    "budget, and the stack is scaled by h<sub>w</sub>/H; the layer "
    "proportions are preserved, so the first layer shrinks proportionally "
    "when the stack is compressed. A floor of 10<super>−3</super>h<sub>1</sub> "
    "prevents a zero-height prism at a vertex with a tiny but positive "
    "budget."
),
P(
    "The per-vertex height field is then limited to a maximum slope along "
    "the wall edges. For every wall edge (a,b) of length L,"
),
EQ(r"|h_a-h_b| \leq \tau L, \qquad \tau = 0.5.", "7.3"),
P(
    "Each iteration moves half of every edge's excess from the higher "
    "endpoint to the lower one, then clamps every vertex to [floor, "
    "budget]. The procedure runs for at most 24 iterations and is "
    "deterministic. This is a discrete Lipschitz constraint, the same idea "
    "as cfMesh's <i>relThicknessTol</i> and snappyHexMesh's "
    "<i>maxThicknessToMedialRatio</i>. It prevents a locally deep first "
    "cell next to a thin one from spiking out of the neighborhood. It is "
    "not a projection onto the feasible set in the Euclidean sense, and "
    "the clamping can make the final field slightly infeasible at a vertex "
    "whose budget is below the local level; the validation catches the "
    "consequences."
),
P(
    "Near a non-BL boundary (an inlet, an outlet, or a patch without a "
    "layer), the budget is tapered by a smoothstep over approximately "
    "three local wall edges. The harmonic reflow is pinned to zero there, "
    "so a full-height stack at the junction would invert the core cell. "
    "The taper compresses the stack locally while keeping the requested "
    "layer count; it does not open a hole in the wall shell."
),
H2("7.3 Prism construction and conforming topology"),
P(
    "For a selected wall face f with vertices w and n<sub>f</sub> layers, "
    "the extruded points are"
),
EQ(r"p_{w,k} = p_w - n_w\,h_w\,\frac{H_k}{H}, \qquad k=1,\dots,n_w.",
   "7.4"),
P(
    "The points are shared between all stacks incident to w. The prism "
    "cell (f,k) is the polyhedron between the polygon at layer k−1 and the "
    "polygon at layer k; its base is the wall face for k=1, and its top is "
    "the translated wall face for k=n<sub>f</sub>. The top face of the "
    "last prism replaces the wall face in the original owner cell: the "
    "original cell keeps its index and becomes the core cell behind the "
    "stack. The inter-layer faces are internal faces between consecutive "
    "prisms; the side faces are shared between adjacent stacks; at the "
    "border of the BL region, a side face lies in the plane of the "
    "adjacent unselected wall face and becomes a boundary face on that "
    "patch."
),
P(
    "The construction is conforming by construction, with no hanging "
    "nodes and no transition cells. The price is that the layer count can "
    "vary from one stack to the next. The code handles this with a "
    "consistency fixpoint: a face's layer count is the minimum over its "
    "vertices, and where two stacks differ in depth, the deeper stack's "
    "extra layers become internal transition faces between the deeper "
    "prism and the original owner cell of the shallower face. This closes "
    "the step against the neighboring interior cell, never as a spurious "
    "boundary surface. The alternative — a global layer count — would "
    "either crush the healthy region or invert the concave corners."
),
P(
    "The code separates three mechanisms. The <i>complete-coverage</i> "
    "policy extrudes a compressed stack even on a flagged face, so the "
    "wall shell has no holes; only a genuinely impossible face is left "
    "bare. <i>Per-face exclusion</i> leaves sliver-affected wall faces out "
    "of the extrusion entirely and widens the exclusion by two rings, so "
    "the neighboring healthy faces are not dragged into twisted prisms. "
    "The local-termination path uses <i>binary layer counts</i>: a wall "
    "face keeps the full stack or is not extruded at all, so there are no "
    "transition bands. The default path is coverage-first with per-face "
    "exclusion for slivers; local termination is the opt-in fallback for "
    "concave geometries. The choice matters: an earlier binary policy left "
    "5.9% of the wall faces bare on a user's tube, which is unacceptable "
    "for a wall-function simulation."
),
P(
    "The top face orientation is chosen so that the original owner cell "
    "(lower index) remains the owner and the normal points from the core "
    "cell toward the prism, matching the wall face normal. The side faces "
    "are oriented geometrically using the prism centroid. Any residual "
    "mis-orientation is repaired by the global winding solve of Section "
    "7.5, which does not rely on the local centroid heuristic."
),
H2("7.4 Core reflow as a constrained harmonic extension"),
P(
    "When the stack is deeper than the local support distance, the core "
    "cells behind it must yield. The code solves a harmonic extension of "
    "the wall displacement on the dual edge graph:"
),
EQ(r"\Delta U = 0 \ \ \mathrm{on\ the\ free\ dual\ vertices}, \qquad "
   r"U = -n_w\,h_w\,\kappa_D \ \ \mathrm{on\ the\ wall}, \qquad "
   r"U = 0 \ \ \mathrm{on\ the\ non\!-\!BL\ boundary},", "7.5"),
P(
    "with κ<sub>D</sub> = 1.3. The wall Dirichlet data is over-driven by "
    "30% so that the first interior shell clears the prism top; without "
    "the over-drive, the harmonic field decays too quickly and the first "
    "core cell is crushed. The solve is 500 Jacobi sweeps with a fixed "
    "iteration count, which keeps the result deterministic and independent "
    "of thread order. The fixed count is an approximation to the harmonic "
    "solution; it is not converged to machine precision, and the report "
    "does not claim that it is."
),
P(
    "The displacement is then bounded by a relative inversion guard. "
    "Across every dual edge (a,b), the displacement magnitude may not "
    "change by more than a fraction of the edge length:"
),
EQ(r"\left|\,|U_a|-|U_b|\,\right| \leq 0.9\,|x_a-x_b|.", "7.6"),
P(
    "The guard is solved as the greatest field below the harmonic one by "
    "min-diffusion: initialize m<sub>i</sub> = |U<sub>i</sub>|, then "
    "iterate m<sub>i</sub> ← min(m<sub>i</sub>, m<sub>j</sub> + 0.9L<sub>ij</sub>) "
    "over every edge, keeping the pinned magnitudes fixed. The final "
    "displacement is U scaled to the constrained magnitude. This is a "
    "relative bound, not an absolute cap: an absolute cap would forbid a "
    "displacement larger than one cell, which is exactly the multi-cell "
    "consumption the reflow exists to provide. The measured consequence of "
    "using an absolute cap was a near-wall field clamped to about 3.3 mm "
    "while the stack needed 6.2 mm, crushing the first core cell."
),
P(
    "Two guards protect the reflow from slivers and caps. A cell whose "
    "minimum edge is below 0.1 times the target depth is pinned, and its "
    "wall faces are excluded; the harmonic field is not allowed to move a "
    "vertex across a sliver. Inlet/outlet caps are detected as coplanar, "
    "co-oriented patches normal to the flow axis, and are pinned with a "
    "one-ring rim so the junction does not invert. These are geometric "
    "heuristics, measured on real cases, not proofs."
),
P(
    "The measured effect on a tube is the strongest positive result of "
    "the BL path: the mesh goes from one layer to six layers on 100% of "
    "the wall faces, with the first-layer ratio 1.00 (true y+), and real "
    "checkMesh passes with non-orthogonality 56.75° versus 60.50° for the "
    "single-layer baseline. On the harder venturi and concave_bracket "
    "cases, local termination improves the maximum non-orthogonality "
    "(146.41° → 85.44°, 80.64° → 81.29°, 105.41° → 88.51°) but does not "
    "reach PASS and can worsen the aspect ratio on the concave case "
    "(566.6 → 1145.8). The reflow is a bounded relocation, not a "
    "whole-cell absorption; the latter cannot stitch without transition "
    "cells, which is why the code does not attempt it."
),
H2("7.5 The global winding solve"),
P(
    "Every face has a stored vertex order. For a cell to have a closed "
    "oriented boundary, the two faces of the cell that share an edge must "
    "traverse that edge in opposite directions. This gives one parity "
    "constraint per cell-edge incidence. Let x<sub>f</sub> ∈ {+1,−1} be "
    "the decision to keep or reverse face f, and let d<sub>f</sub>, "
    "d<sub>g</sub> ∈ {+1,−1} encode the direction in which the cell "
    "traverses the shared edge as seen from each face's stored winding. "
    "The constraint is"
),
EQ(r"x_f\,x_g = -\,d_f\,d_g.", "7.7"),
P(
    "The face-adjacency graph with these parity labels is solved by a "
    "breadth-first search. Each connected component has exactly two "
    "solutions, x and −x; the component sign is chosen so that the total "
    "signed volume is positive. A conflict during the BFS means that the "
    "product of the parities around a cycle is −1; the mesh is not "
    "orientable (or the stored winding has a parity inconsistency), and "
    "the solver reports it instead of silently flipping faces. The solve "
    "is deterministic and O(F+E)."
),
P(
    "The mathematical structure is a Z<sub>2</sub> cochain problem. The "
    "edge-direction signs form a 1-cochain on the face-adjacency complex; "
    "the constraint says that this 1-cochain is a coboundary. The flip "
    "variables x are a 0-cochain whose coboundary is the given 1-cochain. "
    "The obstruction is the first cohomology class H<super>1</super>(M; "
    "Z<sub>2</sub>): a mesh is orientable exactly when the class vanishes. "
    "This is the precise sense in which the solve is exact rather than "
    "iterative. The earlier per-cell greedy flip was a local search in the "
    "same space; it converged to a local minimum and left 24 cells "
    "unclosed at scale 1.0, then cascaded into 378,605 non-positive "
    "volumes at scale 0.6. The exact solve removes the cascade."
),
H2("7.6 Validation and fallbacks"),
P(
    "The BL build is validated by the same suite as the dual, with two "
    "additions. The boundary must remain a 2-manifold: every boundary edge "
    "is shared by exactly two boundary faces. The patches must tile the "
    "boundary block exactly. The cell volumes must be positive by the "
    "exact fan measure. The face-pyramid count must not exceed the input "
    "count: the BL is allowed to inherit existing violations on a concave "
    "input, but it is not allowed to add new ones. An optional BL/core "
    "volume-ratio guard checks the transition from the outermost prism to "
    "the adjacent core cell."
),
P(
    "The volume-conservation tolerance is 5×10<super>−3</super> in the BL "
    "path, not 10<super>−6</super>. The reason is deliberate shell loss: "
    "unselected boundary faces give up their wall shell (the wall face "
    "moves to the top of the stack, but no prism replaces the wedge), and "
    "the measured worst case is 1.16×10<super>−3</super> on a part with "
    "10% dropped faces. This is a safety net, not the primary correctness "
    "check; the closure test at 10<super>−8</super> catches a genuine "
    "topological hole regardless of face shape."
),
P(
    "If the build fails, the caller retries with a smaller scale, fewer "
    "layers, local termination, or a wider exclusion set. The best valid "
    "attempt is written; if none is valid, the dual without a boundary "
    "layer is written and the failure is reported. This fallback is "
    "important for production: a boundary layer that silently writes an "
    "invalid mesh is worse than no boundary layer."
),
H2("7.7 Measured behavior and limitations"),
P(
    "The BL is conforming and topologically valid on the measured cases; "
    "the winding solve closes every cell. Its geometric cost is local. On "
    "the three benchmark geometries, the average non-orthogonality is "
    "essentially unchanged (+0.0° to +0.6°), while the maximum rises "
    "sharply on the faces shared by a sliver prism and a larger core cell. "
    "The aspect ratio rises because the prisms are thin by design. The "
    "local-termination and exclusion policies reduce the damage but do not "
    "eliminate it on the hardest geometries."
),
P(
    "The honest summary is that the BL path is production-ready on "
    "well-resolved walls and marginal on geometries where the surface "
    "triangulation contains tiny faces at concave features. The tiny faces "
    "are the root cause: a prism on a tiny base is a sliver, and its "
    "shared face with the core cell has a large center-to-center distance "
    "relative to its area. The mesher can exclude those faces, which "
    "leaves a small bare patch, or extrude them, which creates an extreme "
    "metric. Neither choice is free. This is a concrete example of the "
    "kind of trade-off that a convergence condition based on a single "
    "maximum metric would penalize, and a condition based on an "
    "area-weighted flux error might not."
),
H2("7.8 Thin-feature limitation on the experimental path"),
P(
    "The in-house BL engine fails on the reference thin-feature part at "
    "every height scale, on both Gmsh- and fTetWild-derived duals: the "
    "dual cells at thin features are pancake-shaped, and any extruded "
    "stack inverts. The cause was isolated to the multi-cell consume "
    "reflow, which double-emits prism side faces on wall columns with "
    "sub-millimeter polygons and leaves ten boundary edges shared by four "
    "faces. With consume disabled the layer builds: 71,025 prism cells, a "
    "median of five layers, 95.4% coverage, and checkMesh reports the "
    "region as OK. The failure is a thin-feature limitation of the part, "
    "not of the tet stage; the fTetWild dual is about eight times cleaner "
    "than the Gmsh one on this part (30 against 263 residual defects at "
    "the coarse resolution; 90 against 338 at the fine). The GUI failure "
    "message was corrected to state this distinction."
),

H1("Part III — Guarantees, measurements, and convergence"),
H1("8. What is guaranteed, and what is only measured"),
P(
    "This section separates four levels of statement, because the "
    "distinction matters to a numerical analyst. <b>Exact invariants</b> "
    "are proved and hold to floating point. <b>Structural properties</b> "
    "hold by construction and are validated. <b>Measured behavior</b> is "
    "empirical and is reported with its provenance. <b>Open properties</b> "
    "are not guaranteed and are the subject of Section 10. A report that "
    "blurred these levels would be useless; the whole point of the "
    "exercise is to know exactly what the mesher promises."
),
H2("8.1 Exact boundary tiling"),
P(
    "<b>Proposition 8.1.</b> In the default mode, the boundary of the dual "
    "is a disjoint subdivision of the input triangulation. Each boundary "
    "triangle (a,b,c) is partitioned into three planar quads, one per "
    "vertex, with vertices at the triangle vertices, the edge midpoints, "
    "and the triangle centroid G. The union of the three quads is the "
    "triangle, their interiors are disjoint, and the total boundary area "
    "is exactly the input area."
),
P(
    "<i>Proof.</i> Let m<sub>ab</sub>, m<sub>bc</sub>, m<sub>ca</sub> be "
    "the edge midpoints. The three quads are [a, m<sub>ab</sub>, G, "
    "m<sub>ca</sub>], [b, m<sub>bc</sub>, G, m<sub>ab</sub>], and [c, "
    "m<sub>ca</sub>, G, m<sub>bc</sub>]. Each is planar because all its "
    "vertices lie in the plane of the triangle. The first quad is the "
    "union of the triangles (a, m<sub>ab</sub>, G) and (a, G, "
    "m<sub>ca</sub>), and similarly for the others. The three quads meet "
    "along the medians [m<sub>ab</sub>, G], [m<sub>bc</sub>, G], and "
    "[m<sub>ca</sub>, G], which have zero area; their interiors are "
    "therefore disjoint. The union of the three quads is the union of the "
    "six triangles (a, m<sub>ab</sub>, G), (a, G, m<sub>ca</sub>), (b, "
    "m<sub>bc</sub>, G), (b, G, m<sub>ab</sub>), (c, m<sub>ca</sub>, G), "
    "(c, G, m<sub>bc</sub>), which is the original triangle. Since the "
    "decomposition is a subdivision, the areas add exactly. Q.E.D."
),
P(
    "The construction is a barycentric subdivision at the level of the "
    "surface: the centroid is the barycentre, the midpoints are the "
    "one-dimensional barycentres. It is the simplest subdivision that is "
    "conforming with the dual cells (each quad belongs to the cell of its "
    "triangle vertex) and has no hanging nodes. The price is three "
    "boundary faces per input triangle; the benefit is that the polyhedral "
    "boundary is the input surface, not an approximation of it. This is "
    "the property that makes the default dual attractive for "
    "surface-sensitive problems."
),
H2("8.2 Exact total volume"),
P(
    "<b>Proposition 8.2.</b> For a closed input surface, the total volume "
    "of the default dual equals the input volume to floating-point "
    "accuracy. The exact fan formula is used for each cell, and the "
    "internal faces cancel in the divergence-theorem sum."
),
P(
    "<i>Proof.</i> Triangulate every face with a fan and write the volume "
    "of cell K as V<sub>K</sub> = (1/3)Σ<sub>τ∈T<sub>K</sub></sub> "
    "x<sub>τ</sub>·n<sub>τ</sub>A<sub>τ</sub>, where τ runs over the "
    "triangles of the fan triangulation of ∂K, x<sub>τ</sub> is the "
    "centroid of τ, and n<sub>τ</sub> is its outward normal. Summing over "
    "K, every internal triangle appears twice — the two adjacent cells "
    "use the same fan on the shared face — with opposite normals, and "
    "cancels; the remaining sum is over the boundary triangles. In the "
    "default mode those triangles tile the input surface, so the boundary "
    "sum is the divergence-theorem volume of the input surface, which is "
    "the input volume. The only error is floating-point round-off. Q.E.D."
),
P(
    "This is stronger than discrete conservation. Conservation says that "
    "the sum of the cell volumes is invariant under the flux; it does not "
    "say that the invariant equals the geometric volume of the input. The "
    "dual's total volume is the latter, exactly, because the boundary is "
    "not approximated. In collapse mode the proposition fails and the "
    "volume becomes an O(h²) approximation; this is quantified in "
    "Section 8.5."
),
H2("8.3 The star-shapedness certificate"),
P(
    "The pyramid test of Section 5.3 is a certificate, and it is worth "
    "stating precisely what it certifies. For a cell K with centroid "
    "x<sub>K</sub> and a face σ with area vector S<sub>σ</sub> (outward) "
    "and centroid c<sub>σ</sub>, the test is"
),
EQ(r"S_\sigma\cdot(c_\sigma-x_K) > 0.", "8.1"),
P(
    "For a planar face, (8.1) says that x<sub>K</sub> lies strictly on the "
    "interior side of the face plane. If it holds for every face, "
    "x<sub>K</sub> lies in the intersection of the interior half-spaces "
    "of all faces. For a polyhedron, that intersection is the kernel of "
    "the cell: the set of points that see the whole cell. Hence the test "
    "is a <i>sufficient</i> certificate of star-shapedness with respect to "
    "x<sub>K</sub>, and it is <i>necessary</i> for star-shapedness with "
    "respect to that same point: if a face fails, x<sub>K</sub> is on the "
    "wrong side of its plane and cannot be in the kernel. It is not "
    "necessary for the cell to be star-shaped at all: a cell can be "
    "star-shaped with respect to a different interior point and still fail "
    "the test for the computed centroid."
),
P(
    "Two qualifications matter. First, the test uses the face centroid, "
    "not every point of the face. For a planar convex face, the half-space "
    "condition is the same for every point of the face, so the centroid "
    "suffices. For a non-convex or warped face, the centroid test is a "
    "one-point quadrature of the visibility condition; it can pass while a "
    "corner of the face is hidden, or fail while the face is still "
    "visible. The dual's boundary faces are planar quads by construction; "
    "the internal faces can be warped. Second, the strict inequality "
    "excludes zero margin: a cell whose centroid lies on a face plane is "
    "not strictly star-shaped, and the code flags it. The margin "
    "m<sub>σ</sub> = S<sub>σ</sub>·(c<sub>σ</sub>−x<sub>K</sub>)/|S<sub>σ</sub>| "
    "is the pyramid height and is the natural continuous measure of how "
    "far the cell is from failing."
),
P(
    "The certificate is therefore best read as a conservative geometric "
    "filter. Passing is a proof of star-shapedness for the computed "
    "centroid under the planar-face assumption. Failing is evidence that "
    "the centroid is not in the kernel, not a proof that no point is. The "
    "merge uses this asymmetry deliberately: it looks for a cell-level "
    "change, not a different centroid, because the centroid is what the "
    "FV discretization uses."
),
H2("8.4 Exact orientability"),
P(
    "<b>Proposition 8.3.</b> The parity system (7.7) has a solution if and "
    "only if the edge-direction 1-cochain is a coboundary. The BFS finds "
    "the solution in O(F+E) time, and the component sign is fixed by "
    "requiring positive total signed volume. When the system is solvable, "
    "every cell is closed and consistently oriented by construction."
),
P(
    "<i>Proof.</i> The BFS assigns x<sub>f</sub> from the parity constraint "
    "along a spanning tree. Every non-tree edge gives a cycle; the product "
    "of the parities around the cycle is the obstruction. If it is +1, the "
    "assignment extends; if it is −1, no assignment exists. The graph has "
    "one free sign per connected component, fixed by the volume. Q.E.D."
),
P(
    "The cohomological reading is the precise one: the obstruction is the "
    "class of the 1-cochain in H<super>1</super>(M; Z<sub>2</sub>). A mesh "
    "is orientable exactly when the class vanishes. The solver reports a "
    "conflict instead of silently flipping a face, so a non-orientable "
    "input cannot produce a mesh with a hidden sign error. This is a "
    "stronger guarantee than a local orientation heuristic, and it is "
    "cheap."
),
H2("8.5 The collapse trade-off"),
P(
    "The default dual's exactness has a price: three boundary faces per "
    "input triangle. The collapse mode replaces them with one boundary "
    "face per boundary vertex, reducing the face count by roughly a factor "
    "of three at the wall. The boundary is then no longer a tiling of the "
    "input; it is a chordal approximation. The volume drift is second "
    "order in the surface mesh size: the sagitta of a chord over an arc of "
    "length h on a surface of radius R is O(h²/R), and the volume error is "
    "the integral of the sagitta over the surface, hence O(h²)."
),
P(
    "The measured sphere drift is consistent with a relative error "
    "C n<super>−2</super>, where n is the number of subdivisions:"
),
TBL([
    ["n", "Relative volume drift", "Drift × n²"],
    ["3", "−12.2%", "1.10"],
    ["4", "−7.3%", "1.17"],
    ["6", "−3.5%", "1.26"],
    ["8", "−2.0%", "1.28"],
    ["10", "−1.3%", "1.30"],
], [28 * mm, 62 * mm, 42 * mm],
   align_right=[1, 2],
   caption="Table 8.1. Collapse mode on a sphere. The drift is negative (the "
           "chordal polyhedron lies inside the sphere) and the product "
           "drift×n² is nearly constant, as expected for a second-order "
           "geometric error."),
P(
    "The trade-off is explicit and opt-in. The default preserves the "
    "surface exactly; the collapse mode trades an O(h²) volume error for a "
    "substantial face-count reduction. The report does not recommend one "
    "over the other in general: the right choice depends on whether the "
    "surface geometry or the face budget dominates the error. What it does "
    "recommend is that the choice be recorded, because a convergence study "
    "that mixes the two modes is comparing different geometries."
),
H2("8.6 Non-planar faces and the flux quadrature"),
P(
    "The geometric invariants and the flux are different objects. The "
    "invariants — closure, volume, orientation — use the exact fan "
    "decomposition and are exact even for warped faces. The flux uses one "
    "normal and one centroid per face, which is a one-point quadrature. "
    "For a face warped by δ, the quadrature error is O(δ²) relative to the "
    "face scale, and it enters the FV consistency error as an additional "
    "term. Triangulation removes the warping at the cost of more faces; "
    "planarization removes it at the cost of moving vertices. The mesher "
    "guarantees the geometry, not the flux; the quadrature is a solver "
    "choice. This division of responsibility is the right one, but it "
    "means that a convergence condition stated purely in terms of the mesh "
    "must say whether it assumes planar faces."
),
H2("8.7 Summary of guarantees"),
TBL([
    ["Property", "Default dual", "Collapse", "Merge", "Boundary layer"],
    ["Boundary = exact input surface", "Yes", "No, O(h²)", "Inherits", "Selected faces; bare excluded patches"],
    ["Total volume", "Exact (float)", "O(h²)", "Exact", "Exact up to shell loss"],
    ["Positive cell volumes", "Validated", "Validated", "Validated", "Validated"],
    ["Boundary 2-manifold", "Validated", "Validated", "Validated", "Validated"],
    ["Orientability", "Exact solve", "Exact solve", "Exact solve", "Exact solve"],
    ["Star-shapedness", "Measured, not guaranteed", "Measured", "Improved, not guaranteed", "Inherited / improved"],
    ["Face count", "Reduced ~5.5–6×", "Further reduction", "Reduced", "Increased"],
    ["Convergence condition", "Not claimed", "Not claimed", "Not claimed", "Not claimed"],
], [42 * mm, 30 * mm, 26 * mm, 30 * mm, 40 * mm],
   caption="Table 8.2. The guarantee structure. The last row is the most "
           "important: no configuration claims a convergence theorem. The "
           "table is a map of what can be assumed in a proof and what must "
           "be measured."),
P(
    "The table is deliberately blunt. The default dual is exact in its "
    "geometry and honest about its shape quality. The collapse mode is an "
    "approximation with a known order. The merge and the boundary layer "
    "are repairs with measured, not proved, behavior. A reader who needs a "
    "theorem should use the exact columns as hypotheses and treat the "
    "measured columns as data."
),

H1("9. Measured quality on representative geometries"),
P(
    "The numbers in this section come from the in-process checkMesh replica "
    "unless a row is explicitly labelled as a production-converter run. "
    "The distinction matters: the in-process detector is the one that "
    "drives the repairs, while the production converter is the one that "
    "writes the OpenFOAM case. The two use the same formulas but not "
    "always the same converter path, and the report does not mix them "
    "silently."
),
H2("9.1 The block with a cylindrical bore"),
TBL([
    ["Quantity", "Value"],
    ["Input tetrahedra", "251,048"],
    ["Dual polyhedral cells", "47,528"],
    ["Polyhedral fraction", "100%"],
    ["Real checkMesh", "Mesh OK"],
    ["Mis-oriented faces", "0"],
    ["Maximum skewness", "2.25"],
    ["Maximum non-orthogonality", "54.43° (input tets: 66.55°)"],
    ["Maximum aspect ratio", "4.47"],
    ["Total volume", "Identical to input"],
    ["Conversion time", "8.8 s"],
], [78 * mm, 70 * mm],
   caption="Table 9.1. Block with a cylindrical bore. The dual improves every "
           "checkMesh metric relative to the input tetrahedra and preserves "
           "the volume. This is the clean case that establishes the "
           "construction is not inherently low quality."),
P(
    "The run-to-run stability is also good: three conversions of the same "
    "geometry with slightly different input triangulations all produced "
    "maximum skewness between about 2 and 2.3, with Mesh OK. The variation "
    "is in the input, not in the converter; the dual is deterministic for "
    "a fixed input."
),
H2("9.2 The industrial valve"),
P(
    "The valve is the hard case. It has thin walls, concave feature edges, "
    "and a surface triangulation with slivers. The in-process baseline and "
    "the pairwise merge are:"
),
TBL([
    ["Configuration", "Cells", "Pyramids", "max θ", "avg θ", "max skew", "Failed"],
    ["Dual baseline", "152,086", "340", "95.62°", "10.57°", "22.16", "3"],
    ["Merge A (keep-best)", "151,763", "46", "94.30°", "10.70°", "21.17", "3"],
], [40 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 18 * mm],
   align_right=[1, 2, 3, 4, 5, 6],
   caption="Table 9.2. Industrial valve, collapsed-boundary production "
           "dual (152,086 cells), in-process detector. The merge fixes "
           "302 of 348 concave cells (86.8%) and reduces the pyramid count "
           "by 86.5%, but the maximum non-orthogonality remains above 90° "
           "and three quality gates still fail."),
P(
    "The failure is heavy-tailed and boundary-dominated. In the diagnostic "
    "decomposition of the earlier 895-defect snapshot (the run behind "
    "Table 9.3), almost all inverted pyramids were boundary faces; the "
    "normals of the two faces meeting at the defect span a wide angle, up "
    "to about 130°, versus about 1° on healthy faces; the cell volumes are "
    "normal; the defects are localized at concave feature edges and affect "
    "a small fraction of the cells. This is not a volume collapse or a "
    "topological hole. It is a local geometric configuration in which the "
    "cell centroid falls outside a boundary quad, and the face normal is "
    "nearly perpendicular to the line of centers."
),
P(
    "The maximum 95.62° deserves a comment, because it is more than a "
    "quality number. The non-orthogonality is "
    "θ = arccos(d·S/(|d||S|)), where d is the center-to-center vector and "
    "S the face area vector. If θ &gt; 90°, then d·S &lt; 0, and the "
    "over-relaxed two-point transmissibility |S|/(d·n) is <i>negative</i>. "
    "The coefficient of the neighbor in the discrete operator changes "
    "sign; the matrix loses the M-matrix property; the correction term "
    "must carry the flux. This is a qualitative change in the operator, "
    "not a small perturbation. The average 10.57° hides it completely. "
    "This single fact is, in the author's view, the strongest argument for "
    "a maximum-based or flux-weighted condition rather than an "
    "average-based one."
),
P(
    "The sign flip also has an algebraic reading. With a positive "
    "neighbour coefficient the discrete operator is out of the M-matrix "
    "class, and the iterative solvers that rely on that structure — "
    "preconditioned conjugate gradient, GAMG, the Gauss–Seidel smoothers "
    "— do not inherit its guarantees: the linear convergence rate "
    "degrades, and without a robust handling of the non-orthogonal "
    "correction (implicit treatment, limited corrections, extra corrector "
    "sweeps, relaxation) the linear solve can stall or diverge even "
    "though the mesh itself is valid. Section 10.2 develops the point."
),
P(
    "The production converter gives a different view, because it uses a "
    "terminal-face boundary policy rather than the exact three-quad "
    "tiling. An older production run on 824,661 tetrahedra produced:"
),
TBL([
    ["Policy", "Coverage", "Mis-oriented", "max skew", "NO errors", "max aspect", "Time"],
    ["Terminal-face", "80%", "3,545", "34.94", "5", "1,240 (FAIL)", "62.7 s"],
    ["Dual (older prod.)", "100%", "895", "44.33", "55", "779 (OK)", "33.7 s"],
], [34 * mm, 20 * mm, 24 * mm, 20 * mm, 20 * mm, 28 * mm, 18 * mm],
   align_right=[1, 2, 3, 4, 5, 6],
   caption="Table 9.3. Production converter, older run, 824,661 tetrahedra. "
           "These numbers are not directly comparable to Table 9.2: the "
           "converter path and the input triangulation differ. They are "
           "included because they show the coverage/quality trade-off "
           "between the two boundary policies."),
P(
    "A second production run on a finer input triangulation produced full "
    "coverage, a similar number of mis-oriented faces, higher skewness, "
    "and three failed gates. The input-to-input variation is large; the "
    "qualitative conclusion is stable: the valve is a valid mesh with a "
    "small number of extreme boundary faces, and the exact geometry does "
    "not guarantee quality."
),
H2("9.3 The venturi and the merge family"),
TBL([
    ["Configuration", "Cells", "Pyramids", "max θ", "avg θ", "max skew"],
    ["Dual baseline", "21,798", "224", "97.18°", "11.72°", "18.30"],
    ["Merge A (keep-best)", "21,654", "109", "—", "—", "—"],
    ["Merge + global smoother (C5 guard)", "21,660", "110", "97.13°", "12.09°", "18.09"],
], [52 * mm, 24 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm],
   align_right=[1, 2, 3, 4, 5],
   caption="Table 9.4. Venturi. The merge halves the pyramid count; the global "
           "smoother does not improve the maximum, and slightly increases "
           "the average. This is a negative result for smoothing on "
           "boundary-dominated defects."),
P(
    "The venturi family is useful because the geometry is fixed and the "
    "repair changes only the cells. The pyramid count halves, but the "
    "maximum non-orthogonality is essentially unchanged. The merge "
    "removes cells that fail the pyramid "
    "test; it does not remove the extreme face that causes the maximum. "
    "This is the same lesson as the valve, in a cleaner setting: the "
    "pyramid count and the maximum non-orthogonality are different "
    "defects, and a repair that targets one does not necessarily target "
    "the other."
),
H2("9.4 The boundary-layer cost"),
TBL([
    ["Geometry", "Dual max θ", "Dual verdict", "+BL max θ", "+BL verdict", "Dual aspect", "+BL aspect", "Local term."],
    ["cube_duct", "51.87°", "PASS", "80.64°", "FAIL", "7.5", "554.2", "81.29°"],
    ["venturi", "74.00°", "FAIL", "146.41°", "FAIL", "—", "108.7", "85.44°"],
    ["concave_bracket", "62.66° (+36 pyr.)", "FAIL", "105.41°", "FAIL", "—", "566.6", "88.51°"],
], [31 * mm, 23 * mm, 18 * mm, 21 * mm, 18 * mm, 18 * mm, 18 * mm, 21 * mm],
   caption="Table 9.5. Boundary-layer cost on three geometries. The average "
           "non-orthogonality is flat (+0.0° to +0.6°), while the maximum "
           "rises sharply on the faces shared by a sliver prism and a larger "
           "core cell. Local termination is the decoupled-vertex strategy "
           "with local height retry."),
P(
    "The numbers tell a consistent story. The BL is conforming and "
    "topologically valid, and on the tube it is a clear success (six "
    "layers, true first-layer ratio, and a real checkMesh pass). On the "
    "three harder geometries it raises the maximum and the aspect ratio. "
    "The cause is the tiny wall face: a small fraction of the boundary "
    "faces on the venturi are below 10<super>−6</super> m². A prism on "
    "such a base is a sliver, and the face it shares with the core cell "
    "has a center-to-center distance much larger than its size. The "
    "local-termination policy recovers much of the venturi and concave gap "
    "but not the cube_duct gap, and it worsens the concave aspect ratio. "
    "There is no free lunch: the exclusion and termination policies trade "
    "one metric for another."
),
H2("9.5 Average versus maximum"),
TBL([
    ["Geometry", "Avg θ", "Max θ", "Ratio", "Cells", "Pyramids"],
    ["Valve", "10.57°", "95.62°", "9.0", "152,086", "340"],
    ["Venturi", "11.72°", "97.18°", "8.3", "21,798", "224"],
], [34 * mm, 22 * mm, 22 * mm, 20 * mm, 26 * mm, 24 * mm],
   align_right=[1, 2, 3, 4, 5],
   caption="Table 9.6. The maximum is about eight to nine times the average. "
           "A condition based on the average would accept both meshes; a "
           "condition based on the maximum rejects them. The choice of norm "
           "is not a technicality."),
P(
    "The distribution is heavy-tailed: a few hundred faces out of tens or "
    "hundreds of thousands carry the maximum. An area-weighted norm would "
    "be closer to the average; a face-counted maximum is dominated by the "
    "tail; a flux-weighted norm would weight each face by the flux it "
    "actually carries, which depends on the solution. The report does not "
    "know which norm the right condition uses. It provides the data for "
    "all three, and it recommends that a convergence study report the "
    "maximum, the area-weighted mean, and the fraction above the threshold, "
    "rather than a single number."
),

H1("10. Relation to convergence theory for cell-centered FVM"),
H2("10.1 The EGH setting and the orthogonality condition"),
P(
    "The classical convergence theory for cell-centered finite volumes is "
    "the one of Eymard, Gallouët, and Herbin (2000). For the model problem"
),
EQ(r"-\nabla\cdot(\kappa\nabla u)=f \ \ \mathrm{in}\ \Omega, \qquad "
   r"u=0 \ \ \mathrm{on}\ \partial\Omega,", "10.1"),
P(
    "with κ symmetric positive definite, an <i>admissible</i> mesh is one "
    "in which, for every internal face σ = K|L, the line joining the cell "
    "centres x<sub>K</sub> and x<sub>L</sub> is orthogonal to σ and meets "
    "it at a point x<sub>σ</sub>. The two-point flux is"
),
EQ(r"F_{K\sigma}=\tau_\sigma\,(u_K-u_L), \qquad "
   r"\tau_\sigma=\frac{|\sigma|\,\kappa_\sigma}{d(x_K,x_L)}.", "10.2"),
P(
    "On admissible meshes and regular families, the scheme converges at "
    "first order in the discrete H<super>1</super> norm and at second "
    "order in L<super>2</super>. The orthogonality is not a technical "
    "convenience: it is what makes the two-point flux consistent, because "
    "the difference u<sub>K</sub>−u<sub>L</sub> then approximates the "
    "normal derivative along the same line that the transmissibility "
    "uses."
),
P(
    "The dual mesh is not admissible in this sense. The cell centres are "
    "barycentres of polyhedra, and the line joining two centres is "
    "generally not normal to the shared face. The over-relaxed correction "
    "is an attempt to enlarge the admissible class; it is not the EGH "
    "two-point scheme, and its convergence is not covered by the EGH "
    "theorem. This distinction is the starting point of everything that "
    "follows."
),
H2("10.2 The over-relaxed correction and its limits"),
P(
    "Let n = S/|S| be the unit face normal, d = x<sub>L</sub>−x<sub>K</sub> "
    "the center-to-center vector, d<sub>n</sub> = d·n the normal distance, "
    "and d<sub>t</sub> = d − d<sub>n</sub>n the tangential part. Since "
    "u<sub>L</sub>−u<sub>K</sub> = ∇u·d = ∇u·n d<sub>n</sub> + "
    "∇u·d<sub>t</sub>, the normal derivative is"
),
EQ(r"\nabla u\cdot n = \frac{u_L-u_K}{d_n} - \frac{\nabla_\sigma u\cdot d_t}{d_n},"
   "10.3"),
P(
    "and the flux is |σ| κ times this expression. The first term is the "
    "two-point flux; the second is the non-orthogonal correction. On an "
    "orthogonal mesh d<sub>t</sub> = 0 and the correction vanishes. On a "
    "non-orthogonal mesh it does not, and its relative size is governed by "
    "tan θ, where θ is the angle between d and n."
),
P(
    "Three consequences follow. First, the correction is not a small "
    "higher-order term in general: it is proportional to the tangential "
    "gradient and does not vanish under refinement unless the mesh becomes "
    "orthogonal or the solution becomes locally one-dimensional. It is a "
    "consistency term, not a quadrature term. Second, the coefficient "
    "1/d<sub>n</sub> changes sign at θ = 90°. For θ &gt; 90°, the "
    "two-point part of the flux has the wrong sign, and no amount of "
    "correction can restore the M-matrix sign pattern of the two-point "
    "stencil. The correction can be included implicitly, but the resulting "
    "matrix is not an M-matrix in general, and the discrete maximum "
    "principle is not guaranteed. Third, the correction requires a face "
    "gradient, which in turn requires a reconstruction from cell values; "
    "the accuracy of that reconstruction controls the accuracy of the "
    "correction. This is why the corrected scheme is often second-order "
    "for linear solutions but not uniformly second-order for smooth ones."
),
P(
    "The valve's 95.62° face is exactly the θ &gt; 90° case. The two-point "
    "coefficient is negative. The mesh is valid in the sense of positive "
    "volumes, closure, and manifold boundary, but the two-point operator "
    "has a sign anomaly. A convergence condition that permits θ &gt; 90° "
    "must therefore rely on the correction for the leading flux, not on "
    "the two-point stencil. A condition that requires θ &lt; 90° is "
    "sufficient for the sign pattern but not necessary for consistency. "
    "The choice between these positions is a mathematical question, not a "
    "meshing preference, and it is one of the questions this report puts "
    "to the reader."
),
P(
    "Two operational consequences close the subsection. The first is "
    "algebraic. A negative τ<sub>σ</sub> turns the off-diagonal entry "
    "−τ<sub>σ</sub> positive: the matrix is no longer an M-matrix, and "
    "if the negative mass is large enough relative to the diagonal, "
    "positive definiteness itself is at risk. The preconditioned "
    "conjugate gradient method then leaves the hypotheses under which it "
    "is guaranteed to work; the geometric–algebraic multigrid solver and "
    "the Gauss–Seidel smoothers it relies on degrade, and the linear "
    "iteration slows down, stalls, or diverges unless the non-orthogonal "
    "correction term is handled robustly — implicitly, with limited "
    "corrections, with additional corrector sweeps, or with field "
    "relaxation. This is the operationally sharpest reason for the mesher "
    "to export the sign-violating faces rather than a single average: "
    "the solver configuration can then be matched to the geometry "
    "instead of guessed."
),
P(
    "The second is the anisotropic case. With a tensor κ the analysis of "
    "this subsection is unchanged in structure once the scalar κ is "
    "replaced by the normal component κ<sub>n</sub> = n·κn of the tensor: "
    "the two-point coefficient is "
    "|S<sub>σ</sub>| κ<sub>n,σ</sub>/(d<sub>σ</sub>·n<sub>σ</sub>), and "
    "the correction term carries the component of κn tangential to the "
    "face — the cross-diffusion coupling. Geometry and anisotropy enter "
    "the same explicit term, so a condition stated in the purely "
    "geometric θ under-measures the correction exactly when κ is rotated "
    "with respect to the mesh, even though the sign boundary of the "
    "implicit part remains θ = 90° for positive-definite κ. Section 10.7 "
    "lists this among its questions to the reader."
),
H2("10.3 Generalized finite-volume schemes"),
P(
    "The modern answer to non-orthogonality is to abandon the two-point "
    "flux and use a scheme designed for polyhedra. The Gradient "
    "Discretisation Method (Droniou, Eymard, Gallouët, Guichard, and "
    "Herbin, 2018) defines a discrete gradient and a consistent flux, and "
    "proves convergence under three hypotheses: coercivity, consistency, "
    "and compactness. The gradient reconstruction on a cell is a "
    "Green–Gauss-type operator,"
),
EQ(r"\nabla_K u = \frac{1}{|K|}\sum_{\sigma\in\partial K}|\sigma|\,"
   r"(u_\sigma-u_K)\,n_\sigma,", "10.4"),
P(
    "where u<sub>σ</sub> is a face value obtained by interpolation. The "
    "star-shapedness of K with respect to x<sub>K</sub> is a natural "
    "geometric hypothesis for this reconstruction: it gives a positive "
    "pyramid partition of the cell, a positive volume for each pyramid, "
    "and a control of the interpolation error by the pyramid heights. "
    "Mimetic finite differences (Brezzi, Lipnikov, and Simon, 2005; "
    "Lipnikov, Manzini, and Shashkov, 2014) and virtual elements (Beirão "
    "da Veiga et al., 2013) take a different route: they keep face "
    "unknowns and add a stabilization, which makes them more robust to "
    "non-orthogonality at the cost of a larger stencil."
),
P(
    "The theory measures the error in a discrete H<super>1</super> norm "
    "built on the discrete gradient. For a scheme with cell unknowns "
    "u<sub>K</sub> and, where the discretization carries them, face "
    "unknowns u<sub>σ</sub>, a standard instance on the polyhedral "
    "complex is"
),
EQM([
    r"\|u\|^2_{1,\mathcal{D}} = \sum_K |K|\,u_K^2 + \sum_K |K|\,|\nabla_K u|^2",
    r"+ \sum_{\sigma\in\mathcal{F}_{\mathrm{int}}} |\sigma|\,h_\sigma\,"
    r"(u_\sigma-u_K)^2,",
], "10.5"),
P(
    "where the first sum is the L<super>2</super> norm of the cell "
    "values, the second is the volume-weighted sum of the squared "
    "reconstructed gradients — the discrete analogue of the "
    "H<super>1</super> seminorm — and the third, present only when the "
    "scheme carries face unknowns, is the L<super>2</super> norm of the "
    "jump between the face value and its interpolation from the cell over "
    "the internal faces, weighted by the face area and a length "
    "h<sub>σ</sub>. On the dual mesh that length is the pyramid height of "
    "Section 10.4: its minimum enters the coercivity constant, and the "
    "jump term controls the face traces. The GDM convergence theorem is "
    "stated with the gradient and L<super>2</super> reconstruction norms; "
    "(10.5) is the discrete H<super>1</super> graph norm of this family, "
    "and a consistent discretization is first order in it for a smooth "
    "solution, the constant depending on the geometry and not on h."
),
P(
    "This report does not claim that the dual mesh satisfies any of these "
    "theories. It claims something weaker and more useful: the dual "
    "provides the geometric quantities that these theories need, and it "
    "provides controlled families in which those quantities vary "
    "independently. That is the role of a mesher in a convergence study."
),
H2("10.4 What star-shapedness actually buys"),
P(
    "For a cell K with centroid x<sub>K</sub> and a face σ, the pyramid "
    "over σ has volume"
),
EQ(r"V_{K\sigma}=\frac{1}{3}\,h_\sigma\,|\sigma|, \qquad "
   r"h_\sigma=\frac{S_\sigma\cdot(c_\sigma-x_K)}{|\sigma|}.", "10.6"),
P(
    "If h<sub>σ</sub> &gt; 0 for every face, the pyramids tile K and their "
    "volumes sum to |K|. The height h<sub>σ</sub> is the star-shapedness "
    "margin. It gives four things: (i) a positive partition of the cell, "
    "which is the geometric hypothesis for the gradient reconstruction; "
    "(ii) an exact volume formula, which is what makes Proposition 8.2 "
    "work; (iii) a natural definition of the cell gradient from face "
    "values; (iv) a quantitative conditioning measure, since the "
    "reconstruction's sensitivity to face values scales like "
    "1/h<sub>σ</sub>. A cell can pass the test with a tiny margin and "
    "still be ill-conditioned; the margin, not just the sign, is the "
    "useful number."
),
P(
    "The other metrics control different parts of the error. The "
    "non-orthogonality controls the alignment between the center line and "
    "the face normal, hence the size of the correction and the sign of the "
    "two-point coefficient. The skewness controls the tangential "
    "displacement between the face centroid and the intersection of the "
    "center line with the face plane, hence the interpolation error. The "
    "aspect ratio controls the anisotropy and the constants in the discrete "
    "Poincaré and coercivity inequalities. The planarity controls the face "
    "quadrature. None of these alone implies convergence; together they "
    "are the geometric inputs of the analysis."
),
H2("10.5 A checklist for a convergence condition"),
TBL([
    ["Quantity", "Definition", "Role", "Field", "Threshold"],
    ["Star margin", "h<sub>σ</sub> = S<sub>σ</sub>·(c<sub>σ</sub>−x<sub>K</sub>)/|σ|",
     "Positive pyramid partition; conditioning", "min h<sub>σ</sub>", "&gt; 0"],
    ["Pyramid count", "# faces with h<sub>σ</sub> ≤ 0", "Violated cells",
     "count", "0"],
    ["Non-orthogonality", "θ<sub>σ</sub> = arccos(d·n/|d|)",
     "Consistency; M-matrix sign", "max, avg, area-weighted", "70° gate; 90° sign"],
    ["Skewness", "Tangential offset / face extent", "Gradient error",
     "max", "4.0"],
    ["Aspect ratio", "Directional area sums", "Anisotropy; Poincaré constant",
     "max", "1000"],
    ["Planarity", "Max deviation / bbox diagonal", "Face quadrature",
     "max", "10<super>−6</super>"],
    ["Closure", "|ΣS| / Σ|S|", "Conservation", "max", "10<super>−8</super>"],
    ["Manifoldness", "Boundary edge face count", "Well-posed boundary",
     "count", "2"],
    ["Orientability", "BFS parity conflict", "Sign consistency",
     "yes/no", "none"],
    ["Volume ratio", "min / max cell volume", "BL/core transition",
     "optional", "—"],
], [30 * mm, 46 * mm, 38 * mm, 30 * mm, 24 * mm],
   caption="Table 10.1. A checklist of geometric quantities, their role in a "
           "cell-centered FV analysis, the field exported by the mesher, and "
           "the production threshold. The 90° entry is a sign boundary, not "
           "a quality gate: above it the two-point coefficient is negative."),
P(
    "The table is offered as a template, not as a theorem. The thresholds "
    "in the last column are the production gates; they are not derived "
    "from a convergence proof. The point of the table is to make the "
    "conversation precise: a condition can be stated in terms of these "
    "quantities, and the mesher can measure all of them."
),
H2("10.6 What PolyFoamMesh can test"),
P(
    "The mesher can generate controlled families in which the geometric "
    "quantities vary independently. The sphere collapse is a one-parameter "
    "family with a known volume drift. The merge is a family from 340 to "
    "46 pyramids on the valve at fixed geometry. The smoother is a family "
    "of non-orthogonality at fixed topology. The boundary layer is a "
    "family of anisotropic cells with a controlled first-layer height. The "
    "valve is a valid mesh with a 95.62° face and a negative two-point "
    "coefficient. These are exactly the test cases a convergence condition "
    "needs."
),
P(
    "The valve is the sharpest of them. If a corrected scheme converges on "
    "the valve for a smooth solution, then θ &lt; 90° and star-shapedness "
    "are not necessary for convergence. If it fails, the valve is a "
    "concrete counterexample to the sufficiency of validity alone. Either "
    "outcome is informative, which is the mark of a good test case. The "
    "sphere collapse plays the same role for exact volume: if the scheme "
    "converges at the same order despite the O(h²) volume drift, then "
    "exact volume is not necessary for the rate, only for the constant."
),
H2("10.7 Questions for the reader"),
*B([
    "Is star-shapedness necessary, sufficient, or neither for the "
    "convergence of the over-relaxed corrected TPFA on polyhedral meshes?",
    "Is the relevant non-orthogonality the maximum, an area-weighted norm, "
    "or a flux-weighted norm? The valve and venturi are test cases where "
    "the three answers differ.",
    "Are non-planar faces admissible if the flux uses a consistent "
    "quadrature, or must the mesh be triangulated or planarized?",
    "Does the exact boundary tiling matter for the convergence rate, or "
    "only for the geometric error? The collapse mode is the controlled "
    "experiment.",
    "For anisotropic κ, should the condition be weighted by κ (the angle "
    "between κn and d), rather than by n and d? The dual's aspect ratio "
    "suggests that the answer may depend on the anisotropy, and a tensor "
    "κ adds a cross-diffusion term that no scalar angle can represent.",
    "Can the valve's 95.62° face be used as a sharp counterexample to a "
    "maximum-based condition, or does the correction restore convergence?",
]),

H1("11. A concrete research agenda"),
P(
    "The report closes with a protocol, not a promise. The protocol is "
    "designed so that a numerical analyst can test a convergence condition "
    "on PolyFoamMesh meshes without having to trust the mesher's own "
    "quality claims. Every step is reproducible from the exported data."
),
H2("11.1 Export the certificate"),
P(
    "The first deliverable is a per-cell and per-face certificate. For each "
    "cell: id, volume, centroid, minimum star margin, maximum "
    "non-orthogonality, maximum skewness, aspect ratio, maximum planarity "
    "deviation, and face count. For each face: id, area, normal, centroid, "
    "owner, neighbour, non-orthogonality, skewness, and planarity. For the "
    "mesh: the global counts and the thresholds of Table 10.1. The "
    "certificate is a CSV/JSON sidecar; it does not change the OpenFOAM "
    "case and it does not require a solver run. It turns the report into a "
    "dataset that can be joined with the discretization error."
),
H2("11.2 A manufactured solution"),
P(
    "The model problem is (10.1) on the unit cube with a spherical or "
    "cylindrical hole, with κ = I and the exact solution"
),
EQ(r"u(x,y,z)=\sin(\pi x)\sin(\pi y)\sin(\pi z), \qquad "
   r"f=3\pi^2 u,", "11.1"),
P(
    "and homogeneous Dirichlet data. The solution is smooth and vanishes "
    "on the boundary of the cube; the hole boundary is also Dirichlet. The "
    "error is measured in L<super>2</super>, H<super>1</super>, and "
    "L<super>∞</super> against the exact solution. Three schemes are run "
    "on the same meshes: the plain two-point TPFA, the over-relaxed "
    "corrected scheme, and a generalized scheme (GDM or a mimetic scheme). "
    "The observed order p is fitted from error ≈ C h<super>p</super>."
),
P(
    "The hypothesis is not that one scheme wins. It is that the "
    "<i>geometric predictor</i> of the order differs between schemes: "
    "TPFA should degrade when the maximum non-orthogonality crosses 90°, "
    "the corrected scheme should be more robust but lose monotonicity, and "
    "the generalized scheme should be robust with a larger constant. If "
    "the data show this, the geometric quantities in Table 10.1 are the "
    "right predictors; if not, the condition needs a different norm."
),
H2("11.3 Mesh sequences and controlled perturbations"),
P(
    "Generate sequences with n = 10, 20, 40, and 80 cells per direction. "
    "For each n, generate the dual, dual + smoothing, dual + merge, "
    "dual + boundary layer, and collapse mode. The first four share the "
    "topology at fixed n; the last changes the boundary geometry. Then "
    "generate controlled perturbations on the finest mesh: move one "
    "interior vertex by δ = 0.01h, 0.1h, and 0.5h to create a known "
    "non-orthogonality; warp one internal face by the same fractions; and "
    "rotate an anisotropic κ. For each perturbation, record the geometric "
    "quantities and the error. The result is a scatter of error versus "
    "each quantity, not a single pass/fail."
),
H2("11.4 Necessity and sharpness"),
P(
    "Three experiments test necessity. First, the valve: a valid mesh with "
    "340 pyramids and a 95.62° face. If the corrected scheme converges on "
    "a smooth solution on this geometry, star-shapedness and θ &lt; 90° "
    "are not necessary. Second, the sphere collapse: a family with a "
    "known O(h²) volume drift. If the convergence order is unchanged, "
    "exact volume is not necessary for the rate. Third, the merge family: "
    "the pyramid count falls from 340 to 46 at fixed geometry. If the "
    "error does not improve monotonically with the count, the condition is "
    "not a simple count."
),
P(
    "Sharpness is the other half. A condition is sharp if it is not only "
    "sufficient but also close to necessary: there are meshes just outside "
    "the condition that fail, and meshes just inside that pass. The "
    "perturbation study is designed to find that boundary. A condition "
    "that is sufficient but far from necessary is not wrong, but it is "
    "expensive; a condition that is necessary but not sufficient is not "
    "usable as a design rule. The report does not prejudge which one the "
    "reader's condition is."
),
H2("11.5 Proposed collaboration"),
P(
    "The mesher can provide the certificate, the meshes, and the "
    "controlled families. The reader's group can run the solver and the "
    "convergence study. A joint paper could compare the geometric "
    "condition with the observed order on the five families, with the "
    "valve and the collapse as the two sharp ends. The author would "
    "welcome a precise statement of the condition in the language of "
    "Table 10.1; the mesher can then be instrumented to report exactly "
    "that quantity, and the loop closes."
),
H1("12. Conclusion"),
P(
    "PolyFoamMesh's polyhedral core is a geometric construction with a "
    "small set of exact invariants and a larger set of measured "
    "properties. The default barycentric dual subdivides the input "
    "boundary exactly, preserves the total volume to floating point, and "
    "orients every cell by an exact Z<sub>2</sub> solve. It produces valid "
    "polyhedra on smooth geometries and reduces the cell count by roughly "
    "a factor of six relative to the input tetrahedra. On the block with a "
    "bore it improves every checkMesh metric. On the industrial valve it "
    "produces a valid mesh that still fails three quality gates, with a "
    "95.62° face whose two-point coefficient is negative."
),
P(
    "The repair loops are built around a keep-best contract: a pass is "
    "accepted only if it does not increase the defect count and does not "
    "violate validity, and the best configuration is the one written. The "
    "pairwise merge fixes 86.8% of the concave cells on the valve and "
    "halves the pyramid count on the venturi; the Laplacian smoother "
    "cannot fix boundary-dominated defects; feature splitting makes the "
    "count worse and is off by default; non-planar faces are handled by "
    "triangulation or planarization, both opt-in. The negative results are "
    "part of the result, not an appendix to it."
),
P(
    "The boundary layer is conforming, and the global winding solve makes "
    "it topologically exact. On a well-resolved tube it delivers six "
    "layers with a true first-layer ratio and a real checkMesh pass. On "
    "sliver-heavy surfaces it raises the maximum non-orthogonality and the "
    "aspect ratio; local termination recovers part of the gap but not all "
    "of it. The tiny wall faces are the root cause, and the choice between "
    "excluding them and extruding them is a genuine trade-off."
),
P(
    "The report does not claim a convergence theorem. It claims that the "
    "mesher can measure the geometric quantities a theorem needs, that it "
    "can generate controlled families in which those quantities vary, and "
    "that it can export the data for a numerical study. The question that "
    "remains is the reader's: which condition on these quantities is "
    "necessary and sufficient for the convergence of a cell-centered FV "
    "scheme on polyhedral meshes? The mesher is ready to test the answer."
),

H1("Appendix A. Pseudocode"),
P(
    "The following blocks are compact pseudocode for the algorithms "
    "described in the text. They are not the production code; they are "
    "intended to make the control flow and the invariants explicit. The "
    "production implementation adds bookkeeping, error handling, and "
    "parallel chunks, but the mathematical steps are these."
),
H2("A.1 Dual construction"),
CODE(
"""build_dual(primal):
    cells = one polyhedral cell per primal vertex
    faces = one quad ring per primal edge
    for each boundary triangle (a, b, c):
        G = (a + b + c) / 3
        add_quad(cell[a], [a, mid(a,b), G, mid(c,a)])
        add_quad(cell[b], [b, mid(b,c), G, mid(a,b)])
        add_quad(cell[c], [c, mid(c,a), G, mid(b,c)])
    solve_windings()
    compute_centres_and_volumes()
    validate()"""
),
story.append(CondPageBreak(45 * mm))
H2("A.2 Cell centre by the centre-point decomposition"),
CODE(
"""cell_centre(cell):
    c_est = mean(face-centre coordinates)
    for each face f of cell:
        w_f = S_f . (c_f - c_est)
        c  += w_f * (0.75 * c_f + 0.25 * c_est)
    return c / sum(w_f)"""
),
story.append(CondPageBreak(45 * mm))
H2("A.3 Keep-best Laplacian smoothing"),
CODE(
"""smooth(mesh, max_iter):
    best = mesh
    omega = 0.5
    for iter in range(max_iter):
        for each free vertex i:
            p_i' = p_i + omega * sum(w_ij * (p_j - p_i)) / sum(w_ij)
        if total_defects(new) < total_defects(best):
            best = new
        elif total_defects(new) == total_defects(best) and J(new) < J(best):
            best = new
        else:
            omega /= 2          # at most 5 backtracking halvings
    return best"""
),
story.append(CondPageBreak(45 * mm))
H2("A.4 Pairwise concave-cell merge"),
CODE(
"""merge_concave(mesh):
    for cell K with a pyramid defect:
        for L in interior neighbours of K, decreasing shared area:
            U = union(K, L)
            if valid(U) and no non-positive pyramid in U:
                merge(K, L)
                break
    repeat for at most 2 rounds
    keep a round only if pyramids fall, volumes stay positive,
    and max NO/skew do not rise"""
),
story.append(CondPageBreak(120 * mm))
H2("A.5 Boundary-layer prism stack"),
CODE(
"""extrude(wall_faces):
    for each wall vertex w:
        B_w = fade_w * min(clamp_factor * d_w, 0.5 * e_w)
        h_w = min(H, B_w)
    limit |h_a - h_b| <= 0.5 * L_ab along every wall edge
    for each wall face f:
        n_f = min over vertices w of n_w
        for k = 1..n_f:
            for w in f:
                p[w,k] = p_w - n_w * h_w * H_k / H
            build_prism([p[f,k-1], p[f,k]])
        replace the wall face of owner(f) by the top face
    solve_windings()
    validate()"""
),
story.append(CondPageBreak(45 * mm))
H2("A.6 Global winding solve"),
CODE(
"""solve_windings(faces):
    for each component of the face-adjacency graph:
        BFS: x_g = -x_f * d_f * d_g
        conflict -> report non-orientable
        if signed_volume(component) < 0: flip all x in component"""
),
story.append(CondPageBreak(45 * mm))
H2("A.7 Validation"),
CODE(
"""validate(mesh):
    assert owner.length == faces.length
    assert every neighbour index is in range
    assert patches start at n_internal and tile the boundary
    assert every boundary edge has exactly 2 boundary faces
    assert |sum S| / sum |S| < 1e-8 on every cell
    assert every volume > 0
    assert |V_total - V_input| / V_input < tol"""
),

story.append(CondPageBreak(120 * mm))
H1("Appendix B. Constants and tolerances"),
TBL([
    ["Constant", "Value", "Meaning"],
    ["skewness_max", "4.0", "checkMesh skewness gate"],
    ["non_ortho_max", "70.0°", "checkMesh non-orthogonality gate"],
    ["aspect_ratio_max", "1000.0", "checkMesh aspect-ratio gate"],
    ["optimizer threshold", "65°", "internal smoothing target"],
    ["planarity_rel_tol", "10<super>−6</super>", "max deviation / bbox diagonal"],
    ["closure tolerance", "10<super>−8</super>", "|ΣS| / Σ|S| per cell"],
    ["BL volume tolerance", "5×10<super>−3</super>", "shell-loss allowance"],
    ["_CONSUME_MAX_LAYERS", "40", "maximum consumed layers"],
    ["_CONSUME_DIRICHLET", "1.3", "wall over-drive in the reflow"],
    ["_CONSUME_SLIVER_FRAC", "0.1", "sliver threshold / target depth"],
    ["_CONSUME_MAX_SLIVER_FACE_FRAC", "0.005", "max sliver face fraction"],
    ["_CONSUME_MAX_EXCLUDED_FRAC", "0.8", "max excluded wall fraction"],
    ["_CONSUME_EXCLUSION_RINGS", "2", "sliver exclusion ring widening"],
    ["_CONSUME_CAP_ANGLE_DEG", "1.0°", "cap detection angle"],
    ["_CONSUME_CAP_PLANE_TOL_FRAC", "10<super>−4</super>", "cap planarity tolerance"],
    ["_CONSUME_CAP_MIN_PATCH_FACES", "4", "minimum cap patch size"],
    ["_CONSUME_CAP_RIM_RING", "1", "pinned rim ring at a cap"],
    ["_WALL_HEIGHT_JUMP_TOL", "0.5", "wall-height gradient limiter τ"],
    ["_WALL_HEIGHT_LIMIT_ITERS", "24", "gradient-limiter iterations"],
    ["smoothing relaxation", "0.5", "Laplacian ω"],
    ["smoothing backtracking", "5", "relaxation halvings"],
    ["reflow Jacobi", "500", "harmonic sweeps"],
    ["min-diffusion", "80", "guard iterations"],
    ["inversion guard", "0.9", "relative edge-length fraction"],
    ["collapse_volume_tolerance", "0.05", "collapse-mode volume check"],
    ["boundary_feature_angle", "90.0°", "feature-edge threshold"],
], [62 * mm, 24 * mm, 82 * mm],
   caption="Table B.1. Constants and tolerances as implemented. The first "
           "three are the production checkMesh gates; the optimizer threshold "
           "is a stricter internal target. The BL constants are the values "
           "used by the measured runs."),

story.append(CondPageBreak(45 * mm))
H1("Appendix C. Source map and reproducibility"),
TBL([
    ["Module", "Responsibility"],
    ["tet_poly_dual.py", "Dual construction, exact boundary tiling, face geometry, cell centres, defect detector, validation"],
    ["bl_poly.py", "Boundary-layer precompute, prism stacks, harmonic reflow, winding solve, validation"],
    ["poly_smoother.py", "Keep-best Laplacian smoothing; compactness, axis-balance, and zonal-aspect objectives"],
    ["poly_cell_merge.py", "Pairwise union of concave cells; virtual merge test; keep-best acceptance"],
    ["quality_thresholds.py", "Canonical quality gates and optimizer threshold"],
    ["hex_poly_dual.py", "Hex-dominant and cut-cell dual engine"],
    ["ftetwild_wrapper.py", "Experimental fTetWild tet stage: sizing, decimation, surface-fidelity controls, progress reporting"],
    ["gmsh_wrapper.py", "Default tet stage: STEP ingestion, sizing and curvature fields, tet mesh, optional baked prism layer"],
    ["native_mesher.py", "Native cut-cell hex-dominant generator from a tessellated surface (input to the hex dual)"],
    ["geometry.py", "Surface loading, local-thickness sampling, and sizing hints"],
    ["geometry_repair.py", "Vertex-snap repair and PyMeshFix fallback for non-watertight surfaces"],
    ["patch_roles.py", "Shared patch-role classifier used by boundary-layer selection"],
    ["foam_mesh_io.py", "OpenFOAM polyMesh reader/writer (ASCII and binary, faceList and faceCompactList)"],
], [44 * mm, 124 * mm],
   caption="Table C.1. The modules that implement the constructions and the "
            "measured behavior described in this report."),
P(
    "The GUI wiring that exposes these options lives in "
    "<i>params_panel.py</i> and <i>main_window.py</i>; "
    "<i>openfoam_runner.py</i> invokes checkMesh and the solver. These "
    "files are supporting infrastructure rather than numerical modules and "
    "are listed here for completeness."
),
P(
    "All numerical facts in this report were measured on the reference "
    "configuration (OpenFOAM 2512, WSL2) and recorded in the project's "
    "benchmark logs and internal notes. The source modules listed above "
    "implement the algorithms described in Parts II and III; they were not "
    "modified during the preparation of the document. The report is "
    "print-ready at A4, with embedded fonts and high-resolution equations."
),

story.append(CondPageBreak(45 * mm))
H1("Appendix D. Engineering status: roadmap and open items"),
P(
    "This appendix records the engineering status of the implementation at "
    "the time of writing, including the work completed in the fTetWild "
    "iteration (issue #12) and the items that remain open. It is included "
    "for completeness and reproducibility; it is not part of the numerical "
    "claims of Parts I–III."
),
H2("D.1 Completed in the fTetWild iteration"),
*B([
    "fTetWild tet stage with measured sizing (decimation toward the "
    "target, anti-runaway).",
    "Complete desingularization (bowtie and non-manifold split with "
    "micro-offset, equivalent to the --manifold-surface option).",
    "Outlier micro-cluster removal and a single-region guarantee.",
    "Taubin surface smoothing before the call (features preserved).",
    "Free-boundary dual smoothing (opt-in, enabled automatically on the "
    "fTetWild path).",
    "Visible tet progress (loglevel 3; FTETWILD_QUIET=1 silences it).",
    "Exact checkMesh boundary-face skewness replica (OpenFOAM 2512 "
    "boundaryFaceSkewness, bit-equal).",
    "Hard edge-length guard on free-boundary smoothing (0.5×).",
    "Boundary-pyramid clearance post-pass (measured on the reference "
    "part).",
    "BL builds at the medium resolution with consume off; the "
    "fine-resolution build is still open.",
    "The growth widget drives the y+ physics computation.",
    "Input decimation toward the target (prevents the multi-hour stall "
    "when the input surface is finer than the target).",
    "Subdivision of under-dense surfaces before decimation (the "
    "under-dense mirror of the target alignment).",
    "Surface-fidelity controls exposed (ε, stop energy, filtering, "
    "simplification; simplification off by default).",
]),
H2("D.2 Open items"),
*B([
    "Fine-resolution BL still fails validation: the drop path adds pyramid "
    "faces, and split/shift repairs reduce but do not clear them. Target: "
    "Mesh OK on the fine resolution.",
    "Skew clearance: the boundary-polygon shift can trade skew for "
    "pyramid defects, so it is kept only when the global detector count "
    "does not rise; the pyramid-clearance half is the reliable part. The "
    "free-boundary smoother reduces the total warped-boundary defect "
    "count but boundary skew rises.",
    "BL on thin-feature parts: the engine cannot build on pancake cells at "
    "any height scale. A minimum-support threshold (zero layers below "
    "k·h1) would make the failure honest instead of attempting doomed "
    "stacks.",
    "Performance: the BL precompute (normals, fade, support) and the "
    "build dominate the cost; vectorization and winding-cache reuse are "
    "the natural next step, with an estimated 2–3× gain.",
    "GUI smoke test: the live GUI has not been exercised end-to-end on the "
    "full pipeline after the latest commits; a manual run is required.",
]),
H2("D.3 Experimental status"),
P(
    "The fTetWild mesher is labelled experimental in the GUI and "
    "documented as such in the user guide. It is a speed and quality "
    "option for parts where the surface stage already resolves the "
    "geometry finely: on the 3 m pipe it is several times faster, produces "
    "an order of magnitude fewer tets, and improves the tet quality "
    "substantially. It is not a universal Gmsh replacement: on a 2 m "
    "cylindrical part it loses on time while winning on quality. The "
    "boundary-layer stage is the remaining gap on thin-feature parts and "
    "the focus of the next iteration."
),

story.append(CondPageBreak(45 * mm))
H1("Appendix E. Reproducibility and test coverage"),
P(
    "The in-process defect detector is validated against checkMesh on "
    "recorded fixtures. On the regenerated 2026-09-09 valve fixture the "
    "pyramid count matches 863 of 863; on the recorded 2026-08-01 "
    "production snapshot of Section 5.8 it matches 895 of 895. The "
    "skewness formula uses "
    "OpenFOAM's max normalization, and the boundary-face skewness replica "
    "is bit-equal to the OpenFOAM 2512 implementation on four independent "
    "duals (failure counts 59, 20, 69, 6; maxima 9.73624, 7.27144, "
    "12.43022, 6.35977)."
),
P(
    "The targeted regression set for the modules described in this report "
    "contains 72 tests: bl_engine 17, bl_gui_wiring 8, ftetwild_wrapper "
    "20, tet_poly_dual 19, poly_smoother 8. At the report snapshot the "
    "set passes under pytest; the verification protocol is pytest, ruff "
    "check, and a clean working tree."
),
TBL([
    ["Module", "Tests"],
    ["bl_engine", "17"],
    ["bl_gui_wiring", "8"],
    ["ftetwild_wrapper", "20"],
    ["tet_poly_dual", "19"],
    ["poly_smoother", "8"],
    ["Total", "72"],
], [120 * mm, 48 * mm], align_right=[1],
   caption="Table E.1. Targeted regression coverage for the modules "
           "described in this report. Counts are collected test cases at "
           "the report snapshot; the set passes under pytest."),

story.append(CondPageBreak(45 * mm))
H1("Appendix F. Notation"),
P(
    "The table collects the symbols used in the equations. It is the "
    "single notation reference for the mathematical and the engineering "
    "parts."
),
TBL([
    ["Symbol", "Meaning", "Defined in"],
    ["Ω, ∂Ω", "bounded polyhedral domain and its boundary", "§2.1"],
    ["K, |K|, x_K", "cell, its volume, its centroid", "§2.1, §5.2"],
    ["σ, |σ|", "face and its area (also written |S_σ|)", "§2.1"],
    ["S_σ, n_σ", "face area vector and unit normal", "§2.1"],
    ["x_σ", "face centroid", "§5.1"],
    ["d_σ", "x_L − x_K, the centre-to-centre vector", "§2.1"],
    ["d_n, d_t", "normal and tangential parts of d_σ", "§2.5, §10.2"],
    ["θ_σ", "non-orthogonality angle", "§5.4"],
    ["τ_σ", "over-relaxed transmissibility", "§2.5"],
    ["V_σ, c_σ", "signed pyramid volume and centroid over σ", "§2.2"],
    ["h_σ", "pyramid height; star margin; jump weight", "§5.3, §10.4"],
    ["ε_K", "relative closure residual", "§2.2"],
    ["κ", "symmetric positive-definite diffusivity tensor", "§2.3"],
    ["u, u_K, u_σ", "solution, cell unknown, face unknown", "§2.3, §10.3"],
    ["∇_K", "cell gradient reconstruction", "§10.3"],
    ["‖u‖₁,D", "discrete H¹ graph norm", "§10.3"],
    ["F_K, T_K", "faces of K; fan triangulation of ∂K", "§2.2, §8.2"],
    ["M, V, E, F, T", "mesh complex and its vertex/edge/face/tet sets", "§2.1, §4.1"],
], [30 * mm, 96 * mm, 42 * mm],
   caption="Table F.1. Notation used throughout the report. The section "
           "column points to the definition."),

H1("Selected references and source material"),
P(
    "The references below are the works that the report relies on for the "
    "finite-volume theory and for the implementation context. The meshing "
    "details are documented in the source files and are not attributed to a "
    "single external paper."
),
REF = ParagraphStyle(
    "Ref", parent=S["body"], fontSize=8.7, leading=12.2, leftIndent=14,
    firstLineIndent=-14, spaceAfter=4.0, alignment=TA_LEFT,
)
story += [
    Paragraph(
        "[1] R. Eymard, T. Gallouët, R. Herbin. Finite volume methods. "
        "In P. G. Ciarlet and J.-L. Lions (eds.), <i>Handbook of Numerical "
        "Analysis</i>, Vol. VII, North-Holland, 2000, pp. 713–1020.", REF),
    Paragraph(
        "[2] J. Droniou, R. Eymard, T. Gallouët, C. Guichard, R. Herbin. "
        "<i>The Gradient Discretisation Method</i>. Mathématiques et "
        "Applications, Vol. 82, Springer, 2018.", REF),
    Paragraph(
        "[3] F. Brezzi, K. Lipnikov, V. Simon. A family of mimetic finite "
        "difference methods on polygonal and polyhedral meshes. "
        "<i>Mathematical Models and Methods in Applied Sciences</i> 15 "
        "(2005) 1533–1551.", REF),
    Paragraph(
        "[4] K. Lipnikov, G. Manzini, M. Shashkov. Mimetic finite "
        "difference method. <i>Journal of Computational Physics</i> 257 "
        "(2014) 1163–1227.", REF),
    Paragraph(
        "[5] L. Beirão da Veiga, F. Brezzi, A. Cangiani, G. Manzini, "
        "L. D. Marini, A. Russo. Basic principles of virtual element "
        "methods. <i>Mathematical Models and Methods in Applied Sciences</i> "
        "23 (2013) 199–214.", REF),
    Paragraph(
        "[6] OpenFOAM Foundation. <i>OpenFOAM v2512 source code</i>, "
        "especially <font name='Mono'>primitiveMeshTools.C</font> and the "
        "<font name='Mono'>polyDualMesh</font> utility. 2025.", REF),
    Paragraph(
        "[7] F. Juretić. <i>cfMesh User Guide</i>. Creative Fields, 2015; "
        "cfMesh source and documentation.", REF),
    Paragraph(
        "[8] C. Geuzaine, J.-F. Remacle. Gmsh: a three-dimensional finite "
        "element mesh generator with built-in pre- and post-processing "
        "facilities. <i>International Journal for Numerical Methods in "
        "Engineering</i> 79 (2009) 1309–1331.", REF),
    Paragraph(
        "[9] D. Vartziotis, J. Wipper. GETMe: a mesh quality improvement "
        "tool based on geometric element transformation methods. "
        "arXiv:1406.4333, 2014.", REF),
    Paragraph(
        "[10] F. Alauzet. Size gradation control of anisotropic meshes. "
        "<i>Finite Elements in Analysis and Design</i> 46 (2010) 181–202.",
        REF),
]

H1("Appendix G. fTetWild experimental path: measured status (Oct 2026)"),
P(
    "The fTetWild tet stage (issue #12, GUI opt-in, requires the optional "
    "pytetwild package) is the measured alternative to the default GMSH "
    "tet stage. This appendix records what was measured on the current "
    "tree in October 2026; the numbers below come from the session notes "
    "named at the end of each paragraph, not from estimates. The path "
    "remains experimental: it is faster and often better on suitable "
    "parts, and it is not a universal GMSH replacement."
),
H2("G.1 Surface fidelity and envelopes"),
P(
    "On a small STEP cylinder the GMSH surface stage produces 210 to 748 "
    "triangles with inside median edge 0.476 to 0.250 mm and all 3 physical "
    "patches preserved; the outside sizes to 0.245 mm by GMSH field "
    "propagation, which is declared as propagation, not as a local "
    "measurement (GOAL W10.2, 2026-10-23). The default surface envelope "
    "eps 0.3 mm completed the fine stage (2.37M tets, 3738 s, max_energy "
    "7.55, no stall); 0.03 mm was rejected on time grounds. Envelope, "
    "opt-iteration budget, stop energy, filtering and simplification are "
    "exposed (simplify OFF by default: measured faster and more faithful)."
),
H2("G.2 Speed and quality where it wins"),
P(
    "On a real 3 m pipe the fTetWild path measured 150 s against GMSH's "
    "649 s at matching resolution, with much better tet quality "
    "(USER_GUIDE, session measurement). On hard parts a handful of warped "
    "boundary faces can still trip the final checkMesh -- historically "
    "GMSH failed the same gate on the same part in half of its logged "
    "runs, so neither path dominates everywhere."
),
H2("G.3 Direct-tet boundary layers (W13 A/B on R1)"),
P(
    "The same engine builds prisms on the tet boundary without the dual "
    "(opt-in BL on tets). On the real R1 tet (380,011 tets, 1 layer, "
    "h1 = 0.0002 m, growth 1.2, 5 wall patches) the direct path measured "
    "100% coverage, h1 ratio 0.999, Mesh OK, 90.5 s, 448,864 cells; the "
    "same-point dual BL measured h1 ratio 0.474, 221.4 s and Failed 3 "
    "(11 pyramids, 85 non-orthogonal faces), at ~3.9x fewer cells "
    "(notes/w13_bl_on_tets_reasoning.md, 2026-10-23). Verdict recorded "
    "there: opt-in, not promoted -- production 5-layer and thin-feature "
    "evidence remain open."
),
H2("G.4 Dirty CAD and small valves (WALL-03, measured)"),
TBL([
    ["Case", "Path", "Result"],
    ["dirty_cad (10-triangle open cube)", "fTetWild tet + convert",
     "Mesh OK: 3,515 tets, 0 pyramids, skew 0.87 (snappy: FATAL, 0 cells)"],
    ["mini_valve (STEP)", "fTetWild tet + dual + split_boundary_defects",
     "Mesh OK x2 runs: NOmax 67.80/64.68, skew 3.92/3.91, 0 pyramids "
     "(GMSH tet + dual: Failed 1)"],
], [62 * mm, 62 * mm, 44 * mm],
   caption="Table G.1. fTetWild where GMSH cannot go, and where it beats "
   "the GMSH dual on the same geometry (notes/wall_03_dirty_reasoning.md, "
   "2026-10-23; WALL-03 closed on these runs)."),
P(
    "Caveats measured in the same lanes: fTetWild shows run-to-run "
    "variance on mini_valve (~1 in 5 runs yields a non-manifold boundary; "
    "the regression test encodes a manifold gate with bounded retries), "
    "and the same-tet direct BL was refused for lack of interior support "
    "on GMSH tets. Robust-mode GMSH PLC-fails on the mini_valve BRep; "
    "medium-detail GMSH hangs. None of this is hidden: each statement "
    "above names the lane note that measured it."
),
H1("Appendix H. Engineering status update (W10.2 to WALL-03)"),
P(
    "Appendix D records the status at the time of writing; this appendix "
    "adds the measured deltas since, without editing Appendix D. W10: one "
    "MeshConfig with Fast/Balanced/Fine presets and measured evidence. "
    "W10.1: BL fidelity readback (R1 h1 0.113x support cap), keep-best "
    "quality policy (0 pyramids / 1 skew / Failed 1 at 99.47% coverage), "
    "per-patch h1 and y+ reporting. W10.2: hybrid tet source with visible "
    "fallback (TetSource contract). W10.3: poly release gate with BL-added "
    "defect attribution. W11: multi-region patch fix at the detector "
    "(untagged 2D no longer shadows 3D regions), all-patch report with "
    "bl_selected, shared wall classifier. W13: one Polymesh CTA with an "
    "Advanced engine selector and lossless migration of every historical "
    "mesher key. WALL-03: dirty CAD and mini_valve closed with measured "
    "Mesh OK runs (see Appendix G). Still open, with numbers: venturi "
    "dual-only wall (NOmax 97.2, skew 18.3, 224 pyramids; WALL-01), R1 h1 "
    "cap 0.2x best direct-tet vs 0.9x bar (WALL-02), dual-5M RAM wall "
    "~45-57 GB projected vs 16 GB bar (WALL-04). The full per-lane "
    "evidence lives in notes/w*.md; the guardrails (no silent "
    "substitution, keep-best, checkMesh decides) are unchanged."
),

# === APPEND HERE ===

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

OUT = Path(__file__).resolve().parents[1] / "PolyFoamMesh_Technical_Report.pdf"
doc = ReportDoc(
    str(OUT),
    pagesize=A4,
    leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP, bottomMargin=BOTTOM,
    title="PolyFoamMesh: Polyhedral Mesh Generation, Geometric Invariants, "
          "and Quality Control for Cell-Centered Finite Volumes",
    author="Lorenzo Riti",
    subject="Technical report on the barycentric-dual mesher and its "
            "geometric invariants for cell-centered finite volumes",
    creator="Lorenzo Riti",
)
doc.multiBuild(story)
print(f"wrote {OUT}")
