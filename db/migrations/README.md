# Migrations de la base de données

Le schéma de départ est créé par `db/init/` (001 à 003) au premier démarrage de la base.
Toute évolution ensuite passe par un fichier numéroté ici, en continuant la numérotation :

    004_ajout_utilisateurs.sql
    005_agenda_interne.sql

Règles :

- ne **jamais** modifier une migration déjà appliquée (le lanceur compare une somme de
  contrôle et refuse de démarrer) : crée la suivante ;
- préférer les opérations qui ajoutent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN`) ;
- une opération destructive (`DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, `DELETE FROM`,
  changement de type de colonne) est refusée par défaut. Pour l'autoriser : sauvegarder la
  base (`scripts/backup`), ajouter dans le fichier la ligne
  `-- destructive-approved: raison` et lancer avec `ALLOW_DESTRUCTIVE_MIGRATIONS=1` ;
- pas de `CREATE INDEX CONCURRENTLY` (chaque migration s'exécute dans une transaction).

Le lanceur (`orchestrator/migrate.py`) s'exécute au démarrage de l'orchestrateur.
Vérifier l'état à la main : `docker compose exec orchestrator python migrate.py --status`.
