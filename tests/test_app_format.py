"""Branchement du nettoyage des réponses dans l'API et du style dans le prompt."""
import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
TELEGRAM_ID = 4242


@pytest.fixture
def owner(make_owner):
    return make_owner(TELEGRAM_ID)


@pytest.fixture
def client(app_module, owner, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "MAX_TOKENS_PER_DAY", 10**9)
    monkeypatch.setattr(app_module, "OWNER_MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "OWNER_MAX_TOKENS_PER_DAY", 10**9)
    return app_module.app.test_client()


def model_says(agent, monkeypatch, reply, pending=None):
    monkeypatch.setattr(agent, "chat", lambda *a, **k: (reply, 5, pending or []))


def say(client, text="Calcule"):
    return client.post("/message", json={"telegram_id": TELEGRAM_ID, "text": text}, headers=HEADERS)


def test_le_latex_est_nettoye_avant_d_etre_renvoye_et_enregistre(client, agent, db, owner, monkeypatch):
    model_says(agent, monkeypatch, "  L'aire vaut $\\pi r^2$ et \\sqrt{\\pi}.  \n\n\n\nFin  ")
    reply = say(client).get_json()["reply"]
    assert reply == "L'aire vaut π r² et √π.\n\nFin"
    assert db.load_history(owner["id"], 5) == [("Calcule", reply)]  # l'historique garde le texte propre


def test_le_code_de_la_reponse_reste_intact(client, agent, monkeypatch):
    reply_in = "Exemple :\n```python\nx = a^2\n```"
    model_says(agent, monkeypatch, reply_in)
    assert say(client).get_json()["reply"] == reply_in


def test_interrupteur_de_nettoyage(client, agent, monkeypatch):
    import formatting

    monkeypatch.setattr(formatting, "FORMAT_REPLIES", False)
    model_says(agent, monkeypatch, "brut \\sqrt{\\pi}  ")
    assert say(client).get_json()["reply"] == "brut \\sqrt{\\pi}  "


def test_reponse_vide_reste_non_enregistree(client, agent, db, owner, monkeypatch):
    model_says(agent, monkeypatch, agent.EMPTY_REPLY)
    assert say(client).get_json()["reply"] == agent.EMPTY_REPLY
    assert db.load_history(owner["id"], 5) == []


def test_reponse_faite_uniquement_de_blancs_devient_reponse_vide(client, agent, db, owner, monkeypatch):
    model_says(agent, monkeypatch, "   \n\n  ")
    assert say(client).get_json()["reply"] == agent.EMPTY_REPLY
    assert db.load_history(owner["id"], 5) == []


def test_actions_en_attente_une_information_par_ligne(client, agent, monkeypatch):
    summary = "Créer l'événement « Réunion »\nQuand : le 12/10/2026 de 09:00 à 10:00\nLieu : Douala"
    model_says(agent, monkeypatch, "x", pending=[{"id": "a1", "summary": summary}])
    body = say(client).get_json()
    assert body["pending"][0]["summary"] == summary  # le résumé brut reste intact pour le bot
    assert body["reply"] == (
        "Action(s) en attente de confirmation :\n"
        "- Créer l'événement « Réunion »\n  Quand : le 12/10/2026 de 09:00 à 10:00\n  Lieu : Douala"
    )


# --- Style demandé au modèle -------------------------------------------------------------
@pytest.mark.parametrize("with_tools, role", [(True, "owner"), (False, "tester")])
def test_le_style_est_demande_au_modele_pour_tous_les_roles(agent, with_tools, role):
    prompt = agent.system_prompt(with_tools=with_tools, role=role)
    for consigne in ("LaTeX", "emojis", "tableau", "sens", "√π"):
        assert consigne in prompt, consigne
    assert "français" in prompt and "anglais" in prompt  # la règle de langue reste en place


def test_le_prompt_reste_court_pour_economiser_les_tokens(agent):
    # Un testeur n'a que 15 000 tokens par jour : le prompt est envoyé à chaque appel.
    assert len(agent.SYSTEM_PROMPT) < 1100


def test_le_prompt_du_secours_groq_contient_le_meme_style(agent, monkeypatch):
    seen = {}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}}

    def fake_post(url, headers, json, timeout):
        seen["messages"] = json["messages"]
        return Reply()

    monkeypatch.setattr(agent.httpx, "post", fake_post)
    agent.groq_reply(1, "Bonjour")
    assert "LaTeX" in seen["messages"][0]["content"]
