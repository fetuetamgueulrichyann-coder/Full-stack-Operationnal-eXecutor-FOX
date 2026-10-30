# Évolutions à reporter dans le cahier des charges et les prompts

Ce fichier suit, au fil du développement, ce qui change par rapport au cahier des charges
initial (13 pages) et au document « Prompts de développement ». Il sert de base à la mise à
jour de ces deux documents. Dernière mise à jour : 28/10/2026 (phases A2b, B1 à B4 et C livrées : mise en forme, tâches, rappels, mémoire, journal, briefing, e-mails, clients, devis en PDF).

## Décisions validées

| Sujet | Cahier initial | Décision |
|---|---|---|
| Interface | Telegram principal, interface web de configuration | Une PWA responsive (ordinateur et Android) devient l'interface finale ; Telegram reste compatible pendant la transition |
| Voix | ElevenLabs ou Hume, reconnaissance du locuteur (ECAPA-TDNN) | Commande vocale du navigateur (Web Speech API), saisie écrite en secours ; compatibilité, HTTPS et confidentialité du moteur vocal à vérifier ; reconnaissance du locuteur repoussée |
| E-mails | Gmail OAuth (lecture, tri, envoi supervisé) | Version publique : rédaction, amélioration, résumé d'un e-mail collé et lien Gmail prérempli, envoi manuel ; pas de Gmail API ; option « Ne pas enregistrer » par défaut pour les e-mails collés |
| Agenda | Google Calendar pour l'utilisateur | Agenda interne PostgreSQL par utilisateur (validation des dates, détection de conflits) pour les testeurs ; Google Calendar réservé au propriétaire |
| Agents | Un orchestrateur, outils extensibles | Modules métier au-dessus de l'orchestrateur, sans agents IA imbriqués : commercial d'abord (clients, devis, e-mails), puis comptabilité et chantiers |
| Calculs | Non précisé | Tous les calculs (devis, totaux, TVA) effectués par Python ; devise XAF par défaut, devise et TVA configurables |
| Public cible | Indépendants, étudiants-entrepreneurs, PME | Indépendants, petites entreprises et PME, orientation commerciale et BTP ; scénario de démonstration réaliste et limité |
| Authentification | Restriction à l'identifiant Telegram du propriétaire | Comptes, sessions sécurisées, inscription par codes d'invitation ; l'identité vient de la session, jamais d'un identifiant envoyé par le client |
| Hébergement | VPS Hetzner dès le mois 1 | Développement local d'abord ; VPS dans environ deux mois (HTTPS, sauvegardes, déploiement) |
| Fournisseurs IA | Gemini, secours Groq | Gemini pour le propriétaire ; Groq par défaut pour les testeurs ; aucune bascule automatique des testeurs vers Gemini gratuit tant que cet usage n'est pas validé |
| Données | Journal d'audit, sauvegarde | Historique consultable et supprimable, données séparées par utilisateur, durée de conservation définie, logs sans contenu personnel |

## Risques à ajouter (section 11)

| Risque | Impact | Mitigation |
|---|---|---|
| Conditions Gemini gratuit : contenus utilisés pour améliorer les produits Google, relecture humaine possible, niveau gratuit non autorisé pour l'EEE, la Suisse et le Royaume-Uni | Données de testeurs exposées ; non-conformité | Groq par défaut pour les testeurs ; consentement et avertissement à l'inscription ; testeurs majeurs uniquement ; relire les conditions avant ouverture |
| Quota gratuit Groq partagé par tout le compte (environ 200 000 tokens et 1 000 requêtes par jour sur `openai/gpt-oss-20b`) | Service indisponible pour tous les testeurs | Quotas par utilisateur et par minute, plafond global, message clair |
| Modèles Groq retirés (`llama-3.3-70b-versatile` et `llama-3.1-8b-instant` fermés le 16/08/2026) | Secours en panne silencieuse | `check_models.py` vérifie le modèle configuré ; modèle configurable sans changement de code |
| Jeton Google (agenda du propriétaire) expiré au bout de 7 jours tant que l'application Google Cloud est en mode « Test » (`invalid_grant`) | Agenda indisponible dans le briefing et les outils | Publier l'application (écran de consentement complété) ou régénérer le jeton avec `scripts/get_google_token.py` ; à terme, compte de service pour le déploiement |
| Évolution du schéma de base sans perte | Perte de données | Migrations numérotées non destructives, sauvegardes avant mise à jour, opérations destructives refusées par défaut |

## Avancement par rapport aux prompts

| Prompt | État au 28/10/2026 |
|---|---|
| 1 à 4 (fondations) | Fait |
| 5, 5.1, 7, 8, 9 (outils, confirmation, créer, lire, annuler) | Fait |
| 6 (OAuth Google par utilisateur) | Remplacé : Google Calendar reste personnel au propriétaire ; agenda interne pour les testeurs |
| 10 (tests fonctionnels) | Partiel : suite pytest de non-régression (1 083 tests au 28/10/2026) ; jeu de 30 commandes en langage naturel à écrire |
| 11, 12 (Gmail OAuth, envoi) | Remplacés par la rédaction, le résumé et le lien Gmail prérempli |
| 13 (permissions et audit généralisés) | Partiel : rôles et contrôle à l'exécution faits (phase A1) ; audit des connexions et inscriptions fait |
| 16 (quotas et secours) | Fait, y compris quotas par utilisateur et plafond Groq global (phase A1) |
| 18 (suivi des coûts) | Partiel : compteurs globaux, par utilisateur et par fournisseur (phase A1) ; tableau de suivi à faire |
| 19 (sauvegardes) | Fait : scripts de sauvegarde et de restauration (phase 0) |
| 14, 15, 17, 20 à 25 | À faire |

## Prompts à ajouter

1. Migrations numérotées et sauvegardes (fait, phase 0).
2. Comptes, sessions, codes d'invitation, rôles propriétaire et testeur (fait, phase A1).
3. Quotas par utilisateur (fait, phase A1) et par minute (à faire), configurables ; plafond global.
4. Historique par conversation : consultation, suppression, option « Ne pas enregistrer », purge (fait, phase A2a ; mise en forme des réponses : fait, phase A2b).
4bis. Tâches persistantes : priorité, échéance, statut, suivi jusqu'à résolution (fait, phase B1). Rappels à l'heure d'une tâche, mémoire contrôlable (/memoire, /retiens) et journal des actions (/journal) : fait, phase B2. Briefing du matin avec tâches, échéances et agenda, activable par chacun (/briefing) : fait, phase B3. E-mails reliés aux tâches (/mail : brouillon avec lien Gmail, amélioration, résumé avec tâches à confirmer ; sans lecture de la boîte) : fait, phase B4.
5. Agenda interne par utilisateur (/rdv, /agenda, détection de conflits, rappels avant rendez-vous, fusion dans le briefing) : fait, phase B5. Reste : fuseau horaire par utilisateur, récurrence, outils IA de même nom que Google.
6. E-mails : rédaction, amélioration, résumé, lien Gmail prérempli, bloc copiable (fait, phase B4).
7. Module commercial : clients, devis (lignes, totaux, TVA, numérotation) calculés en Python (fait, phase C : /clients, /client, /devis ; PDF du devis, profil d'entreprise et brouillon d'e-mail au client : fait, phase C3 ; clients/devis dans la PWA à venir).
8. PWA : API, chat, historique, agenda, clients et devis, vie privée, consommation, voix.
9. Préparation du VPS : Docker Compose de production, HTTPS, sauvegardes, déploiement.
