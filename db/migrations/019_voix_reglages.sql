-- Phase H7 : réglages de voix par utilisateur (voix Gemini choisie, vitesse de lecture). Migration additive.
CREATE TABLE IF NOT EXISTS voice_settings (
    user_id    BIGINT PRIMARY KEY,
    voice      TEXT,
    speed      NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
