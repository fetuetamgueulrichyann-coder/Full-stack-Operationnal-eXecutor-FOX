"""Voix de FOX : nettoyage du texte, appel à la synthèse (simulé), route /voice/speak, plafonds par utilisateur."""
import base64
import io
import json
import struct
import urllib.error

import pytest

import speech
from test_app_commercial import HEADERS, OTHER, OWNER, alice, call, client, friend  # noqa: F401

PCM = b"\x01\x00" * 480


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reply(audio=PCM):
    data = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16", "data": base64.b64encode(audio).decode()}}]}}]}
    return FakeResponse(json.dumps(data).encode())


@pytest.fixture
def gemini(monkeypatch):
    """Simule l'API : enregistre les requêtes et répond par un son."""
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data.decode()))
        return reply()

    monkeypatch.setattr(speech.urllib.request, "urlopen", fake)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    return calls


# --- nettoyage -------------------------------------------------------------------------------
def test_markdown_n_est_pas_lu():
    assert speech.clean_for_speech("## Titre\n\n**Bonjour** [ici](https://x.example/a) :\n- un\n- deux") == "Titre. Bonjour ici : un. deux"


def test_code_et_adresses_retires():
    assert speech.clean_for_speech("Voici :\n```py\nprint(1)\n```\nVoir https://x.example/long fin") == "Voici : Voir fin"


def test_texte_borne_a_une_phrase_entiere():
    text = "Phrase une. " * 200
    out = speech.clean_for_speech(text, limit=100)
    assert len(out) <= 100 and out.endswith(".")


def test_texte_vide_ou_none():
    assert speech.clean_for_speech("") == "" and speech.clean_for_speech(None) == ""
    assert speech.clean_for_speech("***") == ""


# --- son -------------------------------------------------------------------------------------
def test_wav_valide():
    wav = speech.wav_from_pcm(PCM)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert struct.unpack("<I", wav[40:44])[0] == len(PCM)
    assert struct.unpack("<I", wav[24:28])[0] == 24000


def test_synthese_enveloppe_le_son_brut(gemini):
    assert speech.synthesize("Bonjour")[:4] == b"RIFF"
    assert gemini[0]["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert gemini[0]["contents"][0]["parts"][0]["text"] == "Bonjour"


def test_synthese_garde_un_wav_deja_complet(monkeypatch, gemini):
    wav = speech.wav_from_pcm(PCM)
    monkeypatch.setattr(speech.urllib.request, "urlopen", lambda req, timeout=None: reply(wav))
    assert speech.synthesize("Bonjour") == wav


def test_seconde_ecriture_de_la_voix_si_400(monkeypatch, gemini):
    seen = []

    def fake(req, timeout=None):
        body = json.loads(req.data.decode())
        seen.append(body["generationConfig"]["speechConfig"])
        if len(seen) == 1:
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
        return reply()

    monkeypatch.setattr(speech.urllib.request, "urlopen", fake)
    assert speech.synthesize("Bonjour")[:4] == b"RIFF"
    assert "prebuiltVoiceConfig" in seen[0]["voiceConfig"] and seen[1]["voiceConfig"] == {"voice": "Kore"}


@pytest.mark.parametrize("code", [403, 429, 500])
def test_autres_erreurs_http_sans_nouvel_essai(monkeypatch, gemini, code):
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)

    monkeypatch.setattr(speech.urllib.request, "urlopen", fake)
    with pytest.raises(speech.SpeechError) as exc:
        speech.synthesize("Bonjour")
    assert exc.value.code == "upstream_error"


def test_reseau_coupe_et_reponse_vide(monkeypatch, gemini):
    def down(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(speech.urllib.request, "urlopen", down)
    with pytest.raises(speech.SpeechError):
        speech.synthesize("Bonjour")
    monkeypatch.setattr(speech.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(b'{"candidates": []}'))
    with pytest.raises(speech.SpeechError) as exc:
        speech.synthesize("Bonjour")
    assert exc.value.code == "empty_audio"


def test_voix_et_modele_configurables(monkeypatch, gemini):
    monkeypatch.setenv("VOICE_NAME", "Puck")
    monkeypatch.setenv("VOICE_MODEL", "mon-modele")
    seen = []
    monkeypatch.setattr(speech.urllib.request, "urlopen", lambda req, timeout=None: (seen.append((req.full_url, json.loads(req.data.decode()))), reply())[1])
    speech.synthesize("Bonjour")
    assert "models/mon-modele:" in seen[0][0]
    assert seen[0][1]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"


def test_la_cle_n_est_jamais_dans_l_adresse(gemini, monkeypatch):
    urls = []
    monkeypatch.setattr(speech.urllib.request, "urlopen", lambda req, timeout=None: (urls.append(req.full_url), reply())[1])
    speech.synthesize("Bonjour")
    assert "test-key" not in urls[0]


# --- API -------------------------------------------------------------------------------------
def test_config(client, gemini):
    got = call(client, "GET", "/voice/config").get_json()
    assert got["enabled"] is True and got["voice"] == "Kore" and got["used"] == 0 and got["daily_limit"] == 40000


def test_config_testeur_a_un_plafond_plus_bas(client, friend, gemini):
    assert call(client, "GET", "/voice/config", telegram_id=OTHER).get_json()["daily_limit"] == 4000


def test_config_desactivee(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "0")
    assert call(client, "GET", "/voice/config").get_json()["enabled"] is False
    assert call(client, "POST", "/voice/speak", text="Bonjour").status_code == 409


def test_parler_renvoie_un_wav(client, gemini):
    r = call(client, "POST", "/voice/speak", text="**Bonjour** Yann")
    assert r.status_code == 200 and r.mimetype == "audio/wav" and r.data[:4] == b"RIFF"
    assert r.headers["Cache-Control"] == "no-store"
    assert gemini[0]["contents"][0]["parts"][0]["text"] == "Bonjour Yann"


def test_parler_compte_les_caracteres(client, gemini):
    call(client, "POST", "/voice/speak", text="Bonjour Yann")
    assert call(client, "GET", "/voice/config").get_json()["used"] == len("Bonjour Yann")


@pytest.mark.parametrize("text", ["", "   ", "***", None, 5, ["a"]])
def test_texte_invalide(client, gemini, text):
    assert call(client, "POST", "/voice/speak", text=text).status_code == 400
    assert gemini == []


def test_plafond_journalier_par_utilisateur(client, friend, gemini, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "TESTER_VOICE_CHARS_PER_DAY", 25)
    assert call(client, "POST", "/voice/speak", telegram_id=OTHER, text="Bonjour tout le monde").status_code == 200
    r = call(client, "POST", "/voice/speak", telegram_id=OTHER, text="Encore une phrase")
    assert r.status_code == 429 and r.get_json()["code"] == "voice_quota"
    # le propriétaire n'est pas touché
    assert call(client, "POST", "/voice/speak", text="Encore une phrase").status_code == 200


def test_un_echec_rend_les_caracteres(client, gemini, monkeypatch):
    def down(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(speech.urllib.request, "urlopen", down)
    r = call(client, "POST", "/voice/speak", text="Bonjour Yann")
    assert r.status_code == 502 and r.get_json()["code"] == "voice_unavailable"
    assert call(client, "GET", "/voice/config").get_json()["used"] == 0
    assert "test-key" not in r.get_data(as_text=True)


def test_sans_connexion(client, gemini):
    assert client.post("/voice/speak", json={"text": "Bonjour"}).status_code == 401
    assert client.get("/voice/config").status_code == 401
    assert gemini == []


def test_modele_absent_on_essaie_le_suivant(monkeypatch, gemini):
    urls = []

    def fake(req, timeout=None):
        urls.append(req.full_url)
        if "gemini-3.1-flash-tts-preview" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, None)
        return reply()

    monkeypatch.setattr(speech.urllib.request, "urlopen", fake)
    monkeypatch.delenv("VOICE_MODEL", raising=False)
    assert speech.synthesize("Bonjour")[:4] == b"RIFF"
    assert "gemini-3.1-flash-tts-preview" in urls[0] and "gemini-2.5-flash-preview-tts" in urls[1]


def test_modele_choisi_a_la_main_n_est_pas_remplace(monkeypatch, gemini):
    urls = []

    def fake(req, timeout=None):
        urls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "no", {}, None)

    monkeypatch.setattr(speech.urllib.request, "urlopen", fake)
    monkeypatch.setenv("VOICE_MODEL", "mon-modele")
    with pytest.raises(speech.SpeechError):
        speech.synthesize("Bonjour")
    assert len(urls) == 1


def test_tous_les_modeles_absents(monkeypatch, gemini):
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "no", {}, None)

    monkeypatch.setattr(speech.urllib.request, "urlopen", fake)
    monkeypatch.delenv("VOICE_MODEL", raising=False)
    with pytest.raises(speech.SpeechError) as exc:
        speech.synthesize("Bonjour")
    assert exc.value.code == "upstream_error"
