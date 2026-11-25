"""Envoi des rappels (rendez-vous et tâches) en notifications push. Lancé par push_worker.py toutes les 30 secondes.

Les rappels sont « réclamés » avec leur propre marqueur (canal « push ») : Telegram et le navigateur ne se volent jamais
un rappel.

Confidentialité (phase 1b) : par défaut, la notification est DISCRÈTE. Le texte qui part vers le service de notification
(Google, Apple, Mozilla… selon le navigateur) est alors fabriqué sans jamais lire le titre, le lieu ni les tâches liées :
masquer le texte à l'écran ne suffirait pas, car il traverse ces services et s'affiche sur l'écran verrouillé. Le mode
« complet » (titre, heure, lieu, tâches) ne s'applique que si l'utilisateur l'a choisi dans ses réglages. En cas de doute
(réglage illisible, valeur inconnue, base indisponible), on reste discret.
"""
import logging
from datetime import datetime

import agenda
import db
import privacy_store
import push_store
import tools
import webpush

log = logging.getLogger("push")

DISCRET, COMPLET = "discret", "complet"
DISCREET_TITLE = "Rappel FOX"
DISCREET_BODY = "Touche pour ouvrir FOX."


def detail_for(user_id):
    """Détail choisi par CET utilisateur, relu à chaque envoi. Discret au moindre doute."""
    try:
        detail = privacy_store.get_settings(user_id)["notification_detail"]
    except Exception:  # base indisponible, ligne illisible : mieux vaut un rappel discret qu'un détail divulgué
        log.exception("réglage de notification illisible, rappel discret")
        return DISCRET
    return COMPLET if detail == COMPLET else DISCRET


def _discreet(reminder, kind, url):
    """Notification sans contenu : seule l'adresse interne et une étiquette technique (numéro) sont reprises."""
    return {"title": DISCREET_TITLE, "body": DISCREET_BODY, "url": url, "tag": f"{kind}-{reminder['id']}"}


def _hhmm(moment, zone):
    with tools.user_zone(zone):
        return moment.astimezone(tools.tz()).strftime("%H:%M")


def event_payload(reminder, detail=DISCRET):
    if detail != COMPLET:  # défaut sûr : le titre, le lieu et les tâches ne sont même pas lus
        return _discreet(reminder, "event", "/agenda")
    when = _hhmm(reminder["start_at"], reminder.get("timezone"))
    body = f"À {when}" + (f" · {reminder['location']}" if reminder.get("location") else "")
    linked = reminder.get("tasks") or []
    if linked:
        body += " · à faire : " + ", ".join(linked[:3])
    return {"title": reminder["title"], "body": body, "url": "/agenda", "tag": f"event-{reminder['id']}"}


def task_payload(reminder, detail=DISCRET):
    if detail != COMPLET:
        return _discreet(reminder, "task", "/taches")
    return {"title": reminder["title"], "body": "Tâche à faire maintenant", "url": "/taches", "tag": f"task-{reminder['id']}"}


def deliver(user_id, payload, sender=None):
    """Envoie à tous les appareils de l'utilisateur ; renvoie le nombre d'appareils atteints."""
    sender = sender or webpush.send
    reached = 0
    for subscription in push_store.for_user(user_id):
        try:
            sender(subscription, payload)
        except webpush.Gone:
            push_store.forget(subscription["endpoint"])
        except webpush.PushError as exc:
            log.warning("envoi refusé : %s", exc)
            push_store.mark_failed(subscription["endpoint"])
        else:
            push_store.mark_ok(subscription["endpoint"])
            reached += 1
    return reached


def run_once(sender=None):
    """Un passage : réclame les rappels dus et les envoie. Renvoie le nombre de rappels traités."""
    handled = 0
    for reminder in db.claim_due_event_reminders(agenda.REMIND_MINUTES, channel="push"):
        deliver(reminder["user_id"], event_payload(reminder, detail_for(reminder["user_id"])), sender)
        handled += 1
    for reminder in db.claim_due_reminders(channel="push"):
        deliver(reminder["user_id"], task_payload(reminder, detail_for(reminder["user_id"])), sender)
        handled += 1
    return handled
