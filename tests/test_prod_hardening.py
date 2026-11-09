"""Phase E : inscriptions fermables, contrôle de configuration."""
import pytest

import prodcheck

STRONG = {"FOX_SERVICE_TOKEN": "x" * 40, "POSTGRES_PASSWORD": "un-mot-de-passe-long"}


def test_configuration_locale_saine_ne_dit_rien():
    assert prodcheck.problems({**STRONG, "DOMAIN": "localhost"}) == []


def test_en_ligne_exige_https_cookie_et_proxy():
    out = prodcheck.problems({**STRONG, "DOMAIN": "fox.example.com"})
    joined = " ".join(out)
    assert "COOKIE_SECURE" in joined and "TRUST_PROXY" in joined and "PUBLIC_ORIGIN" in joined


def test_configuration_en_ligne_correcte():
    env = {**STRONG, "DOMAIN": "fox.example.com", "PUBLIC_ORIGIN": "https://fox.example.com", "COOKIE_SECURE": "1", "TRUST_PROXY": "1"}
    assert prodcheck.problems(env) == []


def test_origine_en_http_signalee_en_ligne():
    env = {**STRONG, "DOMAIN": "fox.example.com", "PUBLIC_ORIGIN": "http://fox.example.com", "COOKIE_SECURE": "1", "TRUST_PROXY": "1"}
    assert any("https://" in p for p in prodcheck.problems(env))


@pytest.mark.parametrize("token", ["", "change_me", "court"])
def test_jeton_de_service_faible(token):
    assert any("FOX_SERVICE_TOKEN" in p for p in prodcheck.problems({**STRONG, "FOX_SERVICE_TOKEN": token}))


def test_mot_de_passe_proprietaire_laisse_dans_env_en_ligne():
    env = {**STRONG, "DOMAIN": "fox.example.com", "PUBLIC_ORIGIN": "https://fox.example.com", "COOKIE_SECURE": "1", "TRUST_PROXY": "1", "OWNER_PASSWORD": "abc"}
    assert any("OWNER_PASSWORD" in p for p in prodcheck.problems(env))


def test_les_avertissements_ne_contiennent_jamais_les_valeurs():
    env = {**STRONG, "FOX_SERVICE_TOKEN": "secret-tres-specifique", "DOMAIN": "localhost"}
    assert "secret-tres-specifique" not in " ".join(prodcheck.problems(env))


def test_mode_d_inscription_inconnu_signale():
    assert any("REGISTRATION_MODE" in p for p in prodcheck.problems({**STRONG, "REGISTRATION_MODE": "ouvert"}))


# --- inscriptions fermées -------------------------------------------------------------------------
def test_inscription_web_refusee_quand_fermee(app_module, accounts, monkeypatch):
    monkeypatch.setenv("REGISTRATION_MODE", "closed")
    code = accounts.create_invitation(days=1)
    code = code["code"] if isinstance(code, dict) else code
    r = app_module.app.test_client().post("/auth/register", json={"code": code, "username": "nouveau", "password": "mot-de-passe-solide"})
    assert r.status_code == 403 and r.get_json()["code"] == "registration_closed"


def test_inscription_web_possible_par_defaut(app_module, accounts, monkeypatch):
    monkeypatch.delenv("REGISTRATION_MODE", raising=False)
    code = accounts.create_invitation(days=1)
    code = code["code"] if isinstance(code, dict) else code
    r = app_module.app.test_client().post("/auth/register", json={"code": code, "username": "nouveau", "password": "mot-de-passe-solide"})
    assert r.status_code == 201


def test_inscription_telegram_refusee_quand_fermee(accounts, monkeypatch):
    monkeypatch.setenv("REGISTRATION_MODE", "closed")
    with pytest.raises(accounts.AuthError) as exc:
        accounts.register_telegram("ABCD-EFGH-JKLM", 123456)
    assert exc.value.code == "registration_closed"


def test_la_connexion_reste_possible_quand_fermee(app_module, make_user, monkeypatch):
    user = make_user()
    monkeypatch.setenv("REGISTRATION_MODE", "closed")
    r = app_module.app.test_client().post("/auth/login", json={"username": user["username"], "password": user["password"]})
    assert r.status_code == 200
