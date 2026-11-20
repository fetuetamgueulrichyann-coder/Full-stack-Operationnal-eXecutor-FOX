-- Phase J1 : fichiers joints (photos, PDF, Word, Excel, texte, archives). Migration additive.
-- Le contenu est gardé dans la base (donc dans les sauvegardes). Comme `tasks`, pas de clé étrangère vers `users`.
CREATE TABLE IF NOT EXISTS files (
    id         UUID PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    name       TEXT NOT NULL,
    mime       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    data       BYTEA NOT NULL,
    attached   BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_files_user ON files (user_id, created_at);

-- Fichiers joints à un message (liste de {id, name, kind, size}), pour les réafficher dans l'historique.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb;
