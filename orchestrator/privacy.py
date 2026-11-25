"""Politique de confidentialité de FOX : bibliothèque PURE (ni base de données ni réseau).

Elle ne fait rien d'elle-même en phase 1a : elle décrit les niveaux, les catégories de données et la
décision « cette donnée peut-elle partir chez ce fournisseur ? », et calcule les catégories réellement
utilisées par un échange. La passerelle d'envoi (egress.py) s'appuiera dessus à partir de la phase 1c.

Catégories de données (ce qui peut partir) :
  texte_libre : ce que l'utilisateur écrit ou dit ;
  personnel   : agenda, tâches, rappels, mémoire ;
  metier      : clients, devis, chantiers, comptabilité ;
  fichier     : contenu d'un fichier joint (toujours autorisé fichier par fichier) ;
  voix        : envoi d'un texte à un service de synthèse vocale (s'ajoute aux catégories du texte lu).
Catégorie d'outil supplémentaire : systeme = n'expose aucune donnée de l'utilisateur au modèle.
"""
import contextvars
import os
from datetime import timedelta

STANDARD, PROTEGE, STRICT = "standard", "protege", "strict"
LEVELS = (STANDARD, PROTEGE, STRICT)
DEFAULT_LEVEL = PROTEGE

TEXTE_LIBRE, PERSONNEL, METIER, FICHIER, VOIX, SYSTEME = "texte_libre", "personnel", "metier", "fichier", "voix", "systeme"
DATA_CATEGORIES = (TEXTE_LIBRE, PERSONNEL, METIER, FICHIER)  # étiquettes possibles d'un échange
GRANT_CATEGORIES = DATA_CATEGORIES + (VOIX,)  # catégories qu'une autorisation peut couvrir
TOOL_CATEGORIES = (PERSONNEL, METIER, FICHIER, SYSTEME)  # un outil déclare l'une d'elles, obligatoirement

NOTIFICATION_DETAILS = ("discret", "complet")

# Durées maximales fixées par le code (le serveur calcule l'expiration, jamais le client).
MAX_CONVERSATION_GRANT = timedelta(hours=12)
MESSAGE_GRANT_TTL = timedelta(minutes=10)
HELD_MESSAGE_TTL = timedelta(minutes=10)

ALLOWED, NEEDS_GRANT, BLOCKED = "allowed", "needs_grant", "blocked"

# Les niveaux ne contrôlent PAS encore les envois : la passerelle (egress.py) n'est pas branchée. Cette constante
# passe à True en phase 1c, quand l'application des niveaux est réellement en place. Tant qu'elle est fausse,
# l'interface et l'API doivent le dire clairement et ne jamais présenter un niveau comme déjà opérationnel.
ENFORCEMENT_ACTIVE = False

# Version du texte d'information à lire. L'enregistrer ne vaut PAS autorisation d'envoi.
CONSENT_VERSION = 1

# Description des niveaux. Écrite au conditionnel tant que ENFORCEMENT_ACTIVE est fausse.
LEVEL_INFO = {
    PROTEGE: {
        "label": "Protégé",
        "summary": "Quand ce niveau sera appliqué : chaque envoi de données à un fournisseur externe demandera ton "
                   "autorisation. C'est le niveau par défaut.",
    },
    STANDARD: {
        "label": "Standard",
        "summary": "Quand ce niveau sera appliqué : ton texte et tes données personnelles (agenda, tâches, mémoire) "
                   "pourront partir sans autorisation une fois l'information lue ; les données métier, les fichiers "
                   "et la voix demanderont toujours ton autorisation.",
    },
    STRICT: {
        "label": "Strict",
        "summary": "Quand ce niveau sera appliqué : plus rien ne partira chez un fournisseur externe ; FOX ne "
                   "répondra qu'avec ses règles locales.",
    },
}


def level_label(level):
    """Libellé affichable. Strict est signalé « pas encore actif » tant que les niveaux ne sont pas appliqués."""
    label = LEVEL_INFO[level]["label"]
    if level == STRICT and not ENFORCEMENT_ACTIVE:
        label += " (pas encore actif)"
    return label

# Catégories envoyées sans autorisation en niveau Standard, une fois le consentement enregistré.
STANDARD_OPEN = (TEXTE_LIBRE, PERSONNEL)


def block_all():
    """Interrupteur d'urgence : PRIVACY_BLOCK_ALL=1 interdit tout envoi. Il ne peut que restreindre."""
    return os.environ.get("PRIVACY_BLOCK_ALL", "").strip() == "1"


def decide(level, category, consented=True):
    """Décision pour UNE catégorie : ALLOWED, NEEDS_GRANT ou BLOCKED (indépendante du fournisseur).

    Sans consentement enregistré, Standard se comporte comme Protégé. Un niveau inconnu est traité
    comme Strict (le plus restrictif) : une valeur corrompue ne doit jamais ouvrir davantage.
    """
    if category not in GRANT_CATEGORIES:
        return BLOCKED
    if block_all():
        return BLOCKED
    if level == STANDARD:
        if consented and category in STANDARD_OPEN:
            return ALLOWED
        return NEEDS_GRANT
    if level == PROTEGE:
        return NEEDS_GRANT
    return BLOCKED  # strict, ou niveau inconnu


def voice_categories(spoken_classes):
    """Catégories à couvrir pour lire un texte à voix haute chez un fournisseur de synthèse.

    `voix` plus chacune des catégories du texte lu. Catégories inconnues (None) : toutes les catégories
    de données, par prudence.
    """
    classes = DATA_CATEGORIES if spoken_classes is None else tuple(spoken_classes)
    wanted = [VOIX] + [c for c in DATA_CATEGORIES if c in classes]
    return tuple(dict.fromkeys(wanted))


def covered(level, categories, grants, consented=True):
    """Parmi `categories`, lesquelles bloquent ? Retourne (manquantes, bloquées).

    `grants` : ensemble des catégories couvertes par une autorisation valide pour ce fournisseur.
    """
    missing, blocked = [], []
    for category in categories:
        verdict = decide(level, category, consented)
        if verdict == BLOCKED:
            blocked.append(category)
        elif verdict == NEEDS_GRANT and category not in grants:
            missing.append(category)
    return tuple(missing), tuple(blocked)


def history_included(level, classes, grants, consented=True):
    """Un échange ancien peut-il être renvoyé à un fournisseur ? (sinon : remplacé par une note masquée)

    Étiquette inconnue (None, ex. échange d'avant la confidentialité) : sensible dans toutes les catégories.
    Rien n'est jamais supprimé : le masquage ne concerne que le contexte envoyé.
    """
    wanted = DATA_CATEGORIES if classes is None else tuple(classes)
    missing, blocked = covered(level, wanted, grants, consented)
    return not missing and not blocked


# --- Étiquetage calculé par le serveur ----------------------------------------
# Une « boîte » par requête (ContextVar, comme agent.SPOKEN) : les modules qui touchent réellement à des
# données y inscrivent la catégorie. Le modèle ne peut ni l'ajouter ni la retirer.
_BOX = contextvars.ContextVar("fox_exchange_classes", default=None)
_ENGINE = contextvars.ContextVar("fox_exchange_engine", default=None)


class _Box:
    __slots__ = ("classes", "unknown")

    def __init__(self):
        self.classes = set()
        self.unknown = False


def begin():
    """Ouvre la boîte d'une requête ; retourne les jetons à passer à end()."""
    return _BOX.set(_Box()), _ENGINE.set(None)


def end(tokens):
    _BOX.reset(tokens[0])
    _ENGINE.reset(tokens[1])


def mark(category):
    """Inscrit qu'une donnée de cette catégorie a réellement été utilisée. Sans boîte : ne fait rien."""
    box = _BOX.get()
    if box is None or category in (None, SYSTEME):
        return
    if category not in DATA_CATEGORIES:
        box.unknown = True  # catégorie inattendue : on ne devine pas, l'étiquette sera « inconnue »
        return
    box.classes.add(category)


def mark_unknown():
    """Du contenu d'origine inconnue (ex. ancien échange non étiqueté) est entré dans le contexte."""
    box = _BOX.get()
    if box is not None:
        box.unknown = True


def note_engine(name):
    if _BOX.get() is not None and name in ("gemini", "groq"):
        _ENGINE.set(name)


def collect(user_text=True):
    """Retourne (catégories ou None si inconnu, moteur). Hors boîte : (None, None)."""
    box = _BOX.get()
    if box is None:
        return None, None
    if box.unknown:
        return None, _ENGINE.get()
    classes = set(box.classes)
    if user_text:
        classes.add(TEXTE_LIBRE)
    return sorted(classes, key=DATA_CATEGORIES.index), _ENGINE.get()
