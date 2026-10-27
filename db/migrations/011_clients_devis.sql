-- Phase C : clients et devis. Migration uniquement additive.
-- Comme `tasks`, pas de clé étrangère vers `users` ; les devis gardent le nom du client tel qu'il était
-- à leur création (ils restent lisibles si la fiche client est supprimée).

CREATE TABLE IF NOT EXISTS clients (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    name       TEXT NOT NULL,
    phone      TEXT,
    email      TEXT,
    address    TEXT,
    notes      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_user_name ON clients (user_id, lower(name));

CREATE TABLE IF NOT EXISTS quotes (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    client_id   BIGINT REFERENCES clients (id) ON DELETE SET NULL,
    client_name TEXT NOT NULL,
    number      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'brouillon' CHECK (status IN ('brouillon', 'envoye', 'accepte', 'refuse')),
    currency    TEXT NOT NULL,
    vat_rate    NUMERIC(5, 2) NOT NULL CHECK (vat_rate >= 0 AND vat_rate <= 100),
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, number)
);
CREATE INDEX IF NOT EXISTS idx_quotes_user ON quotes (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS quote_lines (
    id          BIGSERIAL PRIMARY KEY,
    quote_id    BIGINT NOT NULL REFERENCES quotes (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    description TEXT NOT NULL,
    quantity    NUMERIC(12, 3) NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(14, 2) NOT NULL CHECK (unit_price >= 0)
);
CREATE INDEX IF NOT EXISTS idx_quote_lines_quote ON quote_lines (quote_id, position);

-- Numérotation continue par utilisateur et par année : un numéro n'est jamais réutilisé, même après suppression.
CREATE TABLE IF NOT EXISTS quote_counters (
    user_id  BIGINT NOT NULL,
    year     INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    PRIMARY KEY (user_id, year)
);
