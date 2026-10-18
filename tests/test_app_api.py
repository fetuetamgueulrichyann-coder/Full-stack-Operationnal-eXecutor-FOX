"""API Flask de l'orchestrateur : validation, contrôle d'accès, erreurs contrôlées."""
import logging
import uuid

import pytest


@pytest.fixture
def client(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "ALLOWED_USER_ID", "")
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "MAX_TOKENS_PER_DAY", 10**9)
    return app_module.app.test_client()


def post(client, text="Bonjour", user_id=1):
    return client.post("/message", json={"user_id": user_id, "text": text})


def stub_chat(monkeypatch, agent, result=None, error=None):
    def fake(user_id, text):
        if error:
            raise error
        return result

    monkeypatch.setattr(agent, "chat", fake)


def test_sante(client):
    assert client.get("/health").get_json() == {"status": "ok"}


@pytest.mark.parametrize("body", [{}, {"text": "x"}, {"user_id": "abc", "text": "x"}])
def test_user_id_invalide(client, body):
    assert client.post("/message", json=body).status_code == 400


def test_texte_vide_et_trop_long(client):
    assert post(client, "   ").status_code == 400
    assert post(client, "x" * 4001).status_code == 413


def test_utilisateur_non_autorise(client, app_module, db, monkeypatch):
    monkeypatch.setattr(app_module, "ALLOWED_USER_ID", "42")
    assert post(client, user_id=7).status_code == 403
    with db.cursor() as cur:
        cur.execute("SELECT event, user_id FROM audit_log")
        assert cur.fetchall() == [("denied", 7)]


def test_message_normal_est_enregistre(client, agent, db, monkeypatch):
    stub_chat(monkeypatch, agent, ("Salut !", 30, []))
    response = post(client, "Bonjour")
    assert response.status_code == 200 and response.get_json()["reply"] == "Salut !"
    assert db.load_history(1, 5) == [("Bonjour", "Salut !")]
    with db.cursor() as cur:
        cur.execute("SELECT calls, tokens FROM api_usage")
        assert cur.fetchone() == (1, 30)


def test_action_en_attente(client, agent, monkeypatch):
    stub_chat(monkeypatch, agent, ("x", 5, [{"id": "a1", "summary": "Créer Réunion"}]))
    body = post(client).get_json()
    assert body["pending"] == [{"id": "a1", "summary": "Créer Réunion"}]
    assert "Créer Réunion" in body["reply"]


def test_modele_indisponible_donne_503(client, agent, monkeypatch):
    stub_chat(monkeypatch, agent, error=agent.LLMUnavailable("503"))
    response = post(client)
    assert response.status_code == 503 and response.get_json()["error"] == "modele indisponible"


def test_erreur_gemini_non_temporaire_donne_502_avec_code(client, agent, helpers, monkeypatch):
    stub_chat(monkeypatch, agent, error=helpers.error(404))
    response = post(client)
    assert response.status_code == 502 and response.get_json()["code"] == 404


def test_erreur_python_donne_502_et_reste_journalisee(client, agent, monkeypatch, caplog):
    stub_chat(monkeypatch, agent, error=ZeroDivisionError("bug"))
    with caplog.at_level(logging.ERROR):
        response = post(client)
    assert response.status_code == 502 and response.get_json()["error"] == "service indisponible"
    assert "ZeroDivisionError" in caplog.text  # l'erreur n'est pas masquée dans les journaux


def test_plafond_journalier_donne_429(client, app_module, agent, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1)
    stub_chat(monkeypatch, agent, ("ok", 1, []))
    assert post(client).status_code == 200
    assert post(client).status_code == 429


def test_reponse_vide_n_est_pas_enregistree(client, agent, db, monkeypatch):
    stub_chat(monkeypatch, agent, (agent.EMPTY_REPLY, 3, []))
    post(client, "Salut")
    assert db.load_history(1, 5) == []


def test_demande_d_annulation_ne_consomme_pas_le_modele(client, agent, db, monkeypatch):
    stub_chat(monkeypatch, agent, error=AssertionError("le modèle ne doit pas être appelé"))
    response = post(client, "annule ça")
    assert response.status_code == 200
    assert response.get_json()["reply"] == "Aucune action récente à annuler."


def test_identifiant_d_action_invalide(client):
    response = client.post("/actions/pas-un-uuid/confirm", json={"user_id": 1})
    assert response.status_code == 400


def test_confirmation_d_une_action_reelle(client, agent, db, tools):
    ran = []
    tools.register(
        tools.Tool(
            name="t_api", description="", parameters=None, classification=tools.WRITE,
            run=lambda: ran.append(1) or {}, describe=lambda a: "Faire", done=lambda r: "Fait.",
        )
    )
    try:
        action = db.create_pending(1, "t_api", {}, "Faire", 10)
        assert client.post(f"/actions/{action}/confirm", json={"user_id": 2}).get_json()[
            "reply"
        ] == "Cette action n'est plus en attente."
        assert ran == []
        assert client.post(f"/actions/{action}/confirm", json={"user_id": 1}).get_json()[
            "reply"
        ] == "Fait."
        assert ran == [1]
    finally:
        tools.REGISTRY.pop("t_api", None)


def test_confirmation_refusee_pour_un_utilisateur_non_autorise(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "ALLOWED_USER_ID", "42")
    response = client.post(f"/actions/{uuid.uuid4()}/confirm", json={"user_id": 7})
    assert response.status_code == 403
