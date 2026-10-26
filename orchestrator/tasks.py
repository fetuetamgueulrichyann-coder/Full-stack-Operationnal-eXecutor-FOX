"""Tâches persistantes : saisie, affichage et outils de l'agent (phase B1)."""
import re
from datetime import datetime, timedelta

import db
from tools import DATE_HELP, READ, TZ, WRITE, Tool, parse_dt, register

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
    return datetime.now(TZ).date()


def end_of_day(day):
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=TZ)


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


def parse_quick(text, now_date=None):
    """« Appeler X | 25/10 | haute » -> (titre, priorité, échéance). Titre seul accepté."""
    parts = [p.strip() for p in str(text or "").split("|")]
    title = clean_title(parts[0])
    priority, due = "normale", None
    for part in parts[1:]:
        if not part:
            continue
        if part.lower() in PRIORITY_ALIASES:
            priority = PRIORITY_ALIASES[part.lower()]
        else:
            try:
                due = parse_due(part, now_date)
            except TaskInputError:
                raise TaskInputError(
                    f"Je ne comprends pas « {part} ». Format : titre | échéance | priorité."
                ) from None
    return title, priority, due


def public(task):
    """Représentation JSON d'une tâche (dates dans le fuseau de FOX)."""
    def iso(value):
        return value.astimezone(TZ).isoformat(timespec="minutes") if value else None

    return {
        "id": task["id"], "title": task["title"], "priority": task["priority"],
        "status": task["status"], "overdue": task["overdue"],
        "due_at": iso(task["due_at"]), "created_at": iso(task["created_at"]),
        "done_at": iso(task["done_at"]),
    }


def describe_due(task):
    if not task["due_at"]:
        return None
    local = task["due_at"].astimezone(TZ)
    day = f"{local:%d/%m/%Y}"
    return day if (local.hour, local.minute) == (23, 59) else f"{day} {local:%H:%M}"


# --- Outils de l'agent --------------------------------------------------------
def lister_taches(user_id, statut=None):
    statuses = db.OPEN_STATUSES if statut in (None, "", "ouvertes") else (clean_status(statut),)
    return [
        {**{k: v for k, v in public(t).items() if k in ("id", "title", "priority", "status", "overdue")},
         "echeance": describe_due(t)}
        for t in db.list_tasks(user_id, statuses, limit=30)
    ]


def creer_tache(user_id, titre, priorite="normale", echeance=None):
    due = parse_due(echeance) if echeance else None
    task = db.create_task(user_id, clean_title(titre), clean_priority(priorite), due)
    return {"id": task["id"]}


def modifier_tache(user_id, tache_id, titre=None, priorite=None, echeance=None, statut=None):
    changes = {}
    if titre is not None:
        changes["title"] = clean_title(titre)
    if priorite is not None:
        changes["priority"] = clean_priority(priorite)
    if echeance is not None:
        changes["due_at"] = None if str(echeance).strip().lower() in ("aucune", "aucun", "") else parse_due(echeance)
    if statut is not None:
        changes["status"] = clean_status(statut)
    if not changes:
        raise TaskInputError("aucune modification demandée")
    task = db.update_task(user_id, int(tache_id), **changes)
    if task is None:
        raise TaskInputError("tâche introuvable")
    return {"id": task["id"], "status": task["status"]}


def describe_creer(args, user_id):
    lines = [f"Créer la tâche « {clean_title(args['titre'])} »"]
    if args.get("echeance"):
        lines.append(f"Échéance : {describe_due({'due_at': parse_due(args['echeance'])})}")
    if args.get("priorite"):
        lines.append(f"Priorité : {clean_priority(args['priorite'])}")
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
        }, "required": ["tache_id"]},
        classification=WRITE, run=modifier_tache, describe=describe_modifier,
        done=done_modifier, needs_user=True,
    ))


register_task_tools()
