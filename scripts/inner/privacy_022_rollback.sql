-- Retour arrière de la migration 022 (confidentialité, phase 1a). SQL pur, MANUEL, jamais appliqué par
-- le lanceur de migrations. Voir docs/RETOUR_ARRIERE_022.md : exporte d'abord (privacy_022_export.psql).
--
-- Garde-fou : si une table contient des lignes (ou si des échanges sont étiquetés), le script s'arrête
-- avec une erreur, sans rien supprimer, tant que la confirmation n'est pas donnée. Lancer avec :
--
--   psql -v ON_ERROR_STOP=1 --single-transaction -c "SET fox.rollback_confirmed = 'oui'" -f privacy_022_rollback.sql
--
-- (sans la confirmation, le script ne fait que vérifier et refuse s'il y a des données.)

DO $$
DECLARE
    n_settings INTEGER;
    n_grants   INTEGER;
    n_held     INTEGER;
    n_labeled  INTEGER;
    confirmed  BOOLEAN := coalesce(current_setting('fox.rollback_confirmed', true), '') = 'oui';
BEGIN
    SELECT count(*) INTO n_settings FROM privacy_settings;
    SELECT count(*) INTO n_grants FROM data_grants;
    SELECT count(*) INTO n_held FROM held_messages;
    SELECT count(*) INTO n_labeled FROM conversations WHERE data_classes IS NOT NULL OR engine IS NOT NULL;
    RAISE NOTICE 'privacy_settings=%, data_grants=%, held_messages=%, conversations étiquetées=%',
        n_settings, n_grants, n_held, n_labeled;
    IF (n_settings + n_grants + n_held + n_labeled) > 0 AND NOT confirmed THEN
        RAISE EXCEPTION 'Retour arrière refusé : des données existent. Exporte-les (privacy_022_export.psql), garde les fichiers, puis relance avec SET fox.rollback_confirmed = ''oui''.';
    END IF;
END
$$;

DROP TABLE IF EXISTS held_messages;
DROP TABLE IF EXISTS data_grants;
DROP TABLE IF EXISTS privacy_settings;
ALTER TABLE conversations DROP COLUMN IF EXISTS data_classes;
ALTER TABLE conversations DROP COLUMN IF EXISTS engine;
DELETE FROM schema_migrations WHERE version = 22;
