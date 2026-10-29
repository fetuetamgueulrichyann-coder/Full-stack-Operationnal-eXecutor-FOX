-- Phase C3 : profil d'entreprise de l'utilisateur (en-tête de ses devis PDF). Additive.
CREATE TABLE IF NOT EXISTS business_profiles (
    user_id    BIGINT PRIMARY KEY,
    name       TEXT NOT NULL,
    phone      TEXT,
    email      TEXT,
    address    TEXT,
    tax_id     TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
