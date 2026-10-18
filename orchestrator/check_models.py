"""Teste la disponibilité de modèles Gemini avec ta clé, sans passer par Telegram.

Usage (dans le conteneur) :
    python check_models.py                      # modèle configuré + deux modèles Flash-Lite
    python check_models.py gemini-3.8-flash     # un modèle précis

Le secours Groq est aussi vérifié si GROQ_API_KEY est défini : le modèle configuré (GROQ_MODEL)
doit figurer dans la liste de ceux que ton compte peut utiliser, et répondre à une question simple.
La clé n'est jamais affichée.

Chaque modèle reçoit 2 requêtes : une question simple, puis une question qui doit
déclencher un outil (function calling), comme dans FOX.
"""
import os
import sys
import time

import httpx

from google import genai
from google.genai import errors, types

DEFAULT_MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
MODELS = sys.argv[1:] or DEFAULT_MODELS

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"],
    http_options=types.HttpOptions(timeout=30000),
)

TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="lire_agenda",
            description="Liste les événements de l'agenda à partir d'une date.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "date_debut": {"type": "STRING", "description": "Date ISO, ex. 2026-10-15"}
                },
                "required": ["date_debut"],
            },
        )
    ]
)


def call(model, contents, config=None):
    start = time.monotonic()
    try:
        response = client.models.generate_content(model=model, contents=contents, config=config)
    except errors.APIError as exc:
        return None, time.monotonic() - start, f"ERREUR {exc.code} {exc.status}"
    except Exception as exc:
        return None, time.monotonic() - start, f"ERREUR {type(exc).__name__}"
    return response, time.monotonic() - start, None


for model in MODELS:
    print(f"\n== {model}")

    response, seconds, error = call(model, "Réponds uniquement par le mot OK.")
    if error:
        print(f"   texte simple : {error} ({seconds:.1f} s)")
    else:
        print(f"   texte simple : réponse reçue en {seconds:.1f} s -> {(response.text or '').strip()[:40]!r}")

    config = types.GenerateContentConfig(
        tools=[TOOL],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    response, seconds, error = call(
        model, "Nous sommes le 2026-10-14. Qu'ai-je dans mon agenda demain ?", config
    )
    if error:
        print(f"   outil        : {error} ({seconds:.1f} s)")
    else:
        names = [c.name for c in (response.function_calls or [])]
        verdict = "outil appelé " + ", ".join(names) if names else "aucun appel d'outil"
        print(f"   outil        : {verdict} ({seconds:.1f} s)")


# --- Secours Groq ---------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "").strip()
GROQ_BASE = "https://api.groq.com/openai/v1"
NON_CHAT = ("whisper", "orpheus", "guard", "safeguard", "tts")

print("\n== Groq (secours)")
if not GROQ_API_KEY:
    print("   GROQ_API_KEY non défini : secours désactivé.")
else:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        listing = httpx.get(f"{GROQ_BASE}/models", headers=headers, timeout=20)
        listing.raise_for_status()
        available = sorted(m["id"] for m in listing.json()["data"])
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(f"   liste des modèles : ERREUR {type(exc).__name__} (statut={status})")
    else:
        chat_models = [m for m in available if not any(word in m for word in NON_CHAT)]
        print("   modèles de conversation disponibles :", ", ".join(chat_models) or "aucun")
        if not GROQ_MODEL:
            print("   GROQ_MODEL non défini : choisis-en un dans la liste ci-dessus.")
        elif GROQ_MODEL not in available:
            print(f"   GROQ_MODEL={GROQ_MODEL} : ABSENT de ta liste (retiré ou renommé). À remplacer.")
        else:
            start = time.monotonic()
            try:
                reply = httpx.post(
                    f"{GROQ_BASE}/chat/completions",
                    headers=headers,
                    json={
                        "model": GROQ_MODEL,
                        "messages": [{"role": "user", "content": "Réponds uniquement par le mot OK."}],
                    },
                    timeout=30,
                )
                reply.raise_for_status()
                text = (reply.json()["choices"][0]["message"].get("content") or "").strip()
                print(f"   {GROQ_MODEL} : réponse reçue en {time.monotonic() - start:.1f} s -> {text[:40]!r}")
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                print(f"   {GROQ_MODEL} : ERREUR {type(exc).__name__} (statut={status})")
