#!/bin/sh
# Mise à jour sûre : sauvegarde de la base, puis reconstruction et redémarrage.
# Les migrations en attente s'appliquent au démarrage de l'orchestrateur.
set -eu
cd "$(dirname "$0")/.."
./scripts/backup.sh
docker compose up -d --build
docker compose logs --tail 20 orchestrator
