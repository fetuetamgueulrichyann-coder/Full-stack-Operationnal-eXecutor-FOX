"""Briefing du matin : tâches en retard, du jour et à venir, agenda du propriétaire (phase B3).

Texte construit par le code, sans appel au modèle : il ne consomme aucun quota et ne peut rien inventer.
"""
import logging
import re
from datetime import datetime, timedelta

import db
import tasks
from tools import REGISTRY, TZ

log = logging.getLogger("orchestrator.briefing")

DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MAX_LINES = 8
SOON_DAYS = 3


class BriefingInputError(ValueError):
    """Réglage invalide ; le message est destiné à l'utilisateur."""


def parse_time(value):
    """« 7:30 », « 07:30 », « 7h30 », « 8h » -> « 07:30 »."""
    match = re.fullmatch(r"\s*(\d{1,2})\s*(?:[h:]\s*(\d{2})?)\s*", str(value or "").lower())
    if not match:
        raise BriefingInputError("Heure non reconnue. Exemples : 07:30, 8h, 7h45.")
    hour, minute = int(match[1]), int(match[2] or 0)
    if hour > 23 or minute > 59:
        raise BriefingInputError("Heure non reconnue. Exemples : 07:30, 8h, 7h45.")
    return f"{hour:02d}:{minute:02d}"


def configure(user_id, enabled=None, time=None, now=None):
    """Active/désactive le briefing et/ou change l'heure. Retourne les réglages.

    Activer ou régler une heure déjà passée aujourd'hui ne déclenche pas d'envoi rétroactif : le prochain
    briefing est celui de demain.
    """
    now = now or datetime.now(TZ)
    send_time = parse_time(time) if time is not None else None
    effective = send_time or db.get_briefing(user_id)["time"]
    skip_today = now.date() if now.strftime("%H:%M") >= effective else None
    return db.set_briefing(user_id, enabled, send_time, today=skip_today)


def agenda_events(role, now):
    """Événements du jour (propriétaire seulement). None si l'agenda est indisponible ou non configuré."""
    tool = REGISTRY.get("lire_agenda")
    if tool is None or not tool.allowed_for(role):
        return None
    try:
        return tool.run(date_debut=now.strftime("%Y-%m-%d"))
    except Exception as exc:
        # Cause visible dans les journaux (type et début du message, jamais de secret) ; le texte du briefing reste générique.
        log.warning("Agenda indisponible pour le briefing : %s: %s", type(exc).__name__, str(exc)[:200])
        return False  # configuré mais en panne : on le dit au lieu de l'ignorer


def own_events(user_id, now):
    """Rendez-vous du jour de l'agenda interne, au même format que les événements Google."""
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    items = db.list_events(user_id, start, start + timedelta(days=1))
    return [
        {"debut": e["start_at"].astimezone(TZ).isoformat(timespec="minutes"),
         "fin": e["end_at"].astimezone(TZ).isoformat(timespec="minutes"),
         "titre": e["title"], "lieu": e["location"]}
        for e in items
    ]


def event_line(event):
    start = str(event.get("debut") or "")
    end = str(event.get("fin") or "")
    when = f"{start[11:16]}-{end[11:16]} " if len(start) > 10 and len(end) > 10 else "toute la journée "
    place = f" ({event['lieu']})" if event.get("lieu") else ""
    return f"- {when}{event.get('titre', '(sans titre)')}{place}"


def task_line(task, today):
    local = task["due_at"].astimezone(TZ) if task["due_at"] else None
    prefix = ""
    if local and db.has_time(local):  # l'heure se lit dans le fuseau de FOX : « jour seul » = 23:59:59 local
        prefix = f"{local:%H:%M} "
    if local and local.date() < today:
        prefix = f"depuis le {local:%d/%m} · "
    elif local and local.date() > today:
        prefix = f"{local:%d/%m} · "
    flag = " (priorité haute)" if task["priority"] == "haute" else ""
    return f"- {prefix}{task['title']}{flag}"


def section(title, lines):
    if not lines:
        return []
    shown = lines[:MAX_LINES]
    more = [f"… et {len(lines) - MAX_LINES} autre(s)."] if len(lines) > MAX_LINES else []
    return [f"**{title} ({len(lines)})**", *shown, *more, ""]


def build(user_id, role="owner", now=None):
    """Texte Markdown du briefing de la journée."""
    now = now or datetime.now(TZ)
    today = now.date()
    overdue, due_today, soon, undated = [], [], [], 0
    for task in db.list_tasks(user_id, db.OPEN_STATUSES, limit=200):
        if task["due_at"] is None:
            undated += 1
            continue
        day = task["due_at"].astimezone(TZ).date()
        if day < today:
            overdue.append(task)
        elif day == today:
            due_today.append(task)
        elif day <= today + timedelta(days=SOON_DAYS):
            soon.append(task)
    due_today.sort(key=lambda t: (not db.has_time(t["due_at"].astimezone(TZ)), t["due_at"]))

    lines = [f"**Briefing du {DAYS[today.weekday()]} {today:%d/%m}**", ""]
    lines += section("En retard", [task_line(t, today) for t in overdue])
    lines += section("Aujourd'hui", [task_line(t, today) for t in due_today])

    google = agenda_events(role, now)
    events = own_events(user_id, now) + (google if google else [])
    events.sort(key=lambda e: (len(str(e.get("debut") or "")) <= 10, str(e.get("debut") or "")))  # les événements à heure d'abord, puis ceux de la journée entière
    if events:
        lines += section("Agenda", [event_line(e) for e in events])
    if google is False:
        lines += ["Agenda Google indisponible pour le moment.", ""]

    lines += section(f"Dans les {SOON_DAYS} prochains jours", [task_line(t, today) for t in soon])
    if undated:
        lines.append(f"{undated} tâche(s) sans échéance. /taches pour les voir.")
    if not (overdue or due_today or soon or undated or events or google is False):
        lines = [lines[0], "", "Rien de prévu aujourd'hui."]
    return "\n".join(lines).strip()
