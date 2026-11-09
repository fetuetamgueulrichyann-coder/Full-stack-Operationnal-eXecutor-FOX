# Déployer FOX sur un VPS (HTTPS, sécurité)

Guide pas à pas pour mettre FOX en ligne sur un serveur Linux (Ubuntu 22.04/24.04) avec un nom de domaine.
Compter environ 1 heure. Tout ce qui est secret reste dans le fichier `.env` du serveur, jamais dans Git.

## 1. Avant de commencer

- Un VPS (1 vCPU, 2 Go de RAM suffisent) et un nom de domaine, par exemple `fox.exemple.com`.
- Dans le DNS du domaine : un enregistrement **A** `fox` qui pointe vers l'adresse IP du VPS (attendre quelques minutes).
- Une clé SSH pour se connecter (pas de connexion par mot de passe).

## 2. Préparer le serveur

```bash
# Mises à jour et pare-feu : seuls SSH (22), HTTP (80) et HTTPS (443) sont ouverts.
sudo apt update && sudo apt -y upgrade
sudo apt -y install ufw fail2ban unattended-upgrades git
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443
sudo ufw enable

# SSH : clé seulement, pas de root. Dans /etc/ssh/sshd_config : PermitRootLogin no, PasswordAuthentication no
sudo systemctl restart ssh

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # puis se reconnecter
```

Important : Docker contourne `ufw` pour les ports qu'il publie. Dans `docker-compose.yml`, FOX ne publie que
`127.0.0.1:5000` (orchestrateur, invisible depuis Internet) et le proxy sur 80/443. La base PostgreSQL ne publie rien.
Ne change pas ces `ports`.

## 3. Installer FOX

```bash
git clone <adresse-de-ton-depot> fox && cd fox
cp .env.example .env && chmod 600 .env
nano .env
```

À renseigner dans `.env` (génère chaque secret avec `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`) :

| Variable | Valeur en ligne |
|---|---|
| `POSTGRES_PASSWORD` | un mot de passe long, unique |
| `FOX_SERVICE_TOKEN` | un secret de 32 caractères minimum |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_TOKEN` | tes clés (jamais partagées ni commitées) |
| `TELEGRAM_ALLOWED_USER_ID` | ton identifiant Telegram |
| `DOMAIN` | `fox.exemple.com` |
| `PUBLIC_ORIGIN` | `https://fox.exemple.com` |
| `COOKIE_SECURE` | `1` |
| `TRUST_PROXY` | `1` |
| `REGISTRATION_MODE` | `invite` (ou `closed` quand plus personne ne doit s'inscrire) |
| `OWNER_PASSWORD` | à définir le temps du premier démarrage, **puis à retirer** |

## 4. Lancer

```bash
docker compose --profile build run --rm web_build   # construit l'interface dans web/dist
docker compose up -d --build                         # base, orchestrateur, bot
docker compose --profile prod up -d proxy            # HTTPS automatique (Caddy)
docker compose logs --tail 50 orchestrator           # aucune ligne « configuration : » ne doit apparaître
```

Au démarrage, FOX contrôle sa propre configuration et écrit un avertissement par réglage dangereux
(cookie non sécurisé, jeton trop court, `OWNER_PASSWORD` oublié…). Il n'écrit jamais les valeurs.

Vérifier : `https://fox.exemple.com` s'ouvre avec le cadenas ; `https://fox.exemple.com/api/health` répond `{"status":"ok"}`.
Puis activer HSTS (une fois le HTTPS validé) : décommenter la ligne `Strict-Transport-Security` de `deploy/Caddyfile`
et `docker compose --profile prod restart proxy`.

## 5. Après le premier démarrage

1. Retirer `OWNER_PASSWORD` du `.env`, puis `docker compose up -d orchestrator`.
2. Inviter les testeurs : `docker compose exec orchestrator python admin.py create-invite`.
3. Application installable : ouvrir le site sur le téléphone, menu du navigateur, « Ajouter à l'écran d'accueil ».
4. Sauvegardes automatiques : `crontab -e` puis `0 3 * * * cd ~/fox && ./scripts/backup.sh`. Copier de temps en temps
   le dossier `backups/` hors du serveur (les sauvegardes contiennent des données personnelles : ne pas les publier).

## 6. Mises à jour

```bash
./scripts/backup.sh && git pull && ./scripts/upgrade.sh
docker compose --profile build run --rm web_build && docker compose --profile prod restart proxy
```

## 7. Ce que FOX protège déjà (audit de la phase E)

| Sujet | État |
|---|---|
| Mots de passe | hachés avec scrypt, jamais en clair ; politique de longueur minimale |
| Sessions | jeton aléatoire, stocké haché en base, cookie `HttpOnly`, `Secure` en ligne |
| Requêtes du navigateur | contrôle d'origine + jeton CSRF lié à la session |
| Essais de connexion et de codes | limités par compte et par adresse IP, avec attente |
| Données entre utilisateurs | chaque requête est filtrée par l'identité de la session, jamais par un champ envoyé |
| Requêtes SQL | paramétrées (pas de concaténation de saisies) |
| Journal d'audit | les faits seulement, jamais les contenus (noms, montants) |
| Conteneur de l'orchestrateur | non-root (utilisateur `fox`) depuis la phase E |
| En-têtes du proxy | CSP stricte, `frame-ancestors 'none'`, `nosniff`, micro limité à FOX, HSTS à activer |
| Inscriptions | sur invitation ; `REGISTRATION_MODE=closed` les ferme sans toucher aux comptes existants |

## 8. Limites connues (à connaître pour le jury)

- Les clés (Gemini, Groq, Telegram, Google) sont dans `.env` en clair, protégé par les droits du fichier (`chmod 600`)
  et par l'accès au serveur. Elles ne sont pas en base. Un coffre de secrets (Docker secrets, Vault) serait l'étape suivante.
- Les sauvegardes ne sont pas chiffrées : à chiffrer (par exemple `gpg`) avant toute copie hors serveur.
- Les notifications quand l'application est fermée (Web Push) ne sont pas encore faites.
- Pas d'authentification à deux facteurs sur l'interface web.
