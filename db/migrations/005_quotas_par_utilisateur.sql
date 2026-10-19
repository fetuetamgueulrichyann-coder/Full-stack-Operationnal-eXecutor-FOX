-- Phase A : quotas par utilisateur et par fournisseur d'IA. Migration uniquement additive.
-- api_usage reste le plafond global tous utilisateurs confondus.

CREATE TABLE IF NOT EXISTS user_usage (
    user_id BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    day     DATE NOT NULL,
    calls   INTEGER NOT NULL DEFAULT 0,
    tokens  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS provider_usage (
    provider TEXT NOT NULL,
    day      DATE NOT NULL,
    calls    INTEGER NOT NULL DEFAULT 0,
    tokens   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, day)
);
