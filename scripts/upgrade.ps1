# Mise à jour sûre : sauvegarde de la base, puis reconstruction et redémarrage.
# Les migrations en attente s'appliquent au démarrage de l'orchestrateur.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& (Join-Path $PSScriptRoot "backup.ps1")
docker compose up -d --build
docker compose logs --tail 20 orchestrator
