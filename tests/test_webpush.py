"""Web Push : chiffrement validé sur le vecteur de test officiel de la RFC 8291 (annexe A) et sécurité des envois."""
import base64
import json
import urllib.error

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

import webpush

# RFC 8291, annexe A
PLAINTEXT = b"When I grow up, I want to be a watermelon"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
EXPECTED = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def test_chiffrement_conforme_au_vecteur_de_la_rfc():
    server_key = ec.derive_private_key(int.from_bytes(webpush.b64d(AS_PRIVATE), "big"), ec.SECP256R1())
    body = webpush.encrypt(PLAINTEXT, UA_PUBLIC, AUTH, as_private=server_key, salt=webpush.b64d(SALT))
    assert webpush.b64e(body) == EXPECTED


def test_deux_envois_identiques_donnent_des_corps_differents():
    ua = webpush.generate_vapid_keys()[0]
    auth = webpush.b64e(b"0123456789abcdef")
    assert webpush.encrypt(b"x", ua, auth) != webpush.encrypt(b"x", ua, auth)


def test_texte_trop_long_refuse():
    ua = webpush.generate_vapid_keys()[0]
    with pytest.raises(ValueError):
        webpush.encrypt(b"x" * 4000, ua, webpush.b64e(b"0123456789abcdef"))


def test_cles_vapid_valides_et_signature_verifiable():
    public, private = webpush.generate_vapid_keys()
    assert len(webpush.b64d(public)) == 65 and len(webpush.b64d(private)) == 32
    headers = webpush.vapid_headers("https://fcm.googleapis.com/fcm/send/abc", "mailto:a@b.fr", public, private, now=1_000_000)
    scheme, rest = headers["Authorization"].split(" ", 1)
    assert scheme == "vapid" and rest.endswith("k=" + public)
    token = rest.split("t=")[1].split(",")[0]
    h, c, s = token.split(".")
    claims = json.loads(webpush.b64d(c))
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1_000_000 + 43200, "sub": "mailto:a@b.fr"}
    raw = webpush.b64d(s)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64d(public))
    from cryptography.hazmat.primitives import hashes
    pub.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # lève une exception si la signature est fausse


@pytest.mark.parametrize("endpoint, ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
    ("https://web.push.apple.com/abc", True),
    ("https://wns2-par02p.notify.windows.com/?token=x", True),
    ("http://fcm.googleapis.com/x", False),
    ("https://localhost/x", False),
    ("https://127.0.0.1/x", False),
    ("https://169.254.169.254/latest/meta-data", False),
    ("https://orchestrator:5000/x", False),
    ("https://fcm.googleapis.com.evil.example/x", False),
    ("https://evil.example/fcm.googleapis.com", False),
    ("https://user:pw@fcm.googleapis.com/x", False),
    ("https://fcm.googleapis.com:8443/x", False),
    ("", False),
])
def test_adresses_d_abonnement(endpoint, ok):
    assert webpush.endpoint_allowed(endpoint) is ok


def test_hote_supplementaire_autorise_par_configuration(monkeypatch):
    monkeypatch.setenv("PUSH_ALLOWED_HOSTS", "push.example.org")
    assert webpush.endpoint_allowed("https://push.example.org/abc")


def sub(endpoint="https://fcm.googleapis.com/fcm/send/abc"):
    public, _ = webpush.generate_vapid_keys()
    return {"endpoint": endpoint, "p256dh": public, "auth": webpush.b64e(b"0123456789abcdef")}


def env():
    public, private = webpush.generate_vapid_keys()
    return {"VAPID_PUBLIC_KEY": public, "VAPID_PRIVATE_KEY": private, "VAPID_SUBJECT": "mailto:a@b.fr"}


class FakeResponse:
    def __init__(self, status=201):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_envoi_reussi_envoie_les_bons_en_tetes():
    seen = {}

    def opener(request, timeout):
        seen["url"], seen["headers"], seen["len"] = request.full_url, dict(request.header_items()), len(request.data)
        return FakeResponse(201)

    webpush.send(sub(), {"title": "t"}, env=env(), opener=opener)
    assert seen["url"].startswith("https://fcm.googleapis.com/")
    assert seen["headers"]["Content-encoding"] == "aes128gcm" and seen["headers"]["Authorization"].startswith("vapid t=")
    assert seen["len"] > 100


@pytest.mark.parametrize("code, error", [(404, webpush.Gone), (410, webpush.Gone), (429, webpush.PushError), (500, webpush.PushError)])
def test_codes_http_du_service_de_push(code, error):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)

    with pytest.raises(error):
        webpush.send(sub(), {"title": "t"}, env=env(), opener=opener)


def test_reseau_coupe_est_une_erreur_temporaire():
    def opener(request, timeout):
        raise urllib.error.URLError("coupé")

    with pytest.raises(webpush.PushError):
        webpush.send(sub(), {"title": "t"}, env=env(), opener=opener)


def test_aucun_envoi_vers_une_adresse_interdite():
    called = []
    with pytest.raises(webpush.Gone):
        webpush.send(sub("https://169.254.169.254/x"), {"title": "t"}, env=env(), opener=lambda r, timeout: called.append(r))
    assert called == []


def test_sans_cles_vapid_rien_ne_part():
    with pytest.raises(webpush.PushError):
        webpush.send(sub(), {"title": "t"}, env={}, opener=lambda r, timeout: FakeResponse())
