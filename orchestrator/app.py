import hmac
import logging
import os
import uuid

from flask import Flask, jsonify, request
from google.genai import errors

import accounts
import agent
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")

# Plafond GLOBAL (tous utilisateurs confondus) par jour, UTC.
MAX_CALLS_PER_DAY = int(os.environ.get("MAX_CALLS_PER_DAY", "200"))
MAX_TOKENS_PER_DAY = int(os.environ.get("MAX_TOKENS_PER_DAY", "500000"))
# Plafonds PAR UTILISATEUR et par jour, selon le rôle.
OWNER_MAX_CALLS_PER_DAY = int(os.environ.get("OWNER_MAX_CALLS_PER_DAY", "200"))
OWNER_MAX_TOKENS_PER_DAY = int(os.environ.get("OWNER_MAX_TOKENS_PER_DAY", "500000"))
TESTER_MAX_CALLS_PER_DAY = int(os.environ.get("TESTER_MAX_CALLS_PER_DAY", "30"))
TESTER_MAX_TOKENS_PER_DAY = int(os.environ.get("TESTER_MAX_TOKENS_PER_DAY", "15000"))

# Jeton partagé entre l'orchestrateur et le bot Telegram (service interne de confiance).
SERVICE_TOKEN = os.environ.get("FOX_SERVICE_TOKEN", "").strip()
COOKIE_NAME = "fox_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"  # à activer derrière HTTPS (VPS)
MAX_INPUT_CHARS = 4000

app = Flask(__name__)

AUTH_STATUS = {
    "invalid_credentials": 401,
    "too_many_attempts": 429,
    "invitation_invalid": 400,
    "username_taken": 409,
    "username_invalid": 400,
    "password_weak": 400,
}


def user_limits(role):
    if role == accounts.ROLE_OWNER:
        return OWNER_MAX_CALLS_PER_DAY, OWNER_MAX_TOKENS_PER_DAY
    return TESTER_MAX_CALLS_PER_DAY, TESTER_MAX_TOKENS_PER_DAY


@app.before_request
def require_json_for_post():
    # Un corps JSON déclenche une vérification préalable (preflight) des navigateurs entre
    # origines différentes : un formulaire d'un autre site ne peut pas imiter ces requêtes.
    if request.method == "POST" and not request.is_json:
        return jsonify(error="Content-Type application/json requis"), 415


@app.errorhandler(accounts.AuthError)
def handle_auth_error(exc):
    return jsonify(error=exc.message, code=exc.code), AUTH_STATUS.get(exc.code, 400)


def authenticate(data):
    """Retourne (utilisateur, erreur). L'identité ne vient JAMAIS d'un user_id du client.

    - interface web : cookie de session ;
    - bot Telegram : jeton de service + identifiant Telegram, résolu en compte actif.
    """
    service_token = request.headers.get("X-Fox-Service-Token")
    if service_token is not None:
        if not SERVICE_TOKEN or not hmac.compare_digest(service_token, SERVICE_TOKEN):
            db.audit(None, "denied", None, {"reason": "jeton de service invalide"})
            return None, (jsonify(error="non authentifie"), 401)
        user = accounts.user_from_telegram(data.get("telegram_id"))
        if user is None:
            db.audit(None, "denied", None, {"reason": "compte Telegram inconnu ou desactive"})
            return None, (jsonify(error="non autorise"), 403)
        return user, None

    user = accounts.user_from_session(request.cookies.get(COOKIE_NAME))
    if user is None:
        return None, (jsonify(error="non authentifie"), 401)
    return user, None


def open_session(response, token):
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=accounts.SESSION_DAYS * 86400,
        httponly=True,
        samesite="Lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    return response


@app.get("/health")
def health():
    return jsonify(status="ok")


# --- Comptes -----------------------------------------------------------------
@app.post("/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    user = accounts.register_from_ip(
        data.get("code"), data.get("username"), data.get("password"), request.remote_addr
    )
    db.audit(user["id"], "registered", None, {"role": user["role"]})
    token, user = accounts.login(data.get("username"), data.get("password"), request.remote_addr)
    return open_session(jsonify(user=user), token), 201


@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    try:
        token, user = accounts.login(data.get("username"), data.get("password"), request.remote_addr)
    except accounts.AuthError as exc:
        db.audit(None, "login_failed", None, {"code": exc.code})
        raise
    db.audit(user["id"], "login", None, {})
    return open_session(jsonify(user=user), token)


@app.post("/auth/logout")
def logout():
    token = request.cookies.get(COOKIE_NAME)
    user = accounts.user_from_session(token)
    accounts.logout(token)
    if user:
        db.audit(user["id"], "logout", None, {})
    response = jsonify(status="ok")
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/auth/me")
def me():
    user, error = authenticate({})
    if error:
        return error
    return jsonify(user=user)


# --- Messages ----------------------------------------------------------------
@app.post("/message")
def message():
    data = request.get_json(silent=True) or {}
    user, error = authenticate(data)
    if error:
        return error
    user_id, role = user["id"], user["role"]

    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="text est requis"), 400
    if len(text) > MAX_INPUT_CHARS:
        return jsonify(error="message trop long"), 413

    if agent.is_undo_request(text):
        reply, pending = agent.propose_undo(user_id)
    else:
        user_calls, user_tokens = user_limits(role)
        try:
            db.reserve_quota(user_id, user_calls, user_tokens, MAX_CALLS_PER_DAY, MAX_TOKENS_PER_DAY)
        except db.QuotaExceeded as exc:
            return jsonify(error="plafond journalier atteint", scope=exc.scope), 429

        try:
            reply, tokens, pending = agent.chat(user_id, text, role)
        except agent.ProviderQuotaExceeded:
            return jsonify(error="capacite du service atteinte"), 503
        except agent.LLMUnavailable:
            return jsonify(error="modele indisponible"), 503
        except errors.APIError as exc:
            log.error(
                "Erreur Gemini non temporaire (code=%s, statut=%s) : %s",
                exc.code, exc.status, exc.message,
            )
            return jsonify(error="erreur modele", code=exc.code), 502
        except Exception:
            log.exception("echec de l'appel au modele")
            return jsonify(error="service indisponible"), 502

        if tokens:
            db.add_tokens(user_id, tokens)

    if pending:
        reply = "Action(s) en attente de confirmation :\n" + "\n".join(
            f"- {p['summary']}" for p in pending
        )
    if reply != agent.EMPTY_REPLY:
        db.save_exchange(user_id, text, reply)

    return jsonify(reply=reply, pending=pending)


def decide(action_id, confirm):
    data = request.get_json(silent=True) or {}
    user, error = authenticate(data)
    if error:
        return error

    try:
        uuid.UUID(action_id)
    except ValueError:
        return jsonify(error="identifiant d'action invalide"), 400

    result = agent.resolve_action(user["id"], action_id, confirm, user["role"])
    label = "Confirmation de l'action proposée" if confirm else "Annulation de l'action proposée"
    db.save_exchange(user["id"], label, result)
    return jsonify(reply=result)


@app.post("/actions/<action_id>/confirm")
def confirm_action(action_id):
    return decide(action_id, True)


@app.post("/actions/<action_id>/cancel")
def cancel_action(action_id):
    return decide(action_id, False)
