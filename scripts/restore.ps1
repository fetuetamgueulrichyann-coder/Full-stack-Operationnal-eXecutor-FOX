# Restaure une sauvegarde : .\scripts\restore.ps1 fox_20261017_090000.dump
# ATTENTION : remplace le contenu actuel de la base. Une sauvegarde de sécurité est faite d'abord.
param([Parameter(Mandatory = $true)][string]$Name)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$answer = Read-Host "Le contenu actuel de la base sera REMPLACÉ par $Name. Taper OUI pour continuer"
if ($answer -ne "OUI") { Write-Host "Annulé."; exit 1 }

docker compose exec -T -u root -e PREFIX=avant_restauration db sh /scripts/db_backup.sh
if ($LASTEXITCODE -ne 0) { throw "La sauvegarde de sécurité a échoué : restauration annulée." }
docker compose stop orchestrator telegram_bot
docker compose exec -T -u root db sh /scripts/db_restore.sh $Name
if ($LASTEXITCODE -ne 0) { throw "La restauration a échoué." }
docker compose start orchestrator telegram_bot
