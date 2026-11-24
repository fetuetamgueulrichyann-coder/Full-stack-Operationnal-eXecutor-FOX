"""Réglages, autorisations et messages retenus (base de données)."""
import threading
import uuid

import pytest

import privacy

USER, OTHER = 501, 502
store = None  # module chargé par la fixture ci-dessous (il exige la base temporaire)


@pytest.fixture(autouse=True)
def _charge_le_store(db):
    global store
    import privacy_store

    store = privacy_store


@pytest.fixture
def chat(db):
    return db.save_exchange(USER, "bonjour", "salut")


@pytest.fixture
def file_id(db):
    return db.add_file(USER, "plan.pdf", "application/pdf", "pdf", b"%PDF-1.4", 10_000_000)


def op():
    return uuid.uuid4().hex


def rows(db, query, args=()):
    with db.cursor() as cur:
        cur.execute(query, args)
        return cur.fetchall() if cur.description else []


# --- Réglages ------------------------------------------------------------------------------------
def test_sans_ligne_protege_sans_consentement(db):
    s = store.get_settings(USER)
    assert s["level"] == "protege" and s["consented"] is False and s["notification_detail"] == "discret"
    assert rows(db, "SELECT count(*) FROM privacy_settings")[0][0] == 0  # lire ne crée rien


def test_reglages_valides_et_refus_des_valeurs_invalides(db):
    assert store.save_settings(USER, level="strict")["level"] == "strict"
    assert store.save_settings(USER, preferred_provider="groq", notification_detail="complet")["preferred_provider"] == "groq"
    for bad in ({"level": "ouvert"}, {"preferred_provider": "openai"}, {"notification_detail": "x"}, {"inconnu": 1}):
        with pytest.raises(store.GrantError):
            store.save_settings(USER, **bad)
    assert store.get_settings(USER)["level"] == "strict"


def test_le_consentement_est_enregistre_avec_sa_version(db):
    s = store.record_consent(USER, 1)
    assert s["consented"] and s["consent_version"] == 1 and s["consent_at"] is not None
    assert s["level"] == "protege"  # le consentement ne change pas le niveau


def test_la_base_refuse_un_niveau_invalide(db):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        rows(db, "INSERT INTO privacy_settings (user_id, level) VALUES (1, 'ouvert')")


# --- Création d'autorisations ----------------------------------------------------------------------
def test_expiration_calculee_par_le_serveur_et_plafonnee(db, chat):
    g1 = store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    g2 = store.create_grant(USER, "groq", "texte_libre", "message", operation_id=op())
    (c1, e1), (c2, e2) = [rows(db, "SELECT created_at, expires_at FROM data_grants WHERE id = %s", (g,))[0] for g in (g1, g2)]
    assert e1 - c1 == privacy.MAX_CONVERSATION_GRANT
    assert e2 - c2 == privacy.MESSAGE_GRANT_TTL


def test_la_base_refuse_une_autorisation_de_plus_de_12_heures(db, chat):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        rows(db, "INSERT INTO data_grants (user_id, provider, category, scope, chat_id, expires_at) "
                 "VALUES (%s, 'groq', 'texte_libre', 'conversation', %s, now() + interval '13 hours')", (USER, chat))


@pytest.mark.parametrize("kwargs", [
    dict(provider="openai", category="texte_libre", scope="message"),
    dict(provider="groq", category="secret", scope="message"),
    dict(provider="groq", category="texte_libre", scope="toujours"),
    dict(provider="groq", category="texte_libre", scope="message"),  # sans opération
    dict(provider="groq", category="texte_libre", scope="conversation"),  # sans conversation
    dict(provider="groq", category="fichier", scope="message"),  # fichier sans fichier
])
def test_demandes_invalides_refusees(kwargs):
    with pytest.raises(store.GrantError):
        store.create_grant(USER, kwargs.pop("provider"), kwargs.pop("category"), kwargs.pop("scope"), **kwargs)


def test_une_categorie_hors_fichier_n_accepte_pas_de_fichier(file_id):
    with pytest.raises(store.GrantError):
        store.create_grant(USER, "groq", "texte_libre", "message", operation_id=op(), file_id=file_id)


def test_une_autorisation_ne_peut_pas_viser_la_conversation_ou_le_fichier_d_un_autre(db, chat, file_id):
    with pytest.raises(store.GrantError):
        store.create_grant(OTHER, "groq", "texte_libre", "conversation", chat_id=chat)
    with pytest.raises(store.GrantError):
        store.create_grant(OTHER, "groq", "fichier", "message", operation_id=op(), file_id=file_id)


# --- Usage unique par opération ----------------------------------------------------------------------
def test_toutes_les_manches_d_une_operation_partagent_l_autorisation(db):
    operation = op()
    store.create_grant(USER, "groq", "personnel", "message", operation_id=operation)
    ids = {store.use_grant(USER, "groq", "personnel", operation_id=operation) for _ in range(5)}  # 5 tours d'outils
    assert len(ids) == 1 and None not in ids
    assert rows(db, "SELECT consumed_at IS NOT NULL FROM data_grants")[0][0] is True


def test_une_seconde_operation_ne_peut_pas_utiliser_l_autorisation(db):
    first, second = op(), op()
    store.create_grant(USER, "groq", "personnel", "message", operation_id=first)
    assert store.use_grant(USER, "groq", "personnel", operation_id=first) is not None
    assert store.use_grant(USER, "groq", "personnel", operation_id=second) is None
    assert store.use_grant(USER, "groq", "personnel", operation_id=first) is not None  # la première continue


def test_une_autorisation_ne_couvre_que_son_fournisseur_sa_categorie_et_son_utilisateur(db):
    operation = op()
    store.create_grant(USER, "groq", "personnel", "message", operation_id=operation)
    assert store.use_grant(USER, "gemini", "personnel", operation_id=operation) is None
    assert store.use_grant(USER, "groq", "metier", operation_id=operation) is None
    assert store.use_grant(USER, "groq", "texte_libre", operation_id=operation) is None
    assert store.use_grant(OTHER, "groq", "personnel", operation_id=operation) is None


def test_deux_operations_simultanees_une_seule_gagne(db):
    store.create_grant(USER, "groq", "personnel", "message", operation_id="op-A")
    results = []

    def attempt(name):
        results.append((name, store.use_grant(USER, "groq", "personnel", operation_id=name)))

    threads = [threading.Thread(target=attempt, args=(n,)) for n in ("op-A", "op-B", "op-C", "op-D")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    winners = [name for name, grant in results if grant is not None]
    assert winners == ["op-A"]


def test_autorisation_de_conversation_pour_cette_conversation_seulement(db, chat):
    store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    assert store.use_grant(USER, "groq", "texte_libre", operation_id=op(), chat_id=chat) is not None
    assert store.use_grant(USER, "groq", "texte_libre", operation_id=op(), chat_id=chat) is not None  # reste valable
    assert rows(db, "SELECT consumed_at FROM data_grants")[0][0] is None  # une autorisation de conversation n'est jamais « consommée »
    assert store.use_grant(USER, "groq", "texte_libre", operation_id=op(), chat_id=chat + 999) is None
    assert store.use_grant(USER, "groq", "texte_libre", operation_id=op(), chat_id=None) is None


def test_une_autorisation_de_texte_ne_couvre_pas_un_fichier(db, chat, file_id):
    store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    assert store.use_grant(USER, "groq", "fichier", operation_id=op(), chat_id=chat, file_id=file_id) is None


def test_autorisation_de_fichier_par_fichier(db, chat, file_id):
    second = db.add_file(USER, "autre.pdf", "application/pdf", "pdf", b"%PDF-1.4", 10_000_000)
    operation = op()
    store.create_grant(USER, "groq", "fichier", "message", operation_id=operation, file_id=file_id)
    assert store.use_grant(USER, "groq", "fichier", operation_id=operation, file_id=file_id) is not None
    assert store.use_grant(USER, "groq", "fichier", operation_id=operation, file_id=second) is None
    assert store.use_grant(USER, "groq", "fichier", operation_id=operation) is None  # sans fichier : non
    # « ce fichier, cette conversation » : valable pour ce fichier seulement
    store.create_grant(USER, "groq", "fichier", "conversation", chat_id=chat, file_id=second)
    assert store.use_grant(USER, "groq", "fichier", operation_id=op(), chat_id=chat, file_id=second) is not None
    assert store.use_grant(USER, "groq", "fichier", operation_id=op(), chat_id=chat, file_id=file_id) is None


def test_expiration_et_revocation_prennent_effet_a_l_appel_suivant(db, chat):
    operation = op()
    g = store.create_grant(USER, "groq", "personnel", "message", operation_id=operation)
    assert store.use_grant(USER, "groq", "personnel", operation_id=operation) == g
    assert store.revoke(USER, g)
    assert store.use_grant(USER, "groq", "personnel", operation_id=operation) is None  # tour suivant : refusé
    assert not store.revoke(USER, g)  # déjà révoquée
    g2 = store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    rows(db, "UPDATE data_grants SET created_at = now() - interval '13 hours', expires_at = now() - interval '1 hour' WHERE id = %s", (g2,))
    assert store.use_grant(USER, "groq", "texte_libre", operation_id=op(), chat_id=chat) is None


def test_on_ne_revoque_pas_l_autorisation_d_un_autre(db):
    g = store.create_grant(USER, "groq", "personnel", "message", operation_id=op())
    assert not store.revoke(OTHER, g)
    assert store.revoke_all(OTHER) == 0
    assert store.revoke_all(USER) == 1


def test_covered_categories_ne_consomme_rien(db, chat):
    operation = op()
    store.create_grant(USER, "groq", "personnel", "message", operation_id=operation)
    store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    assert store.covered_categories(USER, "groq", operation_id=operation, chat_id=chat) == {"personnel", "texte_libre"}
    assert rows(db, "SELECT count(*) FROM data_grants WHERE consumed_at IS NOT NULL")[0][0] == 0


# --- Cohérence et nettoyage des autorisations liées aux conversations ----------------------------------
def count(db, table):
    return rows(db, f"SELECT count(*) FROM {table}")[0][0]  # noqa: S608


def test_supprimer_une_conversation_supprime_ses_autorisations(db, chat):
    other = db.save_exchange(USER, "garde", "ok", chat_id=db.start_chat(USER))
    store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    keep = store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=other)
    assert db.delete_chat(USER, chat)
    assert [r[0] for r in rows(db, "SELECT id FROM data_grants")] == [keep]


def test_supprimer_tout_l_historique_supprime_les_autorisations_de_conversation(db, chat):
    store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    store.create_grant(USER, "groq", "personnel", "message", operation_id=op())  # sans conversation : reste jusqu'à expiration
    store.hold_message(USER, chat, op(), {"text": "x"})
    assert db.delete_all_chats(USER) == 1
    assert count(db, "held_messages") == 0
    assert [r[0] for r in rows(db, "SELECT category FROM data_grants")] == ["personnel"]


def test_la_purge_de_l_historique_ancien_supprime_aussi_les_autorisations(db, chat):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, role) VALUES (%s, 'tester501', 'tester')", (USER,))
    store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
    rows(db, "UPDATE chats SET updated_at = now() - interval '40 days' WHERE id = %s", (chat,))
    assert db.purge_history(owner_days=0, tester_days=30)["chats"] == 1
    assert count(db, "data_grants") == 0


def test_purge_expired_nettoie_sans_toucher_au_valide(db, chat, file_id):
    valid = store.create_grant(USER, "groq", "personnel", "message", operation_id=op())
    old = store.create_grant(USER, "groq", "metier", "message", operation_id=op())
    rows(db, "UPDATE data_grants SET created_at = now() - interval '2 days 1 hour', expires_at = now() - interval '2 days' WHERE id = %s", (old,))
    recent = store.create_grant(USER, "groq", "texte_libre", "message", operation_id=op())
    rows(db, "UPDATE data_grants SET created_at = now() - interval '2 hours', expires_at = now() - interval '1 hour' WHERE id = %s", (recent,))
    orphan = store.create_grant(USER, "groq", "fichier", "message", operation_id=op(), file_id=file_id)
    rows(db, "DELETE FROM files")
    store.hold_message(USER, chat, "vieux", {"text": "x"})
    rows(db, "UPDATE held_messages SET created_at = now() - interval '1 hour', expires_at = now() - interval '50 minutes'")
    result = store.purge_expired()
    assert result == {"held": 1, "grants": 1, "orphan_file_grants": 1}
    assert {r[0] for r in rows(db, "SELECT id FROM data_grants")} == {valid, recent}
    assert orphan not in {r[0] for r in rows(db, "SELECT id FROM data_grants")}


def test_forget_user_efface_tout_de_cet_utilisateur_seulement(db, chat):
    store.save_settings(USER, level="strict")
    store.save_settings(OTHER, level="standard")
    store.create_grant(USER, "groq", "personnel", "message", operation_id=op())
    store.create_grant(OTHER, "groq", "personnel", "message", operation_id=op())
    store.hold_message(USER, chat, op(), {"text": "x"})
    store.forget_user(USER)
    assert [r[0] for r in rows(db, "SELECT user_id FROM data_grants")] == [OTHER]
    assert [r[0] for r in rows(db, "SELECT user_id FROM privacy_settings")] == [OTHER]
    assert count(db, "held_messages") == 0


# --- Messages retenus ----------------------------------------------------------------------------------
def test_message_retenu_repris_une_seule_fois(db, chat):
    operation = op()
    store.hold_message(USER, chat, operation, {"text": "bonjour", "files": []})
    assert store.take_held(OTHER, operation) is None  # un autre utilisateur ne le reprend pas
    got = store.take_held(USER, operation)
    assert got == {"payload": {"text": "bonjour", "files": []}, "chat_id": chat}
    assert store.take_held(USER, operation) is None
    assert count(db, "held_messages") == 0


def test_message_retenu_expire_et_annulation(db, chat):
    operation = op()
    store.hold_message(USER, chat, operation, {"text": "x"})
    rows(db, "UPDATE held_messages SET created_at = now() - interval '11 minutes', expires_at = now() - interval '1 minute'")
    assert store.take_held(USER, operation) is None
    store.hold_message(USER, chat, "a", {"text": "x"})
    store.hold_message(USER, chat, "b", {"text": "x"})
    assert store.cancel_held(USER, "a") == 1
    assert store.cancel_held(USER) == 2  # « b » et le message expiré resté en base
    assert count(db, "held_messages") == 0


def test_la_base_refuse_un_message_retenu_plus_de_10_minutes(db):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        rows(db, "INSERT INTO held_messages (user_id, operation_id, payload, expires_at) VALUES (1, 'x', '{}', now() + interval '11 minutes')")
