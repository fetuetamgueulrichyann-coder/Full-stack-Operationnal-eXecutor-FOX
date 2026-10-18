#!/bin/sh
# Sauvegarde la base FOX dans ./backups (14 exemplaires conservés par défaut : KEEP=30 ./scripts/backup.sh)
# À lancer avant toute mise à jour importante. Ne modifie pas la base.
set -eu
cd "$(dirname "$0")/.."
docker compose exec -T -u root -e KEEP="${KEEP:-14}" db sh /scripts/db_backup.sh
