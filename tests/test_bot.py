"""Gestionnaires du bot Telegram, avec de faux objets Telegram et un faux orchestrateur."""
import asyncio
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

os.environ.setdefault("TELEGRAM_TOKEN", "123:jeton-de-test")
os.environ.setdefault("ORCHESTRATOR_URL", "http://orchestrator:5000")
os.environ.setdefault("FOX_SERVICE_TOKEN", "jeton-de-service-de-test")

import bot  # noqa: E402
import botlogic  # noqa: E402


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bot.httpx.HTTPStatusError("erreur", request=None, response=None)


class Message:
    def __init__(self, text="Bonjour"):
        self.text = text
        self.chat_id = 55
        self.replies = []
        self.modes = []
        self.refuse_html = False

    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        if self.refuse_html and parse_mode is not None:
            raise bot.BadRequest("Can't parse entities")
        self.replies.append((text, reply_markup))
        self.modes.append(parse_mode)


class Query:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(text="Action proposée :\nCréer Réunion")
        self.edits = []
        self.answered = False

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class Bot:
    async def send_chat_action(self, **kwargs):
        pass


@pytest.fixture
def server(monkeypatch):
    """Faux orchestrateur : réponses programmées par (méthode, chemin), appels enregistrés."""
    state = SimpleNamespace(calls=[], routes={}, default=Response(200, {"reply": "ok"}))

    async def call(context, method, path, telegram_id, payload=None):
        state.calls.append((method, path, telegram_id, payload))
        response = state.routes.get((method, path), state.default)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(bot, "call", call)
    return state


@pytest.fixture
def context():
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"unknown": botlogic.UnknownUsers()}),
        bot=Bot(),
        args=[],
    )


def update(text="Bonjour", user_id=900):
    message = Message(text)
    return SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id)), message


def run(coro):
    return asyncio.run(coro)


def buttons(markup):
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


# --- Messages ----------------------------------------------------------------
def test_message_d_un_compte_actif_est_transmis_avec_son_identifiant(server, context):
    server.routes[("POST", "/message")] = Response(200, {"reply": "Salut !", "pending": []})
    upd, msg = update("Bonjour", user_id=900)
    run(bot.relay(upd, context))
    assert server.calls == [("POST", "/message", 900, {"text": "Bonjour"})]
    assert msg.replies == [("Salut !", None)]


def test_le_bot_ne_filtre_plus_sur_un_identifiant_fixe():
    # Qui peut parler à FOX est décidé par les comptes de l'orchestrateur.
    assert not hasattr(bot, "ALLOWED_USER_ID")


def test_inconnu_recoit_la_consigne_puis_plus_aucun_appel(server, context):
    server.routes[("POST", "/message")] = Response(403, {"error": "non autorise"})
    upd, msg = update("Salut", user_id=1234)
    run(bot.relay(upd, context))
    assert msg.replies[0][0] == bot.NO_ACCOUNT and "/start" in bot.NO_ACCOUNT
    run(bot.relay(*[update("encore", user_id=1234)[0], context]))
    run(bot.relay(*[update("encore", user_id=1234)[0], context]))
    assert len(server.calls) == 1  # les messages suivants n'atteignent plus l'orchestrateur


def test_jeton_de_service_incorrect_signale_une_erreur_de_configuration(server, context):
    server.routes[("POST", "/message")] = Response(401, {})
    upd, msg = update()
    run(bot.relay(upd, context))
    assert "configuration" in msg.replies[0][0]


@pytest.mark.parametrize(
    "status, fragment",
    [(429, "Limite"), (413, "trop long"), (503, "sollicité"), (502, "configuration")],
)
def test_erreurs_de_l_orchestrateur_expliquees(server, context, status, fragment):
    server.routes[("POST", "/message")] = Response(status, {})
    upd, msg = update()
    run(bot.relay(upd, context))
    assert fragment in msg.replies[0][0]


def test_orchestrateur_injoignable(server, context):
    server.routes[("POST", "/message")] = bot.httpx.ConnectError("hors ligne")
    upd, msg = update()
    run(bot.relay(upd, context))
    assert msg.replies == [(bot.UNAVAILABLE, None)]


def test_action_en_attente_affiche_les_boutons(server, context):
    server.routes[("POST", "/message")] = Response(
        200, {"reply": "x", "pending": [{"id": "6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11", "summary": "Créer Réunion"}]}
    )
    upd, msg = update()
    run(bot.relay(upd, context))
    text, markup = msg.replies[0]
    assert "Créer Réunion" in text
    assert buttons(markup) == [[
        ("✅ Confirmer", "ok:6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"),
        ("❌ Annuler", "no:6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"),
    ]]


# --- /start et inscription -----------------------------------------------------
def test_start_avec_code_cree_le_compte(server, context):
    server.routes[("POST", "/auth/telegram")] = Response(201, {"user": {}})
    context.args = ["FOX-ABCD-EFGH-JKLM"]
    upd, msg = update("/start FOX-ABCD-EFGH-JKLM", user_id=777)
    context.application.bot_data["unknown"].mark(777)
    run(bot.start_command(upd, context))
    assert server.calls == [("POST", "/auth/telegram", 777, {"code": "FOX-ABCD-EFGH-JKLM"})]
    assert "Bienvenue" in msg.replies[0][0] and "/nouveau" in msg.replies[0][0]
    assert not context.application.bot_data["unknown"].is_quiet(777)


@pytest.mark.parametrize(
    "status, fragment",
    [(400, "invalide"), (409, "déjà inscrit"), (429, "Trop de tentatives"), (500, "indisponible")],
)
def test_start_avec_code_refuse(server, context, status, fragment):
    server.routes[("POST", "/auth/telegram")] = Response(status, {})
    context.args = ["FOX-XXXX"]
    upd, msg = update()
    run(bot.start_command(upd, context))
    assert fragment in msg.replies[0][0]


def test_start_sans_code_pour_un_inconnu(server, context):
    server.routes[("GET", "/chats")] = Response(403, {})
    upd, msg = update(user_id=4)
    run(bot.start_command(upd, context))
    assert msg.replies[0][0] == bot.NO_ACCOUNT


def test_start_sans_code_pour_un_compte_actif(server, context):
    server.routes[("GET", "/chats")] = Response(200, {"chats": [], "current": None, "private": False})
    upd, msg = update()
    run(bot.start_command(upd, context))
    assert "FOX" in msg.replies[0][0] and "/historique" in msg.replies[0][0]


# --- Conversations -------------------------------------------------------------
def test_nouveau(server, context):
    server.routes[("POST", "/chats")] = Response(201, {"chat_id": 5, "private": False})
    upd, msg = update(user_id=11)
    run(bot.new_command(upd, context))
    assert server.calls == [("POST", "/chats", 11, None)]
    assert "Nouvelle conversation" in msg.replies[0][0]


def test_historique_propose_des_boutons_pour_reprendre(server, context):
    server.routes[("GET", "/chats")] = Response(
        200,
        {"chats": [{"id": 9, "title": "Devis", "updated_at": "2026-10-19T10:00:00"}], "current": 9, "private": False},
    )
    upd, msg = update()
    run(bot.history_command(upd, context))
    assert buttons(msg.replies[0][1]) == [[("● Devis · 19/10", "open:9")]]


def test_historique_vide(server, context):
    server.routes[("GET", "/chats")] = Response(200, {"chats": [], "current": None, "private": False})
    upd, msg = update()
    run(bot.history_command(upd, context))
    assert "Aucune conversation" in msg.replies[0][0]


def test_effacer_propose_conversation_courante_et_tout(server, context):
    server.routes[("GET", "/chats")] = Response(
        200, {"chats": [{"id": 9, "title": "t", "updated_at": "2026-10-19"}], "current": 9, "private": False}
    )
    upd, msg = update()
    run(bot.erase_command(upd, context))
    assert buttons(msg.replies[0][1]) == [
        [("🗑 Cette conversation", "del:9")],
        [("🗑 Tout l'historique", "delall")],
        [("Annuler", "keep")],
    ]


def test_effacer_en_mode_prive_ne_propose_pas_de_conversation_courante(server, context):
    server.routes[("GET", "/chats")] = Response(
        200, {"chats": [{"id": 9, "title": "t", "updated_at": "2026-10-19"}], "current": None, "private": True}
    )
    upd, msg = update()
    run(bot.erase_command(upd, context))
    assert [row[0][1] for row in buttons(msg.replies[0][1])] == ["delall", "keep"]


def test_effacer_sans_historique(server, context):
    server.routes[("GET", "/chats")] = Response(200, {"chats": [], "current": None, "private": False})
    upd, msg = update()
    run(bot.erase_command(upd, context))
    assert "Aucun historique" in msg.replies[0][0]


@pytest.mark.parametrize("args, on", [([], True), (["off"], False), (["STOP"], False), (["bizarre"], True)])
def test_prive(server, context, args, on):
    server.routes[("POST", "/private")] = Response(200, {"private": on})
    context.args = args
    upd, msg = update(user_id=3)
    run(bot.private_command(upd, context))
    assert server.calls == [("POST", "/private", 3, {"on": on})]
    if on:
        assert msg.replies[0][0].startswith("Mode privé activé")
        assert "pas enregistrée" in msg.replies[0][0]
    else:
        assert msg.replies[0][0] == "Mode privé désactivé."


# --- Boutons de gestion --------------------------------------------------------
def manage(server, context, data, user_id=900):
    query = Query(data, user_id)
    run(bot.on_manage(SimpleNamespace(callback_query=query), context))
    return query


def test_reprendre_une_conversation(server, context):
    query = manage(server, context, "open:9")
    assert server.calls == [("POST", "/chats/9/open", 900, None)]
    assert "reprise" in query.edits[0][0] and query.answered


def test_supprimer_la_conversation_courante(server, context):
    query = manage(server, context, "del:9")
    assert server.calls == [("DELETE", "/chats/9", 900, None)]
    assert "supprimée" in query.edits[0][0]


def test_supprimer_tout_demande_une_seconde_confirmation_sans_appel(server, context):
    query = manage(server, context, "delall")
    assert server.calls == []  # rien n'est supprimé au premier clic
    assert "TOUT" in query.edits[0][0]
    assert buttons(query.edits[0][1]) == [[("Oui, tout supprimer", "delall_ok"), ("Annuler", "keep")]]


def test_supprimer_tout_apres_confirmation(server, context):
    query = manage(server, context, "delall_ok")
    assert server.calls == [("DELETE", "/chats", 900, {"confirm": "all"})]
    assert "Tout l'historique" in query.edits[0][0]


def test_annuler_ne_supprime_rien(server, context):
    query = manage(server, context, "keep")
    assert server.calls == [] and "rien n'a été supprimé" in query.edits[0][0]


def test_conversation_disparue(server, context):
    server.routes[("POST", "/chats/9/open")] = Response(404, {})
    query = manage(server, context, "open:9")
    assert "n'existe plus" in query.edits[0][0]


def test_bouton_d_un_inconnu_refuse(server, context):
    server.routes[("DELETE", "/chats/9")] = Response(403, {})
    query = manage(server, context, "del:9", user_id=4)
    assert query.edits[0][0] == bot.NO_ACCOUNT


# --- Confirmation d'actions ----------------------------------------------------
def test_confirmation_utilise_l_identifiant_de_celui_qui_clique(server, context):
    action = "6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"
    server.routes[("POST", f"/actions/{action}/confirm")] = Response(200, {"reply": "Fait."})
    query = Query(f"ok:{action}", 4242)
    run(bot.on_decision(SimpleNamespace(callback_query=query), context))
    assert server.calls == [("POST", f"/actions/{action}/confirm", 4242, None)]
    assert query.edits[0][0].endswith("Fait.")


def test_confirmation_refusee_pour_un_inconnu(server, context):
    action = "6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"
    server.routes[("POST", f"/actions/{action}/cancel")] = Response(403, {})
    query = Query(f"no:{action}", 4)
    run(bot.on_decision(SimpleNamespace(callback_query=query), context))
    assert bot.NO_ACCOUNT in query.edits[0][0]


# --- Enregistrement des gestionnaires -------------------------------------------
def test_toutes_les_commandes_annoncees_ont_un_gestionnaire():
    declared = {c.command for c in bot.COMMANDS}
    assert declared == {"nouveau", "historique", "effacer", "prive", "taches", "tache", "memoire", "retiens", "journal", "briefing", "annuler", "aide"}
    for name in declared:
        assert f"/{name}" in bot.HELP or name == "aide"


def build_application(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bot.Application, "run_polling", lambda self, **kwargs: captured.setdefault("app", self)
    )
    bot.main()
    return captured["app"]


def test_main_enregistre_commandes_et_boutons(monkeypatch):
    app = build_application(monkeypatch)
    handlers = app.handlers[0]
    commands = set()
    patterns = []
    for handler in handlers:
        if hasattr(handler, "commands"):
            commands |= set(handler.commands)
        if hasattr(handler, "pattern") and handler.pattern is not None:
            patterns.append(handler.pattern)
    assert commands == {"start", "aide", "help", "nouveau", "historique", "effacer", "prive", "taches", "tache", "memoire", "retiens", "journal", "briefing", "annuler"}
    uuid_data = "ok:6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"
    for data in ("open:9", "del:9", "delall", "delall_ok", "keep", "tdone:9", "tcancel:9", "mdel:9", "mall", "mall_ok", uuid_data):
        assert sum(1 for p in patterns if p.match(data)) == 1, data  # un seul gestionnaire
    for data in ("open:abc", "del:", "delall_ok2", "open:1; DROP", "ok:123", "tdone:", "tdone:1x", "tcancel:abc", "mdel:", "mdel:x", "mall2"):
        assert not any(p.match(data) for p in patterns), data


def test_les_commandes_sont_limitees_aux_conversations_privees(monkeypatch):
    app = build_application(monkeypatch)
    for handler in app.handlers[0]:
        if hasattr(handler, "commands"):
            assert handler.filters is not None, handler.commands


# --- Mise en forme des réponses (phase A2b) ---------------------------------------
def test_reponse_rendue_en_html_telegram(server, context):
    server.routes[("POST", "/message")] = Response(
        200, {"reply": "Voici **l'essentiel** : `x < y` & fin", "pending": []}
    )
    upd, msg = update()
    run(bot.relay(upd, context))
    assert msg.replies == [("Voici <b>l'essentiel</b> : <code>x &lt; y</code> &amp; fin", None)]
    assert msg.modes == [bot.ParseMode.HTML]


def test_repli_en_texte_brut_si_telegram_refuse_le_html(server, context):
    server.routes[("POST", "/message")] = Response(200, {"reply": "Un **gras** <ok>", "pending": []})
    upd, msg = update()
    msg.refuse_html = True
    run(bot.relay(upd, context))
    assert msg.replies == [("Un **gras** <ok>", None)]  # le texte arrive quand même, sans HTML
    assert msg.modes == [None]


def test_long_message_envoye_en_plusieurs_parties_aux_paragraphes(server, context):
    paragraphs = [f"Partie {i} " + "mot " * 300 for i in range(8)]
    server.routes[("POST", "/message")] = Response(200, {"reply": "\n\n".join(paragraphs), "pending": []})
    upd, msg = update()
    run(bot.relay(upd, context))
    assert len(msg.replies) > 1
    assert all(len(text) <= 4096 for text, _ in msg.replies)
    assert all(text.startswith("Partie") for text, _ in msg.replies)


def test_action_en_attente_en_html_avec_boutons_et_echappement(server, context):
    action = "6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"
    server.routes[("POST", "/message")] = Response(
        200,
        {"reply": "x", "pending": [{"id": action, "summary": "Créer l'événement « <b>A</b> »\nQuand : demain"}]},
    )
    upd, msg = update()
    run(bot.relay(upd, context))
    text, markup = msg.replies[0]
    assert text == "<b>Action proposée</b>\nCréer l'événement « &lt;b&gt;A&lt;/b&gt; »\nQuand : demain"
    assert msg.modes == [bot.ParseMode.HTML]
    assert buttons(markup)[0][0][1] == f"ok:{action}"


def test_action_en_attente_repli_texte_brut_garde_les_boutons(server, context):
    action = "6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"
    server.routes[("POST", "/message")] = Response(
        200, {"reply": "x", "pending": [{"id": action, "summary": "Créer A"}]}
    )
    upd, msg = update()
    msg.refuse_html = True
    run(bot.relay(upd, context))
    text, markup = msg.replies[0]
    assert text == "Action proposée :\nCréer A"
    assert buttons(markup)[0][0][1] == f"ok:{action}"


# --- Tâches (phase B1) --------------------------------------------------------------
TASKS = [
    {"id": 3, "title": "Payer la facture", "due_at": "2026-10-20T23:59+01:00", "overdue": True,
     "priority": "haute", "status": "a_faire"},
    {"id": 5, "title": "Appeler Paul", "due_at": None, "overdue": False, "priority": "normale", "status": "a_faire"},
]


def test_taches_liste_avec_boutons(server, context):
    server.routes[("GET", "/tasks")] = Response(200, {"tasks": TASKS})
    upd, msg = update()
    run(bot.tasks_command(upd, context))
    text, markup = msg.replies[0]
    assert "<b>Tes tâches (2)</b>" in text and "1. Payer la facture (en retard · 20/10, priorité haute)" in text
    assert buttons(markup) == [
        [("Terminer 1", "tdone:3"), ("Annuler 1", "tcancel:3")],
        [("Terminer 2", "tdone:5"), ("Annuler 2", "tcancel:5")],
    ]
    assert server.calls == [("GET", "/tasks", 900, None)]


def test_taches_vide_sans_boutons(server, context):
    server.routes[("GET", "/tasks")] = Response(200, {"tasks": []})
    upd, msg = update()
    run(bot.tasks_command(upd, context))
    text, markup = msg.replies[0]
    assert "Aucune tâche" in text and markup is None


def test_taches_repli_texte_brut_garde_les_boutons(server, context):
    server.routes[("GET", "/tasks")] = Response(200, {"tasks": TASKS})
    upd, msg = update()
    msg.refuse_html = True
    run(bot.tasks_command(upd, context))
    text, markup = msg.replies[0]
    assert "<b>" not in text and "Payer la facture" in text and markup is not None


def test_taches_inconnu_refuse(server, context):
    server.routes[("GET", "/tasks")] = Response(403)
    upd, msg = update(user_id=555)
    run(bot.tasks_command(upd, context))
    assert msg.replies == [(bot.NO_ACCOUNT, None)]


def test_ajout_rapide(server, context):
    server.routes[("POST", "/tasks")] = Response(201, {"task": {"id": 7}})
    upd, msg = update()
    context.args = ["Appeler", "Paul", "|", "25/10", "|", "haute"]
    run(bot.add_task_command(upd, context))
    assert server.calls == [("POST", "/tasks", 900, {"text": "Appeler Paul | 25/10 | haute"})]
    assert msg.replies[0][0] == "Tâche ajoutée. /taches pour les voir."


def test_ajout_sans_texte_explique_la_syntaxe(server, context):
    upd, msg = update()
    run(bot.add_task_command(upd, context))
    assert server.calls == [] and "/tache" in msg.replies[0][0] and "|" in msg.replies[0][0]


@pytest.mark.parametrize("status", [400, 409])
def test_ajout_refuse_affiche_le_message_du_serveur(server, context, status):
    server.routes[("POST", "/tasks")] = Response(status, {"error": "Échéance non reconnue : « x »."})
    upd, msg = update()
    context.args = ["a", "|", "x"]
    run(bot.add_task_command(upd, context))
    assert msg.replies == [("Échéance non reconnue : « x ».", None)]


def test_ajout_serveur_indisponible(server, context):
    server.routes[("POST", "/tasks")] = bot.httpx.ConnectError("hors ligne")
    upd, msg = update()
    context.args = ["x"]
    run(bot.add_task_command(upd, context))
    assert msg.replies == [(bot.UNAVAILABLE, None)]


class ButtonQuery(Query):
    def __init__(self, data, user_id=900):
        super().__init__(data, user_id)
        self.message = Message()


@pytest.mark.parametrize("data, status, word", [("tdone:12", "terminee", "terminée"), ("tcancel:12", "annulee", "annulée")])
def test_boutons_terminer_et_annuler(server, context, data, status, word):
    server.routes[("PATCH", "/tasks/12")] = Response(200, {"task": {"id": 12}})
    query = ButtonQuery(data)
    run(bot.on_task_button(SimpleNamespace(callback_query=query), context))
    assert server.calls == [("PATCH", "/tasks/12", 900, {"status": status})]
    assert query.answered and f"Tâche #12 {word}" in query.message.replies[0][0]


def test_bouton_sur_une_tache_disparue(server, context):
    server.routes[("PATCH", "/tasks/12")] = Response(404)
    query = ButtonQuery("tdone:12")
    run(bot.on_task_button(SimpleNamespace(callback_query=query), context))
    assert "n'existe plus" in query.message.replies[0][0]


def test_bouton_d_un_compte_inconnu(server, context):
    server.routes[("PATCH", "/tasks/12")] = Response(403)
    query = ButtonQuery("tdone:12", user_id=555)
    run(bot.on_task_button(SimpleNamespace(callback_query=query), context))
    assert query.message.replies[0][0] == bot.NO_ACCOUNT and context.application.bot_data["unknown"].is_quiet(555)


def test_ajout_simple_ne_montre_pas_de_numero_interne(server, context):
    server.routes[("POST", "/tasks")] = Response(201, {"task": {"id": 57}})
    upd, msg = update()
    context.args = ["x"]
    run(bot.add_task_command(upd, context))
    assert "57" not in msg.replies[0][0]


def test_commandes_declarees_et_aide():
    assert {"taches", "tache", "memoire", "retiens", "journal", "briefing"} <= {c.command for c in bot.COMMANDS}
    for command in ("/taches", "/tache ", "/memoire", "/retiens", "/journal", "/briefing"):
        assert command in bot.HELP


# --- Mémoire, journal, rappels (phase B2) ----------------------------------------------
ITEMS = [{"id": 31, "content": "Je préfère les réunions le matin"}, {"id": 12, "content": "Projet : cabinet <Douala>"}]


class EditQuery(Query):
    """Bouton avec message modifiable ; garde aussi le mode d'analyse utilisé."""

    def __init__(self, data, user_id=900):
        super().__init__(data, user_id)
        self.modes = []
        self.refuse_html = False

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        if self.refuse_html and parse_mode is not None:
            raise bot.BadRequest("Can't parse entities")
        self.edits.append((text, reply_markup))
        self.modes.append(parse_mode)


def test_memoire_liste_avec_boutons(server, context):
    server.routes[("GET", "/memory")] = Response(200, {"items": ITEMS, "private": False})
    upd, msg = update()
    run(bot.memory_command(upd, context))
    text, markup = msg.replies[0]
    assert "<b>Ce que je retiens (2)</b>" in text and "1. Je préfère les réunions le matin" in text
    assert "cabinet &lt;Douala&gt;" in text  # le contenu est échappé
    assert buttons(markup) == [[("Oublier 1", "mdel:31")], [("Oublier 2", "mdel:12")], [("Tout effacer", "mall")]]


def test_memoire_vide(server, context):
    server.routes[("GET", "/memory")] = Response(200, {"items": [], "private": False})
    upd, msg = update()
    run(bot.memory_command(upd, context))
    assert "/retiens" in msg.replies[0][0] and msg.replies[0][1] is None


def test_memoire_effacer_demande_confirmation(server, context):
    server.routes[("GET", "/memory")] = Response(200, {"items": ITEMS, "private": False})
    upd, msg = update()
    context.args = ["effacer"]
    run(bot.memory_command(upd, context))
    text, markup = msg.replies[0]
    assert "TOUTE la mémoire" in text and buttons(markup) == [[("Oui, tout effacer", "mall_ok"), ("Annuler", "keep")]]
    assert [c[0] for c in server.calls] == ["GET"]  # rien n'est supprimé avant le clic


def test_memoire_effacer_quand_rien_a_effacer(server, context):
    server.routes[("GET", "/memory")] = Response(200, {"items": [], "private": False})
    upd, msg = update()
    context.args = ["effacer"]
    run(bot.memory_command(upd, context))
    assert "Je ne retiens rien" in msg.replies[0][0]


def test_memoire_inconnu_refuse(server, context):
    server.routes[("GET", "/memory")] = Response(403)
    upd, msg = update(user_id=555)
    run(bot.memory_command(upd, context))
    assert msg.replies == [(bot.NO_ACCOUNT, None)]


def test_retiens(server, context):
    server.routes[("POST", "/memory")] = Response(201, {"item": {"id": 3}})
    upd, msg = update()
    context.args = ["Je", "préfère", "le", "matin"]
    run(bot.remember_command(upd, context))
    assert server.calls == [("POST", "/memory", 900, {"text": "Je préfère le matin"})]
    assert msg.replies[0][0].startswith("C'est noté.")


def test_retiens_sans_texte(server, context):
    upd, msg = update()
    run(bot.remember_command(upd, context))
    assert server.calls == [] and "/retiens" in msg.replies[0][0]


@pytest.mark.parametrize("status", [400, 409])
def test_retiens_refuse_affiche_le_message_du_serveur(server, context, status):
    server.routes[("POST", "/memory")] = Response(status, {"error": "Le mode privé est actif."})
    upd, msg = update()
    context.args = ["x"]
    run(bot.remember_command(upd, context))
    assert msg.replies == [("Le mode privé est actif.", None)]


def test_retiens_serveur_indisponible(server, context):
    server.routes[("POST", "/memory")] = bot.httpx.ConnectError("hors ligne")
    upd, msg = update()
    context.args = ["x"]
    run(bot.remember_command(upd, context))
    assert msg.replies == [(bot.UNAVAILABLE, None)]


def test_oublier_un_element_met_la_liste_a_jour_sur_place(server, context):
    server.routes[("DELETE", "/memory/31")] = Response(200, {"status": "deleted"})
    server.routes[("GET", "/memory")] = Response(200, {"items": [ITEMS[1]], "private": False})
    query = EditQuery("mdel:31")
    run(bot.on_memory_button(SimpleNamespace(callback_query=query), context))
    assert [c[:2] for c in server.calls] == [("DELETE", "/memory/31"), ("GET", "/memory")]
    text, markup = query.edits[0]
    assert "(1)" in text and "Je préfère" not in text
    assert buttons(markup) == [[("Oublier 1", "mdel:12")], [("Tout effacer", "mall")]]


def test_oublier_un_element_deja_supprime_actualise_quand_meme(server, context):
    server.routes[("DELETE", "/memory/31")] = Response(404)
    server.routes[("GET", "/memory")] = Response(200, {"items": [], "private": False})
    query = EditQuery("mdel:31")
    run(bot.on_memory_button(SimpleNamespace(callback_query=query), context))
    assert "Je ne retiens rien" in query.edits[0][0]


def test_liste_de_memoire_repli_texte_brut(server, context):
    server.routes[("DELETE", "/memory/31")] = Response(200, {})
    server.routes[("GET", "/memory")] = Response(200, {"items": ITEMS[1:], "private": False})
    query = EditQuery("mdel:31")
    query.refuse_html = True
    run(bot.on_memory_button(SimpleNamespace(callback_query=query), context))
    assert "<b>" not in query.edits[0][0] and "cabinet <Douala>" in query.edits[0][0]


def test_tout_effacer_demande_puis_efface(server, context):
    server.routes[("DELETE", "/memory")] = Response(200, {"status": "deleted", "count": 2})
    ask = EditQuery("mall")
    run(bot.on_memory_button(SimpleNamespace(callback_query=ask), context))
    assert server.calls == [] and "TOUTE la mémoire" in ask.edits[0][0]
    confirm = EditQuery("mall_ok")
    run(bot.on_memory_button(SimpleNamespace(callback_query=confirm), context))
    assert server.calls == [("DELETE", "/memory", 900, {"confirm": "all"})]
    assert confirm.edits[0][0] == "Mémoire effacée."


def test_boutons_memoire_inconnu(server, context):
    server.routes[("DELETE", "/memory/31")] = Response(403)
    query = EditQuery("mdel:31", user_id=555)
    run(bot.on_memory_button(SimpleNamespace(callback_query=query), context))
    assert query.edits[0][0] == bot.NO_ACCOUNT and context.application.bot_data["unknown"].is_quiet(555)


def test_boutons_memoire_serveur_indisponible(server, context):
    server.routes[("DELETE", "/memory/31")] = bot.httpx.ConnectError("hors ligne")
    query = EditQuery("mdel:31")
    run(bot.on_memory_button(SimpleNamespace(callback_query=query), context))
    assert query.edits[0][0] == bot.UNAVAILABLE


def test_journal(server, context):
    server.routes[("GET", "/journal")] = Response(200, {"entries": [
        {"at": "2026-10-22T11:25+01:00", "text": "Exécutée : Créer la tâche « Appeler Paul »"}]})
    upd, msg = update()
    run(bot.journal_command(upd, context))
    assert "22/10 11:25 · Exécutée : Créer la tâche « Appeler Paul »" in msg.replies[0][0]


# --- Rappels ---------------------------------------------------------------------------
class Sender:
    def __init__(self):
        self.sent = []
        self.fail_for = set()
        self.refuse_html = False

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        if chat_id in self.fail_for:
            raise RuntimeError("utilisateur injoignable")
        if self.refuse_html and parse_mode is not None:
            raise bot.BadRequest("Can't parse entities")
        self.sent.append((chat_id, text, parse_mode, reply_markup))


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.posts = []

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class HttpResponse(Response):
    pass


def application(response):
    sender = Sender()
    return SimpleNamespace(bot=sender, bot_data={"http": FakeHttp(response)}), sender


REMINDERS = [
    {"telegram_id": 111, "id": 7, "title": "Appeler Paul", "due_at": "2026-10-22T11:25+01:00"},
    {"telegram_id": 222, "id": 8, "title": "Payer <la> facture", "due_at": "2026-10-22T11:30+01:00"},
]


def test_envoi_des_rappels_dus():
    app, sender = application(Response(200, {"reminders": REMINDERS}))
    assert run(bot.send_reminders(app)) == 2
    assert app.bot_data["http"].posts == [("http://orchestrator:5000/reminders/claim", {})]
    (chat1, text1, mode1, markup1), (chat2, text2, _, _) = sender.sent
    assert (chat1, text1, mode1) == (111, "<b>Rappel</b>\nAppeler Paul (prévu à 11:25)", bot.ParseMode.HTML)
    assert buttons(markup1) == [[("Terminer", "tdone:7")]]
    assert chat2 == 222 and "Payer &lt;la&gt; facture" in text2


def test_aucun_rappel_a_envoyer():
    app, sender = application(Response(200, {"reminders": []}))
    assert run(bot.send_reminders(app)) == 0 and sender.sent == []


def test_un_rappel_qui_echoue_ne_bloque_pas_les_suivants():
    app, sender = application(Response(200, {"reminders": REMINDERS}))
    sender.fail_for = {111}
    assert run(bot.send_reminders(app)) == 1
    assert [s[0] for s in sender.sent] == [222]


def test_rappel_repli_texte_brut():
    app, sender = application(Response(200, {"reminders": REMINDERS[:1]}))
    sender.refuse_html = True
    assert run(bot.send_reminders(app)) == 1
    assert sender.sent[0][1] == "**Rappel**\nAppeler Paul (prévu à 11:25)" and sender.sent[0][2] is None
    assert buttons(sender.sent[0][3]) == [[("Terminer", "tdone:7")]]


@pytest.mark.parametrize("response", [bot.httpx.ConnectError("hors ligne"), Response(500), Response(200, {})])
def test_orchestrateur_indisponible_pour_les_rappels(response):
    app, sender = application(response)
    assert run(bot.send_reminders(app)) == 0 and sender.sent == []


def test_la_boucle_des_rappels_survit_aux_erreurs(monkeypatch):
    calls = []

    async def boom(application):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("panne")
        raise asyncio.CancelledError

    monkeypatch.setattr(bot, "send_reminders", boom)
    monkeypatch.setattr(bot, "REMINDER_POLL_SECONDS", 0)
    with pytest.raises(asyncio.CancelledError):
        run(bot.reminder_loop(SimpleNamespace()))
    assert len(calls) == 3


def test_le_demarrage_lance_la_boucle_et_l_arret_la_coupe(monkeypatch):
    async def scenario():
        async def idle(application):
            await asyncio.sleep(3600)

        monkeypatch.setattr(bot, "reminder_loop", idle)

        class FakeBot:
            async def set_my_commands(self, commands):
                pass

        app = SimpleNamespace(bot=FakeBot(), bot_data={})
        await bot.on_start(app)
        task = app.bot_data["reminders"]
        assert not task.done()
        await bot.on_stop(app)
        await asyncio.sleep(0)
        return task.cancelled()

    assert asyncio.run(scenario())


# --- Briefing du matin (phase B3) -----------------------------------------------------
def test_etat_du_briefing_par_la_commande(server, context):
    server.routes[("GET", "/briefing")] = Response(200, {"enabled": True, "time": "07:30"})
    upd, msg = update()
    run(bot.briefing_cmd(upd, context))
    assert server.calls == [("GET", "/briefing", 900, None)]
    assert msg.replies[0][0].startswith("Briefing du matin activé à 07:30.")


def test_activer_le_briefing(server, context):
    server.routes[("POST", "/briefing")] = Response(200, {"enabled": True, "time": "08:00"})
    upd, msg = update()
    context.args = ["on"]
    run(bot.briefing_cmd(upd, context))
    assert server.calls == [("POST", "/briefing", 900, {"enabled": True})]
    assert "activé à 08:00" in msg.replies[0][0]


def test_regler_l_heure_du_briefing(server, context):
    server.routes[("POST", "/briefing")] = Response(200, {"enabled": True, "time": "07:30"})
    upd, msg = update()
    context.args = ["7h30"]
    run(bot.briefing_cmd(upd, context))
    assert server.calls == [("POST", "/briefing", 900, {"enabled": True, "time": "7h30"})]


def test_desactiver_le_briefing(server, context):
    server.routes[("POST", "/briefing")] = Response(200, {"enabled": False, "time": "08:00"})
    upd, msg = update()
    context.args = ["off"]
    run(bot.briefing_cmd(upd, context))
    assert "désactivé" in msg.replies[0][0]


def test_apercu_du_briefing(server, context):
    server.routes[("GET", "/briefing/preview")] = Response(200, {"text": "**Briefing du jeudi 22/10**\n\nRien de prévu aujourd'hui."})
    upd, msg = update()
    context.args = ["maintenant"]
    run(bot.briefing_cmd(upd, context))
    assert msg.replies[0][0] == "<b>Briefing du jeudi 22/10</b>\n\nRien de prévu aujourd'hui."


def test_briefing_commande_invalide(server, context):
    upd, msg = update()
    context.args = ["demain"]
    run(bot.briefing_cmd(upd, context))
    assert server.calls == [] and "Je n'ai pas compris" in msg.replies[0][0]


def test_briefing_heure_refusee_par_le_serveur(server, context):
    server.routes[("POST", "/briefing")] = Response(400, {"error": "Heure non reconnue."})
    upd, msg = update()
    context.args = ["25:00"]
    run(bot.briefing_cmd(upd, context))
    assert msg.replies == [("Heure non reconnue.", None)]


@pytest.mark.parametrize("status, expected", [(403, bot.NO_ACCOUNT), (500, bot.UNAVAILABLE)])
def test_briefing_erreurs_du_serveur(server, context, status, expected):
    server.routes[("POST", "/briefing")] = Response(status)
    upd, msg = update(user_id=555)
    context.args = ["on"]
    run(bot.briefing_cmd(upd, context))
    assert msg.replies == [(expected, None)]


def test_briefing_serveur_indisponible(server, context):
    server.routes[("POST", "/briefing")] = bot.httpx.ConnectError("hors ligne")
    upd, msg = update()
    context.args = ["on"]
    run(bot.briefing_cmd(upd, context))
    assert msg.replies == [(bot.UNAVAILABLE, None)]


BRIEFINGS = [
    {"telegram_id": 111, "text": "**Briefing du jeudi 22/10**\n\n**Aujourd'hui (1)**\n- 11:25 Appeler <Paul>"},
    {"telegram_id": 222, "text": "**Briefing du jeudi 22/10**\n\nRien de prévu aujourd'hui."},
]


def test_envoi_des_briefings():
    app, sender = application(Response(200, {"briefings": BRIEFINGS}))
    assert run(bot.send_briefings(app)) == 2
    assert app.bot_data["http"].posts == [("http://orchestrator:5000/briefings/claim", {})]
    (chat1, text1, mode1, markup1), (chat2, text2, _, _) = sender.sent
    assert chat1 == 111 and mode1 == bot.ParseMode.HTML and markup1 is None
    assert text1 == "<b>Briefing du jeudi 22/10</b>\n\n<b>Aujourd'hui (1)</b>\n• 11:25 Appeler &lt;Paul&gt;"
    assert chat2 == 222 and text2.endswith("Rien de prévu aujourd'hui.")


def test_briefing_tres_long_envoye_en_plusieurs_messages():
    long_text = "**Briefing**\n\n" + "\n\n".join(f"- ligne {i} " + "mot " * 200 for i in range(15))
    app, sender = application(Response(200, {"briefings": [{"telegram_id": 111, "text": long_text}]}))
    assert run(bot.send_briefings(app)) == 1
    assert len(sender.sent) > 1 and all(len(text) <= 4096 for _, text, _, _ in sender.sent)


def test_briefing_repli_texte_brut():
    app, sender = application(Response(200, {"briefings": BRIEFINGS[:1]}))
    sender.refuse_html = True
    assert run(bot.send_briefings(app)) == 1
    assert sender.sent[0][2] is None and "Appeler <Paul>" in sender.sent[0][1]


def test_un_briefing_qui_echoue_ne_bloque_pas_les_suivants():
    app, sender = application(Response(200, {"briefings": BRIEFINGS}))
    sender.fail_for = {111}
    assert run(bot.send_briefings(app)) == 1
    assert [s[0] for s in sender.sent] == [222]


@pytest.mark.parametrize("response", [bot.httpx.ConnectError("hors ligne"), Response(500), Response(200, {})])
def test_orchestrateur_indisponible_pour_les_briefings(response):
    app, sender = application(response)
    assert run(bot.send_briefings(app)) == 0 and sender.sent == []


def test_la_boucle_envoie_rappels_et_briefings(monkeypatch):
    seen = []

    async def reminders(application):
        seen.append("rappels")

    async def briefings(application):
        seen.append("briefings")
        raise asyncio.CancelledError

    monkeypatch.setattr(bot, "send_reminders", reminders)
    monkeypatch.setattr(bot, "send_briefings", briefings)
    monkeypatch.setattr(bot, "REMINDER_POLL_SECONDS", 0)
    with pytest.raises(asyncio.CancelledError):
        run(asyncio.wait_for(bot.reminder_loop(SimpleNamespace()), timeout=3))  # sans envoi des briefings : TimeoutError
    assert seen == ["rappels", "briefings"]
