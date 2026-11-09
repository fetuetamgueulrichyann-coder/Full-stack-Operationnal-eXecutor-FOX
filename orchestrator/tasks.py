"""Tâches persistantes : saisie, affichage et outils de l'agent (phase B1)."""
import re
from datetime import datetime, timedelta

import db
from tools import DATE_HELP, READ, TZ, Tool, WRITE, parse_dt, register, tz  # noqa: F401  (TZ : fuseau par défaut)

PRIORITIES = ("haute", "normale", "basse")
STATUSES = ("a_faire", "en_cours", "terminee", "annulee")
TITLE_MAX = 200
LABELS = {"a_faire": "à faire", "en_cours": "en cours", "terminee": "terminée", "annulee": "annulée"}
PRIORITY_ALIASES = {"haute": "haute", "urgent": "haute", "urgente": "haute", "normale": "normale", "basse": "basse"}


class TaskInputError(ValueError):
    """Saisie invalide ; le message est destiné à l'utilisateur."""


def clean_title(value):
    title = " ".join(str(value or "").split())
    if not title:
        raise TaskInputError("Le titre de la tâche est vide.")
    if len(title) > TITLE_MAX:
        raise TaskInputError(f"Le titre est trop long ({TITLE_MAX} caractères maximum).")
    return title


def clean_priority(value):
    priority = PRIORITY_ALIASES.get(str(value).strip().lower())
    if priority is None:
        raise TaskInputError("Priorité invalide : haute, normale ou basse.")
    return priority


def clean_status(value):
    status = str(value).strip().lower()
    if status not in STATUSES:
        raise TaskInputError("Statut invalide : " + ", ".join(STATUSES) + ".")
    return status


def today():
    return datetime.now(tz()).date()


def end_of_day(day):
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=tz())


TIME_SUFFIX = re.compile(r"^(?:(.*?)\s+)?(?:à\s+)?(\d{1,2})\s*[h:]\s*(\d{2})$")


def parse_due(value, now_date=None):
    """Échéance saisie par une personne, avec heure facultative.

    Jour : JJ/MM, JJ/MM/AAAA, AAAA-MM-JJ, « demain », « aujourd'hui ». Heure : « 11:25 » ou « 11h25 »,
    seule (aujourd'hui) ou après le jour (« demain 11:25 », « 25/10 à 9h30 »).
    """
    now_date = now_date or today()
    text = str(value).strip().lower()
    timed = TIME_SUFFIX.match(text)
    if timed and not re.fullmatch(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}.*", text):
        day_text, hour, minute = timed[1], int(timed[2]), int(timed[3])
        if hour > 23 or minute > 59:
            raise TaskInputError(f"Heure non reconnue : « {value} ».")
        day = parse_due(day_text, now_date) if day_text else end_of_day(now_date)
        return day.replace(hour=hour, minute=minute, second=0)
    if text in ("aujourd'hui", "aujourdhui"):
        return end_of_day(now_date)
    if text == "demain":
        return end_of_day(now_date + timedelta(days=1))
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", text)
    try:
        if match:
            day, month, year = int(match[1]), int(match[2]), match[3]
            if year:
                return end_of_day(datetime(int(year), month, day).date())
            candidate = datetime(now_date.year, month, day).date()
            if candidate < now_date:  # « 05/01 » saisi en décembre : l'année prochaine
                candidate = datetime(now_date.year + 1, month, day).date()
            return end_of_day(candidate)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return end_of_day(datetime.fromisoformat(text).date())
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}.*", text):
            return parse_dt(text.replace(" ", "T"))
    except ValueError:
        pass
    raise TaskInputError(f"Échéance non reconnue : « {value} ». Exemples : 25/10, 25/10/2026, demain.")


EVENT_LINK = re.compile(r"^(?:rdv|rendez[- ]vous|rv)\s*(?:n°|n|#)?\s*(\d{1,18})$", re.IGNORECASE)


def parse_quick(text, now_date=None):
    """« Appeler X | 25/10 | haute » -> (titre, priorité, échéance). Titre seul accepté."""
    return parse_quick_linked(text, now_date)[:3]


def parse_quick_linked(text, now_date=None):
    """« Préparer les slides | demain | haute | rdv 12 » -> (titre, priorité, échéance, n° de rendez-vous ou None).

    Les champs après le titre peuvent venir dans n'importe quel ordre.
    """
    parts = [p.strip() for p in str(text or "").split("|")]
    title = clean_title(parts[0])
    priority, due, event_id = "normale", None, None
    for part in parts[1:]:
        if not part:
            continue
        link = EVENT_LINK.match(part)
        if link:
            event_id = int(link[1])
        elif part.lower() in PRIORITY_ALIASES:
            priority = PRIORITY_ALIASES[part.lower()]
        else:
            try:
                due = parse_due(part, now_date)
            except TaskInputError:
                raise TaskInputError(
                    f"Je ne comprends pas « {part} ». Format : titre | échéance | priorité | rdv n° (facultatifs)."
                ) from None
    return title, priority, due, event_id


def public(task):
    """Représentation JSON d'une tâche (dates dans le fuseau de FOX)."""
    def iso(value):
        return value.astimezone(tz()).isoformat(timespec="minutes") if value else None

    return {
        "id": task["id"], "title": task["title"], "priority": task["priority"],
        "status": task["status"], "overdue": task["overdue"],
        "due_at": iso(task["due_at"]), "created_at": iso(task["created_at"]),
        "done_at": iso(task["done_at"]), "event_id": task.get("event_id"),
    }


def describe_due(task):
    if not task["due_at"]:
        return None
    local = task["due_at"].astimezone(tz())
    day = f"{local:%d/%m/%Y}"
    return day if (local.hour, local.minute) == (23, 59) else f"{day} {local:%H:%M}"


# --- Outils de l'agent --------------------------------------------------------
def lister_taches(user_id, statut=None):
    statuses = db.OPEN_STATUSES if statut in (None, "", "ouvertes") else (clean_status(statut),)
    items = db.list_tasks(user_id, statuses, limit=30)
    titles = db.event_titles(user_id, [t["event_id"] for t in items])
    result = []
    for t in items:
        entry = {**{k: v for k, v in public(t).items() if k in ("id", "title", "priority", "status", "overdue")},
                 "echeance": describe_due(t)}
        if t["event_id"] in titles:
            entry["rdv_id"], entry["rdv"] = t["event_id"], titles[t["event_id"]]
        result.append(entry)
    return result


def link_target(value):
    """Numéro de rendez-vous fourni par le modèle -> entier, ou None pour « pas de lien » (0, « aucun »)."""
    if value is None or str(value).strip().lower() in ("", "0", "aucun", "aucune", "none"):
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        raise TaskInputError("Numéro de rendez-vous invalide.") from None


QUOTE_REQUEST = re.compile(
    r"^\s*(?:(?:faire|fais|créer|créé|creer|crée|cree|établir|établis|etablir|préparer|prépare|preparer|rédiger|rédige|"
    r"rediger|générer|génère|generer)\s+)(?:(?:un|le|ce|mon|son)\s+)?devis\b",
    re.IGNORECASE,
)


def refuse_quote_as_task(title):
    """Une demande de devis n'est pas une tâche : le modèle doit utiliser creer_devis."""
    if QUOTE_REQUEST.match(str(title or "")):
        raise TaskInputError(
            "Un devis ne se crée pas comme une tâche : utilise l'outil creer_devis avec le client et les lignes "
            "« description | quantité | prix unitaire », ou demande à l'utilisateur ce qu'il faut facturer."
        )


def creer_tache(user_id, titre, priorite="normale", echeance=None, rdv_id=None):
    refuse_quote_as_task(titre)
    due = parse_due(echeance) if echeance else None
    try:
        task = db.create_task(user_id, clean_title(titre), clean_priority(priorite), due, event_id=link_target(rdv_id))
    except db.LinkedEventNotFound:
        raise TaskInputError("rendez-vous introuvable") from None
    return {"id": task["id"]}


def modifier_tache(user_id, tache_id, titre=None, priorite=None, echeance=None, statut=None, rdv_id=None):
    changes = {}
    if titre is not None:
        changes["title"] = clean_title(titre)
    if priorite is not None:
        changes["priority"] = clean_priority(priorite)
    if echeance is not None:
        changes["due_at"] = None if str(echeance).strip().lower() in ("aucune", "aucun", "") else parse_due(echeance)
    if statut is not None:
        changes["status"] = clean_status(statut)
    if rdv_id is not None:
        changes["event_id"] = link_target(rdv_id)
    if not changes:
        raise TaskInputError("aucune modification demandée")
    try:
        task = db.update_task(user_id, int(tache_id), **changes)
    except db.LinkedEventNotFound:
        raise TaskInputError("rendez-vous introuvable") from None
    if task is None:
        raise TaskInputError("tâche introuvable")
    return {"id": task["id"], "status": task["status"]}


def describe_creer(args, user_id):
    refuse_quote_as_task(args.get("titre"))
    lines = [f"Créer la tâche « {clean_title(args['titre'])} »"]
    if args.get("echeance"):
        lines.append(f"Échéance : {describe_due({'due_at': parse_due(args['echeance'])})}")
    if args.get("priorite"):
        lines.append(f"Priorité : {clean_priority(args['priorite'])}")
    target = link_target(args.get("rdv_id"))
    if target is not None:
        title = db.event_titles(user_id, [target]).get(target)
        if title is None:
            raise TaskInputError("rendez-vous introuvable")
        lines.append(f"Liée au rendez-vous « {title} »")
    return "\n".join(lines)


def describe_modifier(args, user_id):
    task = db.get_task(user_id, int(args["tache_id"]))
    if task is None:
        raise TaskInputError("tâche introuvable")
    verbs = {"terminee": "Terminer", "annulee": "Annuler"}
    status = clean_status(args["statut"]) if args.get("statut") else None
    lines = [f"{verbs.get(status, 'Modifier')} la tâche « {task['title']} »"]
    if status and status not in verbs:
        lines.append(f"Nouveau statut : {LABELS[status]}")
    if args.get("titre"):
        lines.append(f"Nouveau titre : « {clean_title(args['titre'])} »")
    if args.get("priorite"):
        lines.append(f"Nouvelle priorité : {clean_priority(args['priorite'])}")
    if args.get("echeance"):
        if str(args["echeance"]).strip().lower() in ("aucune", "aucun"):
            lines.append("Échéance supprimée")
        else:
            lines.append(f"Nouvelle échéance : {describe_due({'due_at': parse_due(args['echeance'])})}")
    if args.get("rdv_id") is not None:
        target = link_target(args["rdv_id"])
        title = db.event_titles(user_id, [target]).get(target) if target is not None else None
        if target is not None and title is None:
            raise TaskInputError("rendez-vous introuvable")
        lines.append(f"Liée au rendez-vous « {title} »" if target is not None else "Détachée de son rendez-vous")
    if len(lines) == 1 and status is None:
        raise TaskInputError("aucune modification demandée")
    return "\n".join(lines)


def done_modifier(result):
    return {"terminee": "Tâche terminée.", "annulee": "Tâche annulée."}.get(result.get("status"), "Tâche modifiée.")


def register_task_tools():
    register(Tool(
        name="lister_taches",
        description="Liste les tâches de l'utilisateur (par défaut les tâches ouvertes, en retard d'abord).",
        parameters={"type": "OBJECT", "properties": {
            "statut": {"type": "STRING", "description": "ouvertes (par défaut), a_faire, en_cours, terminee ou annulee"},
        }},
        classification=READ, run=lister_taches, needs_user=True,
    ))
    register(Tool(
        name="creer_tache",
        description="Crée une tâche à suivre jusqu'à sa résolution (à utiliser pour toute chose à faire à retenir).",
        parameters={"type": "OBJECT", "properties": {
            "titre": {"type": "STRING", "description": "Ce qu'il faut faire, court et précis"},
            "echeance": {"type": "STRING", "description": DATE_HELP + " (facultatif)"},
            "priorite": {"type": "STRING", "description": "haute, normale (par défaut) ou basse"},
            "rdv_id": {"type": "INTEGER", "description": "Numéro d'un rendez-vous (lister_rdv) auquel lier la tâche, par exemple « préparer la réunion » (facultatif)"},
        }, "required": ["titre"]},
        classification=WRITE, run=creer_tache, describe=describe_creer,
        done=lambda result: "Tâche créée.", needs_user=True,
    ))
    register(Tool(
        name="modifier_tache",
        description=(
            "Modifie une tâche, la termine (statut terminee) ou l'annule (statut annulee). "
            "Retrouver d'abord son numéro avec lister_taches."
        ),
        parameters={"type": "OBJECT", "properties": {
            "tache_id": {"type": "INTEGER", "description": "Numéro de la tâche, tel que retourné par lister_taches"},
            "titre": {"type": "STRING", "description": "Nouveau titre"},
            "echeance": {"type": "STRING", "description": "Nouvelle échéance, ou « aucune » pour la supprimer"},
            "priorite": {"type": "STRING", "description": "haute, normale ou basse"},
            "statut": {"type": "STRING", "description": "a_faire, en_cours, terminee ou annulee"},
            "rdv_id": {"type": "INTEGER", "description": "Numéro du rendez-vous à lier à la tâche, ou 0 pour la détacher"},
        }, "required": ["tache_id"]},
        classification=WRITE, run=modifier_tache, describe=describe_modifier,
        done=done_modifier, needs_user=True,
    ))


register_task_tools()
