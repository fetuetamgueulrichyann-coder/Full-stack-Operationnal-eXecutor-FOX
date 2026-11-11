-- Phase H : voix de FOX (synthèse vocale). Migration additive.
-- Compte les caractères envoyés à la synthèse vocale, par utilisateur et par jour (UTC).
CREATE TABLE IF NOT EXISTS voice_usage (
    user_id  BIGINT NOT NULL,
    day      DATE NOT NULL,
    chars    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
