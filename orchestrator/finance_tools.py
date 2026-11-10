"""Outils IA des chantiers et de la comptabilité (phase F).

Ils agissent toujours sur les données de l'utilisateur qui parle (jamais fourni par le modèle). Tous les
montants et les totaux sont calculés ici et en base, jamais par le modèle de langage. Toute écriture
passe par la confirmation de l'utilisateur ; aucun outil ne supprime : un chantier s'archive, et une
écriture erronée se supprime depuis la page Compta.
"""
from datetime import datetime

import commercial
import db
import finance
import finance_store
from tools import READ, Tool, WRITE, register, tz


def _today():
    return datetime.now(tz()).date()


def _truthy(value, default=False):
    if value in (None, ""):
        return default
    return str(value).strip().lower() in ("true", "1", "oui")


# --- Lecture ------------------------------------------------------------------------------------
def lister_chantiers(user_id, statut=None, archives=False):
    status = finance.clean_status(statut) if commercial.squash(statut) else None
    items = finance_store.list_projects(user_id, status, _truthy(archives), limit=30)
    return [finance.public_project(p) for p in items]


def lister_ecritures(user_id, mois=None, type=None, chantier=None):  # noqa: A002 (nom d'argument vu par le modèle)
    start, end, month = finance.parse_month(mois, _today())
    kind = finance.clean_kind(type) if commercial.squash(type) else None
    project = finance_store.resolve_project(user_id, chantier) if commercial.squash(chantier) else None
    entries = finance_store.list_entries(user_id, start, end, kind, project["id"] if project else None, limit=30)
    return {"month": month, "entries": [finance.public_entry(e) for e in entries]}


def bilan(user_id, mois=None, chantier=None):
    start, end, month = finance.parse_month(mois, _today())
    project = finance_store.resolve_project(user_id, chantier) if commercial.squash(chantier) else None
    summary = finance_store.summary(user_id, start, end, project["id"] if project else None)
    out = finance.public_summary(summary, month)
    if project:
        out["project"] = finance.public_project(project)
    return out


# --- Création d'un chantier --------------------------------------------------------------------
def _project_fields(args, user_id):
    return finance_store.new_project_fields(
        user_id, args.get("nom"), args.get("client"), args.get("devis"), args.get("budget"),
        args.get("debut"), args.get("fin"), args.get("note"), _today(),
    )


def creer_chantier(user_id, nom, client=None, devis=None, budget=None, debut=None, fin=None, note=None):
    fields = _project_fields({"nom": nom, "client": client, "devis": devis, "budget": budget, "debut": debut,
                              "fin": fin, "note": note}, user_id)
    try:
        project = finance_store.create_project(user_id, **fields)
    except finance_store.DuplicateProject:
        raise commercial.CommercialInputError("Un chantier porte déjà ce nom.") from None
    except finance_store.ProjectLimit as exc:
        raise commercial.CommercialInputError(f"Limite de {exc.args[0]} chantiers atteinte.") from None
    return {"id": project["id"], "name": project["name"]}


def describe_chantier(args, user_id):
    fields = _project_fields(args, user_id)
    for existing in finance_store.find_projects(user_id, fields["name"]):
        if existing["name"].lower() == fields["name"].lower():
            raise commercial.CommercialInputError("Un chantier porte déjà ce nom.")
    lines = [f"Créer le chantier « {fields['name']} »"]
    if fields["client_name"]:
        lines.append(f"Client : {fields['client_name']}")
    if fields["quote_id"]:
        lines.append(f"Devis lié : {commercial.squash(args.get('devis'))}")
    if fields["budget"] is not None:
        lines.append(f"Budget : {commercial.money(fields['budget'], commercial.DEFAULT_CURRENCY)}")
    if fields["start_date"] or fields["end_date"]:
        lines.append(f"Dates : {fields['start_date'] or '?'} → {fields['end_date'] or '?'}")
    if fields["notes"]:
        lines.append(f"Note : {fields['notes']}")
    return "\n".join(lines)


# --- Modification d'un chantier ---------------------------------------------------------------
def _changes(args):
    changes = {}
    if commercial.squash(args.get("statut")):
        changes["status"] = finance.clean_status(args["statut"])
    if args.get("budget") not in (None, ""):
        changes["budget"] = finance.parse_budget(args["budget"])
    if commercial.squash(args.get("fin")):
        changes["end_date"] = finance.parse_date(args["fin"], _today())
    if args.get("archiver") not in (None, ""):
        changes["archived"] = _truthy(args["archiver"], True)
    if not changes:
        raise commercial.CommercialInputError("Rien à modifier : précise le statut, le budget, la date de fin ou l'archivage.")
    return changes


def modifier_chantier(user_id, chantier, statut=None, budget=None, fin=None, archiver=None):
    project = finance_store.resolve_project(user_id, chantier)
    changes = _changes({"statut": statut, "budget": budget, "fin": fin, "archiver": archiver})
    updated = finance_store.update_project(user_id, project["id"], changes)
    return {"name": updated["name"], "changes": sorted(changes)}


def describe_modification(args, user_id):
    project = finance_store.resolve_project(user_id, args.get("chantier"))
    changes = _changes(args)
    lines = [f"Modifier le chantier « {project['name']} »"]
    if "status" in changes:
        lines.append(f"Statut : {finance.STATUS_LABELS[changes['status']]}")
    if "budget" in changes:
        lines.append("Budget : " + ("retiré" if changes["budget"] is None else commercial.money(changes["budget"], commercial.DEFAULT_CURRENCY)))
    if "end_date" in changes:
        lines.append(f"Fin : {changes['end_date']}")
    if "archived" in changes:
        lines.append("Archiver le chantier (il reste conservé)" if changes["archived"] else "Désarchiver le chantier")
    return "\n".join(lines)


# --- Écritures ----------------------------------------------------------------------------------
def _entry_input(args, user_id):
    project = finance_store.resolve_project(user_id, args.get("chantier")) if commercial.squash(args.get("chantier")) else None
    return {
        "kind": finance.clean_kind(args.get("type")),
        "amount": finance.parse_entry_amount(args.get("montant")),
        "date": finance.parse_date(args.get("date"), _today()),
        "description": finance.clean_text(args.get("description"), "La description", finance.DESC_MAX, required=True),
        "category": finance.clean_text(args.get("categorie"), "La catégorie", finance.CATEGORY_MAX),
        "project": project,
    }


def enregistrer_ecriture(user_id, type, montant, description, categorie=None, chantier=None, date=None):  # noqa: A002
    data = _entry_input({"type": type, "montant": montant, "description": description, "categorie": categorie,
                         "chantier": chantier, "date": date}, user_id)
    try:
        entry = finance_store.add_entry(
            user_id, data["kind"], data["amount"], commercial.DEFAULT_CURRENCY, data["date"], data["description"],
            data["category"], data["project"]["id"] if data["project"] else None,
        )
    except finance_store.LedgerLimit as exc:
        raise commercial.CommercialInputError(f"Limite de {exc.args[0]} écritures atteinte.") from None
    pub = finance.public_entry(entry)
    return {"kind_label": pub["kind_label"], "amount_label": pub["amount_label"], "description": pub["description"],
            "project": pub["project"]}


def describe_ecriture(args, user_id):
    data = _entry_input(args, user_id)
    label = finance.KIND_LABELS[data["kind"]]
    lines = [f"Enregistrer une {label} de {commercial.money(data['amount'], commercial.DEFAULT_CURRENCY)}",
             f"Description : {data['description']}", f"Date : {data['date']}"]
    if data["category"]:
        lines.append(f"Catégorie : {data['category']}")
    if data["project"]:
        lines.append(f"Chantier : {data['project']['name']}")
    return "\n".join(lines)


def done_ecriture(result):
    where = f" (chantier {result['project']})" if result["project"] else ""
    return f"{result['kind_label'].capitalize()} de {result['amount_label']} enregistrée : {result['description']}{where}."


def register_finance_tools():
    register(Tool(
        name="lister_chantiers",
        description="Liste les chantiers de l'utilisateur avec leur budget, leurs recettes, leurs dépenses et leur marge.",
        parameters={"type": "OBJECT", "properties": {
            "statut": {"type": "STRING", "description": "en cours, suspendu ou terminé (facultatif)"},
            "archives": {"type": "BOOLEAN", "description": "true pour les chantiers archivés (exclus par défaut)"},
        }},
        classification=READ, run=lister_chantiers, needs_user=True,
    ))
    register(Tool(
        name="creer_chantier",
        description="Crée un chantier (projet) : nom, et si connus client, devis lié, budget, dates.",
        parameters={"type": "OBJECT", "properties": {
            "nom": {"type": "STRING", "description": "Nom du chantier"},
            "client": {"type": "STRING", "description": "Nom du client (facultatif)"},
            "devis": {"type": "STRING", "description": "Numéro du devis lié, par exemple DV-2026-003 (facultatif)"},
            "budget": {"type": "STRING", "description": "Budget de dépenses prévu (facultatif)"},
            "debut": {"type": "STRING", "description": "Date de début AAAA-MM-JJ (facultatif)"},
            "fin": {"type": "STRING", "description": "Date de fin prévue AAAA-MM-JJ (facultatif)"},
            "note": {"type": "STRING", "description": "Note (facultatif)"},
        }, "required": ["nom"]},
        classification=WRITE, run=creer_chantier, describe=describe_chantier,
        done=lambda result: f"Chantier « {result['name']} » créé.", needs_user=True,
    ))
    register(Tool(
        name="modifier_chantier",
        description=(
            "Change le statut (en cours, suspendu, terminé), le budget ou la date de fin d'un chantier, ou l'archive. "
            "Un chantier ne se supprime pas : on l'archive."
        ),
        parameters={"type": "OBJECT", "properties": {
            "chantier": {"type": "STRING", "description": "Nom ou numéro du chantier"},
            "statut": {"type": "STRING", "description": "en cours, suspendu ou terminé (facultatif)"},
            "budget": {"type": "STRING", "description": "Nouveau budget (facultatif)"},
            "fin": {"type": "STRING", "description": "Nouvelle date de fin AAAA-MM-JJ (facultatif)"},
            "archiver": {"type": "BOOLEAN", "description": "true pour archiver, false pour désarchiver (facultatif)"},
        }, "required": ["chantier"]},
        classification=WRITE, run=modifier_chantier, describe=describe_modification,
        done=lambda result: f"Chantier « {result['name']} » mis à jour.", needs_user=True,
    ))
    register(Tool(
        name="enregistrer_ecriture",
        description=(
            "Enregistre une recette (argent reçu : paiement, acompte) ou une dépense (argent dépensé : achat, main-d'œuvre) "
            "dans la comptabilité, éventuellement rattachée à un chantier. Ne calcule aucun total : FOX le fait. "
            "Si le montant ou le type n'est pas clair, demande à l'utilisateur au lieu de deviner."
        ),
        parameters={"type": "OBJECT", "properties": {
            "type": {"type": "STRING", "description": "recette ou dépense"},
            "montant": {"type": "STRING", "description": "Montant, par exemple 25000"},
            "description": {"type": "STRING", "description": "Ce que c'est, par exemple « Ciment 20 sacs »"},
            "categorie": {"type": "STRING", "description": "Catégorie courte : matériaux, main-d'œuvre, transport… (facultatif)"},
            "chantier": {"type": "STRING", "description": "Nom ou numéro du chantier concerné (facultatif)"},
            "date": {"type": "STRING", "description": "AAAA-MM-JJ, « aujourd'hui » ou « hier » (par défaut : aujourd'hui)"},
        }, "required": ["type", "montant", "description"]},
        classification=WRITE, run=enregistrer_ecriture, describe=describe_ecriture, done=done_ecriture, needs_user=True,
    ))
    register(Tool(
        name="lister_ecritures",
        description="Liste les dernières recettes et dépenses d'un mois (et éventuellement d'un chantier).",
        parameters={"type": "OBJECT", "properties": {
            "mois": {"type": "STRING", "description": "AAAA-MM (par défaut : le mois en cours)"},
            "type": {"type": "STRING", "description": "recette ou dépense (facultatif)"},
            "chantier": {"type": "STRING", "description": "Nom ou numéro du chantier (facultatif)"},
        }},
        classification=READ, run=lister_ecritures, needs_user=True,
    ))
    register(Tool(
        name="bilan",
        description="Bilan d'un mois : total des recettes, des dépenses, solde, et répartition par catégorie (éventuellement pour un seul chantier).",
        parameters={"type": "OBJECT", "properties": {
            "mois": {"type": "STRING", "description": "AAAA-MM (par défaut : le mois en cours)"},
            "chantier": {"type": "STRING", "description": "Nom ou numéro du chantier (facultatif)"},
        }},
        classification=READ, run=bilan, needs_user=True,
    ))


register_finance_tools()
