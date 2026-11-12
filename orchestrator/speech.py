"""Voix de FOX : texte -> parole naturelle, via l'API de synthèse vocale de Gemini.

Le navigateur ne fait que lire le son reçu ; la clé Gemini reste sur le serveur. Le texte est
d'abord nettoyé (pas de symboles Markdown lus à voix haute) et borné.
"""
import base64
import io
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.request

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Modèles essayés dans l'ordre (le suivant est tenté si le précédent n'existe pas pour cette clé).
DEFAULT_MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts", "gemini-3.8-flash-lite-tts")
DEFAULT_VOICE = "Kore"
MAX_SPEECH_CHARS = 1200
TIMEOUT_SECONDS = 40
SAMPLE_RATE = 24000


class SpeechError(Exception):
    """Échec de la synthèse. `code` : voice_disabled | upstream_error | empty_audio."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get("VOICE_ENABLED", "1").strip() != "0" and bool(env.get("GEMINI_API_KEY", "").strip())


def voice_name(env=None):
    env = os.environ if env is None else env
    return (env.get("VOICE_NAME") or DEFAULT_VOICE).strip() or DEFAULT_VOICE


_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_MARKS = re.compile(r"[*_`#>|~]+")
_BULLET = re.compile(r"^\s*(?:[-+•]|\d+[.)])\s+", re.M)
_SPACES = re.compile(r"[ \t  ]+")


def clean_for_speech(text, limit=MAX_SPEECH_CHARS):
    """Texte lisible à voix haute : sans Markdown, sans code, sans adresses, borné à `limit` caractères."""
    out = _CODE_BLOCK.sub(" ", text or "")
    out = _LINK.sub(r"\1", out)
    out = _URL.sub(" ", out)
    out = _BULLET.sub("", out)
    out = _MARKS.sub("", out)
    out = _SPACES.sub(" ", out)
    out = re.sub(r"\s*\n\s*", ". ", out.strip())
    out = re.sub(r"([.!?:;])\s*\.\s*", r"\1 ", out).strip()
    if len(out) <= limit:
        return out
    cut = out[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 2 else cut).strip()


def wav_from_pcm(pcm, rate=SAMPLE_RATE):
    """Enveloppe du son brut (16 bits, mono) dans un fichier WAV lisible par un navigateur."""
    buf = io.BytesIO()
    buf.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE")
    buf.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
    buf.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
    return buf.getvalue()


def _request_body(text, voice, style):
    config = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}} if style == "prebuilt" else {"voiceConfig": {"voice": voice}}
    return {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": config},
    }


def _post(url, payload, key, opener):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with opener(req, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode())


# Un modèle dont le quota est épuisé (429) est mis de côté un moment : on ne le sollicite plus pour rien,
# et la voix passe tout de suite au modèle suivant (chaque modèle a son propre quota).
_cooling = {}
_lock = threading.Lock()


def cooldown_seconds(env=None):
    env = os.environ if env is None else env
    try:
        return max(5, int(env.get("VOICE_COOLDOWN_SECONDS", "120")))
    except ValueError:
        return 120


def reset():
    with _lock:
        _cooling.clear()


def _cooling_now(model):
    with _lock:
        until = _cooling.get(model, 0)
    return until > time.monotonic()


def _cool(model, seconds):
    with _lock:
        _cooling[model] = time.monotonic() + seconds


def synthesize(text, env=None, opener=None):
    """Retourne le son (WAV) de `text`. Lève SpeechError (code upstream_error, empty_audio, voice_disabled ou busy)."""
    env = os.environ if env is None else env
    opener = opener or urllib.request.urlopen
    if not enabled(env):
        raise SpeechError("voice_disabled", "la voix est désactivée")
    spoken = clean_for_speech(text)
    if not spoken:
        raise SpeechError("empty_audio", "rien à lire")
    key = env["GEMINI_API_KEY"].strip()
    configured = (env.get("VOICE_MODEL") or "").strip()
    models = (configured,) if configured else DEFAULT_MODELS  # un modèle choisi à la main n'est pas remplacé
    voice = voice_name(env)
    data = None
    last_status = None
    busy = 0
    for model in models:
        if _cooling_now(model):
            busy += 1
            continue
        url = ENDPOINT.format(model=model)
        for style in ("prebuilt", "simple"):  # deux écritures de la voix selon la version de l'API
            try:
                data = _post(url, _request_body(spoken, voice, style), key, opener)
                break
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                if exc.code == 400 and style == "prebuilt":
                    continue
                if exc.code == 429:  # quota de ce modèle épuisé : pause, puis modèle suivant
                    _cool(model, cooldown_seconds(env))
                    busy += 1
                    break
                if exc.code in (404, 500, 502, 503, 504):  # modèle absent ou en panne : modèle suivant
                    break
                raise SpeechError("upstream_error", f"synthèse refusée ({exc.code})") from exc
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                raise SpeechError("upstream_error", "synthèse indisponible") from exc
        if data is not None:
            break
    if data is None:
        if busy:
            raise SpeechError("busy", "tous les modèles de voix sont saturés pour le moment")
        raise SpeechError("upstream_error", f"synthèse refusée ({last_status})")
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        audio = base64.b64decode(part["data"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SpeechError("empty_audio", "réponse audio vide") from exc
    if not audio:
        raise SpeechError("empty_audio", "réponse audio vide")
    return audio if audio[:4] == b"RIFF" else wav_from_pcm(audio)
