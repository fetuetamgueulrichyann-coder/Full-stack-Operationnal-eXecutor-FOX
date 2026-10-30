-- Phase B5 : agenda interne par utilisateur (rendez-vous). Migration uniquement additive.
-- `reminded_at` : le rappel avant le rendez-vous a déjà été envoyé (remis à NULL si l'horaire change).

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    title       TEXT NOT NULL,
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    location    TEXT,
    reminded_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_at > start_at)
);
CREATE INDEX IF NOT EXISTS idx_events_user_start ON events (user_id, start_at);
CREATE INDEX IF NOT EXISTS idx_events_reminders ON events (start_at) WHERE reminded_at IS NULL;
