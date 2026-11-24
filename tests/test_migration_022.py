"""Migration 022 (confidentialité) : sûreté, contraintes, index, clés étrangères, retour arrière et export."""
import re
import shutil
import subprocess
import uuid

import psycopg2
import pytest

from conftest import DB_DIR, SCRIPTS_DIR, apply_init_scripts, drop_database, make_database

INNER = SCRIPTS_DIR / "inner"
EXPORT = INNER / "privacy_022_export.psql"
ROLLBACK = INNER / "privacy_022_rollback.sql"
MIGRATION = DB_DIR / "migrations" / "022_confidentialite.sql"


@pytest.fixture
def migrate():
    import migrate as module

    return module


@pytest.fixture
def before(tmp_path, migrate):
    """Base réelle jusqu'à la migration 021, avec des données, prête à recevoir la 022."""
    name = "fox_test_022_" + uuid.uuid4().hex[:8]
    url = make_database(name)
    apply_init_scripts(url)
    old = tmp_path / "migrations"
    old.mkdir()
    for path in sorted((DB_DIR / "migrations").glob("*.sql")):
        if not path.name.startswith("022"):
            shutil.copy(path, old / path.name)
    migrate.run(url, old)
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO conversations (user_id, user_message, bot_reply) VALUES (7, 'bonjour', 'salut'), (7, 'merci', 'de rien'), (8, 'x', 'y')")
    conn.close()
    yield url
    drop_database(name)


def run_sql(url, sql, args=()):
    conn = psycopg2.connect(url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, args or None)  # sans paramètres : les « % » du texte restent littéraux
            return cur.fetchall() if cur.description else None
    finally:
        conn.close()


@pytest.fixture
def after(before, migrate):
    assert migrate.run(before, DB_DIR / "migrations") == ["022_confidentialite.sql"]
    return before


def test_la_migration_s_applique_sur_des_donnees_existantes_sans_les_toucher(before, migrate):
    snapshot = run_sql(before, "SELECT id, user_id, user_message, bot_reply, created_at FROM conversations ORDER BY id")
    chats = run_sql(before, "SELECT id, user_id, title FROM chats ORDER BY id")
    assert migrate.run(before, DB_DIR / "migrations") == ["022_confidentialite.sql"]
    assert run_sql(before, "SELECT id, user_id, user_message, bot_reply, created_at FROM conversations ORDER BY id") == snapshot
    assert run_sql(before, "SELECT id, user_id, title FROM chats ORDER BY id") == chats
    # anciens échanges : étiquette et moteur inconnus (NULL), traités comme sensibles
    assert run_sql(before, "SELECT count(*) FROM conversations WHERE data_classes IS NULL AND engine IS NULL")[0][0] == 3
    for table in ("privacy_settings", "data_grants", "held_messages"):
        assert run_sql(before, f"SELECT count(*) FROM {table}")[0][0] == 0  # noqa: S608
    assert run_sql(before, "SELECT name FROM schema_migrations WHERE version = 22") == [("022_confidentialite.sql",)]


def test_la_migration_n_est_pas_destructive_et_est_rejouable(after):
    import migrate

    sql = MIGRATION.read_text(encoding="utf-8")
    migrate.check_destructive("022", sql, allow=False)  # ne lève pas : aucune opération destructive
    run_sql(after, sql)  # rejouée telle quelle : IF NOT EXISTS partout, sans erreur ni perte
    run_sql(after, sql)
    assert run_sql(after, "SELECT count(*) FROM conversations")[0][0] == 3


def test_index_attendus(after):
    names = {r[0] for r in run_sql(after, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    assert {"idx_data_grants_lookup", "idx_data_grants_operation", "idx_data_grants_chat", "idx_data_grants_expires",
            "idx_held_messages_user", "idx_held_messages_expires"} <= names
    definition = run_sql(after, "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_data_grants_lookup'")[0][0]
    assert "WHERE (revoked_at IS NULL)" in definition


def test_cles_etrangeres_vers_chats_en_cascade_et_aucune_vers_users(after):
    fks = run_sql(after, """
        SELECT c.conrelid::regclass::text, c.confrelid::regclass::text, c.confdeltype
        FROM pg_constraint c WHERE c.contype = 'f' AND c.conrelid::regclass::text IN ('data_grants', 'held_messages', 'privacy_settings')
        ORDER BY 1""")
    assert fks == [("data_grants", "chats", "c"), ("held_messages", "chats", "c")]  # 'c' = CASCADE


def test_contraintes_attendues(after):
    names = {r[0] for r in run_sql(after, "SELECT conname FROM pg_constraint WHERE contype = 'c'")}
    assert {"privacy_settings_level_check", "privacy_settings_provider_check", "privacy_settings_notif_check",
            "data_grants_provider_check", "data_grants_category_check", "data_grants_scope_check",
            "data_grants_scope_target", "data_grants_file_rule", "data_grants_expiry", "data_grants_consumed_scope",
            "held_messages_expiry", "conversations_data_classes_check", "conversations_engine_check"} <= names


@pytest.mark.parametrize("sql", [
    "INSERT INTO conversations (user_id, user_message, bot_reply, data_classes) VALUES (1, 'a', 'b', ARRAY['voix'])",
    "INSERT INTO conversations (user_id, user_message, bot_reply, data_classes) VALUES (1, 'a', 'b', ARRAY['inconnue'])",
    "INSERT INTO conversations (user_id, user_message, bot_reply, engine) VALUES (1, 'a', 'b', 'openai')",
    "INSERT INTO data_grants (user_id, provider, category, scope, expires_at) VALUES (1, 'groq', 'texte_libre', 'message', now() + interval '1 minute')",  # sans opération
    "INSERT INTO data_grants (user_id, provider, category, scope, operation_id, expires_at) VALUES (1, 'groq', 'fichier', 'message', 'o', now() + interval '1 minute')",  # fichier sans fichier
    "INSERT INTO data_grants (user_id, provider, category, scope, operation_id, file_id, expires_at) VALUES (1, 'groq', 'texte_libre', 'message', 'o', gen_random_uuid(), now() + interval '1 minute')",  # fichier hors catégorie fichier
    "INSERT INTO data_grants (user_id, provider, category, scope, operation_id, expires_at) VALUES (1, 'groq', 'texte_libre', 'message', 'o', now() - interval '1 minute')",  # déjà expirée
    "INSERT INTO data_grants (user_id, provider, category, scope, operation_id, expires_at) VALUES (1, 'groq', 'texte_libre', 'message', 'o', now() + interval '13 hours')",  # plus de 12 h
])
def test_la_base_refuse_les_donnees_incoherentes(after, sql):
    with pytest.raises(psycopg2.errors.CheckViolation):
        run_sql(after, sql)


def test_une_conversation_inexistante_est_refusee_par_la_cle_etrangere(after):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        run_sql(after, "INSERT INTO data_grants (user_id, provider, category, scope, chat_id, expires_at) VALUES (1, 'groq', 'texte_libre', 'conversation', 999999, now() + interval '1 hour')")


# --- Retour arrière : export psql et script SQL séparés ---------------------------------------------------------------------
def test_l_export_est_un_fichier_psql_et_le_retour_arriere_du_sql_pur():
    export = EXPORT.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")
    # export : uniquement des meta-commandes psql (\copy, \set, \echo) et des commentaires, jamais de SQL qui modifie
    for line in export.splitlines():
        stripped = line.strip()
        assert not stripped or stripped.startswith(("--", "\\")), line
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT)\b", re.sub(r"--[^\n]*", "", export), re.I)
    assert sum(1 for line in export.splitlines() if line.startswith("\\copy")) == 4
    # retour arrière : aucune meta-commande psql (lisible par psycopg2 comme par psql)
    assert not any(line.lstrip().startswith("\\") for line in rollback.splitlines())
    assert "CASCADE" not in rollback.upper()  # pas de suppression en cascade aveugle


@pytest.fixture
def with_data(after):
    run_sql(after, "INSERT INTO chats (id, user_id) VALUES (900, 7)")
    run_sql(after, "INSERT INTO privacy_settings (user_id, level) VALUES (7, 'strict')")
    run_sql(after, "INSERT INTO data_grants (user_id, provider, category, scope, chat_id, expires_at) VALUES (7, 'groq', 'texte_libre', 'conversation', 900, now() + interval '1 hour')")
    run_sql(after, "UPDATE conversations SET data_classes = ARRAY['texte_libre'], engine = 'groq' WHERE id = (SELECT min(id) FROM conversations)")
    return after


def test_le_retour_arriere_refuse_de_supprimer_des_donnees_sans_confirmation(with_data):
    with pytest.raises(psycopg2.errors.RaiseException, match="refusé"):
        run_sql(with_data, ROLLBACK.read_text(encoding="utf-8"))
    for table in ("privacy_settings", "data_grants"):
        assert run_sql(with_data, f"SELECT count(*) FROM {table}")[0][0] == 1  # noqa: S608
    assert run_sql(with_data, "SELECT count(*) FROM conversations WHERE data_classes IS NOT NULL")[0][0] == 1


def test_une_fausse_confirmation_ne_suffit_pas(with_data):
    conn = psycopg2.connect(with_data)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET fox.rollback_confirmed = 'peut-etre'")
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute(ROLLBACK.read_text(encoding="utf-8"))
    finally:
        conn.close()
    assert run_sql(with_data, "SELECT count(*) FROM data_grants")[0][0] == 1


def test_sans_donnees_le_retour_arriere_passe_sans_confirmation_et_conserve_les_conversations(after):
    run_sql(after, ROLLBACK.read_text(encoding="utf-8"))
    tables = {r[0] for r in run_sql(after, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert not {"privacy_settings", "data_grants", "held_messages"} & tables
    assert run_sql(after, "SELECT count(*) FROM conversations")[0][0] == 3  # aucune conversation perdue
    columns = {r[0] for r in run_sql(after, "SELECT column_name FROM information_schema.columns WHERE table_name = 'conversations'")}
    assert not {"data_classes", "engine"} & columns
    assert run_sql(after, "SELECT count(*) FROM schema_migrations WHERE version = 22")[0][0] == 0


def test_avec_confirmation_le_retour_arriere_supprime_et_la_migration_se_rejoue(with_data, migrate):
    conn = psycopg2.connect(with_data)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET fox.rollback_confirmed = 'oui'")
            cur.execute(ROLLBACK.read_text(encoding="utf-8"))
    finally:
        conn.close()
    assert run_sql(with_data, "SELECT count(*) FROM conversations")[0][0] == 3
    assert migrate.run(with_data, DB_DIR / "migrations") == ["022_confidentialite.sql"]  # aller-retour complet
    assert run_sql(with_data, "SELECT count(*) FROM data_grants")[0][0] == 0


psql_needed = pytest.mark.skipif(not shutil.which("psql"), reason="psql absent de cet environnement")


def psql(url, *args):
    return subprocess.run(["psql", url, "-v", "ON_ERROR_STOP=1", "-X", "-q", *args], capture_output=True, text=True, timeout=60)


@psql_needed
def test_l_export_psql_ecrit_quatre_fichiers_avant_le_retour_arriere(with_data, tmp_path):
    script = tmp_path / "export.psql"
    script.write_text(EXPORT.read_text(encoding="utf-8").replace("/backups/", str(tmp_path) + "/"), encoding="utf-8")
    result = psql(with_data, "-f", str(script))
    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in tmp_path.glob("*_022.csv"))
    assert names == ["conversations_classes_022.csv", "data_grants_022.csv", "held_messages_022.csv", "privacy_settings_022.csv"]
    assert "strict" in (tmp_path / "privacy_settings_022.csv").read_text(encoding="utf-8")
    assert "texte_libre" in (tmp_path / "data_grants_022.csv").read_text(encoding="utf-8")
    classes = (tmp_path / "conversations_classes_022.csv").read_text(encoding="utf-8")
    assert "groq" in classes and classes.count("\n") == 2  # en-tête + 1 échange étiqueté
    # l'export ne modifie rien
    assert run_sql(with_data, "SELECT count(*) FROM data_grants")[0][0] == 1


@psql_needed
def test_la_confirmation_par_pgoptions_comme_dans_la_procedure_documentee(with_data):
    """La doc donne la confirmation par PGOPTIONS (sans guillemets imbriqués, fragiles sous PowerShell)."""
    import os

    env = {**os.environ, "PGOPTIONS": "-c fox.rollback_confirmed=oui"}
    done = subprocess.run(["psql", with_data, "-v", "ON_ERROR_STOP=1", "-X", "-q", "--single-transaction", "-f", str(ROLLBACK)],
                          capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode == 0, done.stderr
    assert run_sql(with_data, "SELECT to_regclass('data_grants')")[0][0] is None


@psql_needed
def test_le_retour_arriere_via_psql_refuse_puis_accepte_avec_confirmation(with_data):
    refused = psql(with_data, "--single-transaction", "-f", str(ROLLBACK))
    assert refused.returncode != 0 and "refusé" in refused.stderr
    assert run_sql(with_data, "SELECT count(*) FROM data_grants")[0][0] == 1  # rien n'a été supprimé
    done = psql(with_data, "--single-transaction", "-c", "SET fox.rollback_confirmed = 'oui'", "-f", str(ROLLBACK))
    assert done.returncode == 0, done.stderr
    assert run_sql(with_data, "SELECT to_regclass('data_grants')")[0][0] is None
    assert run_sql(with_data, "SELECT count(*) FROM conversations")[0][0] == 3
