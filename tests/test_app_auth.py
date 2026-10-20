"""Authentification de l'API : sessions web, jeton de service, isolation entre utilisateurs."""
import pytest

TOKEN = "jeton-de-service-de-test"
PASSWORD = "mot-de-passe-solide"


@pytest.fixture
def app_client(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "MAX_TOKENS_PER_DAY", 10**9)
    return app_module.app


@pytest.fixture
def client(app_client):
    return app_client.test_client()


@pytest.fixture
def seen(agent, monkeypatch):
    """Remplace l'agent par un double qui note qui l'appelle."""
    calls = []

    def fake(user_id, text, role="owner", **_):
        calls.append({"user_id": user_id, "role": role, "text": text})
        return f"écho : {text}", 5, []

    monkeypatch.setattr(agent, "chat", fake)
    return calls


def signup(client, accounts, username="marie", password=PASSWORD):
    code = accounts.create_invitation(days=1)
    return client.post(
        "/auth/register", json={"code": code, "username": username, "password": password}
    )


def say(client, text="Bonjour", **extra):
    return client.post("/message", json={"text": text, **extra})


# --- Accès sans identité -----------------------------------------------------
def test_sans_session_rien_n_est_accessible(client):
    assert say(client).status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert client.post(
        "/actions/6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11/confirm", json={}
    ).status_code == 401


def test_post_non_json_refuse(client):
    response = client.post("/auth/login", data={"username": "a", "password": "b"})
    assert response.status_code == 415


# --- Inscription, connexion, déconnexion --------------------------------------
def test_inscription_ouvre_une_session_avec_cookie_protege(client, accounts):
    response = signup(client, accounts)
    assert response.status_code == 201
    assert response.get_json()["user"]["role"] == "tester"
    cookie = response.headers["Set-Cookie"]
    assert "fox_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    me = client.get("/auth/me").get_json()["user"]
    assert me["username"] == "marie"
    assert "password" not in str(me).lower()


def test_cookie_secure_si_active(app_module, client, accounts, monkeypatch):
    monkeypatch.setattr(app_module, "COOKIE_SECURE", True)
    assert "Secure" in signup(client, accounts).headers["Set-Cookie"]


@pytest.mark.parametrize(
    "payload, status",
    [
        ({"code": "FOX-AAAA-BBBB-CCCC", "username": "paul", "password": PASSWORD}, 400),
        ({"username": "paul", "password": PASSWORD}, 400),
    ],
)
def test_inscription_refusee_sans_code_valide(client, payload, status):
    assert client.post("/auth/register", json=payload).status_code == status


def test_inscription_refusee_mot_de_passe_faible_ou_nom_pris(client, accounts, app_client):
    code = accounts.create_invitation(days=1, max_uses=3)
    weak = client.post("/auth/register", json={"code": code, "username": "paul", "password": "court"})
    assert weak.status_code == 400 and weak.get_json()["code"] == "password_weak"
    signup(client, accounts, "doublon")
    other = app_client.test_client()
    taken = other.post(
        "/auth/register", json={"code": code, "username": "DOUBLON", "password": PASSWORD}
    )
    assert taken.status_code == 409


def test_essais_de_codes_limites_par_adresse(client, accounts):
    for _ in range(accounts.MAX_FAILED_REGISTER_PER_IP):
        client.post(
            "/auth/register",
            json={"code": "FOX-AAAA-BBBB-CCCC", "username": "paul", "password": PASSWORD},
        )
    valid = accounts.create_invitation(days=1)
    blocked = client.post(
        "/auth/register", json={"code": valid, "username": "paul", "password": PASSWORD}
    )
    assert blocked.status_code == 429


def test_connexion_puis_deconnexion(app_client, client, accounts):
    signup(client, accounts, "luc")
    other = app_client.test_client()
    assert other.post("/auth/login", json={"username": "luc", "password": PASSWORD}).status_code == 200
    assert other.get("/auth/me").status_code == 200
    stolen = other.get_cookie("fox_session").value

    assert other.post("/auth/logout", json={}).status_code == 200
    assert other.get("/auth/me").status_code == 401

    replay = app_client.test_client()
    replay.set_cookie("fox_session", stolen)
    assert replay.get("/auth/me").status_code == 401  # un cookie volé avant la déconnexion est mort


def test_connexion_echec_message_identique_et_journal_sans_mot_de_passe(client, accounts, db):
    signup(client, accounts, "nina")
    bad = client.post("/auth/login", json={"username": "nina", "password": "mauvais-mot-de-passe"})
    ghost = client.post("/auth/login", json={"username": "fantome", "password": "mauvais-mot-de-passe"})
    assert bad.status_code == ghost.status_code == 401
    assert bad.get_json() == ghost.get_json()
    with db.cursor() as cur:
        cur.execute("SELECT string_agg(details::text, ' ') FROM audit_log WHERE event = 'login_failed'")
        logged = cur.fetchone()[0]
    assert "mauvais-mot-de-passe" not in logged


def test_trop_d_echecs_de_connexion_donne_429(app_client, client, accounts):
    signup(client, accounts, "otto")
    attacker = app_client.test_client()
    for _ in range(accounts.MAX_FAILED_PER_USERNAME):
        attacker.post("/auth/login", json={"username": "otto", "password": "mauvais-mot-de-passe"})
    response = attacker.post("/auth/login", json={"username": "otto", "password": PASSWORD})
    assert response.status_code == 429


def test_compte_desactive_perd_l_acces_immediatement(client, accounts, seen):
    signup(client, accounts, "pia")
    assert say(client).status_code == 200
    accounts.set_disabled("pia", True)
    assert say(client).status_code == 401


# --- L'identité vient du serveur, jamais du client ----------------------------
def test_user_id_envoye_par_le_client_est_ignore(client, accounts, db, make_owner, seen):
    owner = make_owner(telegram_id=4242)
    signup(client, accounts, "quentin")
    me = client.get("/auth/me").get_json()["user"]
    response = say(client, "je me fais passer pour le propriétaire", user_id=owner["id"], telegram_id=4242)
    assert response.status_code == 200
    assert seen == [{"user_id": me["id"], "role": "tester", "text": "je me fais passer pour le propriétaire"}]
    assert db.load_history(owner["id"], 10) == []
    assert len(db.load_history(me["id"], 10)) == 1


def test_jeton_de_service_invalide_ou_absent_de_la_configuration(client, make_owner, db, app_module, monkeypatch):
    make_owner(telegram_id=4242)
    body = {"telegram_id": 4242, "text": "salut"}
    wrong = client.post("/message", json=body, headers={"X-Fox-Service-Token": "faux"})
    assert wrong.status_code == 401
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", "")
    empty = client.post("/message", json=body, headers={"X-Fox-Service-Token": ""})
    assert empty.status_code == 401  # un jeton vide ne doit jamais ouvrir l'accès
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_log WHERE event = 'denied'")
        assert cur.fetchone()[0] == 2


def test_jeton_de_service_faux_n_est_pas_rattrape_par_un_cookie_valide(client, accounts, seen):
    signup(client, accounts, "rita")
    response = client.post(
        "/message", json={"text": "salut"}, headers={"X-Fox-Service-Token": "faux"}
    )
    assert response.status_code == 401 and seen == []


def test_jeton_de_service_avec_le_compte_telegram_du_proprietaire(client, make_owner, seen):
    owner = make_owner(telegram_id=4242)
    response = client.post(
        "/message", json={"telegram_id": 4242, "text": "salut"}, headers={"X-Fox-Service-Token": TOKEN}
    )
    assert response.status_code == 200
    assert seen[0]["user_id"] == owner["id"] and seen[0]["role"] == "owner"


def test_un_compte_non_lie_a_telegram_n_est_pas_joignable_par_le_bot(client, accounts, seen):
    signup(client, accounts, "sam")
    bot = client.application.test_client()
    response = bot.post(
        "/message", json={"telegram_id": 31337, "text": "salut"}, headers={"X-Fox-Service-Token": TOKEN}
    )
    assert response.status_code == 403 and seen == []


# --- Isolation entre utilisateurs --------------------------------------------
def test_deux_utilisateurs_ont_des_historiques_separes(app_client, accounts, db, seen):
    a, b = app_client.test_client(), app_client.test_client()
    signup(a, accounts, "alice-a")
    signup(b, accounts, "bruno-b")
    say(a, "secret d'Alice")
    say(b, "secret de Bruno")
    id_a = a.get("/auth/me").get_json()["user"]["id"]
    id_b = b.get("/auth/me").get_json()["user"]["id"]
    assert db.load_history(id_a, 10) == [("secret d'Alice", "écho : secret d'Alice")]
    assert db.load_history(id_b, 10) == [("secret de Bruno", "écho : secret de Bruno")]


def test_un_utilisateur_ne_peut_pas_confirmer_l_action_d_un_autre(app_client, accounts, db, tools):
    ran = []
    tools.register(
        tools.Tool(
            name="t_iso", description="", parameters=None, classification=tools.WRITE,
            run=lambda: ran.append(1) or {}, describe=lambda a: "Faire", done=lambda r: "Fait.",
        )
    )
    try:
        a, b = app_client.test_client(), app_client.test_client()
        signup(a, accounts, "alice-c")
        signup(b, accounts, "bruno-c")
        id_a = a.get("/auth/me").get_json()["user"]["id"]
        action = db.create_pending(id_a, "t_iso", {}, "Faire", 10)

        stolen = b.post(f"/actions/{action}/confirm", json={})
        assert stolen.get_json()["reply"] == "Cette action n'est plus en attente."
        assert ran == []
        assert a.post(f"/actions/{action}/confirm", json={}).get_json()["reply"] == "Fait."
        assert ran == [1]
    finally:
        tools.REGISTRY.pop("t_iso", None)


# --- Quotas par utilisateur via l'API ----------------------------------------
def test_le_quota_d_un_testeur_n_affecte_ni_les_autres_ni_le_proprietaire(
    app_client, accounts, make_owner, app_module, seen, monkeypatch
):
    monkeypatch.setattr(app_module, "TESTER_MAX_CALLS_PER_DAY", 2)
    make_owner(telegram_id=4242)
    a, b = app_client.test_client(), app_client.test_client()
    signup(a, accounts, "alice-q")
    signup(b, accounts, "bruno-q")
    assert [say(a).status_code for _ in range(3)] == [200, 200, 429]
    assert say(a).get_json()["scope"] == "user"
    assert say(b).status_code == 200
    bot = app_client.test_client()
    owner_reply = bot.post(
        "/message", json={"telegram_id": 4242, "text": "salut"}, headers={"X-Fox-Service-Token": TOKEN}
    )
    assert owner_reply.status_code == 200


def test_plafond_global_donne_429_avec_portee_globale(app_client, accounts, app_module, seen, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1)
    a, b = app_client.test_client(), app_client.test_client()
    signup(a, accounts, "alice-g")
    signup(b, accounts, "bruno-g")
    assert say(a).status_code == 200
    refused = say(b)
    assert refused.status_code == 429 and refused.get_json()["scope"] == "global"
