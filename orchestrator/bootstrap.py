"""Création du compte propriétaire au démarrage (idempotent).

Le propriétaire est l'utilisateur Telegram autorisé (ALLOWED_USER_ID). Au premier lancement :
- son compte est créé (rôle `owner`, lié à son identifiant Telegram) ;
- l'historique, les actions en attente et le journal d'audit existants, qui portaient son
  identifiant Telegram, sont rattachés à son compte. Aucune ligne n'est supprimée.

Aux lancements suivants, rien ne change (sauf définition du mot de passe web, voir ci-dessous).

Variables : ALLOWED_USER_ID (obligatoire), OWNER_USERNAME (défaut « owner »),
OWNER_PASSWORD (facultatif : active la connexion web du propriétaire ; appliqué seulement si le
compte n'a pas encore de mot de passe).
"""
import json
import logging
import os
import sys

import accounts
import db

log = logging.getLogger("bootstrap")

LEGACY_TABLES = ("chats", "conversations", "pending_actions", "audit_log")


def bootstrap_owner(telegram_id, username="owner", password=None):
    """Retourne une chaîne décrivant ce qui a été fait."""
    accounts.check_username(username)
    if password:
        accounts.check_password_policy(password)

    with db.cursor() as cur:
        cur.execute("SELECT id, telegram_id, password_hash FROM users WHERE role = 'owner'")
        owner = cur.fetchone()

        if owner is not None:
            owner_id, current_telegram, current_hash = owner
            if current_telegram != telegram_id:
                log.warning(
                    "Le propriétaire existant est lié à un autre identifiant Telegram que "
                    "ALLOWED_USER_ID : aucun changement (utilise admin.py si nécessaire)."
                )
            if password and not current_hash:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE id = %s",
                    (accounts.hash_password(password), owner_id),
                )
                return "mot de passe du propriétaire défini"
            return "propriétaire déjà présent"

        cur.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (username,))
        if cur.fetchone():
            raise RuntimeError(f"le nom d'utilisateur « {username} » est déjà pris : change OWNER_USERNAME")

        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, telegram_id)
            VALUES (%s, %s, 'owner', %s) RETURNING id
            """,
            (username, accounts.hash_password(password) if password else None, telegram_id),
        )
        owner_id = cur.fetchone()[0]

        moved = {}
        if owner_id != telegram_id:
            # Un autre compte porte déjà cet identifiant interne : le rattachement mélangerait
            # des historiques. On s'arrête sans rien déplacer.
            cur.execute("SELECT 1 FROM users WHERE id = %s AND id <> %s", (telegram_id, owner_id))
            if cur.fetchone():
                log.error("Rattachement de l'historique ignoré : conflit d'identifiant.")
            else:
                for table in LEGACY_TABLES:
                    cur.execute(
                        f"UPDATE {table} SET user_id = %s WHERE user_id = %s",  # noqa: S608 (noms fixes)
                        (owner_id, telegram_id),
                    )
                    moved[table] = cur.rowcount
        cur.execute(
            "INSERT INTO audit_log (user_id, event, details) VALUES (%s, 'owner_created', %s::jsonb)",
            (owner_id, json.dumps({"rattaches": moved})),
        )
    return f"propriétaire créé (id {owner_id}) ; lignes rattachées : {moved}"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw = os.environ.get("ALLOWED_USER_ID", "").strip()
    if not raw or not raw.lstrip("-").isdigit() or int(raw) == 0:
        log.warning("ALLOWED_USER_ID absent ou invalide : aucun compte propriétaire créé.")
        return 0
    try:
        outcome = bootstrap_owner(
            int(raw),
            os.environ.get("OWNER_USERNAME", "owner").strip() or "owner",
            os.environ.get("OWNER_PASSWORD") or None,
        )
    except (accounts.AuthError, RuntimeError) as exc:
        log.error("Création du propriétaire impossible : %s", getattr(exc, "message", exc))
        return 1
    log.info("Propriétaire : %s", outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
