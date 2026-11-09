"""Archivage des devis, garde-fou « une tâche n'est pas un devis » et actions en attente (phase D5)."""
import pytest

from test_app_commercial import QUOTE, alice, call, client, friend, OTHER, OWNER  # noqa: F401


def create(client, telegram_id=OWNER, text=QUOTE):
    r = call(client, "POST", "/quotes", telegram_id=telegram_id, text=text)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["quote"]


def numbers(client, query="", telegram_id=OWNER):
    r = call(client, "GET", "/quotes" + query, telegram_id=telegram_id)
    assert r.status_code == 200
    return [q["number"] for q in r.get_json()["quotes"]]


def test_archiver_masque_le_devis_sans_changer_son_statut(client):
    quote = create(client)
    call(client, "PATCH", f"/quotes/{quote['id']}", status="accepte")
    r = call(client, "PATCH", f"/quotes/{quote['id']}", archived=True)
    assert r.status_code == 200
    archived = r.get_json()["quote"]
    assert archived["archived"] is True and archived["status"] == "accepte"
    assert numbers(client) == []
    assert numbers(client, "?archived=1") == [quote["number"]]


def test_desarchiver_remet_le_devis_dans_la_liste(client):
    quote = create(client)
    call(client, "PATCH", f"/quotes/{quote['id']}", archived=True)
    r = call(client, "PATCH", f"/quotes/{quote['id']}", archived=False)
    assert r.get_json()["quote"]["archived"] is False
    assert numbers(client) == [quote["number"]] and numbers(client, "?archived=1") == []


def test_un_devis_accepte_archive_reste_non_supprimable(client):
    quote = create(client)
    call(client, "PATCH", f"/quotes/{quote['id']}", status="accepte")
    call(client, "PATCH", f"/quotes/{quote['id']}", archived=True)
    assert call(client, "DELETE", f"/quotes/{quote['id']}").status_code == 409


@pytest.mark.parametrize("value", ["oui", 1, None, "true"])
def test_archived_doit_etre_un_booleen(client, value):
    quote = create(client)
    assert call(client, "PATCH", f"/quotes/{quote['id']}", archived=value).status_code == 400


def test_on_n_archive_que_ses_propres_devis(client, friend):
    mine = create(client)
    # le numéro est propre à chaque utilisateur : l'ami n'a pas de devis, la référence n'existe pas chez lui
    assert call(client, "PATCH", f"/quotes/{mine['id']}", telegram_id=OTHER, archived=True).status_code == 404
    assert numbers(client) == [mine["number"]]


def test_le_devis_archive_garde_ses_lignes_et_son_pdf(client):
    quote = create(client)
    call(client, "PATCH", f"/quotes/{quote['id']}", archived=True)
    detail = call(client, "GET", f"/quotes/{quote['id']}").get_json()["quote"]
    assert len(detail["lines"]) == 2 and detail["archived"] is True
    assert call(client, "GET", f"/quotes/{quote['id']}/pdf").status_code == 200


# --- Outils IA --------------------------------------------------------------------------------
def test_l_outil_archive_puis_la_liste_courante_l_exclut(agent, db, make_user):
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "creer_devis", {"client": "Paul", "lignes": "A | 1 | 100"})
    agent.resolve_action(user["id"], v["id"], True, "tester")
    number = db.list_quotes(user["id"])[0]["number"]
    k, v = agent.dispatch_tool_call(user["id"], "tester", "archiver_devis", {"devis": number})
    assert k == "pending" and "Archiver" in v["summary"] and number in v["summary"]
    assert "archivé" in agent.resolve_action(user["id"], v["id"], True, "tester")
    assert agent.dispatch_tool_call(user["id"], "tester", "lister_devis", {})[1] == []
    k, listed = agent.dispatch_tool_call(user["id"], "tester", "lister_devis", {"archives": True})
    assert k == "result" and [q["number"] for q in listed] == [number] and listed[0]["archived"] is True
    k, v = agent.dispatch_tool_call(user["id"], "tester", "archiver_devis", {"devis": number, "archiver": False})
    assert "Désarchiver" in v["summary"]
    agent.resolve_action(user["id"], v["id"], True, "tester")
    assert db.get_quote(user["id"], number)["archived_at"] is None


def test_archiver_un_devis_inconnu_est_une_erreur(agent, make_user):
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "archiver_devis", {"devis": "DV-2026-999"})
    assert k == "error" and "introuvable" in v


# --- Une tâche n'est pas un devis ------------------------------------------------------------
@pytest.mark.parametrize("titre", [
    "Faire un devis pour Atelier Nkolo : 2 tablettes à 85 000",
    "faire un devis de 45000 pour le client Yann",
    "Créer le devis de Paul", "Établir un devis", "fais un devis pour Yann",
])
def test_une_demande_de_devis_n_est_pas_acceptee_comme_tache(agent, make_user, titre):
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "creer_tache", {"titre": titre})
    assert k == "error" and "creer_devis" in v


@pytest.mark.parametrize("titre", ["Envoyer le devis à Nkolo", "Relancer Paul pour son devis", "Appeler le client", "Faire un point avec l'équipe"])
def test_les_vraies_taches_liees_aux_devis_restent_possibles(agent, make_user, titre):
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "creer_tache", {"titre": titre})
    assert k == "pending", v


# --- Actions en attente ----------------------------------------------------------------------
def test_les_actions_en_attente_se_retrouvent_apres_rechargement(client, db, alice, friend):
    mine = db.create_pending(alice, "creer_tache", {"titre": "A"}, "Créer la tâche « A »", 10)
    db.create_pending(friend, "creer_tache", {"titre": "B"}, "Créer la tâche « B »", 10)
    expired = db.create_pending(alice, "creer_tache", {"titre": "C"}, "Créer la tâche « C »", 10)
    with db.cursor() as cur:
        cur.execute("UPDATE pending_actions SET expires_at = now() - interval '1 minute' WHERE id = %s", (expired,))
    r = call(client, "GET", "/actions/pending")
    assert r.status_code == 200
    assert [(a["id"], a["summary"]) for a in r.get_json()["actions"]] == [(mine, "Créer la tâche « A »")]
    assert call(client, "GET", "/actions/pending", telegram_id=999).status_code == 403
    db.cancel_pending(mine, alice)
    assert call(client, "GET", "/actions/pending").get_json()["actions"] == []


def test_events_list_exposes_reminder_lead(client):
    from agenda import REMIND_MINUTES
    r = call(client, "GET", "/events?range=aujourd'hui")
    assert r.status_code == 200
    assert r.get_json()["reminder_minutes"] == REMIND_MINUTES
