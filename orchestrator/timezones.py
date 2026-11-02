"""Choix du fuseau horaire par l'utilisateur : noms IANA (Europe/Paris) ou noms de villes courants (Paris, Douala)."""
import unicodedata
from functools import lru_cache
from zoneinfo import available_timezones

from tools import valid_zone

ALIASES = {
    "paris": "Europe/Paris", "france": "Europe/Paris", "bruxelles": "Europe/Brussels", "belgique": "Europe/Brussels",
    "geneve": "Europe/Zurich", "suisse": "Europe/Zurich", "londres": "Europe/London", "london": "Europe/London",
    "berlin": "Europe/Berlin", "madrid": "Europe/Madrid", "rome": "Europe/Rome", "lisbonne": "Europe/Lisbon",
    "douala": "Africa/Douala", "yaounde": "Africa/Douala", "cameroun": "Africa/Douala",
    "libreville": "Africa/Libreville", "gabon": "Africa/Libreville", "brazzaville": "Africa/Brazzaville",
    "kinshasa": "Africa/Kinshasa", "lagos": "Africa/Lagos", "nigeria": "Africa/Lagos", "abidjan": "Africa/Abidjan",
    "dakar": "Africa/Dakar", "casablanca": "Africa/Casablanca", "maroc": "Africa/Casablanca", "alger": "Africa/Algiers",
    "tunis": "Africa/Tunis", "le caire": "Africa/Cairo", "nairobi": "Africa/Nairobi",
    "johannesburg": "Africa/Johannesburg", "montreal": "America/Toronto", "toronto": "America/Toronto",
    "new york": "America/New_York", "chicago": "America/Chicago", "los angeles": "America/Los_Angeles",
    "dubai": "Asia/Dubai", "pekin": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "tokyo": "Asia/Tokyo",
    "utc": "UTC",
}


def fold(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(text.replace("_", " ").split())


@lru_cache(maxsize=1)
def iana_names():
    return {fold(name): name for name in available_timezones()}


def resolve(text):
    """Texte saisi -> nom IANA valide. Lève ValueError (message affichable) si le fuseau est inconnu."""
    key = fold(text)
    if not key:
        raise ValueError("Indique un fuseau : par exemple Europe/Paris, Africa/Douala ou une ville (Paris).")
    name = ALIASES.get(key) or iana_names().get(key)
    if name is None or valid_zone(name) is None:
        raise ValueError(f"Fuseau « {str(text).strip()[:40]} » inconnu. Utilise un nom comme Europe/Paris ou Africa/Douala.")
    return name
