"""Sécurité web : origine des requêtes, jeton CSRF, format d'erreur uniforme (phase D0)."""
import pytest
from flask.testing import FlaskClient

import websecurity

TOKEN = "jeton-de-service-de-test"
PASSWORD = "mot-de-passe-solide"
ORIGIN = "http://localhost"


@pytest.fixture
def app_client(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    monkeypatch.setattr(websecurity, "ALLOWED_ORIGINS", frozenset())
    return app_module.app


@pytest.fixture
def raw(app_client):
    """Client brut : n'envoie ni origine ni jeton (contrairement au client de test habituel)."""
    return FlaskClient(app_client, app_client.response_class, use_cookies=True)


@pytest.fixture
def logged(raw, accounts):
    code = accounts.create_invitation(days=1)
    response = raw.post(
        "/auth/register",
        json={"code": code, "username": "marie", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 201
    return raw, response.get_json()["csrf_token"]


def good(csrf, **extra):
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf, **extra}


# --- Fonctions pures -----------------------------------------------------------------
def test_jeton_lie_a_la_session_et_stable():
    assert websecurity.csrf_token("abc") == websecurity.csrf_token("abc")
    assert websecurity.csrf_token("abc") != websecurity.csrf_token("abd")
    assert websecurity.csrf_token("") == "" and websecurity.csrf_token(None) == ""
    assert len(websecurity.csrf_token("abc")) == 64


def test_normalisation_des_origines():
    n = websecurity.normalize_origin
    assert n("https://Exemple.COM/chemin?x=1") == "https://exemple.com"
    assert n("http://localhost:5173") == "http://localhost:5173"
    assert n("null") is None and n("") is None and n(None) is None
    assert n("ftp://exemple.com") is None and n("pas une url") is None
    assert websecurity.parse_origins("https://a.com, http://b.com:81 ,,x") == {
        "https://a.com", "http://b.com:81"}


# --- Origine ---------------------------------------------------------------------------
def test_connexion_depuis_une_origine_etrangere_refusee(raw, accounts):
    accounts.create_invitation(days=1)
    r = raw.post("/auth/login", json={"username": "x", "password": "y"},
                 headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.get_json()["code"] == "origin_forbidden"


def test_origine_null_refusee(raw):
    r = raw.post("/auth/login", json={"username": "x", "password": "y"}, headers={"Origin": "null"})
    assert r.status_code == 403


def test_connexion_meme_origine_acceptee_meme_sans_jeton(raw, accounts):
    r = raw.post("/auth/login", json={"username": "inconnu", "password": "y"}, headers={"Origin": ORIGIN})
    assert r.status_code == 401  # l'origine passe, ce sont les identifiants qui sont refusés


def test_origine_autorisee_par_configuration(raw, monkeypatch):
    monkeypatch.setattr(websecurity, "ALLOWED_ORIGINS", frozenset({"https://fox.example.com"}))
    r = raw.post("/auth/login", json={"username": "x", "password": "y"},
                 headers={"Origin": "https://fox.example.com"})
    assert r.status_code == 401
    r = raw.post("/auth/login", json={"username": "x", "password": "y"},
                 headers={"Origin": "https://fox.example.com.evil.io"})
    assert r.status_code == 403


def test_referer_sert_quand_origin_est_absent(raw):
    ok = raw.post("/auth/login", json={"username": "x", "password": "y"},
                  headers={"Referer": ORIGIN + "/connexion"})
    assert ok.status_code == 401
    bad = raw.post("/auth/login", json={"username": "x", "password": "y"},
                   headers={"Referer": "https://evil.example/page"})
    assert bad.status_code == 403


def test_client_sans_navigateur_et_sans_session_n_est_pas_bloque(raw):
    assert raw.post("/auth/login", json={"username": "x", "password": "y"}).status_code == 401


# --- CSRF --------------------------------------------------------------------------------
def test_la_connexion_renvoie_un_jeton_et_me_le_redonne(logged):
    client, csrf = logged
    assert len(csrf) == 64
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.get_json()["csrf_token"] == csrf


def test_requete_modifiante_sans_jeton_refusee(logged):
    client, _ = logged
    r = client.post("/chats", json={}, headers={"Origin": ORIGIN})
    assert r.status_code == 403 and r.get_json()["code"] == "csrf_invalid"


def test_jeton_faux_refuse(logged):
    client, csrf = logged
    r = client.post("/chats", json={}, headers=good("0" * 64))
    assert r.status_code == 403 and r.get_json()["code"] == "csrf_invalid"
    r = client.post("/chats", json={}, headers=good(csrf[:-1] + ("0" if csrf[-1] != "0" else "1")))
    assert r.status_code == 403


def test_jeton_d_une_autre_session_refuse(logged, accounts, app_client):
    client, csrf = logged
    other = FlaskClient(app_client, app_client.response_class, use_cookies=True)
    code = accounts.create_invitation(days=1)
    r = other.post("/auth/register", json={"code": code, "username": "paul", "password": PASSWORD},
                   headers={"Origin": ORIGIN})
    other_csrf = r.get_json()["csrf_token"]
    assert other_csrf != csrf
    assert client.post("/chats", json={}, headers=good(other_csrf)).status_code == 403


def test_jeton_correct_accepte(logged):
    client, csrf = logged
    assert client.post("/chats", json={}, headers=good(csrf)).status_code in (200, 201)


def test_origine_absente_avec_cookie_refusee_meme_avec_bon_jeton(logged):
    client, csrf = logged
    r = client.post("/chats", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.get_json()["code"] == "origin_missing"


def test_origine_etrangere_avec_cookie_et_bon_jeton_refusee(logged):
    client, csrf = logged
    r = client.post("/chats", json={}, headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.get_json()["code"] == "origin_forbidden"


@pytest.mark.parametrize("method,path", [
    ("post", "/message"), ("post", "/tasks"), ("patch", "/tasks/1"), ("delete", "/tasks/1"),
    ("post", "/events"), ("delete", "/chats"), ("post", "/private"), ("put", "/business"),
    ("delete", "/memory"), ("post", "/auth/logout"),
])
def test_toutes_les_routes_modifiantes_exigent_le_jeton(logged, method, path):
    client, csrf = logged
    r = getattr(client, method)(path, json={}, headers={"Origin": ORIGIN})
    assert r.status_code == 403 and r.get_json()["code"] == "csrf_invalid"


def test_lecture_sans_jeton_autorisee(logged):
    client, _ = logged
    assert client.get("/tasks").status_code == 200


def test_deconnexion_avec_jeton_fonctionne_et_invalide_le_cookie(logged):
    client, csrf = logged
    assert client.post("/auth/logout", json={}, headers=good(csrf)).status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_session_expiree_donne_401_et_non_403(logged, db):
    client, csrf = logged
    with db.cursor() as cur:
        cur.execute("DELETE FROM sessions")
    r = client.post("/chats", json={}, headers=good(csrf))
    assert r.status_code == 401


# --- Bot Telegram ------------------------------------------------------------------------
def test_le_bot_avec_jeton_de_service_n_est_pas_soumis_au_csrf(raw, make_owner):
    owner = make_owner(telegram_id=777)
    r = raw.post("/chats", json={"telegram_id": 777}, headers={"X-Fox-Service-Token": TOKEN})
    assert r.status_code in (200, 201)


def test_un_mauvais_jeton_de_service_reste_refuse(raw):
    r = raw.post("/chats", json={"telegram_id": 1}, headers={"X-Fox-Service-Token": "faux"})
    assert r.status_code == 401


def test_un_cookie_ne_contourne_pas_la_regle_avec_un_faux_jeton_de_service(logged):
    client, _ = logged
    r = client.post("/chats", json={"telegram_id": 1}, headers={"X-Fox-Service-Token": "faux"})
    assert r.status_code == 401  # ne passe pas pour autant : la route refuse le faux jeton


# --- Format d'erreur uniforme ----------------------------------------------------------------
def test_erreur_non_authentifie_a_un_code(raw):
    r = raw.get("/tasks")
    assert r.status_code == 401
    assert r.get_json() == {"error": "non authentifie", "code": "unauthenticated"}


def test_route_inconnue_en_json(raw):
    r = raw.get("/nexiste-pas")
    assert r.status_code == 404 and r.is_json
    assert r.get_json()["code"] == "not_found" and r.get_json()["error"]


def test_methode_interdite_en_json(raw):
    r = raw.get("/auth/login")
    assert r.status_code == 405 and r.get_json()["code"] == "method_not_allowed"


def test_corps_non_json_donne_415_avec_code(raw):
    r = raw.post("/auth/login", data="a=b", headers={"Origin": ORIGIN})
    assert r.status_code == 415 and r.get_json()["code"] == "unsupported_media_type"


def test_les_codes_existants_ne_sont_pas_ecrases(raw):
    r = raw.post("/auth/login", json={"username": "x", "password": "y"}, headers={"Origin": ORIGIN})
    assert r.status_code == 401 and r.get_json()["code"] == "invalid_credentials"


def test_reponses_non_mises_en_cache(raw):
    r = raw.get("/health")
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Content-Type-Options"] == "nosniff"


# --- Codes d'erreur précis du modèle d'IA ------------------------------------------------
def test_modele_indisponible_a_un_code_stable(logged, agent, monkeypatch):
    client, csrf = logged

    def boom(*args, **kwargs):
        raise agent.LLMUnavailable()

    monkeypatch.setattr(agent, "chat", boom)
    r = client.post("/message", json={"text": "bonjour"}, headers=good(csrf))
    assert r.status_code == 503 and r.get_json()["code"] == "model_unavailable"


def test_erreur_inattendue_du_modele_a_un_code_texte(logged, agent, monkeypatch):
    client, csrf = logged

    def boom(*args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(agent, "chat", boom)
    r = client.post("/message", json={"text": "bonjour"}, headers=good(csrf))
    assert r.status_code == 502 and r.get_json()["code"] == "upstream_error"


def test_quota_depasse_a_un_code_stable(logged, agent, app_module, monkeypatch):
    client, csrf = logged
    monkeypatch.setattr(agent, "chat", lambda *a, **k: ("ok", 1, []))
    monkeypatch.setattr(app_module, "TESTER_MAX_CALLS_PER_DAY", 1)
    assert client.post("/message", json={"text": "un"}, headers=good(csrf)).status_code == 200
    r = client.post("/message", json={"text": "deux"}, headers=good(csrf))
    assert r.status_code == 429
    assert r.get_json()["code"] == "quota_exceeded" and r.get_json()["scope"]
