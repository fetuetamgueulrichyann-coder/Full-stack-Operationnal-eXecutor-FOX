"""Teste la disponibilité de modèles Gemini avec ta clé, sans passer par Telegram.

Usage (dans le conteneur) :
    python check_models.py                      # modèle configuré + deux modèles Flash-Lite
    python check_models.py gemini-3.8-flash     # un modèle précis

Chaque modèle reçoit 2 requêtes : une question simple, puis une question qui doit
déclencher un outil (function calling), comme dans FOX.
"""
import os
import sys
import time

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
