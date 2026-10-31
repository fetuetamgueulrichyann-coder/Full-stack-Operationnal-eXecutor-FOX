"""Rôles : outils exposés selon le rôle, routage des fournisseurs d'IA (propriétaire / testeurs)."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def registry(tools):
    ran = []
    registered = {
        "t_commun": tools.Tool(
            name="t_commun", description="pour tous", parameters=None,
            classification=tools.READ, run=lambda: ran.append("commun") or "ok",
        ),
        "t_proprio": tools.Tool(
            name="t_proprio", description="propriétaire", parameters=None,
            classification=tools.WRITE, run=lambda: ran.append("proprio") or {"id": 1},
            describe=lambda a: "Action du propriétaire", done=lambda r: "Fait.",
            roles=("owner",),
        ),
        "t_testeur": tools.Tool(
            name="t_testeur", description="testeur", parameters=None,
            classification=tools.READ, run=lambda: ran.append("testeur") or "ok",
            roles=("tester",),
        ),
    }
    for tool in registered.values():
        tools.register(tool)
    yield ran
    for name in registered:
        tools.REGISTRY.pop(name, None)


def names(tool_list):
    return {t.name for t in tool_list}


def test_outils_exposes_selon_le_role(agent, registry):
    owner = names(agent.exposed_tools("owner"))
    tester = names(agent.exposed_tools("tester"))
    assert {"t_commun", "t_proprio"} <= owner and "t_testeur" not in owner
    assert {"t_commun", "t_testeur"} <= tester and "t_proprio" not in tester
    assert "annuler_derniere_action" not in owner | tester  # outil interne, jamais exposé


def test_les_outils_google_agenda_sont_reserves_au_proprietaire(monkeypatch):
    """Vérifié sur la définition réelle de register_calendar_tools()."""
    import tools as tools_module

    before = dict(tools_module.REGISTRY)
    try:
        tools_module.register_calendar_tools()
        calendar = {
            n: t for n, t in tools_module.REGISTRY.items()
            if n in ("lire_agenda", "creer_evenement", "modifier_evenement", "supprimer_evenement")
        }
        assert len(calendar) == 4
        for tool in calendar.values():
            assert tool.allowed_for("owner") and not tool.allowed_for("tester"), tool.name
    finally:
        tools_module.REGISTRY.clear()
        tools_module.REGISTRY.update(before)


def test_configuration_gemini_ne_declare_que_les_outils_du_role(agent, registry):
    config = agent.build_config("tester")
    declared = [d.name for t in (config.tools or []) for d in t.function_declarations]
    assert "t_proprio" not in declared and "t_testeur" in declared
    owner_declared = [d.name for t in (agent.build_config("owner").tools or []) for d in t.function_declarations]
    assert "t_proprio" in owner_declared


def test_prompt_sans_outil_quand_le_role_n_en_a_aucun(agent, tools):
    saved = dict(tools.REGISTRY)
    try:
        for name in [n for n, t in tools.REGISTRY.items() if t.expose]:
            tools.REGISTRY.pop(name)
        assert agent.PROMPT_WITHOUT_TOOLS in agent.system_prompt(role="tester")
    finally:
        tools.REGISTRY.update(saved)


# --- Routage des fournisseurs --------------------------------------------------
@pytest.fixture
def providers(agent, monkeypatch):
    state = SimpleNamespace(gemini=0, groq=[])

    def gemini(*args, **kwargs):
        state.gemini += 1
        raise AssertionError("Gemini ne doit pas être appelé pour ce rôle")

    def groq(user_id, text, *_):
        state.groq.append((user_id, text))
        return "réponse Groq", 12, []

    monkeypatch.setattr(agent, "generate_with_retry", gemini)
    monkeypatch.setattr(agent, "groq_chat", groq)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "cle-groq-de-test")
    return state


def test_un_testeur_passe_uniquement_par_groq(agent, providers):
    assert agent.chat(5, "Bonjour", "tester") == ("réponse Groq", 12, [])
    assert providers.gemini == 0 and providers.groq == [(5, "Bonjour")]


def test_un_testeur_n_est_jamais_bascule_vers_gemini_si_groq_est_indisponible(agent, providers, monkeypatch):
    def down(user_id, text, *_):
        raise agent.LLMUnavailable("Groq indisponible")

    monkeypatch.setattr(agent, "groq_chat", down)
    with pytest.raises(agent.LLMUnavailable):
        agent.chat(5, "Bonjour", "tester")
    assert providers.gemini == 0


def test_un_testeur_sans_cle_groq_recoit_une_erreur_controlee(agent, providers, monkeypatch):
    monkeypatch.setattr(agent, "GROQ_API_KEY", "")
    with pytest.raises(agent.LLMUnavailable):
        agent.chat(5, "Bonjour", "tester")
    assert providers.gemini == 0 and providers.groq == []


def test_le_propriétaire_utilise_gemini(agent, gemini_ok, helpers):
    reply, tokens, pending = agent.chat(1, "Bonjour", "owner")
    assert reply == "Réponse de test" and gemini_ok == ["modele-principal-test"]


@pytest.fixture
def gemini_ok(agent, helpers, monkeypatch):
    used = []

    def fake(model, contents, config):
        used.append(model)
        return helpers.response("Réponse de test")

    monkeypatch.setattr(agent.client.models, "generate_content", fake)
    return used


# --- Contrôle à l'exécution ----------------------------------------------------
def test_un_testeur_ne_peut_pas_executer_un_outil_reserve_au_proprietaire(agent, db, registry, make_user):
    tester = make_user()
    forged = db.create_pending(tester["id"], "t_proprio", {}, "Action du propriétaire", 10)
    reply = agent.resolve_action(tester["id"], forged, True, "tester")
    assert reply == "Cet outil n'est plus disponible."
    assert registry == []  # l'outil n'a pas tourné
    with db.cursor() as cur:
        cur.execute("SELECT status FROM pending_actions WHERE id = %s", (forged,))
        assert cur.fetchone()[0] == "failed"


def test_le_proprietaire_execute_son_outil(agent, db, registry, make_owner):
    owner = make_owner()
    action = db.create_pending(owner["id"], "t_proprio", {}, "Action du propriétaire", 10)
    assert agent.resolve_action(owner["id"], action, True, "owner") == "Fait."
    assert registry == ["proprio"]


def test_outil_non_autorise_demande_par_le_modele_est_refuse_et_journalise(
    agent, db, registry, gemini_script, helpers
):
    gemini_script.append(helpers.response(calls=[helpers.call("t_testeur")]))
    gemini_script.append(helpers.response("Terminé"))
    reply, _, _ = agent.chat(1, "fais ça", "owner")  # t_testeur n'est pas autorisé au propriétaire
    assert registry == []
    with db.cursor() as cur:
        cur.execute("SELECT event, tool FROM audit_log")
        assert ("denied", "t_testeur") in cur.fetchall()


@pytest.fixture
def gemini_script(agent, monkeypatch):
    queue = []
    monkeypatch.setattr(
        agent.client.models, "generate_content", lambda model, contents, config: queue.pop(0)
    )
    return queue
