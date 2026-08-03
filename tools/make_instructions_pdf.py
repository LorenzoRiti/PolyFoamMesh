# -*- coding: utf-8 -*-
"""Genera il PDF di istruzioni per l'amico (installer/output/)."""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable,
    ListItem, PageBreak,
)

OUT = Path(__file__).resolve().parent.parent / "installer" / "output" \
    / "CFMesh-AutoGUI-2.1.0-Istruzioni.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

ACCENT = colors.HexColor("#0f766e")       # teal-700
DARK = colors.HexColor("#1e293b")
LIGHT = colors.HexColor("#f1f5f9")
WARN = colors.HexColor("#b45309")

def S(name, **kw):
    base = dict(fontName="Helvetica", fontSize=10, leading=14,
                textColor=DARK, spaceAfter=4)
    base.update(kw)
    return ParagraphStyle(name, **base)

st_title = S("title", fontName="Helvetica-Bold", fontSize=22, leading=26,
             textColor=ACCENT, alignment=TA_CENTER, spaceAfter=2)
st_sub = S("sub", fontSize=11, leading=14, textColor=colors.grey,
           alignment=TA_CENTER, spaceAfter=10)
st_h1 = S("h1", fontName="Helvetica-Bold", fontSize=13, leading=16,
          textColor=ACCENT, spaceBefore=10, spaceAfter=5)
st_h2 = S("h2", fontName="Helvetica-Bold", fontSize=11, leading=14,
          textColor=DARK, spaceBefore=8, spaceAfter=3)
st_body = S("body")
st_note = S("note", textColor=WARN)
st_code = S("code", fontName="Courier", fontSize=9, leading=12,
            backColor=LIGHT, borderPadding=5)

doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=18 * mm, rightMargin=18 * mm,
    topMargin=16 * mm, bottomMargin=16 * mm,
    title="CFMesh-AutoGUI 2.1.0 — Istruzioni",
    author="CFMesh-AutoGUI",
)

def bullets(items):
    return ListFlowable(
        [ListItem(Paragraph(t, st_body), leftIndent=6) for t in items],
        bulletType="bullet", start="•", bulletColor=ACCENT,
        leftIndent=14, bulletFontSize=9,
    )

story = []
story.append(Paragraph("CFMesh-AutoGUI 2.1.0", st_title))
story.append(Paragraph("Istruzioni di installazione e prova — per l'amico", st_sub))
story.append(Spacer(1, 4))

story.append(Paragraph("1. Requisiti del PC", st_h1))
story.append(bullets([
    "Windows 10 o 11, <b>64 bit</b> (x64).",
    "~2 GB di RAM libera (consigliato 4+ GB).",
    "~2.5 GB di spazio su disco.",
    "<b>Non serve Python</b> e non serve installare nient'altro.",
]))

story.append(Paragraph("2. Installazione", st_h1))
story.append(bullets([
    "Doppio clic su <b>CFMesh-AutoGUI-2.1.0-Setup.exe</b>.",
    "Se Windows SmartScreen avvisa (“Windows ha protetto il PC”): "
    "clicca <b>“Ulteriori informazioni” → “Esegui comunque”</b>. "
    "L'exe non è firmato con un certificato a pagamento: è un falso "
    "positivo, il pacchetto è generato dalla macchina di sviluppo.",
    "Completa la procedura (in italiano). Alla fine l'app si avvia da sola.",
    "D'ora in poi: menu <b>Start → CFMesh-AutoGUI</b> (o icona sul desktop).",
]))

story.append(Paragraph("3. Cosa puoi provare (funziona senza WSL)", st_h1))
story.append(Paragraph(
    "Seleziona il pulsante mesher nel pannello <b>Mesh</b> (seconda scheda):",
    st_body))
tbl = Table(
    [["Percorso", "Pulsante", "Risultato"],
     ["CFD Poly (GMSH, no-WSL)",
      "2° pulsante",
      "Tet GMSH → dual → <b>100% poliedrica, stile STAR-CCM+</b>"],
     ["FEM Tetra (GMSH, no-WSL)",
      "3° pulsante",
      "Mesh tetraedrica per solver FEM"],
     ["Visualizzazione", "—", "Viewer 3D di geometria e mesh"]],
    colWidths=[52 * mm, 26 * mm, 74 * mm],
)
tbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("LEADING", (0, 0), (-1, -1), 12),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
story.append(Spacer(1, 4))
story.append(tbl)
story.append(Spacer(1, 4))

story.append(Paragraph("Prova veloce consigliata", st_h2))
story.append(bullets([
    "<b>File → Load Geometry...</b> → apri un file STL chiuso "
    "(watertight), es. <font face='Courier'>venturi.stl</font>.",
    "Mesh tab → seleziona <b>“CFD Poly (GMSH, no-WSL)”</b>.",
    "Case directory: un percorso <b>senza spazi</b> (es. "
    "<font face='Courier'>C:\\prova</font>).",
    "Premi <b>Run</b>: nel log vedrai GMSH tet → dual → "
    "mesh 100% poliedrica.",
]))

story.append(Paragraph("4. Cosa NON è incluso (percorsi WSL)", st_h1))
story.append(Paragraph(
    "<b>“Cartesian cfMesh”</b> e la <b>validazione checkMesh</b> richiedono "
    "<b>WSL2 + OpenFOAM</b>, che questo pacchetto <b>non</b> installa "
    "(versione demo). Se selezioni quei percorsi, l'app mostra un avviso "
    "chiaro e non procede.", st_body))

story.append(Paragraph("5. Disinstallazione", st_h1))
story.append(bullets([
    "<b>Start → CFMesh-AutoGUI (cartella) → Disinstalla</b>, oppure",
    "Impostazioni → App → CFMesh-AutoGUI → Disinstalla.",
    "Rimuove tutto (app, associazioni file, scorciatoie). I log restano in "
    "<font face='Courier'>%APPDATA%\\cfmesh-autogui\\logs</font> "
    "(puoi cancellare la cartella).",
]))

story.append(Paragraph("6. Limiti noti (onesti)", st_h1))
story.append(bullets([
    "STL <b>auto-intersecanti</b> (triangoli sovrapposti, fori non tagliati "
    "nel CAD) possono far fallire GMSH con “PLC Error: two segments "
    "intersect” — usa STL esportati puliti.",
    "Formati consigliati: <b>STL chiuso</b> (watertight) o <b>STEP</b>.",
    "I moduli opzionali (snappyHexMesh, MMG, cfMesh) possono non essere "
    "presenti nel pacchetto demo.",
]))

story.append(PageBreak())
story.append(Paragraph("Appendice — per chi sviluppa", st_h1))
story.append(Paragraph("File del pacchetto:", st_h2))
story.append(Paragraph(
    "<font face='Courier'>installer/output/CFMesh-AutoGUI-2.1.0-Setup.exe"
    "</font> — <i>da dare all'amico</i><br/>"
    "<font face='Courier'>dist/CFMesh-AutoGUI/</font> — bundle one-dir "
    "(1.3 GB)", st_body))
story.append(Spacer(1, 4))
story.append(Paragraph("Ricompilare da sorgente:", st_h2))
story.append(Paragraph(
    "<font face='Courier'>pyinstaller --noconfirm --clean "
    "CFMesh-AutoGUI.spec<br/>"
    "ISCC.exe installer\\inno_setup.iss</font>", st_code))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "Nota: in Git-Bash gli switch <font face='Courier'>/VERYSILENT</font> "
    "vengono manglati in percorsi MSYS — usare "
    "<font face='Courier'>MSYS2_ARG_CONV_EXCL='*'</font>.", st_body))

doc.build(story)
print("PDF scritto:", OUT, OUT.stat().st_size, "bytes")
