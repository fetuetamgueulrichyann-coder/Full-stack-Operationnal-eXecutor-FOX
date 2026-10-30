"""Agenda interne (phase B5) : saisie, conflits, routes, rappels, briefing, purge."""
from datetime import datetime, timedelta

import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001


@pytest.fixture
def agenda(agent):
    import agenda as module

    return module


@pytest.fixture
def tz(agent):
    import tools

    return tools.TZ


@pytest.fixture
def now(tz):
    return datetime(2026, 10, 29, 10, 0, tzinfo=tz)  # un jeudi


@pytest.fixture
def alice(make_owner):
    return make_owner(OWNER)["id"]


@pytest.fixture
def friend(db, accounts):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return cur.fetchone()[0]


# --- Horaires ------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("demain 14h", (2026, 10, 30, 14, 0)), ("demain 14:30", (2026, 10, 30, 14, 30)), ("après-demain 8h15", (2026, 10, 31, 8, 15)),
    ("14h", (2026, 10, 29, 14, 0)), ("aujourd'hui à 18h", (2026, 10, 29, 18, 0)), ("27/11 à 9h30", (2026, 11, 27, 9, 30)),
    ("27/11/2026 9h", (2026, 11, 27, 9, 0)), ("2026-12-01 09:00", (2026, 12, 1, 9, 0)), ("2026-12-01T09:00", (2026, 12, 1, 9, 0)),
    ("lundi 10:00", (2026, 11, 2, 10, 0)), ("jeudi 15h", (2026, 11, 5, 15, 0)), ("Vendredi 9H", (2026, 10, 30, 9, 0)),
    ("29/10 à 10h", (2026, 10, 29, 10, 0)),
])
def test_horaires(agenda, now, text, expected):
    assert agenda.parse_start(text, now).timetuple()[:5] == expected


@pytest.mark.parametrize("text, message", [
    ("", "Indique quand"), ("demain", "Indique l'heure"), ("demain 25h", "Heure non reconnue"), ("demain 14h61", "Heure non reconnue"),
    ("demain 14h99", "Heure non reconnue"), ("hier 10h", "Date non reconnue"), ("31/02 9h", "Date non reconnue"),
    ("32/10 9h", "Date non reconnue"), ("25/10 9h", "déjà passé"), ("29/10 9h", "déjà passé"), ("2026-01-01 09:00", "déjà passé"),
    ("2026-13-01T09:00", "Date non reconnue"),
])
def test_horaires_invalides(agenda, now, text, message):
    with pytest.raises(agenda.AgendaInputError, match=message):
        agenda.parse_start(text, now)


def test_une_date_passee_sans_annee_n_est_pas_reportee_a_l_an_prochain(agenda, now):
    with pytest.raises(agenda.AgendaInputError, match="déjà passé"):
        agenda.parse_start("25/10 9h", now)


def test_un_instant_a_la_minute_pres_reste_accepte(agenda, now):
    assert agenda.parse_start("29/10 à 10h", now).hour == 10
    assert agenda.parse_start("2026-10-29 09:59", now).minute == 59  # 1 minute de tolérance


@pytest.mark.parametrize("text, minutes", [
    ("", 60), (None, 60), ("1h", 60), ("1h30", 90), ("2h", 120), ("45 min", 45), ("45min", 45), ("90", 90), ("30m", 30),
    ("24h", 1440), ("5 min", 5), ("0h05", 5),
])
def test_durees(agenda, text, minutes):
    assert agenda.parse_duration(text) == timedelta(minutes=minutes)


@pytest.mark.parametrize("text", ["4 min", "25h", "1441", "abc", "1h2", "-5", "1h99x"])
def test_durees_invalides(agenda, text):
    with pytest.raises(agenda.AgendaInputError):
        agenda.parse_duration(text)


def test_evenement_complet(agenda, now):
    event = agenda.parse_event("  Réunion  équipe | demain 14h | 1h30 | Douala centre ", now)
    assert event["title"] == "Réunion équipe" and event["location"] == "Douala centre"
    assert (event["start"].hour, event["end"].hour, event["end"].minute) == (14, 15, 30)


def test_evenement_minimal(agenda, now):
    event = agenda.parse_event("Appel | 14h", now)
    assert event["end"] - event["start"] == timedelta(hours=1) and event["location"] is None
    assert agenda.parse_event("Appel | 14h | | ", now)["location"] is None


@pytest.mark.parametrize("text, message", [
    ("", "Format"), ("Appel", "Format"), ("a|b|c|d|e", "Format"), (" | demain 14h", "titre"), ("x" * 151 + " | 14h", "trop long"),
    ("A | demain 14h | 1h | " + "x" * 101, "Lieu|lieu"), ("A | demain 14h | abc", "Durée"), ("A | demain", "Indique l'heure"),
])
def test_evenement_invalide(agenda, now, text, message):
    with pytest.raises(agenda.AgendaInputError, match=message):
        agenda.parse_event(text, now)


@pytest.mark.parametrize("word, days, offset", [
    (None, 7, 0), ("", 7, 0), ("semaine", 7, 0), ("mois", 30, 0), ("aujourd'hui", 1, 0), ("demain", 1, 1), ("Demain", 1, 1),
])
def test_periodes(agenda, now, word, days, offset):
    start, count = agenda.parse_range(word, now)
    assert count == days and start.date() == (now + timedelta(days=offset)).date() and (start.hour, start.minute) == (0, 0)


def test_periode_inconnue(agenda, now):
    with pytest.raises(agenda.AgendaInputError, match="/agenda"):
        agenda.parse_range("annee", now)


# --- Base de données --------------------------------------------------------------------------
def at(tz, day, hour, minute=0):
    return datetime(2030, 1, day, hour, minute, tzinfo=tz)


def make(db, user, tz, title, day, hour, hours=1, minute=0, location=None):
    start = at(tz, day, hour, minute)
    return db.create_event(user, title, start, start + timedelta(hours=hours), location)


def test_creation_et_lecture(db, alice, tz):
    event = make(db, alice, tz, "Réunion", 10, 14, location="Douala")
    assert event["title"] == "Réunion" and event["location"] == "Douala"
    assert db.get_event(alice, event["id"])["start_at"] == at(tz, 10, 14)


def test_liste_chronologique_et_fenetre(db, alice, tz):
    for title, day, hour in (("tard", 10, 18), ("tot", 10, 8), ("autre jour", 11, 9)):
        make(db, alice, tz, title, day, hour)
    day_10 = db.list_events(alice, at(tz, 10, 0), at(tz, 11, 0))
    assert [e["title"] for e in day_10] == ["tot", "tard"]
    assert [e["title"] for e in db.list_events(alice, at(tz, 10, 0), at(tz, 12, 0))] == ["tot", "tard", "autre jour"]


def test_la_fenetre_inclut_un_rendez_vous_qui_la_traverse(db, alice, tz):
    make(db, alice, tz, "nuit", 10, 23, hours=3)  # 23:00 -> 02:00
    assert [e["title"] for e in db.list_events(alice, at(tz, 11, 0), at(tz, 12, 0))] == ["nuit"]
    assert db.list_events(alice, at(tz, 11, 2), at(tz, 12, 0)) == []  # fini pile à 02:00 : exclu


def test_conflits(db, alice, tz):
    make(db, alice, tz, "A", 10, 14, hours=2)  # 14-16
    assert [e["title"] for e in db.find_conflicts(alice, at(tz, 10, 15), at(tz, 10, 17))] == ["A"]
    assert [e["title"] for e in db.find_conflicts(alice, at(tz, 10, 13), at(tz, 10, 15))] == ["A"]
    assert [e["title"] for e in db.find_conflicts(alice, at(tz, 10, 14, 30), at(tz, 10, 15))] == ["A"]  # inclus
    assert [e["title"] for e in db.find_conflicts(alice, at(tz, 10, 12), at(tz, 10, 18))] == ["A"]  # englobant
    assert db.find_conflicts(alice, at(tz, 10, 16), at(tz, 10, 17)) == []  # commence quand A finit
    assert db.find_conflicts(alice, at(tz, 10, 12), at(tz, 10, 14)) == []  # finit quand A commence


def test_conflits_exclut_soi_meme_et_les_autres_utilisateurs(db, alice, friend, tz):
    mine = make(db, alice, tz, "A", 10, 14)
    make(db, friend, tz, "B", 10, 14)
    assert db.find_conflicts(alice, at(tz, 10, 14), at(tz, 10, 15), exclude_id=mine["id"]) == []
    assert [e["title"] for e in db.find_conflicts(alice, at(tz, 10, 14), at(tz, 10, 15))] == ["A"]


def test_limite_de_rendez_vous_a_venir(db, alice, tz):
    for hour in (8, 9):
        make(db, alice, tz, f"R{hour}", 10, hour)
    with pytest.raises(db.EventLimit):
        db.create_event(alice, "trop", at(tz, 10, 12), at(tz, 10, 13), max_upcoming=2)
    # les rendez-vous passés ne comptent pas
    old = datetime(2020, 1, 1, 9, tzinfo=tz)
    db.create_event(alice, "ancien", old, old + timedelta(hours=1), max_upcoming=0)
    assert db.create_event(alice, "ok", at(tz, 10, 12), at(tz, 10, 13), max_upcoming=3)["title"] == "ok"
    with pytest.raises(db.EventLimit):
        db.create_event(alice, "encore", at(tz, 10, 14), at(tz, 10, 15), max_upcoming=3)


def test_modification_partielle(db, alice, tz):
    event = make(db, alice, tz, "Réunion", 10, 14, location="Douala")
    changed = db.update_event(alice, event["id"], title="Point")
    assert (changed["title"], changed["location"], changed["start_at"]) == ("Point", "Douala", at(tz, 10, 14))
    assert db.update_event(alice, event["id"], location=None)["location"] is None
    moved = db.update_event(alice, event["id"], start_at=at(tz, 11, 9), end_at=at(tz, 11, 10))
    assert (moved["start_at"], moved["end_at"], moved["title"]) == (at(tz, 11, 9), at(tz, 11, 10), "Point")
    assert db.update_event(alice, 999999, title="x") is None


def test_suppression_isolee(db, alice, friend, tz):
    event = make(db, alice, tz, "A", 10, 14)
    assert not db.delete_event(friend, event["id"]) and db.get_event(friend, event["id"]) is None
    assert db.update_event(friend, event["id"], title="piraté") is None
    assert db.delete_event(alice, event["id"]) and not db.delete_event(alice, event["id"])


def test_purge_des_rendez_vous_passes(db, alice, tz):
    for year, title in ((2020, "vieux"), (2030, "futur")):
        db.create_event(alice, title, datetime(year, 1, 5, 9, tzinfo=tz), datetime(year, 1, 5, 10, tzinfo=tz), max_upcoming=0)
    assert db.purge_old_events(60) == 1
    assert [e["title"] for e in db.list_events(alice, datetime(2019, 1, 1, tzinfo=tz), datetime(2031, 1, 1, tzinfo=tz))] == ["futur"]
    assert db.purge_old_events(0) == 0
    assert db.purge_history()["events"] == 0


def test_purge_utilise_la_duree_par_defaut(db, alice, tz, monkeypatch):
    start = datetime.now(tz) - timedelta(days=30)
    db.create_event(alice, "il y a 30 jours", start, start + timedelta(hours=1), max_upcoming=0)
    monkeypatch.setattr(db, "EVENT_RETENTION_DAYS", 20)
    assert db.purge_old_events() == 1


# --- Rappels --------------------------------------------------------------------------------------
def event_at(db, user, minutes_from_now, title="RDV", created_minutes_ago=120):
    """Rendez-vous commençant dans N minutes, créé il y a `created_minutes_ago` minutes."""
    start = datetime.now().astimezone() + timedelta(minutes=minutes_from_now)
    event = db.create_event(user, title, start, start + timedelta(hours=1), max_upcoming=0)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - make_interval(mins => %s) WHERE id = %s", (created_minutes_ago, event["id"]))
    return event


def test_rappel_dans_la_fenetre(db, alice):
    event_at(db, alice, 20)
    due = db.claim_due_event_reminders(30)
    assert [d["title"] for d in due] == ["RDV"] and due[0]["telegram_id"] == OWNER


def test_pas_de_rappel_trop_tot(db, alice):
    event_at(db, alice, 45)
    assert db.claim_due_event_reminders(30) == []


def test_pas_de_rappel_apres_le_debut_depuis_longtemps(db, alice):
    event_at(db, alice, -20)
    assert db.claim_due_event_reminders(30) == []
    event_at(db, alice, -5, title="juste commencé")
    assert [d["title"] for d in db.claim_due_event_reminders(30)] == ["juste commencé"]


def test_un_rappel_n_est_rendu_qu_une_fois(db, alice):
    event_at(db, alice, 10)
    assert len(db.claim_due_event_reminders(30)) == 1
    assert db.claim_due_event_reminders(30) == []


def test_pas_de_rappel_immediat_pour_un_rendez_vous_cree_dans_la_fenetre(db, alice, tz):
    start = datetime.now(tz) + timedelta(minutes=10)
    db.create_event(alice, "bientôt", start, start + timedelta(hours=1))
    assert db.claim_due_event_reminders(30) == []


def test_rappel_desactive(db, alice):
    event_at(db, alice, 10)
    assert db.claim_due_event_reminders(0) == []


def test_deplacer_un_rendez_vous_remet_le_rappel_a_zero(db, alice):
    event = event_at(db, alice, 10)
    db.claim_due_event_reminders(30)
    later = event["start_at"] + timedelta(minutes=5)
    db.update_event(alice, event["id"], start_at=later, end_at=later + timedelta(hours=1))
    with db.cursor() as cur:
        cur.execute("SELECT reminded_at FROM events WHERE id = %s", (event["id"],))
        assert cur.fetchone()[0] is None
        cur.execute("UPDATE events SET updated_at = now() - interval '2 hours' WHERE id = %s", (event["id"],))
    assert len(db.claim_due_event_reminders(30)) == 1


def test_changer_le_titre_garde_le_rappel_envoye(db, alice):
    event = event_at(db, alice, 10)
    db.claim_due_event_reminders(30)
    db.update_event(alice, event["id"], title="Renommé")
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - interval '2 hours' WHERE id = %s", (event["id"],))
    assert db.claim_due_event_reminders(30) == []


def test_pas_de_rappel_pour_un_compte_desactive_ou_sans_telegram(db, alice, friend):
    event_at(db, friend, 10, title="ami")
    with db.cursor() as cur:
        cur.execute("UPDATE users SET disabled_at = now() WHERE id = %s", (friend,))
    event_at(db, alice, 10, title="moi")
    assert [d["title"] for d in db.claim_due_event_reminders(30)] == ["moi"]


# --- Routes ------------------------------------------------------------------------------------------
@pytest.fixture
def client(app_module, alice, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


def call(client, method, path, telegram_id=OWNER, **payload):
    if method == "GET":
        sep = "&" if "?" in path else "?"
        return client.get(f"{path}{sep}telegram_id={telegram_id}", headers=HEADERS)
    return getattr(client, method.lower())(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


@pytest.mark.parametrize("method, path", [("GET", "/events"), ("POST", "/events"), ("PATCH", "/events/1"), ("DELETE", "/events/1")])
def test_les_routes_exigent_une_identite(client, method, path):
    assert call(client, method, path, telegram_id=999).status_code == 403
    if method != "GET":
        assert client.open(path, method=method, json={}).status_code == 401
        assert client.open(path, method=method, data="x", headers=HEADERS).status_code == 415


def test_creer_et_lister(client, tz):
    response = call(client, "POST", "/events", text="Réunion | demain 14h | 1h30 | Douala")
    assert response.status_code == 201
    data = response.get_json()
    event = data["event"]
    assert event["title"] == "Réunion" and event["location"] == "Douala" and data["conflicts"] == []
    assert event["start"][11:16] == "14:00" and event["end"][11:16] == "15:30"
    listed = call(client, "GET", "/events").get_json()
    assert [e["id"] for e in listed["events"]] == [event["id"]] and len(listed["today"]) == 10


def test_creer_par_champs(client):
    response = call(client, "POST", "/events", title="Appel", start="2030-05-02 09:00", duration="30 min", location="Zoom")
    event = response.get_json()["event"]
    assert event["start"].startswith("2030-05-02T09:00") and event["end"][11:16] == "09:30" and event["location"] == "Zoom"


@pytest.mark.parametrize("payload", [
    {"text": ""}, {"text": "Titre seul"}, {"text": "A | hier 10h"}, {"text": "A | 01/01/2020 10h"}, {"text": "A | demain 14h | abc"},
    {"title": "", "start": "demain 14h"}, {"title": "A"}, {},
])
def test_creation_invalide(client, payload):
    assert call(client, "POST", "/events", **payload).status_code == 400
    assert call(client, "GET", "/events?range=mois").get_json()["events"] == []


def test_le_conflit_est_signale_mais_le_rendez_vous_est_cree(client):
    call(client, "POST", "/events", text="Premier | demain 14h | 2h")
    response = call(client, "POST", "/events", text="Second | demain 15h | 1h")
    assert response.status_code == 201
    assert [c["title"] for c in response.get_json()["conflicts"]] == ["Premier"]
    assert len(call(client, "GET", "/events").get_json()["events"]) == 2


def test_limite(client, db, monkeypatch):
    monkeypatch.setattr(db, "MAX_UPCOMING_EVENTS", 2)
    codes = [call(client, "POST", "/events", text=f"R{i} | demain {8 + i}h").status_code for i in range(3)]
    assert codes == [201, 201, 409]


def test_periodes_de_la_liste(client, tz):
    ten_days = (datetime.now(tz) + timedelta(days=10)).strftime("%Y-%m-%d")
    call(client, "POST", "/events", text="demain | demain 10h")
    call(client, "POST", "/events", text=f"dans dix jours | {ten_days} 10:00")
    names = lambda r: [e["title"] for e in call(client, "GET", f"/events?range={r}").get_json()["events"]]  # noqa: E731
    assert names("demain") == ["demain"] and names("aujourd'hui") == []
    assert names("semaine") == ["demain"] and names("mois") == ["demain", "dans dix jours"]
    assert names("") == ["demain"]
    assert call(client, "GET", "/events?range=annee").status_code == 400


def test_modifier(client):
    event = call(client, "POST", "/events", text="Réunion | demain 14h | 1h30 | Douala").get_json()["event"]
    path = f"/events/{event['id']}"
    changed = call(client, "PATCH", path, title="Point").get_json()["event"]
    assert changed["title"] == "Point" and changed["start"] == event["start"] and changed["location"] == "Douala"
    moved = call(client, "PATCH", path, start="demain 16h").get_json()["event"]
    assert moved["start"][11:16] == "16:00" and moved["end"][11:16] == "17:30"  # la durée est conservée
    longer = call(client, "PATCH", path, duration="2h").get_json()["event"]
    assert longer["start"][11:16] == "16:00" and longer["end"][11:16] == "18:00"
    assert call(client, "PATCH", path, location="").get_json()["event"]["location"] is None


def test_modifier_invalide(client):
    event = call(client, "POST", "/events", text="Réunion | demain 14h").get_json()["event"]
    path = f"/events/{event['id']}"
    for payload in ({}, {"title": ""}, {"start": "hier 10h"}, {"duration": "abc"}, {"start": "25/10 9h"}):
        assert call(client, "PATCH", path, **payload).status_code == 400
    assert call(client, "PATCH", "/events/99999", title="x").status_code == 404


def test_modification_signale_les_conflits(client):
    call(client, "POST", "/events", text="Premier | demain 14h | 2h")
    second = call(client, "POST", "/events", text="Second | demain 17h").get_json()["event"]
    response = call(client, "PATCH", f"/events/{second['id']}", start="demain 15h")
    assert [c["title"] for c in response.get_json()["conflicts"]] == ["Premier"]


def test_supprimer(client):
    event = call(client, "POST", "/events", text="Réunion | demain 14h").get_json()["event"]
    assert call(client, "DELETE", f"/events/{event['id']}").status_code == 200
    assert call(client, "DELETE", f"/events/{event['id']}").status_code == 404
    assert call(client, "GET", "/events").get_json()["events"] == []


def test_chacun_son_agenda(client, friend):
    mine = call(client, "POST", "/events", text="Privé | demain 14h").get_json()["event"]
    assert call(client, "GET", "/events", telegram_id=OTHER).get_json()["events"] == []
    assert call(client, "PATCH", f"/events/{mine['id']}", telegram_id=OTHER, title="piraté").status_code == 404
    assert call(client, "DELETE", f"/events/{mine['id']}", telegram_id=OTHER).status_code == 404
    other = call(client, "POST", "/events", telegram_id=OTHER, text="Autre | demain 14h").get_json()
    assert other["conflicts"] == []  # pas de conflit avec l'agenda de quelqu'un d'autre
    assert call(client, "GET", "/events").get_json()["events"][0]["title"] == "Privé"


def test_les_testeurs_ont_un_agenda(client, friend):
    assert call(client, "POST", "/events", telegram_id=OTHER, text="RDV | demain 9h").status_code == 201


def test_route_des_rappels(client, db, alice, friend, monkeypatch):
    event_at(db, alice, 10, title="Bientôt")
    event_at(db, friend, 10, title="Chez l'ami")
    assert client.post("/events/reminders/claim", json={}).status_code == 401
    assert client.post("/events/reminders/claim", json={}, headers={"X-Fox-Service-Token": "faux"}).status_code == 401
    reminders = client.post("/events/reminders/claim", json={}, headers=HEADERS).get_json()["reminders"]
    assert sorted(r["title"] for r in reminders) == ["Bientôt", "Chez l'ami"]
    first = next(r for r in reminders if r["title"] == "Bientôt")
    assert first["telegram_id"] == OWNER and len(first["start"]) == 22 and first["location"] is None
    assert client.post("/events/reminders/claim", json={}, headers=HEADERS).get_json()["reminders"] == []


def test_rappels_desactives_par_la_configuration(client, db, alice, agenda, monkeypatch):
    event_at(db, alice, 10)
    monkeypatch.setattr(agenda, "REMIND_MINUTES", 0)
    assert client.post("/events/reminders/claim", json={}, headers=HEADERS).get_json()["reminders"] == []


def test_journal_de_l_agenda(client, db, alice):
    import journal

    event = call(client, "POST", "/events", text="Rendez-vous très privé | demain 14h | 1h | Chez Paul").get_json()["event"]
    call(client, "PATCH", f"/events/{event['id']}", title="Autre")
    call(client, "DELETE", f"/events/{event['id']}")
    assert {e["text"] for e in journal.entries(alice, 20)} == {"Rendez-vous ajouté", "Rendez-vous modifié", "Rendez-vous supprimé"}
    with db.cursor() as cur:
        cur.execute("SELECT details::text FROM audit_log WHERE event LIKE 'event_%'")
        assert not any(w in " ".join(r[0] for r in cur.fetchall()) for w in ("privé", "Paul", "Autre"))


# --- Briefing --------------------------------------------------------------------------------------------
def test_le_briefing_contient_les_rendez_vous_du_jour(db, alice, friend, tz):
    import briefing

    now = datetime(2026, 10, 29, 8, 0, tzinfo=tz)
    for user, title, hour in ((alice, "Réunion", 14), (alice, "Appel", 9), (friend, "Chez l'ami", 10)):
        db.create_event(user, title, datetime(2026, 10, 29, hour, tzinfo=tz), datetime(2026, 10, 29, hour + 1, tzinfo=tz), max_upcoming=0)
    db.create_event(alice, "Demain", datetime(2026, 10, 30, 9, tzinfo=tz), datetime(2026, 10, 30, 10, tzinfo=tz), max_upcoming=0)
    text = briefing.build(alice, "tester", now)
    assert "**Agenda (2)**\n- 09:00-10:00 Appel\n- 14:00-15:00 Réunion" in text and "Chez l'ami" not in text and "Demain" not in text
    assert "Rien de prévu" not in text
    assert "Agenda" in briefing.build(friend, "tester", now) and "Réunion" not in briefing.build(friend, "tester", now)


def test_le_briefing_fusionne_google_et_agenda_interne(db, alice, tz, monkeypatch):
    import briefing

    now = datetime(2026, 10, 29, 8, 0, tzinfo=tz)
    db.create_event(alice, "Interne", datetime(2026, 10, 29, 11, tzinfo=tz), datetime(2026, 10, 29, 12, tzinfo=tz), max_upcoming=0)
    monkeypatch.setattr(briefing, "agenda_events", lambda role, n: [{"debut": "2026-10-29T09:00+01:00", "fin": "2026-10-29T10:00+01:00", "titre": "Google"}])
    text = briefing.build(alice, "owner", now)
    assert "**Agenda (2)**\n- 09:00-10:00 Google\n- 11:00-12:00 Interne" in text


def test_google_en_panne_n_efface_pas_l_agenda_interne(db, alice, tz, monkeypatch):
    import briefing

    now = datetime(2026, 10, 29, 8, 0, tzinfo=tz)
    db.create_event(alice, "Interne", datetime(2026, 10, 29, 11, tzinfo=tz), datetime(2026, 10, 29, 12, tzinfo=tz), max_upcoming=0)
    monkeypatch.setattr(briefing, "agenda_events", lambda role, n: False)
    text = briefing.build(alice, "owner", now)
    assert "- 11:00-12:00 Interne" in text and "Agenda Google indisponible pour le moment." in text
