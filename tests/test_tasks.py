"""Tâches : base de données, saisie, outils de l'agent (phase B1)."""
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def tasks(agent):
    import tasks as module

    return module


@pytest.fixture
def alice(make_owner):
    return make_owner(1001, "alice")["id"]


@pytest.fixture
def bob(make_user):
    return make_user("bob")["id"]


def days(db, n):
    return datetime.now().astimezone() + timedelta(days=n)


# --- Base de données ----------------------------------------------------------
def test_creation_et_valeurs_par_defaut(db, alice):
    task = db.create_task(alice, "Appeler le fournisseur")
    assert task["title"] == "Appeler le fournisseur"
    assert task["priority"] == "normale" and task["status"] == "a_faire"
    assert task["due_at"] is None and task["overdue"] is False and task["done_at"] is None


def test_les_taches_sont_privees_par_utilisateur(db, alice, bob):
    mine = db.create_task(alice, "Secret d'Alice")
    assert db.list_tasks(bob) == []
    assert db.get_task(bob, mine["id"]) is None
    assert db.update_task(bob, mine["id"], status="terminee") is None
    assert db.delete_task(bob, mine["id"]) is False
    assert db.get_task(alice, mine["id"])["status"] == "a_faire"


def test_tri_en_retard_puis_echeance_puis_sans_echeance(db, alice):
    db.create_task(alice, "sans échéance basse", "basse")
    db.create_task(alice, "sans échéance haute", "haute")
    db.create_task(alice, "dans 5 jours", due_at=days(db, 5))
    db.create_task(alice, "dans 2 jours", due_at=days(db, 2))
    db.create_task(alice, "en retard hier", due_at=days(db, -1))
    db.create_task(alice, "en retard il y a 3 jours", due_at=days(db, -3))
    assert [t["title"] for t in db.list_tasks(alice)] == [
        "en retard il y a 3 jours", "en retard hier", "dans 2 jours", "dans 5 jours",
        "sans échéance haute", "sans échéance basse",
    ]
    overdue = {t["title"]: t["overdue"] for t in db.list_tasks(alice)}
    assert overdue["en retard hier"] and not overdue["dans 2 jours"]


def test_liste_par_defaut_ne_montre_que_les_taches_ouvertes(db, alice):
    open_ = db.create_task(alice, "ouverte")
    doing = db.create_task(alice, "en cours")
    db.update_task(alice, doing["id"], status="en_cours")
    done = db.create_task(alice, "finie")
    db.update_task(alice, done["id"], status="terminee")
    cancelled = db.create_task(alice, "annulée")
    db.update_task(alice, cancelled["id"], status="annulee")
    assert {t["title"] for t in db.list_tasks(alice)} == {"ouverte", "en cours"}
    assert [t["title"] for t in db.list_tasks(alice, ("terminee",))] == ["finie"]
    assert len(db.list_tasks(alice, db.OPEN_STATUSES + db.CLOSED_STATUSES)) == 4
    assert open_["id"]


def test_une_tache_terminee_n_est_pas_en_retard(db, alice):
    task = db.create_task(alice, "passée", due_at=days(db, -2))
    assert db.get_task(alice, task["id"])["overdue"] is True
    db.update_task(alice, task["id"], status="terminee")
    assert db.get_task(alice, task["id"])["overdue"] is False


def test_terminer_date_la_cloture_et_rouvrir_l_efface(db, alice):
    task = db.create_task(alice, "x")
    done = db.update_task(alice, task["id"], status="terminee")
    assert done["done_at"] is not None
    assert db.update_task(alice, task["id"], status="a_faire")["done_at"] is None


def test_modification_partielle_garde_le_reste(db, alice):
    task = db.create_task(alice, "titre", "haute", days(db, 3))
    changed = db.update_task(alice, task["id"], title="nouveau")
    assert changed["title"] == "nouveau" and changed["priority"] == "haute"
    assert changed["due_at"] == task["due_at"]


def test_effacer_l_echeance_est_different_de_ne_pas_la_toucher(db, alice):
    task = db.create_task(alice, "x", due_at=days(db, 3))
    assert db.update_task(alice, task["id"], title="y")["due_at"] is not None
    assert db.update_task(alice, task["id"], due_at=None)["due_at"] is None


def test_la_modification_remet_a_jour_la_date_de_mise_a_jour(db, alice):
    task = db.create_task(alice, "x")
    with db.cursor() as cur:
        cur.execute("UPDATE tasks SET updated_at = now() - interval '5 days' WHERE id = %s", (task["id"],))
    assert db.update_task(alice, task["id"], title="y")["updated_at"] > task["updated_at"] - timedelta(seconds=1)
    assert db.get_task(alice, task["id"])["updated_at"] > datetime.now().astimezone() - timedelta(minutes=1)


def test_valeurs_interdites_refusees_par_la_base(db, alice):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        db.create_task(alice, "x", priority="urgentissime")
    with pytest.raises(psycopg2.errors.CheckViolation):
        db.update_task(alice, db.create_task(alice, "y")["id"], status="perdue")


def test_plafond_de_taches_ouvertes(db, alice, bob):
    for i in range(3):
        db.create_task(alice, f"t{i}", max_open=3)
    with pytest.raises(db.TaskLimit):
        db.create_task(alice, "de trop", max_open=3)
    db.create_task(bob, "autre utilisateur", max_open=3)  # le plafond est par utilisateur
    first = db.list_tasks(alice)[0]
    db.update_task(alice, first["id"], status="terminee", max_open=3)
    db.create_task(alice, "place libérée", max_open=3)  # une tâche close ne compte pas


def test_rouvrir_une_tache_respecte_le_plafond(db, alice):
    old = db.create_task(alice, "ancienne", max_open=2)
    db.update_task(alice, old["id"], status="terminee", max_open=2)
    db.create_task(alice, "a", max_open=2)
    db.create_task(alice, "b", max_open=2)
    with pytest.raises(db.TaskLimit):
        db.update_task(alice, old["id"], status="a_faire", max_open=2)
    assert db.get_task(alice, old["id"])["status"] == "terminee"
    # passer de « à faire » à « en cours » n'est pas une réouverture
    current = db.list_tasks(alice)[0]
    assert db.update_task(alice, current["id"], status="en_cours", max_open=2)["status"] == "en_cours"


def test_plafond_zero_signifie_sans_limite(db, alice):
    for i in range(4):
        db.create_task(alice, f"t{i}", max_open=0)
    assert len(db.list_tasks(alice)) == 4


def test_suppression_definitive(db, alice):
    task = db.create_task(alice, "x")
    assert db.delete_task(alice, task["id"]) is True
    assert db.get_task(alice, task["id"]) is None
    assert db.delete_task(alice, task["id"]) is False


def test_purge_ne_supprime_que_les_taches_closes_anciennes(db, alice):
    ids = {}
    for name, status, age in (
        ("close ancienne", "terminee", 120), ("annulée ancienne", "annulee", 100),
        ("close récente", "terminee", 10), ("ouverte ancienne", "a_faire", 400),
    ):
        task = db.create_task(alice, name)
        db.update_task(alice, task["id"], status=status)
        with db.cursor() as cur:
            cur.execute("UPDATE tasks SET updated_at = now() - make_interval(days => %s) WHERE id = %s", (age, task["id"]))
        ids[name] = task["id"]
    assert db.purge_closed_tasks(90) == 2
    assert {t["title"] for t in db.list_tasks(alice, db.OPEN_STATUSES + db.CLOSED_STATUSES)} == {
        "close récente", "ouverte ancienne",
    }
    assert db.purge_closed_tasks(0) == 0  # 0 = ne jamais purger


def test_la_purge_de_l_historique_purge_aussi_les_taches(db, alice):
    task = db.create_task(alice, "x")
    db.update_task(alice, task["id"], status="terminee")
    with db.cursor() as cur:
        cur.execute("UPDATE tasks SET updated_at = now() - interval '200 days'")
    assert db.purge_history()["tasks"] == 1


# --- Saisie -------------------------------------------------------------------
NOW = date(2026, 10, 21)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("25/10", date(2026, 10, 25)), ("25/10/2027", date(2027, 10, 25)), ("2026-11-02", date(2026, 11, 2)),
        ("demain", date(2026, 10, 22)), ("Demain", date(2026, 10, 22)), ("aujourd'hui", NOW),
        ("5/1", date(2027, 1, 5)),  # déjà passé cette année : l'an prochain
        ("21/10", NOW),  # aujourd'hui n'est pas « passé »
    ],
)
def test_echeances_reconnues(tasks, text, expected):
    due = tasks.parse_due(text, NOW)
    assert due.date() == expected and (due.hour, due.minute) == (23, 59)


def test_echeance_avec_heure(tasks):
    due = tasks.parse_due("2026-10-25T09:30")
    assert (due.hour, due.minute) == (9, 30) and due.tzinfo is not None


@pytest.mark.parametrize("text", ["bientôt", "32/01", "31/02", "2026-13-01", "", "25-10", "25/10/26"])
def test_echeances_invalides(tasks, text):
    with pytest.raises(tasks.TaskInputError):
        tasks.parse_due(text, NOW)


def test_saisie_rapide_complete(tasks):
    title, priority, due = tasks.parse_quick("Appeler le fournisseur | 25/10 | haute", NOW)
    assert (title, priority, due.date()) == ("Appeler le fournisseur", "haute", date(2026, 10, 25))


def test_saisie_rapide_ordre_libre_et_alias(tasks):
    assert tasks.parse_quick("x | urgent | demain", NOW)[1] == "haute"
    assert tasks.parse_quick("x | basse", NOW) == ("x", "basse", None)


def test_saisie_rapide_titre_seul_et_espaces(tasks):
    assert tasks.parse_quick("   Ranger   le  bureau  ", NOW) == ("Ranger le bureau", "normale", None)
    assert tasks.parse_quick("x | | ", NOW) == ("x", "normale", None)


@pytest.mark.parametrize("text", ["", "   ", "| 25/10", "x | n'importe quoi", "a" * 201])
def test_saisie_rapide_invalide(tasks, text):
    with pytest.raises(tasks.TaskInputError):
        tasks.parse_quick(text, NOW)


def test_le_message_d_erreur_cite_la_partie_incomprise(tasks):
    with pytest.raises(tasks.TaskInputError, match="n'importe quoi"):
        tasks.parse_quick("x | n'importe quoi", NOW)


def test_les_dates_de_l_api_sont_dans_le_fuseau_de_fox(db, tasks, alice):
    task = db.create_task(alice, "x", due_at=tasks.parse_due("2026-10-25"))
    shown = tasks.public(db.get_task(alice, task["id"]))
    assert shown["due_at"].startswith("2026-10-25T23:59")


# --- Outils de l'agent --------------------------------------------------------
def test_les_outils_de_taches_sont_enregistres(agent, tools):
    assert tools.REGISTRY["lister_taches"].classification == tools.READ
    assert tools.REGISTRY["creer_tache"].needs_confirmation
    assert tools.REGISTRY["modifier_tache"].needs_confirmation
    assert all(tools.REGISTRY[n].needs_user for n in ("lister_taches", "creer_tache", "modifier_tache"))
    assert {"creer_tache", "lister_taches"} <= {t.name for t in agent.exposed_tools("tester")}


def test_l_outil_recoit_l_utilisateur_et_jamais_celui_du_modele(tools, db, tasks, alice, bob):
    tools.REGISTRY["creer_tache"].execute({"titre": "Pour Alice", "user_id": bob}, alice)
    assert [t["title"] for t in db.list_tasks(alice)] == ["Pour Alice"]
    assert db.list_tasks(bob) == []


def test_lister_taches_resume(tools, db, tasks, alice):
    db.create_task(alice, "en retard", due_at=days(db, -1))
    db.create_task(alice, "plus tard", "haute")
    done = db.create_task(alice, "finie")
    db.update_task(alice, done["id"], status="terminee")
    result = tools.REGISTRY["lister_taches"].execute({}, alice)
    assert [t["title"] for t in result] == ["en retard", "plus tard"]
    assert result[0]["overdue"] is True and result[0]["echeance"] is not None
    assert [t["title"] for t in tools.REGISTRY["lister_taches"].execute({"statut": "terminee"}, alice)] == ["finie"]


def test_resume_de_creation(tools, tasks, alice):
    tool = tools.REGISTRY["creer_tache"]
    assert tool.summarize({"titre": "Payer la facture", "echeance": "2026-10-30", "priorite": "haute"}, alice) == (
        "Créer la tâche « Payer la facture »\nÉchéance : 30/10/2026\nPriorité : haute"
    )
    assert tool.summarize({"titre": "Simple"}, alice) == "Créer la tâche « Simple »"


def test_resume_et_resultat_de_cloture(tools, db, tasks, alice):
    task = db.create_task(alice, "Relancer Paul")
    tool = tools.REGISTRY["modifier_tache"]
    assert tool.summarize({"tache_id": task["id"], "statut": "terminee"}, alice) == "Terminer la tâche « Relancer Paul »"
    assert tool.summarize({"tache_id": task["id"], "statut": "annulee"}, alice) == "Annuler la tâche « Relancer Paul »"
    result = tool.execute({"tache_id": task["id"], "statut": "terminee"}, alice)
    assert tool.done(result) == "Tâche terminée."
    assert db.get_task(alice, task["id"])["status"] == "terminee"
    assert tool.done({"status": "a_faire"}) == "Tâche modifiée."


def test_resume_de_modification_multiple(tools, db, tasks, alice):
    task = db.create_task(alice, "Ancien")
    text = tools.REGISTRY["modifier_tache"].summarize(
        {"tache_id": task["id"], "titre": "Nouveau", "priorite": "basse", "echeance": "aucune", "statut": "en_cours"}, alice
    )
    assert text == (
        "Modifier la tâche « Ancien »\nNouveau statut : en cours\nNouveau titre : « Nouveau »\n"
        "Nouvelle priorité : basse\nÉchéance supprimée"
    )


def test_modifier_une_tache_d_un_autre_utilisateur_est_refuse(tools, db, tasks, alice, bob):
    task = db.create_task(alice, "À Alice")
    tool = tools.REGISTRY["modifier_tache"]
    with pytest.raises(tasks.TaskInputError):
        tool.summarize({"tache_id": task["id"], "statut": "terminee"}, bob)
    with pytest.raises(tasks.TaskInputError):
        tool.execute({"tache_id": task["id"], "statut": "terminee"}, bob)
    assert db.get_task(alice, task["id"])["status"] == "a_faire"


def test_modifier_sans_rien_demander_est_refuse(tools, db, tasks, alice):
    task = db.create_task(alice, "x")
    with pytest.raises(tasks.TaskInputError):
        tools.REGISTRY["modifier_tache"].summarize({"tache_id": task["id"]}, alice)
    with pytest.raises(tasks.TaskInputError):
        tools.REGISTRY["modifier_tache"].execute({"tache_id": task["id"]}, alice)


def test_effacer_l_echeance_par_l_outil(tools, db, tasks, alice):
    task = db.create_task(alice, "x", due_at=days(db, 3))
    tools.REGISTRY["modifier_tache"].execute({"tache_id": task["id"], "echeance": "aucune"}, alice)
    assert db.get_task(alice, task["id"])["due_at"] is None


@pytest.mark.parametrize("args", [{"titre": ""}, {"titre": "x", "priorite": "folle"}, {"titre": "x", "echeance": "bientôt"}])
def test_creation_invalide_par_l_outil(tools, tasks, alice, args):
    with pytest.raises(tasks.TaskInputError):
        tools.REGISTRY["creer_tache"].execute(args, alice)
