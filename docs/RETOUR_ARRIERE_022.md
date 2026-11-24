# Retour arrière de la migration 022 (confidentialité)

**En général, tu n'as rien à faire.** La migration est additive : si tu reviens à l'ancienne version du code, elle ignore
simplement les tables et colonnes ajoutées. Laisse-les en place, c'est sans danger et sans perte.

N'utilise ce qui suit que si tu veux vraiment retirer ces tables et colonnes de la base. Les deux outils sont **séparés
volontairement** :

| Fichier | Nature | Rôle |
|---|---|---|
| `scripts/inner/privacy_022_export.psql` | commandes **psql** (`\copy`, côté client) | exporte les données de confidentialité en CSV ; ne modifie rien |
| `scripts/inner/privacy_022_rollback.sql` | **SQL pur** | supprime les tables et colonnes ; **refuse** s'il existe des données sans confirmation |

Ni l'un ni l'autre n'est dans `db/migrations/` : le lanceur de migrations ne les exécute jamais.

## Procédure

1. **Sauvegarde complète** : `scripts\backup.ps1`.
2. **Remets d'abord l'ancien code** (version d'avant la 1a), puis redémarre : `docker compose up -d --build orchestrator`.
3. **Exporte** (écrit quatre fichiers `*_022.csv` dans le dossier `backups` du projet) :

       docker compose exec db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 -f /scripts/privacy_022_export.psql'

   Vérifie que les quatre fichiers existent et copie-les ailleurs.
4. **Essaie sans confirmation** (le script ne fait que vérifier) :

       docker compose exec db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 --single-transaction -f /scripts/privacy_022_rollback.sql'

   S'il existe des réglages, autorisations, messages retenus ou échanges étiquetés, il s'arrête avec « Retour arrière refusé »
   **sans rien supprimer**. S'il n'y a aucune donnée, il retire directement les tables et colonnes.
5. **Confirme** seulement après avoir vérifié l'export :

       docker compose exec -e "PGOPTIONS=-c fox.rollback_confirmed=oui" db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1 --single-transaction -f /scripts/privacy_022_rollback.sql'

   Le tout est une seule transaction : en cas d'erreur, rien n'est modifié. Les conversations ne sont jamais supprimées ;
   seules les colonnes `data_classes` et `engine` disparaissent. La ligne 022 de `schema_migrations` est retirée, ce qui permet
   de rejouer la migration plus tard (testé : aller-retour complet).

## À savoir

- Revenir à une version du code **sans** confidentialité n'est pas neutre dès la phase 1c : les protections ne s'appliqueraient
  plus. En cas de doute, `PRIVACY_BLOCK_ALL=1` bloque tout envoi externe.
- Les CSV contiennent des identifiants de conversation et d'opération, des catégories et des dates ; jamais le texte de tes messages.
