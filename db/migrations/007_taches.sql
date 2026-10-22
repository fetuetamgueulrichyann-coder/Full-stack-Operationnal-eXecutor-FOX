-- Phase B1 : tâches persistantes.
-- Migration uniquement additive. Comme `chats`, pas de clé étrangère vers `users`.

CREATE TABLE IF NOT EXISTS tasks (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    title      TEXT NOT NULL,
    priority   TEXT NOT NULL DEFAULT 'normale' CHECK (priority IN ('haute', 'normale', 'basse')),
    due_at     TIMESTAMPTZ,
    status     TEXT NOT NULL DEFAULT 'a_faire' CHECK (status IN ('a_faire', 'en_cours', 'terminee', 'annulee')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks (user_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_closed ON tasks (updated_at) WHERE status IN ('terminee', 'annulee');
