"""Accès base des chantiers et du livre de comptes (phase F). Toujours filtré par utilisateur."""
import os

from db import cursor

_PROJECT_SELECT = """
    SELECT p.id, p.name, p.client_id, p.client_name, p.quote_id, q.number, p.status, p.budget, p.start_date,
           p.end_date, p.notes, p.archived_at, p.created_at,
           COALESCE(SUM(e.amount) FILTER (WHERE e.kind = 'recette'), 0),
           COALESCE(SUM(e.amount) FILTER (WHERE e.kind = 'depense'), 0)
    FROM projects p
    LEFT JOIN quotes q ON q.id = p.quote_id
    LEFT JOIN ledger_entries e ON e.project_id = p.id AND e.user_id = p.user_id
"""
_PROJECT_GROUP = " GROUP BY p.id, q.number"


class DuplicateProject(Exception):
    """Un chantier du même nom existe déjà (sans tenir compte de la casse)."""


class ProjectLimit(Exception):
    """Trop de chantiers pour cet utilisateur."""


class LedgerLimit(Exception):
    """Trop d'écritures pour cet utilisateur."""


def _project(row):
    keys = ("id", "name", "client_id", "client_name", "quote_id", "quote_number", "status", "budget", "start_date",
            "end_date", "notes", "archived_at", "created_at", "income", "expense")
    return dict(zip(keys, row))


def _lock(cur, user_id, what):
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{what}:{user_id}",))


def create_project(user_id, name, client_id=None, client_name=None, quote_id=None, budget=None,
                   start_date=None, end_date=None, notes=None, max_projects=None):
    if max_projects is None:
        max_projects = int(os.environ.get("MAX_PROJECTS", "100"))
    with cursor() as cur:
        _lock(cur, user_id, "projects")
        cur.execute("SELECT 1 FROM projects WHERE user_id = %s AND lower(name) = lower(%s)", (user_id, name))
        if cur.fetchone():
            raise DuplicateProject(name)
        cur.execute("SELECT count(*) FROM projects WHERE user_id = %s", (user_id,))
        if max_projects and cur.fetchone()[0] >= max_projects:
            raise ProjectLimit(max_projects)
        cur.execute(
            "INSERT INTO projects (user_id, name, client_id, client_name, quote_id, budget, start_date, end_date, notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, name, client_id, client_name, quote_id, budget, start_date, end_date, notes),
        )
        new_id = cur.fetchone()[0]
    return get_project(user_id, new_id)


def get_project(user_id, project_id):
    with cursor() as cur:
        cur.execute(_PROJECT_SELECT + " WHERE p.user_id = %s AND p.id = %s" + _PROJECT_GROUP, (user_id, project_id))
        row = cur.fetchone()
    return _project(row) if row else None


def find_projects(user_id, ref):
    """Un chantier par numéro (« 12 »), par nom exact (casse ignorée), sinon tous les noms qui contiennent le texte."""
    text = str(ref or "").strip()
    if text.isdigit():
        found = get_project(user_id, int(text))
        if found:
            return [found]
    with cursor() as cur:
        cur.execute(_PROJECT_SELECT + " WHERE p.user_id = %s AND lower(p.name) = lower(%s)" + _PROJECT_GROUP, (user_id, text))
        rows = cur.fetchall()
        if not rows and text:
            like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            cur.execute(
                _PROJECT_SELECT + " WHERE p.user_id = %s AND p.name ILIKE %s" + _PROJECT_GROUP + " ORDER BY lower(p.name) LIMIT 10",
                (user_id, like),
            )
            rows = cur.fetchall()
    return [_project(r) for r in rows]


def list_projects(user_id, status=None, archived=False, limit=100):
    sql = _PROJECT_SELECT + " WHERE p.user_id = %s AND (p.archived_at IS NOT NULL) = %s"
    params = [user_id, archived]
    if status:
        sql += " AND p.status = %s"
        params.append(status)
    sql += _PROJECT_GROUP + " ORDER BY (p.status = 'termine'), lower(p.name), p.id LIMIT %s"
    params.append(limit)
    with cursor() as cur:
        cur.execute(sql, params)
        return [_project(r) for r in cur.fetchall()]


_CHANGEABLE = {"status": "status", "budget": "budget", "start_date": "start_date", "end_date": "end_date", "notes": "notes"}


def update_project(user_id, project_id, changes):
    """`changes` : status, budget, start_date, end_date, notes (valeurs déjà validées) et/ou archived (booléen)."""
    sets, params = [], []
    for key, column in _CHANGEABLE.items():
        if key in changes:
            sets.append(f"{column} = %s")
            params.append(changes[key])
    if "archived" in changes:
        sets.append("archived_at = " + ("now()" if changes["archived"] else "NULL"))
    if not sets:
        return get_project(user_id, project_id)
    with cursor() as cur:
        cur.execute(
            "UPDATE projects SET " + ", ".join(sets) + ", updated_at = now() WHERE id = %s AND user_id = %s",  # noqa: S608
            (*params, project_id, user_id),
        )
        if cur.rowcount == 0:
            return None
    return get_project(user_id, project_id)


# --- Écritures -----------------------------------------------------------------------------------
_ENTRY_SELECT = (
    "SELECT e.id, e.kind, e.amount, e.currency, e.entry_date, e.category, e.description, e.project_id, p.name "
    "FROM ledger_entries e LEFT JOIN projects p ON p.id = e.project_id"
)


def _entry(row):
    keys = ("id", "kind", "amount", "currency", "entry_date", "category", "description", "project_id", "project_name")
    return dict(zip(keys, row))


def add_entry(user_id, kind, amount, currency, entry_date, description, category=None, project_id=None, max_entries=None):
    if max_entries is None:
        max_entries = int(os.environ.get("MAX_LEDGER_ENTRIES", "20000"))
    with cursor() as cur:
        _lock(cur, user_id, "ledger")
        cur.execute("SELECT count(*) FROM ledger_entries WHERE user_id = %s", (user_id,))
        if max_entries and cur.fetchone()[0] >= max_entries:
            raise LedgerLimit(max_entries)
        if project_id is not None:
            cur.execute("SELECT 1 FROM projects WHERE id = %s AND user_id = %s", (project_id, user_id))
            if cur.fetchone() is None:
                raise LookupError("chantier introuvable")
        cur.execute(
            "INSERT INTO ledger_entries (user_id, kind, amount, currency, entry_date, category, description, project_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, kind, amount, currency, entry_date, category, description, project_id),
        )
        new_id = cur.fetchone()[0]
    return get_entry(user_id, new_id)


def get_entry(user_id, entry_id):
    with cursor() as cur:
        cur.execute(_ENTRY_SELECT + " WHERE e.user_id = %s AND e.id = %s", (user_id, entry_id))
        row = cur.fetchone()
    return _entry(row) if row else None


def list_entries(user_id, start=None, end=None, kind=None, project_id=None, limit=200):
    sql, params = _ENTRY_SELECT + " WHERE e.user_id = %s", [user_id]
    if start is not None:
        sql += " AND e.entry_date >= %s"
        params.append(start)
    if end is not None:
        sql += " AND e.entry_date < %s"
        params.append(end)
    if kind:
        sql += " AND e.kind = %s"
        params.append(kind)
    if project_id is not None:
        sql += " AND e.project_id = %s"
        params.append(project_id)
    sql += " ORDER BY e.entry_date DESC, e.id DESC LIMIT %s"
    params.append(limit)
    with cursor() as cur:
        cur.execute(sql, params)
        return [_entry(r) for r in cur.fetchall()]


def delete_entry(user_id, entry_id):
    with cursor() as cur:
        cur.execute("DELETE FROM ledger_entries WHERE id = %s AND user_id = %s", (entry_id, user_id))
        return cur.rowcount > 0


def summary(user_id, start, end, project_id=None):
    where, params = "user_id = %s AND entry_date >= %s AND entry_date < %s", [user_id, start, end]
    if project_id is not None:
        where += " AND project_id = %s"
        params.append(project_id)
    with cursor() as cur:
        cur.execute(
            "SELECT kind, category, SUM(amount), count(*) FROM ledger_entries WHERE " + where  # noqa: S608
            + " GROUP BY kind, category ORDER BY kind, SUM(amount) DESC",
            params,
        )
        rows = cur.fetchall()
    income = sum((r[2] for r in rows if r[0] == "recette"), 0)
    expense = sum((r[2] for r in rows if r[0] == "depense"), 0)
    return {
        "income": income, "expense": expense, "count": sum(r[3] for r in rows),
        "by_category": [{"kind": r[0], "category": r[1], "total": r[2]} for r in rows],
    }


# --- Saisie partagée par l'API et les outils IA ----------------------------------------------------
def new_project_fields(user_id, name, client=None, quote=None, budget=None, start_date=None, end_date=None,
                       notes=None, today=None):
    """Valide la création d'un chantier et résout le client et le devis liés. Lève CommercialInputError."""
    import commercial
    import db
    import finance

    fields = {
        "name": finance.clean_project_name(name),
        "budget": finance.parse_budget(budget),
        "start_date": finance.parse_optional_date(start_date, today),
        "end_date": finance.parse_optional_date(end_date, today),
        "notes": finance.clean_text(notes, "La note", finance.NOTES_MAX),
        "client_id": None, "client_name": None, "quote_id": None,
    }
    if fields["start_date"] and fields["end_date"] and fields["end_date"] < fields["start_date"]:
        raise commercial.CommercialInputError("La date de fin est avant la date de début.")
    client_text = commercial.squash(client)
    if client_text:
        matches = db.find_clients(user_id, client_text)
        if len(matches) > 1:
            raise commercial.CommercialInputError(
                "Plusieurs clients correspondent : " + ", ".join(c["name"] for c in matches) + ". Précise le nom complet.")
        if matches:
            fields["client_id"], fields["client_name"] = matches[0]["id"], matches[0]["name"]
        else:
            fields["client_name"] = commercial.clean_name(client_text)
    quote_text = commercial.squash(quote)
    if quote_text:
        found = db.get_quote(user_id, quote_text)
        if found is None:
            raise commercial.CommercialInputError("Devis introuvable.")
        fields["quote_id"] = found["id"]
        if not fields["client_name"]:
            fields["client_id"], fields["client_name"] = found["client_id"], found["client_name"]
    return fields


def resolve_project(user_id, ref):
    """Le chantier désigné par un numéro ou un nom ; CommercialInputError si absent ou ambigu."""
    import commercial

    matches = find_projects(user_id, ref)
    if not matches:
        raise commercial.CommercialInputError("Chantier introuvable.")
    if len(matches) > 1:
        raise commercial.CommercialInputError(
            "Plusieurs chantiers correspondent : " + ", ".join(p["name"] for p in matches) + ". Précise le nom complet.")
    return matches[0]
