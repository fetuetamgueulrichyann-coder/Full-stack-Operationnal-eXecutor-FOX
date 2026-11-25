# Confidentialité de FOX (Lot 1)

Objectif : que rien de ce qui touche à tes données ne parte chez un fournisseur externe (Gemini, Groq, synthèse
vocale…) sans une règle claire, ton accord quand il le faut, et une trace sans contenu. Ce document décrit l'état
**après la phase 1b**. Les niveaux se choisissent et s'enregistrent, mais ne contrôlent
**pas encore** les envois : c'est la phase 1c.

## Phase 1a : fondations, sans changement de comportement

Rien ne change pour toi : FOX répond, lit, parle exactement comme avant. La 1a pose les briques et les
verrouille par des tests ; elles ne sont branchées qu'aux phases suivantes (1c : application dans le chat, les
fichiers, la voix et le mail).

| Brique | Rôle | État en 1a |
|---|---|---|
| `orchestrator/privacy.py` | niveaux, catégories, décision « cette donnée peut-elle partir ? », étiquetage | bibliothèque pure, testée |
| `orchestrator/privacy_store.py` | réglages, autorisations, messages retenus (base) | testé, non appelé par le flux (sauf nettoyage périodique) |
| `orchestrator/providers.py` | table fixe des fournisseurs, rôles autorisés, réalité vérifiée (`GEMINI_TIER`, `GROQ_ZDR`) | testé |
| `orchestrator/egress.py` | passerelle : refus par défaut, autorisations relues à chaque appel, journal sans contenu | écrite, **non branchée** |
| migration `022_confidentialite.sql` | tables et colonnes de support | additive, vide au départ |
| `Tool.category` | chaque outil déclare la catégorie de données qu'il manipule (obligatoire) | actif |
| `conversations.data_classes` / `engine` | catégories réellement utilisées et moteur de chaque nouvel échange | actif (écriture seule) |
| `tests/test_architecture.py` | aucun nouveau chemin d'envoi externe sans déclaration | actif |

### Niveaux

- **Protégé** (défaut) : toute catégorie exige une autorisation.
- **Standard** : texte libre et données personnelles partent sans autorisation **après consentement** ;
  métier, fichiers et voix exigent une autorisation.
- **Strict** : rien ne part ; règles et explications locales seulement.
- Un niveau inconnu ou corrompu est traité comme Strict.
- `PRIVACY_BLOCK_ALL=1` (variable d'environnement) bloque tout envoi, quel que soit le niveau. Il ne peut que restreindre.

> Cette matrice formalise les niveaux validés ; elle sera relue avant d'être appliquée en 1c.

### Catégories

`texte_libre` (ce que tu écris ou dis), `personnel` (agenda, tâches, rappels, mémoire), `metier` (clients, devis,
chantiers, compta), `fichier` (contenu d'un fichier joint, toujours fichier par fichier), `voix` (envoi d'un
texte à une synthèse vocale). Un outil déclare aussi `systeme` quand il n'expose aucune donnée au modèle.

### Étiquetage des échanges

Les catégories d'un échange sont **calculées par le serveur** à partir de ce qui a réellement servi : outils
exécutés (leur catégorie est fixée dans le code), fichiers joints, mémoire injectée, échanges précédents repris
dans le contexte. Le modèle ne peut ni les ajouter ni les retirer. Un échange d'avant la 1a, ou dont l'origine est
incertaine, reçoit l'étiquette **inconnue** (`NULL`), traitée comme sensible dans toutes les catégories. Rien n'est
supprimé ni réétiqueté.

### Autorisations (règles déjà garanties par le store)

- l'expiration est calculée par le serveur ; une autorisation de conversation dure au plus **12 h** (contrainte en base aussi) ;
- une autorisation « message » est liée à **une opération** (un message et tous ses tours d'outils, jusqu'à 5) ;
  elle est marquée consommée à la première utilisation, tous les tours de la même opération la partagent, une
  autre opération ne peut pas l'utiliser ;
- une autorisation couvre un seul fournisseur et une seule catégorie ; une autorisation de texte ne couvre jamais
  les fichiers ni les données métier ; un fichier exige son autorisation propre ;
- la validité est relue en base à chaque appel : une révocation prend effet à l'appel suivant (un appel déjà parti ne se rappelle pas) ;
- supprimer une conversation, tout l'historique, ou purger l'ancien supprime ses autorisations (clé étrangère en cascade) ;
  `purge_expired()` nettoie en plus les messages retenus expirés, les autorisations expirées depuis plus d'un jour et celles dont le fichier a disparu.

### Synthèse vocale

Lire un texte à voix haute chez un fournisseur exige l'autorisation `voix` **et** celle de chaque catégorie du texte lu
(la politique expose `voice_categories`). Texte de catégorie inconnue : toutes les catégories. Sans autorisation : voix locale
du navigateur ou texte seul, jamais de bascule automatique vers un autre fournisseur. Application en 1c.

### Fournisseurs et réalité vérifiée

- Gemini : uniquement pour le propriétaire. Conditions officielles (version du 18 déc. 2025) : 18 ans minimum, accès dans
  une région disponible (le Cameroun figure dans la liste), offre **payante obligatoire** pour des utilisateurs de l'EEE, de
  la Suisse ou du Royaume-Uni. Offre gratuite : contenus pouvant servir à améliorer les produits Google, relecture humaine
  possible, sans opposition. `GEMINI_TIER=paid` n'est à positionner que si c'est vrai.
- Groq : pas de conservation des échanges par défaut, journaux techniques jusqu'à 30 jours. `GROQ_ZDR=1` seulement
  après vérification dans ta console (Settings > Data Controls > « Inference APIs ZDR » activé). Sans `GROQ_ZDR=1` dans
  `.env`, FOX affiche la mention prudente « journaux jusqu'à 30 jours ». Ce que FOX affiche vient de cette déclaration :
  il ne peut pas la vérifier lui-même.

## Phase 1b : réglages, information et notifications discrètes

La 1b rend la confidentialité **visible et réglable**, sans rien appliquer. `ENFORCEMENT_ACTIVE` reste `False` (il passe à
`True` en 1c) : tant qu'il est faux, tout l'écran le dit (bandeau permanent « Protections pas encore appliquées »), Strict
s'affiche « (pas encore actif) » et chaque résumé de niveau commence par « Quand ce niveau sera appliqué ».

| Élément | Comportement en 1b |
|---|---|
| Réglages > Confidentialité | niveau (Protégé par défaut), fournisseur préféré, détail des notifications, information lue |
| Page `/confidentialite` | les trois niveaux, les fournisseurs et leur conservation réelle, les types de données, le suivi prévu en 1c |
| `/prive` | inchangée, avec un lien vers la page Confidentialité |
| Inscription | encadré d'information ; rien d'obligatoire, aucune case |
| `GET/POST /privacy/settings`, `POST /privacy/consent`, `GET /privacy/info` | voir `API.md` ; les changements ne passent que par une session web (refusés depuis le bot Telegram) |
| Audit | événements `privacy_settings` et `privacy_consent` (journal sans contenu) |

Règles de la 1b :

- **Le consentement n'autorise rien.** Il enregistre une version et une date de lecture, ne bloque pas FOX et n'est pas une
  autorisation d'envoi. Le sens que lui donne Standard (envoi libre du texte et du personnel) n'existe qu'à partir de la 1c.
- **Le fournisseur préféré est seulement enregistré.** Un testeur ne peut choisir que Groq ; le propriétaire, Gemini ou Groq.
  FOX ne s'en sert pas avant la 1c.
- **Pas de liste des envois.** Le suivi commence en 1c ; l'écran dit que les envois ne sont pas encore tracés.
- **L'écran ne garantit rien de plus que le serveur.** Les textes de niveaux et de fournisseurs viennent de l'API ; la
  conservation affichée suit `GEMINI_TIER` et `GROQ_ZDR`, déclarés par l'administrateur. Quand `enforcement_active` passera à
  vrai, le bandeau disparaît de lui-même.
- **Les affichages de l'application ne déclenchent aucun envoi externe** : lire ou changer un réglage n'appelle aucun fournisseur.

### Notifications discrètes

Par défaut, pour tout le monde (propriétaire compris), une notification de rappel affiche seulement « Rappel FOX » et
« Touche pour ouvrir FOX. » ; toucher ouvre la bonne page (agenda ou tâches). **Le contenu n'est pas construit** en mode
discret : le titre, le lieu et les tâches ne sont pas lus pour la notification, donc rien n'est transmis au service de
notification du navigateur (Google, Apple, Mozilla). Le mode « complet » (choisi par l'utilisateur) rétablit les détails.

- Le réglage est relu à chaque envoi (`push_delivery.detail_for`) ; sans réglage enregistré, erreur de lecture ou valeur
  inconnue : **discret**. Chaque utilisateur a son propre réglage.
- Limite connue : les rappels envoyés par **Telegram** affichent encore le titre. Prévu en 1d.
- La notification de test (« Les notifications fonctionnent. ») ne contient aucune donnée de l'utilisateur.

### Polices locales

Manrope (500, 600, 700, 800) et JetBrains Mono (500, 700) viennent des paquets `@fontsource/*` en version exacte 5.3.0
(licence SIL OFL 1.1, textes dans `web/public/licences/`). FOX ne charge plus Google Fonts : `index.html` n'y fait plus
appel et la CSP de `deploy/Caddyfile` n'autorise plus `fonts.googleapis.com` ni `fonts.gstatic.com`. Ainsi, ouvrir FOX
n'envoie plus l'adresse IP de l'utilisateur à Google.

### Pas de nouvelle migration

La 1b ne change pas la base : la migration `022` de la 1a suffit (`python migrate.py --status` doit rester à 22).

## Test d'architecture et liste blanche temporaire

`tests/test_architecture.py` lit le code source et échoue si un fichier importe une bibliothèque réseau, ouvre une
connexion, lance un processus ou fait un import dynamique sans être déclaré dans `tests/architecture_allowlist.py`.

- Chaque entrée dit **pourquoi** ; elle est **temporaire** (avec sa phase de retrait) ou **permanente** (canal distinct justifié).
- Temporaires (retrait prévu en **1c**) : `agent.py` (httpx, google.genai), `app.py` (erreurs Gemini), `speech.py` (urllib.request).
- Quand `CURRENT_PHASE` atteint la phase de retrait, le test échoue tant que l'entrée n'est pas retirée.
- Une entrée devenue inutile fait échouer le test ; la liste a des plafonds (fichiers et modules).
- **`agent.py` est gelé en 1a** : le test compare exactement les appels externes (fonction englobante, appelé, nombre
  d'arguments, mots-clés) et les chemins qui y mènent (chat, complete, mail). Un appel ajouté, déplacé ou modifié, un alias
  (`post = httpx.post`) ou un nouvel appelant est refusé.
- Tout outil (`Tool(...)`) du code doit déclarer sa catégorie, y compris ceux enregistrés plus tard (Google Calendar).

## Retour arrière de la migration 022

Voir [`RETOUR_ARRIERE_022.md`](RETOUR_ARRIERE_022.md).
