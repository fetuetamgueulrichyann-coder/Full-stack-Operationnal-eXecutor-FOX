"""Scripts de sauvegarde et de restauration (partie exécutée dans le conteneur de la base)."""
import os
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlsplit

import psycopg2
import pytest

from conftest import ADMIN_URL, DB_DIR, ROOT, drop_database, make_database

INNER = ROOT / "scripts" / "inner"

pytestmark = pytest.mark.skipif(
    not (shutil.which("pg_dump") and shutil.which("pg_restore")),
    reason="pg_dump/pg_restore absents de cet environnement",
)


@pytest.fixture
def source_db():
    name = "fox_test_bak_" + uuid.uuid4().hex[:8]
    url = make_database(name)
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE precieux (id int, valeur text)")
        cur.execute("INSERT INTO precieux VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    conn.close()
    yield name, url
    drop_database(name)


def env_for(name, backup_dir, **extra):
    parts = urlsplit(ADMIN_URL)
    env = dict(os.environ)
    env.update(
        POSTGRES_USER=parts.username or "postgres",
        POSTGRES_DB=name,
        PGHOST=parts.hostname or "localhost",
        PGPORT=str(parts.port or 5432),
        BACKUP_DIR=str(backup_dir),
    )
    if parts.password:
        env["PGPASSWORD"] = parts.password
    env.update(extra)
    return env


def run(script, env, *args):
    return subprocess.run(
        ["sh", str(INNER / script), *args], env=env, capture_output=True, text=True
    )


def count(url):
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM precieux")
        value = cur.fetchone()[0]
    conn.close()
    return value


def test_sauvegarde_puis_restauration_apres_perte_de_donnees(source_db, tmp_path):
    name, url = source_db
    env = env_for(name, tmp_path)
    result = run("db_backup.sh", env)
    assert result.returncode == 0, result.stderr
    dumps = list(tmp_path.glob("fox_*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_size > 0

    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM precieux")
    conn.close()
    assert count(url) == 0

    result = run("db_restore.sh", env, dumps[0].name)
    assert result.returncode == 0, result.stderr
    assert count(url) == 3


def test_rotation_conserve_les_plus_recentes(source_db, tmp_path):
    name, _ = source_db
    env = env_for(name, tmp_path, KEEP="2")
    for _ in range(3):
        assert run("db_backup.sh", env).returncode == 0
        time.sleep(1.1)  # le nom contient l'heure à la seconde
    assert len(list(tmp_path.glob("fox_*.dump"))) == 2


def test_la_sauvegarde_ne_modifie_pas_la_base(source_db, tmp_path):
    name, url = source_db
    run("db_backup.sh", env_for(name, tmp_path))
    assert count(url) == 3


def test_restauration_d_un_fichier_inexistant_echoue_proprement(source_db, tmp_path):
    name, url = source_db
    result = run("db_restore.sh", env_for(name, tmp_path), "absent.dump")
    assert result.returncode != 0 and "introuvable" in result.stderr
    assert count(url) == 3  # rien n'a été touché


def test_restauration_n_accepte_pas_de_chemin_hors_du_dossier(source_db, tmp_path):
    """Seul le nom du fichier compte : un chemin '../..' ne sort pas du dossier de sauvegardes."""
    name, _ = source_db
    result = run("db_restore.sh", env_for(name, tmp_path), "../../etc/passwd")
    assert result.returncode != 0
