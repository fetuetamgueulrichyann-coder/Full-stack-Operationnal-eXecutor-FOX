-- Phase F : chantiers et comptabilité simple (recettes / dépenses). Migration uniquement additive.
-- Comme `clients` et `quotes`, pas de clé étrangère vers `users` ; le nom du client est recopié pour que
-- le chantier reste lisible si la fiche client est supprimée. Un chantier ne se supprime pas : il s'archive.

CREATE TABLE IF NOT EXISTS projects (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    name        TEXT NOT NULL,
    client_id   BIGINT REFERENCES clients (id) ON DELETE SET NULL,
    client_name TEXT,
    quote_id    BIGINT REFERENCES quotes (id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'en_cours' CHECK (status IN ('en_cours', 'suspendu', 'termine')),
    budget      NUMERIC(14, 2) CHECK (budget IS NULL OR budget >= 0),
    start_date  DATE,
    end_date    DATE,
    notes       TEXT,
    archived_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_user_name ON projects (user_id, lower(name));

CREATE TABLE IF NOT EXISTS ledger_entries (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('recette', 'depense')),
    amount      NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    currency    TEXT NOT NULL,
    entry_date  DATE NOT NULL,
    category    TEXT,
    description TEXT NOT NULL,
    project_id  BIGINT REFERENCES projects (id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ledger_user_date ON ledger_entries (user_id, entry_date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_project ON ledger_entries (project_id);
