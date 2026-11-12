# API de FOX : référence pour l'interface web

Ce document décrit l'API de l'orchestrateur (Flask) telle que l'interface React doit l'utiliser. Il complète le
README. Les routes existantes ne changent pas : le bot Telegram et la future interface web appellent les mêmes.

Sommaire : [1. Principes](#1-principes) · [2. Authentification et CSRF](#2-authentification-et-csrf) ·
[3. Erreurs](#3-erreurs) · [4. Routes](#4-routes) · [5. États de chargement et attente de l'IA](#5-états-de-chargement-et-attente-de-lia) ·
[6. Déploiement sur un seul domaine](#6-déploiement-sur-un-seul-domaine) · [7. Exemple de client](#7-exemple-de-client-react)

## 1. Principes

- **Même domaine, pas de CORS.** En production, React et l'API sont servis par le même domaine
  (`https://fox.example.com`). L'API est sous le préfixe `/api` : le proxy retire ce préfixe, donc
  `/api/tasks` côté navigateur correspond à `/tasks` côté Flask. Les chemins ci-dessous sont ceux de Flask.
- **JSON partout.** Toute requête `POST`, `PUT`, `PATCH` ou `DELETE` envoie `Content-Type: application/json`
  (même avec un corps vide : `{}`), sinon l'API répond `415`. Les réponses sont du JSON, sauf le PDF d'un devis.
- **Réponses en objets nommés** : `{"tasks": [...]}`, `{"task": {...}}`, jamais un tableau seul. On peut donc
  ajouter des champs sans casser le front.
- **Dates** : ISO 8601 avec le décalage horaire, dans le fuseau de l'utilisateur (`/timezone`).
- **Montants** : texte déjà formaté (`"355 365 XAF"`), plus un objet `amounts` en chaînes décimales pour les calculs.
- **Isolation** : l'identité vient toujours de la session (cookie), jamais d'un identifiant envoyé par le client.
- **Pas de mise en cache** : chaque réponse porte `Cache-Control: no-store`.

## 2. Authentification et CSRF

L'interface web s'authentifie avec le cookie de session `fox_session` (HttpOnly, `SameSite=Lax`, `Secure` en
production). Le JavaScript ne peut pas le lire. Le front doit donc :

1. envoyer les cookies avec **chaque** requête : `fetch(url, { credentials: "include" })` ;
2. envoyer le jeton CSRF avec chaque requête qui **modifie** quelque chose (`POST`, `PUT`, `PATCH`, `DELETE`),
   dans l'en-tête `X-CSRF-Token`.

**Où trouver le jeton.** `POST /auth/login`, `POST /auth/register` et `GET /auth/me` le renvoient dans le champ
`csrf_token`. Il est lié à la session : il change à chaque connexion. Au chargement de la page, le front appelle
`/auth/me` : 200 donne l'utilisateur et le jeton, 401 renvoie vers la connexion. Le jeton se garde en mémoire (pas
dans `localStorage`).

**Ce que le serveur vérifie** pour une requête modifiante portant le cookie :

| Contrôle | Règle | Échec |
|---|---|---|
| Origine | `Origin` (ou `Referer`) doit être l'origine de l'API ou figurer dans `PUBLIC_ORIGIN` / `ALLOWED_ORIGINS` | `403`, `origin_forbidden` ; `origin_missing` si absent |
| CSRF | `X-CSRF-Token` doit être le jeton de la session | `403`, `csrf_invalid` |

La connexion et l'inscription (pas encore de session) sont soumises à la vérification d'origine seulement. Les
lectures (`GET`) ne sont pas concernées. Les navigateurs envoient `Origin` tout seuls : le front n'a rien à faire
pour cela.

**Bot Telegram.** Il s'identifie avec l'en-tête `X-Fox-Service-Token` et un `telegram_id` : ni cookie, ni jeton CSRF.
Ces routes ne sont pas destinées au navigateur.

**Session expirée.** Une requête avec un cookie périmé répond `401` (`unauthenticated`) : le front revient à la page de
connexion.

## 3. Erreurs

Toute erreur a la même forme :

```json
{ "error": "message lisible en français", "code": "identifiant_stable" }
```

Le front affiche `error` à l'utilisateur et se décide d'après `code` et le statut HTTP, jamais d'après le texte.
Certaines erreurs ajoutent des champs (par exemple `scope` pour un quota).

| Statut | `code` | Quand |
|---|---|---|
| 400 | `bad_request` (ou un code précis : `username_invalid`, `password_weak`, `invitation_invalid`) | Données invalides |
| 401 | `unauthenticated` | Pas de session valide |
| 401 | `invalid_credentials` | Mauvais identifiant ou mot de passe |
| 403 | `forbidden` | Compte non autorisé pour cette route |
| 403 | `origin_forbidden`, `origin_missing`, `csrf_invalid` | Contrôles de sécurité web (section 2) |
| 404 | `not_found` | Ressource introuvable (ou appartenant à un autre utilisateur) |
| 405 | `method_not_allowed` | Mauvaise méthode HTTP |
| 409 | `conflict`, `username_taken`, `account_exists` | Conflit (limite atteinte, nom déjà pris) |
| 403 | `registration_closed` | Inscriptions fermées (`REGISTRATION_MODE=closed`) |
| 413 | `payload_too_large` | Texte trop long (4 000 caractères) |
| 415 | `unsupported_media_type` | Corps non JSON |
| 429 | `quota_exceeded` | Plafond journalier de l'utilisateur ou global (champ `scope`) |
| 429 | `too_many_attempts` | Trop d'essais de connexion |
| 502 | `upstream_error` | Le modèle d'IA a échoué (champ `upstream_status`) |
| 503 | `model_unavailable`, `capacity_reached` | Modèle indisponible ou capacité du fournisseur atteinte |
| 500 | `internal_error` | Erreur inattendue du serveur |

Conduite du front : `401` → page de connexion ; `429` → afficher le message et la limite ; `502` et `503` → message
« FOX est momentanément indisponible » avec un bouton Réessayer ; `403` de sécurité → recharger la page (le jeton ne
correspond plus à la session).

## 4. Routes

Légende : 🔒 session (cookie) requise ; ✍️ requête modifiante (jeton CSRF) ; 🤖 réservé au bot Telegram.

### Santé

| Route | Description |
|---|---|
| `GET /health` | `{"status": "ok"}`. Sans authentification, pour les sondes. |

### Comptes

| Route | Corps | Réponse |
|---|---|---|
| `POST /auth/register` | `code` (invitation), `username`, `password` | `201` `{user, csrf_token}` + cookie de session. Erreurs : `invitation_invalid`, `username_taken`, `username_invalid`, `password_weak`. |
| `POST /auth/login` | `username`, `password` | `{user, csrf_token}` + cookie. Erreurs : `invalid_credentials` (401), `too_many_attempts` (429). |
| `POST /auth/logout` ✍️ | `{}` | `{"status": "ok"}`, cookie supprimé. |
| `GET /auth/me` 🔒 | | `{user, csrf_token}`. `user` : `id`, `username`, `role` (`owner` ou `tester`), `timezone` (`null` = fuseau par défaut). |
| `POST /auth/telegram` 🤖 | `code`, `telegram_id` | Inscription d'un testeur depuis Telegram. |

### Discussion

| Route | Corps / paramètres | Réponse |
|---|---|---|
| `POST /message` 🔒✍️ | `text` (obligatoire, 4 000 caractères au plus) ; `chat_id` (facultatif) ; `voice: true` (facultatif : conversation orale, FOX répond court, fait pour être lu à voix haute) | `{reply, pending, chat_id, private}`. `pending` est une liste d'actions à confirmer : `[{id, summary}]`. Erreurs : 400, 413, 429 `quota_exceeded`, 502, 503. |
| `POST /actions/<id>/confirm` 🔒✍️ | `{}` | `{reply}` : résultat de l'action confirmée. |
| `POST /actions/<id>/cancel` 🔒✍️ | `{}` | `{reply}`. |
| `GET /chats` 🔒 | | `{chats: [{id, title, created_at, updated_at, messages}], current, private}`. `current` : conversation active non vide ou `null`. |
| `GET /chats/<id>/messages` 🔒 | | `{chat_id, messages: [{id, role: "user"\|"assistant", text, at}]}` : relire une conversation (200 derniers échanges). 404 si elle n'est pas à vous. |
| `POST /chats` 🔒✍️ | `{}` | `201` `{chat_id}` : nouvelle conversation. |
| `POST /chats/<id>/open` 🔒✍️ | `{}` | `{chat_id, private: false}` : reprendre une conversation. 404 si introuvable. |
| `DELETE /chats/<id>` 🔒✍️ | `{}` | `{status: "deleted"}`. |
| `DELETE /chats` 🔒✍️ | `{"confirm": "all"}` | `{status: "deleted", chats}` : supprime toutes les conversations. |
| `POST /private` 🔒✍️ | `{"on": true/false}` | `{private}` : mode « ne pas enregistrer ». |

Une action sensible (supprimer un rendez-vous, envoyer un e-mail…) n'est jamais exécutée directement : `/message`
renvoie `pending`, l'interface affiche une carte « Action en attente » avec Confirmer / Annuler, puis appelle
`/actions/<id>/confirm` ou `/cancel`. Une action expire au bout de `PENDING_TTL_MINUTES` (10 par défaut).

### Tâches

| Route | Corps / paramètres | Réponse |
|---|---|---|
| `GET /tasks` 🔒 | `?status=open` (défaut), `all`, `a_faire`, `en_cours`, `terminee`, `annulee` | `{tasks: [{id, title, priority, status, overdue, due_at, created_at, done_at, event_id, event_title?}]}` |
| `POST /tasks` 🔒✍️ | `title` ou `text` (« Titre \| échéance \| priorité \| rdv N »), `due`, `priority` (`haute`, `normale`, `basse`), `event_id` | `201` `{task}`. 404 si le rendez-vous n'existe pas, 409 si limite de tâches ouvertes atteinte. |
| `PATCH /tasks/<id>` 🔒✍️ | un ou plusieurs champs : `title`, `due`, `priority`, `status`, `event_id` | `{task}`. 400 si rien à modifier. |
| `DELETE /tasks/<id>` 🔒✍️ | `{}` | `{status: "deleted"}` |

### Agenda

| Route | Corps / paramètres | Réponse |
|---|---|---|
| `GET /events` 🔒 | `?range=aujourd'hui`, `demain`, `semaine` (défaut), `mois` | `{today, reminder_minutes, events: [{id, title, location, start, end, repeat?, occurrence?, tasks: [...]}]}`. Les séries sont développées en occurrences. |
| `GET /events/<id>` 🔒 | | `{event}` |
| `POST /events` 🔒✍️ | `text` (« Titre \| quand \| durée \| lieu \| répétition ») **ou** `title`, `start`, `duration`, `location`, `repeat`, `until` | `201` `{event, conflicts: [...]}`. Une série garde le fuseau de sa création. |
| `PATCH /events/<id>` 🔒✍️ | champs à changer (`title`, `start`, `duration`, `location`, `repeat`, `until`) | `{event, conflicts}` |
| `DELETE /events/<id>` 🔒✍️ | `{}` pour tout supprimer, ou `{"occurrence": "AAAA-MM-JJ"}` pour une seule date d'une série | `{status: "deleted"}` ou `{status: "occurrence_deleted"}` |
| `GET /timezone` 🔒 | | `{timezone, default, custom, now}` |
| `POST /timezone` ✍️ | `{"timezone": "Europe/Paris"}` ou `"defaut"` | même forme que `GET /timezone` |
| `POST /events/reminders/claim` 🤖 | | Rappels de rendez-vous dus (bot). |

### Clients et devis

| Route | Corps / paramètres | Réponse |
|---|---|---|
| `GET /clients` 🔒 | `?q=` (recherche) | `{clients: [...]}` |
| `GET /clients/find` 🔒 | `?q=` (obligatoire) | `{clients: [...]}` |
| `POST /clients` 🔒✍️ | `text` (« Nom ; téléphone ; e-mail ; adresse ») **ou** `name` (obligatoire) et les autres champs | `201` `{client}`. 409 si doublon ou limite atteinte. |
| `GET /clients/<id>` 🔒 | | `{client}` |
| `PATCH /clients/<id>` 🔒✍️ | `text` (« téléphone ; e-mail ; adresse ») **ou** les champs à changer | `{client}`. 400 si rien à modifier. |
| `DELETE /clients/<id>` 🔒✍️ | `{}` | `{status: "deleted"}` |
| `GET /quotes` 🔒 | `?status=brouillon\|envoye\|accepte\|refuse`, `?archived=1` (devis archivés) | `{quotes: [{id, number, status, status_label, client, total, archived}]}` |
| `POST /quotes` 🔒✍️ | `text` (texte libre du devis) | `201` `{quote}`. 409 si plusieurs clients correspondent ou limite atteinte. |
| `GET /quotes/<ref>` 🔒 | `ref` : numéro `DV-2026-003` ou identifiant | `{quote: {number, status, client, currency, vat_rate, lines, subtotal, vat, total, amounts}}` |
| `PATCH /quotes/<ref>` 🔒✍️ | `status` et/ou `archived` (booléen) | `{quote}` |
| `GET /actions/pending` 🔒 | | `{actions: [{id, summary, expires_at}]}` : actions en attente de confirmation (non expirées). |
| `GET /projects` 🔒 | `?status=en_cours\|suspendu\|termine`, `?archived=1` | `{projects: [{id, name, status, status_label, client, quote, budget, income, expense, margin, budget_left, budget_used_percent, archived, …_label}]}` (montants en chaînes décimales, plus une version `_label` formatée). |
| `POST /projects` 🔒✍️ | `name` (requis), `client`, `quote` (numéro de devis), `budget`, `start_date`, `end_date`, `notes` | `201 {project}` ; `409` si le nom existe ou si la limite (`MAX_PROJECTS`) est atteinte. |
| `GET /projects/<id>` 🔒 | | `{project}` ou `404`. |
| `PATCH /projects/<id>` 🔒✍️ | `status`, `budget` (vide = aucun), `start_date`, `end_date`, `notes`, `archived` (booléen) | `{project}`. Il n'y a pas de `DELETE` : un chantier s'archive. |
| `GET /ledger` 🔒 | `?month=AAAA-MM` (défaut : mois courant, `all` = tout), `kind=recette\|depense`, `project=<id>` | `{month, entries: [{id, kind, amount, amount_label, date, category, description, project_id, project}]}`. |
| `GET /ledger/summary` 🔒 | `?month=AAAA-MM`, `project=<id>` | `{summary: {month, income, expense, balance, entries, by_category: […]}}` (+ `_label`). |
| `POST /ledger` 🔒✍️ | `kind` (recette ou dépense), `amount` (> 0), `description` (requis), `category`, `date` (AAAA-MM-JJ, défaut aujourd'hui), `project` (numéro) | `201 {entry}` ; `404` si le chantier n'existe pas pour cet utilisateur. |
| `DELETE /ledger/<id>` 🔒✍️ | | `{status: "deleted"}` ou `404`. |
| `GET /push/config` 🔒 | | `{enabled, public_key, devices, missing}` : notifications configurées (clés VAPID), nombre d'appareils de l'utilisateur, et variables VAPID absentes du serveur (diagnostic). |
| `POST /push/subscribe` 🔒✍️ | `endpoint`, `keys: {p256dh, auth}` (l'abonnement du navigateur) | `201 {status, devices}` ; `400` si l'adresse n'est pas celle d'un service de push connu (HTTPS) ; `409` si non configuré ou limite (`MAX_PUSH_DEVICES`) atteinte. |
| `POST /push/unsubscribe` 🔒✍️ | `endpoint` | `{status, devices}` (ne retire que les appareils de l'utilisateur). |
| `POST /push/test` 🔒✍️ | | `{sent}` : envoie une notification de test à ses appareils ; `404` s'il n'en a aucun. |
| `GET /voice/config` 🔒 | | `{enabled, voice, daily_limit, used}` : la voix est-elle disponible, et combien de caractères reste-t-il aujourd'hui. |
| `POST /voice/speak` 🔒✍️ | `text` | Réponse `audio/wav` (le texte est nettoyé : sans Markdown, code ni adresses, 1 200 caractères au plus). `400` texte vide ; `409` voix désactivée ; `429` plafond du jour (`voice_quota`) ou tous les modèles saturés (`voice_busy`, caractères rendus) ; `502` synthèse indisponible (`voice_unavailable`, caractères rendus). Rien n'est enregistré, même en mode privé. |
| `DELETE /quotes/<ref>` 🔒✍️ | `{}` | `{status: "deleted"}`. 409 si le devis est envoyé ou accepté. |
| `GET /quotes/<ref>/pdf` 🔒 | | Le fichier PDF (pas du JSON). |
| `GET /quotes/<ref>/mail` 🔒 | | Brouillon d'e-mail au client. |
| `GET /business` · `PUT /business` ✍️ · `DELETE /business` ✍️ | `PUT` : `text` | `{business}` : profil d'entreprise |

### Mémoire, journal, briefing

| Route | Corps / paramètres | Réponse |
|---|---|---|
| `GET /memory` 🔒 | | `{items: [{id, content, source, created_at}]}` |
| `POST /memory` 🔒✍️ | `text` | `201` `{item}`. 409 si limite atteinte. |
| `DELETE /memory/<id>` 🔒✍️ | `{}` | `{status: "deleted"}` |
| `DELETE /memory` 🔒✍️ | `{"confirm": "all"}` | `{status: "deleted", count}` |
| `GET /journal` 🔒 | `?limit=15` | `{entries: [{at, text}]}` |
| `GET /briefing` 🔒 | | `{enabled, time}` (heure « HH:MM ») |
| `POST /briefing` 🔒✍️ | `enabled` (booléen) et/ou `time` (« 07:30 ») | `{enabled, time}` mis à jour |
| `GET /briefing/preview` 🔒 | | `{text}` : le briefing du jour, tel qu'il serait envoyé |
| `POST /briefings/claim` 🤖 | | Briefings dus (bot). |
| `POST /reminders/claim` 🤖 | | Rappels de tâches dus (bot). |

### E-mails

| Route | Corps | Réponse |
|---|---|---|
| `POST /mail/draft` 🔒✍️ | `instruction` | `{to, subject, body}` |
| `POST /mail/improve` 🔒✍️ | `text` | `{text}` |
| `POST /mail/summary` 🔒✍️ | `text` | `{summary, pending}` : tâches proposées à confirmer |

Ces trois routes comptent dans le quota journalier, comme `/message`. FOX ne les envoie jamais : l'utilisateur
copie le brouillon ou ouvre un lien Gmail prérempli.

## 5. États de chargement et attente de l'IA

**Conventions pour toutes les vues.** Le serveur ne gère aucun état de chargement ; React en gère quatre par requête :

| État | Affichage |
|---|---|
| `idle` | Rien, ou le contenu précédent |
| `loading` | Squelette ou indicateur ; les boutons d'envoi sont désactivés pour éviter les doubles envois |
| `error` | Message de l'erreur (`error`), bouton Réessayer, redirection si `401` |
| `success` | Contenu ; un état « vide » distinct (« Aucune tâche ») quand la liste est vide |

**Attente de la réponse de l'IA (`/message`).** C'est une requête classique : le serveur répond une seule fois,
quand le modèle a fini. Elle dure en général 1 à 5 secondes, et peut atteindre 40 secondes quand le serveur
réessaie ou bascule sur le modèle de secours. Côté interface :

1. afficher tout de suite le message de l'utilisateur, puis une bulle « FOX réfléchit… » (trois points animés) ;
2. désactiver l'envoi tant que la réponse n'est pas arrivée ;
3. après environ 10 secondes, ajouter « Cela prend plus de temps que d'habitude… » ;
4. fixer un délai maximal côté navigateur de **60 secondes** (`AbortController`) : le serveur coupe à 120 secondes ;
5. en cas d'échec ou de délai dépassé, garder le texte saisi et proposer Réessayer. Attention : si le délai est
   dépassé, le message a pu être traité. Recharger la conversation (`/chats`) avant de renvoyer ;
6. une réponse avec `pending` affiche la carte de confirmation, pas un texte ordinaire.

**Streaming (plus tard, non implémenté).** Pour afficher la réponse au fur et à mesure, on ajoutera une route
`POST /message/stream` en Server-Sent Events, sans toucher à `/message`. Évènements prévus : `delta` (morceau de
texte), `pending` (actions à confirmer), `done` (`chat_id`, `private`), `error` (`code`, `error`). Prérequis :
désactiver la mise en tampon dans le proxy (`flush_interval -1` dans Caddy) et gérer l'annulation côté client. Le
front peut donc être écrit dès maintenant avec une couche « envoi de message » isolée, qu'on remplacera sans
réécrire les écrans.

## 6. Déploiement sur un seul domaine

```
Navigateur ──https──▶ Caddy (profil Docker « prod », ports 80 et 443)
                         ├─ /api/*  ──▶ orchestrateur Flask (réseau Docker, port 5000)
                         └─ le reste ─▶ fichiers statiques de React (web/dist)
```

- Configuration du proxy : `deploy/Caddyfile`. Service `proxy` dans `docker-compose.yml` (profil `prod`).
- Dans `.env` (à ajouter à la main, jamais dans Git) :

| Variable | Local | Production (VPS) |
|---|---|---|
| `DOMAIN` | `localhost` | `fox.example.com` |
| `PUBLIC_ORIGIN` | vide | `https://fox.example.com` |
| `ALLOWED_ORIGINS` | vide (utile seulement si le front et l'API ont des adresses différentes) | vide |
| `COOKIE_SECURE` | `0` | `1` (obligatoire derrière HTTPS) |
| `TRUST_PROXY` | `0` | `1` (IP, schéma et hôte réels derrière Caddy) |

- Mise en route : construire React (`npm run build` produit `web/dist`), puis
  `docker compose --profile prod up -d proxy`.
- Développement : le serveur de dev React (Vite) redirige `/api` vers `http://localhost:5000` en retirant le
  préfixe et conserve l'en-tête `Host` du navigateur : l'API voit la même origine, aucun réglage n'est nécessaire.
- Le port 5000 de l'orchestrateur reste lié à `127.0.0.1` : seul le proxy est exposé à Internet.
- Sur le VPS : pare-feu limité aux ports 22, 80 et 443, et suppression du port publié 5000 si on veut isoler
  complètement l'API (phase E).

## 7. Exemple de client React

```ts
let csrf = "";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const unsafe = method !== "GET";
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    signal: options.signal,
    headers: {
      ...(unsafe ? { "Content-Type": "application/json", "X-CSRF-Token": csrf } : {}),
    },
    body: unsafe ? JSON.stringify(options.body ?? {}) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, data.code ?? "error", data.error ?? "Erreur inattendue");
  if (data.csrf_token) csrf = data.csrf_token;
  return data as T;
}

// Au démarrage : const { user } = await api<{ user: User }>("/auth/me");  (401 → page de connexion)
```
