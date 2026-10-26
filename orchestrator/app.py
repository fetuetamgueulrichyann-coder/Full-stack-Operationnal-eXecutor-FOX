import hmac
import logging
import os
import time
import uuid
from datetime import datetime

from flask import Flask, jsonify, request
from google.genai import errors

import accounts
import agent
import db
import briefing
import formatting
import journal
import memory
import tasks

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

PURGE_INTERVAL_SECONDS = 3600
_last_purge = 0.0

app = Flask(__name__)

AUTH_STATUS = {
    "invalid_credentials": 401,
    "too_many_attempts": 429,
    "invitation_invalid": 400,
    "username_taken": 409,
    "username_invalid": 400,
    "password_weak": 400,
    "account_exists": 409,
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
    if result["chats"] or result["private"] or result["tasks"]:
        log.info(
            "Purge : %s conversation(s), %s contexte(s) privé(s), %s tâche(s) close(s)",
            result["chats"], result["private"], result["tasks"],
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
            return jsonify(error="plafond journalier atteint", scope=exc.scope), 429

        try:
            reply, tokens, pending = agent.chat(
                user_id, text, role, chat_id=chat_id, private=private
            )
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
    return jsonify(tasks=[tasks.public(t) for t in items])


@app.post("/tasks")
def tasks_create():
    user, error = chat_auth()
    if error:
        return error
    data = body()
    try:
        if "text" in data:  # saisie rapide : « titre | échéance | priorité »
            title, priority, due = tasks.parse_quick(data["text"])
        else:
            title = tasks.clean_title(data.get("title"))
            priority = tasks.clean_priority(data.get("priority", "normale"))
            due = tasks.parse_due(data["due"]) if data.get("due") else None
        task = db.create_task(user["id"], title, priority, due)
    except tasks.TaskInputError as exc:
        return task_error(exc)
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
        if not changes:
            return jsonify(error="aucune modification demandée"), 400
        task = db.update_task(user["id"], task_id, **changes)
    except tasks.TaskInputError as exc:
        return task_error(exc)
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
    now = datetime.now(tasks.TZ)
    due = db.claim_due_briefings(now.date(), now.time().replace(microsecond=0))
    items = []
    for entry in due:
        try:
            items.append({"telegram_id": entry["telegram_id"], "text": briefing.build(entry["user_id"], entry["role"], now)})
        except Exception:
            log.exception("echec de la construction du briefing")
    return jsonify(briefings=items)
