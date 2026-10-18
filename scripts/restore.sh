#!/bin/sh
# Restaure une sauvegarde : ./scripts/restore.sh fox_20261017_090000.dump
# ATTENTION : remplace le contenu actuel de la base. Une sauvegarde de sécurité est faite d'abord.
set -eu
cd "$(dirname "$0")/.."
name="${1:?usage : ./scripts/restore.sh nom_du_fichier.dump}"

printf "Le contenu actuel de la base sera REMPLACÉ par %s. Taper OUI pour continuer : " "$name"
read -r answer
[ "$answer" = "OUI" ] || { echo "Annulé."; exit 1; }

docker compose exec -T -u root -e PREFIX=avant_restauration db sh /scripts/db_backup.sh
docker compose stop orchestrator telegram_bot
docker compose exec -T -u root db sh /scripts/db_restore.sh "$name"
docker compose start orchestrator telegram_bot
