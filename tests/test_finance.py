"""Chantiers et comptabilité simple (phase F1) : validation, API, isolation, outils IA."""
from datetime import date
from decimal import Decimal

import pytest

import commercial
import finance
from test_app_commercial import HEADERS, QUOTE, alice, call, client, friend, OTHER, OWNER  # noqa: F401

TODAY = date(2026, 11, 10)


# --- Validation pure ------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("2026-11-02", date(2026, 11, 2)), ("", TODAY), ("aujourd'hui", TODAY), ("hier", date(2026, 11, 9)),
])
def test_dates_acceptees(text, expected):
    assert finance.parse_date(text, TODAY) == expected


@pytest.mark.parametrize("text", ["02/11/2026", "2026-13-01", "2026-02-30", "2099-01-01", "demain"])
def test_dates_refusees(text):
    with pytest.raises(commercial.CommercialInputError):
        finance.parse_date(text, TODAY)


def test_mois_et_bornes():
    assert finance.parse_month("2026-12", TODAY) == (date(2026, 12, 1), date(2027, 1, 1), "2026-12")
    assert finance.parse_month("", TODAY) == (date(2026, 11, 1), date(2026, 12, 1), "2026-11")
    with pytest.raises(commercial.CommercialInputError):
        finance.parse_month("novembre", TODAY)


@pytest.mark.parametrize("word, kind", [("Dépense", "depense"), ("achat", "depense"), ("Recette", "recette"), ("acompte", "recette")])
def test_types_reconnus(word, kind):
    assert finance.clean_kind(word) == kind


def test_type_inconnu():
    with pytest.raises(commercial.CommercialInputError):
        finance.clean_kind("cadeau")


@pytest.mark.parametrize("amount", ["0", "-5", "abc", "10,005"])
def test_montants_refuses(amount):
    with pytest.raises(commercial.CommercialInputError):
        finance.parse_entry_amount(amount)


# --- API : chantiers -----------------------------------------------------------------------
def make_project(c, telegram_id=OWNER, **fields):
    fields.setdefault("name", "Villa Nkolo")
    r = c.post("/projects", json={"telegram_id": telegram_id, **fields}, headers=HEADERS)  # (« client » est aussi un champ)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["project"]


def test_creer_un_chantier_avec_budget_et_client_libre(client):
    project = make_project(client, client="Atelier Nkolo", budget="500000")
    assert project["status"] == "en_cours" and project["client"] == "Atelier Nkolo" and project["client_id"] is None
    assert project["budget"] == "500000.00" and project["income"] == "0" and project["margin"] == "0"


def test_chantier_lie_a_un_client_enregistre_et_a_un_devis(client):
    c = call(client, "POST", "/clients", text="Paul Martin").get_json()["client"]
    q = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]
    project = make_project(client, client="paul", quote=q["number"])
    assert project["client_id"] == c["id"] and project["client"] == "Paul Martin" and project["quote"] == q["number"]


def test_le_devis_fournit_le_client_s_il_manque(client):
    q = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]
    project = make_project(client, name="Peinture", quote=q["number"])
    assert project["client"] == "Paul Martin"


def test_devis_introuvable_et_nom_en_double(client):
    assert call(client, "POST", "/projects", name="X", quote="DV-2099-999").status_code == 400
    make_project(client)
    assert call(client, "POST", "/projects", name="villa nkolo").status_code == 409


@pytest.mark.parametrize("fields", [{"name": ""}, {"name": "A", "budget": "-1"}, {"name": "A", "budget": "x"},
                                    {"name": "A", "start_date": "2026-12-01", "end_date": "2026-11-01"}])
def test_chantier_invalide(client, fields):
    assert call(client, "POST", "/projects", **fields).status_code == 400


def test_statut_budget_et_archivage(client):
    project = make_project(client, budget="100")
    r = call(client, "PATCH", f"/projects/{project['id']}", status="termine", budget="250")
    assert r.get_json()["project"]["status_label"] == "terminé" and r.get_json()["project"]["budget"] == "250.00"
    call(client, "PATCH", f"/projects/{project['id']}", archived=True)
    assert call(client, "GET", "/projects").get_json()["projects"] == []
    archived = call(client, "GET", "/projects?archived=1").get_json()["projects"]
    assert [p["id"] for p in archived] == [project["id"]] and archived[0]["archived"] is True
    assert call(client, "PATCH", f"/projects/{project['id']}", archived="oui").status_code == 400
    assert call(client, "PATCH", f"/projects/{project['id']}").status_code == 400
    assert call(client, "DELETE", f"/projects/{project['id']}").status_code == 405


def test_chantiers_isoles_par_utilisateur(client, friend):
    mine = make_project(client)
    assert call(client, "GET", f"/projects/{mine['id']}", telegram_id=OTHER).status_code == 404
    assert call(client, "PATCH", f"/projects/{mine['id']}", telegram_id=OTHER, status="termine").status_code == 404
    assert call(client, "GET", "/projects", telegram_id=OTHER).get_json()["projects"] == []
    # le même nom reste libre chez un autre utilisateur
    make_project(client, telegram_id=OTHER)


# --- API : écritures et bilan ---------------------------------------------------------------
def add(client, telegram_id=OWNER, **fields):
    fields.setdefault("description", "Ciment")
    r = call(client, "POST", "/ledger", telegram_id=telegram_id, **fields)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["entry"]


def test_ecritures_et_totaux_du_chantier(client):
    p = make_project(client, budget="100000")
    add(client, kind="recette", amount="150000", description="Acompte", project=p["id"], date="2026-11-03")
    add(client, kind="depense", amount="40000", description="Ciment", category="matériaux", project=p["id"], date="2026-11-04")
    add(client, kind="depense", amount="12500", description="Transport", category="transport", project=p["id"], date="2026-11-05")
    got = call(client, "GET", f"/projects/{p['id']}").get_json()["project"]
    assert (got["income"], got["expense"], got["margin"]) == ("150000.00", "52500.00", "97500.00")
    assert got["budget_left"] == "47500.00" and got["budget_used_percent"] == 52
    assert got["margin_label"].replace(" ", " ") == "97 500 XAF"


def test_bilan_du_mois_par_categorie(client):
    add(client, kind="recette", amount="1000", description="Vente", date="2026-11-02")
    add(client, kind="depense", amount="300", description="Colle", category="matériaux", date="2026-11-03")
    add(client, kind="depense", amount="200", description="Clous", category="matériaux", date="2026-11-04")
    add(client, kind="depense", amount="999", description="Octobre", date="2026-10-30")
    s = call(client, "GET", "/ledger/summary?month=2026-11").get_json()["summary"]
    assert (s["income"], s["expense"], s["balance"], s["entries"]) == ("1000.00", "500.00", "500.00", 3)
    cats = {(c["kind"], c["category"]): c["total"] for c in s["by_category"]}
    assert cats[("depense", "matériaux")] == "500.00"
    assert call(client, "GET", "/ledger/summary?month=bidon").status_code == 400


def test_liste_filtree_par_mois_type_et_chantier(client):
    p = make_project(client)
    add(client, kind="depense", amount="10", description="A", project=p["id"], date="2026-11-02")
    add(client, kind="recette", amount="20", description="B", date="2026-11-03")
    add(client, kind="depense", amount="30", description="C", date="2026-10-03")
    month = call(client, "GET", "/ledger?month=2026-11").get_json()
    assert [e["description"] for e in month["entries"]] == ["B", "A"]
    assert [e["description"] for e in call(client, "GET", "/ledger?month=2026-11&kind=depense").get_json()["entries"]] == ["A"]
    assert [e["description"] for e in call(client, "GET", f"/ledger?month=2026-11&project={p['id']}").get_json()["entries"]] == ["A"]


def test_toutes_les_ecritures_d_un_chantier_sur_plusieurs_mois(client):
    p = make_project(client)
    add(client, kind="depense", amount="10", description="A", project=p["id"], date="2026-09-02")
    add(client, kind="depense", amount="20", description="B", project=p["id"], date="2026-11-02")
    got = call(client, "GET", f"/ledger?month=all&project={p['id']}").get_json()
    assert got["month"] == "all" and [e["description"] for e in got["entries"]] == ["B", "A"]


@pytest.mark.parametrize("fields", [
    {"kind": "cadeau", "amount": "5"}, {"kind": "depense", "amount": "0"}, {"kind": "depense", "amount": "5", "description": ""},
    {"kind": "depense", "amount": "5", "date": "2026-02-30"}, {"kind": "depense", "amount": "5", "project": "12"},
])
def test_ecriture_invalide(client, fields):
    fields.setdefault("description", "x")
    assert call(client, "POST", "/ledger", **fields).status_code == 400


def test_ecriture_sur_un_chantier_inexistant_ou_d_autrui(client, friend):
    mine = make_project(client)
    assert call(client, "POST", "/ledger", kind="depense", amount="5", description="x", project=999999).status_code == 404
    assert call(client, "POST", "/ledger", telegram_id=OTHER, kind="depense", amount="5", description="x", project=mine["id"]).status_code == 404


def test_supprimer_une_ecriture_seulement_la_sienne(client, friend):
    entry = add(client, kind="depense", amount="5")
    assert call(client, "DELETE", f"/ledger/{entry['id']}", telegram_id=OTHER).status_code == 404
    assert call(client, "DELETE", f"/ledger/{entry['id']}").status_code == 200
    assert call(client, "DELETE", f"/ledger/{entry['id']}").status_code == 404


def test_bilan_isole_par_utilisateur(client, friend):
    add(client, kind="recette", amount="777", description="Privé", date="2026-11-02")
    s = call(client, "GET", "/ledger/summary?month=2026-11", telegram_id=OTHER).get_json()["summary"]
    assert s["income"] == "0" and s["entries"] == 0
    assert call(client, "GET", "/ledger?month=2026-11", telegram_id=OTHER).get_json()["entries"] == []


def test_supprimer_le_chantier_en_base_garde_les_ecritures(client, db):
    p = make_project(client)
    add(client, kind="depense", amount="5", project=p["id"])
    with db.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (p["id"],))
    assert call(client, "GET", "/ledger").get_json()["entries"][0]["project"] is None


def test_limites(client, monkeypatch):
    import finance_store
    monkeypatch.setenv("MAX_PROJECTS", "1")
    make_project(client)
    assert call(client, "POST", "/projects", name="Deuxième").status_code == 409
    monkeypatch.setenv("MAX_LEDGER_ENTRIES", "1")
    add(client, kind="depense", amount="5")
    assert call(client, "POST", "/ledger", kind="depense", amount="5", description="y").status_code == 409


def test_acces_refuse_sans_authentification(client):
    for method, path in [("get", "/projects"), ("post", "/projects"), ("get", "/ledger"), ("get", "/ledger/summary"),
                         ("post", "/ledger"), ("delete", "/ledger/1")]:
        assert getattr(client, method)(path, **({"json": {}} if method != "get" else {})).status_code in (401, 403)


# --- Outils IA ------------------------------------------------------------------------------
def run(agent, user, name, args, accept=True):
    kind, value = agent.dispatch_tool_call(user["id"], "tester", name, args)
    if kind == "pending" and accept:
        return agent.resolve_action(user["id"], value["id"], True, "tester")
    return value if kind == "pending" else (value if kind == "result" else f"ERREUR {value}")


def test_les_outils_creent_un_chantier_et_des_ecritures(agent, db, make_user):
    import finance_store
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "creer_chantier", {"nom": "Villa Nkolo", "budget": "300000"})
    assert k == "pending" and "Villa Nkolo" in v["summary"] and "300" in v["summary"]
    assert "créé" in agent.resolve_action(user["id"], v["id"], True, "tester")

    k, v = agent.dispatch_tool_call(user["id"], "tester", "enregistrer_ecriture",
                                    {"type": "dépense", "montant": "25000", "description": "Ciment", "chantier": "nkolo", "categorie": "matériaux"})
    assert k == "pending" and "dépense" in v["summary"] and "Villa Nkolo" in v["summary"]
    assert "enregistrée" in agent.resolve_action(user["id"], v["id"], True, "tester")

    k, b = agent.dispatch_tool_call(user["id"], "tester", "bilan", {"chantier": "Villa Nkolo"})
    assert k == "result" and b["expense"] == "25000.00" and b["project"]["budget_left"] == "275000.00"
    k, listed = agent.dispatch_tool_call(user["id"], "tester", "lister_chantiers", {})
    assert [p["name"] for p in listed] == ["Villa Nkolo"]
    k, entries = agent.dispatch_tool_call(user["id"], "tester", "lister_ecritures", {"type": "dépense"})
    assert [e["description"] for e in entries["entries"]] == ["Ciment"]


def test_rien_n_est_ecrit_avant_la_confirmation(agent, db, make_user):
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "enregistrer_ecriture", {"type": "recette", "montant": "5000", "description": "Acompte"})
    assert k == "pending"
    assert db.cursor and True
    import finance_store
    assert finance_store.list_entries(user["id"]) == []
    agent.resolve_action(user["id"], v["id"], False, "tester")
    assert finance_store.list_entries(user["id"]) == []


@pytest.mark.parametrize("args", [
    {"type": "cadeau", "montant": "5", "description": "x"}, {"type": "dépense", "montant": "abc", "description": "x"},
    {"type": "dépense", "montant": "5", "description": ""}, {"type": "dépense", "montant": "5", "description": "x", "chantier": "inconnu"},
])
def test_arguments_invalides_refuses_avant_confirmation(agent, make_user, args):
    user = make_user()
    k, v = agent.dispatch_tool_call(user["id"], "tester", "enregistrer_ecriture", args)
    assert k == "error"


def test_chantier_ambigu_demande_de_preciser(agent, make_user):
    user = make_user()
    for name in ("Villa Nkolo", "Villa Eyenga"):
        run(agent, user, "creer_chantier", {"nom": name})
    k, v = agent.dispatch_tool_call(user["id"], "tester", "enregistrer_ecriture", {"type": "dépense", "montant": "5", "description": "x", "chantier": "villa"})
    assert k == "error"


def test_modifier_et_archiver_un_chantier_par_outil(agent, make_user):
    import finance_store
    user = make_user()
    run(agent, user, "creer_chantier", {"nom": "Toiture"})
    k, v = agent.dispatch_tool_call(user["id"], "tester", "modifier_chantier", {"chantier": "Toiture", "statut": "terminé", "archiver": True})
    assert k == "pending" and "terminé" in v["summary"] and "Archiver" in v["summary"]
    agent.resolve_action(user["id"], v["id"], True, "tester")
    got = finance_store.list_projects(user["id"], archived=True)[0]
    assert got["status"] == "termine" and got["archived_at"] is not None
    k, v = agent.dispatch_tool_call(user["id"], "tester", "modifier_chantier", {"chantier": "Toiture"})
    assert k == "error"


def test_un_testeur_ne_voit_pas_les_chantiers_d_un_autre(agent, make_user):
    a, b = make_user(), make_user()
    run(agent, a, "creer_chantier", {"nom": "Secret"})
    assert agent.dispatch_tool_call(b["id"], "tester", "lister_chantiers", {})[1] == []
    k, v = agent.dispatch_tool_call(b["id"], "tester", "enregistrer_ecriture", {"type": "dépense", "montant": "5", "description": "x", "chantier": "Secret"})
    assert k == "error"
