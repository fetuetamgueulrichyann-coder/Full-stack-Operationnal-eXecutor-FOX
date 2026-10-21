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

from botlogic import UnknownUsers, action_message, history_buttons, start_code, telegram_messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("telegram_bot")

TOKEN = os.environ["TELEGRAM_TOKEN"]
ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]
# Jeton partagé avec l'orchestrateur : prouve que la requête vient bien de ce bot.
# Qui a le droit de parler à FOX est décidé par l'orchestrateur (comptes actifs), pas par le bot.
SERVICE_TOKEN = os.environ["FOX_SERVICE_TOKEN"]
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
    "/annuler : annuler la dernière action effectuée"
)
COMMANDS = [
    BotCommand("nouveau", "Nouvelle conversation"),
    BotCommand("historique", "Reprendre une conversation"),
    BotCommand("effacer", "Supprimer l'historique"),
    BotCommand("prive", "Mode ne pas enregistrer"),
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
    try:
        await application.bot.set_my_commands(COMMANDS)
    except Exception:  # le menu est un confort : on ne bloque pas le démarrage
        log.warning("Impossible de publier le menu des commandes", exc_info=True)


async def on_stop(application: Application) -> None:
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
        ("annuler", undo_command),
    ):
        application.add_handler(CommandHandler(name, handler, filters=private))
    application.add_handler(
        CallbackQueryHandler(on_decision, pattern=r"^(ok|no):[0-9a-fA-F-]{36}$")
    )
    application.add_handler(
        CallbackQueryHandler(on_manage, pattern=r"^(open:[0-9]{1,18}|del:[0-9]{1,18}|delall|delall_ok|keep)$")
    )

    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
