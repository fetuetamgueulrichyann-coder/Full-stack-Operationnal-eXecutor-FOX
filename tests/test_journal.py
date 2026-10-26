"""Journal des actions et rappels à l'heure (phase B2)."""
from datetime import datetime, timedelta

import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001


@pytest.fixture
def alice(make_owner):
    return make_owner(OWNER)["id"]


@pytest.fixture
def friend(db, accounts):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return cur.fetchone()[0]


@pytest.fixture
def journal(agent):
    import journal as module

    return module


@pytest.fixture
def tasks(agent):
    import tasks as module

    return module


def action(db, user_id, tool, summary, status="pending", age_minutes=0, undone=False):
    action_id = db.create_pending(user_id, tool, {}, summary, 10)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE pending_actions SET status = %s, created_at = now() - make_interval(mins => %s), "
            "undone_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = %s",
            (status, age_minutes, undone, action_id),
        )
    return action_id


# --- Journal -----------------------------------------------------------------------
@pytest.mark.parametrize("status, label", [
    ("pending", "En attente"), ("confirmed", "Confirmée"), ("executed", "Exécutée"),
    ("cancelled", "Annulée"), ("expired", "Expirée"), ("failed", "Échec"),
])
def test_chaque_statut_a_un_libelle_lisible(db, journal, alice, status, label):
    action(db, alice, "creer_tache", "Créer la tâche « X »\nÉchéance : 30/10/2026", status)
    assert [e["text"] for e in journal.entries(alice)] == [f"{label} : Créer la tâche « X »"]


def test_une_action_annulee_apres_execution(db, journal, alice):
    action(db, alice, "creer_evenement", "Créer l'événement « Réunion »", "executed", undone=True)
    assert journal.entries(alice)[0]["text"] == "Exécutée puis annulée : Créer l'événement « Réunion »"


def test_ordre_du_plus_recent_au_plus_ancien_et_limite(db, journal, alice):
    for i in range(5):
        action(db, alice, "t", f"action {i}", "executed", age_minutes=10 * (5 - i))
    texts = [e["text"] for e in journal.entries(alice, limit=3)]
    assert texts == ["Exécutée : action 4", "Exécutée : action 3", "Exécutée : action 2"]


def test_les_actions_directes_apparaissent_sans_contenu(db, journal, alice):
    for event in ("task_created", "memory_added", "memory_cleared", "history_deleted"):
        db.audit(alice, event, None, {})
    db.audit(alice, "executed", "lister_taches", {"mode": "direct"})  # bruit : lecture, absent du journal
    texts = {e["text"] for e in journal.entries(alice)}
    assert texts == {"Tâche ajoutée", "Information mémorisée", "Mémoire effacée", "Historique supprimé"}


def test_actions_et_evenements_sont_melanges_par_date(db, journal, alice):
    action(db, alice, "t", "ancienne", "executed", age_minutes=60)
    db.audit(alice, "task_created", None, {})
    action(db, alice, "t", "très ancienne", "executed", age_minutes=600)
    assert [e["text"] for e in journal.entries(alice)] == ["Tâche ajoutée", "Exécutée : ancienne", "Exécutée : très ancienne"]


def test_le_journal_est_prive(db, journal, alice, friend):
    action(db, alice, "t", "à Alice", "executed")
    db.audit(alice, "task_created", None, {})
    assert journal.entries(friend) == []


def test_les_dates_sont_dans_le_fuseau_de_fox(db, journal, alice):
    action(db, alice, "t", "x", "executed")
    at = journal.entries(alice)[0]["at"]
    assert at.endswith("+01:00")  # Africa/Douala


def test_resume_sans_texte_affiche_l_outil(db, journal, alice):
    assert journal.describe({"kind": "action", "status": "executed", "summary": "", "tool": "creer_tache"}) == "Exécutée : creer_tache"


@pytest.fixture
def client(app_module, alice, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


def test_route_du_journal(client, db, alice):
    assert client.get("/journal").status_code == 401
    assert client.get("/journal", query_string={"telegram_id": 999}, headers=HEADERS).status_code == 403
    action(db, alice, "t", "x", "executed")
    body = client.get("/journal", query_string={"telegram_id": OWNER}, headers=HEADERS).get_json()
    assert [e["text"] for e in body["entries"]] == ["Exécutée : x"]
    assert client.get("/journal", query_string={"telegram_id": OWNER, "limit": "abc"}, headers=HEADERS).status_code == 400


def test_la_limite_du_journal_est_bornee(client, db, alice):
    for i in range(60):
        action(db, alice, "t", f"a{i}", "executed")
    count = lambda limit: len(client.get("/journal", query_string={"telegram_id": OWNER, "limit": limit}, headers=HEADERS).get_json()["entries"])
    assert count(5) == 5 and count(500) == 50 and count(0) == 1


def test_les_actions_directes_des_taches_sont_journalisees(client, alice):
    client.post("/tasks", json={"telegram_id": OWNER, "text": "x"}, headers=HEADERS)
    body = client.get("/journal", query_string={"telegram_id": OWNER}, headers=HEADERS).get_json()
    assert [e["text"] for e in body["entries"]] == ["Tâche ajoutée"]


# --- Rappels à l'heure -----------------------------------------------------------------
def at(minutes):
    return datetime.now().astimezone() + timedelta(minutes=minutes)


def test_une_echeance_jour_seul_n_a_pas_d_heure(db, tasks):
    assert db.has_time(tasks.parse_due("2026-10-25")) is False
    assert db.has_time(tasks.parse_due("2026-10-25T11:25")) is True
    assert db.has_time(None) is False


def test_le_rappel_est_du_a_l_heure_et_une_seule_fois(db, alice):
    db.create_task(alice, "Appeler Paul", due_at=at(-1))
    first = db.claim_due_reminders()
    assert [(r["title"], r["telegram_id"]) for r in first] == [("Appeler Paul", OWNER)]
    assert db.claim_due_reminders() == []  # jamais de doublon


def test_pas_de_rappel_avant_l_heure(db, alice):
    db.create_task(alice, "Plus tard", due_at=at(30))
    assert db.claim_due_reminders() == []


def test_pas_de_rappel_pour_une_echeance_sans_heure(db, tasks, alice):
    yesterday = (datetime.now(tasks.TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    db.create_task(alice, "Jour seul", due_at=tasks.parse_due(yesterday))
    assert db.claim_due_reminders() == []


def test_pas_de_rappel_pour_une_tache_close(db, alice):
    for status in ("terminee", "annulee"):
        task = db.create_task(alice, status, due_at=at(-1))
        db.update_task(alice, task["id"], status=status)
    assert db.claim_due_reminders() == []


def test_un_rappel_tres_ancien_n_est_plus_envoye(db, alice):
    db.create_task(alice, "Oublié", due_at=at(-60 * 30))
    assert db.claim_due_reminders() == []
    db.create_task(alice, "Récent", due_at=at(-60 * 5))
    assert [r["title"] for r in db.claim_due_reminders()] == ["Récent"]


def test_changer_l_echeance_reprogramme_le_rappel(db, alice):
    task = db.create_task(alice, "x", due_at=at(-1))
    assert len(db.claim_due_reminders()) == 1
    db.update_task(alice, task["id"], due_at=at(-2))
    assert len(db.claim_due_reminders()) == 1
    db.update_task(alice, task["id"], title="renommée")  # une autre modification ne relance pas
    assert db.claim_due_reminders() == []


def test_rouvrir_une_tache_reprogramme_le_rappel(db, alice):
    task = db.create_task(alice, "x", due_at=at(-1))
    db.claim_due_reminders()
    db.update_task(alice, task["id"], status="terminee")
    db.update_task(alice, task["id"], status="a_faire")
    assert len(db.claim_due_reminders()) == 1


def test_passer_en_cours_ne_relance_pas_le_rappel(db, alice):
    task = db.create_task(alice, "x", due_at=at(-1))
    db.claim_due_reminders()
    db.update_task(alice, task["id"], status="en_cours")
    assert db.claim_due_reminders() == []


def test_pas_de_rappel_pour_un_compte_desactive_ou_sans_telegram(db, alice, friend, make_user):
    db.create_task(alice, "a", due_at=at(-1))
    db.create_task(friend, "b", due_at=at(-1))
    db.create_task(make_user("sans")["id"], "c", due_at=at(-1))  # compte web sans Telegram
    with db.cursor() as cur:
        cur.execute("UPDATE users SET disabled_at = now() WHERE id = %s", (friend,))
    assert [r["title"] for r in db.claim_due_reminders()] == ["a"]


def test_chaque_rappel_va_a_son_proprietaire(db, alice, friend):
    db.create_task(alice, "à Alice", due_at=at(-2))
    db.create_task(friend, "à l'ami", due_at=at(-1))
    assert {(r["title"], r["telegram_id"]) for r in db.claim_due_reminders()} == {("à Alice", OWNER), ("à l'ami", OTHER)}


def test_limite_de_rappels_par_releve(db, alice):
    for i in range(5):
        db.create_task(alice, f"t{i}", due_at=at(-1 - i))
    assert len(db.claim_due_reminders(limit=3)) == 3
    assert len(db.claim_due_reminders(limit=3)) == 2


def test_route_des_rappels_reservee_au_bot(client, db, alice):
    db.create_task(alice, "Appeler Paul", due_at=at(-1))
    assert client.post("/reminders/claim", json={}).status_code == 401
    assert client.post("/reminders/claim", json={}, headers={"X-Fox-Service-Token": "faux"}).status_code == 401
    assert client.post("/reminders/claim", data="x", headers=HEADERS).status_code == 415
    body = client.post("/reminders/claim", json={}, headers=HEADERS).get_json()
    assert [(r["telegram_id"], r["title"]) for r in body["reminders"]] == [(OWNER, "Appeler Paul")]
    assert body["reminders"][0]["due_at"].endswith("+01:00")
    assert client.post("/reminders/claim", json={}, headers=HEADERS).get_json() == {"reminders": []}


def test_creer_une_tache_avec_heure_par_l_api_programme_un_rappel(client, db, alice):
    response = client.post("/tasks", json={"telegram_id": OWNER, "text": "Appeler Paul | 2020-01-01T11:25"}, headers=HEADERS)
    assert response.status_code == 201
    # échéance passée depuis longtemps : enregistrée, mais pas rappelée (au-delà de 24 h)
    assert db.claim_due_reminders() == []


def test_deux_releves_simultanes_ne_rendent_jamais_le_meme_rappel(db, alice):
    import threading

    for i in range(40):
        db.create_task(alice, f"t{i}", due_at=at(-1 - i))
    start, results, errors = threading.Barrier(4), [], []

    def worker():
        try:
            start.wait()
            while True:
                batch = db.claim_due_reminders(limit=5)
                if not batch:
                    return
                results.extend(r["id"] for r in batch)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(results) == 40 and len(set(results)) == 40  # chaque rappel exactement une fois
