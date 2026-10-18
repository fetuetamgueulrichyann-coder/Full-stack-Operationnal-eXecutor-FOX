# Sauvegarde la base FOX dans .\backups (14 exemplaires conservés par défaut).
# À lancer avant toute mise à jour importante. Ne modifie pas la base.
# Usage : .\scripts\backup.ps1            ou      .\scripts\backup.ps1 -Keep 30
param([int]$Keep = 14)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
docker compose exec -T -u root -e KEEP=$Keep db sh /scripts/db_backup.sh
if ($LASTEXITCODE -ne 0) { throw "La sauvegarde a échoué." }
