import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { AgendaEvent, EventSaved, EventsResponse } from "../api/types";
import { dayKey, dayLabel, shortDate, timeOf } from "../lib/dates";

type Range = "aujourd'hui" | "semaine" | "mois";
const RANGES: { id: Range; label: string }[] = [
  { id: "aujourd'hui", label: "Aujourd'hui" },
  { id: "semaine", label: "Semaine" },
  { id: "mois", label: "Mois" },
];
const REPEATS = [
  { value: "", label: "Une seule fois" },
  { value: "chaque jour", label: "Chaque jour" },
  { value: "jours ouvrés", label: "Du lundi au vendredi" },
  { value: "chaque semaine", label: "Chaque semaine" },
  { value: "chaque mois", label: "Chaque mois" },
];

interface Draft {
  title: string;
  start: string;
  duration: string;
  location: string;
  repeat: string;
}
const EMPTY: Draft = { title: "", start: "", duration: "", location: "", repeat: "" };

function groupByDay(events: AgendaEvent[]): [string, AgendaEvent[]][] {
  const map = new Map<string, AgendaEvent[]>();
  for (const event of events) {
    const key = dayKey(event.start);
    map.set(key, [...(map.get(key) ?? []), event]);
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export function Agenda() {
  const [range, setRange] = useState<Range>("semaine");
  const [data, setData] = useState<EventsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<string | null>(null); // « id@jour »

  const load = useCallback(async (which: Range) => {
    try {
      setData(await api<EventsResponse>(`/events?range=${encodeURIComponent(which)}`));
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    setData(null);
    setSelected(null);
    void load(range);
  }, [range, load]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const body: Record<string, string> = { title: draft.title.trim(), start: draft.start };
      if (draft.duration.trim()) body.duration = draft.duration.trim();
      if (draft.location.trim()) body.location = draft.location.trim();
      if (draft.repeat) body.repeat = draft.repeat;
      const saved = await api<EventSaved>("/events", { method: "POST", body });
      if (saved.conflicts.length > 0) {
        setNotice(`Rendez-vous ajouté, mais il chevauche : ${saved.conflicts.map((c) => c.title).join(", ")}.`);
      }
      setDraft(EMPTY);
      setAdding(false);
      await load(range);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(event: AgendaEvent, occurrenceOnly: boolean) {
    setError(null);
    try {
      await api(`/events/${event.id}`, {
        method: "DELETE",
        body: occurrenceOnly ? { occurrence: event.occurrence ?? dayKey(event.start) } : {},
      });
      setSelected(null);
      await load(range);
    } catch (e) {
      setError(describeError(e));
    }
  }

  const days = data ? groupByDay(data.events) : [];
  const chosen = data?.events.find((e) => `${e.id}@${dayKey(e.start)}` === selected) ?? null;
  const set = (field: keyof Draft) => (e: { target: { value: string } }) => setDraft({ ...draft, [field]: e.target.value });

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1>Agenda</h1>
          <span className="muted small-text">Vos prochains rendez-vous</span>
        </div>
        <div className="head-actions">
          <div role="tablist" aria-label="Période" className="segmented">
            {RANGES.map((r) => (
              <button key={r.id} role="tab" aria-selected={range === r.id} className={range === r.id ? "seg on" : "seg"} onClick={() => setRange(r.id)}>
                {r.label}
              </button>
            ))}
          </div>
          <button className="primary compact" onClick={() => setAdding(!adding)} aria-expanded={adding}>
            Ajouter un rendez-vous
          </button>
        </div>
      </header>

      {adding && (
        <form className="card-form" aria-label="Nouveau rendez-vous" onSubmit={(e) => void save(e)}>
          <div className="f">
            <label htmlFor="ev-title">Titre</label>
            <input id="ev-title" required value={draft.title} onChange={set("title")} />
          </div>
          <div className="f">
            <label htmlFor="ev-start">Début</label>
            <input id="ev-start" type="datetime-local" required value={draft.start} onChange={set("start")} />
          </div>
          <div className="f">
            <label htmlFor="ev-duration">Durée</label>
            <input id="ev-duration" placeholder="1h (par défaut)" value={draft.duration} onChange={set("duration")} />
          </div>
          <div className="f">
            <label htmlFor="ev-location">Lieu</label>
            <input id="ev-location" value={draft.location} onChange={set("location")} />
          </div>
          <div className="f">
            <label htmlFor="ev-repeat">Répétition</label>
            <select id="ev-repeat" value={draft.repeat} onChange={set("repeat")}>
              {REPEATS.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>
          <div className="form-actions">
            <button type="submit" className="primary compact" disabled={saving}>Enregistrer</button>
            <button type="button" className="secondary compact" onClick={() => setAdding(false)}>Fermer</button>
          </div>
        </form>
      )}

      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner info" role="status">{notice}</div>}

      <section aria-label="Rendez-vous" className="agenda-days">
        {data && days.length === 0 && <p className="muted">Rien de prévu sur cette période.</p>}
        {days.map(([key, events]) => (
          <div key={key} className="day-card">
            <div className="day-head">
              <span>{dayLabel(key)}</span>
              {data?.today === key && <span className="chip today">Aujourd'hui</span>}
            </div>
            {events.map((event) => {
              const id = `${event.id}@${key}`;
              return (
                <button key={id} className={selected === id ? "ev on" : "ev"} onClick={() => setSelected(selected === id ? null : id)}>
                  <span className="ev-time">
                    {timeOf(event.start)}-{timeOf(event.end)}
                    {event.repeat ? ` · ${event.repeat}` : ""}
                  </span>
                  <span className="ev-name">{event.title}</span>
                  {event.location && <span className="ev-sub">{event.location}</span>}
                  {event.tasks?.map((t) => (
                    <span key={t.id} className="ev-sub">↳ à faire : {t.title}</span>
                  ))}
                </button>
              );
            })}
          </div>
        ))}
      </section>

      {chosen && (
        <section aria-label="Détail du rendez-vous" className="detail">
          <div className="detail-head">
            <h2>{chosen.title}</h2>
            {chosen.repeat && <span className="chip rdv">{chosen.repeat}</span>}
          </div>
          <div className="detail-meta">
            <span>{timeOf(chosen.start)}-{timeOf(chosen.end)}</span>
            {chosen.location && <span>{chosen.location}</span>}
          </div>
          <div className="form-actions">
            {chosen.repeat ? (
              <>
                <button className="secondary compact" onClick={() => void remove(chosen, true)}>
                  Supprimer seulement le {shortDate(dayKey(chosen.start))}
                </button>
                <button className="secondary compact danger" onClick={() => void remove(chosen, false)}>Supprimer la série</button>
              </>
            ) : (
              <button className="secondary compact danger" onClick={() => void remove(chosen, false)}>Supprimer</button>
            )}
          </div>
        </section>
      )}
    </main>
  );
}
