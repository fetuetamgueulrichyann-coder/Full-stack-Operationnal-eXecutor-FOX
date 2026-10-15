# FOX

Services : `db` (PostgreSQL), `orchestrator` (Flask), `telegram_bot`.

## Lancement

    cp .env.example .env
    # renseigner GEMINI_API_KEY, TELEGRAM_TOKEN, TELEGRAM_ALLOWED_USER_ID
    docker compose up -d --build
    docker compose logs -f

Les scripts de `db/init/` ne s'exécutent qu'à la création du volume.
Pour appliquer le schéma sur une base déjà créée :

    docker compose exec -T db psql -U fox -d fox < db/init/002_actions_audit.sql
    docker compose exec -T db psql -U fox -d fox < db/init/003_undo.sql

Pour repartir de zéro : `docker compose down -v` puis relancer.

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
    curl -s -X POST localhost:5000/message \
      -H 'Content-Type: application/json' \
      -d '{"user_id": TON_ID_TELEGRAM, "text": "Bonjour"}'
