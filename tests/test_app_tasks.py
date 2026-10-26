"""API des tâches et flux de l'agent avec les outils de tâches (phase B1)."""
import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001


@pytest.fixture
def owner(make_owner):
    return make_owner(OWNER)


@pytest.fixture
def client(app_module, owner, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


@pytest.fixture
def friend(accounts, db):
    """Compte testeur lié à un identifiant Telegram."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return {"id": cur.fetchone()[0]}


def get(client, path="/tasks", telegram_id=OWNER, **params):
    return client.get(path, query_string={"telegram_id": telegram_id, **params}, headers=HEADERS)


def send(client, method, path, telegram_id=OWNER, **payload):
    return client.open(path, method=method, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


def add(client, text, **kw):
    return send(client, "POST", "/tasks", text=text, **kw)


# --- Accès ---------------------------------------------------------------------
def test_les_routes_exigent_une_identite(client):
    assert client.get("/tasks").status_code == 401
    assert client.post("/tasks", json={"text": "x"}).status_code == 401
    assert client.patch("/tasks/1", json={"status": "terminee"}).status_code == 401
    assert client.delete("/tasks/1", json={}).status_code == 401


def test_un_inconnu_est_refuse(client):
    assert get(client, telegram_id=999).status_code == 403
    assert add(client, "x", telegram_id=999).status_code == 403


def test_les_modifications_exigent_du_json(client):
    assert client.patch("/tasks/1", data="x", headers=HEADERS).status_code == 415
    assert client.post("/tasks", data="x", headers=HEADERS).status_code == 415


# --- Création et liste ---------------------------------------------------------
def test_creation_par_saisie_rapide(client):
    response = add(client, "Appeler le fournisseur | 2026-12-24 | haute")
    assert response.status_code == 201
    task = response.get_json()["task"]
    assert task["title"] == "Appeler le fournisseur" and task["priority"] == "haute"
    assert task["status"] == "a_faire" and task["due_at"].startswith("2026-12-24T23:59")
    assert get(client).get_json()["tasks"] == [task]


def test_creation_structuree_pour_l_interface_web(client):
    response = send(client, "POST", "/tasks", title="Écrire le rapport", priority="basse", due="2026-12-01")
    assert response.status_code == 201
    assert response.get_json()["task"]["priority"] == "basse"


@pytest.mark.parametrize(
    "payload",
    [{"text": ""}, {"text": "x | nimporte"}, {"title": ""}, {"title": "x", "priority": "folle"}, {"title": "x", "due": "bientôt"}, {}],
)
def test_creation_invalide(client, payload):
    response = send(client, "POST", "/tasks", **payload)
    assert response.status_code == 400 and response.get_json()["error"]


def test_plafond_de_taches(client, app_module, db, monkeypatch):
    monkeypatch.setattr(db, "MAX_OPEN_TASKS", 2)
    assert add(client, "a").status_code == 201 and add(client, "b").status_code == 201
    response = add(client, "c")
    assert response.status_code == 409 and "2" in response.get_json()["error"]


def test_chacun_ne_voit_que_ses_taches(client, friend):
    add(client, "à moi")
    add(client, "à l'ami", telegram_id=OTHER)
    assert [t["title"] for t in get(client).get_json()["tasks"]] == ["à moi"]
    assert [t["title"] for t in get(client, telegram_id=OTHER).get_json()["tasks"]] == ["à l'ami"]


def test_filtre_par_statut(client):
    first = add(client, "finie").get_json()["task"]["id"]
    add(client, "ouverte")
    send(client, "PATCH", f"/tasks/{first}", status="terminee")
    assert [t["title"] for t in get(client).get_json()["tasks"]] == ["ouverte"]
    assert [t["title"] for t in get(client, status="terminee").get_json()["tasks"]] == ["finie"]
    assert len(get(client, status="all").get_json()["tasks"]) == 2
    assert get(client, status="n'importe quoi").status_code == 400


# --- Modification --------------------------------------------------------------
def test_terminer_une_tache(client):
    task_id = add(client, "x").get_json()["task"]["id"]
    response = send(client, "PATCH", f"/tasks/{task_id}", status="terminee")
    assert response.status_code == 200
    task = response.get_json()["task"]
    assert task["status"] == "terminee" and task["done_at"]


def test_modifier_titre_priorite_echeance(client):
    task_id = add(client, "x | 2026-12-01").get_json()["task"]["id"]
    task = send(client, "PATCH", f"/tasks/{task_id}", title="y", priority="haute", due="2026-12-05").get_json()["task"]
    assert (task["title"], task["priority"]) == ("y", "haute") and task["due_at"].startswith("2026-12-05")
    assert send(client, "PATCH", f"/tasks/{task_id}", due=None).get_json()["task"]["due_at"] is None


def test_modification_vide_ou_invalide(client):
    task_id = add(client, "x").get_json()["task"]["id"]
    assert send(client, "PATCH", f"/tasks/{task_id}").status_code == 400
    assert send(client, "PATCH", f"/tasks/{task_id}", status="perdue").status_code == 400
    assert send(client, "PATCH", f"/tasks/{task_id}", title="  ").status_code == 400


def test_tache_inconnue_ou_d_un_autre(client, friend):
    other_id = add(client, "à l'ami", telegram_id=OTHER).get_json()["task"]["id"]
    assert send(client, "PATCH", f"/tasks/{other_id}", status="terminee").status_code == 404
    assert send(client, "PATCH", "/tasks/99999", status="terminee").status_code == 404
    assert send(client, "DELETE", f"/tasks/{other_id}").status_code == 404
    assert get(client, telegram_id=OTHER).get_json()["tasks"][0]["status"] == "a_faire"


def test_rouvrir_au_plafond_refuse(client, db, monkeypatch):
    monkeypatch.setattr(db, "MAX_OPEN_TASKS", 1)
    old = add(client, "ancienne").get_json()["task"]["id"]
    send(client, "PATCH", f"/tasks/{old}", status="terminee")
    add(client, "nouvelle")
    assert send(client, "PATCH", f"/tasks/{old}", status="a_faire").status_code == 409


def test_suppression(client):
    task_id = add(client, "x").get_json()["task"]["id"]
    assert send(client, "DELETE", f"/tasks/{task_id}").status_code == 200
    assert get(client, status="all").get_json()["tasks"] == []
    assert send(client, "DELETE", f"/tasks/{task_id}").status_code == 404


def test_le_journal_garde_les_actions_sans_le_texte(client, db, owner):
    task_id = add(client, "contenu très privé").get_json()["task"]["id"]
    send(client, "PATCH", f"/tasks/{task_id}", status="terminee")
    send(client, "DELETE", f"/tasks/{task_id}")
    with db.cursor() as cur:
        cur.execute("SELECT event, details::text FROM audit_log WHERE event LIKE 'task_%' ORDER BY id")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["task_created", "task_updated", "task_deleted"]
    assert not any("privé" in r[1] for r in rows)


# --- Agent : proposer, confirmer, exécuter --------------------------------------
@pytest.fixture
def gemini_script(agent, monkeypatch):
    queue = []
    monkeypatch.setattr(agent.client.models, "generate_content", lambda model, contents, config: queue.pop(0))
    return queue


def test_creer_une_tache_par_l_agent_passe_par_la_confirmation(agent, db, owner, gemini_script, helpers):
    gemini_script.append(helpers.response(calls=[helpers.call("creer_tache", titre="Relancer Paul", echeance="2026-12-01")]))
    reply, _, pending = agent.chat(owner["id"], "Rappelle-moi de relancer Paul le 1er décembre")
    assert reply == "Action en attente de confirmation."
    assert pending[0]["summary"] == "Créer la tâche « Relancer Paul »\nÉchéance : 01/12/2026"
    assert db.list_tasks(owner["id"]) == []  # rien n'est créé avant la confirmation
    assert agent.resolve_action(owner["id"], pending[0]["id"], True, "owner")== "Tâche créée."
    assert [t["title"] for t in db.list_tasks(owner["id"])] == ["Relancer Paul"]


def test_annuler_la_proposition_ne_cree_rien(agent, db, owner, gemini_script, helpers):
    gemini_script.append(helpers.response(calls=[helpers.call("creer_tache", titre="X")]))
    _, _, pending = agent.chat(owner["id"], "ajoute X")
    assert agent.resolve_action(owner["id"], pending[0]["id"], False, "owner") == "Action annulée."
    assert db.list_tasks(owner["id"]) == []


def test_lister_les_taches_est_une_lecture_directe(agent, db, owner, gemini_script, helpers):
    db.create_task(owner["id"], "Tâche existante")
    gemini_script.extend([helpers.response(calls=[helpers.call("lister_taches")]), helpers.response("Tu as 1 tâche.")])
    reply, _, pending = agent.chat(owner["id"], "Quelles sont mes tâches ?")
    assert reply == "Tu as 1 tâche." and pending == []


def test_la_proposition_d_un_autre_utilisateur_ne_peut_pas_etre_confirmee(agent, db, owner, friend, gemini_script, helpers):
    mine = db.create_task(owner["id"], "À moi")
    gemini_script.append(helpers.response(calls=[helpers.call("modifier_tache", tache_id=mine["id"], statut="terminee")]))
    _, _, pending = agent.chat(owner["id"], "termine ma tâche")
    assert agent.resolve_action(friend["id"], pending[0]["id"], True, "tester") == "Cette action n'est plus en attente."
    assert db.get_task(owner["id"], mine["id"])["status"] == "a_faire"


def test_proposer_la_modification_d_une_tache_inconnue_est_refuse(agent, owner, gemini_script, helpers):
    gemini_script.extend([
        helpers.response(calls=[helpers.call("modifier_tache", tache_id=424242, statut="terminee")]),
        helpers.response("Je ne trouve pas cette tâche."),
    ])
    reply, _, pending = agent.chat(owner["id"], "termine la 424242")
    assert pending == [] and reply == "Je ne trouve pas cette tâche."


def test_le_prompt_parle_des_taches(agent):
    assert "tâche" in agent.system_prompt(role="owner")
