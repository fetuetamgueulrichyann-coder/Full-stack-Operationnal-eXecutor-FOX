import hmac
import logging
import os
import time
import uuid
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from google.genai import errors

import accounts
import agent
import db
import briefing
import formatting
import journal
import mail
import memory
import agenda
import commercial
import finance
import finance_store
import quote_pdf
import tasks
import timezones
import tools
import prodcheck
import push_delivery
import push_store
import speech
import webpush
import websecurity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")
for _problem in prodcheck.problems(os.environ):
    log.warning("configuration : %s", _problem)

# Plafond GLOBAL (tous utilisateurs confondus) par jour, UTC.
MAX_CALLS_PER_DAY = int(os.environ.get("MAX_CALLS_PER_DAY", "200"))
MAX_TOKENS_PER_DAY = int(os.environ.get("MAX_TOKENS_PER_DAY", "500000"))
# Plafonds PAR UTILISATEUR et par jour, selon le rôle.
OWNER_MAX_CALLS_PER_DAY = int(os.environ.get("OWNER_MAX_CALLS_PER_DAY", "200"))
OWNER_MAX_TOKENS_PER_DAY = int(os.environ.get("OWNER_MAX_TOKENS_PER_DAY", "500000"))
TESTER_MAX_CALLS_PER_DAY = int(os.environ.get("TESTER_MAX_CALLS_PER_DAY", "30"))
TESTER_MAX_TOKENS_PER_DAY = int(os.environ.get("TESTER_MAX_TOKENS_PER_DAY", "15000"))
# Voix de FOX : caractères lus à voix haute par jour et par utilisateur.
OWNER_VOICE_CHARS_PER_DAY = int(os.environ.get("OWNER_VOICE_CHARS_PER_DAY", "40000"))
TESTER_VOICE_CHARS_PER_DAY = int(os.environ.get("TESTER_VOICE_CHARS_PER_DAY", "4000"))

# Jeton partagé entre l'orchestrateur et le bot Telegram (service interne de confiance).
SERVICE_TOKEN = os.environ.get("FOX_SERVICE_TOKEN", "").strip()
COOKIE_NAME = "fox_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"  # à activer derrière HTTPS (VPS)
# À activer derrière un reverse proxy de confiance (Caddy, nginx) : adresse IP, schéma et hôte réels.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"
MAX_INPUT_CHARS = 4000

PURGE_INTERVAL_SECONDS = 3600
_last_purge = 0.0

app = Flask(__name__)
if TRUST_PROXY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

AUTH_STATUS = {
    "invalid_credentials": 401,
    "too_many_attempts": 429,
    "invitation_invalid": 400,
    "username_taken": 409,
    "username_invalid": 400,
    "password_weak": 400,
    "account_exists": 409,
    "registration_closed": 403,
}


def body():
    return request.get_json(silent=True) or {}


def maybe_purge():
    """Supprime l'historique trop ancien, au plus une fois par heure et par processus."""
    global _last_purge
    now = time.monotonic()
    if _last_purge and now - _last_purge < PURGE_INTERVAL_SECONDS:
        return
    _last_purge = now
    try:
        result = db.purge_history()
    except Exception:
        log.exception("echec de la purge de l'historique")
        return
    if result["chats"] or result["private"] or result["tasks"] or result.get("events"):
        log.info(
            "Purge : %s conversation(s), %s contexte(s) privé(s), %s tâche(s) close(s), %s rendez-vous passé(s)",
            result["chats"], result["private"], result["tasks"], result.get("events", 0),
        )


def user_limits(role):
    if role == accounts.ROLE_OWNER:
        return OWNER_MAX_CALLS_PER_DAY, OWNER_MAX_TOKENS_PER_DAY
    return TESTER_MAX_CALLS_PER_DAY, TESTER_MAX_TOKENS_PER_DAY


@app.before_request
def require_json_for_post():
    # Un corps JSON déclenche une vérification préalable (preflight) des navigateurs entre
    # origines différentes : un formulaire d'un autre site ne peut pas imiter ces requêtes.
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.is_json:
        return jsonify(error="Content-Type application/json requis"), 415


@app.before_request
def protect_web_requests():
    """Origine + jeton CSRF pour les requêtes de navigateur (cookie). Le bot (jeton de service) est exempté."""
    problem = websecurity.check(
        request.method,
        request.path,
        request.headers,
        request.cookies.get(COOKIE_NAME),
        request.host_url,
    )
    if problem:
        code, message = problem
        log.warning("requête web refusée (%s) : %s %s", code, request.method, request.path)
        return jsonify(error=message, code=code), 403


ERROR_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
}


@app.after_request
def uniform_responses(response):
    """Toutes les erreurs JSON ont la forme {"error": message, "code": identifiant stable}."""
    if response.status_code >= 400 and response.is_json:
        payload = response.get_json(silent=True)
        if isinstance(payload, dict) and "error" in payload and "code" not in payload:
            payload["code"] = ERROR_CODES.get(response.status_code, "error")
            response.set_data(jsonify(payload).get_data())
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.errorhandler(HTTPException)
def handle_http_error(exc):
    return jsonify(error=exc.description or exc.name), exc.code


@app.teardown_request
def forget_user_zone(exc=None):
    tools.reset_user_zone()  # le fuseau d'un utilisateur ne doit jamais servir à la requête suivante du même processus


@app.errorhandler(accounts.AuthError)
def handle_auth_error(exc):
    return jsonify(error=exc.message, code=exc.code), AUTH_STATUS.get(exc.code, 400)


def service_token_valid(token):
    return bool(SERVICE_TOKEN) and token is not None and hmac.compare_digest(token, SERVICE_TOKEN)


def authenticate(data):
    """Retourne (utilisateur, erreur). L'identité ne vient JAMAIS d'un user_id du client.

    - interface web : cookie de session ;
    - bot Telegram : jeton de service + identifiant Telegram, résolu en compte actif.
    """
    service_token = request.headers.get("X-Fox-Service-Token")
    if service_token is not None:
        if not service_token_valid(service_token):
            db.audit(None, "denied", None, {"reason": "jeton de service invalide"})
            return None, (jsonify(error="non authentifie"), 401)
        user = accounts.user_from_telegram(data.get("telegram_id"))
        if user is None:
            db.audit(None, "denied", None, {"reason": "compte Telegram inconnu ou desactive"})
            return None, (jsonify(error="non autorise"), 403)
        tools.set_user_zone(user.get("timezone"))
        return user, None

    user = accounts.user_from_session(request.cookies.get(COOKIE_NAME))
    if user is None:
        return None, (jsonify(error="non authentifie"), 401)
    tools.set_user_zone(user.get("timezone"))
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
    return open_session(jsonify(user=user, csrf_token=websecurity.csrf_token(token)), token), 201


@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    try:
        token, user = accounts.login(data.get("username"), data.get("password"), request.remote_addr)
    except accounts.AuthError as exc:
        db.audit(None, "login_failed", None, {"code": exc.code})
        raise
    db.audit(user["id"], "login", None, {})
    return open_session(jsonify(user=user, csrf_token=websecurity.csrf_token(token)), token)


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
    return jsonify(user=user, csrf_token=websecurity.csrf_token(request.cookies.get(COOKIE_NAME)))


@app.post("/auth/telegram")
def register_telegram():
    """Inscription d'un testeur depuis Telegram (/start CODE). Réservé au bot (jeton de service)."""
    if not service_token_valid(request.headers.get("X-Fox-Service-Token")):
        db.audit(None, "denied", None, {"reason": "jeton de service invalide"})
        return jsonify(error="non authentifie"), 401
    data = body()
    user = accounts.register_telegram(data.get("code"), data.get("telegram_id"))
    db.audit(user["id"], "registered", None, {"role": user["role"], "via": "telegram"})
    return jsonify(user=user), 201


# --- Messages ----------------------------------------------------------------
def conversation_context(user_id, data):
    """Retourne (privé, identifiant de conversation, erreur).

    Le mode privé l'emporte toujours : on n'enregistre jamais, même si un chat_id est fourni.
    """
    if db.private_active(user_id):
        return True, None, None
    chat_id = data.get("chat_id")
    if chat_id is None:
        return False, db.current_chat_id(user_id), None
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        return False, None, (jsonify(error="chat_id invalide"), 400)
    if not db.chat_belongs_to(user_id, chat_id):
        return False, None, (jsonify(error="conversation introuvable"), 404)
    return False, chat_id, None


def store_exchange(user_id, private, chat_id, user_message, bot_reply):
    """Enregistre l'échange dans la conversation, ou en mémoire temporaire en mode privé."""
    if private:
        db.save_private_exchange(user_id, user_message, bot_reply)
        return None
    return db.save_exchange(user_id, user_message, bot_reply, chat_id)


@app.post("/message")
def message():
    data = body()
    user, error = authenticate(data)
    if error:
        return error
    user_id, role = user["id"], user["role"]

    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="text est requis"), 400
    if len(text) > MAX_INPUT_CHARS:
        return jsonify(error="message trop long"), 413

    maybe_purge()
    private, chat_id, context_error = conversation_context(user_id, data)
    if context_error:
        return context_error

    if agent.is_undo_request(text):
        reply, pending = agent.propose_undo(user_id)
    else:
        user_calls, user_tokens = user_limits(role)
        try:
            db.reserve_quota(user_id, user_calls, user_tokens, MAX_CALLS_PER_DAY, MAX_TOKENS_PER_DAY)
        except db.QuotaExceeded as exc:
            return jsonify(error="plafond journalier atteint", code="quota_exceeded", scope=exc.scope), 429

        spoken_token = agent.SPOKEN.set(data.get("voice") is True)
        try:
            reply, tokens, pending = agent.chat(
                user_id, text, role, chat_id=chat_id, private=private
            )
        except agent.ProviderQuotaExceeded:
            return jsonify(error="capacite du service atteinte", code="capacity_reached"), 503
        except agent.LLMUnavailable:
            return jsonify(error="modele indisponible", code="model_unavailable"), 503
        except errors.APIError as exc:
            log.error(
                "Erreur Gemini non temporaire (code=%s, statut=%s) : %s",
                exc.code, exc.status, exc.message,
            )
            return jsonify(error="erreur modele", code="upstream_error", upstream_status=exc.code), 502
        except Exception:
            log.exception("echec de l'appel au modele")
            return jsonify(error="service indisponible"), 502
        finally:
            agent.SPOKEN.reset(spoken_token)

        if tokens:
            db.add_tokens(user_id, tokens)

    if pending:
        reply = "Action(s) en attente de confirmation :\n" + "\n".join(
            "- " + p["summary"].replace("\n", "\n  ") for p in pending
        )
    elif reply != agent.EMPTY_REPLY:
        reply = formatting.clean_reply(reply) or agent.EMPTY_REPLY
    chat_id = store_exchange(user_id, private, chat_id, text, reply) if reply != agent.EMPTY_REPLY else chat_id

    return jsonify(reply=reply, pending=pending, chat_id=chat_id, private=private)


def decide(action_id, confirm):
    data = body()
    user, error = authenticate(data)
    if error:
        return error

    try:
        uuid.UUID(action_id)
    except ValueError:
        return jsonify(error="identifiant d'action invalide"), 400

    result = agent.resolve_action(user["id"], action_id, confirm, user["role"])
    label = "Confirmation de l'action proposée" if confirm else "Annulation de l'action proposée"
    private = db.private_active(user["id"])
    store_exchange(user["id"], private, None, label, result)
    return jsonify(reply=result)


@app.get("/actions/pending")
def actions_pending():
    """Actions proposées par FOX et pas encore confirmées ni annulées (non expirées), pour les retrouver après un rechargement."""
    user, error = chat_auth()
    if error:
        return error
    return jsonify(actions=db.list_pending(user["id"]))


@app.post("/actions/<action_id>/confirm")
def confirm_action(action_id):
    return decide(action_id, True)


@app.post("/actions/<action_id>/cancel")
def cancel_action(action_id):
    return decide(action_id, False)


# --- Conversations et mode privé -----------------------------------------------
def chat_auth():
    """Authentifie une requête sur les conversations (pour GET : telegram_id en paramètre)."""
    data = {"telegram_id": request.args.get("telegram_id")} if request.method == "GET" else body()
    return authenticate(data)


@app.get("/chats")
def chats_list():
    user, error = chat_auth()
    if error:
        return error
    private = db.private_active(user["id"])
    chats = db.list_chats(user["id"])
    # « current » : la conversation active, seulement si elle contient déjà des messages
    # (après /nouveau, la conversation active est vide : il n'y a rien à reprendre ni à supprimer).
    current = None
    if not private and chats and db.current_chat_id(user["id"]) == chats[0]["id"]:
        current = chats[0]["id"]
    return jsonify(chats=chats, current=current, private=private)


@app.post("/chats")
def chats_new():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(chat_id=db.start_chat(user["id"]), private=False), 201


@app.post("/chats/<int:chat_id>/open")
def chats_open(chat_id):
    user, error = chat_auth()
    if error:
        return error
    if not db.open_chat(user["id"], chat_id):
        return jsonify(error="conversation introuvable"), 404
    return jsonify(chat_id=chat_id, private=False)


@app.get("/chats/<int:chat_id>/messages")
def chats_messages(chat_id):
    """Messages d'une conversation, pour afficher l'historique dans l'interface web."""
    user, error = chat_auth()
    if error:
        return error
    items = db.chat_messages(user["id"], chat_id)
    if items is None:
        return jsonify(error="conversation introuvable"), 404
    zone = tools.tz()
    messages = []
    for item in items:
        at = item["created_at"].astimezone(zone).isoformat(timespec="minutes")
        messages.append({"id": f"{item['id']}u", "role": "user", "text": item["user_message"], "at": at})
        messages.append({"id": f"{item['id']}a", "role": "assistant", "text": item["bot_reply"], "at": at})
    return jsonify(chat_id=chat_id, messages=messages)


@app.delete("/chats/<int:chat_id>")
def chats_delete(chat_id):
    user, error = chat_auth()
    if error:
        return error
    if not db.delete_chat(user["id"], chat_id):
        return jsonify(error="conversation introuvable"), 404
    db.audit(user["id"], "chat_deleted", None, {"chat_id": chat_id})
    return jsonify(status="deleted")


@app.delete("/chats")
def chats_delete_all():
    user, error = chat_auth()
    if error:
        return error
    if body().get("confirm") != "all":
        return jsonify(error='confirmation requise : {"confirm": "all"}'), 400
    count = db.delete_all_chats(user["id"])
    db.audit(user["id"], "history_deleted", None, {"chats": count})
    return jsonify(status="deleted", chats=count)


@app.post("/private")
def private_mode():
    user, error = chat_auth()
    if error:
        return error
    on = body().get("on")
    if not isinstance(on, bool):
        return jsonify(error="on doit être true ou false"), 400
    db.set_private(user["id"], on)
    db.audit(user["id"], "private_on" if on else "private_off", None, {})
    return jsonify(private=on)


# --- Tâches ----------------------------------------------------------------------
def task_error(exc):
    return jsonify(error=str(exc)), 400


@app.get("/tasks")
def tasks_list():
    user, error = chat_auth()
    if error:
        return error
    scope = request.args.get("status", "open")
    if scope == "open":
        statuses = db.OPEN_STATUSES
    elif scope == "all":
        statuses = tasks.STATUSES
    else:
        try:
            statuses = (tasks.clean_status(scope),)
        except tasks.TaskInputError as exc:
            return task_error(exc)
    items = db.list_tasks(user["id"], statuses)
    titles = db.event_titles(user["id"], [t["event_id"] for t in items])
    return jsonify(tasks=[{**tasks.public(t), **({"event_title": titles[t["event_id"]]} if t["event_id"] in titles else {})} for t in items])


@app.post("/tasks")
def tasks_create():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    try:
        if "text" in data:  # saisie rapide : « titre | échéance | priorité »
            title, priority, due, event_id = tasks.parse_quick_linked(data["text"])
        else:
            title = tasks.clean_title(data.get("title"))
            priority = tasks.clean_priority(data.get("priority", "normale"))
            due = tasks.parse_due(data["due"]) if data.get("due") else None
            event_id = tasks.link_target(data.get("event_id"))
        task = db.create_task(user["id"], title, priority, due, event_id=event_id)
    except tasks.TaskInputError as exc:
        return task_error(exc)
    except db.LinkedEventNotFound:
        return jsonify(error="rendez-vous introuvable"), 404
    except db.TaskLimit as exc:
        return jsonify(error=f"limite de {exc.args[0]} tâches ouvertes atteinte"), 409
    db.audit(user["id"], "task_created", None, {"task_id": task["id"], "mode": "direct"})
    return jsonify(task=tasks.public(task)), 201


@app.patch("/tasks/<int:task_id>")
def tasks_update(task_id):
    user, error = chat_auth()
    if error:
        return error
    data = body()
    changes = {}
    try:
        if "title" in data:
            changes["title"] = tasks.clean_title(data["title"])
        if "priority" in data:
            changes["priority"] = tasks.clean_priority(data["priority"])
        if "status" in data:
            changes["status"] = tasks.clean_status(data["status"])
        if "due" in data:
            changes["due_at"] = tasks.parse_due(data["due"]) if data["due"] else None
        if "event_id" in data:
            changes["event_id"] = tasks.link_target(data["event_id"])
        if not changes:
            return jsonify(error="aucune modification demandée"), 400
        task = db.update_task(user["id"], task_id, **changes)
    except tasks.TaskInputError as exc:
        return task_error(exc)
    except db.LinkedEventNotFound:
        return jsonify(error="rendez-vous introuvable"), 404
    except db.TaskLimit as exc:
        return jsonify(error=f"limite de {exc.args[0]} tâches ouvertes atteinte"), 409
    if task is None:
        return jsonify(error="tâche introuvable"), 404
    db.audit(user["id"], "task_updated", None, {"task_id": task_id, "fields": sorted(changes)})
    return jsonify(task=tasks.public(task))


@app.delete("/tasks/<int:task_id>")
def tasks_delete(task_id):
    user, error = chat_auth()
    if error:
        return error
    if not db.delete_task(user["id"], task_id):
        return jsonify(error="tâche introuvable"), 404
    db.audit(user["id"], "task_deleted", None, {"task_id": task_id})
    return jsonify(status="deleted")


# --- Agenda interne (phase B5) ---------------------------------------------------------
def event_payload(event, conflicts):
    return {"event": agenda.public(event), "conflicts": [agenda.public(c) for c in conflicts]}


@app.get("/events")
def events_list():
    user, error = chat_auth()
    if error:
        return error
    now = datetime.now(tools.tz())
    try:
        start, days = agenda.parse_range(request.args.get("range"), now)
    except agenda.AgendaInputError as exc:
        return jsonify(error=str(exc)), 400
    items = db.list_events(user["id"], start, start + timedelta(days=days))
    linked = db.open_tasks_for_events(user["id"], [e["id"] for e in items])
    return jsonify(today=now.date().isoformat(), reminder_minutes=agenda.REMIND_MINUTES, events=[{**agenda.public(e), "tasks": linked.get(e["id"], [])} for e in items])


@app.get("/events/<int:event_id>")
def events_get(event_id):
    user, error = chat_auth()
    if error:
        return error
    event = db.get_event(user["id"], event_id)
    if event is None:
        return jsonify(error="rendez-vous introuvable"), 404
    linked = db.open_tasks_for_events(user["id"], [event_id])
    return jsonify(event={**agenda.public(event), "tasks": linked.get(event_id, [])})


@app.post("/events")
def events_create():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    try:
        if "text" in data:  # « titre | quand | durée | lieu | répétition »
            fields = agenda.parse_event(data["text"])
        else:
            start = agenda.parse_start(data.get("start"))
            rule = until = None
            if str(data.get("repeat") or "").strip():
                rule, until = agenda.parse_repeat(data["repeat"], start)
            if str(data.get("until") or "").strip():
                if rule is None:
                    raise agenda.AgendaInputError("Indique la répétition (repeat) avant sa date de fin (until).")
                until = agenda.parse_day(str(data["until"]).strip().lower(), datetime.now(tools.tz()).date())
                agenda.check_series(start, rule, until)
            fields = {
                "title": agenda.clean_title(data.get("title")), "start": start,
                "end": start + agenda.parse_duration(data.get("duration")),
                "location": agenda.clean_location(data.get("location")), "repeat": rule, "until": until,
            }
        event = db.create_event(user["id"], fields["title"], fields["start"], fields["end"], fields["location"],
                                recurrence=fields["repeat"], until=fields["until"], tz_name=tools.tz_name())
    except agenda.AgendaInputError as exc:
        return jsonify(error=str(exc)), 400
    except db.EventLimit as exc:
        return jsonify(error=f"Limite de {exc.args[0]} rendez-vous à venir atteinte."), 409
    db.audit(user["id"], "event_created", None, {"event_id": event["id"], "repeat": event["recurrence"]})
    conflicts = db.find_conflicts(user["id"], event["start_at"], event["end_at"], exclude_id=event["id"])
    return jsonify(event_payload(event, conflicts)), 201


@app.patch("/events/<int:event_id>")
def events_update(event_id):
    user, error = chat_auth()
    if error:
        return error
    data = body()
    current = db.get_event(user["id"], event_id)
    if current is None:
        return jsonify(error="rendez-vous introuvable"), 404
    try:
        changes = agenda.build_changes(current, data)
    except agenda.AgendaInputError as exc:
        return jsonify(error=str(exc)), 400
    event = db.update_event(user["id"], event_id, tz_name=tools.tz_name(), **changes)
    db.audit(user["id"], "event_updated", None, {"event_id": event_id, "fields": sorted(changes)})
    conflicts = db.find_conflicts(user["id"], event["start_at"], event["end_at"], exclude_id=event_id)
    return jsonify(event_payload(event, conflicts))


@app.delete("/events/<int:event_id>")
def events_delete(event_id):
    """Supprime un rendez-vous ; pour une série, `occurrence` (AAAA-MM-JJ) ne supprime que cette occurrence."""
    user, error = chat_auth()
    if error:
        return error
    occurrence = body().get("occurrence")
    if occurrence:
        try:
            day = datetime.strptime(str(occurrence), "%Y-%m-%d").date()
        except ValueError:
            return jsonify(error="occurrence doit être une date AAAA-MM-JJ"), 400
        if not db.skip_occurrence(user["id"], event_id, day):
            return jsonify(error="occurrence introuvable"), 404
        db.audit(user["id"], "event_updated", None, {"event_id": event_id, "skipped": day.isoformat()})
        return jsonify(status="occurrence_deleted")
    if not db.delete_event(user["id"], event_id):
        return jsonify(error="rendez-vous introuvable"), 404
    db.audit(user["id"], "event_deleted", None, {"event_id": event_id})
    return jsonify(status="deleted")


@app.post("/events/reminders/claim")
def event_reminders_claim():
    """Rappels de rendez-vous dus, pour le bot Telegram uniquement. Chaque rappel n'est rendu qu'une fois."""
    if not service_token_valid(request.headers.get("X-Fox-Service-Token")):
        return jsonify(error="non authentifie"), 401
    reminders = []
    for r in db.claim_due_event_reminders(agenda.REMIND_MINUTES):
        with tools.user_zone(r["timezone"]):
            reminders.append({"telegram_id": r["telegram_id"], **agenda.public(r), "tasks": r.get("tasks", [])})
    return jsonify(reminders=reminders)


# --- Clients et devis (phase C) ----------------------------------------------------
def input_error(exc):
    return jsonify(error=str(exc)), 400


def client_conflict(exc):
    if isinstance(exc, db.DuplicateClient):
        return jsonify(error="Un client porte déjà ce nom."), 409
    return jsonify(error=f"Limite de {exc.args[0]} clients atteinte."), 409


@app.get("/clients")
def clients_list():
    user, error = chat_auth()
    if error:
        return error
    items = db.list_clients(user["id"], (request.args.get("q") or "").strip() or None)
    return jsonify(clients=[commercial.public_client(c) for c in items])


@app.get("/clients/find")
def clients_find():
    user, error = chat_auth()
    if error:
        return error
    name = (request.args.get("q") or "").strip()
    if not name:
        return jsonify(error="Nom du client manquant."), 400
    return jsonify(clients=[commercial.public_client(c) for c in db.find_clients(user["id"], name)])


@app.post("/clients")
def clients_create():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    try:
        if "text" in data:  # « Nom ; téléphone ; e-mail ; adresse »
            fields = commercial.parse_client(data["text"])
        else:
            fields = {"name": commercial.clean_name(data.get("name"))}
            for key, label in commercial.FIELDS:
                fields[key] = commercial.clean_optional(data.get(key), label)
        client = db.create_client(user["id"], **fields)
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    except (db.DuplicateClient, db.ClientLimit) as exc:
        return client_conflict(exc)
    db.audit(user["id"], "client_created", None, {"client_id": client["id"]})
    return jsonify(client=commercial.public_client(client)), 201


@app.get("/clients/<int:client_id>")
def clients_get(client_id):
    user, error = chat_auth()
    if error:
        return error
    client = db.get_client(user["id"], client_id)
    if client is None:
        return jsonify(error="client introuvable"), 404
    return jsonify(client=commercial.public_client(client))


@app.patch("/clients/<int:client_id>")
def clients_update(client_id):
    user, error = chat_auth()
    if error:
        return error
    data = body()
    try:
        if "text" in data:  # « téléphone ; e-mail ; adresse » (vide = inchangé, - = effacer)
            changes = commercial.parse_client_changes(data["text"])
        else:
            changes = {}
            if "name" in data:
                changes["name"] = commercial.clean_name(data["name"])
            for key, label in commercial.FIELDS:
                if key in data:
                    changes[key] = commercial.clean_optional(data[key], label)
            if not changes:
                return jsonify(error="aucune modification demandée"), 400
        client = db.update_client(user["id"], client_id, **changes)
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    except db.DuplicateClient as exc:
        return client_conflict(exc)
    if client is None:
        return jsonify(error="client introuvable"), 404
    db.audit(user["id"], "client_updated", None, {"client_id": client_id, "fields": sorted(changes)})
    return jsonify(client=commercial.public_client(client))


@app.delete("/clients/<int:client_id>")
def clients_delete(client_id):
    user, error = chat_auth()
    if error:
        return error
    if not db.delete_client(user["id"], client_id):
        return jsonify(error="client introuvable"), 404
    db.audit(user["id"], "client_deleted", None, {"client_id": client_id})
    return jsonify(status="deleted")


@app.get("/quotes")
def quotes_list():
    user, error = chat_auth()
    if error:
        return error
    status = request.args.get("status")
    try:
        status = commercial.clean_status(status) if status else None
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    archived = request.args.get("archived") in ("1", "true")
    items = db.list_quotes(user["id"], status, archived=archived)
    return jsonify(quotes=[commercial.public_quote_summary(q) for q in items])


@app.post("/quotes")
def quotes_create():
    user, error = chat_auth()
    if error:
        return error
    try:
        parsed = commercial.parse_quote(body().get("text"))
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    matches = db.find_clients(user["id"], parsed["client"])
    if len(matches) > 1:
        names = ", ".join(c["name"] for c in matches)
        return jsonify(error=f"Plusieurs clients correspondent : {names}. Précise le nom complet."), 409
    client = matches[0] if matches else None
    vat_rate = commercial.DEFAULT_VAT_RATE if parsed["vat_rate"] is None else parsed["vat_rate"]
    try:
        quote = db.create_quote(
            user["id"], client["id"] if client else None, client["name"] if client else parsed["client"],
            parsed["lines"], vat_rate, commercial.DEFAULT_CURRENCY, parsed["notes"],
        )
    except db.QuoteLimit as exc:
        return jsonify(error=f"Limite de {exc.args[0]} devis atteinte."), 409
    db.audit(user["id"], "quote_created", None, {"quote_id": quote["id"]})
    return jsonify(quote=commercial.public_quote(quote), registered=client is not None), 201


@app.get("/quotes/<ref>")
def quotes_get(ref):
    user, error = chat_auth()
    if error:
        return error
    quote = db.get_quote(user["id"], ref)
    if quote is None:
        return jsonify(error="devis introuvable"), 404
    return jsonify(quote=commercial.public_quote(quote))


@app.patch("/quotes/<ref>")
def quotes_update(ref):
    user, error = chat_auth()
    if error:
        return error
    data = body()
    if "archived" in data:  # archivage : le statut du devis n'est pas modifié
        if not isinstance(data["archived"], bool):
            return jsonify(error="archived doit être true ou false"), 400
        quote = db.set_quote_archived(user["id"], ref, data["archived"])
        if quote is None:
            return jsonify(error="devis introuvable"), 404
        db.audit(user["id"], "quote_archived" if data["archived"] else "quote_unarchived", None, {"quote_id": quote["id"]})
        return jsonify(quote=commercial.public_quote(quote))
    try:
        status = commercial.clean_status(data.get("status"))
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    quote = db.set_quote_status(user["id"], ref, status)
    if quote is None:
        return jsonify(error="devis introuvable"), 404
    db.audit(user["id"], "quote_status", None, {"quote_id": quote["id"], "status": status})
    return jsonify(quote=commercial.public_quote(quote))


@app.delete("/quotes/<ref>")
def quotes_delete(ref):
    user, error = chat_auth()
    if error:
        return error
    try:
        quote = db.delete_quote(user["id"], ref)
    except db.QuoteLocked:
        return jsonify(error="Un devis envoyé ou accepté ne peut pas être supprimé. Passe-le d'abord en refusé."), 409
    if quote is None:
        return jsonify(error="devis introuvable"), 404
    db.audit(user["id"], "quote_deleted", None, {"quote_id": quote["id"]})
    return jsonify(status="deleted")


@app.get("/quotes/<ref>/pdf")
def quotes_pdf(ref):
    user, error = chat_auth()
    if error:
        return error
    quote = db.get_quote(user["id"], ref)
    if quote is None:
        return jsonify(error="devis introuvable"), 404
    client = db.get_client(user["id"], quote["client_id"]) if quote["client_id"] else None
    pdf = quote_pdf.render(commercial.public_quote(quote), db.get_business(user["id"]), client)
    db.audit(user["id"], "quote_pdf", None, {"quote_id": quote["id"]})
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{quote["number"]}.pdf"', "X-Quote-Number": quote["number"]})


@app.get("/quotes/<ref>/mail")
def quotes_mail(ref):
    user, error = chat_auth()
    if error:
        return error
    quote = db.get_quote(user["id"], ref)
    if quote is None:
        return jsonify(error="devis introuvable"), 404
    client = db.get_client(user["id"], quote["client_id"]) if quote["client_id"] else None
    return jsonify(commercial.quote_mail(commercial.public_quote(quote), client, db.get_business(user["id"])))


@app.get("/business")
def business_get():
    user, error = chat_auth()
    if error:
        return error
    business = db.get_business(user["id"])
    return jsonify(business=commercial.public_business(business) if business else None)


@app.put("/business")
def business_set():
    user, error = chat_auth()
    if error:
        return error
    try:
        business = db.set_business(user["id"], **commercial.parse_business(body().get("text")))
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    db.audit(user["id"], "business_updated", None, {})
    return jsonify(business=commercial.public_business(business))


@app.delete("/business")
def business_delete():
    user, error = chat_auth()
    if error:
        return error
    if not db.delete_business(user["id"]):
        return jsonify(error="Aucun profil d'entreprise."), 404
    db.audit(user["id"], "business_updated", None, {})
    return jsonify(status="deleted")


@app.post("/reminders/claim")
def reminders_claim():
    """Rappels dus, pour le bot Telegram uniquement (jeton de service). Chaque rappel n'est rendu qu'une fois."""
    if not service_token_valid(request.headers.get("X-Fox-Service-Token")):
        return jsonify(error="non authentifie"), 401
    due = db.claim_due_reminders()
    return jsonify(reminders=[
        {"telegram_id": r["telegram_id"], "id": r["id"], "title": r["title"],
         "due_at": tasks.public({**r, "priority": "normale", "status": "a_faire", "overdue": True,
                                 "created_at": None, "done_at": None})["due_at"]}
        for r in due
    ])


# --- Mémoire ---------------------------------------------------------------------
@app.get("/memory")
def memory_list():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(items=[memory.public(i) for i in db.list_memories(user["id"])], private=db.private_active(user["id"]))


@app.post("/memory")
def memory_add():
    user, error = chat_auth()
    if error:
        return error
    try:
        item = memory.remember(user["id"], body().get("text"))
    except memory.MemoryInputError as exc:
        return jsonify(error=str(exc)), 400
    except db.MemoryLimit as exc:
        return jsonify(error=f"limite de {exc.args[0]} éléments atteinte : oublie-en un avec /memoire"), 409
    db.audit(user["id"], "memory_added", None, {"memory_id": item["id"], "mode": "direct"})
    return jsonify(item=memory.public(item)), 201


@app.delete("/memory/<int:memory_id>")
def memory_delete(memory_id):
    user, error = chat_auth()
    if error:
        return error
    if not db.delete_memory(user["id"], memory_id):
        return jsonify(error="élément introuvable"), 404
    db.audit(user["id"], "memory_deleted", None, {"memory_id": memory_id, "mode": "direct"})
    return jsonify(status="deleted")


@app.delete("/memory")
def memory_clear():
    user, error = chat_auth()
    if error:
        return error
    if body().get("confirm") != "all":
        return jsonify(error='confirmation requise : {"confirm": "all"}'), 400
    count = db.delete_all_memories(user["id"])
    db.audit(user["id"], "memory_cleared", None, {"count": count})
    return jsonify(status="deleted", count=count)


# --- Journal des actions ---------------------------------------------------------
@app.get("/journal")
def journal_list():
    user, error = chat_auth()
    if error:
        return error
    try:
        limit = min(max(int(request.args.get("limit", 15)), 1), 50)
    except ValueError:
        return jsonify(error="limit invalide"), 400
    return jsonify(entries=journal.entries(user["id"], limit))


# --- Briefing du matin -----------------------------------------------------------
@app.get("/briefing")
def briefing_get():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(**db.get_briefing(user["id"]))


@app.post("/briefing")
def briefing_set():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        return jsonify(error="enabled doit être true ou false"), 400
    if enabled is None and data.get("time") is None:
        return jsonify(error="rien à modifier : enabled et/ou time"), 400
    try:
        settings = briefing.configure(user["id"], enabled, data.get("time"))
    except briefing.BriefingInputError as exc:
        return jsonify(error=str(exc)), 400
    db.audit(user["id"], "briefing_set", None, {"enabled": settings["enabled"], "time": settings["time"]})
    return jsonify(**settings)


@app.get("/briefing/preview")
def briefing_preview():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(text=briefing.build(user["id"], user["role"]))


@app.post("/briefings/claim")
def briefings_claim():
    """Briefings dus, pour le bot Telegram uniquement. Chaque briefing n'est rendu qu'une fois par jour."""
    if not service_token_valid(request.headers.get("X-Fox-Service-Token")):
        return jsonify(error="non authentifie"), 401
    due = db.claim_due_briefings(datetime.now(tools.TZ), tools.TZ_NAME)
    items = []
    for entry in due:
        try:
            with tools.user_zone(entry["timezone"]):
                items.append({"telegram_id": entry["telegram_id"],
                              "text": briefing.build(entry["user_id"], entry["role"], datetime.now(tools.tz()))})
        except Exception:
            log.exception("echec de la construction du briefing")
    return jsonify(briefings=items)


# --- Fuseau horaire ---------------------------------------------------------------
def zone_payload(user):
    name = user.get("timezone")
    return {"timezone": tools.tz_name(), "default": tools.TZ_NAME, "custom": bool(name),
            "now": datetime.now(tools.tz()).isoformat(timespec="minutes")}


@app.get("/timezone")
def timezone_get():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(zone_payload(user))


@app.post("/timezone")
def timezone_set():
    user, error = chat_auth()
    if error:
        return error
    value = body().get("timezone")
    if not isinstance(value, str) or not value.strip():
        return jsonify(error="timezone est requis (nom de fuseau ou « defaut »)"), 400
    if timezones.fold(value) in ("defaut", "default", "auto"):
        name = None
    else:
        try:
            name = timezones.resolve(value)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
    db.set_user_timezone(user["id"], name)
    db.audit(user["id"], "timezone_set", None, {"timezone": name})
    user = {**user, "timezone": name}
    tools.set_user_zone(name)
    return jsonify(zone_payload(user))


# --- Chantiers et comptabilité (phase F) ------------------------------------------------------
def _today():
    return datetime.now(tools.tz()).date()


def _project_json(project):
    return finance.public_project(project)


@app.get("/projects")
def projects_list():
    user, error = chat_auth()
    if error:
        return error
    try:
        status = finance.clean_status(request.args["status"]) if request.args.get("status") else None
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    archived = request.args.get("archived") in ("1", "true")
    items = finance_store.list_projects(user["id"], status, archived)
    return jsonify(projects=[_project_json(p) for p in items])


@app.post("/projects")
def projects_create():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    try:
        fields = finance_store.new_project_fields(
            user["id"], data.get("name"), data.get("client"), data.get("quote"), data.get("budget"),
            data.get("start_date"), data.get("end_date"), data.get("notes"), _today(),
        )
        project = finance_store.create_project(user["id"], **fields)
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    except finance_store.DuplicateProject:
        return jsonify(error="Un chantier porte déjà ce nom."), 409
    except finance_store.ProjectLimit as exc:
        return jsonify(error=f"Limite de {exc.args[0]} chantiers atteinte."), 409
    db.audit(user["id"], "project_created", None, {"project_id": project["id"]})
    return jsonify(project=_project_json(project)), 201


@app.get("/projects/<int:project_id>")
def projects_get(project_id):
    user, error = chat_auth()
    if error:
        return error
    project = finance_store.get_project(user["id"], project_id)
    if project is None:
        return jsonify(error="chantier introuvable"), 404
    return jsonify(project=_project_json(project))


@app.patch("/projects/<int:project_id>")
def projects_update(project_id):
    user, error = chat_auth()
    if error:
        return error
    data = body()
    today = _today()
    changes = {}
    try:
        if "archived" in data:
            if not isinstance(data["archived"], bool):
                return jsonify(error="archived doit être true ou false"), 400
            changes["archived"] = data["archived"]
        if "status" in data:
            changes["status"] = finance.clean_status(data["status"])
        if "budget" in data:
            changes["budget"] = finance.parse_budget(data["budget"])
        for key in ("start_date", "end_date"):
            if key in data:
                changes[key] = finance.parse_optional_date(data[key], today)
        if "notes" in data:
            changes["notes"] = finance.clean_text(data["notes"], "La note", finance.NOTES_MAX)
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    if not changes:
        return jsonify(error="Rien à modifier."), 400
    project = finance_store.update_project(user["id"], project_id, changes)
    if project is None:
        return jsonify(error="chantier introuvable"), 404
    event = "project_archived" if changes.get("archived") is True else "project_unarchived" if changes.get("archived") is False else "project_updated"
    db.audit(user["id"], event, None, {"project_id": project_id})
    return jsonify(project=_project_json(project))


def _int_arg(name):
    value = request.args.get(name)
    return int(value) if value and value.isdigit() else None


@app.get("/ledger")
def ledger_list():
    user, error = chat_auth()
    if error:
        return error
    try:
        if request.args.get("month") == "all":  # toutes les écritures (fiche d'un chantier)
            start, end, month = None, None, "all"
        else:
            start, end, month = finance.parse_month(request.args.get("month"), _today())
        kind = finance.clean_kind(request.args["kind"]) if request.args.get("kind") else None
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    entries = finance_store.list_entries(user["id"], start, end, kind, _int_arg("project"))
    return jsonify(month=month, entries=[finance.public_entry(e) for e in entries])


@app.get("/ledger/summary")
def ledger_summary():
    user, error = chat_auth()
    if error:
        return error
    try:
        start, end, month = finance.parse_month(request.args.get("month"), _today())
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    summary = finance_store.summary(user["id"], start, end, _int_arg("project"))
    return jsonify(summary=finance.public_summary(summary, month))


@app.post("/ledger")
def ledger_create():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    project_id = data.get("project")
    if project_id is not None and (not isinstance(project_id, int) or isinstance(project_id, bool)):
        return jsonify(error="project doit être le numéro du chantier"), 400
    try:
        entry = finance_store.add_entry(
            user["id"], finance.clean_kind(data.get("kind")), finance.parse_entry_amount(data.get("amount")),
            commercial.DEFAULT_CURRENCY, finance.parse_date(data.get("date"), _today()),
            finance.clean_text(data.get("description"), "La description", finance.DESC_MAX, required=True),
            finance.clean_text(data.get("category"), "La catégorie", finance.CATEGORY_MAX), project_id,
        )
    except commercial.CommercialInputError as exc:
        return input_error(exc)
    except LookupError:
        return jsonify(error="chantier introuvable"), 404
    except finance_store.LedgerLimit as exc:
        return jsonify(error=f"Limite de {exc.args[0]} écritures atteinte."), 409
    db.audit(user["id"], "ledger_entry_added", None, {"entry_id": entry["id"], "kind": entry["kind"]})
    return jsonify(entry=finance.public_entry(entry)), 201


@app.delete("/ledger/<int:entry_id>")
def ledger_delete(entry_id):
    user, error = chat_auth()
    if error:
        return error
    if not finance_store.delete_entry(user["id"], entry_id):
        return jsonify(error="écriture introuvable"), 404
    db.audit(user["id"], "ledger_entry_deleted", None, {"entry_id": entry_id})
    return jsonify(status="deleted")


# --- Notifications push (rappels hors onglet) -------------------------------------------------
@app.get("/push/config")
def push_config():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(
        enabled=webpush.configured(), public_key=os.environ.get("VAPID_PUBLIC_KEY") if webpush.configured() else None,
        devices=push_store.count(user["id"]), missing=webpush.missing(),
    )


@app.post("/push/subscribe")
def push_subscribe():
    user, error = chat_auth()
    if error:
        return error
    if not webpush.configured():
        return jsonify(error="Les notifications ne sont pas configurées sur ce serveur."), 409
    data = body()
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    endpoint, p256dh, auth = data.get("endpoint"), keys.get("p256dh"), keys.get("auth")
    if not all(isinstance(v, str) and 0 < len(v) <= 2000 for v in (endpoint, p256dh, auth)):
        return jsonify(error="Abonnement incomplet."), 400
    if not webpush.endpoint_allowed(endpoint):
        return jsonify(error="Ce service de notification n'est pas accepté."), 400
    try:
        if len(webpush.b64d(p256dh)) != 65 or len(webpush.b64d(auth)) != 16:
            raise ValueError
    except Exception:  # noqa: BLE001 - toute clé illisible ou de mauvaise taille
        return jsonify(error="Clés d'abonnement invalides."), 400
    try:
        push_store.add(user["id"], endpoint, p256dh, auth)
    except push_store.TooManyDevices as exc:
        return jsonify(error=f"Limite de {exc.args[0]} appareils atteinte : désactivez-en un d'abord."), 409
    db.audit(user["id"], "push_enabled", None, None)
    return jsonify(status="subscribed", devices=push_store.count(user["id"])), 201


@app.post("/push/unsubscribe")
def push_unsubscribe():
    user, error = chat_auth()
    if error:
        return error
    endpoint = body().get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return jsonify(error="Appareil inconnu."), 400
    if push_store.remove(user["id"], endpoint):
        db.audit(user["id"], "push_disabled", None, None)
    return jsonify(status="unsubscribed", devices=push_store.count(user["id"]))


@app.post("/push/test")
def push_test():
    user, error = chat_auth()
    if error:
        return error
    if not webpush.configured():
        return jsonify(error="Les notifications ne sont pas configurées sur ce serveur."), 409
    if push_store.count(user["id"]) == 0:
        return jsonify(error="Aucun appareil enregistré : activez d'abord les notifications."), 404
    sent = push_delivery.deliver(user["id"], {"title": "FOX", "body": "Les notifications fonctionnent.", "url": "/reglages", "tag": "test"})
    return jsonify(sent=sent)


# --- E-mails ---------------------------------------------------------------------
def mail_endpoint(field, operation, audit_event):
    """Opération sur un texte collé (rédiger, améliorer, résumer). Quotas et erreurs comme /message."""
    data = body()
    user, error = authenticate(data)
    if error:
        return None, error
    text = (data.get(field) or "").strip()
    if not text:
        return None, (jsonify(error=f"{field} est requis"), 400)
    if len(text) > MAX_INPUT_CHARS:
        return None, (jsonify(error="texte trop long"), 413)
    user_calls, user_tokens = user_limits(user["role"])
    try:
        db.reserve_quota(user["id"], user_calls, user_tokens, MAX_CALLS_PER_DAY, MAX_TOKENS_PER_DAY)
    except db.QuotaExceeded as exc:
        return None, (jsonify(error="plafond journalier atteint", code="quota_exceeded", scope=exc.scope), 429)
    try:
        result, tokens = operation(user, text)
    except mail.MailInputError as exc:
        return None, (jsonify(error=str(exc)), 400)
    except agent.ProviderQuotaExceeded:
        return None, (jsonify(error="capacite du service atteinte", code="capacity_reached"), 503)
    except agent.LLMUnavailable:
        return None, (jsonify(error="modele indisponible", code="model_unavailable"), 503)
    except errors.APIError as exc:
        log.error("Erreur Gemini non temporaire (code=%s, statut=%s) : %s", exc.code, exc.status, exc.message)
        return None, (jsonify(error="erreur modele", code="upstream_error", upstream_status=exc.code), 502)
    except Exception:
        log.exception("echec de l'operation e-mail")
        return None, (jsonify(error="service indisponible"), 502)
    if tokens:
        db.add_tokens(user["id"], tokens)
    db.audit(user["id"], audit_event, None, {})
    return (user, result), None


@app.post("/mail/draft")
def mail_draft():
    done, error = mail_endpoint("instruction", lambda user, text: mail.draft(user["role"], text), "mail_draft")
    if error:
        return error
    return jsonify(**done[1])


@app.post("/mail/improve")
def mail_improve():
    done, error = mail_endpoint("text", lambda user, text: mail.improve(user["role"], text), "mail_improve")
    if error:
        return error
    return jsonify(text=done[1])


@app.post("/mail/summary")
def mail_summary():
    done, error = mail_endpoint("text", lambda user, text: mail.summarize(user["role"], text), "mail_summary")
    if error:
        return error
    user, result = done
    return jsonify(summary=result["summary"], pending=mail.propose_tasks(user["id"], result["tasks"]))


# --- Voix de FOX (conversation orale) ---------------------------------------------------------
def voice_limit(role):
    return OWNER_VOICE_CHARS_PER_DAY if role == accounts.ROLE_OWNER else TESTER_VOICE_CHARS_PER_DAY


@app.get("/voice/config")
def voice_config():
    user, error = chat_auth()
    if error:
        return error
    return jsonify(
        enabled=speech.enabled(), voice=speech.voice_name(),
        daily_limit=voice_limit(user["role"]), used=db.voice_used(user["id"]),
    )


@app.post("/voice/speak")
def voice_speak():
    """Lit un texte à voix haute : renvoie un fichier son (WAV). Rien n'est enregistré, même en mode privé."""
    data = body()
    user, error = authenticate(data)
    if error:
        return error
    if not speech.enabled():
        return jsonify(error="la voix est désactivée", code="voice_disabled"), 409
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify(error="text est requis"), 400
    spoken = speech.clean_for_speech(text)
    if not spoken:
        return jsonify(error="rien à lire"), 400
    if not db.reserve_voice_chars(user["id"], len(spoken), voice_limit(user["role"])):
        return jsonify(error="plafond de voix atteint pour aujourd'hui", code="voice_quota"), 429
    try:
        audio = speech.synthesize(spoken)
    except speech.SpeechError as exc:
        db.release_voice_chars(user["id"], len(spoken))
        log.warning("synthèse vocale échouée (%s) : %s", exc.code, exc.message)
        if exc.code == "busy":
            return jsonify(error="la voix est saturée, réessayez dans un instant", code="voice_busy"), 429
        return jsonify(error="la voix est indisponible pour le moment", code="voice_unavailable"), 502
    return Response(audio, mimetype="audio/wav", headers={"Cache-Control": "no-store"})
