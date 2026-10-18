"""Migrations : conservation des données, ordre, sommes de contrôle, opérations destructives."""
import uuid

import psycopg2
import pytest

from conftest import ADMIN_URL, apply_init_scripts, drop_database, make_database


@pytest.fixture
def migrate():
    import migrate as module

    return module


@pytest.fixture
def fresh_db():
    """Base temporaire avec le schéma de départ (db/init) et quelques données."""
    name = "fox_test_mig_" + uuid.uuid4().hex[:8]
    url = make_database(name)
    apply_init_scripts(url)
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (user_id, user_message, bot_reply) VALUES "
            "(1, 'bonjour', 'salut'), (1, 'merci', 'de rien'), (2, 'autre', 'oui')"
        )
        cur.execute("INSERT INTO api_usage (day, calls, tokens) VALUES (current_date, 7, 700)")
    conn.close()
    yield url
    drop_database(name)


def write(directory, name, sql):
    (directory / name).write_text(sql, encoding="utf-8")


def scalar(url, query):
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute(query)
        value = cur.fetchone()[0]
    conn.close()
    return value


def test_aucune_migration_ne_touche_aux_donnees(migrate, fresh_db, tmp_path):
    assert migrate.run(fresh_db, tmp_path) == []
    assert scalar(fresh_db, "SELECT count(*) FROM conversations") == 3
    assert scalar(fresh_db, "SELECT calls FROM api_usage") == 7


def test_ajout_de_table_conserve_les_donnees_existantes(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_ajout_notes.sql", "CREATE TABLE notes (id serial PRIMARY KEY, texte text);")
    write(
        tmp_path,
        "005_colonne.sql",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS titre text;",
    )
    assert migrate.run(fresh_db, tmp_path) == ["004_ajout_notes.sql", "005_colonne.sql"]
    assert scalar(fresh_db, "SELECT count(*) FROM conversations") == 3
    assert scalar(fresh_db, "SELECT count(*) FROM schema_migrations") == 2


def test_relancer_n_applique_rien_de_plus(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_ajout_notes.sql", "CREATE TABLE notes (id serial PRIMARY KEY);")
    assert len(migrate.run(fresh_db, tmp_path)) == 1
    assert migrate.run(fresh_db, tmp_path) == []
    done, pending = migrate.status(fresh_db, tmp_path)
    assert done == ["004_ajout_notes.sql"] and pending == []


def test_migration_modifiee_apres_application_est_refusee(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_ajout_notes.sql", "CREATE TABLE notes (id serial PRIMARY KEY);")
    migrate.run(fresh_db, tmp_path)
    write(tmp_path, "004_ajout_notes.sql", "CREATE TABLE notes (id serial PRIMARY KEY, x int);")
    with pytest.raises(migrate.MigrationError, match="modifiée"):
        migrate.run(fresh_db, tmp_path)


def test_migration_en_echec_ne_laisse_aucune_trace(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_ok.sql", "CREATE TABLE ok_table (id int);")
    write(
        tmp_path,
        "005_echec.sql",
        "CREATE TABLE a_creer (id int); SELECT 1/0;",
    )
    with pytest.raises(migrate.MigrationError, match="005_echec"):
        migrate.run(fresh_db, tmp_path)
    # La première est appliquée, la seconde est entièrement annulée.
    assert scalar(fresh_db, "SELECT count(*) FROM schema_migrations") == 1
    assert scalar(fresh_db, "SELECT to_regclass('a_creer') IS NULL") is True
    assert scalar(fresh_db, "SELECT count(*) FROM conversations") == 3


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE conversations;",
        "ALTER TABLE conversations DROP COLUMN bot_reply;",
        "TRUNCATE conversations;",
        "DELETE FROM conversations;",
        "ALTER TABLE conversations ALTER COLUMN user_id TYPE integer;",
    ],
)
def test_operation_destructive_refusee_et_rien_n_est_applique(migrate, fresh_db, tmp_path, sql):
    write(tmp_path, "004_inoffensive.sql", "CREATE TABLE inoffensive (id int);")
    write(tmp_path, "005_destructive.sql", sql)
    with pytest.raises(migrate.MigrationError, match="destructive"):
        migrate.run(fresh_db, tmp_path)
    # Rien n'est appliqué, même la migration inoffensive qui précède : pas d'état partiel.
    assert scalar(fresh_db, "SELECT count(*) FROM schema_migrations") == 0
    assert scalar(fresh_db, "SELECT count(*) FROM conversations") == 3


def test_destructive_exige_marqueur_et_autorisation(migrate, fresh_db, tmp_path):
    sql = "-- destructive-approved: test\nDELETE FROM api_usage;"
    write(tmp_path, "004_nettoyage.sql", sql)
    # Marqueur présent mais autorisation d'environnement absente : refusé.
    with pytest.raises(migrate.MigrationError):
        migrate.run(fresh_db, tmp_path, allow_destructive=False)
    # Autorisation sans marqueur dans le fichier : refusé aussi.
    write(tmp_path, "004_nettoyage.sql", "DELETE FROM api_usage;")
    with pytest.raises(migrate.MigrationError):
        migrate.run(fresh_db, tmp_path, allow_destructive=True)
    # Les deux réunis : appliqué.
    write(tmp_path, "004_nettoyage.sql", sql)
    assert migrate.run(fresh_db, tmp_path, allow_destructive=True) == ["004_nettoyage.sql"]


def test_un_commentaire_ne_declenche_pas_la_detection(migrate, fresh_db, tmp_path):
    write(
        tmp_path,
        "004_commentaire.sql",
        "-- on ne fait pas de DROP TABLE ici\nCREATE TABLE sans_danger (id int);",
    )
    assert migrate.run(fresh_db, tmp_path) == ["004_commentaire.sql"]


def test_numerotation_invalide(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_a.sql", "SELECT 1;")
    write(tmp_path, "006_c.sql", "SELECT 1;")
    with pytest.raises(migrate.MigrationError, match="discontinue"):
        migrate.run(fresh_db, tmp_path)


def test_numero_en_double(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_a.sql", "SELECT 1;")
    write(tmp_path, "004_b.sql", "SELECT 1;")
    with pytest.raises(migrate.MigrationError, match="même numéro"):
        migrate.run(fresh_db, tmp_path)


def test_nom_invalide(migrate, fresh_db, tmp_path):
    write(tmp_path, "ajout.sql", "SELECT 1;")
    with pytest.raises(migrate.MigrationError, match="nom de migration invalide"):
        migrate.run(fresh_db, tmp_path)


def test_schema_de_base_absent_est_signale(migrate, tmp_path):
    name = "fox_test_mig_" + uuid.uuid4().hex[:8]
    url = make_database(name)  # base vide : db/init n'a pas été appliqué
    try:
        with pytest.raises(migrate.MigrationError, match="schéma de base incomplet"):
            migrate.run(url, tmp_path)
    finally:
        drop_database(name)


def test_migration_appliquee_dont_le_fichier_disparait(migrate, fresh_db, tmp_path):
    write(tmp_path, "004_a.sql", "CREATE TABLE a (id int);")
    migrate.run(fresh_db, tmp_path)
    (tmp_path / "004_a.sql").unlink()
    with pytest.raises(migrate.MigrationError, match="fichier absent"):
        migrate.run(fresh_db, tmp_path)


def test_les_migrations_reelles_du_depot_s_appliquent(migrate, fresh_db):
    """Les fichiers de db/migrations du dépôt doivent s'appliquer sur une base existante."""
    from conftest import DB_DIR

    migrate.run(fresh_db, DB_DIR / "migrations")
    assert scalar(fresh_db, "SELECT count(*) FROM conversations") == 3
