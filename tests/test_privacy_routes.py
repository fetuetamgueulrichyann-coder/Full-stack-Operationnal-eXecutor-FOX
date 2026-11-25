"""Routes de confidentialité (phase 1b-A) : réglages, consentement, information.

Les niveaux sont enregistrés mais ne contrôlent PAS encore les envois : ces tests vérifient que l'API le dit
honnêtement et que rien d'autre ne change.
"""
import re

import pytest

from test_app_auth import PASSWORD, TOKEN, app_client, client, say, seen, signup  # noqa: F401

SERVICE = {"X-Fox-Service-Token": TOKEN}


@pytest.fixture
def tester(client, accounts):
    signup(client, accounts, "marie")
    return client


@pytest.fixture
def owner(app_client, accounts, db):
    web = app_client.test_client()
    signup(web, accounts, "chef")
    with db.cursor() as cur:
        cur.execute("UPDATE users SET role = 'owner' WHERE username = 'chef'")
    return web


def settings(c):
    return c.get("/privacy/settings").get_json()


def audit_events(db, event):
    with db.cursor() as cur:
        cur.execute("SELECT details FROM audit_log WHERE event = %s ORDER BY id", (event,))
        return [r[0] for r in cur.fetchall()]


# --- Accès -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method, path", [
    ("GET", "/privacy/settings"), ("POST", "/privacy/settings"), ("POST", "/privacy/consent"), ("GET", "/privacy/info"),
])
def test_sans_session_rien_n_est_accessible(client, method, path):
    assert client.open(path, method=method, json={} if method == "POST" else None).status_code == 401


def test_les_changements_sont_refuses_depuis_le_bot(client, make_owner):
    make_owner(telegram_id=4242)
    for path, payload in (("/privacy/settings", {"level": "strict"}), ("/privacy/consent", {"version": 1})):
        response = client.post(path, json={"telegram_id": 4242, **payload}, headers=SERVICE)
        assert response.status_code == 403 and "site" in response.get_json()["error"]
    # la lecture reste possible depuis le bot
    assert client.get("/privacy/settings?telegram_id=4242", headers=SERVICE).status_code == 200


# --- Valeurs par défaut et honnêteté --------------------------------------------------------------------
def test_valeurs_par_defaut_protege_sans_consentement(tester):
    data = settings(tester)
    assert data["level"] == "protege" and data["default_level"] == "protege"
    assert data["notification_detail"] == "discret" and data["preferred_provider"] is None
    assert data["consent"]["read"] is False and data["consent"]["version"] is None and data["consent"]["at"] is None
    assert data["block_all"] is False


def test_l_api_dit_que_les_niveaux_ne_sont_pas_appliques(tester):
    data = settings(tester)
    assert data["enforcement_active"] is False
    assert "pas encore appliqués" in data["enforcement_notice"] and "comme avant" in data["enforcement_notice"]
    assert all(level["applied"] is False for level in data["levels"])


def test_strict_n_est_jamais_presente_comme_operationnel(tester):
    levels = {level["id"]: level for level in settings(tester)["levels"]}
    assert levels["strict"]["label"] == "Strict (pas encore actif)"
    for level in levels.values():
        assert level["summary"].startswith("Quand ce niveau sera appliqué")  # conditionnel, jamais présent
    text = " ".join(l["summary"] + l["label"] for l in levels.values()).lower()
    assert not re.search(r"\b(bloque|protège|garantit|empêche)\b", text)
    # la même réponse après avoir choisi Strict : toujours « pas encore actif »
    assert tester.post("/privacy/settings", json={"level": "strict"}).status_code == 200
    again = settings(tester)
    assert again["level"] == "strict" and again["enforcement_active"] is False
    assert {l["id"]: l["label"] for l in again["levels"]}["strict"].endswith("(pas encore actif)")


def test_si_les_niveaux_etaient_appliques_le_libelle_strict_change(tester, monkeypatch):
    import privacy

    monkeypatch.setattr(privacy, "ENFORCEMENT_ACTIVE", True)
    data = settings(tester)
    assert data["enforcement_notice"] is None and data["enforcement_active"] is True
    assert {l["id"]: l["label"] for l in data["levels"]}["strict"] == "Strict"


# --- Réglages ------------------------------------------------------------------------------------------------
def test_changer_le_niveau_et_les_notifications(tester, db):
    response = tester.post("/privacy/settings", json={"level": "standard", "notification_detail": "complet"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["level"] == "standard" and data["notification_detail"] == "complet"
    assert settings(tester)["level"] == "standard"
    assert audit_events(db, "privacy_settings") == [{"level": "standard", "notification_detail": "complet"}]


@pytest.mark.parametrize("payload", [
    {}, {"level": "ouvert"}, {"level": None}, {"level": ["strict"]}, {"notification_detail": "tout"},
    {"preferred_provider": "openai"}, {"preferred_provider": "gemini_tts"}, {"preferred_provider": 3},
])
def test_valeurs_invalides_refusees_sans_rien_changer(tester, db, payload):
    before = settings(tester)
    assert tester.post("/privacy/settings", json=payload).status_code == 400
    assert settings(tester) == before
    assert audit_events(db, "privacy_settings") == []


def test_un_testeur_n_a_que_groq(tester):
    assert [c["id"] for c in settings(tester)["provider_choices"]] == ["groq"]
    refused = tester.post("/privacy/settings", json={"preferred_provider": "gemini"})
    assert refused.status_code == 400 and "indisponible" in refused.get_json()["error"]
    ok = tester.post("/privacy/settings", json={"preferred_provider": "groq"})
    assert ok.status_code == 200 and ok.get_json()["preferred_provider"] == "groq"
    assert tester.post("/privacy/settings", json={"preferred_provider": None}).get_json()["preferred_provider"] is None


def test_le_proprietaire_choisit_entre_gemini_et_groq(owner):
    assert [c["id"] for c in settings(owner)["provider_choices"]] == ["gemini", "groq"]
    assert owner.post("/privacy/settings", json={"preferred_provider": "gemini"}).status_code == 200
    assert settings(owner)["preferred_provider"] == "gemini"


def test_chaque_utilisateur_a_ses_propres_reglages(app_client, accounts):
    a, b = app_client.test_client(), app_client.test_client()
    signup(a, accounts, "anna")
    signup(b, accounts, "boris")
    a.post("/privacy/settings", json={"level": "strict"})
    a.post("/privacy/consent", json={"version": 1})
    assert settings(b)["level"] == "protege" and settings(b)["consent"]["read"] is False
    assert settings(a)["level"] == "strict" and settings(a)["consent"]["read"] is True


# --- Consentement ------------------------------------------------------------------------------------------
def test_consentement_enregistre_version_et_date_sans_rien_autoriser(tester, db):
    response = tester.post("/privacy/consent", json={"version": 1})
    assert response.status_code == 200
    consent = response.get_json()["consent"]
    assert consent["read"] is True and consent["version"] == 1 and consent["at"]
    assert "ne constitue pas une autorisation" in consent["notice"]
    data = settings(tester)
    assert data["level"] == "protege" and data["enforcement_active"] is False  # aucun réglage d'envoi modifié
    assert audit_events(db, "privacy_consent") == [{"version": 1}]
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM data_grants")
        assert cur.fetchone()[0] == 0  # aucune autorisation créée


@pytest.mark.parametrize("version", [0, 2, -1, "1", None, True, 1.5, [1]])
def test_consentement_refuse_si_version_inconnue(tester, db, version):
    response = tester.post("/privacy/consent", json={"version": version})
    assert response.status_code == 400 and response.get_json()["current_version"] == 1
    assert settings(tester)["consent"]["read"] is False
    assert audit_events(db, "privacy_consent") == []


def test_une_nouvelle_version_du_texte_demande_une_nouvelle_lecture(tester, monkeypatch):
    import privacy

    tester.post("/privacy/consent", json={"version": 1})
    assert settings(tester)["consent"]["read"] is True
    monkeypatch.setattr(privacy, "CONSENT_VERSION", 2)
    data = settings(tester)
    assert data["consent"]["read"] is False and data["consent"]["version"] == 1 and data["consent"]["current_version"] == 2


# --- Information -----------------------------------------------------------------------------------------------
def test_info_decrit_les_fournisseurs_et_le_suivi(tester):
    info = tester.get("/privacy/info").get_json()
    assert info["enforcement_active"] is False
    by_id = {p["id"]: p for p in info["providers"]}
    assert set(by_id) == {"gemini", "groq", "gemini_tts", "elevenlabs"}
    assert by_id["groq"]["available"] is True and by_id["gemini"]["available"] is False  # testeur
    assert info["tracking"]["active"] is False
    assert "commence avec la phase 1c" in info["tracking"]["notice"] and "pas encore tracés" in info["tracking"]["notice"]
    assert [c["id"] for c in info["categories"]] == ["texte_libre", "personnel", "metier", "fichier", "voix"]


def test_info_reflete_les_garanties_declarees(tester, monkeypatch):
    for key in ("GEMINI_TIER", "GROQ_ZDR"):
        monkeypatch.delenv(key, raising=False)
    info = tester.get("/privacy/info").get_json()
    assert info["declared"]["groq_zdr"] is False and info["declared"]["gemini_tier"] == "free"
    groq = {p["id"]: p for p in info["providers"]}["groq"]["retention"]
    assert "30 jours" in groq
    monkeypatch.setenv("GROQ_ZDR", "1")
    monkeypatch.setenv("GEMINI_TIER", "paid")
    info = tester.get("/privacy/info").get_json()
    assert info["declared"]["groq_zdr"] is True and info["declared"]["gemini_tier"] == "paid"
    assert "zéro" in {p["id"]: p for p in info["providers"]}["groq"]["retention"]
    assert "déclarées" in info["declared"]["note"]


def test_une_valeur_zdr_autre_que_1_n_est_pas_prise_pour_vraie(tester, monkeypatch):
    for value in ("true", "yes", "0", " ", "2"):
        monkeypatch.setenv("GROQ_ZDR", value)
        assert tester.get("/privacy/info").get_json()["declared"]["groq_zdr"] is False


def test_le_bloc_total_est_reflete(tester, monkeypatch):
    monkeypatch.setenv("PRIVACY_BLOCK_ALL", "1")
    assert settings(tester)["block_all"] is True


# --- Rien d'autre ne change -----------------------------------------------------------------------------------------
def test_changer_le_niveau_ne_change_rien_au_chat(tester, seen, db):
    assert say(tester, "Bonjour").get_json()["reply"] == "écho : Bonjour"
    for level in ("strict", "standard", "protege"):
        tester.post("/privacy/settings", json={"level": level})
        assert say(tester, "Bonjour").get_json()["reply"] == "écho : Bonjour"
    assert len(seen) == 4  # l'agent est appelé à chaque fois : aucun message n'est bloqué ni retenu
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM held_messages")
        assert cur.fetchone()[0] == 0


def test_les_evenements_sont_lisibles_dans_le_journal(tester):
    tester.post("/privacy/settings", json={"level": "strict"})
    tester.post("/privacy/consent", json={"version": 1})
    texts = [e["text"] for e in tester.get("/journal?limit=10").get_json()["entries"]]
    assert "Réglage de confidentialité modifié" in texts and "Information de confidentialité lue" in texts


def test_le_journal_ne_contient_que_les_reglages(tester, db):
    tester.post("/privacy/settings", json={"level": "strict", "notification_detail": "complet", "preferred_provider": "groq"})
    assert audit_events(db, "privacy_settings") == [{"level": "strict", "notification_detail": "complet", "preferred_provider": "groq"}]


def test_les_variables_sont_transmises_par_docker_compose():
    import pathlib

    from conftest import ORCHESTRATOR_DIR

    root = ORCHESTRATOR_DIR.parent
    compose = root / "docker-compose.yml"
    env_example = root / ".env.example"
    if not compose.exists() or not env_example.exists():  # conteneur de tests : seuls certains dossiers sont montés
        pytest.skip("docker-compose.yml non monté dans cet environnement")
    text, example = compose.read_text(encoding="utf-8"), env_example.read_text(encoding="utf-8")
    for key in ("GEMINI_TIER", "GROQ_ZDR", "PRIVACY_BLOCK_ALL"):
        assert f"{key}: ${{{key}:-}}" in text, key  # valeur vide par défaut : jamais « vrai » sans que tu l'aies écrit
        assert re.search(rf"^{key}=$", example, re.M), key  # présent dans .env.example, vide
    assert pathlib.Path(compose).name == "docker-compose.yml"
