"""Tâches liées à un rendez-vous (phase B6)."""
from datetime import datetime, timedelta, timezone

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


def make_event(db, user, minutes=60 * 24, title="Réunion"):
    start = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return db.create_event(user, title, start, start + timedelta(hours=1), max_upcoming=0)


@pytest.fixture
def tasks(agent):
    import tasks as module

    return module


# --- Saisie rapide ---------------------------------------------------------------------------------
@pytest.mark.parametrize("text, event_id", [
    ("Slides | rdv 12", 12), ("Slides | rdv n°12", 12), ("Slides | RDV #12", 12), ("Slides | rendez-vous 12", 12),
    ("Slides | haute | demain | rdv 12", 12), ("Slides | rdv 12 | haute", 12), ("Slides", None), ("Slides | haute", None),
])
def test_saisie_rapide_avec_lien(tasks, text, event_id):
    title, priority, due, linked = tasks.parse_quick_linked(text)
    assert title == "Slides" and linked == event_id
    assert tasks.parse_quick(text) == (title, priority, due)  # l'ancienne fonction reste compatible


def test_saisie_rapide_incomprise(tasks):
    with pytest.raises(tasks.TaskInputError, match="rdv n"):
        tasks.parse_quick_linked("Slides | n'importe quoi")


# --- Base ----------------------------------------------------------------------------------------------
def test_creer_lier_detacher(db, alice):
    event = make_event(db, alice)
    task = db.create_task(alice, "Préparer", event_id=event["id"])
    assert task["event_id"] == event["id"]
    assert db.update_task(alice, task["id"], event_id=None)["event_id"] is None
    assert db.update_task(alice, task["id"], event_id=event["id"])["event_id"] == event["id"]
    assert db.update_task(alice, task["id"], title="Autre")["event_id"] == event["id"]  # inchangé sans demande


def test_lien_refuse_vers_le_rendez_vous_d_un_autre_ou_inexistant(db, alice, friend):
    theirs = make_event(db, friend)
    with pytest.raises(db.LinkedEventNotFound):
        db.create_task(alice, "Intrus", event_id=theirs["id"])
    with pytest.raises(db.LinkedEventNotFound):
        db.create_task(alice, "Fantôme", event_id=999999)
    mine = db.create_task(alice, "Ok")
    with pytest.raises(db.LinkedEventNotFound):
        db.update_task(alice, mine["id"], event_id=theirs["id"])
    assert db.get_task(alice, mine["id"])["event_id"] is None


def test_supprimer_le_rendez_vous_conserve_la_tache(db, alice):
    event = make_event(db, alice)
    task = db.create_task(alice, "Garder", event_id=event["id"])
    db.delete_event(alice, event["id"])
    assert db.get_task(alice, task["id"])["event_id"] is None


def test_taches_ouvertes_par_rendez_vous(db, alice):
    event = make_event(db, alice)
    open_task = db.create_task(alice, "À faire", event_id=event["id"])
    done = db.create_task(alice, "Fait", event_id=event["id"])
    db.update_task(alice, done["id"], status="terminee")
    db.create_task(alice, "Sans lien")
    assert db.open_tasks_for_events(alice, [event["id"]]) == {event["id"]: [{"id": open_task["id"], "title": "À faire"}]}
    assert db.open_tasks_for_events(alice, []) == {} and db.event_titles(alice, [event["id"], None]) == {event["id"]: "Réunion"}


# --- Routes ---------------------------------------------------------------------------------------------
def test_routes_creation_modification_et_liste(client, db, alice):
    event = make_event(db, alice, title="Comité")
    created = call(client, "POST", "/tasks", text=f"Slides | haute | rdv {event['id']}")
    assert created.status_code == 201 and created.get_json()["task"]["event_id"] == event["id"]
    task_id = created.get_json()["task"]["id"]
    listed = call(client, "GET", "/tasks").get_json()["tasks"]
    assert listed[0]["event_title"] == "Comité"
    assert call(client, "PATCH", f"/tasks/{task_id}", event_id=None).get_json()["task"]["event_id"] is None
    assert "event_title" not in call(client, "GET", "/tasks").get_json()["tasks"][0]
    assert call(client, "PATCH", f"/tasks/{task_id}", event_id=event["id"]).get_json()["task"]["event_id"] == event["id"]
    by_json = call(client, "POST", "/tasks", title="Autre", event_id=event["id"])
    assert by_json.status_code == 201


def test_routes_refusent_un_rendez_vous_inconnu_ou_etranger(client, db, friend):
    theirs = make_event(db, friend)
    assert call(client, "POST", "/tasks", text=f"X | rdv {theirs['id']}").status_code == 404
    assert call(client, "POST", "/tasks", text="X | rdv 999999").status_code == 404
    task = call(client, "POST", "/tasks", text="Y").get_json()["task"]
    assert call(client, "PATCH", f"/tasks/{task['id']}", event_id=theirs["id"]).status_code == 404


def test_l_agenda_montre_les_taches_liees(client, db, alice):
    event = make_event(db, alice)
    call(client, "POST", "/tasks", text=f"Slides | rdv {event['id']}")
    call(client, "POST", "/tasks", text=f"Imprimer | rdv {event['id']}")
    listed = call(client, "GET", "/events?range=semaine").get_json()["events"]
    assert [t["title"] for t in listed[0]["tasks"]] == ["Slides", "Imprimer"]
    assert [t["title"] for t in call(client, "GET", f"/events/{event['id']}").get_json()["event"]["tasks"]] == ["Slides", "Imprimer"]


def test_les_taches_d_un_autre_n_apparaissent_pas(client, db, alice, friend):
    event = make_event(db, alice)
    db.create_task(friend, "Privée")
    assert call(client, "GET", "/events?range=semaine").get_json()["events"][0]["tasks"] == []


# --- Rappel et briefing -----------------------------------------------------------------------------------
def test_le_rappel_liste_les_taches_a_faire(client, db, alice):
    event = make_event(db, alice, minutes=10)
    with db.cursor() as cur:
        cur.execute("UPDATE events SET updated_at = now() - interval '2 hours' WHERE id = %s", (event["id"],))
    db.create_task(alice, "Slides", event_id=event["id"])
    finished = db.create_task(alice, "Déjà fait", event_id=event["id"])
    db.update_task(alice, finished["id"], status="terminee")
    reminders = client.post("/events/reminders/claim", json={}, headers=HEADERS).get_json()["reminders"]
    assert reminders[0]["tasks"] == ["Slides"]


def test_le_briefing_mentionne_les_taches_liees(db, alice, tools):
    import briefing

    today = datetime.now(tools.tz())
    start = datetime(today.year, today.month, today.day, 12, tzinfo=tools.tz())
    event = db.create_event(alice, "Midi", start, start + timedelta(hours=1), max_upcoming=0)
    db.create_task(alice, "Slides", event_id=event["id"])
    (item,) = briefing.own_events(alice, today)
    assert item["taches"] == ["Slides"]
    assert briefing.event_line(item).endswith("— à préparer : Slides")


# --- Outils IA ----------------------------------------------------------------------------------------------
def test_outils_ia_lier_une_tache(agent, db, make_user):
    user = make_user()
    event = make_event(db, user["id"], title="Revue")
    kind, value = agent.dispatch_tool_call(user["id"], "tester", "creer_tache", {"titre": "Préparer", "rdv_id": event["id"]})
    assert kind == "pending" and "Liée au rendez-vous « Revue »" in value["summary"]
    agent.resolve_action(user["id"], value["id"], True, "tester")
    kind, listed = agent.dispatch_tool_call(user["id"], "tester", "lister_taches", {})
    assert listed[0]["rdv"] == "Revue" and listed[0]["rdv_id"] == event["id"]
    task_id = listed[0]["id"]
    kind, value = agent.dispatch_tool_call(user["id"], "tester", "modifier_tache", {"tache_id": task_id, "rdv_id": 0})
    assert "Détachée" in value["summary"]
    agent.resolve_action(user["id"], value["id"], True, "tester")
    assert db.get_task(user["id"], task_id)["event_id"] is None


def test_outils_ia_refusent_un_rendez_vous_etranger(agent, db, make_user):
    mine, other = make_user(), make_user()
    theirs = make_event(db, other["id"])
    kind, value = agent.dispatch_tool_call(mine["id"], "tester", "creer_tache", {"titre": "X", "rdv_id": theirs["id"]})
    assert kind == "error" and "introuvable" in value
    kind, value = agent.dispatch_tool_call(mine["id"], "tester", "creer_tache", {"titre": "X", "rdv_id": "abc"})
    assert kind == "error" and "invalide" in value
