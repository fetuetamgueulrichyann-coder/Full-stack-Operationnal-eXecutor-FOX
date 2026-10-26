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
# Chaque utilisateur a des conversations (table `chats`). La conversation active est la plus
# récemment utilisée : `/nouveau` en crée une, ouvrir une ancienne la remet en tête.
# En mode privé (table `private_mode`), rien n'est écrit dans `conversations` : seul un
# contexte temporaire (`private_context`) est gardé quelques minutes.
PRIVATE_CONTEXT_MINUTES = int(os.environ.get("PRIVATE_CONTEXT_MINUTES", "30"))
TITLE_MAX_CHARS = 40
# Conversations inactives supprimées après N jours (0 = jamais), selon le rôle.
RETENTION_DAYS_OWNER = int(os.environ.get("RETENTION_DAYS_OWNER", "90"))
RETENTION_DAYS_TESTER = int(os.environ.get("RETENTION_DAYS_TESTER", "30"))
MAX_OPEN_TASKS = int(os.environ.get("MAX_OPEN_TASKS", "50"))
MAX_MEMORIES = int(os.environ.get("MAX_MEMORIES", "30"))
TASK_RETENTION_DAYS = int(os.environ.get("TASK_RETENTION_DAYS", "90"))  # tâches closées ; 0 = jamais


def make_title(text):
    title = " ".join(str(text or "").split())
    if len(title) > TITLE_MAX_CHARS:
        title = title[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return title or None


def _lock_chats(cur, user_id):
    # Évite deux conversations créées en même temps pour le même utilisateur.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"chats:{user_id}",))


def _latest_chat(cur, user_id):
    cur.execute(
        "SELECT id FROM chats WHERE user_id = %s ORDER BY updated_at DESC, id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _ensure_chat(cur, user_id):
    _lock_chats(cur, user_id)
    chat_id = _latest_chat(cur, user_id)
    if chat_id is None:
        cur.execute("INSERT INTO chats (user_id) VALUES (%s) RETURNING id", (user_id,))
        chat_id = cur.fetchone()[0]
    return chat_id


def current_chat_id(user_id):
    """Identifiant de la conversation active, ou None s'il n'y en a pas encore."""
    with cursor() as cur:
        return _latest_chat(cur, user_id)


def load_history(user_id, limit, chat_id=None):
    """Derniers échanges d'une conversation (par défaut la conversation active)."""
    with cursor() as cur:
        if chat_id is None:
            chat_id = _latest_chat(cur, user_id)
            if chat_id is None:
                return []
        cur.execute(
            """
            SELECT user_message, bot_reply FROM conversations
            WHERE user_id = %s AND chat_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, chat_id, limit),
        )
        rows = cur.fetchall()
    rows.reverse()
    return rows


def save_exchange(user_id, user_message, bot_reply, chat_id=None):
    """Enregistre un échange dans la conversation active (ou celle indiquée, si elle est à lui)."""
    with cursor() as cur:
        if chat_id is None:
            chat_id = _ensure_chat(cur, user_id)
        cur.execute(
            """
            UPDATE chats SET updated_at = now(), title = COALESCE(title, %s)
            WHERE id = %s AND user_id = %s
            """,
            (make_title(user_message), chat_id, user_id),
        )
        if cur.rowcount != 1:
            raise LookupError("conversation introuvable")
        cur.execute(
            """
            INSERT INTO conversations (user_id, chat_id, user_message, bot_reply)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, chat_id, user_message, bot_reply),
        )
    return chat_id


def start_chat(user_id):
    """Nouvelle conversation (sort du mode privé). Réutilise la conversation active si vide."""
    set_private(user_id, False)
    with cursor() as cur:
        _lock_chats(cur, user_id)
        latest = _latest_chat(cur, user_id)
        if latest is not None:
            cur.execute("SELECT 1 FROM conversations WHERE chat_id = %s LIMIT 1", (latest,))
            if cur.fetchone() is None:
                cur.execute("UPDATE chats SET updated_at = now() WHERE id = %s", (latest,))
                return latest
        cur.execute("INSERT INTO chats (user_id) VALUES (%s) RETURNING id", (user_id,))
        return cur.fetchone()[0]


def open_chat(user_id, chat_id):
    """Reprend une conversation existante (sort du mode privé). False si elle n'est pas à lui."""
    with cursor() as cur:
        cur.execute(
            "UPDATE chats SET updated_at = now() WHERE id = %s AND user_id = %s",
            (chat_id, user_id),
        )
        found = cur.rowcount == 1
    if found:
        set_private(user_id, False)
    return found


def chat_belongs_to(user_id, chat_id):
    with cursor() as cur:
        cur.execute("SELECT 1 FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
        return cur.fetchone() is not None


def list_chats(user_id, limit=20):
    """Conversations non vides, la plus récente d'abord."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT count(*) FROM conversations m WHERE m.chat_id = c.id)
            FROM chats c
            WHERE c.user_id = %s
              AND EXISTS (SELECT 1 FROM conversations m WHERE m.chat_id = c.id)
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        return [
            {"id": r[0], "title": r[1], "created_at": r[2].isoformat(),
             "updated_at": r[3].isoformat(), "messages": r[4]}
            for r in cur.fetchall()
        ]


def delete_chat(user_id, chat_id):
    """Supprime définitivement une conversation et ses messages. False si elle n'est pas à lui."""
    with cursor() as cur:
        cur.execute("DELETE FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
        return cur.rowcount == 1


def delete_all_chats(user_id):
    """Supprime tout l'historique de l'utilisateur. Retourne le nombre de conversations."""
    with cursor() as cur:
        cur.execute("DELETE FROM chats WHERE user_id = %s", (user_id,))
        count = cur.rowcount
        cur.execute("DELETE FROM conversations WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM private_context WHERE user_id = %s", (user_id,))
        return count


# --- Mode « ne pas enregistrer » ---------------------------------------------
def private_active(user_id):
    with cursor() as cur:
        cur.execute("SELECT 1 FROM private_mode WHERE user_id = %s", (user_id,))
        return cur.fetchone() is not None


def set_private(user_id, on):
    with cursor() as cur:
        if on:
            cur.execute(
                "INSERT INTO private_mode (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,)
            )
        else:
            cur.execute("DELETE FROM private_mode WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM private_context WHERE user_id = %s", (user_id,))


def save_private_exchange(user_id, user_message, bot_reply):
    with cursor() as cur:
        cur.execute(
            "INSERT INTO private_context (user_id, user_message, bot_reply) VALUES (%s, %s, %s)",
            (user_id, user_message, bot_reply),
        )


def load_private_history(user_id, limit):
    with cursor() as cur:
        cur.execute(
            """
            SELECT user_message, bot_reply FROM private_context
            WHERE user_id = %s AND created_at > now() - make_interval(mins => %s)
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, PRIVATE_CONTEXT_MINUTES, limit),
        )
        rows = cur.fetchall()
    rows.reverse()
    return rows


# --- Purge de l'historique ancien --------------------------------------------
def purge_history(owner_days=None, tester_days=None):
    """Supprime les conversations inactives depuis trop longtemps (0 = ne jamais purger).

    Retourne {"chats": n, "private": m, "tasks": t}. Le contexte du mode privé de plus de
    PRIVATE_CONTEXT_MINUTES minutes est toujours supprimé.
    """
    owner_days = RETENTION_DAYS_OWNER if owner_days is None else owner_days
    tester_days = RETENTION_DAYS_TESTER if tester_days is None else tester_days
    deleted = 0
    with cursor() as cur:
        for role_filter, days in (("= 'owner'", owner_days), ("<> 'owner'", tester_days)):
            if days and days > 0:
                cur.execute(
                    f"""
                    DELETE FROM chats c USING users u
                    WHERE u.id = c.user_id AND u.role {role_filter}
                      AND c.updated_at < now() - make_interval(days => %s)
                    """,  # noqa: S608 (fragment fixe, jamais issu d'une saisie)
                    (days,),
                )
                deleted += cur.rowcount
        cur.execute(
            "DELETE FROM private_context WHERE created_at < now() - make_interval(mins => %s)",
            (PRIVATE_CONTEXT_MINUTES,),
        )
        private = cur.rowcount
    return {"chats": deleted, "private": private, "tasks": purge_closed_tasks()}


# --- Tâches ----------------------------------------------------------------
OPEN_STATUSES = ("a_faire", "en_cours")
CLOSED_STATUSES = ("terminee", "annulee")
_UNSET = object()


class TaskLimit(Exception):
    """Trop de tâches ouvertes pour cet utilisateur."""


_TASK_COLUMNS = (
    "id, title, priority, due_at, status, created_at, updated_at, done_at, "
    "(due_at IS NOT NULL AND due_at < now() AND status IN ('a_faire', 'en_cours'))"
)


def _task(row):
    return {
        "id": row[0], "title": row[1], "priority": row[2], "due_at": row[3], "status": row[4],
        "created_at": row[5], "updated_at": row[6], "done_at": row[7], "overdue": bool(row[8]),
    }


def _lock_tasks(cur, user_id):
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"tasks:{user_id}",))


def _open_count(cur, user_id):
    cur.execute(
        "SELECT count(*) FROM tasks WHERE user_id = %s AND status IN ('a_faire', 'en_cours')",
        (user_id,),
    )
    return cur.fetchone()[0]


def has_time(due_at):
    """Vrai si l'échéance a une heure précise. Une échéance « jour seul » vaut 23:59:59 (fuseau de FOX)."""
    return due_at is not None and (due_at.hour, due_at.minute, due_at.second) != (23, 59, 59)


def create_task(user_id, title, priority="normale", due_at=None, max_open=None):
    max_open = MAX_OPEN_TASKS if max_open is None else max_open
    with cursor() as cur:
        _lock_tasks(cur, user_id)
        if max_open and _open_count(cur, user_id) >= max_open:
            raise TaskLimit(max_open)
        cur.execute(
            f"INSERT INTO tasks (user_id, title, priority, due_at, due_time) VALUES (%s, %s, %s, %s, %s) "
            f"RETURNING {_TASK_COLUMNS}",  # noqa: S608 (colonnes fixes)
            (user_id, title, priority, due_at, has_time(due_at)),
        )
        return _task(cur.fetchone())


def list_tasks(user_id, statuses=OPEN_STATUSES, limit=50):
    """En retard d'abord, puis par échéance, puis les tâches sans échéance (priorité haute en premier)."""
    with cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TASK_COLUMNS} FROM tasks
            WHERE user_id = %s AND status = ANY(%s)
            ORDER BY (due_at IS NOT NULL AND due_at < now() AND status IN ('a_faire', 'en_cours')) DESC,
                     (due_at IS NULL), due_at,
                     CASE priority WHEN 'haute' THEN 0 WHEN 'normale' THEN 1 ELSE 2 END,
                     id
            LIMIT %s
            """,  # noqa: S608 (colonnes fixes)
            (user_id, list(statuses), limit),
        )
        return [_task(r) for r in cur.fetchall()]


def get_task(user_id, task_id):
    with cursor() as cur:
        cur.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = %s AND user_id = %s",  # noqa: S608
            (task_id, user_id),
        )
        row = cur.fetchone()
    return _task(row) if row else None


def update_task(user_id, task_id, title=None, priority=None, due_at=_UNSET, status=None, max_open=None):
    """Modifie une tâche de cet utilisateur. due_at=None efface l'échéance. None si introuvable."""
    max_open = MAX_OPEN_TASKS if max_open is None else max_open
    with cursor() as cur:
        _lock_tasks(cur, user_id)
        cur.execute("SELECT status FROM tasks WHERE id = %s AND user_id = %s FOR UPDATE", (task_id, user_id))
        current = cur.fetchone()
        if current is None:
            return None
        if status in OPEN_STATUSES and current[0] in CLOSED_STATUSES:
            if max_open and _open_count(cur, user_id) >= max_open:
                raise TaskLimit(max_open)
        sets, values = ["updated_at = now()"], []
        if title is not None:
            sets.append("title = %s")
            values.append(title)
        if priority is not None:
            sets.append("priority = %s")
            values.append(priority)
        if due_at is not _UNSET:
            sets.append("due_at = %s")
            values.append(due_at)
            sets.append("due_time = %s")
            values.append(has_time(due_at))
            sets.append("reminded_at = NULL")  # nouvelle échéance : nouveau rappel
        if status is not None:
            sets.append("status = %s")
            values.append(status)
            sets.append("done_at = now()" if status == "terminee" else "done_at = NULL")
            if status in OPEN_STATUSES and current[0] in CLOSED_STATUSES:
                sets.append("reminded_at = NULL")  # tâche rouverte : rappel de nouveau possible
        cur.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s AND user_id = %s "  # noqa: S608 (fragments fixes)
            f"RETURNING {_TASK_COLUMNS}",
            (*values, task_id, user_id),
        )
        return _task(cur.fetchone())


REMINDER_MAX_AGE_HOURS = 24  # un rappel manqué depuis plus longtemps (bot arrêté) n'est plus envoyé


def claim_due_reminders(limit=20):
    """Rappels à envoyer maintenant : tâches ouvertes dont l'heure est passée, pas encore rappelées.

    Chaque tâche n'est retournée qu'une fois (marquée dans la même opération), même si
    plusieurs processus interrogent en même temps.
    """
    with cursor() as cur:
        cur.execute(
            """
            UPDATE tasks t SET reminded_at = now()
            FROM users u
            WHERE t.id IN (
                    SELECT id FROM tasks
                    WHERE due_time AND reminded_at IS NULL AND status IN ('a_faire', 'en_cours')
                      AND due_at <= now() AND due_at > now() - make_interval(hours => %s)
                    ORDER BY due_at LIMIT %s FOR UPDATE SKIP LOCKED)
              AND u.id = t.user_id AND u.telegram_id IS NOT NULL AND u.disabled_at IS NULL
            RETURNING t.id, t.title, t.due_at, u.telegram_id
            """,
            (REMINDER_MAX_AGE_HOURS, limit),
        )
        return [{"id": r[0], "title": r[1], "due_at": r[2], "telegram_id": r[3]} for r in cur.fetchall()]


def delete_task(user_id, task_id):
    with cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
        return cur.rowcount > 0


def purge_closed_tasks(days=None):
    """Supprime les tâches terminées ou annulées depuis plus de `days` jours (0 = jamais)."""
    days = TASK_RETENTION_DAYS if days is None else days
    if not days or days <= 0:
        return 0
    with cursor() as cur:
        cur.execute(
            "DELETE FROM tasks WHERE status IN ('terminee', 'annulee') "
            "AND updated_at < now() - make_interval(days => %s)",
            (days,),
        )
        return cur.rowcount


# --- Mémoire -----------------------------------------------------------------
class MemoryLimit(Exception):
    """Trop d'éléments mémorisés pour cet utilisateur."""


def _memory(row):
    return {"id": row[0], "content": row[1], "source": row[2], "created_at": row[3]}


def create_memory(user_id, content, source="user", max_items=None):
    """Mémorise un élément. Un élément identique (sans tenir compte de la casse) n'est pas dupliqué."""
    max_items = MAX_MEMORIES if max_items is None else max_items
    with cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"memories:{user_id}",))
        cur.execute(
            "SELECT id, content, source, created_at FROM memories "
            "WHERE user_id = %s AND lower(content) = lower(%s)",
            (user_id, content),
        )
        existing = cur.fetchone()
        if existing:
            return _memory(existing)
        cur.execute("SELECT count(*) FROM memories WHERE user_id = %s", (user_id,))
        if max_items and cur.fetchone()[0] >= max_items:
            raise MemoryLimit(max_items)
        cur.execute(
            "INSERT INTO memories (user_id, content, source) VALUES (%s, %s, %s) "
            "RETURNING id, content, source, created_at",
            (user_id, content, source),
        )
        return _memory(cur.fetchone())


def list_memories(user_id):
    """Du plus récent au plus ancien."""
    with cursor() as cur:
        cur.execute(
            "SELECT id, content, source, created_at FROM memories WHERE user_id = %s "
            "ORDER BY created_at DESC, id DESC",
            (user_id,),
        )
        return [_memory(r) for r in cur.fetchall()]


def get_memory(user_id, memory_id):
    with cursor() as cur:
        cur.execute(
            "SELECT id, content, source, created_at FROM memories WHERE id = %s AND user_id = %s",
            (memory_id, user_id),
        )
        row = cur.fetchone()
    return _memory(row) if row else None


def delete_memory(user_id, memory_id):
    with cursor() as cur:
        cur.execute("DELETE FROM memories WHERE id = %s AND user_id = %s", (memory_id, user_id))
        return cur.rowcount > 0


def delete_all_memories(user_id):
    with cursor() as cur:
        cur.execute("DELETE FROM memories WHERE user_id = %s", (user_id,))
        return cur.rowcount


# --- Journal des actions -------------------------------------------------------
JOURNAL_AUDIT_EVENTS = (
    "task_created", "task_updated", "task_deleted", "memory_added", "memory_deleted",
    "memory_cleared", "chat_deleted", "history_deleted",
)


def journal_entries(user_id, limit=15):
    """Actions proposées à confirmer + actions directes de l'utilisateur, la plus récente d'abord.

    Chaque entrée : {"at", "kind", "status", "tool", "summary"}. Le journal d'audit ne contient jamais
    le texte des conversations ni des éléments de mémoire ; les résumés viennent des actions proposées.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT created_at, tool, summary, status, undone_at FROM pending_actions "
            "WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, limit),
        )
        entries = [
            {"at": r[0], "kind": "action", "status": "undone" if r[4] else r[3], "tool": r[1], "summary": r[2]}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT created_at, event FROM audit_log WHERE user_id = %s AND event = ANY(%s) "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, list(JOURNAL_AUDIT_EVENTS), limit),
        )
        entries += [
            {"at": r[0], "kind": "event", "status": r[1], "tool": None, "summary": None}
            for r in cur.fetchall()
        ]
    entries.sort(key=lambda e: e["at"], reverse=True)
    return entries[:limit]


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
