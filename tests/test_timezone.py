"""Fuseau horaire par utilisateur (phase B6)."""
from datetime import datetime, time, timedelta, timezone

import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001


@pytest.fixture
def alice(make_owner):
    return make_owner(OWNER)["id"]


@pytest.fixture
def friend(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return cur.fetchone()[0]


@pytest.fixture
def client(app_module, alice, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


def call(client, method, path, telegram_id=OWNER, **payload):
    if method == "GET":
        sep = "&" if "?" in path else "?"
        return client.get(f"{path}{sep}telegram_id={telegram_id}", headers=HEADERS)
    return getattr(client, method.lower())(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


@pytest.fixture
def timezones(agent):
    import timezones as module

    return module


# --- Choix du fuseau -------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("Paris", "Europe/Paris"), ("  PARIS ", "Europe/Paris"), ("Europe/Paris", "Europe/Paris"), ("europe/paris", "Europe/Paris"),
    ("Yaoundé", "Africa/Douala"), ("le caire", "Africa/Cairo"), ("America/New_York", "America/New_York"), ("UTC", "UTC"),
    ("new york", "America/New_York"),
])
def test_resolution_du_fuseau(timezones, text, expected):
    assert timezones.resolve(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "Atlantide", "Europe/Atlantide", "../etc/passwd", "x" * 200])
def test_fuseau_inconnu_refuse(timezones, text):
    with pytest.raises(ValueError):
        timezones.resolve(text)


def test_contexte_du_fuseau(tools):
    assert tools.tz() == tools.TZ
    with tools.user_zone("Asia/Tokyo"):
        assert tools.tz_name() == "Asia/Tokyo"
        with tools.user_zone("nom invalide"):
            assert tools.tz() == tools.TZ  # nom invalide : retour au fuseau par défaut
        assert tools.tz_name() == "Asia/Tokyo"
    assert tools.tz() == tools.TZ


# --- Routes --------------------------------------------------------------------------------------
def test_fuseau_par_defaut_puis_choix_puis_retour(client, tools):
    data = call(client, "GET", "/timezone").get_json()
    assert data["timezone"] == tools.TZ_NAME and data["custom"] is False
    data = call(client, "POST", "/timezone", timezone="Asia/Tokyo").get_json()
    assert data["timezone"] == "Asia/Tokyo" and data["custom"] is True and data["now"].endswith("+09:00")
    assert call(client, "GET", "/timezone").get_json()["timezone"] == "Asia/Tokyo"
    data = call(client, "POST", "/timezone", timezone="défaut").get_json()
    assert data["timezone"] == tools.TZ_NAME and data["custom"] is False


def test_fuseau_invalide_ou_absent(client):
    assert call(client, "POST", "/timezone", timezone="Atlantide").status_code == 400
    assert call(client, "POST", "/timezone", timezone="").status_code == 400
    assert call(client, "POST", "/timezone").status_code == 400
    assert call(client, "POST", "/timezone", timezone=123).status_code == 400


def test_le_fuseau_d_un_utilisateur_n_affecte_pas_les_autres(client, friend, tools):
    call(client, "POST", "/timezone", timezone="Asia/Tokyo")
    assert call(client, "GET", "/timezone", telegram_id=OTHER).get_json()["timezone"] == tools.TZ_NAME
    assert tools.tz() == tools.TZ  # rien ne fuit hors de la requête


def test_le_fuseau_est_journalise(client, db, alice):
    call(client, "POST", "/timezone", timezone="Europe/Paris")
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM audit_log WHERE event = 'timezone_set' AND user_id = %s", (alice,))
        assert cur.fetchone()


def test_routes_protegees(client):
    assert client.get("/timezone?telegram_id=1").status_code == 401
    assert client.post("/timezone", json={"timezone": "UTC"}).status_code == 401


# --- Effets sur l'agenda ----------------------------------------------------------------------
def test_un_rendez_vous_est_saisi_et_affiche_dans_le_fuseau_de_l_utilisateur(client, friend):
    call(client, "POST", "/timezone", timezone="Asia/Tokyo")
    created = call(client, "POST", "/events", text="Appel | demain 14h").get_json()["event"]
    assert created["start"].endswith("T14:00+09:00")
    other = call(client, "POST", "/events", telegram_id=OTHER, text="Appel | demain 14h").get_json()["event"]
    assert other["start"].endswith("T14:00+01:00")  # fuseau par défaut (tests : Africa/Douala)
    listed = call(client, "GET", "/events?range=semaine").get_json()["events"]
    assert listed[0]["start"].endswith("+09:00")


def test_le_changement_de_fuseau_ne_deplace_pas_les_rendez_vous(client, db, alice):
    call(client, "POST", "/events", text="Appel | demain 14h")
    with db.cursor() as cur:
        cur.execute("SELECT start_at FROM events WHERE user_id = %s", (alice,))
        before = cur.fetchone()[0]
    call(client, "POST", "/timezone", timezone="Asia/Tokyo")
    with db.cursor() as cur:
        cur.execute("SELECT start_at FROM events WHERE user_id = %s", (alice,))
        assert cur.fetchone()[0] == before  # le même instant
    shown = call(client, "GET", "/events?range=semaine").get_json()["events"][0]["start"]
    assert datetime.fromisoformat(shown) == before


def test_le_prompt_donne_l_heure_du_fuseau_de_l_utilisateur(agent, tools):
    with tools.user_zone("Asia/Tokyo"):
        prompt = agent.system_prompt(role="tester")
    assert "Asia/Tokyo" in prompt and "+09:00" in prompt
    assert tools.TZ_NAME in agent.system_prompt(role="tester")


def test_rappel_dans_le_fuseau_de_l_utilisateur(client, db, alice):
    call(client, "POST", "/timezone", timezone="Asia/Tokyo")
    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO events (user_id, title, start_at, end_at, updated_at) VALUES (%s, 'Tokyo', %s, %s, now() - interval '1 hour')",
            (alice, start, start + timedelta(hours=1)),
        )
    reminders = client.post("/events/reminders/claim", json={}, headers=HEADERS).get_json()["reminders"]
    assert reminders[0]["start"].endswith("+09:00")


# --- Briefing du matin ---------------------------------------------------------------------
def test_le_briefing_part_a_l_heure_locale_de_chacun(db, alice, friend, tools):
    db.set_user_timezone(friend, "Asia/Tokyo")
    db.set_briefing(alice, True, "08:00")
    db.set_briefing(friend, True, "08:00")
    # 23:30 UTC : 00:30 à Douala (UTC+1, jour suivant), 08:30 à Tokyo (UTC+9)
    instant = datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc)
    due = db.claim_due_briefings(instant, tools.TZ_NAME)
    assert [d["telegram_id"] for d in due] == [OTHER] and due[0]["timezone"] == "Asia/Tokyo"
    assert db.claim_due_briefings(instant, tools.TZ_NAME) == []  # un seul envoi par jour local
    morning = datetime(2026, 10, 23, 7, 30, tzinfo=timezone.utc)  # 08:30 à Douala
    assert [d["telegram_id"] for d in db.claim_due_briefings(morning, tools.TZ_NAME)] == [OWNER]


def test_le_briefing_manque_n_est_pas_envoye_apres_la_fenetre(db, alice, tools):
    db.set_briefing(alice, True, "08:00")
    late = datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc).replace(hour=14)  # 15:00 à Douala : plus de 6 h après
    assert db.claim_due_briefings(late, tools.TZ_NAME) == []


def test_briefing_du_jour_dans_le_fuseau_de_l_utilisateur(db, alice, tools):
    import briefing

    db.set_user_timezone(alice, "Asia/Tokyo")
    with tools.user_zone("Asia/Tokyo"):
        today = datetime.now(tools.tz())
        start = datetime(today.year, today.month, today.day, 12, tzinfo=tools.tz())  # midi, heure de Tokyo
        with db.cursor() as cur:
            cur.execute("INSERT INTO events (user_id, title, start_at, end_at) VALUES (%s, 'Midi', %s, %s)",
                        (alice, start, start + timedelta(hours=1)))
        events = briefing.own_events(alice, datetime.now(tools.tz()))
    assert [e["debut"] for e in events] == [start.isoformat(timespec="minutes")]


def test_la_liste_de_l_agenda_commence_au_jour_local_de_l_utilisateur(client, app_module, monkeypatch):
    """20:00 UTC le 1er novembre : il est déjà le 2 à Tokyo (05:00) mais encore le 1er à Douala (21:00)."""
    fixed = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(app_module, "datetime", FrozenDatetime)
    assert call(client, "GET", "/events?range=aujourd'hui").get_json()["today"] == "2026-11-01"
    call(client, "POST", "/timezone", timezone="Asia/Tokyo")
    assert call(client, "GET", "/events?range=aujourd'hui").get_json()["today"] == "2026-11-02"
