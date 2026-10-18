"""Migrations de base de données numérotées, sans perte de données.

Principes :
- les fichiers `NNN_description.sql` du dossier de migrations s'appliquent dans l'ordre ;
- chaque migration s'exécute dans sa propre transaction et n'est enregistrée qu'une fois
  réussie (table `schema_migrations`) ;
- une migration déjà appliquée ne doit jamais être modifiée (somme de contrôle vérifiée) ;
- une migration destructive (suppression de table ou de colonne, TRUNCATE, DELETE...)
  est refusée, sauf approbation explicite (voir `check_destructive`) ;
- un verrou empêche deux processus de migrer en même temps ;
- le schéma de base (`db/init`) reste la référence de départ : il est créé au premier
  démarrage de la base, puis seules les migrations numérotées le font évoluer.

Usage :
    python migrate.py            applique les migrations en attente
    python migrate.py --status   liste les migrations appliquées et en attente
"""
import hashlib
import logging
import os
import re
import sys
from pathlib import Path

import psycopg2

log = logging.getLogger("migrate")

MIGRATIONS_DIR = os.environ.get("MIGRATIONS_DIR", "/migrations")
LOCK_KEY = 7_303_405  # identifiant arbitraire du verrou consultatif
NAME_PATTERN = re.compile(r"^(\d{3,})_[a-z0-9_]+\.sql$")
BASELINE_TABLES = ("conversations", "api_usage", "pending_actions", "audit_log")
APPROVAL_MARKER = "-- destructive-approved:"

DESTRUCTIVE_PATTERNS = [
    r"\bDROP\s+(TABLE|COLUMN|SCHEMA|DATABASE|TYPE)\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\s+FROM\b",
    r"\bALTER\s+TABLE\b[^;]*\bDROP\b",
    r"\bALTER\s+COLUMN\b[^;]*\bTYPE\b",
]


class MigrationError(Exception):
    """Erreur de migration : rien n'est appliqué au-delà du point d'échec."""


def checksum(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_comments(sql):
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"/\*.*?\*/", "", sql, flags=re.S)


def check_destructive(name, sql, allow):
    """Refuse une migration destructive sans approbation explicite.

    L'approbation demande deux choses : une ligne `-- destructive-approved: <raison>` dans
    le fichier ET la variable d'environnement ALLOW_DESTRUCTIVE_MIGRATIONS=1.
    """
    body = strip_comments(sql)
    found = [p for p in DESTRUCTIVE_PATTERNS if re.search(p, body, re.I)]
    if not found:
        return
    approved_in_file = any(
        line.strip().lower().startswith(APPROVAL_MARKER) for line in sql.splitlines()
    )
    if approved_in_file and allow:
        log.warning("Migration destructive approuvée : %s", name)
        return
    raise MigrationError(
        f"{name} contient une opération destructive. Elle n'est pas appliquée. "
        f"Sauvegarde la base, puis ajoute la ligne « {APPROVAL_MARKER} raison » au fichier "
        "et lance avec ALLOW_DESTRUCTIVE_MIGRATIONS=1."
    )


def load_files(directory):
    """Retourne [(version, nom, sql)] triés. Refuse les noms invalides, doublons et trous."""
    path = Path(directory)
    if not path.is_dir():
        raise MigrationError(f"dossier de migrations introuvable : {directory}")

    items = []
    for file in sorted(path.glob("*.sql")):
        match = NAME_PATTERN.match(file.name)
        if not match:
            raise MigrationError(
                f"nom de migration invalide : {file.name} (attendu : 004_description.sql)"
            )
        items.append((int(match.group(1)), file.name, file.read_text(encoding="utf-8")))

    versions = [v for v, _, _ in items]
    if len(versions) != len(set(versions)):
        raise MigrationError("deux migrations portent le même numéro")
    items.sort(key=lambda item: item[0])
    for previous, current in zip(items, items[1:]):
        if current[0] != previous[0] + 1:
            raise MigrationError(
                f"numérotation discontinue entre {previous[1]} et {current[1]}"
            )
    return items


def ensure_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            checksum   TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def check_baseline(cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    existing = {row[0] for row in cur.fetchall()}
    missing = [t for t in BASELINE_TABLES if t not in existing]
    if missing:
        raise MigrationError(
            "schéma de base incomplet (tables absentes : " + ", ".join(missing) + "). "
            "Les scripts de db/init créent le schéma au premier démarrage de la base."
        )


def applied(cur):
    cur.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def plan(conn, directory):
    """Retourne (déjà appliquées, en attente) après vérification des sommes de contrôle."""
    files = load_files(directory)
    with conn.cursor() as cur:
        ensure_table(cur)
        check_baseline(cur)
        done = applied(cur)
    conn.commit()

    known = {v for v, _, _ in files}
    for version, (name, _) in done.items():
        if version not in known:
            raise MigrationError(f"migration appliquée mais fichier absent : {name}")

    pending = []
    for version, name, sql in files:
        if version in done:
            if done[version][1] != checksum(sql):
                raise MigrationError(
                    f"{name} a été modifiée après son application : "
                    "crée une nouvelle migration au lieu de modifier l'ancienne."
                )
        else:
            pending.append((version, name, sql))
    return [name for _, name, _ in files if name not in {p[1] for p in pending}], pending


def run(database_url, directory=None, allow_destructive=None):
    """Applique les migrations en attente. Retourne la liste des noms appliqués."""
    directory = directory or MIGRATIONS_DIR
    if allow_destructive is None:
        allow_destructive = os.environ.get("ALLOW_DESTRUCTIVE_MIGRATIONS") == "1"

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        conn.commit()
        try:
            _, pending = plan(conn, directory)
            # Vérifie tout avant de rien appliquer : un refus ne laisse pas d'état partiel.
            for _, name, sql in pending:
                check_destructive(name, sql, allow_destructive)

            applied_names = []
            for version, name, sql in pending:
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                        cur.execute(
                            "INSERT INTO schema_migrations (version, name, checksum) "
                            "VALUES (%s, %s, %s)",
                            (version, name, checksum(sql)),
                        )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    raise MigrationError(f"échec de {name} : {exc}") from exc
                log.info("Migration appliquée : %s", name)
                applied_names.append(name)
            return applied_names
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            conn.commit()
    finally:
        conn.close()


def status(database_url, directory=None):
    """Retourne (appliquées, en attente) sans rien modifier hors table de suivi."""
    conn = psycopg2.connect(database_url)
    try:
        done_names, pending = plan(conn, directory or MIGRATIONS_DIR)
        return done_names, [name for _, name, _ in pending]
    finally:
        conn.close()


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = os.environ["DATABASE_URL"]
    try:
        if "--status" in argv:
            done, pending = status(url)
            print("Appliquées :", ", ".join(done) or "aucune")
            print("En attente :", ", ".join(pending) or "aucune")
        else:
            names = run(url)
            log.info("%d migration(s) appliquée(s)", len(names))
    except MigrationError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
