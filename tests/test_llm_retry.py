"""Tentatives automatiques et erreurs Gemini : ce qui est réessayé, ce qui ne l'est pas."""
import httpx
import pytest


@pytest.fixture
def gemini(agent, monkeypatch):
    """Remplace l'appel Gemini par une file de résultats ; compte les appels et les pauses."""
    state = {"calls": [], "sleeps": [], "queue": []}

    def fake_generate(model, contents, config):
        state["calls"].append(model)
        item = state["queue"].pop(0) if len(state["queue"]) > 1 else state["queue"][0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(agent.client.models, "generate_content", fake_generate)
    monkeypatch.setattr(agent.time, "sleep", lambda s: state["sleeps"].append(s))
    monkeypatch.setattr(agent, "LLM_MAX_ATTEMPTS", 4)
    monkeypatch.setattr(agent, "LLM_RETRY_BUDGET_SECONDS", 40)
    monkeypatch.setattr(agent, "LLM_BACKOFF_BASE", 1.0)
    monkeypatch.setattr(agent, "GEMINI_FALLBACK_MODEL", "")
    return state


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_codes_temporaires_sont_reessayes(agent, gemini, helpers, code):
    gemini["queue"] = [helpers.error(code), helpers.error(code), "OK"]
    result = agent._call_with_retry("m", [], None)
    assert result == "OK"
    assert len(gemini["calls"]) == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_autres_erreurs_gemini_ne_sont_pas_reessayees(agent, gemini, helpers, code):
    from google.genai import errors

    gemini["queue"] = [helpers.error(code)]
    with pytest.raises(errors.APIError) as info:
        agent._call_with_retry("m", [], None)
    assert info.value.code == code
    assert len(gemini["calls"]) == 1


def test_apres_le_maximum_de_tentatives_erreur_controlee(agent, gemini, helpers):
    gemini["queue"] = [helpers.error(503)]
    with pytest.raises(agent.LLMUnavailable):
        agent._call_with_retry("m", [], None)
    assert len(gemini["calls"]) == 4


def test_quota_429_persistant_donne_aussi_une_erreur_controlee(agent, gemini, helpers):
    gemini["queue"] = [helpers.error(429)]
    with pytest.raises(agent.LLMUnavailable):
        agent._call_with_retry("m", [], None)
    assert len(gemini["calls"]) == 4


def test_erreur_python_n_est_pas_masquee(agent, gemini):
    gemini["queue"] = [ValueError("bug interne")]
    with pytest.raises(ValueError, match="bug interne"):
        agent._call_with_retry("m", [], None)
    assert len(gemini["calls"]) == 1


def test_erreur_reseau_est_reessayee(agent, gemini):
    gemini["queue"] = [httpx.ConnectError("réseau coupé"), "OK"]
    assert agent._call_with_retry("m", [], None) == "OK"
    assert len(gemini["calls"]) == 2


def test_delais_progressifs(agent, gemini, helpers):
    gemini["queue"] = [helpers.error(503)]
    with pytest.raises(agent.LLMUnavailable):
        agent._call_with_retry("m", [], None)
    first, second, third = gemini["sleeps"]
    assert 1.0 <= first < 1.5
    assert 2.0 <= second < 2.5
    assert 4.0 <= third < 4.5


def test_budget_de_temps_limite_les_tentatives(agent, gemini, helpers, monkeypatch):
    monkeypatch.setattr(agent, "LLM_RETRY_BUDGET_SECONDS", 0)
    gemini["queue"] = [helpers.error(503)]
    with pytest.raises(agent.LLMUnavailable):
        agent._call_with_retry("m", [], None)
    assert len(gemini["calls"]) == 1
    assert gemini["sleeps"] == []


def test_modele_gemini_de_secours_au_premier_tour(agent, gemini, helpers, monkeypatch):
    monkeypatch.setattr(agent, "GEMINI_FALLBACK_MODEL", "modele-secours")
    monkeypatch.setattr(agent, "LLM_MAX_ATTEMPTS", 1)
    gemini["queue"] = [helpers.error(503), "OK"]
    result, model = agent.generate_with_retry([], None, agent.GEMINI_MODEL, can_switch=True)
    assert (result, model) == ("OK", "modele-secours")
    assert gemini["calls"] == [agent.GEMINI_MODEL, "modele-secours"]


def test_pas_de_bascule_de_modele_apres_un_outil(agent, gemini, helpers, monkeypatch):
    monkeypatch.setattr(agent, "GEMINI_FALLBACK_MODEL", "modele-secours")
    monkeypatch.setattr(agent, "LLM_MAX_ATTEMPTS", 1)
    gemini["queue"] = [helpers.error(503)]
    with pytest.raises(agent.LLMUnavailable):
        agent.generate_with_retry([], None, agent.GEMINI_MODEL, can_switch=False)
    assert gemini["calls"] == [agent.GEMINI_MODEL]
