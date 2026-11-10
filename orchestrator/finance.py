"""Chantiers et comptabilité simple (phase F) : saisie, validation et présentation.

Tous les calculs d'argent sont faits en base ou ici avec Decimal, jamais par le modèle de langage.
Les montants affichés utilisent les mêmes formats que les devis (commercial.money).
"""
import os
import re
from datetime import date, timedelta
from decimal import Decimal

import commercial
from commercial import CommercialInputError, squash

MAX_PROJECTS = int(os.environ.get("MAX_PROJECTS", "100"))
MAX_LEDGER_ENTRIES = int(os.environ.get("MAX_LEDGER_ENTRIES", "20000"))
NAME_MAX = 100
DESC_MAX = 150
CATEGORY_MAX = 40
NOTES_MAX = 500

STATUSES = ("en_cours", "suspendu", "termine")
STATUS_LABELS = {"en_cours": "en cours", "suspendu": "suspendu", "termine": "terminé"}
STATUS_ALIASES = {
    "en_cours": "en_cours", "en cours": "en_cours", "encours": "en_cours", "actif": "en_cours", "demarre": "en_cours", "démarré": "en_cours",
    "suspendu": "suspendu", "pause": "suspendu", "en pause": "suspendu", "suspendre": "suspendu",
    "termine": "termine", "terminé": "termine", "fini": "termine", "finie": "termine", "terminer": "termine", "livre": "termine", "livré": "termine",
}
KINDS = ("recette", "depense")
KIND_LABELS = {"recette": "recette", "depense": "dépense"}
KIND_ALIASES = {
    "recette": "recette", "recettes": "recette", "revenu": "recette", "entree": "recette", "entrée": "recette",
    "encaissement": "recette", "paiement": "recette", "acompte": "recette", "vente": "recette",
    "depense": "depense", "dépense": "depense", "depenses": "depense", "dépenses": "depense", "achat": "depense",
    "sortie": "depense", "charge": "depense", "frais": "depense",
}


def clean_status(value):
    status = STATUS_ALIASES.get(squash(value).lower())
    if status is None:
        raise CommercialInputError("Statut inconnu : en cours, suspendu ou terminé.")
    return status


def clean_kind(value):
    kind = KIND_ALIASES.get(squash(value).lower())
    if kind is None:
        raise CommercialInputError("Type inconnu : recette (argent reçu) ou dépense (argent dépensé).")
    return kind


def clean_project_name(value):
    name = squash(value)
    if not name:
        raise CommercialInputError("Le nom du chantier est vide.")
    if len(name) > NAME_MAX:
        raise CommercialInputError(f"Le nom du chantier est trop long ({NAME_MAX} caractères maximum).")
    return name


def clean_text(value, label, limit, required=False):
    text = squash(value)
    if not text:
        if required:
            raise CommercialInputError(f"{label} est vide.")
        return None
    if len(text) > limit:
        raise CommercialInputError(f"{label} est trop long ({limit} caractères maximum).")
    return text


def parse_date(value, today):
    """AAAA-MM-JJ, « aujourd'hui » ou « hier » ; une date vide vaut aujourd'hui. Reste dans une plage plausible."""
    text = squash(value).lower().replace("’", "'")
    if text in ("", "aujourd'hui", "aujourdhui", "today"):
        return today
    if text == "hier":
        return today - timedelta(days=1)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise CommercialInputError("Date non reconnue : utilise AAAA-MM-JJ (par exemple 2026-11-09).")
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise CommercialInputError("Cette date n'existe pas.") from None
    if abs((parsed - today).days) > 366 * 5:
        raise CommercialInputError("Date trop éloignée.")
    return parsed


def parse_optional_date(value, today):
    return parse_date(value, today) if squash(value) else None


def parse_month(value, today):
    """AAAA-MM (vide = mois courant) -> (premier jour, premier jour du mois suivant, « AAAA-MM »)."""
    text = squash(value)
    if not text:
        first = today.replace(day=1)
    else:
        if not re.fullmatch(r"\d{4}-\d{2}", text):
            raise CommercialInputError("Mois non reconnu : utilise AAAA-MM (par exemple 2026-11).")
        try:
            first = date.fromisoformat(text + "-01")
        except ValueError:
            raise CommercialInputError("Ce mois n'existe pas.") from None
    nxt = date(first.year + (first.month // 12), first.month % 12 + 1, 1)
    return first, nxt, f"{first:%Y-%m}"


def parse_budget(value):
    if squash(value) == "":
        return None
    amount = commercial.parse_amount(value)
    if amount < 0:
        raise CommercialInputError("Le budget ne peut pas être négatif.")
    return amount


def parse_entry_amount(value):
    amount = commercial.parse_amount(value)
    if amount <= 0:
        raise CommercialInputError("Le montant doit être supérieur à zéro.")
    return amount


def _money(value, currency):
    return commercial.money(Decimal(value), currency)


def _iso(value):
    return value.isoformat() if value else None


def public_project(project, currency=None):
    currency = currency or commercial.DEFAULT_CURRENCY
    income, expense = project["income"], project["expense"]
    budget = project["budget"]
    out = {
        "id": project["id"],
        "name": project["name"],
        "status": project["status"],
        "status_label": STATUS_LABELS[project["status"]],
        "client_id": project["client_id"],
        "client": project["client_name"],
        "quote": project["quote_number"],
        "start_date": _iso(project["start_date"]),
        "end_date": _iso(project["end_date"]),
        "notes": project["notes"],
        "archived": project["archived_at"] is not None,
        "currency": currency,
        "income": str(income), "income_label": _money(income, currency),
        "expense": str(expense), "expense_label": _money(expense, currency),
        "margin": str(income - expense), "margin_label": _money(income - expense, currency),
        "budget": None if budget is None else str(budget),
        "budget_label": None if budget is None else _money(budget, currency),
        "budget_left": None, "budget_left_label": None, "budget_used_percent": None,
    }
    if budget is not None:
        left = budget - expense
        out["budget_left"] = str(left)
        out["budget_left_label"] = _money(left, currency)
        out["budget_used_percent"] = int((expense * 100 / budget).to_integral_value()) if budget > 0 else None
    return out


def public_entry(entry):
    currency = entry["currency"]
    return {
        "id": entry["id"],
        "kind": entry["kind"],
        "kind_label": KIND_LABELS[entry["kind"]],
        "amount": str(entry["amount"]),
        "amount_label": _money(entry["amount"], currency),
        "currency": currency,
        "date": entry["entry_date"].isoformat(),
        "category": entry["category"],
        "description": entry["description"],
        "project_id": entry["project_id"],
        "project": entry["project_name"],
    }


def public_summary(summary, month, currency=None):
    currency = currency or commercial.DEFAULT_CURRENCY
    income, expense = summary["income"], summary["expense"]
    return {
        "month": month,
        "currency": currency,
        "income": str(income), "income_label": _money(income, currency),
        "expense": str(expense), "expense_label": _money(expense, currency),
        "balance": str(income - expense), "balance_label": _money(income - expense, currency),
        "entries": summary["count"],
        "by_category": [
            {"kind": c["kind"], "category": c["category"] or "sans catégorie", "total": str(c["total"]),
             "total_label": _money(c["total"], currency)}
            for c in summary["by_category"]
        ],
    }
