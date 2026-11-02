"""Agenda interne par utilisateur (phase B5) : saisie des horaires, durées, conflits, affichage."""
import calendar
import os
import re
import unicodedata
from datetime import datetime, timedelta

from tools import TZ, tz, valid_zone

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
    now = now or datetime.now(tz())
    text = str(value or "").strip().lower()
    if not text:
        raise AgendaInputError("Indique quand : par exemple demain 14h ou 27/10 à 9h30.")
    if ISO_DATETIME.fullmatch(text):
        try:
            start = datetime.fromisoformat(text.replace("t", " "))
        except ValueError:
            raise AgendaInputError(f"Date non reconnue : « {value} ».")
        start = start.replace(tzinfo=tz()) if start.tzinfo is None else start.astimezone(tz())
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
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz())
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


REPEAT_LABELS = {
    "daily": "chaque jour", "weekdays": "du lundi au vendredi", "weekly": "chaque semaine", "monthly": "chaque mois",
}
NO_REPEAT = ("aucune", "aucun", "non", "jamais", "stop")
MAX_SCAN_DAYS = 400  # une période lue ne dépasse jamais ce nombre de jours (garde-fou)


def fold(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(text.replace("'", " ").replace("-", " ").split())


def parse_repeat(text, start=None, now=None):
    """« chaque semaine », « tous les jours jusqu'au 31/12 », « jours ouvrés » -> (règle, dernier jour ou None).

    Règles : daily, weekdays, weekly, monthly. Si `start` est fourni, la cohérence avec le premier rendez-vous est vérifiée.
    """
    now = now or datetime.now(tz())
    raw = str(text or "").strip()
    lowered = raw.lower().replace("\u2019", "'")
    until_text = None
    match = re.search(r"\bjusqu['\s]*(?:au|à|a|en)?\s+(.+)$", lowered)
    if match:
        until_text, lowered = match[1].strip(), lowered[: match.start()]
    key = fold(lowered)
    if re.search(r"jours ouvr|en semaine|lundi au vendredi|lun ven|weekdays?", key):
        rule = "weekdays"
    elif re.search(r"chaque jour|tous les jours|quotidien|journalier|daily|chaque matin|chaque soir", key):
        rule = "daily"
    elif re.search(r"chaque mois|tous les mois|mensuel|monthly", key):
        rule = "monthly"
    elif re.search(r"chaque semaine|toutes les semaines|hebdo|weekly|chaque (?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
                   r"|tous les (?:lundis|mardis|mercredis|jeudis|vendredis|samedis|dimanches)", key):
        rule = "weekly"
    else:
        raise AgendaInputError(
            f"Répétition non reconnue : « {raw} ». Exemples : chaque jour, chaque semaine, jours ouvrés, chaque mois, "
            "chaque semaine jusqu'au 31/12."
        )
    until = None
    if until_text is not None:
        until = parse_day(until_text, now.date())
        if until < now.date():
            raise AgendaInputError("La date de fin de répétition est déjà passée.")
    if start is not None:
        check_series(start, rule, until, key)
    return rule, until


def check_series(start, rule, until, key=""):
    """Cohérence d'une série avec son premier rendez-vous."""
    local = start.astimezone(tz())
    if until is not None and until < local.date():
        raise AgendaInputError("La répétition doit se terminer après le premier rendez-vous.")
    if rule == "weekdays" and local.weekday() > 4:
        raise AgendaInputError("Pour « du lundi au vendredi », le premier rendez-vous doit tomber un jour de semaine.")
    named = re.search(r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)", key)
    if rule == "weekly" and named and WEEKDAYS.index(named[1]) != local.weekday():
        raise AgendaInputError(f"Le premier rendez-vous doit tomber un {named[1]} pour cette répétition.")


def repeat_label(rule, until=None):
    label = REPEAT_LABELS.get(rule)
    if label and until:
        label += f" jusqu'au {until:%d/%m/%Y}"
    return label


def parse_event(text, now=None):
    """« Titre | quand | durée | lieu | répétition » (tout sauf titre et moment est facultatif, un champ peut rester vide)
    -> dict {title, start, end, location, repeat, until}."""
    parts = [p.strip() for p in str(text or "").split("|")]
    if len(parts) < 2 or len(parts) > 5:
        raise AgendaInputError(
            "Format : titre | quand | durée | lieu | répétition (tout sauf le titre et le moment est facultatif). "
            "Exemple : Réunion | demain 14h | 1h30 | Douala | chaque semaine"
        )
    start = parse_start(parts[1], now)
    duration = parse_duration(parts[2]) if len(parts) > 2 else DEFAULT_DURATION
    rule = until = None
    if len(parts) > 4 and parts[4]:
        rule, until = parse_repeat(parts[4], start, now)
    return {
        "title": clean_title(parts[0]), "start": start, "end": start + duration,
        "location": clean_location(parts[3]) if len(parts) > 3 else None, "repeat": rule, "until": until,
    }


def matches_day(rule, first, day):
    """Le jour local `day` fait-il partie de la série commençant le jour local `first` ?"""
    if day < first:
        return False
    if rule == "daily":
        return True
    if rule == "weekdays":
        return day.weekday() < 5
    if rule == "weekly":
        return day.weekday() == first.weekday()
    if rule == "monthly":
        return day.day == min(first.day, calendar.monthrange(day.year, day.month)[1])
    return False


def occurrences(event, start, end):
    """Occurrences d'un rendez-vous récurrent qui touchent la période [start, end[ (datetimes avec fuseau).

    Chaque occurrence est une copie du rendez-vous avec son propre début/fin et `occurrence` (jour local, ISO).
    L'heure locale de la série est celle de son fuseau d'origine (`series_tz`).
    """
    rule = event.get("recurrence")
    if not rule:
        return [event] if event["start_at"] < end and event["end_at"] > start else []
    zone = valid_zone(event.get("series_tz")) or TZ
    first_local = event["start_at"].astimezone(zone)
    first_day = first_local.date()
    duration = event["end_at"] - event["start_at"]
    until = event.get("recurrence_until")
    skipped = set(event.get("skipped_days") or [])
    day = max(first_day, start.astimezone(zone).date() - timedelta(days=2))
    last = min(end.astimezone(zone).date() + timedelta(days=1), day + timedelta(days=MAX_SCAN_DAYS))
    found = []
    while day <= last:
        if (until is None or day <= until) and day not in skipped and matches_day(rule, first_day, day):
            occ_start = datetime.combine(day, first_local.timetz().replace(tzinfo=None), tzinfo=zone)
            occ_end = occ_start + duration
            if occ_start < end and occ_end > start:
                found.append({**event, "start_at": occ_start, "end_at": occ_end, "occurrence": day.isoformat()})
        day += timedelta(days=1)
    return found


def build_changes(current, data):
    """Modifications demandées pour un rendez-vous existant -> arguments de db.update_event.

    `data` peut contenir : title, location, start, duration, repeat (« aucune » arrête la répétition), until
    (« aucune » enlève la date de fin). Lève AgendaInputError si la demande est invalide ou vide.
    """
    changes = {}
    if "title" in data:
        changes["title"] = clean_title(data["title"])
    if "location" in data:
        changes["location"] = clean_location(data["location"])
    start, duration = current["start_at"], current["end_at"] - current["start_at"]
    if "start" in data or "duration" in data:
        start = parse_start(data["start"]) if "start" in data else start
        if "duration" in data:
            duration = parse_duration(data["duration"])
        changes["start_at"], changes["end_at"] = start, start + duration
    rule, until, key = current["recurrence"], current["recurrence_until"], ""
    if "repeat" in data:
        text = str(data["repeat"] or "").strip()
        key = fold(text)
        if not text or key in NO_REPEAT:
            rule, until = None, None
            changes["recurrence"] = None
        else:
            rule, parsed_until = parse_repeat(text, None)
            changes["recurrence"] = rule
            if parsed_until is not None:
                until = changes["until"] = parsed_until
    if "until" in data and rule:
        text = str(data["until"] or "").strip().lower()
        until = None if not text or fold(text) in NO_REPEAT else parse_day(text, datetime.now(tz()).date())
        changes["until"] = until
    if rule and (set(changes) & {"start_at", "recurrence", "until"}):
        check_series(start, rule, until, key)
    if not changes:
        raise AgendaInputError("aucune modification demandée")
    return changes


def parse_range(word, now=None):
    """Mot de /agenda -> (début de période, nombre de jours). Sans mot : 7 jours."""
    now = now or datetime.now(tz())
    key = str(word or "").strip().lower() or "semaine"
    if key not in RANGES:
        raise AgendaInputError("Utilise : /agenda, /agenda aujourd'hui, /agenda demain, /agenda semaine ou /agenda mois.")
    day = now.date() + (timedelta(days=1) if key == "demain" else timedelta(0))
    return datetime(day.year, day.month, day.day, tzinfo=tz()), RANGES[key]


def public(event):
    data = {
        "id": event["id"], "title": event["title"], "location": event["location"],
        "start": event["start_at"].astimezone(tz()).isoformat(timespec="minutes"),
        "end": event["end_at"].astimezone(tz()).isoformat(timespec="minutes"),
    }
    if event.get("recurrence"):
        data["repeat"] = repeat_label(event["recurrence"], event.get("recurrence_until"))
        data["occurrence"] = event.get("occurrence")
    return data
