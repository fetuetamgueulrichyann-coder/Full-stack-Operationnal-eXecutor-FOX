import asyncio
import logging
import os

import httpx
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from botlogic import (
    UnknownUsers,
    action_message,
    briefing_command,
    briefing_status,
    history_buttons,
    journal_message,
    memory_buttons,
    memory_message,
    reminder_message,
    start_code,
    task_buttons,
    tasks_message,
    telegram_messages,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("telegram_bot")

TOKEN = os.environ["TELEGRAM_TOKEN"]
ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]
# Jeton partagé avec l'orchestrateur : prouve que la requête vient bien de ce bot.
# Qui a le droit de parler à FOX est décidé par l'orchestrateur (comptes actifs), pas par le bot.
SERVICE_TOKEN = os.environ["FOX_SERVICE_TOKEN"]
REMINDER_POLL_SECONDS = max(5, int(os.environ.get("REMINDER_POLL_SECONDS", "30")))
UNAVAILABLE = "Service momentanément indisponible."
NO_ACCOUNT = (
    "Tu n'as pas encore de compte FOX actif. "
    "Si tu as un code d'invitation, envoie : /start TON_CODE"
)
HELP = (
    "Commandes :\n"
    "/nouveau : démarrer une nouvelle conversation\n"
    "/historique : reprendre une conversation précédente\n"
    "/effacer : supprimer une conversation ou tout l'historique\n"
    "/prive : mode « ne pas enregistrer » (rien n'est conservé)\n"
    "/taches : voir tes tâches en cours\n"
    "/tache titre | échéance | priorité : ajouter une tâche (échéance et priorité facultatives)\n"
    "/memoire : voir et supprimer ce que FOX retient sur toi\n"
    "/retiens phrase : demander à FOX de retenir une information\n"
    "/journal : voir ce que FOX a proposé et fait\n"
    "/briefing : briefing du matin (on, off, une heure comme 07:30, ou maintenant)\n"
    "/annuler : annuler la dernière action effectuée"
)
COMMANDS = [
    BotCommand("nouveau", "Nouvelle conversation"),
    BotCommand("historique", "Reprendre une conversation"),
    BotCommand("effacer", "Supprimer l'historique"),
    BotCommand("prive", "Mode ne pas enregistrer"),
    BotCommand("taches", "Voir mes tâches"),
    BotCommand("tache", "Ajouter une tâche"),
    BotCommand("memoire", "Ce que FOX retient"),
    BotCommand("retiens", "Faire retenir une information"),
    BotCommand("journal", "Journal des actions"),
    BotCommand("briefing", "Briefing du matin"),
    BotCommand("annuler", "Annuler la dernière action"),
    BotCommand("aide", "Aide"),
]


def decision_keyboard(action_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmer", callback_data=f"ok:{action_id}"),
                InlineKeyboardButton("❌ Annuler", callback_data=f"no:{action_id}"),
            ]
        ]
    )


def keyboard(rows):
    """rows : liste de lignes, chaque ligne = liste de (texte, callback_data)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
    )


async def send_markdown(message, text, reply_markup=None):
    """Envoie un texte Markdown en HTML Telegram ; texte brut si Telegram refuse le HTML."""
    for rendered, plain in telegram_messages(text):
        if rendered is not None:
            try:
                await message.reply_text(rendered, parse_mode=ParseMode.HTML)
                continue
            except BadRequest:
                log.warning("Telegram a refusé le HTML : envoi en texte brut")
        await message.reply_text(plain)


async def send_action(message, summary, action_id):
    """Action en attente, avec ses boutons Confirmer / Annuler."""
    rendered, plain = action_message(summary)
    try:
        await message.reply_text(
            rendered, parse_mode=ParseMode.HTML, reply_markup=decision_keyboard(action_id)
        )
    except BadRequest:
        log.warning("Telegram a refusé le HTML : envoi en texte brut")
        await message.reply_text(plain, reply_markup=decision_keyboard(action_id))


async def call(context, method, path, telegram_id, payload=None):
    """Appelle l'orchestrateur pour le compte de cet identifiant Telegram. Retourne la réponse."""
    http: httpx.AsyncClient = context.application.bot_data["http"]
    if method == "GET":
        return await http.get(f"{ORCHESTRATOR_URL}{path}", params={"telegram_id": telegram_id})
    return await http.request(
        method, f"{ORCHESTRATOR_URL}{path}", json={"telegram_id": telegram_id, **(payload or {})}
    )


def unknown(context) -> UnknownUsers:
    return context.application.bot_data["unknown"]


async def refuse_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    unknown(context).mark(update.effective_user.id)
    await update.effective_message.reply_text(NO_ACCOUNT)


async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    message = update.effective_message
    user_id = update.effective_user.id

    if unknown(context).is_quiet(user_id):
        await message.reply_text(NO_ACCOUNT)
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    try:
        response = await call(context, "POST", "/message", user_id, {"text": text})
        if response.status_code == 429:
            await message.reply_text("Limite journalière atteinte.")
            return
        if response.status_code == 403:
            await refuse_unknown(update, context)
            return
        if response.status_code == 401:
            log.error("Accès refusé par l'orchestrateur : FOX_SERVICE_TOKEN incorrect ?")
            await message.reply_text("Erreur de configuration du service. Consulte les journaux de FOX.")
            return
        if response.status_code == 413:
            await message.reply_text("Message trop long.")
            return
        if response.status_code == 503:
            await message.reply_text(
                "Le modèle est très sollicité ou son quota est atteint. Réessaie dans un instant."
            )
            return
        if response.status_code == 502:
            await message.reply_text(
                "Erreur de configuration ou d'accès au modèle. Consulte les journaux de FOX."
            )
            return
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        log.exception("echec de la requete vers l'orchestrateur")
        await message.reply_text(UNAVAILABLE)
        return

    pending = data.get("pending") or []
    if pending:
        for action in pending:
            await send_action(message, action["summary"], action["id"])
        return

    await send_markdown(message, data["reply"])


async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await forward(update, context, update.effective_message.text)


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await forward(update, context, "annule ça")


async def simple_call(update, context, method, path, payload=None):
    """Appel d'une commande de gestion. Retourne la réponse, ou None après avoir répondu."""
    message = update.effective_message
    user_id = update.effective_user.id
    if unknown(context).is_quiet(user_id):
        await message.reply_text(NO_ACCOUNT)
        return None
    try:
        response = await call(context, method, path, user_id, payload)
    except httpx.HTTPError:
        log.exception("echec de la requete vers l'orchestrateur")
        await message.reply_text(UNAVAILABLE)
        return None
    if response.status_code == 403:
        await refuse_unknown(update, context)
        return None
    if response.status_code >= 400:
        log.error("Réponse inattendue de l'orchestrateur (%s) pour %s", response.status_code, path)
        await message.reply_text(UNAVAILABLE)
        return None
    return response


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = update.effective_user.id
    code = start_code(context.args)

    if code:
        try:
            response = await call(context, "POST", "/auth/telegram", user_id, {"code": code})
        except httpx.HTTPError:
            log.exception("echec de l'inscription")
            await message.reply_text(UNAVAILABLE)
            return
        if response.status_code == 201:
            unknown(context).forget(user_id)
            await message.reply_text(
                "Bienvenue sur FOX ! Ton compte testeur est créé. Écris-moi directement.\n\n" + HELP
            )
        elif response.status_code == 409:
            unknown(context).forget(user_id)
            await message.reply_text("Ce compte Telegram est déjà inscrit. Écris-moi directement.")
        elif response.status_code == 429:
            await message.reply_text("Trop de tentatives. Réessaie dans quelques minutes.")
        elif response.status_code == 400:
            await message.reply_text("Code d'invitation invalide ou expiré.")
        else:
            log.error("Inscription : réponse inattendue (%s)", response.status_code)
            await message.reply_text(UNAVAILABLE)
        return

    response = await simple_call(update, context, "GET", "/chats")
    if response is not None:
        await message.reply_text("Bonjour, je suis FOX.\n\n" + HELP)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await simple_call(update, context, "GET", "/chats")
    if response is not None:
        await update.effective_message.reply_text(HELP)


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await simple_call(update, context, "POST", "/chats")
    if response is not None:
        await update.effective_message.reply_text("Nouvelle conversation démarrée.")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await simple_call(update, context, "GET", "/chats")
    if response is None:
        return
    data = response.json()
    if not data["chats"]:
        await update.effective_message.reply_text("Aucune conversation enregistrée.")
        return
    rows = [[button] for button in history_buttons(data["chats"], data.get("current"))]
    note = "\n(Mode privé actif : rien n'est enregistré.)" if data.get("private") else ""
    await update.effective_message.reply_text(
        "Choisis la conversation à reprendre :" + note, reply_markup=keyboard(rows)
    )


async def erase_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await simple_call(update, context, "GET", "/chats")
    if response is None:
        return
    data = response.json()
    if not data["chats"]:
        await update.effective_message.reply_text("Aucun historique à supprimer.")
        return
    rows = []
    if data.get("current") is not None:
        rows.append([("🗑 Cette conversation", f"del:{data['current']}")])
    rows.append([("🗑 Tout l'historique", "delall")])
    rows.append([("Annuler", "keep")])
    await update.effective_message.reply_text(
        "Que veux-tu supprimer ? La suppression est définitive.", reply_markup=keyboard(rows)
    )


async def private_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    turn_off = bool(context.args) and context.args[0].lower() in ("off", "non", "stop")
    response = await simple_call(update, context, "POST", "/private", {"on": not turn_off})
    if response is None:
        return
    if turn_off:
        await update.effective_message.reply_text("Mode privé désactivé.")
    else:
        await update.effective_message.reply_text(
            "Mode privé activé : cette conversation n'est pas enregistrée. "
            "FOX ne garde un court contexte en mémoire que quelques minutes pour te suivre.\n"
            "/nouveau ou /prive off pour en sortir."
        )


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await simple_call(update, context, "GET", "/tasks")
    if response is None:
        return
    tasks = response.json()["tasks"]
    text = tasks_message(tasks)
    markup = keyboard(task_buttons(tasks)) if tasks else None
    parts = telegram_messages(text)
    for index, (rendered, plain) in enumerate(parts):
        last = index == len(parts) - 1
        try:
            await update.effective_message.reply_text(
                rendered or plain, parse_mode=ParseMode.HTML if rendered else None,
                reply_markup=markup if last else None,
            )
        except BadRequest:
            await update.effective_message.reply_text(plain, reply_markup=markup if last else None)


async def add_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = " ".join(context.args or []).strip()
    if not text:
        await message.reply_text("Écris la tâche après la commande, par exemple :\n/tache Appeler le fournisseur | 25/10 | haute")
        return
    user_id = update.effective_user.id
    if unknown(context).is_quiet(user_id):
        await message.reply_text(NO_ACCOUNT)
        return
    try:
        response = await call(context, "POST", "/tasks", user_id, {"text": text})
    except httpx.HTTPError:
        log.exception("echec de la requete vers l'orchestrateur")
        await message.reply_text(UNAVAILABLE)
        return
    if response.status_code == 403:
        await refuse_unknown(update, context)
    elif response.status_code in (400, 409):
        await message.reply_text(response.json().get("error", "Tâche refusée."))
    elif response.status_code >= 400:
        log.error("Réponse inattendue de l'orchestrateur (%s) pour /tasks", response.status_code)
        await message.reply_text(UNAVAILABLE)
    else:
        task = response.json()["task"]
        await message.reply_text("Tâche ajoutée. /taches pour les voir.")


async def on_task_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Boutons Terminer / Annuler de la liste des tâches."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action, _, value = (query.data or "").partition(":")
    status = "terminee" if action == "tdone" else "annulee"
    if unknown(context).is_quiet(user_id):
        await query.message.reply_text(NO_ACCOUNT)
        return
    try:
        response = await call(context, "PATCH", f"/tasks/{int(value)}", user_id, {"status": status})
    except httpx.HTTPError:
        log.exception("echec de la mise à jour de la tâche")
        await query.message.reply_text(UNAVAILABLE)
        return
    if response.status_code == 403:
        unknown(context).mark(user_id)
        await query.message.reply_text(NO_ACCOUNT)
    elif response.status_code == 404:
        await query.message.reply_text(f"La tâche #{int(value)} n'existe plus.")
    elif response.status_code >= 400:
        await query.message.reply_text(UNAVAILABLE)
    else:
        verb = "terminée" if status == "terminee" else "annulée"
        await query.message.reply_text(f"Tâche #{int(value)} {verb}. /taches pour la liste à jour.")


async def show_list(target, text, rows, edit=False):
    """Affiche un texte Markdown avec boutons, en HTML puis en texte brut si Telegram refuse."""
    rendered, plain = telegram_messages(text)[0]
    markup = keyboard(rows) if rows else None
    send = target.edit_message_text if edit else target.reply_text
    try:
        if rendered is None:
            raise BadRequest("html indisponible")
        await send(rendered, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest:
        await send(plain, reply_markup=markup)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args and context.args[0].lower() in ("effacer", "tout"):
        response = await simple_call(update, context, "GET", "/memory")
        if response is not None and response.json()["items"]:
            await update.effective_message.reply_text(
                "Effacer TOUTE la mémoire ? FOX oubliera tout ce qu'il retient sur toi.",
                reply_markup=keyboard([[("Oui, tout effacer", "mall_ok"), ("Annuler", "keep")]]),
            )
        elif response is not None:
            await update.effective_message.reply_text(memory_message([]))
        return
    response = await simple_call(update, context, "GET", "/memory")
    if response is None:
        return
    items = response.json()["items"]
    await show_list(update.effective_message, memory_message(items), memory_buttons(items))


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = " ".join(context.args or []).strip()
    if not text:
        await message.reply_text("Écris l'information après la commande, par exemple :\n/retiens Je préfère les réunions le matin")
        return
    user_id = update.effective_user.id
    if unknown(context).is_quiet(user_id):
        await message.reply_text(NO_ACCOUNT)
        return
    try:
        response = await call(context, "POST", "/memory", user_id, {"text": text})
    except httpx.HTTPError:
        log.exception("echec de la requete vers l'orchestrateur")
        await message.reply_text(UNAVAILABLE)
        return
    if response.status_code == 403:
        await refuse_unknown(update, context)
    elif response.status_code in (400, 409):
        await message.reply_text(response.json().get("error", "Information refusée."))
    elif response.status_code >= 400:
        log.error("Réponse inattendue de l'orchestrateur (%s) pour /memory", response.status_code)
        await message.reply_text(UNAVAILABLE)
    else:
        await message.reply_text("C'est noté. /memoire pour voir ce que je retiens.")


async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await simple_call(update, context, "GET", "/journal")
    if response is None:
        return
    await send_markdown(update.effective_message, journal_message(response.json()["entries"]))


async def briefing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    kind, *rest = briefing_command(context.args)
    if kind == "invalid":
        await message.reply_text("Je n'ai pas compris. /briefing on, /briefing off, /briefing 07:30 ou /briefing maintenant.")
        return
    if kind == "status":
        response = await simple_call(update, context, "GET", "/briefing")
        if response is not None:
            await message.reply_text(briefing_status(response.json()))
    elif kind == "preview":
        response = await simple_call(update, context, "GET", "/briefing/preview")
        if response is not None:
            await send_markdown(message, response.json()["text"])
    else:
        user_id = update.effective_user.id
        if unknown(context).is_quiet(user_id):
            await message.reply_text(NO_ACCOUNT)
            return
        try:
            response = await call(context, "POST", "/briefing", user_id, rest[0])
        except httpx.HTTPError:
            log.exception("echec de la requete vers l'orchestrateur")
            await message.reply_text(UNAVAILABLE)
            return
        if response.status_code == 403:
            await refuse_unknown(update, context)
        elif response.status_code == 400:
            await message.reply_text(response.json().get("error", "Réglage refusé."))
        elif response.status_code >= 400:
            await message.reply_text(UNAVAILABLE)
        else:
            await message.reply_text(briefing_status(response.json()))


async def on_memory_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Boutons de la mémoire : oublier un élément, tout effacer."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action, _, value = (query.data or "").partition(":")
    if unknown(context).is_quiet(user_id):
        await query.edit_message_text(NO_ACCOUNT)
        return
    if action == "mall":
        await query.edit_message_text(
            "Effacer TOUTE la mémoire ? FOX oubliera tout ce qu'il retient sur toi.",
            reply_markup=keyboard([[("Oui, tout effacer", "mall_ok"), ("Annuler", "keep")]]),
        )
        return
    if action == "mdel":
        method, path, payload = "DELETE", f"/memory/{int(value)}", None
    else:  # mall_ok
        method, path, payload = "DELETE", "/memory", {"confirm": "all"}
    try:
        response = await call(context, method, path, user_id, payload)
        if response.status_code == 403:
            unknown(context).mark(user_id)
            await query.edit_message_text(NO_ACCOUNT)
            return
        if response.status_code >= 400 and response.status_code != 404:
            await query.edit_message_text(UNAVAILABLE)
            return
        if action == "mall_ok":
            await query.edit_message_text("Mémoire effacée.")
            return
        listing = await call(context, "GET", "/memory", user_id)  # liste mise à jour, sur place
        items = listing.json()["items"]
    except (httpx.HTTPError, ValueError, KeyError):
        log.exception("echec de la gestion de la mémoire")
        await query.edit_message_text(UNAVAILABLE)
        return
    await show_list(query, memory_message(items), memory_buttons(items), edit=True)


async def send_reminders(application: Application) -> int:
    """Envoie les rappels dus. Retourne le nombre de rappels envoyés."""
    http: httpx.AsyncClient = application.bot_data["http"]
    try:
        response = await http.post(f"{ORCHESTRATOR_URL}/reminders/claim", json={})
        response.raise_for_status()
        reminders = response.json()["reminders"]
    except (httpx.HTTPError, ValueError, KeyError):
        log.warning("Impossible de relever les rappels", exc_info=True)
        return 0
    sent = 0
    for reminder in reminders:
        text, rows = reminder_message(reminder)
        rendered, plain = telegram_messages(text)[0]
        markup = keyboard(rows)
        try:
            try:
                if rendered is None:
                    raise BadRequest("html indisponible")
                await application.bot.send_message(
                    chat_id=reminder["telegram_id"], text=rendered, parse_mode=ParseMode.HTML, reply_markup=markup
                )
            except BadRequest:
                await application.bot.send_message(chat_id=reminder["telegram_id"], text=plain, reply_markup=markup)
            sent += 1
        except Exception:  # un rappel qui échoue ne doit pas bloquer les suivants
            log.exception("Rappel n°%s non envoyé", reminder.get("id"))
    return sent


async def send_briefings(application: Application) -> int:
    """Envoie les briefings du matin dus. Retourne le nombre de briefings envoyés."""
    http: httpx.AsyncClient = application.bot_data["http"]
    try:
        response = await http.post(f"{ORCHESTRATOR_URL}/briefings/claim", json={})
        response.raise_for_status()
        briefings = response.json()["briefings"]
    except (httpx.HTTPError, ValueError, KeyError):
        log.warning("Impossible de relever les briefings", exc_info=True)
        return 0
    sent = 0
    for item in briefings:
        try:
            for rendered, plain in telegram_messages(item["text"]):
                try:
                    if rendered is None:
                        raise BadRequest("html indisponible")
                    await application.bot.send_message(
                        chat_id=item["telegram_id"], text=rendered, parse_mode=ParseMode.HTML
                    )
                except BadRequest:
                    await application.bot.send_message(chat_id=item["telegram_id"], text=plain)
            sent += 1
        except Exception:  # un briefing qui échoue ne doit pas bloquer les suivants
            log.exception("Briefing non envoyé")
    return sent


async def reminder_loop(application: Application) -> None:
    while True:
        await asyncio.sleep(REMINDER_POLL_SECONDS)
        try:
            await send_reminders(application)
            await send_briefings(application)
        except Exception:  # la boucle ne doit jamais s'arrêter
            log.exception("Erreur inattendue dans la boucle des rappels")


async def on_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Boutons de gestion des conversations (reprendre, supprimer)."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action, _, value = (query.data or "").partition(":")

    if unknown(context).is_quiet(user_id):
        await query.edit_message_text(NO_ACCOUNT)
        return
    if action == "keep":
        await query.edit_message_text("D'accord, rien n'a été supprimé.")
        return
    if action == "delall":
        await query.edit_message_text(
            "Supprimer TOUT ton historique ? C'est définitif.",
            reply_markup=keyboard([[("Oui, tout supprimer", "delall_ok"), ("Annuler", "keep")]]),
        )
        return

    if action == "open":
        method, path, payload = "POST", f"/chats/{int(value)}/open", None
    elif action == "del":
        method, path, payload = "DELETE", f"/chats/{int(value)}", None
    else:  # delall_ok
        method, path, payload = "DELETE", "/chats", {"confirm": "all"}

    try:
        response = await call(context, method, path, user_id, payload)
    except httpx.HTTPError:
        log.exception("echec de la gestion des conversations")
        await query.edit_message_text(UNAVAILABLE)
        return
    if response.status_code == 403:
        unknown(context).mark(user_id)
        await query.edit_message_text(NO_ACCOUNT)
    elif response.status_code == 404:
        await query.edit_message_text("Cette conversation n'existe plus.")
    elif response.status_code >= 400:
        await query.edit_message_text(UNAVAILABLE)
    elif action == "open":
        await query.edit_message_text("Conversation reprise. Tu peux continuer.")
    elif action == "del":
        await query.edit_message_text("Conversation supprimée.")
    else:
        await query.edit_message_text("Tout l'historique a été supprimé.")


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    choice, _, action_id = (query.data or "").partition(":")
    endpoint = "confirm" if choice == "ok" else "cancel"

    try:
        response = await call(context, "POST", f"/actions/{action_id}/{endpoint}", query.from_user.id)
        if response.status_code == 403:
            result = NO_ACCOUNT
        else:
            response.raise_for_status()
            result = response.json()["reply"]
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("echec de la decision sur l'action %s", action_id)
        result = UNAVAILABLE

    await query.edit_message_text(f"{query.message.text}\n\n{result}")


async def on_start(application: Application) -> None:
    application.bot_data["http"] = httpx.AsyncClient(
        timeout=90, headers={"X-Fox-Service-Token": SERVICE_TOKEN}
    )
    application.bot_data["unknown"] = UnknownUsers()
    application.bot_data["reminders"] = asyncio.create_task(reminder_loop(application))
    try:
        await application.bot.set_my_commands(COMMANDS)
    except Exception:  # le menu est un confort : on ne bloque pas le démarrage
        log.warning("Impossible de publier le menu des commandes", exc_info=True)


async def on_stop(application: Application) -> None:
    task = application.bot_data.get("reminders")
    if task:
        task.cancel()
    await application.bot_data["http"].aclose()


def main() -> None:
    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(on_start)
        .post_shutdown(on_stop)
        .build()
    )

    # Seuls les messages privés sont traités. L'orchestrateur décide qui a un compte actif :
    # un inconnu reçoit seulement la consigne d'inscription, sans accès à FOX.
    private = filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, relay))
    for name, handler in (
        ("start", start_command),
        ("aide", help_command),
        ("help", help_command),
        ("nouveau", new_command),
        ("historique", history_command),
        ("effacer", erase_command),
        ("prive", private_command),
        ("taches", tasks_command),
        ("tache", add_task_command),
        ("memoire", memory_command),
        ("retiens", remember_command),
        ("journal", journal_command),
        ("briefing", briefing_cmd),
        ("annuler", undo_command),
    ):
        application.add_handler(CommandHandler(name, handler, filters=private))
    application.add_handler(
        CallbackQueryHandler(on_decision, pattern=r"^(ok|no):[0-9a-fA-F-]{36}$")
    )
    application.add_handler(CallbackQueryHandler(on_memory_button, pattern=r"^(mdel:[0-9]{1,18}|mall|mall_ok)$"))
    application.add_handler(CallbackQueryHandler(on_task_button, pattern=r"^(tdone|tcancel):[0-9]{1,18}$"))
    application.add_handler(
        CallbackQueryHandler(on_manage, pattern=r"^(open:[0-9]{1,18}|del:[0-9]{1,18}|delall|delall_ok|keep)$")
    )

    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
