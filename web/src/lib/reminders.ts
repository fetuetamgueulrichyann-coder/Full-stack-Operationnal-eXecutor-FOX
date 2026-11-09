import type { AgendaEvent, Task } from "../api/types";

export interface Reminder {
  key: string;
  title: string;
  detail: string;
}

const TASK_GRACE_MS = 2 * 60 * 60 * 1000; // une échéance dépassée depuis plus de 2 h n'est plus rappelée
const EVENT_GRACE_MS = 10 * 60 * 1000;

const hhmm = (d: Date) => d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

/**
 * Rappels à afficher maintenant. Lecture seule : les rappels Telegram (consommés côté serveur) ne sont pas touchés ;
 * `seen` est la liste des clés déjà affichées dans ce navigateur.
 */
export function dueReminders(events: AgendaEvent[], tasks: Task[], now: Date, leadMinutes: number, seen: ReadonlySet<string>): Reminder[] {
  const out: Reminder[] = [];
  for (const e of events) {
    const start = new Date(e.start);
    const delta = start.getTime() - now.getTime();
    if (Number.isNaN(delta) || delta > leadMinutes * 60_000 || delta < -EVENT_GRACE_MS) continue;
    const key = `event:${e.id}:${e.start}`;
    if (seen.has(key)) continue;
    const mins = Math.max(0, Math.round(delta / 60_000));
    out.push({
      key,
      title: e.title,
      detail: `${mins <= 0 ? "Maintenant" : `Dans ${mins} min`} (${hhmm(start)})${e.location ? ` · ${e.location}` : ""}`,
    });
  }
  for (const t of tasks) {
    if (!t.due_at || (t.status !== "a_faire" && t.status !== "en_cours")) continue;
    const due = new Date(t.due_at);
    if (Number.isNaN(due.getTime())) continue;
    if (due.getHours() === 0 && due.getMinutes() === 0) continue; // échéance sans heure : pas de rappel
    const delta = now.getTime() - due.getTime();
    if (delta < 0 || delta > TASK_GRACE_MS) continue;
    const key = `task:${t.id}:${t.due_at}`;
    if (seen.has(key)) continue;
    out.push({ key, title: t.title, detail: `Tâche à faire (${hhmm(due)})` });
  }
  return out;
}
