"""Passerelle d'envoi de données vers l'extérieur (phase 1a : écrite et testée, NON BRANCHÉE).

Aucun code de FOX ne l'appelle encore : les appels existants (agent.py, speech.py) restent inchangés et
figés par tests/test_architecture.py. À partir de la phase 1c, tout envoi vers un fournisseur passera par
`authorize()` avant de partir ; les modules concernés sortiront alors de la liste blanche temporaire.

Principes (déjà garantis ici) :
- refus par défaut : sans contexte de requête, rien ne part (fail closed) ;
- la décision vient de la politique pure (privacy.py) et des autorisations relues en base à CHAQUE appel ;
- tous les tours d'outils d'une même opération partagent son autorisation « message » ;
- le journal garde seulement des métadonnées (fournisseur, catégories, tailles), jamais de contenu.
Ce module n'importe aucune bibliothèque réseau : il autorise, il n'envoie pas.
"""
import contextvars
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

import db
import privacy
import privacy_store
import providers

log = logging.getLogger("egress")


@dataclass(frozen=True)
class Context:
    user_id: int
    role: str
    level: str
    consented: bool
    operation_id: str
    chat_id: Optional[int] = None


@dataclass(frozen=True)
class Segment:
    """Un morceau de contenu destiné à partir, avec sa catégorie (jamais déclarée par le modèle)."""
    category: str
    file_id: Optional[str] = None
    size: int = 0


class EgressRefused(Exception):
    """Envoi refusé. `code` : no_context, provider, blocked ou grant_required ; `missing` : ce qui manque."""

    def __init__(self, code, missing=()):
        super().__init__(code)
        self.code = code
        self.missing = tuple(missing)


@dataclass
class Authorization:
    provider: str
    grants: list = field(default_factory=list)
    segments: int = 0
    size: int = 0


_CONTEXT = contextvars.ContextVar("fox_egress_context", default=None)


def new_operation_id():
    return uuid.uuid4().hex


def begin(user_id, role, chat_id=None, operation_id=None):
    """Ouvre le contexte d'une requête (niveau et consentement lus en base). Retourne le jeton pour end()."""
    settings = privacy_store.get_settings(user_id)
    context = Context(
        user_id=user_id, role=role, level=settings["level"], consented=settings["consented"],
        operation_id=operation_id or new_operation_id(), chat_id=chat_id,
    )
    return _CONTEXT.set(context)


def end(token):
    _CONTEXT.reset(token)


def current():
    return _CONTEXT.get()


def authorize(provider, segments):
    """Autorise (ou refuse) l'envoi de ces segments à ce fournisseur. À appeler avant CHAQUE appel sortant.

    Lève EgressRefused. Retourne une Authorization (les autorisations utilisées sont notées au journal).
    """
    context = _CONTEXT.get()
    if context is None:
        raise EgressRefused("no_context")
    if not providers.allowed_for_role(provider, context.role):
        raise EgressRefused("provider")
    if privacy.block_all():
        raise EgressRefused("blocked")

    granted, missing = [], []
    seen = set()
    for segment in segments:
        key = (segment.category, segment.file_id)
        if key in seen:
            continue
        seen.add(key)
        verdict = privacy.decide(context.level, segment.category, context.consented)
        if verdict == privacy.BLOCKED:
            raise EgressRefused("blocked", [segment.category])
        if verdict == privacy.ALLOWED:
            continue
        grant_id = privacy_store.use_grant(
            context.user_id, provider, segment.category,
            operation_id=context.operation_id, chat_id=context.chat_id, file_id=segment.file_id,
        )
        if grant_id is None:
            missing.append(segment.category)
        else:
            granted.append(grant_id)
    if missing:
        raise EgressRefused("grant_required", missing)

    result = Authorization(provider, granted, len(list(segments)), sum(s.size for s in segments))
    record(context, provider, segments, "authorized", granted)
    return result


def record(context, provider, segments, outcome, grants=()):
    """Journal d'audit (événement « egress ») : métadonnées seulement, jamais de contenu ni de nom de fichier."""
    segments = list(segments)
    details = {
        "provider": provider,
        "categories": sorted({s.category for s in segments}),
        "operation_id": context.operation_id,
        "grants": list(grants),
        "segments": len(segments),
        "size": sum(s.size for s in segments),
        "outcome": outcome,
        "chat_id": context.chat_id,
        "file_ids": [s.file_id for s in segments if s.file_id],
    }
    try:
        db.audit(context.user_id, "egress", None, details)
    except Exception:  # un journal en panne ne doit pas autoriser ni empêcher silencieusement : on le signale
        log.exception("écriture du journal d'envoi impossible")
        raise
