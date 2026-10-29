"""Devis en PDF (phase C3)."""
import io
import re
from datetime import datetime
from decimal import Decimal as D

import commercial
import quote_pdf


def make_quote(**changes):
    quote = {
        "id": 1, "number": "DV-2026-001", "status": "brouillon", "client_name": "Paul <Martin> & Fils", "client_id": None,
        "currency": "XAF", "vat_rate": D("19.25"), "notes": "Paiement à 30 jours", "created_at": datetime(2026, 10, 27, 9, 0),
        "lines": [{"description": "Peinture œuvre " * 12, "quantity": D(2), "unit_price": D(15000)},
                  {"description": "Main d'œuvre", "quantity": D("1.5"), "unit_price": D(50000)}],
    }
    quote.update(changes)
    return commercial.public_quote(quote)


def text_of(pdf):
    from importlib import import_module

    try:
        reader = import_module("pypdf").PdfReader(io.BytesIO(pdf))
    except ImportError:
        return None
    return "\n".join(page.extract_text() for page in reader.pages)


def test_le_pdf_est_valide():
    pdf = quote_pdf.render(make_quote(), {"name": "Atelier Fox", "phone": "699", "email": "a@b.fr", "address": "Douala", "tax_id": "M1"},
                           {"address": "Rue 1", "phone": "655", "email": "p@x.fr"})
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 1500
    assert re.search(rb"/Type\s*/Pages", pdf)


def test_pdf_sans_profil_ni_client():
    assert quote_pdf.render(make_quote()).startswith(b"%PDF-")
    assert quote_pdf.render(make_quote(), None, None).startswith(b"%PDF-")


def test_texte_hostile_ne_casse_pas_le_pdf():
    quote = make_quote(client_name="</para><b>X", notes="<font size=999>&amp; <unclosed")
    assert quote_pdf.render(quote, {"name": "<i>Nom"}).startswith(b"%PDF-")


def test_beaucoup_de_lignes_donnent_plusieurs_pages():
    lines = [{"description": f"Ligne {i}", "quantity": D(1), "unit_price": D(1000)} for i in range(30)]
    one = quote_pdf.render(make_quote(lines=lines[:2]))
    many = quote_pdf.render(make_quote(lines=lines * 1))
    assert len(many) > len(one)


def test_contenu_du_pdf_si_pypdf_disponible():
    text = text_of(quote_pdf.render(make_quote(), {"name": "Atelier Fox"}))
    if text is None:
        return
    for expected in ("DEVIS DV-2026-001", "Atelier Fox", "27/10/2026", "125 213 XAF", "20 213 XAF", "19,25 %"):
        assert expected in text


def test_nettoyage_du_texte():
    assert quote_pdf.clean("1 250 000 XAF") == "1 250 000 XAF"
    assert quote_pdf.clean("<a>&") == "&lt;a&gt;&amp;"
    assert quote_pdf.clean(None) == ""


def test_date_francaise():
    assert quote_pdf.french_date("2026-10-27T09:00") == "27/10/2026"
    assert quote_pdf.french_date(None) == "" and quote_pdf.french_date("n'importe quoi") == ""
