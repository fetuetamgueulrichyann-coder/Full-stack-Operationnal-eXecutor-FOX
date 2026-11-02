"""Outils IA de l'agenda interne (phase B5) : lister, créer, modifier et supprimer des rendez-vous.

Ils agissent toujours sur l'agenda de l'utilisateur qui parle (jamais fourni par le modèle) et sont
ouverts au propriétaire comme aux testeurs. Les noms (`*_rdv`) diffèrent des outils Google Calendar
(`*_evenement`), réservés au propriétaire.
"""
from datetime import datetime, timedelta

import agenda
import db
from tools import DATE_HELP, IRREVERSIBLE, READ, Tool, WRITE, register, tz, tz_name

NO_LOCATION = ("aucun", "aucune", "")


def human(event):
    start = event["start_at"].astimezone(tz())
    end = event["end_at"].astimezone(tz())
    return f"{start:%d/%m/%Y %H:%M}-{end:%H:%M}"


def lister_rdv(user_id, periode=None):
    now = datetime.now(tz())
    start, days = agenda.parse_range(periode, now)
    events = db.list_events(user_id, start, start + timedelta(days=days))
    return [agenda.public(e) for e in events[:30]]


def creer_rdv(user_id, titre, debut, duree=None, lieu=None, repetition=None):
    start = agenda.parse_start(debut)
    rule, until = agenda.parse_repeat(repetition, start) if str(repetition or "").strip() else (None, None)
    event = db.create_event(
        user_id, agenda.clean_title(titre), start, start + agenda.parse_duration(duree), agenda.clean_location(lieu),
        recurrence=rule, until=until, tz_name=tz_name(),
    )
    conflicts = db.find_conflicts(user_id, event["start_at"], event["end_at"], exclude_id=event["id"])
    db.audit(user_id, "event_created", None, {"event_id": event["id"], "source": "ia", "repeat": rule})
    return {"id": event["id"], "user_id": user_id, "conflits": [c["title"] for c in conflicts]}


def changes_from(args):
    """Arguments de l'outil modifier_rdv -> demandes de modification (voir agenda.build_changes)."""
    names = {"titre": "title", "debut": "start", "duree": "duration", "repetition": "repeat", "jusqu_au": "until"}
    data = {names[k]: args[k] for k in names if args.get(k) is not None}
    if args.get("lieu") is not None:
        data["location"] = "" if str(args["lieu"]).strip().lower() in NO_LOCATION else args["lieu"]
    return data


def modifier_rdv(user_id, rdv_id, **args):
    current = db.get_event(user_id, int(rdv_id))
    if current is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    changes = agenda.build_changes(current, changes_from(args))
    event = db.update_event(user_id, int(rdv_id), tz_name=tz_name(), **changes)
    if event is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    conflicts = db.find_conflicts(user_id, event["start_at"], event["end_at"], exclude_id=event["id"])
    db.audit(user_id, "event_updated", None, {"event_id": event["id"], "fields": sorted(changes), "source": "ia"})
    return {"id": event["id"], "conflits": [c["title"] for c in conflicts]}


def occurrence_day(value):
    return agenda.parse_day(str(value).strip().lower(), datetime.now(tz()).date())


def supprimer_rdv(user_id, rdv_id, occurrence=None):
    if occurrence:
        if not db.skip_occurrence(user_id, int(rdv_id), occurrence_day(occurrence)):
            raise agenda.AgendaInputError("occurrence introuvable (ce rendez-vous est-il une série, à cette date ?)")
        db.audit(user_id, "event_updated", None, {"event_id": int(rdv_id), "skipped": str(occurrence), "source": "ia"})
        return {"id": int(rdv_id), "occurrence": str(occurrence)}
    if not db.delete_event(user_id, int(rdv_id)):
        raise agenda.AgendaInputError("rendez-vous introuvable")
    db.audit(user_id, "event_deleted", None, {"event_id": int(rdv_id), "source": "ia"})
    return {"id": int(rdv_id)}


def describe_creer(args, user_id):
    start = agenda.parse_start(args["debut"])
    end = start + agenda.parse_duration(args.get("duree"))
    lines = [f"Ajouter le rendez-vous « {agenda.clean_title(args['titre'])} »", f"Le {start:%d/%m/%Y %H:%M}-{end:%H:%M}"]
    place = agenda.clean_location(args.get("lieu"))
    if place:
        lines.append(f"Lieu : {place}")
    if str(args.get("repetition") or "").strip():
        rule, until = agenda.parse_repeat(args["repetition"], start)
        lines.append(f"Répétition : {agenda.repeat_label(rule, until)}")
    conflicts = db.find_conflicts(user_id, start, end)
    if conflicts:
        lines.append("Attention, chevauche : " + ", ".join(sorted({c["title"] for c in conflicts})))
    return "\n".join(lines)


def describe_modifier(args, user_id):
    event = db.get_event(user_id, int(args["rdv_id"]))
    if event is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    changes = agenda.build_changes(event, changes_from(args))  # valide la demande dès la proposition
    suffix = f" ({agenda.repeat_label(event['recurrence'], event['recurrence_until'])})" if event["recurrence"] else ""
    lines = [f"Modifier le rendez-vous « {event['title']} » ({human(event)}){suffix}"]
    if "title" in changes:
        lines.append(f"Nouveau titre : « {changes['title']} »")
    if "start_at" in changes:
        what = "Nouveau premier rendez-vous" if event["recurrence"] else "Nouveau début"
        lines.append(f"{what} : {changes['start_at'].astimezone(tz()):%d/%m/%Y %H:%M}")
        lines.append(f"Durée : {(changes['end_at'] - changes['start_at']).total_seconds() / 3600:g} h")
    if "location" in changes:
        lines.append(f"Nouveau lieu : {changes['location']}" if changes["location"] else "Lieu supprimé")
    if "recurrence" in changes:
        label = agenda.repeat_label(changes["recurrence"], changes.get("until"))
        lines.append(f"Répétition : {label}" if label else "Répétition arrêtée")
    elif "until" in changes:
        lines.append(f"Fin de la répétition : {changes['until']:%d/%m/%Y}" if changes["until"] else "Répétition sans fin")
    return "\n".join(lines)


def describe_supprimer(args, user_id):
    event = db.get_event(user_id, int(args["rdv_id"]))
    if event is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    if args.get("occurrence"):
        if not event["recurrence"]:
            raise agenda.AgendaInputError("ce rendez-vous n'est pas une série")
        return f"Supprimer uniquement l'occurrence du {occurrence_day(args['occurrence']):%d/%m/%Y} de « {event['title']} »"
    if event["recurrence"]:
        return f"Supprimer TOUTE la série « {event['title']} » ({agenda.repeat_label(event['recurrence'], event['recurrence_until'])})"
    return f"Supprimer le rendez-vous « {event['title']} » ({human(event)})"


def undo_creer(args, result):
    db.delete_event(result["user_id"], result["id"])


def done_creer(result):
    message = "Rendez-vous ajouté."
    if result.get("conflits"):
        message += " Attention, il chevauche : " + ", ".join(result["conflits"]) + "."
    return message


def done_modifier(result):
    message = "Rendez-vous modifié."
    if result.get("conflits"):
        message += " Attention, il chevauche : " + ", ".join(result["conflits"]) + "."
    return message


RDV_ID = {"type": "INTEGER", "description": "Numéro du rendez-vous, tel que retourné par lister_rdv"}
REPEAT_HELP = "chaque jour, jours ouvrés, chaque semaine ou chaque mois, éventuellement suivi de « jusqu'au JJ/MM/AAAA »"
INTERNAL = "dans l'agenda interne de FOX (avec rappel Telegram avant l'heure)"


def register_agenda_tools():
    register(Tool(
        name="lister_rdv",
        description=f"Liste les rendez-vous {INTERNAL}.",
        parameters={"type": "OBJECT", "properties": {
            "periode": {"type": "STRING", "description": "aujourd'hui, demain, semaine (par défaut : 7 jours) ou mois"},
        }},
        classification=READ, run=lister_rdv, needs_user=True,
    ))
    register(Tool(
        name="creer_rdv",
        description=f"Ajoute un rendez-vous {INTERNAL}. Le moment doit être dans le futur.",
        parameters={"type": "OBJECT", "properties": {
            "titre": {"type": "STRING", "description": "Titre court du rendez-vous"},
            "debut": {"type": "STRING", "description": DATE_HELP.replace("Date ou date-heure", "Date et heure")},
            "duree": {"type": "STRING", "description": "Durée, par exemple 1h30 ou 45 min (facultatif, 1 h par défaut)"},
            "lieu": {"type": "STRING", "description": "Lieu (facultatif)"},
            "repetition": {"type": "STRING", "description": REPEAT_HELP + " (facultatif ; sans répétition, le rendez-vous est unique)"},
        }, "required": ["titre", "debut"]},
        classification=WRITE, run=creer_rdv, describe=describe_creer, done=done_creer, undo=undo_creer, needs_user=True,
    ))
    register(Tool(
        name="modifier_rdv",
        description=f"Modifie un rendez-vous {INTERNAL}. Retrouver d'abord son numéro avec lister_rdv.",
        parameters={"type": "OBJECT", "properties": {
            "rdv_id": RDV_ID,
            "titre": {"type": "STRING", "description": "Nouveau titre"},
            "debut": {"type": "STRING", "description": "Nouveau début (même format que creer_rdv)"},
            "duree": {"type": "STRING", "description": "Nouvelle durée, par exemple 1h30"},
            "lieu": {"type": "STRING", "description": "Nouveau lieu, ou « aucun » pour l'effacer"},
            "repetition": {"type": "STRING", "description": REPEAT_HELP + ", ou « aucune » pour arrêter la répétition"},
            "jusqu_au": {"type": "STRING", "description": "Nouvelle date de fin de la répétition (JJ/MM/AAAA), ou « aucune »"},
        }, "required": ["rdv_id"]},
        classification=WRITE, run=modifier_rdv, describe=describe_modifier, done=done_modifier, needs_user=True,
    ))
    register(Tool(
        name="supprimer_rdv",
        description=f"Supprime un rendez-vous {INTERNAL}. Retrouver d'abord son numéro avec lister_rdv.",
        parameters={"type": "OBJECT", "properties": {
            "rdv_id": RDV_ID,
            "occurrence": {"type": "STRING", "description": "Pour une série : date (AAAA-MM-JJ) de la seule occurrence à supprimer. Sans cette date, toute la série est supprimée."},
        }, "required": ["rdv_id"]},
        classification=IRREVERSIBLE, run=supprimer_rdv, describe=describe_supprimer,
        done=lambda result: "Rendez-vous supprimé.", needs_user=True,
    ))


register_agenda_tools()
