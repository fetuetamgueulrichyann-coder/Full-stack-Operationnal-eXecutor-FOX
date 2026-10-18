"""Flux de l'agent : outils de lecture, confirmation, refus, expiration, annulation."""
import psycopg2
import pytest


@pytest.fixture
def registry(agent, tools):
    """Enregistre des outils de test et les retire ensuite."""
    ran = {"lecture": 0, "ecriture": [], "annulation": []}

    def lecture():
        ran["lecture"] += 1
        return ["événement"]

    def ecriture(titre):
        ran["ecriture"].append(titre)
        return {"id": "evt-1"}

    registered = {
        "t_lecture": tools.Tool(
            name="t_lecture", description="lecture", parameters=None,
            classification=tools.READ, run=lecture,
        ),
        "t_ecriture": tools.Tool(
            name="t_ecriture", description="écriture", parameters=None,
            classification=tools.WRITE, run=ecriture,
            describe=lambda args: f"Créer {args['titre']}",
            done=lambda result: "Créé.",
            undo=lambda args, result: ran["annulation"].append(result["id"]),
        ),
        "t_echec": tools.Tool(
            name="t_echec", description="échoue", parameters=None,
            classification=tools.WRITE, run=lambda: (_ for _ in ()).throw(RuntimeError("panne")),
            describe=lambda args: "Échouer", done=lambda r: "?",
        ),
    }
    for tool in registered.values():
        tools.register(tool)
    yield ran
    for name in registered:
        tools.REGISTRY.pop(name, None)


@pytest.fixture
def gemini_script(agent, monkeypatch):
    """Fournit à Gemini une suite de réponses prévues à l'avance."""
    queue = []
    monkeypatch.setattr(
        agent.client.models, "generate_content", lambda model, contents, config: queue.pop(0)
    )
    return queue


def audit_events(db):
    with db.cursor() as cur:
        cur.execute("SELECT event, tool FROM audit_log ORDER BY id")
        return cur.fetchall()


def test_reponse_simple(agent, gemini_script, helpers):
    gemini_script.append(helpers.response("Bonjour !", tokens=25))
    assert agent.chat(1, "Salut") == ("Bonjour !", 25, [])


def test_outil_de_lecture_execute_directement(agent, db, registry, gemini_script, helpers):
    gemini_script.extend(
        [helpers.response(calls=[helpers.call("t_lecture")]), helpers.response("Voici.")]
    )
    reply, _, pending = agent.chat(1, "Que reste-t-il ?")
    assert reply == "Voici." and pending == []
    assert registry["lecture"] == 1
    assert ("executed", "t_lecture") in audit_events(db)


def test_outil_d_ecriture_est_propose_jamais_execute(agent, db, registry, gemini_script, helpers):
    gemini_script.append(
        helpers.response(calls=[helpers.call("t_ecriture", titre="Réunion")])
    )
    reply, _, pending = agent.chat(1, "Crée une réunion")
    assert reply == "Action en attente de confirmation."
    assert pending[0]["summary"] == "Créer Réunion"
    assert registry["ecriture"] == []  # rien n'a été exécuté
    assert ("proposed", "t_ecriture") in audit_events(db)


def test_outil_inconnu_est_refuse(agent, db, gemini_script, helpers):
    gemini_script.extend(
        [helpers.response(calls=[helpers.call("outil_invente")]), helpers.response("Désolé.")]
    )
    reply, _, _ = agent.chat(1, "Fais un truc")
    assert reply == "Désolé."
    assert ("denied", "outil_invente") in audit_events(db)


def test_l_outil_d_annulation_n_est_pas_expose_au_modele(agent, tools):
    assert tools.REGISTRY[agent.UNDO_TOOL].expose is False


def test_reponse_vide_reessayee_une_fois_puis_message_neutre(agent, gemini_script, helpers):
    gemini_script.extend([helpers.response(""), helpers.response("")])
    reply, _, _ = agent.chat(1, "Salut")
    assert reply == agent.EMPTY_REPLY


def test_reponse_vide_puis_valide(agent, gemini_script, helpers):
    gemini_script.extend([helpers.response(""), helpers.response("Enfin !")])
    assert agent.chat(1, "Salut")[0] == "Enfin !"


# --- Confirmation -------------------------------------------------------------------
def propose(agent, db, user_id=1, titre="Réunion"):
    return db.create_pending(user_id, "t_ecriture", {"titre": titre}, f"Créer {titre}", 10)


def test_confirmation_execute_une_seule_fois(agent, db, registry):
    action = propose(agent, db)
    assert agent.resolve_action(1, action, True) == "Créé."
    assert registry["ecriture"] == ["Réunion"]
    assert agent.resolve_action(1, action, True) == "Cette action n'est plus en attente."
    assert registry["ecriture"] == ["Réunion"]


def test_refus_n_execute_rien(agent, db, registry):
    action = propose(agent, db)
    assert agent.resolve_action(1, action, False) == "Action annulée."
    assert registry["ecriture"] == []
    assert agent.resolve_action(1, action, True) == "Cette action n'est plus en attente."
    assert registry["ecriture"] == []


def test_un_autre_utilisateur_ne_peut_pas_confirmer(agent, db, registry):
    action = propose(agent, db, user_id=1)
    assert agent.resolve_action(2, action, True) == "Cette action n'est plus en attente."
    assert agent.resolve_action(2, action, False) == "Cette action n'est plus en attente."
    assert registry["ecriture"] == []
    # L'action du propriétaire est toujours confirmable.
    assert agent.resolve_action(1, action, True) == "Créé."


def test_action_expiree_n_est_pas_executee(agent, db, registry):
    action = db.create_pending(1, "t_ecriture", {"titre": "Trop tard"}, "Créer", 0)
    assert "expiré" in agent.resolve_action(1, action, True)
    assert registry["ecriture"] == []
    assert ("expired", "t_ecriture") in audit_events(db)


def test_echec_d_execution_est_signale_et_journalise(agent, db, registry):
    action = db.create_pending(1, "t_echec", {}, "Échouer", 10)
    assert agent.resolve_action(1, action, True) == "L'action a échoué."
    assert ("failed", "t_echec") in audit_events(db)
    with db.cursor() as cur:
        cur.execute("SELECT status FROM pending_actions WHERE id = %s", (action,))
        assert cur.fetchone()[0] == "failed"


def test_outil_disparu_apres_la_proposition(agent, db, registry, tools):
    action = propose(agent, db)
    tools.REGISTRY.pop("t_ecriture")
    assert agent.resolve_action(1, action, True) == "Cet outil n'est plus disponible."


# --- Annulation ---------------------------------------------------------------------
def test_annulation_de_la_derniere_action(agent, db, registry):
    assert agent.propose_undo(1)[0] == "Aucune action récente à annuler."

    action = propose(agent, db)
    agent.resolve_action(1, action, True)
    message, pending = agent.propose_undo(1)
    assert message == "Action en attente de confirmation." and len(pending) == 1

    assert agent.resolve_action(1, pending[0]["id"], True) == "L'action précédente a été annulée."
    assert registry["annulation"] == ["evt-1"]
    # Une action ne s'annule qu'une fois.
    assert agent.propose_undo(1)[0] == "Aucune action récente à annuler."


def test_annulation_ne_concerne_que_son_propre_utilisateur(agent, db, registry):
    agent.resolve_action(1, propose(agent, db, user_id=1), True)
    assert agent.propose_undo(2)[0] == "Aucune action récente à annuler."


@pytest.mark.parametrize(
    "text, expected",
    [
        ("annule", True), ("Annule ça", True), ("/annuler", True),
        ("annule la dernière action", True), ("  annule   ça !", True),
        ("annule mon rendez-vous de demain", False), ("peux-tu annuler la réunion", False),
        ("", False),
    ],
)
def test_detection_de_la_demande_d_annulation(agent, text, expected):
    assert agent.is_undo_request(text) is expected


# --- Invites système --------------------------------------------------------------------
def test_invite_systeme_selon_les_outils(agent, registry):
    with_tools = agent.system_prompt(with_tools=True)
    without = agent.system_prompt(with_tools=False)
    assert "outils disponibles" in with_tools
    assert "aucun outil" in without.lower()
    assert "Date et heure actuelles" in with_tools
