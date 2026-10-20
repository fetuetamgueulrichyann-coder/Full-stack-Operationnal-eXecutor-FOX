"""Configuration commune des tests.

Les tests s'exécutent sur une base PostgreSQL TEMPORAIRE (fox_test_xxxxxxxx), créée au début
de la session et supprimée à la fin. La base de FOX n'est jamais utilisée : TEST_ADMIN_URL ne
sert qu'à créer et supprimer cette base temporaire.

    docker compose --profile test run --rm tests
"""
import os
import pathlib
import sys
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATOR_DIR = pathlib.Path(os.environ.get("ORCHESTRATOR_DIR", ROOT / "orchestrator"))
BOT_DIR = pathlib.Path(os.environ.get("BOT_DIR", ROOT / "telegram_bot"))
DB_DIR = pathlib.Path(os.environ.get("DB_DIR", ROOT / "db"))
ADMIN_URL = os.environ.get("TEST_ADMIN_URL")

if not ADMIN_URL:
    pytest.exit(
        "TEST_ADMIN_URL est requis (accès administrateur au serveur PostgreSQL, utilisé "
        "uniquement pour créer une base temporaire). Voir tests/conftest.py.",
        returncode=2,
    )

sys.path.insert(0, str(ORCHESTRATOR_DIR))
sys.path.insert(1, str(BOT_DIR))  # logique du bot sans Telegram (botlogic.py)

# Environnement fictif défini AVANT l'import des modules de FOX.
os.environ.update(
    {
        "GEMINI_API_KEY": "cle-gemini-de-test",
        "GEMINI_MODEL": "modele-principal-test",
        "GROQ_API_KEY": "cle-groq-de-test",
        "GROQ_MODEL": "modele-groq-test",
        "LLM_BACKOFF_BASE": "0.001",
        "TIMEZONE": "Africa/Douala",
        "ALLOWED_USER_ID": "",
        "FOX_SERVICE_TOKEN": "jeton-de-service-de-test",
    }
)
for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GEMINI_FALLBACK_MODEL"):
    os.environ.pop(name, None)


def with_database(url, name):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))


def make_database(name):
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()
    return with_database(ADMIN_URL, name)


def drop_database(name):
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def apply_init_scripts(url):
    """Reproduit la création du schéma de départ (db/init) comme au premier démarrage."""
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        for script in sorted((DB_DIR / "init").glob("*.sql")):
            cur.execute(script.read_text(encoding="utf-8"))
    conn.close()


@pytest.fixture(scope="session")
def database_url():
    name = "fox_test_" + uuid.uuid4().hex[:8]
    url = make_database(name)
    apply_init_scripts(url)
    import migrate

    migrate.run(url, DB_DIR / "migrations")  # comptes, sessions, quotas (phase A)
    os.environ["DATABASE_URL"] = url
    yield url
    # Ferme le pool de FOX avant de supprimer la base.
    if "db" in sys.modules:
        sys.modules["db"].pool.closeall()
    drop_database(name)


@pytest.fixture(scope="session")
def db(database_url):
    import db as module

    return module


@pytest.fixture(scope="session")
def accounts(database_url, db):
    import accounts as module

    return module


@pytest.fixture
def make_user(accounts):
    """Crée un compte testeur (par invitation) et retourne le dictionnaire utilisateur."""
    counter = {"n": 0}

    def create(username=None, password="mot-de-passe-solide"):
        counter["n"] += 1
        username = username or f"testeur{counter['n']}"
        code = accounts.create_invitation(days=1, max_uses=1)
        user = accounts.register(code, username, password)
        user["password"] = password
        return user

    return create


@pytest.fixture
def make_owner(db):
    """Crée le compte propriétaire (lié à un identifiant Telegram)."""

    def create(telegram_id=4242, username="proprio"):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, role, telegram_id) VALUES (%s, 'owner', %s) "
                "RETURNING id",
                (username, telegram_id),
            )
            return {"id": cur.fetchone()[0], "username": username, "role": "owner",
                    "telegram_id": telegram_id}

    return create


@pytest.fixture(scope="session")
def tools(database_url):
    import tools as module

    return module


@pytest.fixture(scope="session")
def agent(database_url, db, tools):
    import agent as module

    return module


@pytest.fixture(scope="session")
def app_module(database_url, db, agent):
    import app as module

    return module


@pytest.fixture(autouse=True)
def clean_tables():
    """Vide les tables de la base TEMPORAIRE après chaque test (jamais une autre base)."""
    yield
    url = os.environ.get("DATABASE_URL", "")
    if "fox_test_" not in url:
        return  # garde-fou : on ne vide jamais une autre base
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE conversations, api_usage, pending_actions, audit_log, users, sessions, "
            "invitation_codes, auth_attempts, user_usage, provider_usage, chats, private_mode, "
            "private_context RESTART IDENTITY CASCADE"
        )
    conn.close()


# --- Faux objets de réponse Gemini -------------------------------------------------
def fake_response(text="Réponse de test", calls=None, tokens=10):
    from google.genai import types

    if calls:
        parts = [types.Part.from_function_call(name=c.name, args=c.args) for c in calls]
    else:
        parts = [types.Part.from_text(text=text or "")]
    content = types.Content(role="model", parts=parts)
    return SimpleNamespace(
        text=text,
        function_calls=calls or [],
        usage_metadata=SimpleNamespace(total_token_count=tokens),
        candidates=[SimpleNamespace(content=content, finish_reason="STOP")],
        prompt_feedback=None,
    )


def fake_call(name, **args):
    return SimpleNamespace(name=name, args=args)


def gemini_error(code, cls_name=None):
    from google.genai import errors

    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": "erreur de test", "status": "TEST"}})


@pytest.fixture
def helpers():
    return SimpleNamespace(
        response=fake_response, call=fake_call, error=gemini_error
    )
