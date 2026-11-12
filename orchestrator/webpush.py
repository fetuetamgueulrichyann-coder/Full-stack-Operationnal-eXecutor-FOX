"""Notifications Web Push sans dépendance lourde : chiffrement RFC 8291 (aes128gcm) et signature VAPID (RFC 8292).

Seule la bibliothèque `cryptography` est nécessaire. Le contenu d'une notification est chiffré de bout en bout
pour l'appareil : le service de push du navigateur (Google, Mozilla, Apple, Microsoft) ne voit que du texte chiffré.
Les adresses d'abonnement viennent du navigateur de l'utilisateur : on n'écrit JAMAIS vers une adresse qui n'est
pas en HTTPS chez un service de push connu (protection contre l'envoi de requêtes vers des serveurs internes).
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
MAX_PAYLOAD = 3000  # octets de texte clair ; la limite des services de push est ~4 Ko
PUSH_HOST_SUFFIXES = (
    ".googleapis.com",  # Chrome, Edge, Android (fcm.googleapis.com)
    ".push.services.mozilla.com",  # Firefox
    ".push.apple.com",  # Safari, iPhone (web.push.apple.com)
    ".notify.windows.com",  # anciens Edge
)


def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text):
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_hosts_extra():
    return tuple(h.strip().lower() for h in os.environ.get("PUSH_ALLOWED_HOSTS", "").split(",") if h.strip())


def endpoint_allowed(endpoint):
    """Vrai seulement pour une adresse HTTPS d'un service de push connu (sans identifiants ni port étrange)."""
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
        return False
    if len(endpoint) > 2000:
        return False
    return any(host.endswith(s) or host == s.lstrip(".") for s in PUSH_HOST_SUFFIXES) or host in allowed_hosts_extra()


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _point(public_key):
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(plaintext, ua_public_b64, auth_b64, as_private=None, salt=None):
    """Corps chiffré (en-tête + enregistrement) à envoyer au service de push. `as_private`/`salt` : tests seulement."""
    if len(plaintext) > MAX_PAYLOAD:
        raise ValueError("notification trop longue")
    ua_public_bytes = b64d(ua_public_b64)
    auth_secret = b64d(auth_b64)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)
    server_key = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public_bytes = _point(server_key.public_key())
    salt = salt or os.urandom(16)

    secret = server_key.exchange(ec.ECDH(), ua_public)
    ikm = _hkdf(auth_secret, secret, b"WebPush: info\x00" + ua_public_bytes + as_public_bytes, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)  # 0x02 : dernier enregistrement, sans remplissage
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public_bytes)]) + as_public_bytes
    return header + ciphertext


# --- VAPID ---------------------------------------------------------------------------------------
def generate_vapid_keys():
    """(clé publique, clé privée) en base64url, à placer dans VAPID_PUBLIC_KEY et VAPID_PRIVATE_KEY."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64e(_point(key.public_key())), b64e(private)


def _private_key(private_b64):
    return ec.derive_private_key(int.from_bytes(b64d(private_b64), "big"), ec.SECP256R1())


def vapid_headers(endpoint, subject, public_b64, private_b64, now=None):
    parts = urlsplit(endpoint)
    header = b64e(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64e(json.dumps(
        {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now or time.time()) + 12 * 3600, "sub": subject},
        separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(_private_key(private_b64).sign(signing_input, ec.ECDSA(hashes.SHA256())))
    token = f"{header}.{claims}.{b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return {"Authorization": f"vapid t={token}, k={public_b64}"}


class Gone(Exception):
    """L'abonnement n'existe plus (désinstallé, permission retirée) : il faut l'oublier."""


class PushError(Exception):
    """Échec temporaire ou refus du service de push."""


def missing(env=None):
    """Noms des variables VAPID absentes de l'environnement du serveur (pour le diagnostic)."""
    env = os.environ if env is None else env
    return [name for name in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY") if not env.get(name)]


def configured(env=None):
    env = os.environ if env is None else env
    return bool(env.get("VAPID_PUBLIC_KEY") and env.get("VAPID_PRIVATE_KEY"))


def send(subscription, payload, ttl=3600, urgency="high", env=None, opener=None):
    """Envoie `payload` (dict) à un abonnement {endpoint, p256dh, auth}. Lève Gone ou PushError."""
    env = os.environ if env is None else env
    if not configured(env):
        raise PushError("notifications non configurées (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY)")
    endpoint = subscription["endpoint"]
    if not endpoint_allowed(endpoint):
        raise Gone("adresse d'abonnement refusée")
    body = encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), subscription["p256dh"], subscription["auth"])
    headers = {
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": urgency,
        **vapid_headers(endpoint, env.get("VAPID_SUBJECT") or "mailto:admin@example.com", env["VAPID_PUBLIC_KEY"], env["VAPID_PRIVATE_KEY"]),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=10) as response:
            if response.status >= 300:
                raise PushError(f"service de push : {response.status}")
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            raise Gone(str(exc.code)) from None
        raise PushError(f"service de push : {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PushError(f"service de push injoignable : {exc}") from None
