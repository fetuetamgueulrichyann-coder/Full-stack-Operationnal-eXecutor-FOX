"""Logique du bot sans dépendance à Telegram (testable seule)."""
import html
import re
import time

TELEGRAM_LIMIT = 4000
TITLE_BUTTON_CHARS = 34
MAX_HISTORY_BUTTONS = 10
UNKNOWN_QUIET_SECONDS = 600
UNKNOWN_MAX_ENTRIES = 1000


# --- Rendu Telegram -----------------------------------------------------------------
# Le texte de FOX est du Markdown simple. Telegram n'en comprend qu'un sous-ensemble : on le
# convertit en HTML Telegram (<b>, <i>, <code>, <pre>, <a>, <blockquote>), tout le reste du
# texte étant échappé. Si le résultat n'est pas valide, on retombe sur le texte brut.
TAG = re.compile(r"<(/?)(b|i|s|code|pre|a|blockquote)(?:\s[^>]*)?>")
FENCE = re.compile(r"^\s*```")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBERED = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")


def escape(text):
    return html.escape(text, quote=False)


def balanced(markup):
    """Vrai si les balises sont bien imbriquées et toutes fermées."""
    stack = []
    for match in TAG.finditer(markup):
        closing, name = match.groups()
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


def inline(text, emphasis=True):
    """Convertit le Markdown d'une ligne. Le texte est échappé AVANT toute balise ajoutée."""
    codes = []

    def keep_code(match):
        codes.append(f"<code>{escape(match.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = escape(INLINE_CODE.sub(keep_code, text))
    text = LINK.sub(lambda m: '<a href="%s">%s</a>' % (m.group(2).replace('"', "%22"), m.group(1)), text)
    if emphasis:
        text = BOLD.sub(lambda m: f"<b>{m.group(2)}</b>", text)
        text = STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
        text = ITALIC_STAR.sub(lambda m: f"<i>{m.group(1)}</i>", text)
        text = ITALIC_UNDERSCORE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    for index, code in enumerate(codes):
        text = text.replace(f"\x00{index}\x00", code)
    return text


def safe_inline(text):
    result = inline(text)
    if balanced(result):
        return result
    return inline(text, emphasis=False)  # mise en forme croisée : on garde le texte, sans style


def render_html(markdown):
    """Markdown simple vers HTML Telegram. Retourne None si le résultat n'est pas valide."""
    lines = markdown.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            code = []
            i += 1
            while i < len(lines) and not FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1  # ligne de fermeture (absente si le bloc n'est pas fermé)
            out.append("<pre>%s</pre>" % escape("\n".join(code)))
            continue
        if TABLE_ROW.match(line):
            rows = []
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                rows.append(lines[i])
                i += 1
            out.append("<pre>%s</pre>" % escape("\n".join(rows)))
            continue
        if QUOTE.match(line):
            quoted = []
            while i < len(lines) and QUOTE.match(lines[i]):
                quoted.append(safe_inline(QUOTE.match(lines[i]).group(1)))
                i += 1
            out.append("<blockquote>%s</blockquote>" % "\n".join(quoted))
            continue
        heading = HEADING.match(line)
        if heading:
            title = re.sub(r"\*\*|__", "", heading.group(1))
            out.append("<b>%s</b>" % inline(title, emphasis=False))
        elif RULE.match(line):
            out.append("──────────")
        elif BULLET.match(line):
            indent, item = BULLET.match(line).groups()
            out.append("%s• %s" % ("  " * min(len(indent) // 2, 4), safe_inline(item)))
        elif NUMBERED.match(line):
            indent, number, item = NUMBERED.match(line).groups()
            out.append("%s%s. %s" % ("  " * min(len(indent) // 2, 4), number, safe_inline(item)))
        else:
            out.append(safe_inline(line))
        i += 1
    result = "\n".join(out)
    return result if balanced(result) else None


def split_blocks(markdown):
    """Blocs séparés par une ligne vide ; un bloc de code (```) n'est jamais coupé en deux."""
    blocks, current, in_fence = [], [], False
    for line in markdown.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def cut_block(block, limit):
    """Coupe un bloc trop long par lignes (un bloc de code reste entouré de ses ```)."""
    lines = block.split("\n")
    fenced = bool(lines) and FENCE.match(lines[0]) is not None
    if fenced:
        body = lines[1:-1] if len(lines) > 1 and FENCE.match(lines[-1]) else lines[1:]
        room = limit - 8
    else:
        body, room = lines, limit
    pieces, current, size = [], [], 0
    for line in body:
        while len(line) > room:  # ligne unique trop longue : coupure franche
            if current:
                pieces.append(current)
                current, size = [], 0
            cut = line.rfind(" ", 0, room)
            cut = cut if cut > room // 2 else room
            pieces.append([line[:cut]])
            line = line[cut:].lstrip(" ")
        if size + len(line) + 1 > room and current:
            pieces.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append(current)
    joined = ["\n".join(piece) for piece in pieces]
    return ["```\n%s\n```" % piece for piece in joined] if fenced else joined


def split_markdown(markdown, limit=TELEGRAM_LIMIT):
    """Découpe en messages de taille permise, aux limites des paragraphes."""
    chunks, current = [], ""
    for block in split_blocks(markdown):
        for piece in ([block] if len(block) <= limit else cut_block(block, limit)):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def telegram_messages(markdown, limit=TELEGRAM_LIMIT):
    """Liste de (html, texte brut) : `html` vaut None si le rendu n'est pas valide."""
    messages = []
    for chunk in split_markdown(markdown, limit):
        rendered = render_html(chunk)
        if rendered is not None and len(rendered) > 4096:
            rendered = None  # le HTML dépasse la limite de Telegram : texte brut
        messages.append((rendered, chunk))
    return messages


def action_message(summary):
    """Action en attente : (html, texte brut)."""
    return f"<b>Action proposée</b>\n{escape(summary)}", f"Action proposée :\n{summary}"


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
