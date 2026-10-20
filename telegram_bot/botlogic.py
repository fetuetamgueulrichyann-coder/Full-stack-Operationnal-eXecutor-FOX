"""Logique du bot sans dépendance à Telegram (testable seule)."""
import time

TELEGRAM_LIMIT = 4000
TITLE_BUTTON_CHARS = 34
MAX_HISTORY_BUTTONS = 10
UNKNOWN_QUIET_SECONDS = 600
UNKNOWN_MAX_ENTRIES = 1000


def chunks(text, limit=TELEGRAM_LIMIT):
    for i in range(0, len(text), limit):
        yield text[i : i + limit]


def short_date(iso):
    """« 2026-10-19T14:20:00+00:00 » -> « 19/10 »."""
    try:
        day = str(iso)[:10].split("-")
        return f"{day[2]}/{day[1]}"
    except IndexError:
        return ""


def history_buttons(chats, current_id=None):
    """Boutons « reprendre » de la liste des conversations : [(texte, callback_data)]."""
    buttons = []
    for chat in chats[:MAX_HISTORY_BUTTONS]:
        title = chat.get("title") or "Sans titre"
        if len(title) > TITLE_BUTTON_CHARS:
            title = title[: TITLE_BUTTON_CHARS - 1].rstrip() + "…"
        mark = "● " if chat["id"] == current_id else ""
        buttons.append((f"{mark}{title} · {short_date(chat.get('updated_at'))}", f"open:{chat['id']}"))
    return buttons


def start_code(args):
    """Code d'invitation fourni avec /start (le reste du texte est ignoré), sinon None."""
    return " ".join(args).strip() if args else None


class UnknownUsers:
    """Évite de solliciter l'orchestrateur à chaque message d'un inconnu (bruit, journal d'audit).

    Après un refus, les messages suivants du même identifiant reçoivent seulement la consigne,
    sans appel serveur, pendant UNKNOWN_QUIET_SECONDS.
    """

    def __init__(self, quiet=UNKNOWN_QUIET_SECONDS, max_entries=UNKNOWN_MAX_ENTRIES, clock=time.monotonic):
        self.quiet = quiet
        self.max_entries = max_entries
        self.clock = clock
        self._until = {}

    def mark(self, telegram_id):
        if len(self._until) >= self.max_entries:
            now = self.clock()
            self._until = {k: v for k, v in self._until.items() if v > now}
            if len(self._until) >= self.max_entries:
                self._until.clear()
        self._until[telegram_id] = self.clock() + self.quiet

    def is_quiet(self, telegram_id):
        until = self._until.get(telegram_id)
        if until is None:
            return False
        if self.clock() >= until:
            del self._until[telegram_id]
            return False
        return True

    def forget(self, telegram_id):
        self._until.pop(telegram_id, None)
