-- Phase D5 : archivage des devis. Un devis archivé reste intact (numéro, statut, lignes) mais n'apparaît plus
-- dans la liste courante. Migration uniquement additive (aucune donnée existante n'est modifiée).
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
