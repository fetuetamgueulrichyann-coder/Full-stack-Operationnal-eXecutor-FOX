-- Phase B1.1 : rappels à l'heure d'une tâche.
-- `due_time` : l'échéance a une heure précise (sinon c'est seulement un jour : pas de rappel à heure fixe).
-- `reminded_at` : le rappel a déjà été envoyé (évite les doublons ; remis à NULL si l'échéance change).

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_time BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reminded_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_tasks_reminders ON tasks (due_at)
    WHERE due_time AND reminded_at IS NULL AND status IN ('a_faire', 'en_cours');
