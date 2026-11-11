"""Envoi des rappels (rendez-vous et tâches) en notifications push. Lancé par push_worker.py toutes les 30 secondes.

Les rappels sont « réclamés » avec leur propre marqueur (canal « push ») : Telegram et le navigateur ne se volent jamais
un rappel. Le contenu reste court : le titre et l'heure, rien d'autre.
"""
import logging
from datetime import datetime

import agenda
import db
import push_store
import tools
import webpush

log = logging.getLogger("push")


def _hhmm(moment, zone):
    with tools.user_zone(zone):
        return moment.astimezone(tools.tz()).strftime("%H:%M")


def event_payload(reminder):
    when = _hhmm(reminder["start_at"], reminder.get("timezone"))
    body = f"À {when}" + (f" · {reminder['location']}" if reminder.get("location") else "")
    linked = reminder.get("tasks") or []
    if linked:
        body += " · à faire : " + ", ".join(linked[:3])
    return {"title": reminder["title"], "body": body, "url": "/agenda", "tag": f"event-{reminder['id']}"}


def task_payload(reminder):
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
        deliver(reminder["user_id"], event_payload(reminder), sender)
        handled += 1
    for reminder in db.claim_due_reminders(channel="push"):
        deliver(reminder["user_id"], task_payload(reminder), sender)
        handled += 1
    return handled
