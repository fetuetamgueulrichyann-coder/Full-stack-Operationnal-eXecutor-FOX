-- Phase A2 : historique par conversation, mode « ne pas enregistrer ».
-- Migration uniquement additive : aucune ligne n'est supprimée ni modifiée dans son contenu.
-- Comme `conversations.user_id`, les nouvelles tables n'ont pas de clé étrangère vers `users`
-- (les anciennes lignes peuvent porter l'identifiant Telegram avant le rattachement au compte).

CREATE TABLE IF NOT EXISTS chats (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chats_user_updated ON chats (user_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats (updated_at);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS chat_id BIGINT REFERENCES chats (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_conversations_chat ON conversations (chat_id, created_at DESC, id DESC);

-- Mode « ne pas enregistrer » : tant que la ligne existe, rien n'est écrit dans `conversations`.
CREATE TABLE IF NOT EXISTS private_mode (
    user_id BIGINT PRIMARY KEY,
    since   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Contexte temporaire du mode privé : supprimé automatiquement après quelques minutes.
CREATE TABLE IF NOT EXISTS private_context (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    user_message TEXT NOT NULL,
    bot_reply    TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_private_context_user ON private_context (user_id, created_at DESC, id DESC);

-- Rattachement de l'historique existant : une conversation « Historique » par utilisateur.
INSERT INTO chats (user_id, title, created_at, updated_at)
SELECT user_id, 'Historique', min(created_at), max(created_at)
FROM conversations
WHERE chat_id IS NULL
GROUP BY user_id;

UPDATE conversations c
SET chat_id = ch.id
FROM chats ch
WHERE c.chat_id IS NULL AND ch.user_id = c.user_id AND ch.title = 'Historique';
