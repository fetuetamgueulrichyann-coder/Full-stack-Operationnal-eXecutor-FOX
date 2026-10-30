"""Agenda interne par utilisateur (phase B5) : saisie des horaires, durées, conflits, affichage."""
import os
import re
from datetime import datetime, timedelta

from tools import TZ

TITLE_MAX = 150
LOCATION_MAX = 100
DEFAULT_DURATION = timedelta(hours=1)
MIN_DURATION = timedelta(minutes=5)
MAX_DURATION = timedelta(hours=24)
REMIND_MINUTES = int(os.environ.get("AGENDA_REMIND_MINUTES", "30"))
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
RANGES = {"aujourd'hui": 1, "aujourdhui": 1, "demain": 1, "semaine": 7, "mois": 30}


class AgendaInputError(ValueError):
    """Saisie invalide ; le message est destiné à l'utilisateur."""


def clean_title(value):
    title = " ".join(str(value or "").split())
    if not title:
        raise AgendaInputError("Le titre du rendez-vous est vide.")
    if len(title) > TITLE_MAX:
        raise AgendaInputError(f"Le titre est trop long ({TITLE_MAX} caractères maximum).")
    return title


def clean_location(value):
    place = " ".join(str(value or "").split())
    if len(place) > LOCATION_MAX:
        raise AgendaInputError(f"Le lieu est trop long ({LOCATION_MAX} caractères maximum).")
    return place or None


TIME_AT_END = re.compile(r"(?:^|\s)(?:à\s+)?(\d{1,2})\s*(?:h|:)\s*(\d{2})?$")
ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2})?")


def next_weekday(today, index):
    """Prochaine occurrence du jour de la semaine, strictement après aujourd'hui."""
    return today + timedelta(days=(index - today.weekday() - 1) % 7 + 1)


def parse_start(value, now=None):
    """« demain 14h », « 27/10 à 9h30 », « lundi 10:00 », « 14h » (aujourd'hui), « 2026-10-27T14:00 » -> datetime local."""
    now = now or datetime.now(TZ)
    text = str(value or "").strip().lower()
    if not text:
        raise AgendaInputError("Indique quand : par exemple demain 14h ou 27/10 à 9h30.")
    if ISO_DATETIME.fullmatch(text):
        try:
            start = datetime.fromisoformat(text.replace("t", " "))
        except ValueError:
            raise AgendaInputError(f"Date non reconnue : « {value} ».")
        start = start.replace(tzinfo=TZ) if start.tzinfo is None else start.astimezone(TZ)
        return check_future(start, now)
    match = TIME_AT_END.search(text)
    if not match:
        raise AgendaInputError("Indique l'heure : par exemple demain 14h ou 27/10 à 9h30.")
    hour, minute = int(match[1]), int(match[2] or 0)
    if hour > 23 or minute > 59:
        raise AgendaInputError(f"Heure non reconnue : « {match[0].strip()} ».")
    day_text = text[: match.start()].strip()
    today = now.date()
    if not day_text:
        day = today
    elif day_text in ("après-demain", "apres-demain"):
        day = today + timedelta(days=2)
    elif day_text in WEEKDAYS:
        day = next_weekday(today, WEEKDAYS.index(day_text))
    else:
        day = parse_day(day_text, today)
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return check_future(start, now)


def parse_day(text, today):
    """JJ/MM, JJ/MM/AAAA, AAAA-MM-JJ, « aujourd'hui », « demain ». Sans année, le jour reste dans l'année en cours
    (une date passée est refusée plutôt que reportée à l'an prochain)."""
    bad = AgendaInputError(f"Date non reconnue : « {text} ». Exemples : demain, lundi, 27/10, 27/10/2026.")
    if text in ("aujourd'hui", "aujourdhui"):
        return today
    if text == "demain":
        return today + timedelta(days=1)
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", text) or re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        raise bad
    try:
        if "-" in text:
            return datetime(int(match[1]), int(match[2]), int(match[3])).date()
        return datetime(int(match[3] or today.year), int(match[2]), int(match[1])).date()
    except ValueError:
        raise bad


def check_future(start, now):
    if start < now - timedelta(minutes=1):
        raise AgendaInputError("Ce moment est déjà passé.")
    return start


def parse_duration(value):
    """« 1h30 », « 2h », « 45 min », « 90 » (minutes) -> timedelta (5 minutes à 24 heures)."""
    text = str(value or "").strip().lower().replace(" ", "")
    if not text:
        return DEFAULT_DURATION
    hours = re.fullmatch(r"(\d{1,2})h(\d{2})?", text)
    minutes = re.fullmatch(r"(\d{1,4})(?:min|mn|m)?", text)
    if hours:
        delta = timedelta(hours=int(hours[1]), minutes=int(hours[2] or 0))
    elif minutes:
        delta = timedelta(minutes=int(minutes[1]))
    else:
        raise AgendaInputError(f"Durée non reconnue : « {value} ». Exemples : 1h30, 45 min, 2h.")
    if delta < MIN_DURATION or delta > MAX_DURATION:
        raise AgendaInputError("La durée doit être comprise entre 5 minutes et 24 heures.")
    return delta


def parse_event(text, now=None):
    """« Titre | quand | durée | lieu » (durée et lieu facultatifs) -> dict {title, start, end, location}."""
    parts = [p.strip() for p in str(text or "").split("|")]
    if len(parts) < 2 or len(parts) > 4:
        raise AgendaInputError("Format : titre | quand | durée | lieu (durée et lieu facultatifs). Exemple : Réunion | demain 14h | 1h30 | Douala")
    start = parse_start(parts[1], now)
    duration = parse_duration(parts[2]) if len(parts) > 2 else DEFAULT_DURATION
    return {
        "title": clean_title(parts[0]), "start": start, "end": start + duration,
        "location": clean_location(parts[3]) if len(parts) > 3 else None,
    }


def parse_range(word, now=None):
    """Mot de /agenda -> (début de période, nombre de jours). Sans mot : 7 jours."""
    now = now or datetime.now(TZ)
    key = str(word or "").strip().lower() or "semaine"
    if key not in RANGES:
        raise AgendaInputError("Utilise : /agenda, /agenda aujourd'hui, /agenda demain, /agenda semaine ou /agenda mois.")
    day = now.date() + (timedelta(days=1) if key == "demain" else timedelta(0))
    return datetime(day.year, day.month, day.day, tzinfo=TZ), RANGES[key]


def public(event):
    return {
        "id": event["id"], "title": event["title"], "location": event["location"],
        "start": event["start_at"].astimezone(TZ).isoformat(timespec="minutes"),
        "end": event["end_at"].astimezone(TZ).isoformat(timespec="minutes"),
    }
