"""Rendez-vous récurrents (phase B6) : calcul des occurrences, base, rappels, routes."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001
PARIS = ZoneInfo("Europe/Paris")
TOKYO = ZoneInfo("Asia/Tokyo")


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
def friend(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return cur.fetchone()[0]


def series(start, duration=timedelta(hours=1), rule="daily", until=None, skipped=(), zone=None, ident=1):
    return {
        "id": ident, "title": "Série", "location": None, "start_at": start, "end_at": start + duration,
        "recurrence": rule, "recurrence_until": until, "skipped_days": list(skipped),
        "series_tz": zone, "created_at": start, "updated_at": start,
    }


def days_of(found):
    return [o["occurrence"] for o in found]


# --- Calcul des occurrences ----------------------------------------------------------------------
def test_quotidien(agenda, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    found = agenda.occurrences(series(first, zone=tz.key), first, first + timedelta(days=4))
    assert days_of(found) == ["2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]
    assert {o["start_at"].astimezone(tz).hour for o in found} == {14}
    assert all(o["end_at"] - o["start_at"] == timedelta(hours=1) for o in found)


def test_jours_ouvres(agenda, tz):
    first = datetime(2026, 11, 2, 9, 0, tzinfo=tz)  # lundi
    found = agenda.occurrences(series(first, rule="weekdays", zone=tz.key), first, first + timedelta(days=7))
    assert days_of(found) == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]


def test_hebdomadaire(agenda, tz):
    first = datetime(2026, 10, 29, 9, 0, tzinfo=tz)  # jeudi
    found = agenda.occurrences(series(first, rule="weekly", zone=tz.key), first, first + timedelta(days=15))
    assert days_of(found) == ["2026-10-29", "2026-11-05", "2026-11-12"]


def test_mensuel_ramene_a_la_fin_du_mois(agenda, tz):
    first = datetime(2027, 1, 31, 9, 0, tzinfo=tz)
    found = agenda.occurrences(series(first, rule="monthly", zone=tz.key), first, datetime(2027, 5, 1, tzinfo=tz))
    assert days_of(found) == ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"]
    leap = datetime(2027, 12, 31, 9, 0, tzinfo=tz)
    found = agenda.occurrences(series(leap, rule="monthly", zone=tz.key), datetime(2028, 2, 1, tzinfo=tz), datetime(2028, 3, 1, tzinfo=tz))
    assert days_of(found) == ["2028-02-29"]


def test_fin_incluse_et_jours_ignores(agenda, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = series(first, until=date(2026, 11, 2), skipped=[date(2026, 10, 31)], zone=tz.key)
    found = agenda.occurrences(event, first, first + timedelta(days=30))
    assert days_of(found) == ["2026-10-29", "2026-10-30", "2026-11-01", "2026-11-02"]


def test_periode_avant_le_debut_ou_loin_apres(agenda, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = series(first, zone=tz.key)
    assert agenda.occurrences(event, first - timedelta(days=10), first - timedelta(days=1)) == []
    far = datetime(2028, 3, 5, tzinfo=tz)
    assert days_of(agenda.occurrences(event, far, far + timedelta(days=2))) == ["2028-03-05", "2028-03-06"]


def test_occurrence_a_cheval_sur_minuit_visible_le_lendemain(agenda, tz):
    first = datetime(2026, 10, 29, 22, 0, tzinfo=tz)
    event = series(first, duration=timedelta(hours=5), zone=tz.key)
    morning = datetime(2026, 10, 30, 0, 0, tzinfo=tz)
    found = agenda.occurrences(event, morning, morning + timedelta(days=1))
    assert days_of(found) == ["2026-10-29", "2026-10-30"]  # celle de la veille finit à 03:00


def test_l_heure_locale_de_la_serie_survit_au_changement_d_heure(agenda):
    first = datetime(2026, 10, 24, 9, 0, tzinfo=PARIS)  # avant le passage à l'heure d'hiver (25/10)
    event = series(first, zone="Europe/Paris")
    found = agenda.occurrences(event, first, first + timedelta(days=3))
    assert [o["start_at"].astimezone(PARIS).hour for o in found] == [9, 9, 9]
    assert [o["start_at"].astimezone(timezone.utc).hour for o in found] == [7, 8, 8]


def test_la_serie_garde_le_fuseau_de_sa_creation(agenda, tz, now):
    first = datetime(2026, 10, 29, 9, 0, tzinfo=TOKYO)
    found = agenda.occurrences(series(first, zone="Asia/Tokyo"), first, first + timedelta(days=2))
    assert [o["start_at"].astimezone(TOKYO).hour for o in found] == [9, 9]  # même heure à Tokyo, pas à Douala


def test_un_rendez_vous_simple_passe_tel_quel(agenda, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    single = {**series(first), "recurrence": None}
    assert agenda.occurrences(single, first, first + timedelta(days=1)) == [single]
    assert agenda.occurrences(single, first + timedelta(days=1), first + timedelta(days=2)) == []


# --- Saisie de la répétition -----------------------------------------------------------------------
@pytest.mark.parametrize("text, rule", [
    ("chaque jour", "daily"), ("Tous les jours", "daily"), ("quotidien", "daily"),
    ("chaque semaine", "weekly"), ("toutes les semaines", "weekly"), ("hebdomadaire", "weekly"),
    ("jours ouvrés", "weekdays"), ("en semaine", "weekdays"), ("du lundi au vendredi", "weekdays"),
    ("chaque mois", "monthly"), ("tous les mois", "monthly"), ("mensuel", "monthly"),
])
def test_regles_de_repetition(agenda, now, text, rule):
    assert agenda.parse_repeat(text, None, now) == (rule, None)


def test_fin_de_repetition(agenda, now):
    assert agenda.parse_repeat("chaque semaine jusqu'au 31/12", None, now) == ("weekly", date(2026, 12, 31))
    assert agenda.parse_repeat("tous les jours jusqu'au 2027-02-01", None, now) == ("daily", date(2027, 2, 1))
    assert agenda.parse_repeat("chaque mois jusqu au 15/01/2027", None, now) == ("monthly", date(2027, 1, 15))


@pytest.mark.parametrize("text, message", [
    ("", "non reconnue"), ("parfois", "non reconnue"), ("chaque semaine jusqu'au hier", "Date non reconnue"),
    ("chaque jour jusqu'au 01/10", "déjà passée"), ("chaque jour jusqu'au 99/99", "Date non reconnue"),
])
def test_repetition_invalide(agenda, now, text, message):
    with pytest.raises(agenda.AgendaInputError, match=message):
        agenda.parse_repeat(text, None, now)


def test_coherence_avec_le_premier_rendez_vous(agenda, now, tz):
    saturday = datetime(2026, 10, 31, 9, 0, tzinfo=tz)
    with pytest.raises(agenda.AgendaInputError, match="jour de semaine"):
        agenda.parse_repeat("jours ouvrés", saturday, now)
    thursday = datetime(2026, 10, 29, 9, 0, tzinfo=tz)
    with pytest.raises(agenda.AgendaInputError, match="mardi"):
        agenda.parse_repeat("chaque mardi", thursday, now)
    assert agenda.parse_repeat("chaque jeudi", thursday, now) == ("weekly", None)
    with pytest.raises(agenda.AgendaInputError, match="après le premier"):
        agenda.parse_repeat("chaque jour jusqu'au 29/10", datetime(2026, 11, 3, 9, tzinfo=tz), now)


def test_evenement_avec_repetition(agenda, now):
    fields = agenda.parse_event("Point | lundi 9h | 30 min | Salle 2 | chaque semaine jusqu'au 31/12", now)
    assert (fields["repeat"], fields["until"], fields["location"]) == ("weekly", date(2026, 12, 31), "Salle 2")
    sparse = agenda.parse_event("Point | lundi 9h | | | chaque semaine", now)
    assert sparse["location"] is None and sparse["end"] - sparse["start"] == timedelta(hours=1) and sparse["repeat"] == "weekly"
    assert agenda.parse_event("Point | lundi 9h", now)["repeat"] is None
    with pytest.raises(agenda.AgendaInputError, match="non reconnue"):
        agenda.parse_event("Point | lundi 9h | 1h | | bientôt", now)


def test_libelle_de_repetition(agenda):
    assert agenda.repeat_label("weekly") == "chaque semaine"
    assert agenda.repeat_label("daily", date(2026, 12, 31)) == "chaque jour jusqu'au 31/12/2026"


# --- Base de données ---------------------------------------------------------------------------------
def make_series(db, user, first, rule="daily", until=None, title="Série", hours=1):
    return db.create_event(user, title, first, first + timedelta(hours=hours), None, max_upcoming=0,
                           recurrence=rule, until=until, tz_name="Africa/Douala")


def test_creation_et_lecture_d_une_serie(db, alice, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = make_series(db, alice, first)
    assert event["recurrence"] == "daily" and event["series_tz"] == "Africa/Douala"
    single = db.create_event(alice, "Seul", first.replace(hour=9), first.replace(hour=10), max_upcoming=0)
    items = db.list_events(alice, first.replace(hour=0), first.replace(hour=0) + timedelta(days=3))
    assert [(i["title"], i["start_at"].astimezone(tz).day) for i in items] == [("Seul", 29), ("Série", 29), ("Série", 30), ("Série", 31)]
    assert {i["id"] for i in items if i["title"] == "Série"} == {event["id"]}
    assert single["recurrence"] is None and single["series_tz"] is None


def test_les_series_sont_isolees_par_utilisateur(db, alice, friend, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    make_series(db, alice, first)
    assert db.list_events(friend, first, first + timedelta(days=5)) == []


def test_conflits_avec_une_occurrence(db, alice, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = make_series(db, alice, first)
    later = first + timedelta(days=9, minutes=30)  # 14h30 un jour où la série a lieu
    conflicts = db.find_conflicts(alice, later, later + timedelta(hours=1))
    assert [c["id"] for c in conflicts] == [event["id"]]
    assert db.find_conflicts(alice, later, later + timedelta(hours=1), exclude_id=event["id"]) == []


def test_modification_et_arret_d_une_serie(db, alice, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = make_series(db, alice, first, until=date(2026, 12, 1))
    db.skip_occurrence(alice, event["id"], date(2026, 10, 30))
    changed = db.update_event(alice, event["id"], until=date(2026, 11, 5))
    assert changed["recurrence_until"] == date(2026, 11, 5) and changed["skipped_days"] == [date(2026, 10, 30)]
    weekly = db.update_event(alice, event["id"], recurrence="weekly", tz_name="Europe/Paris")
    assert weekly["recurrence"] == "weekly" and weekly["skipped_days"] == [] and weekly["series_tz"] == "Europe/Paris"
    plain = db.update_event(alice, event["id"], recurrence=None)
    assert (plain["recurrence"], plain["recurrence_until"], plain["skipped_days"], plain["series_tz"]) == (None, None, [], None)
    assert db.update_event(alice, event["id"], title="Renommé")["title"] == "Renommé"


def test_supprimer_une_occurrence(db, alice, friend, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = make_series(db, alice, first)
    assert db.skip_occurrence(alice, event["id"], date(2026, 10, 31)) is True
    days = [i["occurrence"] for i in db.list_events(alice, first, first + timedelta(days=4))]
    assert days == ["2026-10-29", "2026-10-30", "2026-11-01"]
    assert db.skip_occurrence(alice, event["id"], date(2026, 10, 28)) is False  # avant le début
    assert db.skip_occurrence(friend, event["id"], date(2026, 10, 30)) is False  # pas son rendez-vous
    single = db.create_event(alice, "Seul", first, first + timedelta(hours=1), max_upcoming=0)
    assert db.skip_occurrence(alice, single["id"], date(2026, 10, 29)) is False  # pas une série
    assert db.skip_occurrence(alice, 999999, date(2026, 10, 29)) is False


def test_supprimer_la_serie_conserve_les_taches_liees_en_base(db, alice, tz):
    first = datetime(2026, 10, 29, 14, 0, tzinfo=tz)
    event = make_series(db, alice, first)
    assert db.delete_event(alice, event["id"]) is True
    assert db.list_events(alice, first, first + timedelta(days=30)) == []


def test_purge_des_series_terminees(db, alice, tz):
    old = datetime.now(tz) - timedelta(days=100)
    ended = make_series(db, alice, old, until=(old + timedelta(days=5)).date(), title="finie")
    open_ended = make_series(db, alice, old, title="sans fin")
    recent = make_series(db, alice, old, until=(datetime.now(tz) - timedelta(days=5)).date(), title="récente")
    assert db.purge_old_events(60) == 1
    left = {e["id"] for e in (db.get_event(alice, x["id"]) for x in (ended, open_ended, recent)) if e}
    assert left == {open_ended["id"], recent["id"]}


def test_limite_comptee_pour_les_series(db, alice, tz):
    first = datetime.now(tz) + timedelta(days=1)
    db.create_event(alice, "a", first, first + timedelta(hours=1), max_upcoming=1)
    with pytest.raises(db.EventLimit):
        db.create_event(alice, "b", first, first + timedelta(hours=1), max_upcoming=1, recurrence="daily", tz_name="UTC")


# --- Rappels ----------------------------------------------------------------------------------------------
def due_series(db, user, days_ago=10, rule="daily", skipped=(), zone=None):
    """Série quotidienne commencée il y a `days_ago` jours, dont l'occurrence d'aujourd'hui est dans 20 minutes."""
    base = datetime.now(timezone.utc) + timedelta(minutes=20)
    first = base - timedelta(days=days_ago)
    event = db.create_event(user, "Série", first, first + timedelta(hours=1), max_upcoming=0,
                            recurrence=rule, tz_name=zone or "UTC")
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - interval '3 days', skipped_days = %s WHERE id = %s", (list(skipped), event["id"]))
    return event, base


def test_rappel_d_une_occurrence_une_seule_fois(db, alice):
    event, base = due_series(db, alice)
    first = db.claim_due_event_reminders(30)
    assert [r["id"] for r in first] == [event["id"]] and first[0]["telegram_id"] == OWNER
    assert abs((first[0]["start_at"] - base).total_seconds()) < 1
    assert first[0]["recurrence"] == "daily" and first[0]["occurrence"] == first[0]["start_at"].astimezone(timezone.utc).date().isoformat()
    assert db.claim_due_event_reminders(30) == []


def test_pas_de_rappel_trop_tot_ni_pour_un_jour_ignore(db, alice):
    event, base = due_series(db, alice)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET skipped_days = %s WHERE id = %s", ([base.astimezone(timezone.utc).date()], event["id"]))
    assert db.claim_due_event_reminders(30) == []
    assert db.claim_due_event_reminders(10) == []  # 20 minutes avant : trop tôt pour un rappel à 10 minutes


def test_l_occurrence_suivante_est_rappelee_a_son_tour(db, alice):
    event, base = due_series(db, alice)
    with db.cursor() as cur:  # l'occurrence d'hier a déjà été rappelée
        cur.execute("UPDATE events SET reminded_for = %s WHERE id = %s", (base - timedelta(days=1), event["id"]))
    assert len(db.claim_due_event_reminders(30)) == 1
    with db.cursor() as cur:
        cur.execute("SELECT reminded_for FROM events WHERE id = %s", (event["id"],))
        assert abs((cur.fetchone()[0] - base).total_seconds()) < 1


def test_serie_creee_apres_l_heure_de_rappel_ne_declenche_rien(db, alice):
    base = datetime.now(timezone.utc) + timedelta(minutes=10)
    db.create_event(alice, "Neuve", base, base + timedelta(hours=1), max_upcoming=0, recurrence="daily", tz_name="UTC")
    assert db.claim_due_event_reminders(30) == []


def test_serie_terminee_ou_utilisateur_inactif_sans_rappel(db, alice):
    event, base = due_series(db, alice)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET recurrence_until = current_date - 3 WHERE id = %s", (event["id"],))
    assert db.claim_due_event_reminders(30) == []
    with db.cursor() as cur:
        cur.execute("UPDATE events SET recurrence_until = NULL WHERE id = %s", (event["id"],))
        cur.execute("UPDATE users SET disabled_at = now() WHERE id = %s", (alice,))
    assert db.claim_due_event_reminders(30) == []


# --- Routes ---------------------------------------------------------------------------------------------------
@pytest.fixture
def client(app_module, alice, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


def call(client, method, path, telegram_id=OWNER, **payload):
    if method == "GET":
        sep = "&" if "?" in path else "?"
        return client.get(f"{path}{sep}telegram_id={telegram_id}", headers=HEADERS)
    return getattr(client, method.lower())(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


def test_creation_d_une_serie_par_texte(client):
    response = call(client, "POST", "/events", text="Point | demain 9h | 30 min | Salle 2 | chaque jour")
    assert response.status_code == 201
    event = response.get_json()["event"]
    assert event["repeat"] == "chaque jour" and event["occurrence"] is None
    listed = call(client, "GET", "/events?range=semaine").get_json()["events"]
    assert len(listed) >= 6 and {e["id"] for e in listed} == {event["id"]}
    assert all(e["occurrence"] for e in listed) and listed[0]["start"][11:16] == "09:00"


def test_creation_d_une_serie_par_champs(client):
    response = call(client, "POST", "/events", title="Sport", start="demain 18h", repeat="chaque semaine", until="31/12/2099")
    assert response.status_code == 201 and response.get_json()["event"]["repeat"] == "chaque semaine jusqu'au 31/12/2099"
    assert call(client, "POST", "/events", title="X", start="demain 18h", until="31/12/2099").status_code == 400
    assert call(client, "POST", "/events", title="X", start="demain 18h", repeat="parfois").status_code == 400


def test_serie_en_conflit_avec_un_rendez_vous(client):
    call(client, "POST", "/events", text="Point | demain 9h | 1h | | chaque jour")
    again = call(client, "POST", "/events", text="Autre | demain 9h30 | 1h").get_json()
    assert [c["title"] for c in again["conflicts"]] == ["Point"]


def test_modification_de_la_repetition(client):
    event = call(client, "POST", "/events", text="Point | demain 9h | | | chaque jour").get_json()["event"]
    path = f"/events/{event['id']}"
    changed = call(client, "PATCH", path, until="31/12/2099").get_json()["event"]
    assert changed["repeat"] == "chaque jour jusqu'au 31/12/2099"
    weekly = call(client, "PATCH", path, repeat="chaque semaine").get_json()["event"]
    assert weekly["repeat"].startswith("chaque semaine")
    plain = call(client, "PATCH", path, repeat="aucune").get_json()["event"]
    assert "repeat" not in plain
    assert call(client, "PATCH", path, repeat="chaque mois jusqu'au 01/01/2000").status_code == 400


def test_deplacer_une_serie_change_sa_premiere_occurrence(client):
    event = call(client, "POST", "/events", text="Point | demain 9h | | | chaque jour").get_json()["event"]
    moved = call(client, "PATCH", f"/events/{event['id']}", start="après-demain 16h").get_json()["event"]
    assert moved["start"][11:16] == "16:00" and moved["repeat"] == "chaque jour"
    listed = call(client, "GET", "/events?range=semaine").get_json()["events"]
    assert {e["start"][11:16] for e in listed} == {"16:00"}


def test_supprimer_une_occurrence_puis_la_serie(client):
    event = call(client, "POST", "/events", text="Point | demain 9h | | | chaque jour").get_json()["event"]
    path = f"/events/{event['id']}"
    day = call(client, "GET", "/events?range=semaine").get_json()["events"][1]["occurrence"]
    assert call(client, "DELETE", path, occurrence=day).get_json() == {"status": "occurrence_deleted"}
    assert day not in [e["occurrence"] for e in call(client, "GET", "/events?range=semaine").get_json()["events"]]
    assert call(client, "DELETE", path, occurrence=day).status_code == 404  # déjà supprimée
    assert call(client, "DELETE", path, occurrence="n'importe quoi").status_code == 400
    assert call(client, "DELETE", path).get_json() == {"status": "deleted"}
    assert call(client, "GET", "/events?range=semaine").get_json()["events"] == []


def test_les_series_d_un_autre_sont_intouchables(client, friend):
    event = call(client, "POST", "/events", text="Point | demain 9h | | | chaque jour").get_json()["event"]
    path = f"/events/{event['id']}"
    assert call(client, "DELETE", path, telegram_id=OTHER, occurrence="2099-01-01").status_code == 404
    assert call(client, "PATCH", path, telegram_id=OTHER, repeat="aucune").status_code == 404
    assert call(client, "GET", "/events?range=semaine", telegram_id=OTHER).get_json()["events"] == []


def test_route_des_rappels_pour_une_serie(client, db, alice):
    due_series(db, alice)
    reminders = client.post("/events/reminders/claim", json={}, headers=HEADERS).get_json()["reminders"]
    assert len(reminders) == 1 and reminders[0]["repeat"] == "chaque jour" and reminders[0]["occurrence"]


def test_pas_de_rappel_pour_une_occurrence_deja_commencee_depuis_longtemps(db, alice):
    started = datetime.now(timezone.utc) - timedelta(minutes=30)  # en cours depuis 30 minutes (durée 1 h)
    first = started - timedelta(days=5)
    event = db.create_event(alice, "En cours", first, first + timedelta(hours=1), max_upcoming=0, recurrence="daily", tz_name="UTC")
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - interval '9 days' WHERE id = %s", (event["id"],))
    assert db.claim_due_event_reminders(30) == []
    just_started = datetime.now(timezone.utc) - timedelta(minutes=5)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET start_at = %s, end_at = %s WHERE id = %s",
                    (just_started - timedelta(days=5), just_started - timedelta(days=5) + timedelta(hours=1), event["id"]))
    assert len(db.claim_due_event_reminders(30)) == 1  # commencé il y a 5 minutes : encore dans la tolérance
