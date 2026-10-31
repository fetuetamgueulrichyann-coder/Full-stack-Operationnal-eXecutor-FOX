"""Outils IA de l'agenda interne et conversation des testeurs avec outils (Groq)."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def tz(agent):
    import tools

    return tools.TZ


@pytest.fixture
def tomorrow(tz):
    """Demain à 14h00, au format ISO attendu des outils."""
    day = datetime.now(tz) + timedelta(days=1)
    return day.strftime("%Y-%m-%d") + "T14:00"


def propose_and_confirm(agent, db, user, tool, args, role="tester"):
    kind, value = agent.dispatch_tool_call(user["id"], role, tool, args)
    assert kind == "pending", value
    return agent.resolve_action(user["id"], value["id"], True, role)


def events_of(db, user):
    return db.list_events(user["id"], datetime.now().astimezone() - timedelta(days=1), datetime.now().astimezone() + timedelta(days=30))


# --- Outils -------------------------------------------------------------------
def test_les_outils_agenda_sont_ouverts_aux_deux_roles(agent, tools):
    for name in ("lister_rdv", "creer_rdv", "modifier_rdv", "supprimer_rdv"):
        assert tools.REGISTRY[name].allowed_for("tester") and tools.REGISTRY[name].allowed_for("owner")
    assert tools.REGISTRY["lister_rdv"].classification == tools.READ
    assert tools.REGISTRY["creer_rdv"].needs_confirmation and tools.REGISTRY["supprimer_rdv"].needs_confirmation


def test_creation_proposee_puis_confirmee(agent, db, make_user, tomorrow):
    user = make_user()
    kind, value = agent.dispatch_tool_call(
        user["id"], "tester", "creer_rdv", {"titre": "Réunion", "debut": tomorrow, "duree": "1h30", "lieu": "Douala"}
    )
    assert kind == "pending" and "Réunion" in value["summary"] and "Douala" in value["summary"]
    assert events_of(db, user) == []  # rien n'est créé avant la confirmation
    assert agent.resolve_action(user["id"], value["id"], True, "tester") == "Rendez-vous ajouté."
    (event,) = events_of(db, user)
    assert event["title"] == "Réunion" and event["end_at"] - event["start_at"] == timedelta(minutes=90)


def test_creation_refusee_si_le_moment_est_passe(agent, make_user):
    user = make_user()
    kind, value = agent.dispatch_tool_call(user["id"], "tester", "creer_rdv", {"titre": "X", "debut": "2020-01-01T10:00"})
    assert kind == "error" and "passé" in value


def test_conflit_signale_a_la_proposition_et_apres_creation(agent, db, make_user, tomorrow):
    user = make_user()
    propose_and_confirm(agent, db, user, "creer_rdv", {"titre": "Premier", "debut": tomorrow})
    kind, value = agent.dispatch_tool_call(user["id"], "tester", "creer_rdv", {"titre": "Second", "debut": tomorrow})
    assert "chevauche : Premier" in value["summary"]
    assert "Premier" in agent.resolve_action(user["id"], value["id"], True, "tester")


def test_lister_ne_montre_que_ses_propres_rendez_vous(agent, db, make_user, tomorrow):
    mine, other = make_user(), make_user()
    propose_and_confirm(agent, db, mine, "creer_rdv", {"titre": "Le mien", "debut": tomorrow})
    propose_and_confirm(agent, db, other, "creer_rdv", {"titre": "L'autre", "debut": tomorrow})
    kind, value = agent.dispatch_tool_call(mine["id"], "tester", "lister_rdv", {"periode": "semaine"})
    assert kind == "result" and [e["title"] for e in value] == ["Le mien"]


def test_modifier_puis_supprimer(agent, db, make_user, tomorrow):
    user = make_user()
    propose_and_confirm(agent, db, user, "creer_rdv", {"titre": "Ancien", "debut": tomorrow, "lieu": "Paris"})
    (event,) = events_of(db, user)
    assert propose_and_confirm(agent, db, user, "modifier_rdv", {"rdv_id": event["id"], "titre": "Nouveau", "lieu": "aucun"}) == "Rendez-vous modifié."
    (event,) = events_of(db, user)
    assert event["title"] == "Nouveau" and event["location"] is None
    assert propose_and_confirm(agent, db, user, "supprimer_rdv", {"rdv_id": event["id"]}) == "Rendez-vous supprimé."
    assert events_of(db, user) == []


def test_impossible_de_toucher_au_rendez_vous_d_un_autre(agent, db, make_user, tomorrow):
    mine, other = make_user(), make_user()
    propose_and_confirm(agent, db, other, "creer_rdv", {"titre": "Privé", "debut": tomorrow})
    (event,) = events_of(db, other)
    for tool, args in (("modifier_rdv", {"rdv_id": event["id"], "titre": "Piraté"}), ("supprimer_rdv", {"rdv_id": event["id"]})):
        kind, value = agent.dispatch_tool_call(mine["id"], "tester", tool, args)
        assert kind == "error" and "introuvable" in value
    assert [e["title"] for e in events_of(db, other)] == ["Privé"]


def test_modification_sans_changement_refusee(agent, db, make_user, tomorrow):
    user = make_user()
    propose_and_confirm(agent, db, user, "creer_rdv", {"titre": "A", "debut": tomorrow})
    (event,) = events_of(db, user)
    kind, value = agent.dispatch_tool_call(user["id"], "tester", "modifier_rdv", {"rdv_id": event["id"]})
    assert kind == "error" and "aucune modification" in value


def test_annulation_de_la_creation(agent, db, make_user, tomorrow):
    user = make_user()
    propose_and_confirm(agent, db, user, "creer_rdv", {"titre": "A annuler", "debut": tomorrow})
    text, pending = agent.propose_undo(user["id"])
    assert pending and "Annuler" in pending[0]["summary"]
    assert agent.resolve_action(user["id"], pending[0]["id"], True, "tester") == "L'action précédente a été annulée."
    assert events_of(db, user) == []


def test_outil_reserve_au_proprietaire_refuse_a_un_testeur(agent, tools, make_user):
    ran = []
    tools.register(tools.Tool(name="t_proprio_seul", description="x", parameters=None, classification=tools.READ,
                              run=lambda: ran.append(1), roles=("owner",)))
    try:
        user = make_user()
        kind, _ = agent.dispatch_tool_call(user["id"], "tester", "t_proprio_seul", {})
        assert kind == "error" and ran == []
    finally:
        tools.REGISTRY.pop("t_proprio_seul", None)


def test_le_prompt_ne_parle_de_google_qu_au_proprietaire(agent, tools):
    saved = dict(tools.REGISTRY)
    try:
        tools.register(tools.Tool(name="lire_agenda", description="g", parameters=None, classification=tools.READ,
                                  run=lambda: [], roles=("owner",)))
        assert "agenda Google" in agent.system_prompt(role="owner")
        assert "agenda Google" not in agent.system_prompt(role="tester")
    finally:
        tools.REGISTRY.clear()
        tools.REGISTRY.update(saved)


# --- Conversation Groq avec outils -----------------------------------------------
class Reply:
    status_code = 200

    def __init__(self, message, tokens=10):
        self._data = {"choices": [{"message": message}], "usage": {"total_tokens": tokens}}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def tool_call(name, args, call_id="c1"):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def groq(agent, monkeypatch):
    state = SimpleNamespace(queue=[], sent=[])

    def post(url, **kwargs):
        state.sent.append(kwargs["json"])
        item = state.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(agent.httpx, "post", post)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "cle-groq-de-test")
    return state


def test_un_testeur_recoit_les_outils_de_son_role(agent, make_user, groq):
    user = make_user()
    groq.queue.append(Reply({"role": "assistant", "content": "Bonjour"}))
    assert agent.chat(user["id"], "Salut", "tester") == ("Bonjour", 10, [])
    names = {t["function"]["name"] for t in groq.sent[0]["tools"]}
    assert {"creer_rdv", "lister_rdv", "creer_tache"} <= names
    assert "lire_agenda" not in names and "creer_evenement" not in names
    schema = next(t for t in groq.sent[0]["tools"] if t["function"]["name"] == "creer_rdv")["function"]["parameters"]
    assert schema["type"] == "object" and schema["properties"]["titre"]["type"] == "string"


def test_mode_prive_masque_l_outil_de_memoire(agent, make_user, groq):
    user = make_user()
    groq.queue.append(Reply({"role": "assistant", "content": "ok"}))
    agent.chat(user["id"], "Salut", "tester", private=True)
    assert "retenir" not in {t["function"]["name"] for t in groq.sent[0]["tools"]}


def test_outil_de_lecture_execute_puis_reponse_finale(agent, db, make_user, groq, tomorrow):
    user = make_user()
    propose_and_confirm(agent, db, user, "creer_rdv", {"titre": "Dentiste", "debut": tomorrow})
    groq.queue += [Reply(tool_call("lister_rdv", {"periode": "semaine"})), Reply({"role": "assistant", "content": "Tu as le dentiste."})]
    reply, tokens, pending = agent.chat(user["id"], "Mon agenda ?", "tester")
    assert (reply, tokens, pending) == ("Tu as le dentiste.", 20, [])
    tool_message = groq.sent[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1" and "Dentiste" in tool_message["content"]


def test_ecriture_demande_confirmation_et_ne_s_execute_pas(agent, db, make_user, groq, tomorrow):
    user = make_user()
    groq.queue.append(Reply(tool_call("creer_rdv", {"titre": "Réunion", "debut": tomorrow})))
    reply, _, pending = agent.chat(user["id"], "Ajoute une réunion demain 14h", "tester")
    assert reply == "Action en attente de confirmation." and "Réunion" in pending[0]["summary"]
    assert events_of(db, user) == []
    assert len(groq.sent) == 1  # pas de second appel : on attend la confirmation


def test_arguments_invalides_sont_renvoyes_au_modele(agent, make_user, groq):
    user = make_user()
    bad = tool_call("creer_rdv", {})
    bad["tool_calls"][0]["function"]["arguments"] = "pas du json"
    groq.queue += [Reply(bad), Reply({"role": "assistant", "content": "Désolé"})]
    assert agent.chat(user["id"], "x", "tester")[0] == "Désolé"
    assert "arguments invalides" in groq.sent[1]["messages"][-1]["content"]


def test_outil_inconnu_refuse_et_journalise(agent, db, make_user, groq):
    user = make_user()
    groq.queue += [Reply(tool_call("lire_agenda", {"date_debut": "2030-01-01"})), Reply({"role": "assistant", "content": "ok"})]
    agent.chat(user["id"], "x", "tester")
    with db.cursor() as cur:
        cur.execute("SELECT event, tool FROM audit_log")
        assert ("denied", "lire_agenda") in cur.fetchall()


def test_requete_avec_outils_refusee_retombe_sur_du_texte_seul(agent, make_user, groq):
    user = make_user()
    groq.queue += [httpx.ConnectError("boom"), Reply({"role": "assistant", "content": "Réponse simple"})]
    assert agent.chat(user["id"], "x", "tester")[0] == "Réponse simple"
    assert "tools" in groq.sent[0] and "tools" not in groq.sent[1]


def test_nombre_de_tours_d_outils_borne(agent, make_user, groq, monkeypatch):
    user = make_user()
    monkeypatch.setattr(agent, "GROQ_MAX_TOOL_ROUNDS", 2)
    groq.queue += [Reply(tool_call("lister_rdv", {})), Reply(tool_call("lister_rdv", {}))]
    reply, _, pending = agent.chat(user["id"], "x", "tester")
    assert reply == "Je n'ai pas pu terminer cette demande." and pending == [] and len(groq.sent) == 2


def test_plafond_groq_atteint_n_est_pas_contourne(agent, make_user, groq, monkeypatch):
    user = make_user()
    monkeypatch.setattr(agent.db, "reserve_provider", lambda *a: False)
    with pytest.raises(agent.ProviderQuotaExceeded):
        agent.chat(user["id"], "x", "tester")
    assert groq.sent == []
