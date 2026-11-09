"""Outils IA des clients et des devis (phase D4).

Ils agissent toujours sur les données de l'utilisateur qui parle (jamais fourni par le modèle) et sont
ouverts au propriétaire comme aux testeurs. Tous les montants sont calculés ici avec Decimal, jamais par
le modèle de langage : il fournit les lignes (description, quantité, prix) et FOX calcule les totaux.
Toute écriture passe par la confirmation de l'utilisateur.
"""
import commercial
import db
from tools import READ, Tool, WRITE, register

LINES_HELP = "Une ligne par article, au format « description | quantité | prix unitaire » (par exemple « Étagère | 4 | 32000 »)"


def _status(value):
    word = commercial.squash(value).lower()
    status = commercial.STATUS_ALIASES.get(word)
    if status is None:
        raise commercial.CommercialInputError("Statut inconnu : brouillon, envoyé, accepté ou refusé.")
    return status


def _quote_input(args):
    """Texte du devis (format de parse_quote) construit à partir des arguments de l'outil."""
    rows = [commercial.squash(args.get("client"))]
    rows += [r for r in str(args.get("lignes") or "").replace("\r", "").split("\n") if r.strip()]
    if args.get("tva") not in (None, ""):
        rows.append(f"TVA {args['tva']}")
    if args.get("note"):
        rows.append("note : " + commercial.squash(args["note"]))
    return commercial.parse_quote("\n".join(rows))


def _resolve_client(user_id, name):
    """Client enregistré qui porte ce nom (ou None). Plusieurs correspondances : l'utilisateur doit préciser."""
    matches = db.find_clients(user_id, name)
    if len(matches) > 1:
        raise commercial.CommercialInputError(
            "Plusieurs clients correspondent : " + ", ".join(c["name"] for c in matches) + ". Précise le nom complet."
        )
    return matches[0] if matches else None


# --- Lecture ------------------------------------------------------------------------------
def lister_clients(user_id, recherche=None):
    items = db.list_clients(user_id, commercial.squash(recherche) or None, limit=30)
    return [commercial.public_client(c) for c in items]


def lister_devis(user_id, statut=None, archives=False):
    status = _status(statut) if commercial.squash(statut) else None
    only_archived = str(archives).strip().lower() in ("true", "1", "oui")
    return [commercial.public_quote_summary(q) for q in db.list_quotes(user_id, status, limit=20, archived=only_archived)]


# --- Création d'un client -------------------------------------------------------------------
def _client_fields(args):
    fields = {"name": commercial.clean_name(args.get("nom"))}
    for key, field, label in (("telephone", "phone", "Le téléphone"), ("email", "email", "L'e-mail"),
                              ("adresse", "address", "L'adresse")):
        fields[field] = commercial.clean_optional(args.get(key), label)
    return fields


def creer_client(user_id, nom, telephone=None, email=None, adresse=None):
    fields = _client_fields({"nom": nom, "telephone": telephone, "email": email, "adresse": adresse})
    try:
        client = db.create_client(user_id, **fields)
    except db.DuplicateClient:
        raise commercial.CommercialInputError("Un client porte déjà ce nom.") from None
    except db.ClientLimit as exc:
        raise commercial.CommercialInputError(f"Limite de {exc.args[0]} clients atteinte.") from None
    return {"id": client["id"], "name": client["name"]}


def describe_client(args, user_id):
    fields = _client_fields(args)
    if db.find_clients(user_id, fields["name"]):
        existing = [c["name"] for c in db.find_clients(user_id, fields["name"])]
        if any(n.lower() == fields["name"].lower() for n in existing):
            raise commercial.CommercialInputError("Un client porte déjà ce nom.")
    lines = [f"Créer le client « {fields['name']} »"]
    for field, label in (("phone", "Téléphone"), ("email", "E-mail"), ("address", "Adresse")):
        if fields[field]:
            lines.append(f"{label} : {fields[field]}")
    return "\n".join(lines)


# --- Création d'un devis --------------------------------------------------------------------
def creer_devis(user_id, client, lignes, tva=None, note=None):
    parsed = _quote_input({"client": client, "lignes": lignes, "tva": tva, "note": note})
    found = _resolve_client(user_id, parsed["client"])
    vat_rate = commercial.DEFAULT_VAT_RATE if parsed["vat_rate"] is None else parsed["vat_rate"]
    try:
        quote = db.create_quote(
            user_id, found["id"] if found else None, found["name"] if found else parsed["client"],
            parsed["lines"], vat_rate, commercial.DEFAULT_CURRENCY, parsed["notes"],
        )
    except db.QuoteLimit as exc:
        raise commercial.CommercialInputError(f"Limite de {exc.args[0]} devis atteinte.") from None
    pub = commercial.public_quote(quote)
    return {"id": quote["id"], "number": quote["number"], "total": pub["total"], "registered": found is not None}


def describe_devis(args, user_id):
    parsed = _quote_input(args)
    found = _resolve_client(user_id, parsed["client"])
    vat_rate = commercial.DEFAULT_VAT_RATE if parsed["vat_rate"] is None else parsed["vat_rate"]
    currency = commercial.DEFAULT_CURRENCY
    result = commercial.totals(parsed["lines"], vat_rate, currency)
    lines = [f"Créer un devis pour « {found['name'] if found else parsed['client']} »"]
    if not found:
        lines.append("(client non enregistré : le devis portera ce nom)")
    for ln in result["lines"]:
        lines.append(
            f"- {ln['description']} : {commercial.format_quantity(ln['quantity'])} × "
            f"{commercial.money(ln['unit_price'], currency)} = {commercial.money(ln['total'], currency)}"
        )
    lines.append(f"Sous-total : {commercial.money(result['subtotal'], currency)}")
    lines.append(f"TVA {commercial.format_rate(vat_rate)} % : {commercial.money(result['vat'], currency)}")
    lines.append(f"Total : {commercial.money(result['total'], currency)}")
    if parsed["notes"]:
        lines.append(f"Note : {parsed['notes']}")
    return "\n".join(lines)


def done_devis(result):
    return f"Devis {result['number']} créé : {result['total']} TTC. Tu peux le télécharger en PDF dans la page Clients et devis."


# --- Changement de statut -------------------------------------------------------------------
def changer_statut_devis(user_id, devis, statut):
    quote = db.set_quote_status(user_id, devis, _status(statut))
    if quote is None:
        raise commercial.CommercialInputError("Devis introuvable.")
    return {"number": quote["number"], "status": quote["status"]}


def describe_statut(args, user_id):
    quote = db.get_quote(user_id, args.get("devis"))
    if quote is None:
        raise commercial.CommercialInputError("Devis introuvable.")
    status = _status(args.get("statut"))
    return f"Passer le devis {quote['number']} ({quote['client_name']}) en « {commercial.STATUS_LABELS[status]} »"


def done_statut(result):
    return f"Devis {result['number']} : {commercial.STATUS_LABELS[result['status']]}."


# --- Archivage ------------------------------------------------------------------------------
def archiver_devis(user_id, devis, archiver=True):
    want = str(archiver).strip().lower() not in ("false", "0", "non")
    quote = db.set_quote_archived(user_id, devis, want)
    if quote is None:
        raise commercial.CommercialInputError("Devis introuvable.")
    return {"number": quote["number"], "archived": want}


def describe_archivage(args, user_id):
    quote = db.get_quote(user_id, args.get("devis"))
    if quote is None:
        raise commercial.CommercialInputError("Devis introuvable.")
    want = str(args.get("archiver", True)).strip().lower() not in ("false", "0", "non")
    verb = "Archiver" if want else "Désarchiver"
    return f"{verb} le devis {quote['number']} ({quote['client_name']}) : il reste conservé, avec son statut."


def done_archivage(result):
    return f"Devis {result['number']} {'archivé' if result['archived'] else 'désarchivé'}."


def register_commercial_tools():
    register(Tool(
        name="lister_clients",
        description="Liste les clients enregistrés de l'utilisateur, avec un filtre facultatif sur le nom.",
        parameters={"type": "OBJECT", "properties": {
            "recherche": {"type": "STRING", "description": "Partie du nom à chercher (facultatif)"},
        }},
        classification=READ, run=lister_clients, needs_user=True,
    ))
    register(Tool(
        name="creer_client",
        description="Enregistre un nouveau client (nom, et si connus : téléphone, e-mail, adresse).",
        parameters={"type": "OBJECT", "properties": {
            "nom": {"type": "STRING", "description": "Nom du client ou de l'entreprise"},
            "telephone": {"type": "STRING", "description": "Numéro de téléphone (facultatif)"},
            "email": {"type": "STRING", "description": "Adresse e-mail (facultatif)"},
            "adresse": {"type": "STRING", "description": "Adresse ou ville (facultatif)"},
        }, "required": ["nom"]},
        classification=WRITE, run=creer_client, describe=describe_client,
        done=lambda result: f"Client « {result['name']} » créé.", needs_user=True,
    ))
    register(Tool(
        name="lister_devis",
        description="Liste les devis récents de l'utilisateur (numéro, client, statut, total TTC).",
        parameters={"type": "OBJECT", "properties": {
            "statut": {"type": "STRING", "description": "brouillon, envoyé, accepté ou refusé (facultatif)"},
            "archives": {"type": "BOOLEAN", "description": "true pour lister les devis archivés (par défaut, ils sont exclus)"},
        }},
        classification=READ, run=lister_devis, needs_user=True,
    ))
    register(Tool(
        name="creer_devis",
        description=(
            "Crée un devis pour un client à partir de ses lignes. N'additionne rien toi-même : FOX calcule les "
            "totaux et la TVA. Utilise cet outil, et non une tâche, dès qu'on demande un devis."
        ),
        parameters={"type": "OBJECT", "properties": {
            "client": {"type": "STRING", "description": "Nom du client (un client enregistré est reconnu automatiquement)"},
            "lignes": {"type": "STRING", "description": LINES_HELP},
            "tva": {"type": "STRING", "description": "Taux de TVA en %, seulement si l'utilisateur en précise un (facultatif)"},
            "note": {"type": "STRING", "description": "Note à faire figurer sur le devis (facultatif)"},
        }, "required": ["client", "lignes"]},
        classification=WRITE, run=creer_devis, describe=describe_devis, done=done_devis, needs_user=True,
    ))
    register(Tool(
        name="changer_statut_devis",
        description="Change le statut d'un devis (brouillon, envoyé, accepté, refusé). Retrouver d'abord son numéro avec lister_devis.",
        parameters={"type": "OBJECT", "properties": {
            "devis": {"type": "STRING", "description": "Numéro du devis, par exemple DV-2026-003"},
            "statut": {"type": "STRING", "description": "brouillon, envoyé, accepté ou refusé"},
        }, "required": ["devis", "statut"]},
        classification=WRITE, run=changer_statut_devis, describe=describe_statut, done=done_statut, needs_user=True,
    ))
    register(Tool(
        name="archiver_devis",
        description=(
            "Archive un devis terminé (il est conservé, avec son statut, mais n'apparaît plus dans la liste courante) "
            "ou le désarchive. Un devis ne se supprime pas : on l'archive."
        ),
        parameters={"type": "OBJECT", "properties": {
            "devis": {"type": "STRING", "description": "Numéro du devis, par exemple DV-2026-003"},
            "archiver": {"type": "BOOLEAN", "description": "true pour archiver (par défaut), false pour désarchiver"},
        }, "required": ["devis"]},
        classification=WRITE, run=archiver_devis, describe=describe_archivage, done=done_archivage, needs_user=True,
    ))


register_commercial_tools()
