"""Sécurité des requêtes venant d'un navigateur (interface web React).

Deux protections complémentaires pour l'authentification par cookie :

1. Vérification de l'origine : toute requête qui modifie quelque chose (POST, PUT, PATCH, DELETE)
   doit venir d'une origine autorisée (en-tête Origin, ou Referer à défaut).
2. Jeton CSRF : une requête portant le cookie de session doit aussi envoyer un en-tête
   ``X-CSRF-Token``. Le jeton est dérivé du cookie de session par HMAC : un autre site ne peut ni le
   lire (il ne voit pas la réponse de FOX) ni le fabriquer (il ne connaît pas le cookie, HttpOnly).

Le bot Telegram s'identifie avec un jeton de service, pas avec un cookie : il n'est pas concerné.
"""
import hashlib
import hmac
import os
from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"
# La connexion et l'inscription n'ont pas encore de session : on vérifie seulement l'origine.
CSRF_EXEMPT_PATHS = frozenset({"/auth/login", "/auth/register"})


def normalize_origin(value):
    """« https://Exemple.com:443/chemin » -> « https://exemple.com:443 » ; None si inutilisable."""
    if not value or value == "null":
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def parse_origins(*values):
    origins = set()
    for value in values:
        for item in (value or "").split(","):
            origin = normalize_origin(item)
            if origin:
                origins.add(origin)
    return frozenset(origins)


# PUBLIC_ORIGIN : l'adresse publique de FOX (ex. https://fox.example.com).
# ALLOWED_ORIGINS : origines supplémentaires, séparées par des virgules (ex. le serveur de dev Vite).
PUBLIC_ORIGIN = os.environ.get("PUBLIC_ORIGIN", "").strip()
ALLOWED_ORIGINS = parse_origins(PUBLIC_ORIGIN, os.environ.get("ALLOWED_ORIGINS", ""))


def csrf_token(session_token):
    """Jeton CSRF lié à la session ; chaîne vide s'il n'y a pas de session."""
    if not session_token:
        return ""
    return hmac.new(session_token.encode(), b"fox-csrf-v1", hashlib.sha256).hexdigest()


def request_origin(headers):
    """Origine annoncée par le navigateur : Origin, sinon l'origine du Referer."""
    origin = headers.get("Origin")
    if origin is not None:
        return normalize_origin(origin), True
    referer = headers.get("Referer")
    if referer:
        return normalize_origin(referer), True
    return None, False


def origin_allowed(origin, same_origin):
    return origin is not None and (origin == same_origin or origin in ALLOWED_ORIGINS)


def check(method, path, headers, session_token, same_origin):
    """Retourne None si la requête est acceptable, sinon (code, message) pour une réponse 403."""
    if method not in UNSAFE_METHODS or headers.get("X-Fox-Service-Token") is not None:
        return None

    origin, announced = request_origin(headers)
    if session_token:
        if not announced:
            return "origin_missing", "origine de la requête absente"
    elif not announced:
        return None  # client sans navigateur et sans session : la route répondra elle-même
    if not origin_allowed(origin, normalize_origin(same_origin)):
        return "origin_forbidden", "origine non autorisée"

    if session_token and path not in CSRF_EXEMPT_PATHS:
        sent = headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, csrf_token(session_token)):
            return "csrf_invalid", "jeton CSRF absent ou invalide"
    return None
