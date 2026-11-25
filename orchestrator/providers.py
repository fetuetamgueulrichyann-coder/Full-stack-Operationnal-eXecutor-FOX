"""Fournisseurs externes auxquels FOX peut envoyer des données (table fixe, sans réseau).

Cette table décrit la réalité vérifiée, pas des souhaits : un fournisseur absent d'ici ne peut jamais
être utilisé par la passerelle (egress.py). Les niveaux de garantie viennent des variables
d'environnement GEMINI_TIER et GROQ_ZDR, qui doivent refléter ce que le propriétaire a réellement vérifié.
"""
import os
from dataclasses import dataclass

LLM = "llm"
TTS = "tts"


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str  # LLM ou TTS
    label: str
    roles: tuple  # rôles autorisés à l'utiliser
    note: str  # avertissement honnête affiché au consentement


PROVIDERS = {
    "gemini": Provider(
        "gemini", LLM, "Gemini (Google)", ("owner",),
        "Offre gratuite : Google peut relire et réutiliser les contenus pour améliorer ses produits, sans opposition possible.",
    ),
    "groq": Provider(
        "groq", LLM, "Groq", ("owner", "tester"),
        "Pas de conservation des échanges par défaut ; des journaux techniques peuvent rester jusqu'à 30 jours.",
    ),
    "gemini_tts": Provider(
        "gemini_tts", TTS, "Voix Gemini (Google)", ("owner",),
        "Le texte lu à voix haute est envoyé à Google.",
    ),
    "elevenlabs": Provider(
        "elevenlabs", TTS, "Voix ElevenLabs", ("owner",),
        "Le texte lu à voix haute est envoyé à ElevenLabs.",
    ),
}


def get(name):
    return PROVIDERS.get(name)


def allowed_for_role(name, role):
    provider = PROVIDERS.get(name)
    return provider is not None and role in provider.roles


def gemini_tier():
    """'free' (défaut, prudent) ou 'paid' : uniquement si le propriétaire l'a vérifié et déclaré."""
    return "paid" if os.environ.get("GEMINI_TIER", "").strip().lower() == "paid" else "free"


def groq_zdr_verified():
    """Vrai seulement si GROQ_ZDR=1 : FOX ne le présume jamais."""
    return os.environ.get("GROQ_ZDR", "").strip() == "1"


def retention_note(name):
    """Phrase honnête sur la conservation chez le fournisseur, selon la réalité déclarée."""
    if name == "groq":
        if groq_zdr_verified():
            return "Conservation zéro des échanges activée sur le compte. Des métadonnées d'utilisation sans contenu restent collectées."
        return "Des journaux techniques peuvent être conservés jusqu'à 30 jours."
    if name == "gemini":
        if gemini_tier() == "paid":
            return "Offre payante : les contenus ne servent pas à améliorer les produits Google."
        return PROVIDERS["gemini"].note
    provider = PROVIDERS.get(name)
    return provider.note if provider else ""
