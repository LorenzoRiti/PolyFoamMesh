#!/usr/bin/env python3
"""Generate the PolyFoamMesh technical report PDF.

DEPRECATED - do not use for new work. This v1 generator (Italian draft)
is superseded by tools/gen_report_pdf_v2.py (English, append-only), which
owns the canonical output PolyFoamMesh_Technical_Report.pdf. Running this
script as-is would silently OVERWRITE the v2 PDF, so it refuses to run
unless POLYFOAMMESH_ALLOW_LEGACY_REPORT=1 is set in the environment (kept
only for archaeology: reproducing the historical v1 document byte-shape
is not guaranteed).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable
)
import os
from pathlib import Path

OUT = str(Path(__file__).resolve().parents[1] / "PolyFoamMesh_Technical_Report.pdf")

# Deprecation guard (W16): this v1 script writes to the same OUT path as
# tools/gen_report_pdf_v2.py, so an accidental run destroys the canonical
# PDF. Refuse unless explicitly allowed for archaeology. Nothing in the
# repo sets this variable (verified: no references to this module).
if os.environ.get("POLYFOAMMESH_ALLOW_LEGACY_REPORT", "") != "1":
    raise SystemExit(
        "gen_report_pdf.py (v1, Italian draft) is deprecated and refuses "
        "to run: it would overwrite the canonical "
        "PolyFoamMesh_Technical_Report.pdf owned by "
        "tools/gen_report_pdf_v2.py (English, append-only). Use v2. "
        "Archaeology only: set POLYFOAMMESH_ALLOW_LEGACY_REPORT=1."
    )

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'CustomTitle', parent=styles['Title'],
    fontSize=22, leading=28, spaceAfter=6,
    textColor=HexColor('#1a3a5c'), alignment=TA_CENTER,
    fontName='Helvetica-Bold'
)
subtitle_style = ParagraphStyle(
    'Subtitle', parent=styles['Normal'],
    fontSize=12, leading=16, spaceAfter=20,
    textColor=HexColor('#555555'), alignment=TA_CENTER,
    fontName='Helvetica'
)
h1_style = ParagraphStyle(
    'H1', parent=styles['Heading1'],
    fontSize=16, leading=20, spaceBefore=18, spaceAfter=8,
    textColor=HexColor('#1a3a5c'), fontName='Helvetica-Bold',
    borderWidth=0, borderPadding=0,
)
h2_style = ParagraphStyle(
    'H2', parent=styles['Heading2'],
    fontSize=13, leading=16, spaceBefore=12, spaceAfter=6,
    textColor=HexColor('#2a5a8c'), fontName='Helvetica-Bold'
)
h3_style = ParagraphStyle(
    'H3', parent=styles['Heading3'],
    fontSize=11, leading=14, spaceBefore=8, spaceAfter=4,
    textColor=HexColor('#3a6a9c'), fontName='Helvetica-Bold'
)
body_style = ParagraphStyle(
    'Body', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=6,
    alignment=TA_JUSTIFY, fontName='Helvetica'
)
bullet_style = ParagraphStyle(
    'Bullet', parent=body_style,
    leftIndent=16, bulletIndent=6, spaceAfter=3,
    fontName='Helvetica'
)
code_style = ParagraphStyle(
    'Code', parent=styles['Code'],
    fontSize=8.5, leading=11, spaceAfter=6,
    fontName='Courier', backColor=HexColor('#f5f5f5'),
    borderWidth=0, leftIndent=8, rightIndent=8,
    textColor=HexColor('#333333')
)
formula_style = ParagraphStyle(
    'Formula', parent=body_style,
    fontSize=10, leading=14, spaceAfter=6, spaceBefore=4,
    fontName='Courier', alignment=TA_CENTER,
    backColor=HexColor('#f0f4f8'), borderWidth=0,
    leftIndent=20, rightIndent=20,
    textColor=HexColor('#1a3a5c')
)
caption_style = ParagraphStyle(
    'Caption', parent=styles['Normal'],
    fontSize=9, leading=12, spaceAfter=8,
    textColor=HexColor('#666666'), alignment=TA_CENTER,
    fontName='Helvetica-Oblique'
)
note_style = ParagraphStyle(
    'Note', parent=body_style,
    fontSize=9, leading=12, spaceAfter=6,
    textColor=HexColor('#666666'), fontName='Helvetica-Oblique',
    leftIndent=12, borderWidth=0
)

def P(text, style=body_style):
    return Paragraph(text, style)

def B(text):
    return Paragraph(text, bullet_style, bulletText='\u2022')

def F(text):
    return Paragraph(text, formula_style)

def C(text):
    return Paragraph(text, code_style)

def make_table(data, col_widths=None):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#1a3a5c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, HexColor('#f8f9fa')]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return t

def hr():
    return HRFlowable(width="100%", thickness=0.5, color=HexColor('#cccccc'),
                      spaceBefore=6, spaceAfter=6)

def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(HexColor('#999999'))
    canvas.drawString(20*mm, 10*mm, "PolyFoamMesh - Technical Report")
    canvas.drawRightString(190*mm, 10*mm, f"Page {doc.page}")
    canvas.setStrokeColor(HexColor('#cccccc'))
    canvas.line(20*mm, 13*mm, 190*mm, 13*mm)
    canvas.restoreState()

doc = SimpleDocTemplate(
    OUT, pagesize=A4,
    leftMargin=20*mm, rightMargin=20*mm,
    topMargin=20*mm, bottomMargin=20*mm,
    title="PolyFoamMesh Technical Report",
    author="Lorenzo Riti"
)

story = []

# === TITLE ===
story.append(Spacer(1, 30*mm))
story.append(P("PolyFoamMesh", title_style))
story.append(P("Relazione Tecnico-Scientifica sul Core Numerico-Geometrico", subtitle_style))
story.append(Spacer(1, 8*mm))
story.append(hr())
story.append(Spacer(1, 4*mm))
story.append(P("Metodo ai Volumi Finiti (Cell-Centered FVM) - Analisi Numerica per Equazioni Ellittiche", subtitle_style))
story.append(Spacer(1, 20*mm))

# === TABLE OF CONTENTS ===
story.append(P("Indice", h1_style))
toc_items = [
    "1. Dualizzazione Baricentrica (Median Dual)",
    "2. Metriche Geometriche e Replica checkMesh",
    "3. Self-Healing e Qualita Loop",
    "4. Estrusione Boundary Layer",
    "5. Implicazioni FVM",
    "6. Sintesi: Punti di Forza e Limiti",
]
for item in toc_items:
    story.append(B(item))
story.append(PageBreak())

# === SECTION 1 ===
story.append(P("1. Dualizzazione Baricentrica (Median Dual)", h1_style))
story.append(hr())

story.append(P("1.1 Costruzione del Duale", h2_style))
story.append(P(
    "L'algoritmo fondamentale e la <b>duale baricentrica</b> (median dual), non Voronoi/circocentrica. "
    "La scelta e motivata dal fatto che il circumcentro di un tet puo cadere fuori dal dominio, "
    "mentre il baricentro (centroide) sta sempre dentro.", body_style))
story.append(Spacer(1, 2*mm))

story.append(C(
    "1 cella di output  per ogni VERTICE primale\n"
    "1 faccia interna   per ogni SPIGOLO primale\n"
    "facce di bordo     = sussudivisione esatta dei triangoli primali"
))
story.append(Spacer(1, 2*mm))

story.append(P("1.1.1 Frontiera esatta (non Voronoi)", h3_style))
story.append(P(
    "Il Voronoi dual richiede clipping contro la superficie CAD. Il duale baricentrico no: "
    "ogni triangolo (a,b,c) di bordo genera 3 quad planari:", body_style))
story.append(C(
    "[a, mid(ab), centroid(abc), mid(ca)] -> cella di a\n"
    "[b, mid(bc), centroid(abc), mid(ab)] -> cella di b\n"
    "[c, mid(ca), centroid(abc), mid(bc)] -> cella di c"
))
story.append(Spacer(1, 2*mm))
story.append(P(
    "Il bordo e identico all'input: nessun clipping CAD, nessuna query di tolleranza. "
    "Il volume totale e conservato a precisione di macchina (~1e-16). "
    "Riduzione celle ~6x (un tet mesh ha circa 6 tet per vertice).", body_style))

story.append(P("1.1.2 Orientamento geometrico", h3_style))
story.append(P(
    "La faccia dello spigolo (a,b) separa le celle di a e b, quindi la normale punta da a verso b. "
    "I quad di bordo ereditano il winding primale. Non e necessario alcun winding heuristic.", body_style))

story.append(P("1.1.3 Connettivita", h3_style))
story.append(B("<b>Spigolo interno:</b> anello chiuso di tet -> la faccia duale passa attraverso i centroidi dei tet."))
story.append(B("<b>Spigolo di bordo:</b> ventaglio aperto -> la faccia duale si chiude attraverso i centroidi dei due triangoli di bordo + il punto medio dello spigolo."))
story.append(B("<b>Invariante topologico:</b> la somma dei vettori area delle facce di ogni cella = 0 (identita telescopica). Test esatto di chiusura: |S_i| / max(|S_i|) < 1e-8."))
story.append(B("<b>Primal cleanup automatico:</b> se celle aperte (throat-class vertices), collapse del vertice nel vicino di spigolo piu corto, ricostruzione del primal, retry."))

story.append(P("1.2 Modalita Collapse (stile polyDualMesh)", h2_style))
story.append(P(
    "Con <font face='Courier'>collapse_smooth_edges=True</font>: UNA faccia di bordo per vertice primale "
    "(poligono n-gon che passa per i centroidi dei triangoli incidenti), con feature edge dove "
    "l'angolo diedro >= boundary_feature_angle (default 90 gradi).", body_style))
story.append(B("Riduce ~3x il conteggio delle facce di bordo"))
story.append(B("Permette l'estrusione BL a colonne poligonali (una pila per faccia)"))
story.append(B("Prezzo: la frontiera non e piu bit-perfect (offset superficiale O(h^2))"))
story.append(B("Misurato: -0.19% / -0.46% su casi reali; su sfera solo n celle: -12.2% a n=3, -1.3% a n=10"))
story.append(B("Tolleranza volume: 5% (default), vs 1e-6 in modalita esatta"))

story.append(PageBreak())

# === SECTION 2 ===
story.append(P("2. Metriche Geometriche e Replica checkMesh", h1_style))
story.append(hr())

story.append(P(
    "L'applicazione replica <b>esattamente</b> le metriche di OpenFOAM checkMesh in-process "
    "(<font face='Courier'>_detect_defects</font> in <font face='Courier'>tet_poly_dual.py</font>), "
    "senza round-trip WSL.", body_style))

story.append(P("2.1 Centroidi e Volumi Cellula EXACT", h2_style))
story.append(P(
    "<font face='Courier'>_cell_centroids</font> e byte-identico a "
    "<font face='Courier'>primitiveMeshTools::makeCellCentresAndVols</font>:", body_style))
story.append(F(
    "c_est = media dei centroidi delle facce<br/>"
    "V_pyramid = (1/3) * (S . (cf - c_est))<br/>"
    "cell_center = SUM(w_i * (0.75*cf + 0.25*c_est)) / SUM(w_i)"
))
story.append(Spacer(1, 2*mm))
story.append(P(
    "Formula dei volumi piramidali con il baricentro della facce come vertice della piramide. "
    "Il test <b>face pyramids</b> e: S . (cf - C_cell) > 0 per entrambi i lati (owner e neighbour). "
    "Se <= 0, la cella e <b>non-star-shaped</b> (il baricentro non vede la faccia).", body_style))

story.append(P("2.2 Non-ortogonalita", h2_style))
story.append(P("Per ogni faccia interna con owner O e neighbour N:", body_style))
story.append(F(
    "d = C_N - C_O<br/>"
    "S = Newell area vector<br/>"
    "cos(theta) = (d . S) / (|d||S|)<br/>"
    "non_ortho_deg = arccos(clip(cos)) in gradi"
))
story.append(Spacer(1, 2*mm))
story.append(P("Soglia: <b>70 gradi</b> (checkMesh-native). L'ottimizzatore usa <b>65 gradi</b> (piu stringente).", body_style))

story.append(P("2.3 Skewness", h2_style))
story.append(P("Formula OpenFOAM esatta:", body_style))
story.append(F(
    "c_perp = baricentro della faccia<br/>"
    "scale  = (S . (c_perp - C_O)) / (S . d)<br/>"
    "v_skew = (c_perp - C_O) - scale * d<br/>"
    "fd     = max(0.2*|d|, face_extent)     <-- MAX, non somma<br/>"
    "skew   = |v_skew| / max(fd, eps)"
))
story.append(Spacer(1, 2*mm))
story.append(P(
    "<b>Bug critico corretto</b>: il codice originale sommava i due normalizzatori; OpenFOAM prende il MAX. "
    "Soglia: <b>4.0</b>.", body_style))

story.append(P("2.4 Aspect Ratio (formula OpenFOAM esatta)", h2_style))
story.append(P("Due termi, si prende il MAX:", body_style))
story.append(F(
    "(a) Direzionale: max_axis( SUM|S_axis| ) / min_axis( SUM|S_axis| )<br/>"
    "(b) Compattezza:  (1/6) * Area_tot / V^(2/3)"
))
story.append(Spacer(1, 2*mm))
story.append(P("Soglia: <b>1000</b> (error threshold checkMesh).", body_style))

story.append(P("2.5 Soglie Canoniche", h2_style))
story.append(make_table([
    ["Metrica", "checkMesh", "Ottimizzatore"],
    ["Skewness", "4.0", "4.0"],
    ["Non-ortogonalita", "70 deg", "65 deg"],
    ["Aspect ratio", "1000", "1000"],
], col_widths=[60*mm, 50*mm, 50*mm]))
story.append(Spacer(1, 2*mm))
story.append(P("Fonte: quality_thresholds.py - single source of truth.", caption_style))

story.append(PageBreak())

# === SECTION 3 ===
story.append(P("3. Self-Healing e Qualita Loop", h1_style))
story.append(hr())

story.append(P("3.1 Smoother Laplacian con Keep-Best", h2_style))
story.append(F(
    "new_pos[v] = pos[v] + relax * (weighted_mean(neighbours) - pos[v])"
))
story.append(Spacer(1, 2*mm))
story.append(B("Solo vertici interni (quelli di bordo sono pinnati, la frontiera e esatta)"))
story.append(B("<b>Inverse-distance weighting</b> (vicini piu pesanti = preservano forma locale)"))
story.append(B("<b>Backtracking</b>: se un passo regredisce, dimezza (fino a 5 livelli)"))
story.append(B("<b>Keep-best</b>: accetta solo se il conteggio dei defecti non aumenta E nessun volume <= 0"))
story.append(B("<b>Oggettivo continuo</b> per rompere parita: SUM max(0, NO-65)^2 + lambda * SUM skew^2"))
story.append(B("<b>Guardie durate</b>: volume positivo, volume ratio max, planarieta massima, severity guard"))

story.append(P("3.1.1 Candidati aggiuntivi (opt-in)", h3_style))
story.append(B("<b>Compactness</b> (GETMe-inspired): riduce l'area superficiale totale cellulare, gradiente di q3 = V - C * A^1.5 (Vartziotis & Wipper)"))
story.append(B("<b>Axis-balance</b>: targeting direzionale del rapporto d'aspetto (terzo termine della formula OpenFOAM)"))
story.append(B("<b>Zonal aspect</b> (Freitag): L_inf sulle celle soglia, maschera i vertici fuori zona"))

story.append(P("3.2 Merge Celle Concave", h2_style))
story.append(P("Per celle che <b>falliscono</b> il test pyramid (non-star-shaped):", body_style))
story.append(B("1. Trova il vicino interno con <b>massima area di faccia condivisa</b>"))
story.append(B("2. <b>Virtual merge test</b>: la cella unisce passa il test pyramid? Se si, mantieni la coppia"))
story.append(B("3. <b>Union-find</b> per raggruppare coppie transitive"))
story.append(B("4. <b>Applica merge</b>: elimina le facce interne tra celle dello stesso gruppo, compatta i punti orfani"))
story.append(B("5. <b>Keep-best</b>: rifiuta se pyramid aumentano, volume rel_err > 1e-6, o skew count aumenta"))
story.append(Spacer(1, 2*mm))
story.append(P(
    "Misurato: <b>302/348 celle concave riparate</b> (86.8%) sul valvola di riferimento. "
    "Non e universale: puo peggiorare altre metriche. Il gate e sulla <b>severita massima</b> "
    "(max NO, max skew), non sui conteggi.", body_style))

story.append(P("3.3 Loop di Feature Splitting (misurato, rifiutato)", h2_style))
story.append(P(
    "Partizionare lo star dei tet attorno a un vertice su feature concava in gruppi per regione liscia, "
    "dual cell separate. <b>Misurato: 895 -> 1215 -> 1218 piramidi</b>. "
    "Fallisce perche trade una cella non convessa per piu celle che si invertono loro stesse. "
    "Lasciato nel codice, default OFF.", body_style))

story.append(P("3.4 Integrazione checkMesh", h2_style))
story.append(P(
    "L'applicazione legge i report <font face='Courier'>checkMesh</font> e attiva iterazioni: "
    "re-mesh con parametri diversi, rimodellazione, o fallback senza BL. "
    "Il gate in-process replica esattamente il verdict di checkMesh, quindi non c'e discrepanza.", body_style))

story.append(PageBreak())

# === SECTION 4 ===
story.append(P("4. Estrusione Boundary Layer", h1_style))
story.append(hr())

story.append(P("4.1 Algoritmo su mesh duale poliedrica", h2_style))
story.append(B("<b>1. Selezione facce:</b> tutte le facce di bordo (o subset da patch_names)"))
story.append(B("<b>2. Normali di estrusione</b> per vertice:"))
story.append(B("   - Default: somma normalizzata dei vettori area incidenti"))
story.append(B("   - most_visible: vettore che minimizza l'angolo massimo con le normali (1-centre-on-sfera, Alauzet)"))
story.append(B("   - Smoothing armonico del campo di normali (Laplacian, preservando unita)"))
story.append(B("   - Guided bilateral filter (Zhang 2015) per preservare ridge"))
story.append(B("<b>3. Altezze layer</b> per vertice:"))
story.append(F("H_total = h1 * (r^n - 1) / (r - 1)   <-- serie geometrica"))
story.append(B("   clamp a clamp_factor * distanza al vertice non-bordo piu vicino"))
story.append(B("<b>4. Gradient limiting</b>: il campo di altezza totale e gradient-limited lungo gli spigoli di parete (cfMesh relThicknessTol / snappy maxThicknessToMedialRatio)"))
story.append(B("<b>5. Costruzione prismi</b>: ogni quad di parete -> pila di prismi; la cella interna adiacente scambia la faccia di parete con la top-face del prisma piu profondo"))
story.append(B("<b>6. Risoluzione winding globale</b> (_solve_global_windings): BFS sul grafo di parita delle facce. Ogni cella deve avere la superficie chiusa orientata: x_f * x_g = -d_f * d_g. Deterministico O(F+E)."))

story.append(P("4.2 Consumo Core (Reflow)", h2_style))
story.append(P(
    "Estendi l'estrusione nel core: i vertici interni si spostano lungo la direzione di estrusione. "
    "La cella assorbe lo stack. Se e uno <b>sliver</b> (min_edge < 0.1 * target_depth) -> pin. "
    "Le facce sliver vengono <b>escluse</b> dall'estrusione (0 strati, nessun prisma). "
    "Anello 2 di esclusione per evitare prismi deboli. Cap geometrico: inlet/outlet non estrui.", body_style))

story.append(P("4.3 Prevenzione Inversione", h2_style))
story.append(B("<b>Budget per vertice</b>: h_max = clamp_factor * distanza al vertice non-bordo piu vicino"))
story.append(B("<b>Sliver core guard</b>: celle con min_edge < 0.1 * h_total sono pinnate"))
story.append(B("<b>Per-face exclusion</b>: facce con sliver base o vertici sliver -> escluse"))
story.append(B("<b>Rim ring</b>: escludi 1-2 anelli attorno alle esclusioni per evitare prismi twist"))
story.append(B("<b>Wall Dirichlet over-drive</b>: 1.3x per garantire che il core shell superi la pila"))

story.append(PageBreak())

# === SECTION 5 ===
story.append(P("5. Implicazioni FVM", h1_style))
story.append(hr())

story.append(P("5.1 Stencil della Matrice Discreta", h2_style))
story.append(P(
    "La topologia poliedrica influenza direttamente il <b>volume di influenza</b> della matrice:", body_style))
story.append(B("<b>Cella tet</b> -> 4 vicini (stencil compatto, diagonale dominante forte)"))
story.append(B("<b>Cella poliedrica</b> -> 10-20+ vicini (stencil piu largo, coefficienti non diagonali maggiori, pattern piu fitto)"))
story.append(Spacer(1, 2*mm))
story.append(P(
    "La <b>non-ortogonalita</b> (theta > 70 gradi) rende il coefficiente non diagonale <b>negativo</b> "
    "(a_P < SUM|a_N|) -> la matrice perde la dominanza diagonale -> possibili oscillazioni nel campo di pressione, "
    "specialmente per l'equazione di Poisson. La correzione della non-ortogonalita (loop di smoothing) "
    "mantiene il coefficiente non diagonale positivo -> convergenza garantita.", body_style))

story.append(P("5.2 Errore di Troncamento", h2_style))
story.append(P(
    "Lo <b>skewness</b> misura quanto la connessione owner-neighbour devia dalla normale della faccia. "
    "Skewness alta -> errore O(h^2) nel gradiente -> <b>errore di troncamento del secondo ordine dominante</b> "
    "nell'equazione di Poisson. Il gate a 4.0 corrisponde a un errore di gradiente del 400%.", body_style))
story.append(Spacer(1, 2*mm))
story.append(P(
    "L'<b>aspect ratio</b> alto (1000) e il limite per celle sliver: in celle allungate il gradiente e mal condizionato "
    "(rigidi diversi lungo direzioni diverse) -> convergenza lenta o stagnazione.", body_style))

story.append(P("5.3 Invarianti e Conservazione", h2_style))
story.append(P(
    "Il duale baricentrico conserva il volume a 1e-16 (identita divergenza: SUM S_i . cf_i / 3 = V). "
    "L'estrusione BL conserva il volume totale per costruzione (i prismi sono topologicamente esatti). "
    "Questo e il <b>requisito minimo</b> per la conservazione FVM: se il volume non e conservato, "
    "il bilancio di massa per cella e violato.", body_style))

story.append(PageBreak())

# === SECTION 6 ===
story.append(P("6. Sintesi: Punti di Forza e Limiti", h1_style))
story.append(hr())

story.append(P("Punti di forza", h2_style))
story.append(B("100% poliedrico per costruzione, nessun tet residuale"))
story.append(B("Frontiera esatta (bit-per-bit) in modalita standard"))
story.append(B("Replica esatta di checkMesh in-process (nessun round-trip)"))
story.append(B("Keep-best universale: nessuna iterazione puo peggiorare la mesh"))
story.append(B("BL conforming per costruzione (prismi su quad di parete)"))
story.append(B("Formula centroidi/volumi EXACT (byte-identico a OpenFOAM)"))
story.append(B("Risoluzione winding globale O(F+E) deterministica"))

story.append(Spacer(1, 4*mm))
story.append(P("Limiti", h2_style))
story.append(B("0.25-0.29% celle non-star-shaped su feature concave (limite geometrico del duale)"))
story.append(B("Skewness residua alta su mesh duale grezze -> serve smoothing aggressivo"))
story.append(B("Non-ortogonalita tipicamente >50 gradi -> convergenza FVM piu lenta che con mesh strutturata"))
story.append(B("BL su geometrie sliver-rich richiede esclusioni estensive (fino al 13.5% delle facce)"))
story.append(B("L'algoritmo di merge non e universale: richiede validazione caso-per-caso"))
story.append(B("Feature splitting misurato come peggiorante (895 -> 1215 piramidi)"))
story.append(B("Modalita collapse: frontiera non bit-perfect (offset O(h^2))"))

story.append(Spacer(1, 8*mm))
story.append(hr())
story.append(P(
    "Documento generato da PolyFoamMesh Technical Analysis. "
    "Riferimenti: tet_poly_dual.py, poly_smoother.py, poly_cell_merge.py, bl_poly.py, "
    "quality_thresholds.py, hex_poly_dual.py, poly_dual_converter.md", caption_style))

doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
print(f"PDF written to: {OUT}")
