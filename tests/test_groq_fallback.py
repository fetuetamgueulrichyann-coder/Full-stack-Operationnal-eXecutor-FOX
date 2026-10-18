"""Secours Groq : quand il s'active, quand il ne doit pas s'activer, et ce qu'il ne divulgue pas."""
import logging
from types import SimpleNamespace

import httpx
import pytest

SECRET_GROQ = "cle-groq-de-test"
SECRET_GEMINI = "cle-gemini-de-test"


@pytest.fixture
def setup(agent, db, helpers, monkeypatch):
    """Gemini toujours en 503, Groq simulé. Retourne l'état partagé."""
    state = SimpleNamespace(groq_calls=[], gemini_calls=0, groq_result=None)

    def gemini_down(model, contents, config):
        state.gemini_calls += 1
        raise helpers.error(503)

    class Reply:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_post(url, **kwargs):
        state.groq_calls.append({"url": url, **kwargs})
        result = state.groq_result
        if isinstance(result, Exception):
            raise result
        return result or Reply(
            {"choices": [{"message": {"content": "Réponse de Groq"}}], "usage": {"total_tokens": 42}}
        )

    state.Reply = Reply
    monkeypatch.setattr(agent.client.models, "generate_content", gemini_down)
    monkeypatch.setattr(agent.httpx, "post", fake_post)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "LLM_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(agent, "GEMINI_FALLBACK_MODEL", "")
    monkeypatch.setattr(agent, "GROQ_API_KEY", SECRET_GROQ)
    return state


def test_groq_prend_le_relais_si_gemini_reste_indisponible(agent, setup):
    reply, tokens, pending = agent.chat(1, "Bonjour")
    assert (reply, tokens, pending) == ("Réponse de Groq", 42, [])
    assert setup.gemini_calls == 2  # les tentatives ont eu lieu avant la bascule
    assert len(setup.groq_calls) == 1


def test_groq_ne_recoit_ni_outils_ni_acces_agenda(agent, setup, tools):
    agent.chat(1, "Quels sont mes rendez-vous demain ?")
    payload = setup.groq_calls[0]["json"]
    assert "tools" not in payload and "functions" not in payload
    system = payload["messages"][0]["content"]
    assert "aucun outil" in system.lower()


def test_la_cle_groq_part_dans_l_en_tete_pas_dans_le_corps(agent, setup):
    agent.chat(1, "Bonjour")
    call = setup.groq_calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {SECRET_GROQ}"
    assert SECRET_GROQ not in str(call["json"])


def test_historique_envoye_a_groq_est_limite_et_raccourci(agent, db, setup, monkeypatch):
    monkeypatch.setattr(agent, "GROQ_HISTORY_LIMIT", 2)
    monkeypatch.setattr(agent, "HISTORY_MAX_CHARS", 100)
    for i in range(6):
        db.save_exchange(1, f"question {i} " + "x" * 500, f"réponse {i}")
    agent.chat(1, "nouvelle question")
    messages = setup.groq_calls[0]["json"]["messages"]
    # système + 2 échanges (4 messages) + la nouvelle question
    assert len(messages) == 1 + 4 + 1
    assert all(len(m["content"]) <= 110 for m in messages[1:-1])
    assert messages[-1]["content"] == "nouvelle question"


def test_pas_de_groq_sans_cle(agent, setup, monkeypatch):
    monkeypatch.setattr(agent, "GROQ_API_KEY", "")
    with pytest.raises(agent.LLMUnavailable):
        agent.chat(1, "Bonjour")
    assert setup.groq_calls == []


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("délai dépassé"),
        httpx.HTTPStatusError(
            "404", request=httpx.Request("POST", "https://x"), response=httpx.Response(404)
        ),
    ],
)
def test_si_groq_echoue_aussi_erreur_controlee_sans_fuite_de_cle(agent, setup, failure, caplog):
    setup.groq_result = failure
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(agent.LLMUnavailable):
            agent.chat(1, "Bonjour")
    logs = caplog.text
    assert SECRET_GROQ not in logs and SECRET_GEMINI not in logs


def test_reponse_groq_vide_est_une_erreur_controlee(agent, setup):
    setup.groq_result = setup.Reply({"choices": [{"message": {"content": "  "}}]})
    with pytest.raises(agent.LLMUnavailable):
        agent.chat(1, "Bonjour")


def test_reponse_groq_mal_formee_est_une_erreur_controlee(agent, setup):
    setup.groq_result = setup.Reply({"inattendu": True})
    with pytest.raises(agent.LLMUnavailable):
        agent.chat(1, "Bonjour")


def test_pas_de_groq_apres_un_appel_d_outil(agent, db, tools, helpers, setup, monkeypatch):
    """Après un outil, l'historique appartient à Gemini : pas de bascule (l'agenda reste sur Gemini)."""
    outil = tools.Tool(
        name="outil_lecture_test",
        description="test",
        parameters=None,
        classification=tools.READ,
        run=lambda: ["rien"],
    )
    tools.register(outil)
    reponses = [helpers.response(calls=[helpers.call("outil_lecture_test")])]

    def first_then_down(model, contents, config):
        if reponses:
            return reponses.pop()
        raise helpers.error(503)

    monkeypatch.setattr(agent.client.models, "generate_content", first_then_down)
    try:
        with pytest.raises(agent.LLMUnavailable):
            agent.chat(1, "Lis ça")
    finally:
        tools.REGISTRY.pop("outil_lecture_test", None)
    assert setup.groq_calls == []
