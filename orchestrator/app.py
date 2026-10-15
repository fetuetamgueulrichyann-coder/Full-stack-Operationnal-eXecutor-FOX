import logging
import os
import uuid

from flask import Flask, jsonify, request

import agent
import db
from google.genai import errors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")

MAX_CALLS_PER_DAY = int(os.environ.get("MAX_CALLS_PER_DAY", "200"))
MAX_TOKENS_PER_DAY = int(os.environ.get("MAX_TOKENS_PER_DAY", "500000"))
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "").strip()
MAX_INPUT_CHARS = 4000

app = Flask(__name__)


def read_user_id(data):
    """Retourne (user_id, erreur). Refuse tout identifiant autre que le propriétaire."""
    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return None, (jsonify(error="user_id invalide"), 400)

    if ALLOWED_USER_ID and str(user_id) != ALLOWED_USER_ID:
        db.audit(user_id, "denied", None, {"reason": "utilisateur non autorise"})
        return None, (jsonify(error="non autorise"), 403)
    return user_id, None


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/message")
def message():
    data = request.get_json(silent=True) or {}
    user_id, error = read_user_id(data)
    if error:
        return error

    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="text est requis"), 400
    if len(text) > MAX_INPUT_CHARS:
        return jsonify(error="message trop long"), 413

    if agent.is_undo_request(text):
        reply, pending = agent.propose_undo(user_id)
    else:
        if not db.reserve_call(MAX_CALLS_PER_DAY, MAX_TOKENS_PER_DAY):
            return jsonify(error="plafond journalier atteint"), 429

        try:
            reply, tokens, pending = agent.chat(user_id, text)
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
            db.add_tokens(tokens)

    if pending:
        reply = "Action(s) en attente de confirmation :\n" + "\n".join(
            f"- {p['summary']}" for p in pending
        )
    if reply != agent.EMPTY_REPLY:
        db.save_exchange(user_id, text, reply)

    return jsonify(reply=reply, pending=pending)


def decide(action_id, confirm):
    data = request.get_json(silent=True) or {}
    user_id, error = read_user_id(data)
    if error:
        return error

    try:
        uuid.UUID(action_id)
    except ValueError:
        return jsonify(error="identifiant d'action invalide"), 400

    result = agent.resolve_action(user_id, action_id, confirm)
    label = "Confirmation de l'action proposée" if confirm else "Annulation de l'action proposée"
    db.save_exchange(user_id, label, result)
    return jsonify(reply=result)


@app.post("/actions/<action_id>/confirm")
def confirm_action(action_id):
    return decide(action_id, True)


@app.post("/actions/<action_id>/cancel")
def cancel_action(action_id):
    return decide(action_id, False)
