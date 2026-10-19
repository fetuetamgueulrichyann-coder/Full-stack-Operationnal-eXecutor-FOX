"""Création du propriétaire au démarrage : rattachement de l'historique sans perte."""
import pytest

TELEGRAM_ID = 987654321


@pytest.fixture
def boot(database_url, accounts, db):
    import bootstrap

    return bootstrap


def seed_legacy(db):
    """Données d'avant la phase A : elles portent l'identifiant Telegram."""
    db.save_exchange(TELEGRAM_ID, "bonjour", "salut")
    db.save_exchange(TELEGRAM_ID, "merci", "de rien")
    db.audit(TELEGRAM_ID, "executed", "creer_evenement", {})
    db.create_pending(TELEGRAM_ID, "creer_evenement", {}, "Créer", 10)


def count(db, table, user_id):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]


def test_historique_rattache_au_proprietaire_sans_perte(boot, db):
    seed_legacy(db)
    boot.bootstrap_owner(TELEGRAM_ID, "yann")
    with db.cursor() as cur:
        cur.execute("SELECT id, role, telegram_id FROM users")
        owner_id, role, telegram_id = cur.fetchone()
    assert (role, telegram_id) == ("owner", TELEGRAM_ID)
    assert count(db, "conversations", owner_id) == 2
    assert count(db, "pending_actions", owner_id) == 1
    assert count(db, "audit_log", owner_id) >= 1
    assert count(db, "conversations", TELEGRAM_ID) == 0  # plus rien sous l'ancien identifiant
    assert db.load_history(owner_id, 10) == [("bonjour", "salut"), ("merci", "de rien")]


def test_idempotent_deuxieme_lancement_ne_change_rien(boot, db):
    seed_legacy(db)
    boot.bootstrap_owner(TELEGRAM_ID, "yann")
    assert boot.bootstrap_owner(TELEGRAM_ID, "yann") == "propriétaire déjà présent"
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM users")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM conversations")
        assert cur.fetchone()[0] == 2


def test_mot_de_passe_applique_une_seule_fois(boot, accounts, db):
    boot.bootstrap_owner(TELEGRAM_ID, "yann")
    assert boot.bootstrap_owner(TELEGRAM_ID, "yann", "premier-mot-de-passe") == (
        "mot de passe du propriétaire défini"
    )
    # Un second mot de passe dans l'environnement ne remplace pas le premier.
    boot.bootstrap_owner(TELEGRAM_ID, "yann", "autre-mot-de-passe-long")
    assert accounts.login("yann", "premier-mot-de-passe", "10.1.1.1")[1]["role"] == "owner"


def test_mot_de_passe_trop_court_refuse_sans_creer_de_compte(boot, accounts, db):
    with pytest.raises(accounts.AuthError):
        boot.bootstrap_owner(TELEGRAM_ID, "yann", "court")
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM users")
        assert cur.fetchone()[0] == 0


def test_conflit_d_identifiant_ne_melange_pas_les_historiques(boot, accounts, db, make_user):
    """Un testeur (id interne 1) existe déjà et l'identifiant Telegram vaut 1 : rien n'est déplacé."""
    tester = make_user("testeur-existant")
    assert tester["id"] == 1
    db.save_exchange(1, "message du testeur", "réponse au testeur")
    boot.bootstrap_owner(1, "proprio")
    assert db.load_history(1, 10) == [("message du testeur", "réponse au testeur")]


def test_main_sans_identifiant_valide_ne_cree_rien(boot, db, monkeypatch):
    for value in ("", "0", "abc"):
        monkeypatch.setenv("ALLOWED_USER_ID", value)
        assert boot.main() == 0
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM users")
        assert cur.fetchone()[0] == 0


def test_main_cree_le_proprietaire_depuis_l_environnement(boot, db, monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_ID", str(TELEGRAM_ID))
    monkeypatch.setenv("OWNER_USERNAME", "yann")
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    assert boot.main() == 0
    with db.cursor() as cur:
        cur.execute("SELECT username, role, password_hash FROM users")
        assert cur.fetchone() == ("yann", "owner", None)
