"""Notifications push : abonnements, rappels par canal (sans voler ceux de Telegram), envoi et isolation."""
from datetime import datetime, timedelta, timezone

import pytest

import webpush
from test_app_auth import app_client, signup  # noqa: F401  (client web avec session, pour l'API de réglages)
from test_app_commercial import HEADERS, OTHER, OWNER, alice, call, client, friend  # noqa: F401

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def keys():
    public, _ = webpush.generate_vapid_keys()
    return {"p256dh": public, "auth": webpush.b64e(b"0123456789abcdef")}


@pytest.fixture
def vapid(monkeypatch):
    public, private = webpush.generate_vapid_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    return public


def subscribe(client, telegram_id=OWNER, endpoint=ENDPOINT, **extra):
    return client.post("/push/subscribe", json={"telegram_id": telegram_id, "endpoint": endpoint, "keys": keys(), **extra},
                       headers=HEADERS)


# --- API ------------------------------------------------------------------------------------
def test_config_sans_cles_est_desactivee(client, monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    got = call(client, "GET", "/push/config").get_json()
    assert got == {"enabled": False, "public_key": None, "devices": 0, "missing": ["VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"]}


def test_config_dit_quelle_variable_manque(client, monkeypatch):
    public, _ = webpush.generate_vapid_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    got = call(client, "GET", "/push/config").get_json()
    assert got["enabled"] is False and got["missing"] == ["VAPID_PRIVATE_KEY"]


def test_config_avec_cles_donne_la_cle_publique(client, vapid):
    got = call(client, "GET", "/push/config").get_json()
    assert got["enabled"] is True and got["public_key"] == vapid


def test_abonnement_puis_desabonnement(client, vapid):
    assert subscribe(client).status_code == 201
    assert call(client, "GET", "/push/config").get_json()["devices"] == 1
    assert subscribe(client).status_code == 201  # idempotent : même appareil
    assert call(client, "GET", "/push/config").get_json()["devices"] == 1
    r = call(client, "POST", "/push/unsubscribe", endpoint=ENDPOINT)
    assert r.get_json()["devices"] == 0


def test_abonnement_refuse_sans_configuration(client, monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    assert subscribe(client).status_code == 409


@pytest.mark.parametrize("endpoint", ["http://fcm.googleapis.com/x", "https://169.254.169.254/x", "https://evil.example/x", "", None])
def test_abonnement_vers_une_adresse_interdite(client, vapid, endpoint):
    assert subscribe(client, endpoint=endpoint).status_code == 400


def test_cles_invalides(client, vapid):
    r = client.post("/push/subscribe", json={"telegram_id": OWNER, "endpoint": ENDPOINT, "keys": {"p256dh": "AAAA", "auth": "BBBB"}},
                    headers=HEADERS)
    assert r.status_code == 400


def test_limite_d_appareils(client, vapid, monkeypatch):
    import push_store
    monkeypatch.setattr(push_store, "MAX_PER_USER", 2)
    assert subscribe(client, endpoint=ENDPOINT + "1").status_code == 201
    assert subscribe(client, endpoint=ENDPOINT + "2").status_code == 201
    assert subscribe(client, endpoint=ENDPOINT + "3").status_code == 409


def test_un_utilisateur_ne_peut_pas_retirer_l_appareil_d_un_autre(client, friend, vapid):
    subscribe(client)
    call(client, "POST", "/push/unsubscribe", telegram_id=OTHER, endpoint=ENDPOINT)
    assert call(client, "GET", "/push/config").get_json()["devices"] == 1


def test_test_de_notification(client, vapid, monkeypatch):
    import push_delivery
    sent = []
    monkeypatch.setattr(webpush, "send", lambda sub, payload, **k: sent.append((sub["endpoint"], payload)))
    assert call(client, "POST", "/push/test").status_code == 404  # aucun appareil
    subscribe(client)
    assert call(client, "POST", "/push/test").get_json() == {"sent": 1}
    assert sent and sent[0][0] == ENDPOINT and sent[0][1]["title"] == "FOX"


def test_acces_refuse_sans_authentification(client):
    for path in ("/push/config", "/push/subscribe", "/push/unsubscribe", "/push/test"):
        r = client.get(path) if path == "/push/config" else client.post(path, json={})
        assert r.status_code in (401, 403)


# --- Envoi des rappels ---------------------------------------------------------------------
class _Lazy:
    """Importe le module au premier usage : db.py a besoin de DATABASE_URL, défini par les fixtures de test."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        import importlib
        return getattr(importlib.import_module(self._name), attr)


push_delivery = _Lazy("push_delivery")
push_store = _Lazy("push_store")
privacy_store = _Lazy("privacy_store")


def full_detail(user):
    """Le réglage « complet » : titre, heure, lieu et tâches dans la notification."""
    privacy_store.save_settings(user, notification_detail="complet")


def event_at(db, user, minutes_from_now, title="RDV", created_minutes_ago=120):
    start = datetime.now().astimezone() + timedelta(minutes=minutes_from_now)
    event = db.create_event(user, title, start, start + timedelta(hours=1), max_upcoming=0)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - make_interval(mins => %s) WHERE id = %s", (created_minutes_ago, event["id"]))
    return event


def device(user, endpoint=ENDPOINT):
    push_store.add(user, endpoint, keys()["p256dh"], webpush.b64e(b"0123456789abcdef"))


class Recorder:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, subscription, payload, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append((subscription["endpoint"], payload))


def test_sans_appareil_aucun_rappel_push(db, alice):
    event_at(db, alice, 20)
    assert db.claim_due_event_reminders(30, channel="push") == []


def test_les_canaux_telegram_et_push_ne_se_volent_pas_les_rappels(db, alice):
    device(alice)
    event_at(db, alice, 20)
    pushed = db.claim_due_event_reminders(30, channel="push")
    assert [d["title"] for d in pushed] == ["RDV"] and pushed[0]["user_id"] == alice
    assert db.claim_due_event_reminders(30, channel="push") == []
    assert [d["title"] for d in db.claim_due_event_reminders(30)] == ["RDV"]  # Telegram l'a encore


def test_envoi_du_rappel_de_rendez_vous(db, alice):
    device(alice)
    full_detail(alice)
    event_at(db, alice, 20, title="Réunion chantier")
    rec = Recorder()
    assert push_delivery.run_once(rec) == 1
    endpoint, payload = rec.sent[0]
    assert endpoint == ENDPOINT and payload["title"] == "Réunion chantier" and payload["url"] == "/agenda"
    assert payload["body"].startswith("À ") and payload["tag"].startswith("event-")
    assert push_delivery.run_once(rec) == 0 and len(rec.sent) == 1  # pas de doublon


def test_envoi_du_rappel_de_tache(db, alice):
    device(alice)
    full_detail(alice)
    db.create_task(alice, "Appeler Yann", due_at=datetime.now().astimezone() - timedelta(minutes=2))
    rec = Recorder()
    assert push_delivery.run_once(rec) == 1
    assert rec.sent[0][1]["title"] == "Appeler Yann" and rec.sent[0][1]["url"] == "/taches"


def test_le_rappel_va_seulement_aux_appareils_de_son_proprietaire(db, alice, friend):
    device(alice, ENDPOINT + "-alice")
    device(friend, ENDPOINT + "-ami")
    event_at(db, alice, 20)
    rec = Recorder()
    push_delivery.run_once(rec)
    assert [e for e, _ in rec.sent] == [ENDPOINT + "-alice"]


def test_appareil_disparu_est_oublie(db, alice):
    device(alice)
    event_at(db, alice, 20)
    push_delivery.run_once(Recorder(fail=webpush.Gone("410")))
    assert push_store.count(alice) == 0


def test_echecs_repetes_font_oublier_l_appareil(db, alice):
    device(alice)
    for _ in range(push_store.MAX_FAILURES - 1):
        push_delivery.deliver(alice, {"title": "x"}, Recorder(fail=webpush.PushError("503")))
        assert push_store.count(alice) == 1
    push_delivery.deliver(alice, {"title": "x"}, Recorder(fail=webpush.PushError("503")))
    assert push_store.count(alice) == 0


def test_un_succes_remet_le_compteur_d_echecs_a_zero(db, alice):
    device(alice)
    push_delivery.deliver(alice, {"title": "x"}, Recorder(fail=webpush.PushError("503")))
    push_delivery.deliver(alice, {"title": "x"}, Recorder())
    for _ in range(push_store.MAX_FAILURES - 1):
        push_delivery.deliver(alice, {"title": "x"}, Recorder(fail=webpush.PushError("503")))
    assert push_store.count(alice) == 1


def test_rendez_vous_deja_passe_a_la_migration_pas_de_rafale(db, alice):
    """Un rappel déjà dû avant l'activation du canal push (créé trop récemment) n'est pas rejoué à retardement."""
    device(alice)
    event_at(db, alice, 20, created_minutes_ago=5)  # créé il y a 5 min pour dans 20 : pas de rappel immédiat
    assert db.claim_due_event_reminders(30, channel="push") == []


# --- Notifications discrètes (phase 1b-B) ------------------------------------------------------------------------
SECRET_TITLE, SECRET_PLACE, SECRET_TASK = "SECRET-TITRE-Dr Mballa", "SECRET-LIEU-12 rue Joss", "SECRET-TACHE-ordonnance"
DISCREET = {"title": "Rappel FOX", "body": "Touche pour ouvrir FOX."}


def event_with_secrets(db, user, minutes=20):
    start = datetime.now().astimezone() + timedelta(minutes=minutes)
    event = db.create_event(user, SECRET_TITLE, start, start + timedelta(hours=1), location=SECRET_PLACE, max_upcoming=0)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - interval '2 hours' WHERE id = %s", (event["id"],))
    db.create_task(user, SECRET_TASK, event_id=event["id"])
    return event


def assert_no_secret(value):
    dumped = str(value)
    for secret in (SECRET_TITLE, SECRET_PLACE, SECRET_TASK, "SECRET", "Mballa", "Joss", "ordonnance"):
        assert secret not in dumped, secret


def test_sans_reglage_enregistre_le_rappel_est_discret(db, alice):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM privacy_settings WHERE user_id = %s", (alice,))
        assert cur.fetchone()[0] == 0
    device(alice)
    event = event_with_secrets(db, alice)
    rec = Recorder()
    assert push_delivery.run_once(rec) == 1
    endpoint, payload = rec.sent[0]
    assert payload == {**DISCREET, "url": "/agenda", "tag": f"event-{event['id']}"}
    assert_no_secret(payload)


def test_rappel_de_tache_discret_par_defaut(db, alice):
    device(alice)
    task = db.create_task(alice, SECRET_TASK, due_at=datetime.now().astimezone() - timedelta(minutes=2))
    rec = Recorder()
    assert push_delivery.run_once(rec) == 1
    assert rec.sent[0][1] == {**DISCREET, "url": "/taches", "tag": f"task-{task['id']}"}
    assert_no_secret(rec.sent[0][1])


def test_le_mode_discret_explicite_est_identique_au_defaut(db, alice):
    privacy_store.save_settings(alice, notification_detail="discret")
    device(alice)
    event_with_secrets(db, alice)
    rec = Recorder()
    push_delivery.run_once(rec)
    assert_no_secret(rec.sent[0][1])
    assert rec.sent[0][1]["title"] == "Rappel FOX"


def test_le_mode_complet_retrouve_le_detail(db, alice):
    full_detail(alice)
    device(alice)
    event_with_secrets(db, alice)
    rec = Recorder()
    push_delivery.run_once(rec)
    payload = rec.sent[0][1]
    assert payload["title"] == SECRET_TITLE and SECRET_PLACE in payload["body"] and SECRET_TASK in payload["body"]
    assert payload["url"] == "/agenda"


def test_les_fonctions_de_contenu_sont_discretes_par_defaut_et_pour_toute_valeur_inconnue():
    reminder = {"id": 7, "title": SECRET_TITLE, "location": SECRET_PLACE, "tasks": [SECRET_TASK], "start_at": datetime.now().astimezone()}
    for detail in ("discret", "", None, "COMPLET", "tout", 1, "complet ", ["complet"]):
        for build in (push_delivery.event_payload, push_delivery.task_payload):
            payload = build(reminder, detail)
            assert_no_secret(payload)
            assert payload["title"] == "Rappel FOX"
    assert push_delivery.event_payload(reminder)["tag"] == "event-7"  # sans second argument : discret
    assert push_delivery.task_payload(reminder)["url"] == "/taches"
    assert push_delivery.event_payload(reminder, "complet")["title"] == SECRET_TITLE


def test_le_contenu_n_est_meme_pas_lu_en_mode_discret():
    """Le titre, le lieu et les tâches ne sont jamais accédés : un rappel sans ces champs ne plante pas."""
    class Poisoned(dict):
        def __getitem__(self, key):
            if key != "id":
                raise AssertionError(f"champ lu en mode discret : {key}")
            return super().__getitem__(key)

        def get(self, key, default=None):
            raise AssertionError(f"champ lu en mode discret : {key}")

    reminder = Poisoned(id=3)
    assert push_delivery.event_payload(reminder)["tag"] == "event-3"
    assert push_delivery.task_payload(reminder, "discret")["tag"] == "task-3"


def test_le_texte_remis_au_systeme_de_notification_est_discret(db, alice, vapid, monkeypatch):
    """Bout en bout : on capte ce qui est remis à l'étape de chiffrement, juste avant de quitter FOX."""
    captured = []

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(webpush, "encrypt", lambda plaintext, p256dh, auth: captured.append(plaintext) or b"chiffre")
    monkeypatch.setattr(webpush, "vapid_headers", lambda *a, **k: {})
    monkeypatch.setattr(webpush.urllib.request, "urlopen", lambda request, timeout=None: Response())
    device(alice)
    event_with_secrets(db, alice)
    assert push_delivery.run_once() == 1  # envoi réel par webpush.send, sans doublure
    assert len(captured) == 1
    plaintext = captured[0].decode()
    assert "Rappel FOX" in plaintext and "/agenda" in plaintext
    assert_no_secret(plaintext)
    # et en mode complet, le détail voyage bien (le test ci-dessus ne passerait pas pour une mauvaise raison)
    full_detail(alice)
    event_with_secrets(db, alice, minutes=25)
    push_delivery.run_once()
    assert SECRET_TITLE in captured[-1].decode()


def test_deux_utilisateurs_deux_reglages_strictement_separes(db, alice, friend):
    device(alice, ENDPOINT + "-alice")
    device(friend, ENDPOINT + "-ami")
    full_detail(alice)  # alice choisit « complet » ; son ami garde le défaut
    event_with_secrets(db, alice)
    event_with_secrets(db, friend)
    rec = Recorder()
    assert push_delivery.run_once(rec) == 2
    by_endpoint = dict(rec.sent)
    assert by_endpoint[ENDPOINT + "-alice"]["title"] == SECRET_TITLE
    assert by_endpoint[ENDPOINT + "-ami"]["title"] == "Rappel FOX"
    assert_no_secret(by_endpoint[ENDPOINT + "-ami"])


def test_le_choix_de_l_un_ne_change_pas_celui_de_l_autre_et_s_applique_au_passage_suivant(db, alice, friend):
    device(alice, ENDPOINT + "-alice")
    device(friend, ENDPOINT + "-ami")
    full_detail(friend)
    privacy_store.save_settings(alice, notification_detail="discret")
    event_with_secrets(db, alice, minutes=20)
    event_with_secrets(db, friend, minutes=20)
    rec = Recorder()
    push_delivery.run_once(rec)
    by_endpoint = dict(rec.sent)
    assert by_endpoint[ENDPOINT + "-alice"]["title"] == "Rappel FOX" and by_endpoint[ENDPOINT + "-ami"]["title"] == SECRET_TITLE
    # alice repasse en complet : le prochain rappel d'alice change, celui de l'ami ne bouge pas
    full_detail(alice)
    privacy_store.save_settings(friend, notification_detail="discret")
    event_with_secrets(db, alice, minutes=26)
    event_with_secrets(db, friend, minutes=26)
    rec2 = Recorder()
    push_delivery.run_once(rec2)
    by_endpoint = dict(rec2.sent)
    assert by_endpoint[ENDPOINT + "-alice"]["title"] == SECRET_TITLE and by_endpoint[ENDPOINT + "-ami"]["title"] == "Rappel FOX"


def test_reglage_illisible_reste_discret(db, alice, monkeypatch):
    device(alice)
    full_detail(alice)  # réglage « complet » enregistré, mais la lecture échoue
    event_with_secrets(db, alice)

    def broken(user_id):
        raise RuntimeError("base indisponible")

    import privacy_store as real

    monkeypatch.setattr(real, "get_settings", broken)
    rec = Recorder()
    push_delivery.run_once(rec)
    assert rec.sent[0][1]["title"] == "Rappel FOX"
    assert_no_secret(rec.sent[0][1])


def test_le_test_de_notification_ne_contient_aucun_contenu_personnel(client, vapid, monkeypatch):
    sent = []
    monkeypatch.setattr(webpush, "send", lambda sub, payload, **k: sent.append(payload))
    subscribe(client)
    call(client, "POST", "/push/test")
    assert sent[0] == {"title": "FOX", "body": "Les notifications fonctionnent.", "url": "/reglages", "tag": "test"}


def test_le_reglage_se_change_par_l_api_et_s_applique_aux_rappels(app_client, accounts, db):
    web = app_client.test_client()
    signup(web, accounts, "lea")
    with db.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = 'lea'")
        lea = cur.fetchone()[0]
    device(lea)
    event_with_secrets(db, lea, minutes=20)
    rec = Recorder()
    push_delivery.run_once(rec)
    assert rec.sent[0][1]["title"] == "Rappel FOX"  # défaut
    assert web.post("/privacy/settings", json={"notification_detail": "complet"}).status_code == 200
    event_with_secrets(db, lea, minutes=27)
    push_delivery.run_once(rec)
    assert rec.sent[1][1]["title"] == SECRET_TITLE
