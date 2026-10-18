#!/bin/sh
# Exécuté DANS le conteneur de la base (voir scripts/backup.sh et scripts/backup.ps1).
# Crée une sauvegarde compressée, vérifie qu'elle est lisible, puis supprime les plus
# anciennes au-delà de KEEP exemplaires. Ne modifie jamais la base.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP="${KEEP:-14}"
PREFIX="${PREFIX:-fox}"

mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/${PREFIX}_$(date +%Y%m%d_%H%M%S).dump"

pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$file"
pg_restore -l "$file" > /dev/null
echo "Sauvegarde créée : $file ($(wc -c < "$file") octets)"

# Rotation : on ne garde que les KEEP sauvegardes les plus récentes de ce préfixe.
ls -1t "$BACKUP_DIR"/${PREFIX}_*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
  echo "Ancienne sauvegarde supprimée : $old"
done
