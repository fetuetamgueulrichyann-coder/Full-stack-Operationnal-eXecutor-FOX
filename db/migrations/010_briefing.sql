-- Phase B3 : briefing du matin, activable par chaque utilisateur (désactivé par défaut).
-- `send_time` : heure locale d'envoi (fuseau de FOX). `last_sent_on` : jour du dernier envoi (un seul par jour).

CREATE TABLE IF NOT EXISTS briefing_settings (
    user_id      BIGINT PRIMARY KEY,
    enabled      BOOLEAN NOT NULL DEFAULT false,
    send_time    TIME NOT NULL DEFAULT '08:00',
    last_sent_on DATE
);
