-- Phase B6 : fuseau horaire par utilisateur, rendez-vous récurrents, tâches liées à un rendez-vous.
-- Migration uniquement additive (aucune donnée existante n'est modifiée).

-- Fuseau horaire choisi par l'utilisateur (nom IANA). NULL : fuseau par défaut de FOX (variable TIMEZONE).
ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;

-- Séries de rendez-vous. `start_at`/`end_at` sont la première occurrence ; les suivantes sont calculées à la lecture.
--   recurrence       : daily | weekdays (lundi-vendredi) | weekly | monthly (même quantième, ramené à la fin du mois)
--   recurrence_until : dernier jour (inclus, jour local de la série) ; NULL = sans fin
--   skipped_days     : occurrences supprimées une par une (jour local)
--   series_tz        : fuseau dans lequel l'heure de la série est fixée (indépendant d'un changement de fuseau ultérieur)
--   reminded_for     : début de la dernière occurrence déjà rappelée
ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence TEXT CHECK (recurrence IN ('daily', 'weekdays', 'weekly', 'monthly'));
ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence_until DATE;
ALTER TABLE events ADD COLUMN IF NOT EXISTS skipped_days DATE[] NOT NULL DEFAULT '{}';
ALTER TABLE events ADD COLUMN IF NOT EXISTS series_tz TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS reminded_for TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_events_series ON events (user_id) WHERE recurrence IS NOT NULL;

-- Une tâche peut être liée à un rendez-vous ; supprimer le rendez-vous conserve la tâche.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS event_id BIGINT REFERENCES events (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_event ON tasks (event_id) WHERE event_id IS NOT NULL;
