-- Phase B2 : mémoire contrôlable par l'utilisateur.
-- Migration additive. Pas de clé étrangère vers `users` (comme chats et tasks).

CREATE TABLE IF NOT EXISTS memories (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'fox')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_user ON memories (user_id, created_at DESC, id DESC);
