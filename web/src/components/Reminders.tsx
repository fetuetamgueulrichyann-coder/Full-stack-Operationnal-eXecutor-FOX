import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { EventsResponse, TasksResponse } from "../api/types";
import { dueReminders, type Reminder } from "../lib/reminders";

const POLL_MS = 60_000;
const STORAGE_KEY = "fox_reminders_seen";
const DEFAULT_LEAD = 30;

function loadSeen(): Set<string> {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]") as unknown;
    return new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

function saveSeen(seen: Set<string>) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...seen].slice(-200)));
  } catch {
    // stockage indisponible : on se contente de l'état en mémoire
  }
}

function notify(r: Reminder) {
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      new Notification(`FOX · ${r.title}`, { body: r.detail });
    }
  } catch {
    // certains navigateurs refusent le constructeur : le bandeau suffit
  }
}

/** Rappels dans l'interface web (onglet ouvert). Ne touche pas aux rappels Telegram : lecture seule. */
export function Reminders() {
  const [items, setItems] = useState<Reminder[]>([]);
  const [canAsk, setCanAsk] = useState(() => typeof Notification !== "undefined" && Notification.permission === "default");
  const seen = useRef<Set<string>>(loadSeen());

  const check = useCallback(async () => {
    try {
      const [ev, tk] = await Promise.all([
        api<EventsResponse>(`/events?range=${encodeURIComponent("aujourd'hui")}`),
        api<TasksResponse>("/tasks"),
      ]);
      const fresh = dueReminders(ev.events, tk.tasks, new Date(), ev.reminder_minutes ?? DEFAULT_LEAD, seen.current);
      if (fresh.length === 0) return;
      for (const r of fresh) {
        seen.current.add(r.key);
        notify(r);
      }
      saveSeen(seen.current);
      setItems((current) => [...current, ...fresh]);
    } catch {
      // réseau ou session : on réessaiera au prochain passage
    }
  }, []);

  useEffect(() => {
    void check();
    const timer = window.setInterval(() => void check(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [check]);

  async function enable() {
    try {
      await Notification.requestPermission();
    } finally {
      setCanAsk(false);
    }
  }

  if (items.length === 0 && !canAsk) return null;
  return (
    <div className="reminders" role="region" aria-label="Rappels">
      {items.map((r) => (
        <div key={r.key} className="reminder" role="alert">
          <div>
            <strong>{r.title}</strong>
            <span>{r.detail}</span>
          </div>
          <button type="button" className="icon-btn" aria-label={`Fermer le rappel ${r.title}`} onClick={() => setItems((c) => c.filter((x) => x.key !== r.key))}>×</button>
        </div>
      ))}
      {canAsk && items.length > 0 && (
        <button type="button" className="secondary compact" onClick={() => void enable()}>Activer les notifications du navigateur</button>
      )}
    </div>
  );
}
