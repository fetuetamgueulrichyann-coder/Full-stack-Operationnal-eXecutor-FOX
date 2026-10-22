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
    assert declared == {"nouveau", "historique", "effacer", "prive", "taches", "tache", "annuler", "aide"}
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
    assert commands == {"start", "aide", "help", "nouveau", "historique", "effacer", "prive", "taches", "tache", "annuler"}
    uuid_data = "ok:6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11"
    for data in ("open:9", "del:9", "delall", "delall_ok", "keep", "tdone:9", "tcancel:9", uuid_data):
        assert sum(1 for p in patterns if p.match(data)) == 1, data  # un seul gestionnaire
    for data in ("open:abc", "del:", "delall_ok2", "open:1; DROP", "ok:123", "tdone:", "tdone:1x", "tcancel:abc"):
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
    assert "<b>Tes tâches (2)</b>" in text and "<b>#3</b> Payer la facture (en retard · 20/10, priorité haute)" in text
    assert buttons(markup) == [
        [("Terminer #3", "tdone:3"), ("Annuler #3", "tcancel:3")],
        [("Terminer #5", "tdone:5"), ("Annuler #5", "tcancel:5")],
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
    assert "n°7" in msg.replies[0][0]


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


def test_commandes_declarees_et_aide():
    assert {"taches", "tache"} <= {c.command for c in bot.COMMANDS}
    assert "/taches" in bot.HELP and "/tache " in bot.HELP
