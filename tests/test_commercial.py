"""Clients et devis (phase C) : saisie, arrondis, formats."""
from datetime import datetime
from decimal import Decimal

import pytest

import commercial as c

D = Decimal


# --- Clients ---------------------------------------------------------------------------------------
def test_client_complet():
    assert c.parse_client("Paul  Martin ; +237 699 12 34 56 ; paul@exemple.com ; Rue 12, Douala") == {
        "name": "Paul Martin", "phone": "+237 699 12 34 56", "email": "paul@exemple.com", "address": "Rue 12, Douala",
    }


def test_client_nom_seul():
    assert c.parse_client("Paul") == {"name": "Paul"}


def test_client_champs_vides():
    assert c.parse_client("Paul ; ; ; Douala") == {"name": "Paul", "phone": None, "email": None, "address": "Douala"}


@pytest.mark.parametrize("text, message", [
    ("", "vide"), ("  ; 699123456", "vide"), ("x" * 101, "trop long"),
    ("Paul ; abc", "téléphone"), ("Paul ; 12", "téléphone"), ("Paul ; ; pas-un-mail", "e-mail"),
    ("Paul ; ; a@b", "e-mail"), ("Paul ; ; ; " + "x" * 201, "trop long"), ("a;b;c;d;e", "Format"),
])
def test_client_invalide(text, message):
    with pytest.raises(c.CommercialInputError, match=message):
        c.parse_client(text)


def test_modification_de_client():
    assert c.parse_client_changes("699123456 ; - ; Yaoundé") == {"phone": "699123456", "email": None, "address": "Yaoundé"}
    assert c.parse_client_changes(" ; a@b.fr") == {"email": "a@b.fr"}


@pytest.mark.parametrize("text", ["", " ; ; ", "1;2;3;4"])
def test_modification_invalide(text):
    with pytest.raises(c.CommercialInputError):
        c.parse_client_changes(text)


# --- Montants ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("15000", D("15000")), ("15 000", D("15000")), ("15 000", D("15000")), ("12,5", D("12.5")),
    ("12.50", D("12.50")), ("0", D(0)),
])
def test_prix(text, expected):
    assert c.parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "-5", "1,234", "1e5", "10 000 000 000 000", "1,2,3", "12 €"])
def test_prix_invalide(text):
    with pytest.raises(c.CommercialInputError):
        c.parse_amount(text)


@pytest.mark.parametrize("text, expected", [("2", D(2)), ("1,5", D("1.5")), ("0.25", D("0.25")), ("1 000", D(1000))])
def test_quantite(text, expected):
    assert c.parse_quantity(text) == expected


@pytest.mark.parametrize("text", ["0", "0,000", "-1", "abc", "1,2345", "2000000"])
def test_quantite_invalide(text):
    with pytest.raises(c.CommercialInputError):
        c.parse_quantity(text)


@pytest.mark.parametrize("text, expected", [("18", D(18)), ("19,25", D("19.25")), ("0", D(0)), ("20 %", D(20)), ("100", D(100))])
def test_tva(text, expected):
    assert c.parse_vat(text) == expected


@pytest.mark.parametrize("text", ["", "101", "-1", "abc", "1,234"])
def test_tva_invalide(text):
    with pytest.raises(c.CommercialInputError):
        c.parse_vat(text)


def test_totaux_xaf_sans_decimales():
    result = c.totals([{"quantity": D("2"), "unit_price": D("15000")}, {"quantity": D("1.5"), "unit_price": D("50000")}],
                      D("19.25"), "XAF")
    assert [ln["total"] for ln in result["lines"]] == [D("30000"), D("75000")]
    assert (result["subtotal"], result["vat"], result["total"]) == (D("105000"), D("20212.5").quantize(D(1), "ROUND_HALF_UP"), D("125213"))


def test_arrondi_a_la_moitie_superieure_et_pas_a_l_pair():
    assert c.quantize(D("0.5"), "XAF") == D("1")
    assert c.quantize(D("1.5"), "XAF") == D("2")
    assert c.quantize(D("2.5"), "XAF") == D("3")
    assert c.quantize(D("2.675"), "EUR") == D("2.68")


def test_totaux_eur_deux_decimales():
    result = c.totals([{"quantity": D("3"), "unit_price": D("9.99")}], D("20"), "EUR")
    assert (result["subtotal"], result["vat"], result["total"]) == (D("29.97"), D("5.99"), D("35.96"))


def test_chaque_ligne_est_arrondie_avant_la_somme():
    result = c.totals([{"quantity": D("0.333"), "unit_price": D("1")}] * 3, D(0), "XAF")
    assert result["subtotal"] == D(0)  # 3 × arrondi(0,333) = 0, comme sur la facture imprimée


def test_pas_d_erreur_de_virgule_flottante():
    result = c.totals([{"quantity": D("1"), "unit_price": D("0.10")}] * 3, D(0), "EUR")
    assert result["total"] == D("0.30")


def test_devis_vide_a_zero():
    assert c.totals([], D("19.25"), "XAF")["total"] == D(0)


@pytest.mark.parametrize("value, currency, expected", [
    (D("1250000"), "XAF", "1 250 000 XAF"), (D("0"), "XAF", "0 XAF"), (D("999"), "XAF", "999 XAF"),
    (D("1234.5"), "EUR", "1 234,50 EUR"), (D("0.05"), "EUR", "0,05 EUR"),
])
def test_format_montant(value, currency, expected):
    assert c.money(value, currency) == expected


@pytest.mark.parametrize("value, expected", [(D("2"), "2"), (D("1.500"), "1,5"), (D("0.25"), "0,25"), (D("10"), "10")])
def test_format_quantite(value, expected):
    assert c.format_quantity(value) == expected


# --- Saisie d'un devis -----------------------------------------------------------------------------------
def test_devis_complet():
    parsed = c.parse_quote("Paul Martin\nPeinture salon | 2 | 15 000\n\nTVA 18\nNote : Paiement à 30 jours\nMain d'œuvre | 1,5 | 50000\r\n")
    assert parsed == {
        "client": "Paul Martin", "vat_rate": D(18), "notes": "Paiement à 30 jours",
        "lines": [
            {"description": "Peinture salon", "quantity": D(2), "unit_price": D(15000)},
            {"description": "Main d'œuvre", "quantity": D("1.5"), "unit_price": D(50000)},
        ],
    }


def test_devis_sans_tva_ni_note():
    parsed = c.parse_quote("Paul\nA | 1 | 100")
    assert parsed["vat_rate"] is None and parsed["notes"] is None


def test_une_description_qui_commence_par_tva_est_une_ligne():
    assert c.parse_quote("Paul\nTVA incluse | 1 | 100")["lines"][0]["description"] == "TVA incluse"


@pytest.mark.parametrize("text, message", [
    ("", "vide"), ("   \n  ", "vide"), ("Paul", "aucune ligne"), ("Paul\nTVA 18", "aucune ligne"),
    ("Paul\nA | 1", "Ligne non reconnue"), ("Paul\nA | 1 | 2 | 3", "Ligne non reconnue"),
    ("Paul\n | 1 | 2", "description"), ("Paul\nA | 0 | 2", "supérieure à zéro"), ("Paul\nA | x | 2", "Quantité non reconnue"),
    ("Paul\nA | 1 | x", "Prix non reconnu"), ("Paul\nA | 1 | 2\nTVA 18\nTVA 19", "deux fois"),
    ("Paul\nA | 1 | 2\nTVA abc", "TVA non reconnue"), ("Paul\n" + "A | 1 | 2\n" * 31, "Trop de lignes"),
    ("Paul\nA | 1 | 2\nnote : " + "x" * 501, "Note trop longue"), ("Paul\n" + "x" * 151 + " | 1 | 2", "Description trop longue"),
])
def test_devis_invalide(text, message):
    with pytest.raises(c.CommercialInputError, match=message):
        c.parse_quote(text)


def test_trente_lignes_acceptees():
    assert len(c.parse_quote("Paul\n" + "A | 1 | 2\n" * 30)["lines"]) == 30


@pytest.mark.parametrize("word, status", [("envoyé", "envoye"), ("Accepté", "accepte"), ("refuse", "refuse"), ("brouillon", "brouillon")])
def test_statuts(word, status):
    assert c.clean_status(word) == status


@pytest.mark.parametrize("word", ["", None, "perdu"])
def test_statut_invalide(word):
    with pytest.raises(c.CommercialInputError):
        c.clean_status(word)


def test_devis_public():
    quote = {
        "id": 1, "number": "DV-2026-001", "status": "brouillon", "client_name": "Paul", "client_id": None,
        "currency": "XAF", "vat_rate": D("19.25"), "notes": None, "created_at": datetime(2026, 10, 27, 9, 0),
        "lines": [{"description": "Peinture", "quantity": D("2.000"), "unit_price": D("15000.00")}],
    }
    public = c.public_quote(quote)
    assert public["lines"] == [{"description": "Peinture", "quantity": "2", "unit_price": "15 000 XAF", "total": "30 000 XAF"}]
    assert (public["subtotal"], public["vat"], public["total"], public["vat_rate"]) == (
        "30 000 XAF", "5 775 XAF", "35 775 XAF", "19,25")
    assert public["amounts"] == {"subtotal": "30000", "vat": "5775", "total": "35775"}
    assert c.public_quote_summary(quote)["total"] == "35 775 XAF"


# --- Profil d'entreprise et e-mail du devis (C3) --------------------------------------------
def test_profil_d_entreprise():
    assert c.parse_business("Atelier Fox ; 699123456 ; a@b.fr ; Douala ; M0123") == {
        "name": "Atelier Fox", "phone": "699123456", "email": "a@b.fr", "address": "Douala", "tax_id": "M0123"}
    assert c.parse_business("Atelier Fox") == {"name": "Atelier Fox"}


@pytest.mark.parametrize("text, message", [("", "vide"), ("A ; 1", "téléphone"), ("A ; ; zz", "e-mail"),
                                           ("A;;;;" + "x" * 201, "trop long"), ("a;b;c;d;e;f", "Format")])
def test_profil_invalide(text, message):
    with pytest.raises(c.CommercialInputError, match=message):
        c.parse_business(text)


QUOTE_PUBLIC = {"client": "Paul", "number": "DV-2026-001", "total": "35\u202f775 XAF"}


def test_mail_du_devis():
    mail = c.quote_mail(QUOTE_PUBLIC, {"email": "p@x.fr"}, {"name": "Atelier Fox"})
    assert mail["to"] == "p@x.fr" and mail["subject"] == "Devis DV-2026-001"
    assert mail["body"].startswith("Bonjour Paul,") and "35\u202f775 XAF TTC" in mail["body"] and mail["body"].endswith("Cordialement,\nAtelier Fox")


def test_mail_du_devis_sans_client_ni_entreprise():
    mail = c.quote_mail(QUOTE_PUBLIC)
    assert mail["to"] == "" and mail["body"].endswith("Cordialement,")
    assert c.quote_mail(QUOTE_PUBLIC, {"email": None})["to"] == ""
