"""Accès à la base pour la confidentialité : réglages, autorisations, messages retenus.

Phase 1a : ces fonctions existent et sont testées, mais le flux de FOX ne les appelle pas encore (sauf le
nettoyage périodique). Règles garanties ici, côté serveur :
- l'expiration est calculée par le serveur (jamais par le client) et plafonnée par le code ;
- une autorisation « message » est liée à UNE opération : tous les tours d'outils de cette opération la
  partagent, une autre opération ne peut pas l'utiliser ; la première utilisation la marque « consommée » ;
- une autorisation ne couvre jamais qu'un seul fournisseur et qu'une seule catégorie ;
- la validité est relue en base à chaque utilisation (aucun cache) : une révocation prend effet au prochain appel.
"""
import logging

from psycopg2.extras import Json

import db
import privacy
import providers

log = logging.getLogger("privacy_store")

SETTING_FIELDS = ("level", "preferred_provider", "notification_detail")


class GrantError(ValueError):
    """Demande d'autorisation invalide (fournisseur, catégorie, portée ou cible incohérents)."""


# --- Réglages ----------------------------------------------------------------
def default_settings():
    return {
        "level": privacy.DEFAULT_LEVEL,
        "preferred_provider": None,
        "notification_detail": "discret",
        "consent_version": None,
        "consent_at": None,
        "consented": False,
    }


def get_settings(user_id):
    """Réglages de l'utilisateur ; sans ligne : niveau Protégé, sans consentement."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT level, preferred_provider, notification_detail, consent_version, consent_at "
            "FROM privacy_settings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return default_settings()
    return {
        "level": row[0], "preferred_provider": row[1], "notification_detail": row[2],
        "consent_version": row[3], "consent_at": row[4], "consented": row[3] is not None,
    }


def save_settings(user_id, **fields):
    """Met à jour un ou plusieurs réglages (validés). Retourne les réglages complets."""
    unknown = set(fields) - set(SETTING_FIELDS)
    if unknown:
        raise GrantError("réglage inconnu : " + ", ".join(sorted(unknown)))
    if "level" in fields and fields["level"] not in privacy.LEVELS:
        raise GrantError("niveau invalide")
    if "preferred_provider" in fields and fields["preferred_provider"] not in (None, "gemini", "groq"):
        raise GrantError("fournisseur invalide")
    if "notification_detail" in fields and fields["notification_detail"] not in privacy.NOTIFICATION_DETAILS:
        raise GrantError("détail de notification invalide")
    with db.cursor() as cur:
        cur.execute("INSERT INTO privacy_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        for key, value in fields.items():
            cur.execute(f"UPDATE privacy_settings SET {key} = %s, updated_at = now() WHERE user_id = %s", (value, user_id))  # noqa: S608 (clé validée ci-dessus)
    return get_settings(user_id)


def record_consent(user_id, version):
    with db.cursor() as cur:
        cur.execute("INSERT INTO privacy_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        cur.execute(
            "UPDATE privacy_settings SET consent_version = %s, consent_at = now(), updated_at = now() WHERE user_id = %s",
            (version, user_id),
        )
    return get_settings(user_id)


# --- Autorisations -------------------------------------------------------------
def _check_target(cur, user_id, chat_id, file_id):
    if chat_id is not None:
        cur.execute("SELECT 1 FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
        if cur.fetchone() is None:
            raise GrantError("conversation introuvable pour cet utilisateur")
    if file_id is not None:
        cur.execute("SELECT 1 FROM files WHERE id = %s::uuid AND user_id = %s", (str(file_id), user_id))
        if cur.fetchone() is None:
            raise GrantError("fichier introuvable pour cet utilisateur")


def create_grant(user_id, provider, category, scope, *, chat_id=None, file_id=None, operation_id=None):
    """Crée une autorisation (action explicite de l'utilisateur, jamais décidée par le modèle). Retourne son id."""
    if providers.get(provider) is None:
        raise GrantError("fournisseur inconnu")
    if category not in privacy.GRANT_CATEGORIES:
        raise GrantError("catégorie inconnue")
    if scope == "conversation":
        if chat_id is None:
            raise GrantError("une autorisation de conversation exige la conversation")
        ttl = privacy.MAX_CONVERSATION_GRANT
    elif scope == "message":
        if not operation_id:
            raise GrantError("une autorisation de message exige l'opération")
        ttl = privacy.MESSAGE_GRANT_TTL
    else:
        raise GrantError("portée inconnue")
    if (category == privacy.FICHIER) != (file_id is not None):
        raise GrantError("une autorisation de fichier exige le fichier, et seulement elle")
    with db.cursor() as cur:
        _check_target(cur, user_id, chat_id, file_id)
        cur.execute(
            """
            INSERT INTO data_grants (user_id, provider, category, scope, chat_id, file_id, operation_id, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, now() + %s)
            RETURNING id
            """,
            (user_id, provider, category, scope, chat_id, None if file_id is None else str(file_id), operation_id, ttl),
        )
        return cur.fetchone()[0]


def use_grant(user_id, provider, category, *, operation_id, chat_id=None, file_id=None):
    """Cherche une autorisation valide pour CET appel sortant et l'utilise. Retourne son id, ou None.

    Valide = non révoquée, non expirée, même utilisateur, fournisseur, catégorie (et fichier), et :
    - « message » : liée à cette opération (les tours d'outils suivants de la même opération passent) ;
    - « conversation » : liée à cette conversation.
    La première utilisation d'une autorisation « message » la marque consommée (date conservée) ; une
    autre opération ne la retrouve jamais. Le tout en une seule instruction atomique.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            WITH g AS (
                SELECT id FROM data_grants
                WHERE user_id = %(user)s AND provider = %(provider)s AND category = %(category)s
                  AND revoked_at IS NULL AND expires_at > now()
                  AND file_id IS NOT DISTINCT FROM %(file)s::uuid
                  AND ((scope = 'message' AND operation_id = %(operation)s)
                    OR (scope = 'conversation' AND chat_id IS NOT NULL AND chat_id = %(chat)s))
                ORDER BY (scope = 'message') DESC, id
                LIMIT 1
                FOR UPDATE
            )
            UPDATE data_grants d
            SET consumed_at = CASE WHEN d.scope = 'message' THEN COALESCE(d.consumed_at, now()) ELSE NULL END
            FROM g WHERE d.id = g.id
            RETURNING d.id
            """,
            {
                "user": user_id, "provider": provider, "category": category, "operation": operation_id,
                "chat": chat_id, "file": None if file_id is None else str(file_id),
            },
        )
        row = cur.fetchone()
    return row[0] if row else None


def covered_categories(user_id, provider, *, operation_id, chat_id=None):
    """Catégories (hors fichier) couvertes par une autorisation valide, sans rien consommer."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT category FROM data_grants
            WHERE user_id = %s AND provider = %s AND category <> 'fichier'
              AND revoked_at IS NULL AND expires_at > now()
              AND ((scope = 'message' AND operation_id = %s) OR (scope = 'conversation' AND chat_id IS NOT NULL AND chat_id = %s))
            """,
            (user_id, provider, operation_id, chat_id),
        )
        return {r[0] for r in cur.fetchall()}


def revoke(user_id, grant_id):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE data_grants SET revoked_at = now() WHERE id = %s AND user_id = %s AND revoked_at IS NULL",
            (grant_id, user_id),
        )
        return cur.rowcount == 1


def revoke_all(user_id):
    """« Tout révoquer » : prend effet au prochain appel sortant. Retourne le nombre d'autorisations révoquées."""
    with db.cursor() as cur:
        cur.execute("UPDATE data_grants SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
        return cur.rowcount


def list_active(user_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, provider, category, scope, chat_id, file_id, created_at, expires_at FROM data_grants "
            "WHERE user_id = %s AND revoked_at IS NULL AND expires_at > now() ORDER BY id",
            (user_id,),
        )
        keys = ("id", "provider", "category", "scope", "chat_id", "file_id", "created_at", "expires_at")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


# --- Messages retenus ------------------------------------------------------------
def hold_message(user_id, chat_id, operation_id, payload):
    """Garde un message (au plus 10 minutes) le temps de demander une autorisation."""
    with db.cursor() as cur:
        _check_target(cur, user_id, chat_id, None)
        cur.execute(
            "INSERT INTO held_messages (user_id, chat_id, operation_id, payload, expires_at) VALUES (%s, %s, %s, %s, now() + %s) RETURNING id",
            (user_id, chat_id, operation_id, Json(payload), privacy.HELD_MESSAGE_TTL),
        )
        return cur.fetchone()[0]


def take_held(user_id, operation_id):
    """Reprend (et supprime) le message retenu : None s'il a expiré, été annulé ou appartient à un autre."""
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM held_messages WHERE user_id = %s AND operation_id = %s AND expires_at > now() RETURNING payload, chat_id",
            (user_id, operation_id),
        )
        row = cur.fetchone()
    return None if row is None else {"payload": row[0], "chat_id": row[1]}


def cancel_held(user_id, operation_id=None):
    """Annule un message retenu (ou tous ceux de l'utilisateur). Retourne le nombre supprimé."""
    with db.cursor() as cur:
        if operation_id is None:
            cur.execute("DELETE FROM held_messages WHERE user_id = %s", (user_id,))
        else:
            cur.execute("DELETE FROM held_messages WHERE user_id = %s AND operation_id = %s", (user_id, operation_id))
        return cur.rowcount


# --- Nettoyage et cohérence ---------------------------------------------------------
def purge_expired(keep_days=1):
    """Supprime les messages retenus expirés, les autorisations expirées depuis plus de `keep_days` jours,
    et celles dont le fichier n'existe plus. Les autorisations de conversation disparaissent déjà avec
    leur conversation (clé étrangère en cascade). Retourne le nombre de lignes supprimées par catégorie."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM held_messages WHERE expires_at <= now()")
        held = cur.rowcount
        cur.execute("DELETE FROM data_grants WHERE expires_at < now() - make_interval(days => %s)", (keep_days,))
        old = cur.rowcount
        cur.execute(
            "DELETE FROM data_grants g WHERE g.file_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM files f WHERE f.id = g.file_id)"
        )
        orphan_files = cur.rowcount
    return {"held": held, "grants": old, "orphan_file_grants": orphan_files}


def forget_user(user_id):
    """Efface réglages, autorisations et messages retenus d'un utilisateur (suppression de compte)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM held_messages WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM data_grants WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM privacy_settings WHERE user_id = %s", (user_id,))
