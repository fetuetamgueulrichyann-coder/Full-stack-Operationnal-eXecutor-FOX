import json
import os
from contextlib import contextmanager

from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

pool = ThreadedConnectionPool(1, 5, os.environ["DATABASE_URL"])


@contextmanager
def cursor():
    conn = pool.getconn()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        pool.putconn(conn)


# --- Plafonds d'utilisation --------------------------------------------------
class QuotaExceeded(Exception):
    """Plafond atteint. `scope` vaut « user » (plafond de l'utilisateur) ou « global »."""

    def __init__(self, scope):
        super().__init__(scope)
        self.scope = scope


def reserve_quota(user_id, user_calls, user_tokens, global_calls, global_tokens):
    """Réserve un appel pour la journée (UTC) : plafond de l'utilisateur, puis plafond global.

    Les deux réservations se font dans une même transaction : si l'une est refusée, rien n'est
    compté. Lève QuotaExceeded sinon.
    """
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_usage (user_id, day, calls, tokens)
            VALUES (%s, (now() AT TIME ZONE 'utc')::date, 1, 0)
            ON CONFLICT (user_id, day) DO UPDATE SET calls = user_usage.calls + 1
            WHERE user_usage.calls < %s AND user_usage.tokens < %s
            RETURNING calls
            """,
            (user_id, user_calls, user_tokens),
        )
        if cur.fetchone() is None:
            raise QuotaExceeded("user")
        cur.execute(
            """
            INSERT INTO api_usage (day, calls, tokens)
            VALUES ((now() AT TIME ZONE 'utc')::date, 1, 0)
            ON CONFLICT (day) DO UPDATE SET calls = api_usage.calls + 1
            WHERE api_usage.calls < %s AND api_usage.tokens < %s
            RETURNING calls
            """,
            (global_calls, global_tokens),
        )
        if cur.fetchone() is None:
            raise QuotaExceeded("global")


def add_tokens(user_id, count):
    """Ajoute les tokens consommés au compteur de l'utilisateur et au compteur global."""
    with cursor() as cur:
        cur.execute(
            """
            UPDATE api_usage SET tokens = tokens + %s
            WHERE day = (now() AT TIME ZONE 'utc')::date
            """,
            (count,),
        )
        cur.execute(
            """
            UPDATE user_usage SET tokens = tokens + %s
            WHERE user_id = %s AND day = (now() AT TIME ZONE 'utc')::date
            """,
            (count, user_id),
        )


def reserve_provider(provider, max_calls, max_tokens):
    """Réserve un appel auprès d'un fournisseur d'IA (plafond tous utilisateurs confondus)."""
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO provider_usage (provider, day, calls, tokens)
            VALUES (%s, (now() AT TIME ZONE 'utc')::date, 1, 0)
            ON CONFLICT (provider, day) DO UPDATE SET calls = provider_usage.calls + 1
            WHERE provider_usage.calls < %s AND provider_usage.tokens < %s
            RETURNING calls
            """,
            (provider, max_calls, max_tokens),
        )
        return cur.fetchone() is not None


def add_provider_tokens(provider, count):
    with cursor() as cur:
        cur.execute(
            """
            UPDATE provider_usage SET tokens = tokens + %s
            WHERE provider = %s AND day = (now() AT TIME ZONE 'utc')::date
            """,
            (count, provider),
        )


# --- Historique --------------------------------------------------------------
def load_history(user_id, limit):
    with cursor() as cur:
        cur.execute(
            """
            SELECT user_message, bot_reply FROM conversations
            WHERE user_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
    rows.reverse()
    return rows


def save_exchange(user_id, user_message, bot_reply):
    with cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (user_id, user_message, bot_reply) VALUES (%s, %s, %s)",
            (user_id, user_message, bot_reply),
        )


# --- Journal d'audit ---------------------------------------------------------
def audit(user_id, event, tool=None, details=None):
    with cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (user_id, event, tool, details) VALUES (%s, %s, %s, %s)",
            (user_id, event, tool, Json(details or {})),
        )


# --- Actions en attente ------------------------------------------------------
def create_pending(user_id, tool, args, summary, ttl_minutes):
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO pending_actions (user_id, tool, args, summary, expires_at)
            VALUES (%s, %s, %s, %s, now() + make_interval(mins => %s))
            RETURNING id
            """,
            (user_id, tool, Json(args), summary, ttl_minutes),
        )
        return str(cur.fetchone()[0])


def confirm_pending(action_id, user_id):
    """Passe l'action de 'pending' à 'confirmed' si elle est valide. Retourne (outil, args) ou None."""
    with cursor() as cur:
        cur.execute(
            """
            UPDATE pending_actions SET status = 'confirmed', resolved_at = now()
            WHERE id = %s AND user_id = %s AND status = 'pending' AND expires_at > now()
            RETURNING tool, args
            """,
            (action_id, user_id),
        )
        return cur.fetchone()


def cancel_pending(action_id, user_id):
    with cursor() as cur:
        cur.execute(
            """
            UPDATE pending_actions SET status = 'cancelled', resolved_at = now()
            WHERE id = %s AND user_id = %s AND status = 'pending'
            RETURNING tool
            """,
            (action_id, user_id),
        )
        return cur.fetchone()


def expire_pending(action_id, user_id):
    with cursor() as cur:
        cur.execute(
            """
            UPDATE pending_actions SET status = 'expired', resolved_at = now()
            WHERE id = %s AND user_id = %s AND status = 'pending' AND expires_at <= now()
            RETURNING tool
            """,
            (action_id, user_id),
        )
        return cur.fetchone()


def _dumps(value):
    return json.dumps(value, default=str)


def finish_pending(action_id, status, result=None):
    with cursor() as cur:
        cur.execute(
            """
            UPDATE pending_actions
            SET status = %s,
                result = %s,
                executed_at = CASE WHEN %s = 'executed' THEN now() ELSE executed_at END
            WHERE id = %s
            """,
            (status, None if result is None else Json(result, dumps=_dumps), status, action_id),
        )


# --- Annulation des actions exécutées ----------------------------------------
def last_executed(user_id, window_minutes, exclude_tool):
    """Dernière action exécutée, non annulée, dans la fenêtre de temps donnée."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, tool, summary FROM pending_actions
            WHERE user_id = %s AND status = 'executed' AND undone_at IS NULL
              AND tool <> %s
              AND executed_at > now() - make_interval(mins => %s)
            ORDER BY executed_at DESC
            LIMIT 1
            """,
            (user_id, exclude_tool, window_minutes),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "tool": row[1], "summary": row[2]}


def claim_undo(target_id):
    """Marque l'action comme annulée (une seule fois). Retourne ses données ou None."""
    with cursor() as cur:
        cur.execute(
            """
            UPDATE pending_actions SET undone_at = now()
            WHERE id = %s AND status = 'executed' AND undone_at IS NULL
            RETURNING user_id, tool, args, result
            """,
            (target_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"user_id": row[0], "tool": row[1], "args": row[2], "result": row[3]}


def release_undo(target_id):
    with cursor() as cur:
        cur.execute(
            "UPDATE pending_actions SET undone_at = NULL WHERE id = %s", (target_id,)
        )
