"""Outils IA des clients et des devis (phase D4)."""
import pytest

LINES = "Tablette de comptoir | 2 | 85000\nÉtagères murales | 4 | 32000"


def dispatch(agent, user, tool, args, role="tester"):
    return agent.dispatch_tool_call(user["id"], role, tool, args)


def confirm(agent, user, tool, args, role="tester"):
    kind, value = dispatch(agent, user, tool, args, role)
    assert kind == "pending", value
    return agent.resolve_action(user["id"], value["id"], True, role)


def test_les_outils_sont_ouverts_aux_deux_roles(agent, tools):
    for name in ("lister_clients", "creer_client", "lister_devis", "creer_devis", "changer_statut_devis"):
        assert tools.REGISTRY[name].allowed_for("tester") and tools.REGISTRY[name].allowed_for("owner")
    assert tools.REGISTRY["lister_devis"].classification == tools.READ
    assert tools.REGISTRY["creer_devis"].needs_confirmation and tools.REGISTRY["creer_client"].needs_confirmation


def test_devis_propose_avec_totaux_calcules_par_fox_puis_confirme(agent, db, make_user):
    user = make_user()
    kind, value = dispatch(agent, user, "creer_devis", {"client": "Atelier Nkolo", "lignes": LINES})
    assert kind == "pending"
    summary = value["summary"].replace("\u202f", " ")  # les montants utilisent une espace insécable fine
    assert "Atelier Nkolo" in summary and "client non enregistré" in summary
    assert "Sous-total : 298 000 XAF" in summary
    assert "TVA 19,25 % : 57 365 XAF" in summary
    assert "Total : 355 365 XAF" in summary
    assert db.list_quotes(user["id"]) == []  # rien n'est créé avant la confirmation
    reply = agent.resolve_action(user["id"], value["id"], True, "tester").replace("\u202f", " ")
    (quote,) = db.list_quotes(user["id"])
    assert quote["number"] in reply and "355 365 XAF" in reply
    assert quote["client_name"] == "Atelier Nkolo" and quote["client_id"] is None


def test_devis_rattache_au_client_enregistre(agent, db, make_user):
    user = make_user()
    client = db.create_client(user["id"], "Boulangerie Ewane")
    kind, value = dispatch(agent, user, "creer_devis", {"client": "boulangerie ewane", "lignes": "Pétrin | 1 | 500000", "tva": "18"})
    assert kind == "pending" and "client non enregistré" not in value["summary"] and "TVA 18 %" in value["summary"]
    agent.resolve_action(user["id"], value["id"], True, "tester")
    (quote,) = db.list_quotes(user["id"])
    assert quote["client_id"] == client["id"] and quote["client_name"] == "Boulangerie Ewane"


def test_plusieurs_clients_possibles_demandent_une_precision(agent, db, make_user):
    user = make_user()
    db.create_client(user["id"], "Atelier Nkolo")
    db.create_client(user["id"], "Atelier Mbarga")
    kind, value = dispatch(agent, user, "creer_devis", {"client": "Atelier", "lignes": "X | 1 | 10"})
    assert kind == "error" and "Plusieurs clients" in value


@pytest.mark.parametrize("lignes", ["", "Étagère 4 32000", "Étagère | 0 | 100", "Étagère | 2 | abc"])
def test_lignes_invalides_refusees_a_la_proposition(agent, make_user, lignes):
    user = make_user()
    kind, value = dispatch(agent, user, "creer_devis", {"client": "Paul", "lignes": lignes})
    assert kind == "error" and value.startswith("parametres invalides")


def test_un_utilisateur_ne_voit_et_ne_modifie_que_ses_devis(agent, db, make_user):
    mine, other = make_user(), make_user()
    confirm(agent, mine, "creer_devis", {"client": "Moi", "lignes": "A | 1 | 100"})
    confirm(agent, other, "creer_devis", {"client": "Autre", "lignes": "B | 1 | 200"})
    confirm(agent, other, "creer_devis", {"client": "Autre 2", "lignes": "C | 1 | 300"})
    kind, value = dispatch(agent, mine, "lister_devis", {})
    assert kind == "result" and [q["client"] for q in value] == ["Moi"]
    # la numérotation est propre à chaque utilisateur : le 2e devis de l'autre n'existe pas chez moi
    foreign = db.list_quotes(other["id"])[0]["number"]
    assert db.get_quote(mine["id"], foreign) is None
    kind, value = dispatch(agent, mine, "changer_statut_devis", {"devis": foreign, "statut": "accepté"})
    assert kind == "error" and "introuvable" in value


def test_changer_le_statut_et_filtrer_la_liste(agent, db, make_user):
    user = make_user()
    confirm(agent, user, "creer_devis", {"client": "Paul", "lignes": "A | 1 | 100"})
    number = db.list_quotes(user["id"])[0]["number"]
    kind, value = dispatch(agent, user, "changer_statut_devis", {"devis": number, "statut": "envoyé"})
    assert kind == "pending" and number in value["summary"] and "envoyé" in value["summary"]
    assert "envoyé" in agent.resolve_action(user["id"], value["id"], True, "tester")
    assert db.get_quote(user["id"], number)["status"] == "envoye"
    kind, listed = dispatch(agent, user, "lister_devis", {"statut": "envoyé"})
    assert kind == "result" and len(listed) == 1
    kind, listed = dispatch(agent, user, "lister_devis", {"statut": "accepté"})
    assert kind == "result" and listed == []
    kind, value = dispatch(agent, user, "lister_devis", {"statut": "n'importe quoi"})
    assert kind == "error"


def test_creer_un_client_puis_refuser_un_doublon(agent, db, make_user):
    user = make_user()
    kind, value = dispatch(agent, user, "creer_client", {"nom": "Atelier Nkolo", "telephone": "6 99 12 34 56", "adresse": "Douala"})
    assert kind == "pending" and "Atelier Nkolo" in value["summary"] and "Douala" in value["summary"]
    assert db.list_clients(user["id"]) == []
    assert "Atelier Nkolo" in agent.resolve_action(user["id"], value["id"], True, "tester")
    (client,) = db.list_clients(user["id"])
    assert client["phone"] == "6 99 12 34 56"
    kind, value = dispatch(agent, user, "creer_client", {"nom": "atelier nkolo"})
    assert kind == "error" and "déjà" in value
    kind, value = dispatch(agent, user, "creer_client", {"nom": "Paul", "email": "pas-un-email"})
    assert kind == "error" and "e-mail" in value


def test_lister_clients_filtre_sur_le_nom(agent, db, make_user):
    user = make_user()
    db.create_client(user["id"], "Atelier Nkolo")
    db.create_client(user["id"], "Boulangerie Ewane")
    kind, value = dispatch(agent, user, "lister_clients", {"recherche": "nkolo"})
    assert kind == "result" and [c["name"] for c in value] == ["Atelier Nkolo"]


def test_l_utilisateur_vient_toujours_du_serveur(agent, db, make_user):
    mine, other = make_user(), make_user()
    kind, value = dispatch(agent, mine, "creer_devis", {"client": "Paul", "lignes": "A | 1 | 100", "user_id": other["id"]})
    assert kind == "pending"
    agent.resolve_action(mine["id"], value["id"], True, "tester")
    assert len(db.list_quotes(mine["id"])) == 1 and db.list_quotes(other["id"]) == []


def test_la_consigne_dirige_les_devis_vers_les_bons_outils(agent):
    prompt = agent.system_prompt(role="owner")
    assert "creer_devis" in prompt and "jamais une tâche" in prompt
