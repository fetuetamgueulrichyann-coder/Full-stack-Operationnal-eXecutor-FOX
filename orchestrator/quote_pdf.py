"""Devis en PDF (phase C3). Les montants viennent de commercial.public_quote : aucun calcul ici."""
import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GREY = colors.HexColor("#555555")
LIGHT = colors.HexColor("#EEEEEE")


def clean(text):
    """Texte sûr pour reportlab : espaces fines remplacées (absentes de la police standard), balises échappées."""
    return escape(str(text or "").replace(" ", " ").replace(" ", " "))


def french_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return ""


def render(quote, business=None, client=None):
    """Retourne les octets du PDF. `quote` : dict de commercial.public_quote ; `business`, `client` : facultatifs."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Devis {quote['number']}", author=(business or {}).get("name") or "FOX",
    )
    base = getSampleStyleSheet()["Normal"]
    normal = ParagraphStyle("n", parent=base, fontSize=10, leading=13)
    small = ParagraphStyle("s", parent=normal, fontSize=9, textColor=GREY)
    title = ParagraphStyle("t", parent=normal, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=2)
    bold = ParagraphStyle("b", parent=normal, fontName="Helvetica-Bold")
    right = ParagraphStyle("r", parent=normal, alignment=2)
    right_bold = ParagraphStyle("rb", parent=right, fontName="Helvetica-Bold", fontSize=11)

    story = []
    business = business or {}
    left = [Paragraph(clean(business.get("name") or ""), ParagraphStyle("bn", parent=bold, fontSize=13, leading=16))]
    for key, label in (("address", ""), ("phone", "Tél. "), ("email", ""), ("tax_id", "N° contribuable : ")):
        if business.get(key):
            left.append(Paragraph(label + clean(business[key]), small))
    head = Table(
        [[left, [Paragraph(f"DEVIS {clean(quote['number'])}", title),
                 Paragraph(f"Date : {french_date(quote.get('created_at'))}", right)]]],
        colWidths=[95 * mm, 75 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 10 * mm)]

    story.append(Paragraph("Client", small))
    story.append(Paragraph(clean(quote["client"]), bold))
    for key, label in (("address", ""), ("phone", "Tél. "), ("email", "")):
        if (client or {}).get(key):
            story.append(Paragraph(label + clean(client[key]), normal))
    story.append(Spacer(1, 8 * mm))

    rows = [[Paragraph("Description", bold), Paragraph("Qté", right_bold), Paragraph("Prix unitaire", right_bold), Paragraph("Total", right_bold)]]
    for line in quote["lines"]:
        rows.append([Paragraph(clean(line["description"]), normal), Paragraph(clean(line["quantity"]), right),
                     Paragraph(clean(line["unit_price"]), right), Paragraph(clean(line["total"]), right)])
    table = Table(rows, colWidths=[78 * mm, 20 * mm, 36 * mm, 36 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(1, 5 * mm)]

    totals = Table(
        [[Paragraph("Sous-total", right), Paragraph(clean(quote["subtotal"]), right)],
         [Paragraph(f"TVA {clean(quote['vat_rate'])} %", right), Paragraph(clean(quote["vat"]), right)],
         [Paragraph("Total", right_bold), Paragraph(clean(quote["total"]), right_bold)]],
        colWidths=[134 * mm, 36 * mm],
    )
    totals.setStyle(TableStyle([("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(totals)
    if quote.get("notes"):
        story += [Spacer(1, 8 * mm), Paragraph("Note", small), Paragraph(clean(quote["notes"]), normal)]
    doc.build(story)
    return buffer.getvalue()
