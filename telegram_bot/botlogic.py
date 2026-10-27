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


MAX_TASKS_SHOWN = 10


def due_label(iso):
    """« 2026-10-25T23:59+01:00 » -> « 25/10 » ; avec l'heure si elle n'est pas 23:59."""
    if not iso:
        return ""
    label = short_date(iso)
    time = str(iso)[11:16]
    return f"{label} {time}" if time and time != "23:59" else label


def tasks_message(tasks):
    """Liste des tâches ouvertes, numérotées 1, 2, 3… (Markdown, rendu ensuite par telegram_messages)."""
    if not tasks:
        return "Aucune tâche en cours. Ajoute-en une avec /tache titre | échéance | priorité."
    lines = [f"**Tes tâches ({len(tasks)})**"]
    for position, task in enumerate(tasks[:MAX_TASKS_SHOWN], start=1):
        details = []
        if task.get("due_at"):
            details.append(("en retard · " if task.get("overdue") else "pour le ") + due_label(task["due_at"]))
        if task.get("priority") in ("haute", "basse"):
            details.append(f"priorité {task['priority']}")
        if task.get("status") == "en_cours":
            details.append("en cours")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"{position}. {task['title']}{suffix}")
    if len(tasks) > MAX_TASKS_SHOWN:
        lines.append(f"… et {len(tasks) - MAX_TASKS_SHOWN} autre(s).")
    return "\n".join(lines)


def task_buttons(tasks):
    """Une ligne par tâche affichée : [Terminer n, Annuler n] (n = position dans la liste)."""
    return [
        [(f"Terminer {n}", f"tdone:{t['id']}"), (f"Annuler {n}", f"tcancel:{t['id']}")]
        for n, t in enumerate(tasks[:MAX_TASKS_SHOWN], start=1)
    ]


def reminder_message(reminder):
    """Texte (Markdown) et boutons du rappel d'une tâche arrivée à l'heure."""
    when = str(reminder.get("due_at") or "")[11:16]
    suffix = f" (prévu à {when})" if when else ""
    return f"**Rappel**\n{reminder['title']}{suffix}", [[("Terminer", f"tdone:{reminder['id']}")]]


MAX_MEMORY_SHOWN = 10


def memory_message(items):
    if not items:
        return "Je ne retiens rien pour l'instant. Pour ajouter une information : /retiens ma phrase."
    lines = [f"**Ce que je retiens ({len(items)})**"]
    for position, item in enumerate(items[:MAX_MEMORY_SHOWN], start=1):
        lines.append(f"{position}. {item['content']}")
    if len(items) > MAX_MEMORY_SHOWN:
        lines.append(f"… et {len(items) - MAX_MEMORY_SHOWN} autre(s) plus anciennes.")
    return "\n".join(lines)


def memory_buttons(items):
    rows = [[(f"Oublier {n}", f"mdel:{i['id']}")] for n, i in enumerate(items[:MAX_MEMORY_SHOWN], start=1)]
    if items:
        rows.append([("Tout effacer", "mall")])
    return rows


IMPROVE_WORDS = ("améliore", "ameliore", "améliorer", "corrige", "corriger", "reformule")
SUMMARY_WORDS = ("résume", "resume", "résumé", "résumer", "resumer")
GMAIL_URL_LIMIT = 1900  # les liens des boutons Telegram sont limités (environ 2000 caractères)
MAIL_HELP = (
    "Utilisation de /mail :\n"
    "/mail rédige une relance polie à Paul pour la facture : FOX écrit un brouillon (rien n'est envoyé)\n"
    "/mail améliore + ton texte : correction et reformulation\n"
    "/mail résume + le mail collé : résumé et tâches à confirmer\n"
    "Tu peux aussi répondre à un message avec /mail résume ou /mail améliore."
)


def parse_mail_command(raw, replied=None):
    """Texte complet de la commande -> ("draft"|"improve"|"summary", texte) ou ("help", None).

    Les retours à la ligne du mail collé sont conservés. Pour « améliore » et « résume », le texte à
    traiter peut venir du message auquel on répond.
    """
    parts = str(raw or "").strip().split(None, 1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    words = rest.split(None, 1)
    first = words[0].lower().rstrip(":,.") if words else ""
    after = words[1].strip() if len(words) > 1 else ""
    for mode, verbs in (("improve", IMPROVE_WORDS), ("summary", SUMMARY_WORDS)):
        if first in verbs:
            text = after or str(replied or "").strip()
            return (mode, text) if text else ("help", None)
    return ("draft", rest) if rest else ("help", None)


def gmail_url(draft):
    """Lien Gmail avec destinataire, objet et texte préremplis. Retourne (lien, tronqué)."""
    from urllib.parse import quote

    def build(body):
        return (
            "https://mail.google.com/mail/?view=cm&fs=1"
            f"&to={quote(draft.get('to') or '', safe='')}&su={quote(draft.get('subject') or '', safe='')}"
            f"&body={quote(body, safe='')}"
        )

    body = draft.get("body") or ""
    url = build(body)
    if len(url) <= GMAIL_URL_LIMIT:
        return url, False
    while len(url) > GMAIL_URL_LIMIT and body:
        body = body[: max(0, len(body) - max(50, (len(url) - GMAIL_URL_LIMIT) // 2))]
        url = build(body.rstrip())
    return url, True


def draft_message(draft):
    """Brouillon en Markdown : objet, destinataire, puis le texte dans un bloc copiable d'un appui."""
    lines = []
    if draft.get("subject"):
        lines.append(f"**Objet :** {draft['subject']}")
    if draft.get("to"):
        lines.append(f"**À :** {draft['to']}")
    body = str(draft.get("body") or "").replace("```", "ʼʼʼ")
    return "\n".join(lines + ([""] if lines else []) + ["```", body, "```"])


ADD_WORDS = ("ajoute", "ajouter", "nouveau", "add")
DELETE_WORDS = ("supprime", "supprimer", "efface", "effacer")
EDIT_WORDS = ("modifie", "modifier", "change")
CLIENT_HELP = (
    "Clients :\n"
    "/clients : liste de tes clients\n"
    "/client Paul : fiche d'un client\n"
    "/client ajoute Nom ; téléphone ; e-mail ; adresse : nouveau client (tout sauf le nom est facultatif)\n"
    "/client modifie Paul ; téléphone ; e-mail ; adresse : changer des champs (vide = inchangé, - = effacer)\n"
    "/client supprime Paul : supprimer la fiche (ses devis sont conservés)"
)
QUOTE_HELP = (
    "Devis :\n"
    "/devis : tes devis récents\n"
    "/devis 3 : afficher le devis n° 3 de l'année (ou /devis DV-2026-003)\n"
    "/devis 3 accepté : changer le statut (brouillon, envoyé, accepté, refusé)\n"
    "/devis 3 supprime : supprimer un brouillon ou un devis refusé\n"
    "Créer un devis (une ligne par prestation, sur plusieurs lignes) :\n"
    "/devis Paul Martin\n"
    "Peinture salon | 2 | 15000\n"
    "Main d'œuvre | 1,5 | 50000\n"
    "TVA 18 (facultatif)\n"
    "note : paiement à 30 jours (facultatif)\n"
    "Format d'une ligne : description | quantité | prix unitaire. Les totaux sont calculés par FOX."
)
QUOTE_REF = re.compile(r"(?i)(?:dv-\d{4}-\d{1,6}|\d{1,6})")
QUOTE_STATUS_CODES = {"e": "envoyé", "a": "accepté", "r": "refusé"}


def command_rest(raw):
    """Texte qui suit le nom de la commande (les retours à la ligne sont conservés)."""
    parts = str(raw or "").strip().split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def client_command(raw):
    """Texte de /client -> ("help",) | ("show", nom) | ("add", texte) | ("edit", nom, champs) | ("delete", nom)."""
    rest = command_rest(raw)
    if not rest:
        return ("help",)
    first, _, after = rest.partition(" ")
    verb, after = first.lower().rstrip(":,"), after.strip()
    if verb in ADD_WORDS:
        return ("add", after) if after else ("help",)
    if verb in DELETE_WORDS:
        return ("delete", after) if after else ("help",)
    if verb in EDIT_WORDS:
        name, _, changes = after.partition(";")
        return ("edit", name.strip(), changes) if name.strip() and changes.strip() else ("help",)
    return ("show", rest)


def quote_command(raw):
    """Texte de /devis -> ("list",) | ("create", texte) | ("show", réf) | ("status", réf, mot) | ("delete", réf) | ("help",)."""
    rest = command_rest(raw)
    if not rest:
        return ("list",)
    if "\n" in rest or "|" in rest:
        return ("create", rest)
    words = rest.split()
    if not QUOTE_REF.fullmatch(words[0]):
        return ("help",)
    if len(words) == 1:
        return ("show", words[0])
    word = " ".join(words[1:]).lower()
    if word in DELETE_WORDS:
        return ("delete", words[0])
    return ("status", words[0], word)


def clients_message(clients):
    if not clients:
        return "Aucun client pour l'instant. Ajoute-en un avec /client ajoute Nom ; téléphone ; e-mail ; adresse."
    lines = [f"**Tes clients ({len(clients)})**"]
    for client in clients:
        extra = " · ".join(v for v in (client.get("phone"), client.get("email")) if v)
        lines.append(f"- {client['name']}" + (f" ({extra})" if extra else ""))
    lines.append("/client Nom pour la fiche complète.")
    return "\n".join(lines)


def client_message(client):
    lines = [f"**{client['name']}**"]
    for label, key in (("Téléphone", "phone"), ("E-mail", "email"), ("Adresse", "address"), ("Note", "notes")):
        if client.get(key):
            lines.append(f"{label} : {client[key]}")
    if len(lines) == 1:
        lines.append("Pas d'autres informations. /client modifie " + client["name"] + " ; téléphone ; e-mail ; adresse")
    return "\n".join(lines)


def clients_choice(clients):
    names = ", ".join(c["name"] for c in clients)
    return f"Plusieurs clients correspondent : {names}. Précise le nom complet."


def quote_message(quote):
    lines = [f"**Devis {quote['number']}** ({quote['status_label']})", f"Client : {quote['client']}", ""]
    for line in quote["lines"]:
        lines.append(f"- {line['description']} : {line['quantity']} × {line['unit_price']} = {line['total']}")
    lines += [
        "",
        f"Sous-total : {quote['subtotal']}",
        f"TVA {quote['vat_rate']} % : {quote['vat']}",
        f"**Total : {quote['total']}**",
    ]
    if quote.get("notes"):
        lines += ["", f"Note : {quote['notes']}"]
    return "\n".join(lines)


def quotes_message(quotes):
    if not quotes:
        return "Aucun devis pour l'instant. /devis pour voir comment en créer un."
    lines = [f"**Tes devis ({len(quotes)})**"]
    for quote in quotes:
        lines.append(f"- {quote['number']} · {quote['client']} · {quote['total']} · {quote['status_label']}")
    lines.append("/devis 3 pour afficher un devis.")
    return "\n".join(lines)


def quote_buttons(quote):
    """Une ligne de boutons pour changer le statut (le statut actuel est omis)."""
    codes = {"envoye": "e", "accepte": "a", "refuse": "r"}
    labels = {"e": "Envoyé", "a": "Accepté", "r": "Refusé"}
    row = [(labels[c], f"qs:{quote['number']}:{c}") for st, c in codes.items() if st != quote["status"]]
    return [row]


def briefing_command(args):
    """Argument de /briefing -> ("status",) | ("preview",) | ("set", payload) | ("invalid",)."""
    if not args:
        return ("status",)
    word = " ".join(args).strip().lower()
    if word in ("maintenant", "now", "voir"):
        return ("preview",)
    if word in ("on", "oui", "activer", "active"):
        return ("set", {"enabled": True})
    if word in ("off", "non", "stop", "desactiver", "désactiver", "desactive"):
        return ("set", {"enabled": False})
    if re.fullmatch(r"\d{1,2}\s*[h:]\s*(\d{2})?", word):
        return ("set", {"enabled": True, "time": word})
    return ("invalid",)


def briefing_status(settings):
    if settings.get("enabled"):
        return (
            f"Briefing du matin activé à {settings['time']}.\n"
            "/briefing off pour le désactiver, /briefing 07:30 pour changer l'heure, "
            "/briefing maintenant pour le voir tout de suite."
        )
    return (
        "Briefing du matin désactivé.\n"
        f"/briefing on pour l'activer ({settings.get('time', '08:00')} par défaut), "
        "/briefing 07:30 pour choisir l'heure, /briefing maintenant pour le voir tout de suite."
    )


def journal_message(entries):
    if not entries:
        return "Le journal est vide pour l'instant."
    lines = [f"**Journal des actions ({len(entries)} dernières)**"]
    for entry in entries:
        lines.append(f"- {short_date(entry['at'])} {str(entry['at'])[11:16]} · {entry['text']}")
    return "\n".join(lines)


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
