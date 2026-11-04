# FOX : interface web

React 18 + TypeScript + Vite. L'interface parle à l'API de FOX (voir `../docs/API.md`) : cookies de session,
jeton CSRF, erreurs `{error, code}`.

## Pages livrées

| Route | Contenu |
|---|---|
| `/connexion`, `/inscription` | Connexion et création de compte par code d'invitation |
| (démarrage) | Écran de chargement : « connexion lente » après 2 s, « Réessayer » si le serveur est injoignable |
| `/` | Discussion : conversations, historique, mode privé, cartes de confirmation des actions, attente de l'IA (« FOX réfléchit… », délai maximal 60 s, Réessayer) |
| `/agenda`, `/taches`, `/devis`, `/prive`, `/reglages` | Pages à venir (menu déjà en place) |

## Avec Docker (aucune installation de Node)

```powershell
docker compose up -d --build orchestrator
docker compose --profile dev up web_dev            # développement : http://localhost:5173
docker compose --profile test run --rm web_tests   # vérification des types + tests
docker compose --profile build run --rm web_build  # construit web/dist pour le proxy de production
```

## Sans Docker (Node 20 ou plus)

```powershell
cd web
npm install
npm run dev        # http://localhost:5173, l'API est attendue sur http://localhost:5000
npm test
npm run build
```

Le serveur de développement redirige `/api` vers l'orchestrateur et retire ce préfixe, exactement comme le
proxy Caddy de production (`../deploy/Caddyfile`). Pour viser un autre serveur : `VITE_API_TARGET`.

## Organisation

- `src/api/client.ts` : seul endroit qui appelle `fetch` (cookies, CSRF, erreurs).
- `src/auth/AuthContext.tsx` : état de connexion, démarrage (`/auth/me`), expiration de session.
- `src/pages/` : une page par écran. `src/components/` : coque, champs, écran de chargement.
- `src/test/` : faux serveur pour les tests.
