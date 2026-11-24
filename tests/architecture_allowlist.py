"""Liste blanche des modules autorisés à sortir du réseau, et gel des appels externes d'agent.py.

Règles (voir docs/CONFIDENTIALITE.md) :
- une entrée par fichier, avec la liste EXACTE des modules réseau qu'il peut importer ;
- chaque entrée dit POURQUOI, et si elle est « temporary » (à retirer à une phase précise) ou « permanent » ;
- toute entrée temporaire a une phase de retrait : le jour où CURRENT_PHASE l'atteint, le test échoue tant que
  l'entrée n'est pas retirée (le code concerné doit alors passer par la passerelle egress.py) ;
- une entrée inutilisée (le fichier n'importe plus le module) fait échouer le test : la liste ne grossit pas
  en silence et ne garde pas d'autorisation morte ;
- ajouter une entrée est une décision de sécurité : elle doit être relue et justifiée.
"""

PHASES = ("1a", "1b", "1c", "1d")
CURRENT_PHASE = "1a"

# Plafonds : la liste doit rester petite.
MAX_ENTRIES = 12  # fichiers
MAX_MODULES = 18  # modules, tous fichiers confondus
TEMPORARY_MODULES = {
    ("orchestrator/agent.py", "httpx"),
    ("orchestrator/agent.py", "google.genai"),
    ("orchestrator/app.py", "google.genai.errors"),
    ("orchestrator/speech.py", "urllib.request"),
}

def _m(module, status, why, remove_in=None):
    entry = {"module": module, "status": status, "why": why}
    if remove_in:
        entry["remove_in"] = remove_in
    return entry


# fichier -> modules réseau qu'il peut importer (chacun avec son statut et sa raison).
ALLOWLIST = {
    # --- TEMPORAIRES : passeront par egress.py en phase 1c -------------------------------------------------
    "orchestrator/agent.py": [
        _m("httpx", "temporary", "Appel existant à Groq (chat, outils, secours). Gelé en 1a : aucun appel ajouté ; "
           "sera branché sur egress.py puis déplacé en 1c.", "1c"),
        _m("google.genai", "temporary", "Appel existant à Gemini (chat, outils). Gelé en 1a : aucun appel ajouté ; "
           "sera branché sur egress.py puis déplacé en 1c.", "1c"),
    ],
    "orchestrator/app.py": [
        _m("google.genai.errors", "temporary", "Importe seulement les classes d'erreur de Gemini pour répondre 502 ; "
           "aucun appel. Remplacé par une erreur neutre de la passerelle en 1c.", "1c"),
        _m("webpush", "permanent", "Routes de configuration des notifications push (clé publique, abonnement) ; "
           "même canal que webpush.py."),
    ],
    "orchestrator/speech.py": [
        _m("urllib.request", "temporary", "Synthèse vocale (Gemini TTS, ElevenLabs) : le texte lu part chez le fournisseur. "
           "Sera soumise aux catégories du texte lu et à `voix` via egress.py en 1c.", "1c"),
    ],
    # --- PERMANENTES : canaux distincts, justifiés ----------------------------------------------------------
    "orchestrator/webpush.py": [
        _m("urllib.request", "permanent", "Envoi des notifications push vers le service push du navigateur "
           "(contenu réglé par notification_detail, phases 1b/1d)."),
    ],
    "orchestrator/push_delivery.py": [_m("webpush", "permanent", "Livraison des rappels push (même canal que webpush.py).")],
    "orchestrator/push_worker.py": [_m("webpush", "permanent", "Processus de fond des rappels push (même canal).")],
    "orchestrator/admin.py": [_m("webpush", "permanent", "Commande d'administration : clés VAPID et test d'envoi push.")],
    "orchestrator/tools.py": [
        _m("google.oauth2", "permanent", "Google Calendar du propriétaire (outils réservés au rôle owner, écritures confirmées)."),
        _m("googleapiclient", "permanent", "Google Calendar du propriétaire (outils réservés au rôle owner, écritures confirmées)."),
    ],
    "orchestrator/check_models.py": [
        _m("httpx", "permanent", "Script d'administration qui liste les modèles ; n'envoie aucune donnée d'utilisateur."),
        _m("google.genai", "permanent", "Script d'administration qui liste les modèles ; n'envoie aucune donnée d'utilisateur."),
    ],
    "telegram_bot/bot.py": [
        _m("httpx", "permanent", "Canal Telegram : appels vers l'orchestrateur interne (réseau Docker)."),
        _m("telegram", "permanent", "Canal Telegram : bibliothèque officielle du bot."),
    ],
    "scripts/get_google_token.py": [
        _m("google_auth_oauthlib", "permanent", "Outil ponctuel d'administration pour le jeton Google Calendar ; hors exécution de FOX."),
    ],
}

# Appels externes d'agent.py gelés : (fonction englobante, appelé, nb d'arguments positionnels, mots-clés).
# Un appel déplacé, ajouté, renommé ou dont la signature change fait échouer le test.
AGENT_EXTERNAL_SITES = [
    ("<module>", "genai.Client", 0, ("api_key", "http_options")),
    ("_call_with_retry", "client.models.generate_content", 0, ("config", "contents", "model")),
    ("groq_post", "httpx.post", 1, ("headers", "json", "timeout")),
]

# Chemins internes qui mènent à ces appels : (fichier, fonction englobante, appelé, positionnels, mots-clés).
AGENT_ENTRY_CALLS = [
    ("orchestrator/agent.py", "generate_with_retry", "_call_with_retry", 3, ()),
    ("orchestrator/agent.py", "generate_with_retry", "_call_with_retry", 3, ()),
    ("orchestrator/agent.py", "groq_complete", "groq_post", 2, ()),
    ("orchestrator/agent.py", "groq_chat", "groq_post", 1, ("tools",)),
    ("orchestrator/agent.py", "groq_reply", "groq_complete", 1, ()),
    ("orchestrator/agent.py", "complete", "groq_complete", 1, ("temperature",)),
    ("orchestrator/agent.py", "complete", "generate_with_retry", 3, ("can_switch",)),
    ("orchestrator/agent.py", "complete", "groq_complete", 1, ("temperature",)),
    ("orchestrator/agent.py", "chat", "groq_chat", 5, ()),
    ("orchestrator/agent.py", "chat", "generate_with_retry", 3, ("can_switch",)),
    ("orchestrator/app.py", "message", "agent.chat", 3, ("chat_id", "private")),
    ("orchestrator/mail.py", "draft", "agent.complete", 3, ()),
    ("orchestrator/mail.py", "improve", "agent.complete", 3, ()),
    ("orchestrator/mail.py", "summarize", "agent.complete", 3, ()),
]
