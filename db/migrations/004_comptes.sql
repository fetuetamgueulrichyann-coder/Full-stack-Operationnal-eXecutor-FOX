-- Phase A : comptes, sessions et codes d'invitation.
-- Migration uniquement additive : aucune table existante n'est modifiée ni supprimée.

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL CHECK (char_length(username) BETWEEN 3 AND 32),
    password_hash TEXT,                       -- NULL : pas de connexion web (ex. propriétaire Telegram seul)
    role          TEXT NOT NULL CHECK (role IN ('owner', 'tester')),
    telegram_id   BIGINT UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    disabled_at   TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (lower(username));
-- Un seul propriétaire.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_one_owner ON users (role) WHERE role = 'owner';

CREATE TABLE IF NOT EXISTS invitation_codes (
    id         BIGSERIAL PRIMARY KEY,
    code_hash  TEXT NOT NULL UNIQUE,          -- SHA-256 du code : le code en clair n'est jamais stocké
    role       TEXT NOT NULL DEFAULT 'tester' CHECK (role = 'tester'),
    max_uses   INTEGER NOT NULL DEFAULT 1 CHECK (max_uses > 0),
    uses       INTEGER NOT NULL DEFAULT 0 CHECK (uses >= 0),
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
    id           BIGSERIAL PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,        -- SHA-256 du jeton : le jeton n'est jamais stocké
    user_id      BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);

ALTER TABLE users ADD COLUMN IF NOT EXISTS invitation_id BIGINT REFERENCES invitation_codes (id);

-- Tentatives de connexion (limitation des essais). Purgées après 30 jours par l'application.
CREATE TABLE IF NOT EXISTS auth_attempts (
    id           BIGSERIAL PRIMARY KEY,
    username_key TEXT NOT NULL,
    ip           TEXT NOT NULL,
    ok           BOOLEAN NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_attempts_user ON auth_attempts (username_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip ON auth_attempts (ip, created_at DESC);
