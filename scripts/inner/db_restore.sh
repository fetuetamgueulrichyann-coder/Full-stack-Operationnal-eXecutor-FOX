#!/bin/sh
# Exécuté DANS le conteneur de la base (voir scripts/restore.sh et scripts/restore.ps1).
# Restaure une sauvegarde : le contenu actuel de la base est REMPLACÉ par celui du fichier.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
name="${1:?usage : db_restore.sh nom_du_fichier.dump}"
file="$BACKUP_DIR/$(basename "$name")"

[ -f "$file" ] || { echo "Fichier introuvable : $file" >&2; exit 1; }
pg_restore -l "$file" > /dev/null

pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner "$file"
echo "Restauration terminée depuis : $file"
