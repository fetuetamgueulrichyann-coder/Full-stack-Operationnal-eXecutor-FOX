"""GET /chats/<id>/messages : relire une conversation dans l'interface web."""
import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777002


@pytest.fixture
def client(app_module, make_owner, agent, monkeypatch):
    make_owner(OWNER)
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "MAX_TOKENS_PER_DAY", 10**9)
    monkeypatch.setattr(app_module, "OWNER_MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "OWNER_MAX_TOKENS_PER_DAY", 10**9)
    monkeypatch.setattr(agent, "chat", lambda user_id, text, role="owner", **_: (f"écho : {text}", 3, []))
    return app_module.app.test_client()


def say(client, text, telegram_id=OWNER, **extra):
    return client.post("/message", json={"telegram_id": telegram_id, "text": text, **extra}, headers=HEADERS)


def messages(client, chat_id, telegram_id=OWNER):
    return client.get(f"/chats/{chat_id}/messages", query_string={"telegram_id": telegram_id}, headers=HEADERS)


def test_les_messages_reviennent_dans_l_ordre(client):
    chat_id = say(client, "premier").get_json()["chat_id"]
    say(client, "second", chat_id=chat_id)
    response = messages(client, chat_id)
    assert response.status_code == 200
    data = response.get_json()
    assert data["chat_id"] == chat_id
    assert [(m["role"], m["text"]) for m in data["messages"]] == [
        ("user", "premier"), ("assistant", "écho : premier"),
        ("user", "second"), ("assistant", "écho : second"),
    ]
    assert len({m["id"] for m in data["messages"]}) == 4
    assert all("T" in m["at"] for m in data["messages"])


def test_conversation_d_un_autre_utilisateur_introuvable(client, make_owner, db):
    chat_id = say(client, "secret").get_json()["chat_id"]
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s)", (OTHER,))
    r = messages(client, chat_id, telegram_id=OTHER)
    assert r.status_code == 404 and r.get_json()["code"] == "not_found"


def test_conversation_inconnue_donne_404(client):
    assert messages(client, 999999).status_code == 404


def test_exige_une_identite(client):
    assert client.get("/chats/1/messages").status_code == 401


def test_conversation_supprimee_n_est_plus_lisible(client):
    chat_id = say(client, "bonjour").get_json()["chat_id"]
    client.delete(f"/chats/{chat_id}", json={"telegram_id": OWNER}, headers=HEADERS)
    assert messages(client, chat_id).status_code == 404
