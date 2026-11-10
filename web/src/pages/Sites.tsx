import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { LedgerEntry, Project, ProjectStatus } from "../api/types";
import { Ledger } from "./Ledger";

const STATUSES: { id: ProjectStatus; label: string }[] = [
  { id: "en_cours", label: "En cours" },
  { id: "suspendu", label: "Suspendu" },
  { id: "termine", label: "Terminé" },
];

type Selection = { kind: "project"; id: number } | { kind: "new" } | null;

const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const frDate = (key: string) => `${key.slice(8, 10)}/${key.slice(5, 7)}/${key.slice(0, 4)}`;

function StatusPill({ project }: { project: Project }) {
  return <span className={`status site-${project.status}`}>{cap(project.status_label)}</span>;
}

/** Page « Chantiers » : deux onglets, la liste des chantiers et la comptabilité (recettes / dépenses). */
export function Sites() {
  const [tab, setTab] = useState<"chantiers" | "compta">("chantiers");
  return (
    <div className="sites">
      <div className="sites-tabs">
        <div className="segmented" role="tablist" aria-label="Chantiers et comptabilité">
          <button role="tab" aria-selected={tab === "chantiers"} className={tab === "chantiers" ? "seg on" : "seg"} onClick={() => setTab("chantiers")}>Chantiers</button>
          <button role="tab" aria-selected={tab === "compta"} className={tab === "compta" ? "seg on" : "seg"} onClick={() => setTab("compta")}>Compta</button>
        </div>
      </div>
      {tab === "chantiers" ? <Projects /> : <Ledger />}
    </div>
  );
}

function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const data = await api<{ projects: Project[] }>(showArchived ? "/projects?archived=1" : "/projects");
      setProjects(data.projects);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, [showArchived]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="split">
      <section className="split-list" aria-label="Chantiers">
        <div className="list-head">
          <h2>Chantiers</h2>
          <button className="secondary compact" onClick={() => setSelection({ kind: "new" })}>Nouveau</button>
        </div>
        <div className="segmented" role="group" aria-label="Filtrer les chantiers">
          <button className={showArchived ? "seg" : "seg on"} aria-pressed={!showArchived} onClick={() => { setShowArchived(false); setSelection(null); }}>Actifs</button>
          <button className={showArchived ? "seg on" : "seg"} aria-pressed={showArchived} onClick={() => { setShowArchived(true); setSelection(null); }}>Archivés</button>
        </div>
        {projects?.length === 0 && <p className="muted small-text">{showArchived ? "Aucun chantier archivé." : "Aucun chantier."}</p>}
        {projects?.map((p) => (
          <button key={p.id} className={selection?.kind === "project" && selection.id === p.id ? "item on" : "item"} onClick={() => setSelection({ kind: "project", id: p.id })}>
            <span className="item-top">
              <span className="item-name">{p.name}</span>
              <StatusPill project={p} />
            </span>
            <span className="item-sub">{[p.client, `Marge ${p.margin_label}`].filter(Boolean).join(" · ")}</span>
          </button>
        ))}
      </section>
      <main className="split-main">
        {error && <div className="banner error" role="alert">{error}</div>}
        {selection === null && (
          <div className="empty-detail">
            <h1>Chantiers</h1>
            <p className="muted">Choisissez un chantier pour voir son budget, ses recettes et ses dépenses, ou créez-en un.</p>
          </div>
        )}
        {selection?.kind === "project" && (
          <ProjectDetail key={selection.id} id={selection.id} onChanged={reload} onArchived={() => { setSelection(null); void reload(); }} />
        )}
        {selection?.kind === "new" && (
          <ProjectForm onCreated={(p) => { setSelection({ kind: "project", id: p.id }); void reload(); }} />
        )}
      </main>
    </div>
  );
}

function ProjectForm({ onCreated }: { onCreated: (project: Project) => void }) {
  const [form, setForm] = useState({ name: "", client: "", quote: "", budget: "", start_date: "", end_date: "", notes: "" });
  const [error, setError] = useState<string | null>(null);
  const set = (field: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const data = await api<{ project: Project }>("/projects", { method: "POST", body: form });
      onCreated(data.project);
    } catch (e) {
      setError(describeError(e));
    }
  }

  return (
    <form className="card-form wide" aria-label="Nouveau chantier" onSubmit={(e) => void submit(e)}>
      <h2 className="form-title">Nouveau chantier</h2>
      <div className="f full">
        <label htmlFor="p-name">Nom du chantier</label>
        <input id="p-name" required value={form.name} onChange={set("name")} />
      </div>
      <div className="f">
        <label htmlFor="p-client">Client</label>
        <input id="p-client" value={form.client} onChange={set("client")} placeholder="Nom (facultatif)" />
      </div>
      <div className="f">
        <label htmlFor="p-quote">Devis lié</label>
        <input id="p-quote" value={form.quote} onChange={set("quote")} placeholder="DV-2026-001 (facultatif)" />
      </div>
      <div className="f">
        <label htmlFor="p-budget">Budget de dépenses</label>
        <input id="p-budget" inputMode="decimal" value={form.budget} onChange={set("budget")} placeholder="Facultatif" />
      </div>
      <div className="f">
        <label htmlFor="p-start">Début</label>
        <input id="p-start" type="date" value={form.start_date} onChange={set("start_date")} />
      </div>
      <div className="f">
        <label htmlFor="p-end">Fin prévue</label>
        <input id="p-end" type="date" value={form.end_date} onChange={set("end_date")} />
      </div>
      <div className="f full">
        <label htmlFor="p-notes">Note</label>
        <input id="p-notes" value={form.notes} onChange={set("notes")} />
      </div>
      {error && <div className="banner error full" role="alert">{error}</div>}
      <div className="form-actions">
        <button type="submit" className="primary compact">Créer le chantier</button>
      </div>
    </form>
  );
}

function ProjectDetail({ id, onChanged, onArchived }: { id: number; onChanged: () => Promise<void>; onArchived: () => void }) {
  const [project, setProject] = useState<Project | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([
        api<{ project: Project }>(`/projects/${id}`),
        api<{ entries: LedgerEntry[] }>(`/ledger?month=all&project=${id}`),
      ]);
      setProject(p.project);
      setEntries(e.entries);
    } catch (err) {
      setError(describeError(err));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function patch(body: Record<string, unknown>, afterArchive = false) {
    setError(null);
    try {
      const data = await api<{ project: Project }>(`/projects/${id}`, { method: "PATCH", body });
      setProject(data.project);
      await onChanged();
      if (afterArchive) onArchived();
    } catch (err) {
      setError(describeError(err));
    }
  }

  if (!project) return error ? <div className="banner error" role="alert">{error}</div> : <p className="muted">Chargement…</p>;
  const used = project.budget_used_percent;
  return (
    <>
      <header className="detail-head">
        <h2>{project.name}</h2>
        <StatusPill project={project} />
      </header>
      <div className="detail-meta">
        {project.client && <span>Client : {project.client}</span>}
        {project.quote && <span>Devis : {project.quote}</span>}
        {project.start_date && <span>Début : {frDate(project.start_date)}</span>}
        {project.end_date && <span>Fin prévue : {frDate(project.end_date)}</span>}
      </div>
      {project.notes && <p className="muted">{project.notes}</p>}
      {error && <div className="banner error" role="alert">{error}</div>}

      <section className="metrics" aria-label="Chiffres du chantier">
        <div className="metric"><span>Recettes</span><strong>{project.income_label}</strong></div>
        <div className="metric"><span>Dépenses</span><strong>{project.expense_label}</strong></div>
        <div className="metric"><span>Marge</span><strong>{project.margin_label}</strong></div>
        {project.budget_label && <div className="metric"><span>Budget</span><strong>{project.budget_label}</strong></div>}
      </section>
      {used !== null && project.budget_left_label && (
        <div className="budget" aria-label="Budget utilisé">
          <div className="bar"><div className={used > 100 ? "fill over" : "fill"} style={{ width: `${Math.min(used, 100)}%` }} /></div>
          <span className="muted small-text">
            {used} % du budget dépensé · {used > 100 ? `dépassement de ${project.budget_left_label.replace("-", "")}` : `reste ${project.budget_left_label}`}
          </span>
        </div>
      )}

      <div className="status-row">
        <span className="label">Statut :</span>
        {STATUSES.map((s) => (
          <button key={s.id} className={project.status === s.id ? "seg on" : "seg"} aria-pressed={project.status === s.id} onClick={() => void patch({ status: s.id })}>{s.label}</button>
        ))}
        <button className="secondary compact" onClick={() => void patch({ archived: !project.archived }, true)}>{project.archived ? "Désarchiver" : "Archiver"}</button>
      </div>

      <section aria-label="Écritures du chantier">
        <h3 className="section-title">Recettes et dépenses</h3>
        {entries.length === 0 && <p className="muted small-text">Aucune écriture pour ce chantier. Ajoutez-en depuis l'onglet Compta ou dites-le à FOX dans la discussion.</p>}
        <ul className="entries">
          {entries.map((e) => (
            <li key={e.id}>
              <span className="muted">{frDate(e.date)}</span>
              <span>{e.description}{e.category ? ` · ${e.category}` : ""}</span>
              <strong className={e.kind === "recette" ? "in" : "out"}>{e.kind === "recette" ? "+" : "−"} {e.amount_label}</strong>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
