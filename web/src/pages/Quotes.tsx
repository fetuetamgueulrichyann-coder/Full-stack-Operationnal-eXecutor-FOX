import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { Client, MailDraft, Quote, QuoteStatus, QuoteSummary } from "../api/types";
import { shortDate } from "../lib/dates";

type Selection =
  | { kind: "quote"; id: number }
  | { kind: "client"; id: number }
  | { kind: "new-quote" }
  | { kind: "new-client" }
  | null;

const STATUS_ORDER: QuoteStatus[] = ["brouillon", "envoye", "accepte", "refuse"];
const STATUS_NAME: Record<QuoteStatus, string> = { brouillon: "brouillon", envoye: "envoyé", accepte: "accepté", refuse: "refusé" };

function StatusPill({ status, label }: { status: QuoteStatus; label: string }) {
  return <span className={`status ${status}`}>{label.charAt(0).toUpperCase() + label.slice(1)}</span>;
}

export function Quotes() {
  const [quotes, setQuotes] = useState<QuoteSummary[] | null>(null);
  const [clients, setClients] = useState<Client[] | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [q, c] = await Promise.all([api<{ quotes: QuoteSummary[] }>("/quotes"), api<{ clients: Client[] }>("/clients")]);
      setQuotes(q.quotes);
      setClients(c.clients);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const done = async (next: Selection) => {
    setSelection(next);
    await reload();
  };

  return (
    <div className="split">
      <section className="split-list" aria-label="Devis et clients">
        <div className="list-head">
          <h2>Devis</h2>
          <button className="primary compact" onClick={() => setSelection({ kind: "new-quote" })}>Nouveau</button>
        </div>
        {quotes?.length === 0 && <p className="muted small-text">Aucun devis.</p>}
        {quotes?.map((q) => (
          <button key={q.id} className={selection?.kind === "quote" && selection.id === q.id ? "item on" : "item"} onClick={() => setSelection({ kind: "quote", id: q.id })}>
            <span className="item-top">
              <span className="num">{q.number}</span>
              <StatusPill status={q.status} label={q.status_label} />
            </span>
            <span className="item-name">{q.client}</span>
            <span className="item-sub">{q.total}</span>
          </button>
        ))}
        <div className="list-head spaced">
          <h2>Clients</h2>
          <button className="secondary compact" onClick={() => setSelection({ kind: "new-client" })}>Nouveau</button>
        </div>
        {clients?.length === 0 && <p className="muted small-text">Aucun client.</p>}
        {clients?.map((c) => (
          <button key={c.id} className={selection?.kind === "client" && selection.id === c.id ? "item on" : "item"} onClick={() => setSelection({ kind: "client", id: c.id })}>
            <span className="item-name">{c.name}</span>
            {(c.address || c.phone) && <span className="item-sub">{[c.address, c.phone].filter(Boolean).join(" · ")}</span>}
          </button>
        ))}
      </section>

      <main className="split-main">
        {error && <div className="banner error" role="alert">{error}</div>}
        {selection === null && (
          <div className="empty-detail">
            <h1>Clients et devis</h1>
            <p className="muted">Choisissez un devis ou un client, ou créez-en un nouveau.</p>
          </div>
        )}
        {selection?.kind === "quote" && (
          <QuoteDetail key={selection.id} id={selection.id} onChanged={reload} onDeleted={() => done(null)} />
        )}
        {selection?.kind === "new-quote" && (
          <QuoteForm clients={clients ?? []} onCreated={(q) => done({ kind: "quote", id: q.id })} onClose={() => setSelection(null)} />
        )}
        {selection?.kind === "client" && (
          <ClientDetail key={selection.id} client={clients?.find((c) => c.id === selection.id) ?? null} onSaved={reload} onDeleted={() => done(null)} />
        )}
        {selection?.kind === "new-client" && (
          <ClientDetail key="new" client={null} onSaved={reload} onCreated={(c) => done({ kind: "client", id: c.id })} onDeleted={() => done(null)} />
        )}
      </main>
    </div>
  );
}

function QuoteDetail({ id, onChanged, onDeleted }: { id: number; onChanged: () => Promise<void>; onDeleted: () => void }) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mail, setMail] = useState<MailDraft | null>(null);

  useEffect(() => {
    let alive = true;
    api<{ quote: Quote }>(`/quotes/${id}`)
      .then((d) => alive && setQuote(d.quote))
      .catch((e) => alive && setError(describeError(e)));
    return () => {
      alive = false;
    };
  }, [id]);

  async function setStatus(status: QuoteStatus) {
    setError(null);
    try {
      const d = await api<{ quote: Quote }>(`/quotes/${id}`, { method: "PATCH", body: { status } });
      setQuote(d.quote);
      await onChanged();
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function remove() {
    setError(null);
    try {
      await api(`/quotes/${id}`, { method: "DELETE", body: {} });
      onDeleted();
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function draftMail() {
    setError(null);
    try {
      setMail(await api<MailDraft>(`/quotes/${id}/mail`));
    } catch (e) {
      setError(describeError(e));
    }
  }

  if (!quote) return error ? <div className="banner error" role="alert">{error}</div> : <p className="muted">Chargement…</p>;

  return (
    <>
      <header className="page-head top">
        <div className="stack">
          <span className="num muted">{quote.number}{quote.created_at ? ` · ${shortDate(quote.created_at.slice(0, 10))}/${quote.created_at.slice(0, 4)}` : ""}</span>
          <h1>{quote.client}</h1>
          <StatusPill status={quote.status} label={quote.status_label} />
        </div>
        <div className="head-actions">
          <a className="secondary compact as-link" href={`/api/quotes/${quote.id}/pdf`} download>Télécharger le PDF</a>
          <button className="secondary compact" onClick={() => void draftMail()}>E-mail au client</button>
        </div>
      </header>
      {error && <div className="banner error" role="alert">{error}</div>}

      <section aria-label="Lignes du devis" className="table-card">
        <table>
          <thead>
            <tr>
              <th scope="col">Désignation</th>
              <th scope="col" className="r">Qté</th>
              <th scope="col" className="r">Prix unitaire</th>
              <th scope="col" className="r">Total</th>
            </tr>
          </thead>
          <tbody>
            {quote.lines.map((l, i) => (
              <tr key={i}>
                <td>{l.description}</td>
                <td className="num r">{l.quantity}</td>
                <td className="num r">{l.unit_price}</td>
                <td className="num r">{l.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-label="Totaux" className="totals">
        <div><span className="muted">Sous-total</span><span className="num">{quote.subtotal}</span></div>
        <div><span className="muted">TVA {quote.vat_rate} %</span><span className="num">{quote.vat}</span></div>
        <div className="grand"><span>Total</span><span className="num">{quote.total}</span></div>
      </section>
      {quote.notes && <p className="muted small-text">Note : {quote.notes}</p>}

      <section aria-label="Changer le statut" className="status-row">
        <span className="label">Statut</span>
        {STATUS_ORDER.filter((s) => s !== quote.status).map((s) => (
          <button key={s} className={s === "accepte" ? "primary compact" : "secondary compact"} onClick={() => void setStatus(s)}>
            Marquer {STATUS_NAME[s]}
          </button>
        ))}
        <button className="secondary compact danger" onClick={() => void remove()}>Supprimer</button>
      </section>

      {mail && (
        <section aria-label="Brouillon d'e-mail" className="mail-card">
          <h2>Brouillon d'e-mail</h2>
          <p className="muted small-text">À : {mail.to || "(adresse du client non renseignée)"} · Objet : {mail.subject}</p>
          <pre>{mail.body}</pre>
          <p className="muted small-text">Téléchargez d'abord le PDF : il est à joindre vous-même au message.</p>
          <a className="primary compact as-link" href={`mailto:${encodeURIComponent(mail.to)}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`}>
            Ouvrir dans ma messagerie
          </a>
        </section>
      )}
    </>
  );
}

interface LineDraft {
  description: string;
  quantity: string;
  price: string;
}
const blankLine = (): LineDraft => ({ description: "", quantity: "1", price: "" });

function QuoteForm({ clients, onCreated, onClose }: { clients: Client[]; onCreated: (q: Quote) => void; onClose: () => void }) {
  const [client, setClient] = useState("");
  const [lines, setLines] = useState<LineDraft[]>([blankLine()]);
  const [vat, setVat] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const patch = (i: number, field: keyof LineDraft, value: string) =>
    setLines(lines.map((l, k) => (k === i ? { ...l, [field]: value } : l)));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    const rows = [client.trim(), ...lines.filter((l) => l.description.trim()).map((l) => `${l.description.trim()} | ${l.quantity.trim() || "1"} | ${l.price.trim()}`)];
    if (vat.trim()) rows.push(`TVA ${vat.trim()}`);
    if (notes.trim()) rows.push(`note : ${notes.trim()}`);
    setSaving(true);
    setError(null);
    try {
      const d = await api<{ quote: Quote }>("/quotes", { method: "POST", body: { text: rows.join("\n") } });
      onCreated(d.quote);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="card-form wide" aria-label="Nouveau devis" onSubmit={(e) => void submit(e)}>
      <h2 className="form-title">Nouveau devis</h2>
      <div className="f full">
        <label htmlFor="q-client">Client</label>
        <input id="q-client" list="client-names" required value={client} onChange={(e) => setClient(e.target.value)} />
        <datalist id="client-names">
          {clients.map((c) => (
            <option key={c.id} value={c.name} />
          ))}
        </datalist>
      </div>
      <fieldset className="lines full">
        <legend>Lignes</legend>
        {lines.map((l, i) => (
          <div key={i} className="line-row">
            <input aria-label={`Désignation ${i + 1}`} placeholder="Désignation" value={l.description} onChange={(e) => patch(i, "description", e.target.value)} />
            <input aria-label={`Quantité ${i + 1}`} inputMode="decimal" placeholder="Qté" value={l.quantity} onChange={(e) => patch(i, "quantity", e.target.value)} />
            <input aria-label={`Prix unitaire ${i + 1}`} inputMode="decimal" placeholder="Prix" value={l.price} onChange={(e) => patch(i, "price", e.target.value)} />
            {lines.length > 1 && (
              <button type="button" className="icon-btn" aria-label={`Retirer la ligne ${i + 1}`} onClick={() => setLines(lines.filter((_, k) => k !== i))}>×</button>
            )}
          </div>
        ))}
        <button type="button" className="secondary compact" onClick={() => setLines([...lines, blankLine()])}>Ajouter une ligne</button>
      </fieldset>
      <div className="f">
        <label htmlFor="q-vat">TVA en % (facultatif)</label>
        <input id="q-vat" inputMode="decimal" placeholder="19,25 par défaut" value={vat} onChange={(e) => setVat(e.target.value)} />
      </div>
      <div className="f">
        <label htmlFor="q-notes">Note (facultatif)</label>
        <input id="q-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      {error && <div className="banner error full" role="alert">{error}</div>}
      <div className="form-actions">
        <button type="submit" className="primary compact" disabled={saving}>Créer le devis</button>
        <button type="button" className="secondary compact" onClick={onClose}>Fermer</button>
      </div>
    </form>
  );
}

function ClientDetail({ client, onSaved, onCreated, onDeleted }: { client: Client | null; onSaved: () => Promise<void>; onCreated?: (c: Client) => void; onDeleted: () => void }) {
  const [form, setForm] = useState({
    name: client?.name ?? "",
    phone: client?.phone ?? "",
    email: client?.email ?? "",
    address: client?.address ?? "",
    notes: client?.notes ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const set = (field: keyof typeof form) => (e: { target: { value: string } }) => {
    setSaved(false);
    setForm({ ...form, [field]: e.target.value });
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      if (client) {
        await api(`/clients/${client.id}`, { method: "PATCH", body: form });
        setSaved(true);
        await onSaved();
      } else {
        const d = await api<{ client: Client }>("/clients", { method: "POST", body: form });
        onCreated?.(d.client);
      }
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function remove() {
    if (!client) return;
    setError(null);
    try {
      await api(`/clients/${client.id}`, { method: "DELETE", body: {} });
      onDeleted();
    } catch (e) {
      setError(describeError(e));
    }
  }

  return (
    <form className="card-form wide" aria-label={client ? "Fiche client" : "Nouveau client"} onSubmit={(e) => void submit(e)}>
      <h2 className="form-title">{client ? client.name : "Nouveau client"}</h2>
      <div className="f full">
        <label htmlFor="c-name">Nom</label>
        <input id="c-name" required value={form.name} onChange={set("name")} />
      </div>
      <div className="f">
        <label htmlFor="c-phone">Téléphone</label>
        <input id="c-phone" type="tel" value={form.phone} onChange={set("phone")} />
      </div>
      <div className="f">
        <label htmlFor="c-email">E-mail</label>
        <input id="c-email" type="email" value={form.email} onChange={set("email")} />
      </div>
      <div className="f full">
        <label htmlFor="c-address">Adresse</label>
        <input id="c-address" value={form.address} onChange={set("address")} />
      </div>
      <div className="f full">
        <label htmlFor="c-notes">Note</label>
        <input id="c-notes" value={form.notes} onChange={set("notes")} />
      </div>
      {error && <div className="banner error full" role="alert">{error}</div>}
      {saved && <div className="banner info full" role="status">Client enregistré.</div>}
      <div className="form-actions">
        <button type="submit" className="primary compact">{client ? "Enregistrer" : "Créer le client"}</button>
        {client && <button type="button" className="secondary compact danger" onClick={() => void remove()}>Supprimer</button>}
      </div>
    </form>
  );
}
