"""Clients et devis (phase C) : saisie, validation et calculs.

Tous les calculs d'argent sont faits ici avec Decimal, jamais par le modèle de langage.
"""
import os
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "XAF").strip().upper() or "XAF"
DEFAULT_VAT_RATE = Decimal(os.environ.get("DEFAULT_VAT_RATE", "19.25"))
DECIMALS = {"XAF": 0, "XOF": 0, "JPY": 0}  # les autres devises : 2 décimales
MAX_LINES = 30
NAME_MAX = 100
FIELD_MAX = 200
DESC_MAX = 150
NOTES_MAX = 500
AMOUNT_MAX = Decimal("1000000000000")
QTY_MAX = Decimal("1000000")
STATUSES = ("brouillon", "envoye", "accepte", "refuse")
STATUS_LABELS = {"brouillon": "brouillon", "envoye": "envoyé", "accepte": "accepté", "refuse": "refusé"}
STATUS_ALIASES = {
    "brouillon": "brouillon", "envoye": "envoye", "envoyé": "envoye", "envoyer": "envoye",
    "accepte": "accepte", "accepté": "accepte", "accepter": "accepte",
    "refuse": "refuse", "refusé": "refuse", "refuser": "refuse",
}
EMAIL = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]{2,}")
PHONE = re.compile(r"\+?[\d\s().-]{6,25}")


class CommercialInputError(ValueError):
    """Saisie invalide ; le message est destiné à l'utilisateur."""


def squash(value):
    return " ".join(str(value or "").split())


# --- Clients ---------------------------------------------------------------------------------
def clean_name(value):
    name = squash(value)
    if not name:
        raise CommercialInputError("Le nom du client est vide.")
    if len(name) > NAME_MAX:
        raise CommercialInputError(f"Le nom est trop long ({NAME_MAX} caractères maximum).")
    return name


def clean_optional(value, field, limit=FIELD_MAX):
    text = squash(value)
    if not text:
        return None
    if len(text) > limit:
        raise CommercialInputError(f"{field} est trop long ({limit} caractères maximum).")
    if field == "L'e-mail" and not EMAIL.fullmatch(text):
        raise CommercialInputError("Adresse e-mail non reconnue.")
    if field == "Le téléphone" and not PHONE.fullmatch(text):
        raise CommercialInputError("Numéro de téléphone non reconnu.")
    return text


FIELDS = (("phone", "Le téléphone"), ("email", "L'e-mail"), ("address", "L'adresse"), ("notes", "La note"))


def parse_client(text):
    """« Nom ; téléphone ; e-mail ; adresse » (tout sauf le nom est facultatif) -> dict."""
    parts = [p.strip() for p in str(text or "").split(";")]
    if len(parts) > 4:
        raise CommercialInputError("Format : Nom ; téléphone ; e-mail ; adresse")
    result = {"name": clean_name(parts[0])}
    for (key, label), value in zip(FIELDS, parts[1:]):
        result[key] = clean_optional(value, label)
    return result


def parse_client_changes(text):
    """« téléphone ; e-mail ; adresse » : vide = inchangé, « - » = effacer. -> {champ: valeur|None}."""
    parts = [p.strip() for p in str(text or "").split(";")]
    if len(parts) > 3:
        raise CommercialInputError("Format : téléphone ; e-mail ; adresse (vide = inchangé, - = effacer)")
    changes = {}
    for (key, label), value in zip(FIELDS, parts):
        if value == "-":
            changes[key] = None
        elif value:
            changes[key] = clean_optional(value, label)
    if not changes:
        raise CommercialInputError("Rien à modifier.")
    return changes


# --- Montants ----------------------------------------------------------------------------------
def decimals(currency):
    return DECIMALS.get(currency, 2)


def quantize(amount, currency):
    return Decimal(amount).quantize(Decimal(1).scaleb(-decimals(currency)), rounding=ROUND_HALF_UP)


def _number(text, label):
    cleaned = str(text).strip().replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        raise CommercialInputError(f"{label} : « {str(text).strip()} ».")
    try:
        return Decimal(cleaned)
    except InvalidOperation:  # pragma: no cover (déjà filtré par l'expression régulière)
        raise CommercialInputError(f"{label} : « {text} ».")


def parse_amount(text):
    value = _number(text, "Prix non reconnu")
    if value > AMOUNT_MAX:
        raise CommercialInputError("Prix trop élevé.")
    if value != value.quantize(Decimal("0.01")):
        raise CommercialInputError("Prix : 2 décimales au maximum.")
    return value


def parse_quantity(text):
    value = _number(text, "Quantité non reconnue")
    if value <= 0:
        raise CommercialInputError("La quantité doit être supérieure à zéro.")
    if value > QTY_MAX:
        raise CommercialInputError("Quantité trop élevée.")
    if value != value.quantize(Decimal("0.001")):
        raise CommercialInputError("Quantité : 3 décimales au maximum.")
    return value


def parse_vat(text):
    cleaned = str(text).strip().replace("%", "").replace(",", ".").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned) or Decimal(cleaned) > 100:
        raise CommercialInputError("TVA non reconnue (entre 0 et 100, 2 décimales au maximum).")
    return Decimal(cleaned)


def totals(lines, vat_rate, currency):
    """Totaux d'un devis. Chaque ligne est arrondie, puis la TVA est calculée sur le sous-total."""
    computed = []
    subtotal = Decimal(0)
    for line in lines:
        amount = quantize(Decimal(line["quantity"]) * Decimal(line["unit_price"]), currency)
        computed.append({**line, "total": amount})
        subtotal += amount
    vat = quantize(subtotal * Decimal(vat_rate) / 100, currency)
    return {"lines": computed, "subtotal": subtotal, "vat": vat, "total": subtotal + vat}


def format_number(value, currency):
    digits = decimals(currency)
    text = f"{Decimal(value):,.{digits}f}"
    integer, _, fraction = text.partition(".")
    return integer.replace(",", " ") + (f",{fraction}" if fraction else "")


def money(value, currency):
    return f"{format_number(value, currency)} {currency}"


def format_quantity(value):
    text = f"{Decimal(value):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def format_rate(value):
    return format_quantity(value)


# --- Saisie d'un devis ---------------------------------------------------------------------
def parse_line(raw):
    """« description | quantité | prix unitaire » -> dict."""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 3:
        raise CommercialInputError(f"Ligne non reconnue : « {raw.strip()} ». Format : description | quantité | prix")
    description = squash(parts[0])
    if not description:
        raise CommercialInputError("Une ligne n'a pas de description.")
    if len(description) > DESC_MAX:
        raise CommercialInputError(f"Description trop longue ({DESC_MAX} caractères maximum).")
    return {"description": description, "quantity": parse_quantity(parts[1]), "unit_price": parse_amount(parts[2])}


def parse_quote(text):
    """Texte multi-lignes -> {"client", "lines", "vat_rate", "notes"}.

    Première ligne : nom du client. Ensuite : « description | quantité | prix », et facultativement
    « TVA 18 » et « note : ... ». Les lignes vides sont ignorées.
    """
    rows = [r for r in str(text or "").replace("\r", "").split("\n") if r.strip()]
    if not rows:
        raise CommercialInputError("Devis vide.")
    client = clean_name(rows[0])
    lines, vat_rate, notes = [], None, None
    for row in rows[1:]:
        stripped = row.strip()
        lowered = stripped.lower()
        if lowered.startswith("tva") and "|" not in stripped:
            if vat_rate is not None:
                raise CommercialInputError("TVA indiquée deux fois.")
            vat_rate = parse_vat(stripped[3:].lstrip(" :"))
        elif re.match(r"notes?\s*:", lowered) and "|" not in stripped:
            notes = squash(stripped.split(":", 1)[1]) or None
            if notes and len(notes) > NOTES_MAX:
                raise CommercialInputError(f"Note trop longue ({NOTES_MAX} caractères maximum).")
        else:
            lines.append(parse_line(stripped))
    if not lines:
        raise CommercialInputError("Le devis n'a aucune ligne. Format : description | quantité | prix")
    if len(lines) > MAX_LINES:
        raise CommercialInputError(f"Trop de lignes ({MAX_LINES} maximum).")
    return {"client": client, "lines": lines, "vat_rate": vat_rate, "notes": notes}


def clean_status(value):
    status = STATUS_ALIASES.get(str(value or "").strip().lower())
    if status is None:
        raise CommercialInputError("Statut invalide : brouillon, envoyé, accepté ou refusé.")
    return status


# --- Présentation --------------------------------------------------------------------------------
def public_client(client):
    return {k: client[k] for k in ("id", "name", "phone", "email", "address", "notes")}


def public_quote(quote):
    """Devis prêt à afficher : montants en texte, totaux calculés à la volée depuis les lignes."""
    currency = quote["currency"]
    result = totals(quote["lines"], quote["vat_rate"], currency)
    return {
        "id": quote["id"], "number": quote["number"], "status": quote["status"],
        "status_label": STATUS_LABELS[quote["status"]], "client": quote["client_name"], "client_id": quote["client_id"], "currency": currency,
        "vat_rate": format_rate(quote["vat_rate"]), "notes": quote["notes"],
        "created_at": quote["created_at"].isoformat(timespec="minutes") if quote.get("created_at") else None,
        "lines": [
            {"description": ln["description"], "quantity": format_quantity(ln["quantity"]),
             "unit_price": money(ln["unit_price"], currency), "total": money(ln["total"], currency)}
            for ln in result["lines"]
        ],
        "subtotal": money(result["subtotal"], currency), "vat": money(result["vat"], currency),
        "total": money(result["total"], currency),
        "amounts": {"subtotal": str(result["subtotal"]), "vat": str(result["vat"]), "total": str(result["total"])},
    }


def public_quote_summary(quote):
    currency = quote["currency"]
    return {
        "id": quote["id"], "number": quote["number"], "status": quote["status"],
        "status_label": STATUS_LABELS[quote["status"]], "client": quote["client_name"],
        "total": money(totals(quote["lines"], quote["vat_rate"], currency)["total"], currency),
        "created_at": quote["created_at"].isoformat(timespec="minutes") if quote.get("created_at") else None,
    }


# --- Profil d'entreprise (en-tête des devis) -----------------------------------------------
BUSINESS_FIELDS = (("phone", "Le téléphone"), ("email", "L'e-mail"), ("address", "L'adresse"), ("tax_id", "Le numéro de contribuable"))


def parse_business(text):
    """« Nom ; téléphone ; e-mail ; adresse ; n° contribuable » (tout sauf le nom est facultatif)."""
    parts = [p.strip() for p in str(text or "").split(";")]
    if len(parts) > 5:
        raise CommercialInputError("Format : Nom ; téléphone ; e-mail ; adresse ; n° contribuable")
    result = {"name": clean_name(parts[0])}
    for (key, label), value in zip(BUSINESS_FIELDS, parts[1:]):
        result[key] = clean_optional(value, label)
    return result


def public_business(business):
    return {k: business.get(k) for k in ("name", "phone", "email", "address", "tax_id")}


def quote_mail(quote, client=None, business=None):
    """Brouillon d'e-mail pour envoyer un devis (modèle fixe, sans IA). Le PDF est à joindre à la main."""
    name = (business or {}).get("name")
    body = (
        f"Bonjour {quote['client']},\n\n"
        f"Veuillez trouver ci-joint notre devis n° {quote['number']} d'un montant de {quote['total']} TTC.\n\n"
        "Je reste à votre disposition pour toute question.\n\n"
        "Cordialement" + (f",\n{name}" if name else ",")
    )
    return {"to": (client or {}).get("email") or "", "subject": f"Devis {quote['number']}", "body": body}
