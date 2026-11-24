-- Lot 1 / phase 1a : fondations de la confidentialité. Migration uniquement additive.
-- Rien ici ne change le comportement de FOX : ces tables et colonnes restent vides ou NULL tant que la
-- passerelle d'envoi (egress.py) n'est pas branchée (phase 1c). Aucune ligne existante n'est modifiée.
--
-- Convention du projet conservée : pas de clé étrangère vers `users` (comme `conversations`, `chats`,
-- `files`, `tasks` : d'anciennes lignes peuvent porter l'identifiant Telegram avant le rattachement
-- au compte). En revanche `chats(id)` est une vraie clé : une autorisation de conversation disparaît
-- avec sa conversation (ON DELETE CASCADE), y compris lors des purges d'historique.
--
-- Retour arrière : voir docs/RETOUR_ARRIERE_022.md (export psql, puis script SQL séparé et protégé).

-- Réglages de confidentialité. Pas de ligne = niveau « protégé » sans consentement enregistré.
CREATE TABLE IF NOT EXISTS privacy_settings (
    user_id             BIGINT PRIMARY KEY,
    level               TEXT NOT NULL DEFAULT 'protege'
                        CONSTRAINT privacy_settings_level_check CHECK (level IN ('standard', 'protege', 'strict')),
    preferred_provider  TEXT
                        CONSTRAINT privacy_settings_provider_check CHECK (preferred_provider IS NULL OR preferred_provider IN ('gemini', 'groq')),
    notification_detail TEXT NOT NULL DEFAULT 'discret'
                        CONSTRAINT privacy_settings_notif_check CHECK (notification_detail IN ('discret', 'complet')),
    consent_version     INTEGER,
    consent_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Autorisations d'envoi de données à un fournisseur externe.
--  * scope 'message'      : liée à UNE opération (operation_id) ; tous les tours d'outils de cette opération
--                           la partagent, une autre opération ne peut pas l'utiliser ;
--  * scope 'conversation' : valable pour une conversation (chat_id), au plus 12 h ;
--  * catégorie 'fichier'  : toujours liée à un fichier précis (file_id), jamais générale.
CREATE TABLE IF NOT EXISTS data_grants (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    provider     TEXT NOT NULL
                 CONSTRAINT data_grants_provider_check CHECK (provider IN ('gemini', 'groq', 'gemini_tts', 'elevenlabs')),
    category     TEXT NOT NULL
                 CONSTRAINT data_grants_category_check CHECK (category IN ('texte_libre', 'personnel', 'metier', 'fichier', 'voix')),
    scope        TEXT NOT NULL
                 CONSTRAINT data_grants_scope_check CHECK (scope IN ('message', 'conversation')),
    chat_id      BIGINT REFERENCES chats (id) ON DELETE CASCADE,
    file_id      UUID,
    operation_id TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    CONSTRAINT data_grants_scope_target CHECK (
        (scope = 'conversation' AND chat_id IS NOT NULL) OR (scope = 'message' AND operation_id IS NOT NULL)
    ),
    CONSTRAINT data_grants_file_rule CHECK (
        (category = 'fichier' AND file_id IS NOT NULL) OR (category <> 'fichier' AND file_id IS NULL)
    ),
    CONSTRAINT data_grants_expiry CHECK (expires_at > created_at AND expires_at <= created_at + interval '12 hours'),
    CONSTRAINT data_grants_consumed_scope CHECK (scope = 'message' OR consumed_at IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_data_grants_lookup ON data_grants (user_id, provider, category) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_data_grants_operation ON data_grants (operation_id) WHERE operation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_data_grants_chat ON data_grants (chat_id) WHERE chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_data_grants_expires ON data_grants (expires_at);

-- Message retenu le temps de demander une autorisation (rejoué ensuite, jamais gardé plus de 10 minutes).
CREATE TABLE IF NOT EXISTS held_messages (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    chat_id      BIGINT REFERENCES chats (id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL UNIQUE,
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    CONSTRAINT held_messages_expiry CHECK (expires_at > created_at AND expires_at <= created_at + interval '10 minutes')
);

CREATE INDEX IF NOT EXISTS idx_held_messages_user ON held_messages (user_id);
CREATE INDEX IF NOT EXISTS idx_held_messages_expires ON held_messages (expires_at);

-- Catégories de données réellement utilisées pour un échange (calculées par le serveur, jamais par le
-- modèle) et moteur qui a répondu. NULL = inconnu : traité comme sensible dans toutes les catégories.
-- Les échanges existants restent NULL ; rien n'est supprimé ni réétiqueté.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS data_classes TEXT[]
    CONSTRAINT conversations_data_classes_check CHECK (data_classes IS NULL OR data_classes <@ ARRAY['texte_libre', 'personnel', 'metier', 'fichier']::text[]);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS engine TEXT
    CONSTRAINT conversations_engine_check CHECK (engine IS NULL OR engine IN ('gemini', 'groq'));
