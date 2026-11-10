import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { LedgerEntry, LedgerSummary, Project } from "../api/types";

const frDate = (key: string) => `${key.slice(8, 10)}/${key.slice(5, 7)}/${key.slice(0, 4)}`;
const currentMonth = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};
const today = () => {
  const now = new Date();
  return `${currentMonth()}-${String(now.getDate()).padStart(2, "0")}`;
};

/** Comptabilité simple : recettes et dépenses d'un mois, bilan par catégorie, saisie rapide. */
export function Ledger() {
  const [month, setMonth] = useState(currentMonth());
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ kind: "depense", amount: "", description: "", category: "", project: "", date: today() });
  const set = (field: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([
        api<{ summary: LedgerSummary }>(`/ledger/summary?month=${month}`),
        api<{ entries: LedgerEntry[] }>(`/ledger?month=${month}`),
      ]);
      setSummary(s.summary);
      setEntries(e.entries);
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [month]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api<{ projects: Project[] }>("/projects")
      .then((d) => setProjects(d.projects))
      .catch(() => undefined);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api("/ledger", {
        method: "POST",
        body: {
          kind: form.kind,
          amount: form.amount,
          description: form.description,
          category: form.category,
          date: form.date,
          ...(form.project ? { project: Number(form.project) } : {}),
        },
      });
      setForm({ ...form, amount: "", description: "", category: "" });
      await load();
    } catch (err) {
      setError(describeError(err));
    }
  }

  async function remove(entry: LedgerEntry) {
    setError(null);
    try {
      await api(`/ledger/${entry.id}`, { method: "DELETE", body: {} });
      await load();
    } catch (err) {
      setError(describeError(err));
    }
  }

  return (
    <main className="page ledger">
      <header className="page-head">
        <div>
          <h1>Compta</h1>
          <span className="muted small-text">Recettes (argent reçu) et dépenses (argent dépensé)</span>
        </div>
        <div className="f month">
          <label htmlFor="l-month">Mois</label>
          <input id="l-month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
        </div>
      </header>
      {error && <div className="banner error" role="alert">{error}</div>}

      {summary && (
        <section className="metrics" aria-label="Bilan du mois">
          <div className="metric"><span>Recettes</span><strong>{summary.income_label}</strong></div>
          <div className="metric"><span>Dépenses</span><strong>{summary.expense_label}</strong></div>
          <div className="metric"><span>Solde</span><strong>{summary.balance_label}</strong></div>
        </section>
      )}
      {summary && summary.by_category.length > 0 && (
        <ul className="categories" aria-label="Par catégorie">
          {summary.by_category.map((c) => (
            <li key={`${c.kind}-${c.category}`}>
              <span>{c.kind === "recette" ? "Recette" : "Dépense"} · {c.category}</span>
              <strong>{c.total_label}</strong>
            </li>
          ))}
        </ul>
      )}

      <form className="card-form wide" aria-label="Nouvelle écriture" onSubmit={(e) => void submit(e)}>
        <h2 className="form-title">Ajouter une écriture</h2>
        <div className="f">
          <label htmlFor="l-kind">Type</label>
          <select id="l-kind" value={form.kind} onChange={set("kind")}>
            <option value="depense">Dépense</option>
            <option value="recette">Recette</option>
          </select>
        </div>
        <div className="f">
          <label htmlFor="l-amount">Montant</label>
          <input id="l-amount" required inputMode="decimal" value={form.amount} onChange={set("amount")} />
        </div>
        <div className="f full">
          <label htmlFor="l-desc">Description</label>
          <input id="l-desc" required value={form.description} onChange={set("description")} placeholder="Ciment 20 sacs" />
        </div>
        <div className="f">
          <label htmlFor="l-cat">Catégorie</label>
          <input id="l-cat" value={form.category} onChange={set("category")} placeholder="matériaux (facultatif)" />
        </div>
        <div className="f">
          <label htmlFor="l-project">Chantier</label>
          <select id="l-project" value={form.project} onChange={set("project")}>
            <option value="">Aucun</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div className="f">
          <label htmlFor="l-date">Date</label>
          <input id="l-date" type="date" required value={form.date} onChange={set("date")} />
        </div>
        <div className="form-actions">
          <button type="submit" className="primary compact">Enregistrer</button>
        </div>
      </form>

      <section aria-label="Écritures du mois">
        <h3 className="section-title">Écritures de {month}</h3>
        {entries?.length === 0 && <p className="muted small-text">Aucune écriture ce mois-ci.</p>}
        <ul className="entries">
          {entries?.map((e) => (
            <li key={e.id}>
              <span className="muted">{frDate(e.date)}</span>
              <span>{e.description}{e.category ? ` · ${e.category}` : ""}{e.project ? ` · ${e.project}` : ""}</span>
              <strong className={e.kind === "recette" ? "in" : "out"}>{e.kind === "recette" ? "+" : "−"} {e.amount_label}</strong>
              <button type="button" className="icon-btn" aria-label={`Supprimer l'écriture ${e.description}`} onClick={() => void remove(e)}>×</button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
