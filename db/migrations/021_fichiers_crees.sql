-- Fichiers créés par FOX (PDF, Word, Excel, zip) : ceux qu'il a joints à sa réponse, pour les revoir dans l'historique.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS reply_files JSONB NOT NULL DEFAULT '[]';
