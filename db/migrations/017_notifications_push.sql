-- Phase G : notifications Web Push (rappels hors onglet). Migration additive.
-- Les rappels Telegram gardent leurs marqueurs (reminded_at / reminded_for) : chaque canal a les siens,
-- pour que l'un ne vole jamais le rappel de l'autre.

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_ok_at  TIMESTAMPTZ,
    failures    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions (user_id);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS push_reminded_at TIMESTAMPTZ;
ALTER TABLE events ADD COLUMN IF NOT EXISTS push_reminded_at TIMESTAMPTZ;
ALTER TABLE events ADD COLUMN IF NOT EXISTS push_reminded_for TIMESTAMPTZ;

-- Les rappels déjà passés ne doivent pas être envoyés d'un coup à la mise à jour : on les marque comme faits.
UPDATE tasks SET push_reminded_at = now() WHERE push_reminded_at IS NULL AND (reminded_at IS NOT NULL OR due_at < now());
UPDATE events SET push_reminded_at = now() WHERE push_reminded_at IS NULL AND start_at < now();
