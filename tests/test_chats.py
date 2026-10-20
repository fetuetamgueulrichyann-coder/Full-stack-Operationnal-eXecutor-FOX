"""Conversations, mode privé et purge : couche base de données (PostgreSQL réelle)."""
import threading
import uuid

import psycopg2
import pytest

from conftest import DB_DIR, apply_init_scripts, drop_database, make_database


def count(db, query, *params):
    with db.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()[0]


def age_chat(db, chat_id, days):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE chats SET updated_at = now() - make_interval(days => %s) WHERE id = %s",
            (days, chat_id),
        )


# --- Conversations -----------------------------------------------------------
def test_le_premier_message_cree_une_conversation_avec_un_titre(db):
    chat_id = db.save_exchange(1, "  Bonjour   FOX,\ncomment vas-tu ? ", "Bien !")
    chats = db.list_chats(1)
    assert [c["id"] for c in chats] == [chat_id]
    assert chats[0]["title"] == "Bonjour FOX, comment vas-tu ?"
    assert chats[0]["messages"] == 1


def test_titre_long_raccourci_et_titre_conserve(db):
    chat_id = db.save_exchange(1, "x" * 200, "r")
    title = db.list_chats(1)[0]["title"]
    assert len(title) == db.TITLE_MAX_CHARS and title.endswith("…")
    db.save_exchange(1, "autre sujet", "r", chat_id)
    assert db.list_chats(1)[0]["title"] == title  # le titre ne change pas ensuite


def test_chaque_conversation_a_son_propre_contexte(db):
    first = db.save_exchange(1, "sujet A", "réponse A")
    second = db.start_chat(1)
    assert second != first
    db.save_exchange(1, "sujet B", "réponse B")
    assert db.load_history(1, 10) == [("sujet B", "réponse B")]  # conversation active
    assert db.load_history(1, 10, first) == [("sujet A", "réponse A")]


def test_nouvelle_conversation_reutilise_la_conversation_vide(db):
    db.save_exchange(1, "bonjour", "salut")
    empty = db.start_chat(1)
    assert db.start_chat(1) == empty
    assert db.start_chat(1) == empty
    assert count(db, "SELECT count(*) FROM chats WHERE user_id = 1") == 2  # pas d'accumulation
    assert len(db.list_chats(1)) == 1
    assert empty not in [c["id"] for c in db.list_chats(1)]  # une conversation vide n'est pas listée


def test_reprendre_une_conversation_la_remet_en_tete(db):
    first = db.save_exchange(1, "sujet A", "réponse A")
    db.start_chat(1)
    db.save_exchange(1, "sujet B", "réponse B")
    assert db.open_chat(1, first) is True
    assert db.current_chat_id(1) == first
    db.save_exchange(1, "suite de A", "ok")
    assert db.load_history(1, 10) == [("sujet A", "réponse A"), ("suite de A", "ok")]


def test_ecrire_dans_une_ancienne_conversation_la_remet_en_tete(db):
    first = db.save_exchange(1, "sujet A", "a")
    second = db.start_chat(1)
    db.save_exchange(1, "sujet B", "b")
    assert db.current_chat_id(1) == second
    db.save_exchange(1, "retour à A", "ok", first)
    assert db.current_chat_id(1) == first


def test_une_conversation_ancienne_reprise_n_est_pas_purgee(db, make_user):
    tester = make_user()
    chat_id = db.save_exchange(tester["id"], "vieux sujet", "r")
    age_chat(db, chat_id, 60)  # au-delà de 30 jours d'inactivité
    db.save_exchange(tester["id"], "on en reparle", "ok", chat_id)  # activité récente
    assert db.purge_history(90, 30)["chats"] == 0
    assert len(db.load_history(tester["id"], 10, chat_id)) == 2


def test_aucun_acces_aux_conversations_d_un_autre(db):
    mine = db.save_exchange(1, "privé de moi", "ok")
    other = db.save_exchange(2, "privé de lui", "ok")
    assert db.chat_belongs_to(1, mine) and not db.chat_belongs_to(1, other)
    assert db.open_chat(1, other) is False
    assert db.delete_chat(1, other) is False
    with pytest.raises(LookupError):
        db.save_exchange(1, "intrusion", "x", other)
    assert db.load_history(2, 10) == [("privé de lui", "ok")]
    assert db.list_chats(1)[0]["id"] == mine and len(db.list_chats(1)) == 1


def test_supprimer_une_conversation_supprime_ses_messages_seulement(db):
    a = db.save_exchange(1, "A", "a")
    b = db.start_chat(1)
    db.save_exchange(1, "B", "b")
    assert db.delete_chat(1, a) is True
    assert count(db, "SELECT count(*) FROM conversations WHERE chat_id = %s", a) == 0
    assert db.load_history(1, 10, b) == [("B", "b")]
    assert db.delete_chat(1, a) is False  # déjà supprimée


def test_supprimer_tout_l_historique_ne_touche_pas_les_autres(db):
    db.save_exchange(1, "A", "a")
    db.start_chat(1)
    db.save_exchange(1, "B", "b")
    db.save_exchange(2, "C", "c")
    assert db.delete_all_chats(1) == 2
    assert db.list_chats(1) == [] and db.load_history(1, 10) == []
    assert count(db, "SELECT count(*) FROM conversations WHERE user_id = 1") == 0
    assert db.load_history(2, 10) == [("C", "c")]


def test_premiers_messages_simultanes_ne_creent_qu_une_conversation(db):
    barrier = threading.Barrier(4)

    def send(i):
        barrier.wait()
        db.save_exchange(7, f"message {i}", "ok")

    threads = [threading.Thread(target=send, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(db.list_chats(7)) == 1
    assert db.list_chats(7)[0]["messages"] == 4


# --- Mode « ne pas enregistrer » ---------------------------------------------
def test_mode_prive_ne_garde_rien_dans_les_conversations(db):
    db.save_exchange(1, "avant", "ok")
    db.set_private(1, True)
    assert db.private_active(1)
    db.save_private_exchange(1, "secret", "confidentiel")
    assert db.load_private_history(1, 10) == [("secret", "confidentiel")]
    assert db.load_history(1, 10) == [("avant", "ok")]  # l'historique n'a pas bougé
    assert count(db, "SELECT count(*) FROM conversations WHERE user_message = 'secret'") == 0
    assert count(db, "SELECT count(*) FROM chats WHERE user_id = 1") == 1


def test_le_contexte_prive_expire(db):
    db.set_private(1, True)
    db.save_private_exchange(1, "ancien", "x")
    db.save_private_exchange(1, "récent", "y")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE private_context SET created_at = now() - interval '31 minutes' "
            "WHERE user_message = 'ancien'"
        )
    assert db.load_private_history(1, 10) == [("récent", "y")]
    assert db.private_active(1)  # le mode reste actif : on n'enregistre jamais par surprise


def test_sortir_du_mode_prive_efface_son_contexte(db):
    db.set_private(1, True)
    db.save_private_exchange(1, "secret", "x")
    db.set_private(1, False)
    assert not db.private_active(1)
    assert count(db, "SELECT count(*) FROM private_context") == 0


@pytest.mark.parametrize("sortie", ["nouvelle", "reprise"])
def test_nouvelle_conversation_ou_reprise_sort_du_mode_prive(db, sortie):
    chat_id = db.save_exchange(1, "avant", "ok")
    db.set_private(1, True)
    db.save_private_exchange(1, "secret", "x")
    if sortie == "nouvelle":
        db.start_chat(1)
    else:
        db.open_chat(1, chat_id)
    assert not db.private_active(1)
    assert count(db, "SELECT count(*) FROM private_context") == 0


def test_le_mode_prive_est_propre_a_chaque_utilisateur(db):
    db.set_private(1, True)
    assert db.private_active(1) and not db.private_active(2)
    db.save_private_exchange(1, "secret", "x")
    assert db.load_private_history(2, 10) == []


# --- Purge -------------------------------------------------------------------
def test_purge_selon_le_role(db, make_owner, make_user):
    owner = make_owner()
    tester = make_user()
    old_owner = db.save_exchange(owner["id"], "vieux proprio", "r")
    db.start_chat(owner["id"])
    recent_owner = db.save_exchange(owner["id"], "récent proprio", "r")
    old_tester = db.save_exchange(tester["id"], "vieux testeur", "r")
    age_chat(db, old_owner, 91)  # > 90 jours
    age_chat(db, old_tester, 31)  # > 30 jours
    result = db.purge_history(90, 30)
    assert result["chats"] == 2
    remaining = {c["id"] for c in db.list_chats(owner["id"])}
    assert remaining == {recent_owner}
    assert db.list_chats(tester["id"]) == []


def test_purge_epargne_ce_qui_est_sous_le_seuil(db, make_owner, make_user):
    owner, tester = make_owner(), make_user()
    a = db.save_exchange(owner["id"], "a", "r")
    b = db.save_exchange(tester["id"], "b", "r")
    age_chat(db, a, 89)
    age_chat(db, b, 29)
    assert db.purge_history(90, 30)["chats"] == 0
    assert db.load_history(owner["id"], 5) == [("a", "r")]
    assert db.load_history(tester["id"], 5) == [("b", "r")]


def test_purge_zero_jour_signifie_ne_jamais_purger(db, make_owner, make_user):
    owner, tester = make_owner(), make_user()
    a = db.save_exchange(owner["id"], "a", "r")
    b = db.save_exchange(tester["id"], "b", "r")
    age_chat(db, a, 4000)
    age_chat(db, b, 4000)
    assert db.purge_history(0, 0)["chats"] == 0
    assert len(db.list_chats(owner["id"])) == 1 and len(db.list_chats(tester["id"])) == 1


def test_purge_supprime_aussi_le_contexte_prive_expire(db):
    db.set_private(1, True)
    db.save_private_exchange(1, "vieux", "x")
    db.save_private_exchange(1, "neuf", "y")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE private_context SET created_at = now() - interval '2 hours' "
            "WHERE user_message = 'vieux'"
        )
    assert db.purge_history(0, 0)["private"] == 1
    assert count(db, "SELECT count(*) FROM private_context") == 1


def test_purge_utilise_les_durees_de_l_environnement_par_defaut(db, make_owner, monkeypatch):
    owner = make_owner()
    chat_id = db.save_exchange(owner["id"], "a", "r")
    age_chat(db, chat_id, 10)
    monkeypatch.setattr(db, "RETENTION_DAYS_OWNER", 5)
    assert db.purge_history()["chats"] == 1


# --- Migration 006 sur des données existantes ---------------------------------
def test_migration_006_rattache_l_historique_existant_sans_perte():
    import migrate

    name = "fox_test_mig006_" + uuid.uuid4().hex[:8]
    url = make_database(name)
    try:
        apply_init_scripts(url)
        conn = psycopg2.connect(url)
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (user_id, user_message, bot_reply) VALUES "
                "(1, 'bonjour', 'salut'), (1, 'merci', 'de rien'), (2, 'autre', 'oui')"
            )
        conn.close()
        applied = migrate.run(url, DB_DIR / "migrations")
        assert "006_conversations_multiples.sql" in applied

        conn = psycopg2.connect(url)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM conversations")
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT count(*) FROM conversations WHERE chat_id IS NULL")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT user_id, title FROM chats ORDER BY user_id")
            assert cur.fetchall() == [(1, "Historique"), (2, "Historique")]
            cur.execute(
                "SELECT c.user_id, count(*) FROM conversations m "
                "JOIN chats c ON c.id = m.chat_id AND c.user_id = m.user_id GROUP BY c.user_id "
                "ORDER BY c.user_id"
            )
            assert cur.fetchall() == [(1, 2), (2, 1)]
        conn.close()

        assert migrate.run(url, DB_DIR / "migrations") == []  # relancer ne change rien
        conn = psycopg2.connect(url)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chats")
            assert cur.fetchone()[0] == 2
        conn.close()
    finally:
        drop_database(name)
