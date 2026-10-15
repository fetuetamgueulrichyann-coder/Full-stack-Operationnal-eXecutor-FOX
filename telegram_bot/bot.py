import logging
import os

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("telegram_bot")

TOKEN = os.environ["TELEGRAM_TOKEN"]
ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]
ALLOWED_USER_ID = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
TELEGRAM_LIMIT = 4000
UNAVAILABLE = "Service momentanément indisponible."


def chunks(text):
    for i in range(0, len(text), TELEGRAM_LIMIT):
        yield text[i : i + TELEGRAM_LIMIT]


def decision_keyboard(action_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmer", callback_data=f"ok:{action_id}"),
                InlineKeyboardButton("❌ Annuler", callback_data=f"no:{action_id}"),
            ]
        ]
    )


async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    message = update.effective_message
    http: httpx.AsyncClient = context.application.bot_data["http"]

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    try:
        response = await http.post(
            f"{ORCHESTRATOR_URL}/message",
            json={"user_id": update.effective_user.id, "text": text},
        )
        if response.status_code == 429:
            await message.reply_text("Limite journalière atteinte.")
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
            await message.reply_text(
                f"Action proposée :\n{action['summary']}",
                reply_markup=decision_keyboard(action["id"]),
            )
        return

    for part in chunks(data["reply"]):
        await message.reply_text(part)


async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await forward(update, context, update.effective_message.text)


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await forward(update, context, "annule ça")


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user.id != ALLOWED_USER_ID:
        return

    await query.answer()
    http: httpx.AsyncClient = context.application.bot_data["http"]

    choice, _, action_id = (query.data or "").partition(":")
    endpoint = "confirm" if choice == "ok" else "cancel"

    try:
        response = await http.post(
            f"{ORCHESTRATOR_URL}/actions/{action_id}/{endpoint}",
            json={"user_id": query.from_user.id},
        )
        response.raise_for_status()
        result = response.json()["reply"]
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("echec de la decision sur l'action %s", action_id)
        result = UNAVAILABLE

    await query.edit_message_text(f"{query.message.text}\n\n{result}")


async def on_start(application: Application) -> None:
    application.bot_data["http"] = httpx.AsyncClient(timeout=90)


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

    # Seuls les messages texte privés de l'utilisateur autorisé sont traités ;
    # tout autre expéditeur ne correspond à aucun handler et reste sans réponse.
    only_owner = (
        filters.User(user_id=ALLOWED_USER_ID)
        & filters.ChatType.PRIVATE
        & filters.TEXT
        & ~filters.COMMAND
    )
    application.add_handler(MessageHandler(only_owner, relay))
    application.add_handler(
        CommandHandler(
            "annuler",
            undo_command,
            filters=filters.User(user_id=ALLOWED_USER_ID) & filters.ChatType.PRIVATE,
        )
    )
    application.add_handler(
        CallbackQueryHandler(on_decision, pattern=r"^(ok|no):[0-9a-fA-F-]{36}$")
    )

    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
