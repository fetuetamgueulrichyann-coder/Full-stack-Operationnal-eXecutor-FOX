"""Comptes, mots de passe, sessions et codes d'invitation.

Principes de sécurité :
- les mots de passe sont hachés avec scrypt (bibliothèque standard), jamais stockés en clair ;
- les jetons de session et les codes d'invitation sont aléatoires et seul leur SHA-256 est
  stocké : une fuite de la base ne permet pas de s'en servir ;
- les essais de connexion sont limités (par nom d'utilisateur et par adresse IP) ;
- les messages d'erreur ne révèlent pas si un nom d'utilisateur existe.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets

import db

SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "7"))
LOGIN_WINDOW_MINUTES = 15
MAX_FAILED_PER_USERNAME = int(os.environ.get("LOGIN_MAX_FAILED_PER_USERNAME", "5"))
MAX_FAILED_PER_IP = int(os.environ.get("LOGIN_MAX_FAILED_PER_IP", "20"))
MAX_FAILED_REGISTER_PER_IP = 10
REGISTER_KEY = "__inscription__"
ATTEMPTS_RETENTION_DAYS = 30

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0/O/1/I

ROLE_OWNER = "owner"
ROLE_TESTER = "tester"


class AuthError(Exception):
    """Erreur d'authentification ou d'inscription. `code` est un identifiant stable."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --- Mots de passe -----------------------------------------------------------
def _b64(data):
    return base64.b64encode(data).decode("ascii")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# Calculé une fois : sert à dépenser le même temps quand l'utilisateur n'existe pas.
_DUMMY_HASH = hash_password("mot-de-passe-factice")


def check_password_policy(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            "password_weak", f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError("password_weak", "Mot de passe trop long.")


def check_username(username):
    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        raise AuthError(
            "username_invalid",
            "Le nom d'utilisateur doit contenir 3 à 32 caractères : lettres, chiffres, _ . -",
        )


def sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- Codes d'invitation ------------------------------------------------------
def create_invitation(days=7, max_uses=1, note=None):
    """Crée un code d'invitation et le retourne EN CLAIR (affiché une seule fois)."""
    if days <= 0 or max_uses <= 0:
        raise ValueError("days et max_uses doivent être positifs")
    groups = ["".join(secrets.choice(INVITE_ALPHABET) for _ in range(4)) for _ in range(3)]
    code = "FOX-" + "-".join(groups)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invitation_codes (code_hash, max_uses, note, expires_at)
            VALUES (%s, %s, %s, now() + make_interval(days => %s))
            """,
            (sha256(normalize_code(code)), max_uses, note, days),
        )
    return code


def normalize_code(code):
    return re.sub(r"[\s-]", "", str(code or "")).upper()


def revoke_invitation(invitation_id):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE invitation_codes SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
            (invitation_id,),
        )
        return cur.rowcount == 1


def list_invitations():
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, note, uses, max_uses, expires_at, revoked_at
            FROM invitation_codes ORDER BY id
            """
        )
        return cur.fetchall()


# --- Inscription -------------------------------------------------------------
def register(code, username, password):
    """Crée un compte testeur avec un code d'invitation valide. Retourne l'utilisateur."""
    check_username(username)
    check_password_policy(password)
    password_hash = hash_password(password)  # avant la transaction : le calcul est lent
    normalized = normalize_code(code)
    if not normalized:
        raise AuthError("invitation_invalid", "Code d'invitation invalide ou expiré.")

    with db.cursor() as cur:
        # Verrou de ligne : deux inscriptions simultanées ne peuvent pas dépasser max_uses.
        cur.execute(
            """
            SELECT id FROM invitation_codes
            WHERE code_hash = %s AND revoked_at IS NULL AND expires_at > now() AND uses < max_uses
            FOR UPDATE
            """,
            (sha256(normalized),),
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("invitation_invalid", "Code d'invitation invalide ou expiré.")
        invitation_id = row[0]

        cur.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (username,))
        if cur.fetchone():
            raise AuthError("username_taken", "Ce nom d'utilisateur n'est pas disponible.")

        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, invitation_id)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (username, password_hash, ROLE_TESTER, invitation_id),
        )
        user_id = cur.fetchone()[0]
        cur.execute("UPDATE invitation_codes SET uses = uses + 1 WHERE id = %s", (invitation_id,))
    return {"id": user_id, "username": username, "role": ROLE_TESTER}


def register_from_ip(code, username, password, ip):
    """Inscription avec limitation des essais de code d'invitation par adresse IP."""
    ip = str(ip or "inconnue")[:64]
    with db.cursor() as cur:
        _, by_ip = _recent_failures(cur, REGISTER_KEY, ip)
    if by_ip >= MAX_FAILED_REGISTER_PER_IP:
        raise AuthError("too_many_attempts", "Trop de tentatives. Réessaie dans quelques minutes.")
    try:
        return register(code, username, password)
    except AuthError as exc:
        if exc.code == "invitation_invalid":
            _record_attempt(REGISTER_KEY, ip, False)
        raise


# --- Connexion et sessions ---------------------------------------------------
def _recent_failures(cur, username_key, ip):
    cur.execute(
        """
        SELECT
          count(*) FILTER (WHERE username_key = %s),
          count(*) FILTER (WHERE ip = %s)
        FROM auth_attempts
        WHERE ok = false AND created_at > now() - make_interval(mins => %s)
          AND (username_key = %s OR ip = %s)
        """,
        (username_key, ip, LOGIN_WINDOW_MINUTES, username_key, ip),
    )
    return cur.fetchone()


def _record_attempt(username_key, ip, ok):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO auth_attempts (username_key, ip, ok) VALUES (%s, %s, %s)",
            (username_key, ip, ok),
        )


def login(username, password, ip):
    """Vérifie les identifiants et ouvre une session. Retourne (jeton, utilisateur)."""
    username_key = str(username or "").strip().lower()[:64]
    ip = str(ip or "inconnue")[:64]

    with db.cursor() as cur:
        by_user, by_ip = _recent_failures(cur, username_key, ip)
    if by_user >= MAX_FAILED_PER_USERNAME or by_ip >= MAX_FAILED_PER_IP:
        raise AuthError(
            "too_many_attempts", "Trop de tentatives. Réessaie dans quelques minutes."
        )

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, username, role, password_hash, disabled_at
            FROM users WHERE lower(username) = %s
            """,
            (username_key,),
        )
        row = cur.fetchone()

    stored = row[3] if row and row[3] else _DUMMY_HASH
    valid = verify_password(password if isinstance(password, str) else "", stored)
    if not row or not row[3] or row[4] is not None or not valid:
        _record_attempt(username_key, ip, False)
        raise AuthError("invalid_credentials", "Nom d'utilisateur ou mot de passe incorrect.")

    token = secrets.token_urlsafe(32)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (token_hash, user_id, expires_at)
            VALUES (%s, %s, now() + make_interval(days => %s))
            """,
            (sha256(token), row[0], SESSION_DAYS),
        )
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (row[0],))
        cur.execute("DELETE FROM sessions WHERE expires_at <= now()")
        cur.execute(
            "DELETE FROM auth_attempts WHERE created_at < now() - make_interval(days => %s)",
            (ATTEMPTS_RETENTION_DAYS,),
        )
    _record_attempt(username_key, ip, True)
    return token, {"id": row[0], "username": row[1], "role": row[2]}


def user_from_session(token):
    """Retourne l'utilisateur d'une session valide, sinon None."""
    if not token:
        return None
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.username, u.role
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > now() AND u.disabled_at IS NULL
            """,
            (sha256(token),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s", (sha256(token),))
    return {"id": row[0], "username": row[1], "role": row[2]}


def logout(token):
    if not token:
        return
    with db.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token_hash = %s", (sha256(token),))


# --- Utilisateurs ------------------------------------------------------------
def user_from_telegram(telegram_id):
    """Utilisateur actif lié à un identifiant Telegram, sinon None."""
    try:
        telegram_id = int(telegram_id)
    except (TypeError, ValueError):
        return None
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, username, role FROM users WHERE telegram_id = %s AND disabled_at IS NULL",
            (telegram_id,),
        )
        row = cur.fetchone()
    return None if row is None else {"id": row[0], "username": row[1], "role": row[2]}


def set_disabled(username, disabled):
    """Désactive ou réactive un compte. La désactivation ferme ses sessions."""
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE users SET disabled_at = CASE WHEN %s THEN now() ELSE NULL END
            WHERE lower(username) = lower(%s) RETURNING id, role
            """,
            (disabled, username),
        )
        row = cur.fetchone()
        if row is None:
            return False
        if disabled:
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (row[0],))
    return True


def set_password(username, password):
    check_password_policy(password)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE lower(username) = lower(%s) RETURNING id",
            (hash_password(password), username),
        )
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (row[0],))  # ferme les sessions
    return True


def list_users():
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, username, role, telegram_id, created_at, last_login_at, disabled_at
            FROM users ORDER BY id
            """
        )
        return cur.fetchall()
