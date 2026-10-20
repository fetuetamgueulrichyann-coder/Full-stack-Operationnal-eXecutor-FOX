# FOX

Services : `db` (PostgreSQL), `orchestrator` (Flask), `telegram_bot`. Documentation des évolutions : `docs/`.

## Lancement

    cp .env.example .env
    # renseigner GEMINI_API_KEY, TELEGRAM_TOKEN, TELEGRAM_ALLOWED_USER_ID
    # et FOX_SERVICE_TOKEN (voir « Comptes et accès » ci-dessous)
    docker compose up -d --build
    docker compose logs -f

Le fichier `.env` contient les secrets : il n'est jamais enregistré dans Git
et ne se partage pas. `.env.example` n'en contient aucun.

## Base de données, migrations et sauvegardes

Le schéma de départ (`db/init/`, scripts 001 à 003) est créé une seule fois, à
la création du volume. Toute évolution passe ensuite par des **migrations
numérotées** (`db/migrations/004_*.sql`, `005_*.sql`...), appliquées
automatiquement au démarrage de l'orchestrateur par `orchestrator/migrate.py` :

- chaque migration s'exécute dans une transaction et n'est enregistrée
  (table `schema_migrations`) qu'une fois réussie ;
- une migration déjà appliquée ne peut pas être modifiée (somme de contrôle) ;
- une opération destructive (`DROP TABLE`, `DROP COLUMN`, `TRUNCATE`,
  `DELETE FROM`, changement de type) est **refusée par défaut** ; les règles
  d'approbation sont dans `db/migrations/README.md`.

Voir l'état : `docker compose exec orchestrator python migrate.py --status`.

**Sauvegarde avant toute mise à jour importante** (fichiers dans `backups/`,
14 exemplaires conservés) :

    .\scripts\backup.ps1        # Windows (PowerShell)
    ./scripts/backup.sh          # Linux / macOS

    .\scripts\upgrade.ps1       # sauvegarde, puis reconstruit et redémarre
    ./scripts/upgrade.sh

Restauration (remplace le contenu actuel de la base, après une sauvegarde de
sécurité automatique et une confirmation « OUI ») :

    .\scripts\restore.ps1 fox_20261017_090000.dump
    ./scripts/restore.sh fox_20261017_090000.dump

`docker compose down -v` **supprime toutes les données** : ne l'utiliser
qu'après une sauvegarde copiée hors du dossier du projet.

## Comptes et accès (phase A)

Plus aucune requête n'est acceptée sans identité. L'identité vient toujours du
serveur, jamais d'un `user_id` envoyé par le client :

- **interface web** : connexion par nom d'utilisateur et mot de passe, session
  par cookie `HttpOnly` (`SameSite=Lax`, `Secure` si `COOKIE_SECURE=1` derrière
  HTTPS), 7 jours par défaut (`SESSION_DAYS`) ;
- **bot Telegram** : service interne de confiance. Il présente le secret
  `FOX_SERVICE_TOKEN` (le même dans l'orchestrateur et le bot) et l'identifiant
  Telegram, que l'orchestrateur associe à un compte actif.

Deux rôles :

| | propriétaire (`owner`) | testeur (`tester`) |
|---|---|---|
| Création | automatique au démarrage, depuis `TELEGRAM_ALLOWED_USER_ID` | inscription avec un code d'invitation |
| IA | Gemini, secours Groq en conversation seule | Groq uniquement, jamais Gemini |
| Outils | Google Agenda (avec confirmation) | aucun pour l'instant (agenda interne en phase B) |
| Quota par jour | 200 appels, 500 000 tokens | 30 appels, 15 000 tokens |

Les plafonds globaux (`MAX_CALLS_PER_DAY`, `MAX_TOKENS_PER_DAY`) et ceux de Groq
(`GROQ_MAX_*`, sous le quota gratuit de 200 000 tokens par jour du compte) restent
en vigueur. Toutes ces valeurs se règlent dans `.env`.

Première mise à jour depuis la phase 0 :

1. Sauvegarde : `.\scripts\backup.ps1`
2. Dans `.env`, ajoute `FOX_SERVICE_TOKEN` (valeur obtenue avec
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`) et, si tu veux
   te connecter à l'interface web, `OWNER_PASSWORD` (10 caractères minimum).
3. `docker compose up -d --build`

Au premier démarrage, ton compte propriétaire est créé et ton historique, tes
actions en attente et ton journal d'audit (qui portaient ton identifiant
Telegram) y sont rattachés. Aucune ligne n'est supprimée.

Administration (dans le conteneur de l'orchestrateur) :

    docker compose exec orchestrator python admin.py create-invite --days 7 --uses 1 --note "Marie"
    docker compose exec orchestrator python admin.py list-invites
    docker compose exec orchestrator python admin.py revoke-invite 3
    docker compose exec orchestrator python admin.py list-users
    docker compose exec orchestrator python admin.py disable-user marie
    docker compose exec orchestrator python admin.py enable-user marie
    docker compose exec -it orchestrator python admin.py set-password yann
    docker compose exec orchestrator python admin.py purge

Le code d'invitation n'est affiché qu'une fois : seule son empreinte est stockée.
Les mots de passe sont hachés avec scrypt. Après 5 échecs de connexion en 15
minutes pour un même nom (20 pour une même adresse IP), les tentatives sont
bloquées temporairement.

Points d'API : `POST /auth/register` (code, username, password), `POST /auth/login`,
`POST /auth/logout`, `GET /auth/me`, `POST /message`, `POST /actions/<id>/confirm|cancel`.
Toute requête `POST`, `PUT`, `PATCH` ou `DELETE` doit être au format JSON.

## Conversations, vie privée et inscription par Telegram (phase A2a)

**Qui peut parler au bot.** Le bot n'est plus verrouillé sur un identifiant : l'orchestrateur
décide, selon les comptes actifs. Un inconnu reçoit seulement la consigne d'inscription et
n'a accès à rien. La variable `TELEGRAM_ALLOWED_USER_ID` sert maintenant uniquement à créer le
compte propriétaire.

**Inviter un testeur sans interface web.**

1. `docker compose exec orchestrator python admin.py create-invite --days 7 --uses 1 --note "Marie"`
2. Tu transmets le code. La personne ouvre le bot et envoie `/start FOX-XXXX-XXXX-XXXX`.
3. Son compte testeur est créé (Groq, sans outils, 30 appels et 15 000 tokens par jour).

Les essais de code sont limités (10 échecs par identifiant Telegram), un compte déjà inscrit
ne consomme pas le code, et le code est à usage unique par défaut.

**Commandes Telegram**

| Commande | Effet |
|---|---|
| `/nouveau` | nouvelle conversation (le contexte de FOX repart de zéro) |
| `/historique` | liste des conversations, boutons pour en reprendre une |
| `/effacer` | supprime la conversation courante ou tout l'historique (seconde confirmation pour « tout ») |
| `/prive` | mode « ne pas enregistrer » ; `/prive off` ou `/nouveau` pour en sortir |
| `/annuler` | annule la dernière action effectuée |

FOX ne relit que la conversation en cours : changer de sujet avec `/nouveau` améliore aussi
les réponses et réduit la consommation de tokens.

**Mode privé.** Tant qu'il est actif, rien n'est écrit dans l'historique. FOX garde seulement un
court contexte en mémoire (30 minutes, `PRIVATE_CONTEXT_MINUTES`) pour suivre la discussion,
supprimé automatiquement et à la sortie du mode. Le mode ne s'éteint jamais tout seul : on
n'enregistre jamais par surprise. Ce qui subsiste malgré tout : le comptage des quotas et le
journal d'audit des actions (par exemple « événement créé »), qui contient les paramètres
d'une action mais jamais le texte de la conversation.

**Suppression et conservation.** `/effacer` supprime définitivement les messages de la base.
Les sauvegardes déjà faites contiennent encore l'ancienne version : elles sont conservées
14 jours, la suppression est donc complète au bout de 14 jours. Les conversations inactives
sont supprimées automatiquement après `RETENTION_DAYS_OWNER` jours (90) pour le propriétaire
et `RETENTION_DAYS_TESTER` jours (30) pour les testeurs (0 = jamais). La purge s'exécute au
plus une fois par heure, à l'usage, ou à la demande avec `admin.py purge`.

**API (pour la future interface web).** `GET /chats`, `POST /chats` (nouvelle),
`POST /chats/<id>/open`, `DELETE /chats/<id>`, `DELETE /chats` avec `{"confirm": "all"}`,
`POST /private` avec `{"on": true|false}`, `POST /message` avec un `chat_id` facultatif
(le mode privé l'emporte toujours), `POST /auth/telegram` (réservé au bot).

## Tests

Les tests utilisent une base PostgreSQL temporaire, créée puis supprimée ; ils
ne touchent jamais à la base de FOX et n'appellent ni Gemini, ni Groq, ni Google.

    docker compose --profile test run --rm tests

Ils couvrent : tentatives automatiques et erreurs Gemini, secours Groq,
confirmation, refus, expiration et annulation des actions, plafonds par
utilisateur, comptes, sessions, codes d'invitation, isolation entre utilisateurs,
rôles et routage des fournisseurs, API Flask, migrations et scripts de sauvegarde.
Les tests des scripts de sauvegarde sont ignorés si `pg_dump` est absent du conteneur.

## Fournisseurs IA

- **Gemini** (principal) : erreurs 429, 500, 502, 503 et 504 réessayées avec des
  délais progressifs (`LLM_MAX_ATTEMPTS`, `LLM_RETRY_BUDGET_SECONDS`), puis
  erreur contrôlée (HTTP 503) affichée clairement dans Telegram.
- **Groq** (conversation seule, sans outils) : modèle principal des testeurs et
  secours du propriétaire (seulement au premier tour d'une conversation).
  L'agenda ne passe jamais par Groq.
  Les modèles Groq changent : `docker compose exec orchestrator python check_models.py`
  vérifie que `GROQ_MODEL` existe encore sur ton compte.

Conditions vérifiées le 17/10/2026 (susceptibles d'évoluer, à relire avant
d'ouvrir FOX à des testeurs) :

| | Gemini gratuit | Groq |
|---|---|---|
| Usage des contenus | utilisés pour améliorer les produits Google, relecture humaine possible | pas d'entraînement sur les entrées et sorties (contrat de service, section 4.2) |
| Conservation | non précisée dans les conditions lues | aucune par défaut ; journalisation possible jusqu'à 30 jours pour la fiabilité ou les abus ; option « Zero Data Retention » à activer dans la console |
| Données sensibles | à ne pas soumettre | non précisé hors données de santé |
| Âge | 18 ans minimum | 18 ans minimum |
| Zone | niveau gratuit non autorisé pour les clients accessibles depuis l'EEE, la Suisse et le Royaume-Uni | pas de restriction trouvée ; entité contractante britannique pour l'EEE et la Suisse |
| Quota gratuit | selon le modèle | limites **partagées par tout le compte** : pour `openai/gpt-oss-20b`, 30 requêtes/min, 1 000/jour, 8 000 tokens/min, 200 000 tokens/jour |

Conséquence : le quota Groq est commun à tous les testeurs et très serré. Les
quotas par utilisateur (30 appels et 15 000 tokens par jour) et le plafond global
Groq (`GROQ_MAX_*`) en tiennent compte : avec ces valeurs, une douzaine de testeurs
actifs par jour atteignent le quota du compte.

## Agenda Google (outils de FOX)

Sans les trois variables `GOOGLE_*`, FOX fonctionne comme simple conversation.
Avec elles, il peut lire l'agenda (directement) et créer, modifier ou
supprimer des événements (après confirmation par boutons dans Telegram).

1. Sur console.cloud.google.com, crée un projet.
2. « API et services » → « Bibliothèque » : active **Google Calendar API**.
3. « Écran de consentement OAuth » : type Externe, puis ajoute ton adresse
   Gmail dans les utilisateurs de test.
4. « Identifiants » → « Créer des identifiants » → **ID client OAuth**, type
   « Application de bureau ». Télécharge le fichier JSON (`client_secret.json`).
5. Sur ta machine :

       pip install google-auth-oauthlib
       python scripts/get_google_token.py client_secret.json

   Connecte-toi dans le navigateur, puis copie les trois lignes affichées
   dans `.env`.
6. `docker compose up -d --build`

Tant que l'application est en mode « Test », Google invalide le jeton au bout
de 7 jours : relance alors l'étape 5, ou passe l'application en « Production »
(sans vérification, usage personnel).

## Fonctionnement des confirmations

- Lecture de l'agenda : exécutée directement.
- Création, modification, suppression : FOX enregistre une action en attente
  et affiche les boutons Confirmer / Annuler. Sans clic dans les
  `PENDING_TTL_MINUTES` minutes, l'action expire.
- Annulation : « annule ça » (ou la commande `/annuler`) propose d'annuler la
  dernière action exécutée dans les `UNDO_WINDOW_MINUTES` minutes (60 par
  défaut) : un événement créé est supprimé, un événement modifié retrouve ses
  valeurs d'origine. La suppression d'un événement n'est pas annulable.
  L'annulation passe elle aussi par Confirmer / Annuler.
- Chaque proposition, confirmation, annulation, expiration et exécution est
  inscrite dans la table `audit_log` :

      docker compose exec db psql -U fox -d fox -c \
        "SELECT created_at, event, tool FROM audit_log ORDER BY id DESC LIMIT 20;"

## Test de l'orchestrateur

    curl -s localhost:5000/health
    curl -s -c cookies.txt -X POST localhost:5000/auth/login \
      -H 'Content-Type: application/json' \
      -d '{"username": "yann", "password": "TON_MOT_DE_PASSE"}'
    curl -s -b cookies.txt -X POST localhost:5000/message \
      -H 'Content-Type: application/json' \
      -d '{"text": "Bonjour"}'
