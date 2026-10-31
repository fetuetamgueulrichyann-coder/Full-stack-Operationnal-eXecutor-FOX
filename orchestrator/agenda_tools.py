"""Outils IA de l'agenda interne (phase B5) : lister, créer, modifier et supprimer des rendez-vous.

Ils agissent toujours sur l'agenda de l'utilisateur qui parle (jamais fourni par le modèle) et sont
ouverts au propriétaire comme aux testeurs. Les noms (`*_rdv`) diffèrent des outils Google Calendar
(`*_evenement`), réservés au propriétaire.
"""
from datetime import datetime, timedelta

import agenda
import db
from tools import DATE_HELP, IRREVERSIBLE, READ, TZ, WRITE, Tool, register

NO_LOCATION = ("aucun", "aucune", "")


def human(event):
    start = event["start_at"].astimezone(TZ)
    end = event["end_at"].astimezone(TZ)
    return f"{start:%d/%m/%Y %H:%M}-{end:%H:%M}"


def lister_rdv(user_id, periode=None):
    now = datetime.now(TZ)
    start, days = agenda.parse_range(periode, now)
    events = db.list_events(user_id, start, start + timedelta(days=days))
    return [agenda.public(e) for e in events[:30]]


def creer_rdv(user_id, titre, debut, duree=None, lieu=None):
    start = agenda.parse_start(debut)
    event = db.create_event(
        user_id, agenda.clean_title(titre), start, start + agenda.parse_duration(duree), agenda.clean_location(lieu)
    )
    conflicts = db.find_conflicts(user_id, event["start_at"], event["end_at"], exclude_id=event["id"])
    db.audit(user_id, "event_created", None, {"event_id": event["id"], "source": "ia"})
    return {"id": event["id"], "user_id": user_id, "conflits": [c["title"] for c in conflicts]}


def modifier_rdv(user_id, rdv_id, titre=None, debut=None, duree=None, lieu=None):
    current = db.get_event(user_id, int(rdv_id))
    if current is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    changes = {}
    if titre is not None:
        changes["title"] = agenda.clean_title(titre)
    if lieu is not None:
        changes["location"] = None if str(lieu).strip().lower() in NO_LOCATION else agenda.clean_location(lieu)
    if debut is not None or duree is not None:
        start = agenda.parse_start(debut) if debut is not None else current["start_at"]
        length = agenda.parse_duration(duree) if duree is not None else current["end_at"] - current["start_at"]
        changes["start_at"], changes["end_at"] = start, start + length
    if not changes:
        raise agenda.AgendaInputError("aucune modification demandée")
    event = db.update_event(user_id, int(rdv_id), **changes)
    if event is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    conflicts = db.find_conflicts(user_id, event["start_at"], event["end_at"], exclude_id=event["id"])
    db.audit(user_id, "event_updated", None, {"event_id": event["id"], "fields": sorted(changes), "source": "ia"})
    return {"id": event["id"], "conflits": [c["title"] for c in conflicts]}


def supprimer_rdv(user_id, rdv_id):
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
    conflicts = db.find_conflicts(user_id, start, end)
    if conflicts:
        lines.append("Attention, chevauche : " + ", ".join(c["title"] for c in conflicts))
    return "\n".join(lines)


def describe_modifier(args, user_id):
    event = db.get_event(user_id, int(args["rdv_id"]))
    if event is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
    lines = [f"Modifier le rendez-vous « {event['title']} » ({human(event)})"]
    if args.get("titre"):
        lines.append(f"Nouveau titre : « {agenda.clean_title(args['titre'])} »")
    if args.get("debut"):
        lines.append(f"Nouveau début : {agenda.parse_start(args['debut']):%d/%m/%Y %H:%M}")
    if args.get("duree"):
        agenda.parse_duration(args["duree"])
        lines.append(f"Nouvelle durée : {args['duree']}")
    if args.get("lieu") is not None:
        lines.append("Lieu supprimé" if str(args["lieu"]).strip().lower() in NO_LOCATION else f"Nouveau lieu : {agenda.clean_location(args['lieu'])}")
    if len(lines) == 1:
        raise agenda.AgendaInputError("aucune modification demandée")
    return "\n".join(lines)


def describe_supprimer(args, user_id):
    event = db.get_event(user_id, int(args["rdv_id"]))
    if event is None:
        raise agenda.AgendaInputError("rendez-vous introuvable")
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
        }, "required": ["rdv_id"]},
        classification=WRITE, run=modifier_rdv, describe=describe_modifier, done=done_modifier, needs_user=True,
    ))
    register(Tool(
        name="supprimer_rdv",
        description=f"Supprime un rendez-vous {INTERNAL}. Retrouver d'abord son numéro avec lister_rdv.",
        parameters={"type": "OBJECT", "properties": {"rdv_id": RDV_ID}, "required": ["rdv_id"]},
        classification=IRREVERSIBLE, run=supprimer_rdv, describe=describe_supprimer,
        done=lambda result: "Rendez-vous supprimé.", needs_user=True,
    ))


register_agenda_tools()
