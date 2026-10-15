CREATE TABLE IF NOT EXISTS conversations (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    user_message TEXT NOT NULL,
    bot_reply    TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_created
    ON conversations (user_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS api_usage (
    day    DATE PRIMARY KEY,
    calls  INTEGER NOT NULL DEFAULT 0,
    tokens BIGINT NOT NULL DEFAULT 0
);
