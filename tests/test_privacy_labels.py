"""Étiquetage des échanges : catégories calculées par le serveur à partir des données réellement utilisées."""
import pytest

from test_app_commercial import OWNER, alice, call, client, friend  # noqa: F401
from test_files import JPEG, seen, send, upload  # noqa: F401


@pytest.fixture
def gemini(agent, monkeypatch):
    queue = []
    monkeypatch.setattr(agent.client.models, "generate_content", lambda model, contents, config: queue.pop(0))
    return queue


def last(db):
    with db.cursor() as cur:
        cur.execute("SELECT data_classes, engine FROM conversations ORDER BY id DESC LIMIT 1")
        return cur.fetchone()


def test_un_message_simple_est_etiquete_texte_libre_avec_son_moteur(client, db, gemini, helpers):
    gemini.append(helpers.response("Bonjour !"))
    assert send(client, "Salut").status_code == 200
    assert last(db) == (["texte_libre"], "gemini")


def test_un_outil_d_agenda_ajoute_personnel(client, db, gemini, helpers):
    gemini.extend([helpers.response(calls=[helpers.call("lister_rdv")]), helpers.response("Rien de prévu.")])
    send(client, "Qu'ai-je demain ?")
    assert last(db)[0] == ["texte_libre", "personnel"]


def test_un_outil_metier_ajoute_metier(client, db, gemini, helpers):
    gemini.extend([helpers.response(calls=[helpers.call("lister_clients")]), helpers.response("Aucun client.")])
    send(client, "Mes clients ?")
    assert last(db)[0] == ["texte_libre", "metier"]


def test_plusieurs_outils_donnent_l_union(client, db, gemini, helpers):
    gemini.extend([
        helpers.response(calls=[helpers.call("lister_rdv")]),
        helpers.response(calls=[helpers.call("lister_clients")]),
        helpers.response("Voilà."),
    ])
    send(client, "Fais le point")
    assert last(db)[0] == ["texte_libre", "personnel", "metier"]


def test_la_memoire_dans_le_contexte_ajoute_personnel(client, db, alice, gemini, helpers):
    import memory

    memory.remember(alice, "Je préfère les devis en francs CFA")
    gemini.append(helpers.response("D'accord."))
    send(client, "Bonjour")
    assert last(db)[0] == ["texte_libre", "personnel"]


def test_un_fichier_joint_ajoute_fichier(client, db, seen):
    file_id = upload(client, JPEG, "mur.jpg").get_json()["file"]["id"]
    send(client, "Regarde", [file_id])
    assert last(db)[0] == ["texte_libre", "fichier"]


def test_le_modele_ne_peut_pas_reduire_l_etiquette(client, db, gemini, helpers):
    """Un modèle qui affirme n'avoir utilisé aucune donnée sensible ne change rien : seul le code étiquette."""
    gemini.extend([
        helpers.response(calls=[helpers.call("lister_rdv", categorie="aucune", data_classes=[])]),
        helpers.response("Aucune donnée sensible utilisée, promis."),
    ])
    send(client, "Mon agenda ?")
    assert last(db)[0] == ["texte_libre", "personnel"]


def test_l_outil_echoue_mais_la_donnee_est_comptee(client, db, gemini, helpers, tools, monkeypatch):
    original = tools.REGISTRY["lister_rdv"]

    def boom(**kwargs):
        raise RuntimeError("panne")

    monkeypatch.setitem(tools.REGISTRY, "lister_rdv", tools.Tool(
        name="lister_rdv", description="x", parameters=None, classification=tools.READ, run=boom,
        needs_user=True, category="personnel"))
    gemini.extend([helpers.response(calls=[helpers.call("lister_rdv")]), helpers.response("Erreur.")])
    send(client, "Mon agenda ?")
    assert last(db)[0] == ["texte_libre", "personnel"]
    assert original is not None


def test_l_historique_ancien_non_etiquete_rend_le_nouvel_echange_inconnu(client, db, alice, gemini, helpers):
    chat_id = db.save_exchange(alice, "ancien message", "ancienne réponse")  # data_classes NULL : d'avant la confidentialité
    gemini.append(helpers.response("Suite."))
    send(client, "Et ensuite ?", chat_id=chat_id)
    assert last(db) == (None, "gemini")  # inconnu : traité comme sensible partout


def test_l_historique_etiquete_transmet_ses_categories(client, db, alice, gemini, helpers):
    chat_id = db.save_exchange(alice, "mon agenda", "voici", data_classes=["texte_libre", "personnel"], engine="groq")
    gemini.append(helpers.response("Suite."))
    send(client, "Merci", chat_id=chat_id)
    assert last(db) == (["texte_libre", "personnel"], "gemini")  # l'échange précédent est dans le contexte


def test_une_conversation_neuve_ne_herite_de_rien(client, db, alice, gemini, helpers):
    old = db.save_exchange(alice, "mon agenda", "voici", data_classes=["texte_libre", "personnel", "metier"])
    new_chat = db.start_chat(alice)
    assert new_chat != old
    gemini.append(helpers.response("Bonjour."))
    send(client, "Salut", chat_id=new_chat)
    assert last(db)[0] == ["texte_libre"]


def test_le_secours_groq_est_note_comme_moteur(client, db, agent, monkeypatch):
    monkeypatch.setattr(agent, "groq_complete", None, raising=False)
    # le moteur « groq » est inscrit par groq_post ; vérifié au niveau de la fonction
    import privacy

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}}

    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: Response())
    monkeypatch.setattr(agent.db, "reserve_provider", lambda *a, **k: True)
    tokens = privacy.begin()
    try:
        agent.groq_post([{"role": "user", "content": "x"}])
        assert privacy.collect() == (["texte_libre"], "groq")
    finally:
        privacy.end(tokens)


def test_la_confirmation_d_une_action_etiquette_son_echange(client, db, alice, gemini, helpers):
    gemini.append(helpers.response(calls=[helpers.call("creer_client", nom="Paul Martin")]))
    pending = send(client, "Ajoute le client Paul Martin").get_json()["pending"]
    assert pending
    call(client, "POST", f"/actions/{pending[0]['id']}/confirm")
    classes, engine = last(db)
    assert classes == ["texte_libre", "metier"] and engine is None


def test_chaque_outil_du_registre_declare_une_categorie_valide(tools, app_module):
    import commercial_tools, finance_tools, privacy, tasks  # noqa: E401,F401

    assert len(tools.REGISTRY) >= 26  # les 4 outils Google Calendar ne sont enregistrés que si Google est configuré (voir test_architecture)
    for name, tool in tools.REGISTRY.items():
        if name.startswith("t_"):  # outils fabriqués par d'autres tests
            continue
        assert tool.category in privacy.TOOL_CATEGORIES, name
    expected = {"personnel": {"lister_rdv", "creer_rdv", "lister_taches", "retenir"}, "metier": {"lister_clients", "creer_devis", "lister_chantiers", "bilan"}}
    for category, names in expected.items():
        for name in names:
            assert tools.REGISTRY[name].category == category, name


def test_un_outil_sans_categorie_ne_peut_pas_etre_enregistre(tools):
    for category in (None, "", "inconnue", "voix", "texte_libre"):
        with pytest.raises(ValueError, match="catégorie"):
            tools.register(tools.Tool(name="t_sans", description="x", parameters=None, classification=tools.READ,
                                      run=lambda: 1, category=category))
    assert "t_sans" not in tools.REGISTRY


def test_un_outil_non_autorise_ne_marque_pas_de_donnee(client, db, gemini, helpers):
    """Un outil refusé (rôle) n'est pas exécuté : l'échange n'est pas étiqueté comme ayant lu l'agenda."""
    gemini.extend([helpers.response(calls=[helpers.call("outil_invente")]), helpers.response("Désolé.")])
    send(client, "Fais un truc")
    assert last(db)[0] == ["texte_libre"]
