"""Notifications push : abonnements, rappels par canal (sans voler ceux de Telegram), envoi et isolation."""
from datetime import datetime, timedelta, timezone

import pytest

import webpush
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
    event_at(db, alice, 20, title="Réunion chantier")
    rec = Recorder()
    assert push_delivery.run_once(rec) == 1
    endpoint, payload = rec.sent[0]
    assert endpoint == ENDPOINT and payload["title"] == "Réunion chantier" and payload["url"] == "/agenda"
    assert payload["body"].startswith("À ") and payload["tag"].startswith("event-")
    assert push_delivery.run_once(rec) == 0 and len(rec.sent) == 1  # pas de doublon


def test_envoi_du_rappel_de_tache(db, alice):
    device(alice)
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
