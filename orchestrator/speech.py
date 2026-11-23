"""Voix de FOX : texte -> parole naturelle, via Gemini (par défaut) ou ElevenLabs.

Le navigateur ne fait que lire le son reçu ; les clés restent sur le serveur. VOICE_PROVIDERS (ex. « elevenlabs,gemini »)
fixe l'ordre : si un moteur est saturé ou en panne, le suivant prend le relais. Le texte est
d'abord nettoyé (pas de symboles Markdown lus à voix haute) et borné.
"""
import base64
import calendar
import io
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
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


PROVIDERS = ("gemini", "elevenlabs")
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
ELEVEN_MODELS = ("eleven_flash_v2_5", "eleven_multilingual_v2")


def providers(env=None):
    """Moteurs de voix à essayer, dans l'ordre, ceux dont la clé est présente (Gemini par défaut)."""
    env = os.environ if env is None else env
    wanted = [p.strip().lower() for p in (env.get("VOICE_PROVIDERS") or env.get("VOICE_PROVIDER") or "gemini").split(",")]
    keys = {"gemini": env.get("GEMINI_API_KEY", ""), "elevenlabs": env.get("ELEVENLABS_API_KEY", "")}
    out = []
    for name in wanted:
        if name in PROVIDERS and keys[name].strip() and name not in out:
            if name == "elevenlabs" and not env.get("ELEVENLABS_VOICE_ID", "").strip():
                continue
            out.append(name)
    return out


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get("VOICE_ENABLED", "1").strip() != "0" and bool(providers(env))


# Voix prédéfinies de Gemini (nom, caractère). Liste fermée : un nom inconnu n'est jamais envoyé à l'API.
GEMINI_VOICES = (
    ("Kore", "Ferme"), ("Puck", "Dynamique"), ("Charon", "Informative"), ("Zephyr", "Lumineuse"),
    ("Fenrir", "Enthousiaste"), ("Leda", "Jeune"), ("Orus", "Ferme"), ("Aoede", "Aérienne"),
    ("Callirrhoe", "Détendue"), ("Autonoe", "Lumineuse"), ("Enceladus", "Soufflée"), ("Iapetus", "Claire"),
    ("Umbriel", "Détendue"), ("Algieba", "Douce"), ("Despina", "Douce"), ("Erinome", "Claire"),
    ("Algenib", "Rauque"), ("Rasalgethi", "Informative"), ("Laomedeia", "Enjouée"), ("Achernar", "Douce"),
    ("Alnilam", "Ferme"), ("Schedar", "Posée"), ("Gacrux", "Mature"), ("Pulcherrima", "Directe"),
    ("Achird", "Amicale"), ("Zubenelgenubi", "Décontractée"), ("Vindemiatrix", "Délicate"), ("Sadachbia", "Vive"),
    ("Sadaltager", "Savante"), ("Sulafat", "Chaleureuse"),
)
VOICE_NAMES = tuple(name for name, _ in GEMINI_VOICES)
MIN_SPEED, MAX_SPEED = 0.7, 1.5


def valid_voice(name):
    return name if isinstance(name, str) and name in VOICE_NAMES else None


def valid_speed(value):
    """Vitesse de lecture entre 0,7 et 1,5, arrondie à 0,05 ; None si invalide."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (MIN_SPEED <= value <= MAX_SPEED):
        return None
    return round(round(value / 0.05) * 0.05, 2)


def voice_name(env=None):
    env = os.environ if env is None else env
    return (env.get("VOICE_NAME") or DEFAULT_VOICE).strip() or DEFAULT_VOICE


_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_MARKS = re.compile(r"[*_`#>|~]+")
_BULLET = re.compile(r"^\s*(?:[-+•]|\d+[.)])\s+", re.M)
_SPACES = re.compile(r"[ \t  ]+")


# --- Dates, heures et montants : écrits pour l'œil, dits pour l'oreille ----------------------------------
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
_ISO_DATE = re.compile(r"(?<![\d/.-])(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?![\d-])")
_FULL_DATE = re.compile(r"(?<![\d/.-])(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})(?![\d/])")
_SHORT_DATE = re.compile(r"(?<![\d/.:-])(\d{2})/(0[1-9]|1[0-2])(?![\d/:])")
_TIME_RANGE = re.compile(r"(?<!\d)(\d{1,2})(?::|\s?h\s?)(\d{2})\s*[-–—]\s*(\d{1,2})(?::|\s?h\s?)(\d{2})(?!\d)")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?![\d:])")
_TIME_H = re.compile(r"(?<!\d)0?(\d{1,2})\s?h\s?(\d{2})?(?!\w)")
_CFA = re.compile(r"\s*(?:F\s?CFA|FCFA|XAF|XOF)\b", re.I)


def _say_date(day, month, year=None):
    day, month = int(day), int(month)
    if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(2024, month)[1]):  # 29 février accepté (année inconnue)
        return None
    text = ("1er" if day == 1 else str(day)) + " " + MONTHS[month - 1]
    if year is not None:
        year = int(year)
        text += " " + str(year if year >= 100 else 2000 + year)
    return text


def _say_time(hour, minute):
    hour, minute = int(hour), int(minute)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour == 0 and minute == 0:
        return "minuit"
    return f"{hour} h" + (f" {minute:02d}" if minute else "")


def _sub_date(match):
    spoken = _say_date(match.group(3), match.group(2), match.group(1))
    if spoken is None:
        return match.group(0)
    if match.group(4) is not None:
        when = _say_time(match.group(4), match.group(5))
        if when:
            spoken += " à " + when
    return spoken


def _sub_full_date(match):
    return _say_date(match.group(1), match.group(3), match.group(4)) or match.group(0)


def _sub_range(match):
    start, end = _say_time(match.group(1), match.group(2)), _say_time(match.group(3), match.group(4))
    return f"de {start} à {end}" if start and end else match.group(0)


def _sub_time(match):
    return _say_time(match.group(1), match.group(2)) or match.group(0)


def _sub_time_h(match):
    hour, minute = match.group(1), match.group(2)
    if int(hour) > 24 or (minute and int(minute) > 59):
        return match.group(0)
    return f"{int(hour)} h" + (f" {minute}" if minute else "")


def speak_numbers(text):
    """Réécrit dates, heures et montants comme on les dit : « 21/11/2026 08:00-09:00 » → « 21 novembre 2026 de 8 h à 9 h »."""
    out = _ISO_DATE.sub(_sub_date, text)
    out = _FULL_DATE.sub(_sub_full_date, out)
    out = _SHORT_DATE.sub(lambda m: _say_date(m.group(1), m.group(2)) or m.group(0), out)
    out = _TIME_RANGE.sub(_sub_range, out)
    out = _TIME.sub(_sub_time, out)
    out = _TIME_H.sub(_sub_time_h, out)
    out = _CFA.sub(" francs CFA", out)
    out = re.sub(r"\s*€", " euros", out)
    out = re.sub(r"\s*%", " pour cent", out)
    out = re.sub(r"\s*°C\b", " degrés", out)
    out = re.sub(r"\bN°\s*", "numéro ", out)
    out = re.sub(r"\bMme\b\.?", "madame", out)
    out = re.sub(r"\bM\.(?=\s+[A-ZÉÈ])", "monsieur", out)
    return out


def clean_for_speech(text, limit=MAX_SPEECH_CHARS):
    """Texte lisible à voix haute : sans Markdown, sans code, sans adresses, borné à `limit` caractères."""
    out = _CODE_BLOCK.sub(" ", text or "")
    out = _LINK.sub(r"\1", out)
    out = _URL.sub(" ", out)
    out = speak_numbers(out)
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


def _google_message(exc):
    """Message d'erreur renvoyé par Google (sans jamais contenir la clé), court."""
    try:
        body = exc.read().decode("utf-8", "replace") if exc.fp is not None else ""
        text = json.loads(body).get("error", {}).get("message", "")
    except (ValueError, AttributeError, OSError, TypeError):
        text = ""
    return re.sub(r"\s+", " ", str(text))[:160]


def _audio_of(data):
    """(son brut, explication) d'une réponse Gemini ; son vide si Google n'a rien renvoyé."""
    notes = []
    if not isinstance(data, dict):
        return b"", "réponse illisible"
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        notes.append(f"texte refusé : {block}")
    for cand in data.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        for part in (cand.get("content") or {}).get("parts") or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            try:
                audio = base64.b64decode(inline.get("data") or "")
            except (ValueError, TypeError):
                audio = b""
            if audio:
                return audio, ""
            if part.get("text"):
                notes.append("texte reçu : " + re.sub(r"\s+", " ", str(part["text"]))[:80])
        if cand.get("finishReason"):
            notes.append(f"fin : {cand['finishReason']}")
    return b"", ", ".join(notes) or "aucun contenu"


def _gemini(spoken, env, opener, voice_override=None):
    """Son (WAV) de `spoken` par Gemini : nouvel essai si la réponse est vide, puis modèle suivant."""
    key = env["GEMINI_API_KEY"].strip()
    configured = (env.get("VOICE_MODEL") or "").strip()
    models = (configured,) if configured else DEFAULT_MODELS  # un modèle choisi à la main n'est pas remplacé
    voice = valid_voice(voice_override) or voice_name(env)
    busy = 0
    empties = 0
    notes = []
    for model in models:
        if _cooling_now(model):
            busy += 1
            continue
        url = ENDPOINT.format(model=model)
        style = "prebuilt"
        for _attempt in range(3):
            try:
                data = _post(url, _request_body(spoken, voice, style), key, opener)
            except urllib.error.HTTPError as exc:
                if exc.code == 400 and style == "prebuilt":  # autre écriture de la voix
                    style = "simple"
                    continue
                if exc.code == 429:  # quota de ce modèle épuisé : pause, puis modèle suivant
                    _cool(model, cooldown_seconds(env))
                    busy += 1
                    break
                notes.append(f"{model} : HTTP {exc.code} {_google_message(exc)}".strip())
                break  # 400/401/403/404/5xx : modèle suivant
            except (TimeoutError, ValueError) as exc:
                notes.append(f"{model} : {type(exc).__name__}")
                break
            except (urllib.error.URLError, OSError) as exc:
                raise SpeechError("upstream_error", f"synthèse indisponible ({type(exc).__name__})") from exc
            audio, why = _audio_of(data)
            if audio:
                return audio if audio[:4] == b"RIFF" else wav_from_pcm(audio)
            empties += 1
            notes.append(f"{model} : réponse sans audio ({why})")
            # une réponse vide arrive parfois par à-coups : un seul nouvel essai avant le modèle suivant
            if sum(1 for n in notes if n.startswith(model + " : réponse sans audio")) >= 2:
                break
    detail = " ; ".join(notes)
    if busy and not empties:
        raise SpeechError("busy", "tous les modèles de voix sont saturés pour le moment")
    if empties:
        raise SpeechError("empty_audio", f"réponse audio vide — {detail}")
    raise SpeechError("upstream_error", f"synthèse refusée — {detail}")


def _elevenlabs(spoken, env, opener):
    """Son (MP3) de `spoken` par ElevenLabs. Les clés et l'identifiant de voix viennent de .env."""
    if _cooling_now("elevenlabs"):
        raise SpeechError("busy", "ElevenLabs est en pause")
    key = env["ELEVENLABS_API_KEY"].strip()
    voice = env["ELEVENLABS_VOICE_ID"].strip()
    configured = (env.get("ELEVENLABS_MODEL") or "").strip()
    models = (configured,) if configured else ELEVEN_MODELS
    last_status = None
    for model in models:
        req = urllib.request.Request(
            ELEVEN_URL.format(voice=urllib.parse.quote(voice, safe="")),
            data=json.dumps({"text": spoken, "model_id": model}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Accept": "audio/mpeg", "xi-api-key": key},
        )
        try:
            with opener(req, timeout=TIMEOUT_SECONDS) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            if exc.code == 429:  # quota ou trop de demandes : pause
                _cool("elevenlabs", cooldown_seconds(env))
                raise SpeechError("busy", "ElevenLabs est saturé") from exc
            if exc.code in (401, 402, 403):  # clé refusée ou voix non permise par l'offre : inutile d'insister
                _cool("elevenlabs", 600)
                raise SpeechError("upstream_error", f"ElevenLabs refuse la demande ({exc.code})") from exc
            if exc.code in (400, 404, 422):  # modèle inconnu : modèle suivant
                continue
            raise SpeechError("upstream_error", f"ElevenLabs en erreur ({exc.code})") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise SpeechError("upstream_error", "ElevenLabs indisponible") from exc
        if not audio:
            raise SpeechError("empty_audio", "réponse audio vide")
        return audio
    raise SpeechError("upstream_error", f"ElevenLabs refuse la demande ({last_status})")


def synthesize_audio(text, env=None, opener=None, voice=None):
    """Retourne (son, type MIME, moteur). Essaie les moteurs dans l'ordre ; lève SpeechError."""
    env = os.environ if env is None else env
    opener = opener or urllib.request.urlopen
    if env.get("VOICE_ENABLED", "1").strip() == "0":
        raise SpeechError("voice_disabled", "la voix est désactivée")
    chain = providers(env)
    if not chain:
        raise SpeechError("voice_disabled", "aucun moteur de voix n'est configuré")
    spoken = clean_for_speech(text)
    if not spoken:
        raise SpeechError("empty_audio", "rien à lire")
    last = None
    for name in chain:
        try:
            if name == "elevenlabs":
                return _elevenlabs(spoken, env, opener), "audio/mpeg", name
            return _gemini(spoken, env, opener, voice), "audio/wav", name
        except SpeechError as exc:
            if last is None or exc.code == "busy":
                last = exc
    raise last


def synthesize(text, env=None, opener=None):
    """Compatibilité : le son seul."""
    return synthesize_audio(text, env, opener)[0]
