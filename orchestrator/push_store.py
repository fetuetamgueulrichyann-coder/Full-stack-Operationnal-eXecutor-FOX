"""Abonnements aux notifications push (un par appareil et navigateur). Toujours filtré par utilisateur."""
import os

from db import cursor

MAX_PER_USER = int(os.environ.get("MAX_PUSH_DEVICES", "10"))
MAX_FAILURES = 5  # au-delà, l'appareil est oublié


class TooManyDevices(Exception):
    pass


def add(user_id, endpoint, p256dh, auth):
    """Enregistre (ou met à jour) un appareil. Un même appareil ne peut appartenir qu'à un utilisateur à la fois."""
    with cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"push:{user_id}",))
        cur.execute("SELECT user_id FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT count(*) FROM push_subscriptions WHERE user_id = %s", (user_id,))
            if MAX_PER_USER and cur.fetchone()[0] >= MAX_PER_USER:
                raise TooManyDevices(MAX_PER_USER)
        cur.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh, "
            "auth = EXCLUDED.auth, failures = 0",
            (user_id, endpoint, p256dh, auth),
        )


def remove(user_id, endpoint):
    with cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE user_id = %s AND endpoint = %s", (user_id, endpoint))
        return cur.rowcount > 0


def forget(endpoint):
    with cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))


def for_user(user_id):
    with cursor() as cur:
        cur.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = %s ORDER BY id", (user_id,))
        return [{"endpoint": r[0], "p256dh": r[1], "auth": r[2]} for r in cur.fetchall()]


def count(user_id):
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM push_subscriptions WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]


def mark_ok(endpoint):
    with cursor() as cur:
        cur.execute("UPDATE push_subscriptions SET last_ok_at = now(), failures = 0 WHERE endpoint = %s", (endpoint,))


def mark_failed(endpoint):
    """Compte un échec ; l'appareil est oublié après MAX_FAILURES échecs de suite."""
    with cursor() as cur:
        cur.execute("UPDATE push_subscriptions SET failures = failures + 1 WHERE endpoint = %s RETURNING failures", (endpoint,))
        row = cur.fetchone()
        if row and row[0] >= MAX_FAILURES:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
