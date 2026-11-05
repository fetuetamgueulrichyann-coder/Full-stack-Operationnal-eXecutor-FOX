import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { Task, TasksResponse, TaskStatus } from "../api/types";
import { dueLabel } from "../lib/dates";

type Filter = "open" | "terminee" | "all";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "open", label: "En cours" },
  { id: "terminee", label: "Terminées" },
  { id: "all", label: "Toutes" },
];

const STATUS_LABEL: Record<TaskStatus, string> = {
  a_faire: "À faire",
  en_cours: "En cours",
  terminee: "Terminée",
  annulee: "Annulée",
};

export function Tasks() {
  const [filter, setFilter] = useState<Filter>("open");
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [quick, setQuick] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (which: Filter) => {
    try {
      const data = await api<TasksResponse>(`/tasks?status=${which}`);
      setTasks(data.tasks);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    setTasks(null);
    void load(filter);
  }, [filter, load]);

  async function change(task: Task, status: TaskStatus) {
    setError(null);
    try {
      await api(`/tasks/${task.id}`, { method: "PATCH", body: { status } });
      await load(filter);
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const text = quick.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api("/tasks", { method: "POST", body: { text } });
      setQuick("");
      await load(filter);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  const late = tasks?.filter((t) => t.overdue).length ?? 0;
  const subtitle =
    tasks === null ? "Chargement…" : `${tasks.length} ${tasks.length > 1 ? "tâches" : "tâche"}${late ? ` · ${late} en retard` : ""}`;

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1>Tâches</h1>
          <span className="muted small-text">{subtitle}</span>
        </div>
        <div role="tablist" aria-label="Filtre" className="segmented">
          {FILTERS.map((f) => (
            <button key={f.id} role="tab" aria-selected={filter === f.id} className={filter === f.id ? "seg on" : "seg"} onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
      </header>

      <form className="quick-add" aria-label="Ajouter une tâche" onSubmit={(e) => void add(e)}>
        <label htmlFor="new-task" className="sr-only">Nouvelle tâche</label>
        <input id="new-task" type="text" value={quick} onChange={(e) => setQuick(e.target.value)} placeholder="Titre | échéance | priorité (ex. Appeler Paul | demain 10h | haute)" />
        <button type="submit" className="primary compact" disabled={busy || !quick.trim()}>Ajouter</button>
      </form>

      {error && <div className="banner error" role="alert">{error}</div>}

      <section aria-label="Liste des tâches" className="task-list">
        {tasks?.length === 0 && <p className="muted">Aucune tâche ici.</p>}
        {tasks?.map((task) => {
          const closed = task.status === "terminee" || task.status === "annulee";
          return (
            <div key={task.id} className={closed ? "task-row closed" : "task-row"}>
              <button
                className={task.status === "terminee" ? "check on" : "check"}
                aria-label={task.status === "terminee" ? `Rouvrir : ${task.title}` : `Terminer : ${task.title}`}
                disabled={task.status === "annulee"}
                onClick={() => void change(task, task.status === "terminee" ? "a_faire" : "terminee")}
              />
              <div className="task-body">
                <span className="task-name">{task.title}</span>
                <span className="task-meta">
                  {task.overdue && task.due_at && <span className="chip late">En retard · {dueLabel(task.due_at)}</span>}
                  {!task.overdue && task.due_at && <span>Pour le {dueLabel(task.due_at)}</span>}
                  {!task.due_at && <span>Sans échéance</span>}
                  {task.priority === "haute" && <span className="chip high">Priorité haute</span>}
                  {task.priority === "basse" && <span className="chip">Priorité basse</span>}
                  {task.event_title && <span className="chip rdv">Rdv : {task.event_title}</span>}
                  {closed && <span className="chip">{STATUS_LABEL[task.status]}</span>}
                </span>
              </div>
              {!closed && (
                <button className="secondary compact" onClick={() => void change(task, "annulee")}>
                  Annuler
                </button>
              )}
            </div>
          );
        })}
      </section>
    </main>
  );
}
