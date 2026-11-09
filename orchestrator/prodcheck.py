"""Contrôle de la configuration au démarrage : signale (sans jamais bloquer) les réglages dangereux.

Les avertissements apparaissent dans `docker compose logs orchestrator`. Aucune valeur secrète n'est écrite :
on ne dit que ce qui ne va pas, jamais le contenu des variables.
"""
from urllib.parse import urlsplit

WEAK_PLACEHOLDERS = {"", "change_me", "changeme", "password", "postgres", "fox", "secret"}


def _is_local(host):
    return host in ("", "localhost", "127.0.0.1", "::1")


def problems(env):
    """Liste de phrases décrivant ce qui est dangereux pour une mise en ligne ; vide si tout va bien."""
    out = []
    origin = (env.get("PUBLIC_ORIGIN") or "").strip()
    domain = (env.get("DOMAIN") or "localhost").strip()
    scheme = urlsplit(origin).scheme if origin else ""
    public = scheme == "https" or not _is_local(domain)

    if public:
        if (env.get("COOKIE_SECURE") or "0").strip() != "1":
            out.append("COOKIE_SECURE devrait valoir 1 en ligne : sans cela le cookie de session peut circuler hors HTTPS.")
        if (env.get("TRUST_PROXY") or "0").strip() != "1":
            out.append("TRUST_PROXY devrait valoir 1 derrière le proxy : sinon toutes les visites semblent venir de la même adresse.")
        if not origin:
            out.append("PUBLIC_ORIGIN est vide : renseigne l'adresse publique (https://...) pour que les requêtes du navigateur soient acceptées.")
        elif scheme != "https":
            out.append("PUBLIC_ORIGIN devrait commencer par https:// en ligne.")

    token = (env.get("FOX_SERVICE_TOKEN") or "").strip()
    if token.lower() in WEAK_PLACEHOLDERS or len(token) < 32:
        out.append("FOX_SERVICE_TOKEN est vide, d'exemple ou trop court (32 caractères minimum).")
    if (env.get("POSTGRES_PASSWORD") or "").strip().lower() in WEAK_PLACEHOLDERS:
        out.append("POSTGRES_PASSWORD est vide ou d'exemple.")
    if public and (env.get("OWNER_PASSWORD") or "").strip():
        out.append("OWNER_PASSWORD est encore dans le .env : une fois le mot de passe appliqué, retire-le (admin.py set-password pour le changer).")
    mode = (env.get("REGISTRATION_MODE") or "invite").strip().lower()
    if mode not in ("invite", "closed"):
        out.append("REGISTRATION_MODE doit valoir invite ou closed ; « invite » est utilisé.")
    return out
