"""Contexte envoyé au modèle : limité et raccourci."""


def test_shorten_garde_debut_et_fin(agent, monkeypatch):
    monkeypatch.setattr(agent, "HISTORY_MAX_CHARS", 20)
    short = agent.shorten("A" * 10 + "B" * 100 + "C" * 10)
    assert short.startswith("A" * 10) and short.endswith("C" * 10)
    assert len(short) < 40


def test_texte_court_inchange(agent):
    assert agent.shorten("bonjour") == "bonjour"


def test_historique_utile_ignore_les_reponses_vides(agent, db):
    db.save_exchange(1, "q1", "r1")
    db.save_exchange(1, "q2", agent.EMPTY_REPLY)
    assert agent.useful_history(1, 10) == [("q1", "r1")]


def test_nombre_d_echanges_envoyes_au_modele(agent, db, monkeypatch):
    monkeypatch.setattr(agent, "HISTORY_LIMIT", 3)
    for i in range(8):
        db.save_exchange(1, f"q{i}", f"r{i}")
    contents = agent.build_contents(1, "nouvelle")
    assert len(contents) == 3 * 2 + 1
    assert contents[-1].parts[0].text == "nouvelle"
