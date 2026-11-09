import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { ChatsResponse, JournalEntry, MemoryItem } from "../api/types";
import { dayKey, shortDate, timeOf } from "../lib/dates";

export function Privacy() {
  const [priv, setPriv] = useState<boolean | null>(null);
  const [memories, setMemories] = useState<MemoryItem[] | null>(null);
  const [journal, setJournal] = useState<JournalEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newMemory, setNewMemory] = useState("");
  const [confirming, setConfirming] = useState<"chats" | "memory" | null>(null);

  const load = useCallback(async () => {
    try {
      const [chats, mem, jr] = await Promise.all([
        api<ChatsResponse>("/chats"),
        api<{ items: MemoryItem[] }>("/memory"),
        api<{ entries: JournalEntry[] }>("/journal?limit=15"),
      ]);
      setPriv(chats.private);
      setMemories(mem.items);
      setJournal(jr.entries);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, message?: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      if (message) setNotice(message);
      await load();
    } catch (e) {
      setError(describeError(e));
    }
  }

  const togglePrivate = () => run(async () => api("/private", { method: "POST", body: { on: !priv } }));
  const removeMemory = (id: number) => run(() => api(`/memory/${id}`, { method: "DELETE", body: {} }));
  const addMemory = (event: FormEvent) => {
    event.preventDefault();
    const text = newMemory.trim();
    if (!text) return;
    void run(async () => {
      await api("/memory", { method: "POST", body: { text } });
      setNewMemory("");
    });
  };
  const clearAll = async (what: "chats" | "memory") => {
    setConfirming(null);
    if (what === "chats") await run(() => api("/chats", { method: "DELETE", body: { confirm: "all" } }), "Toutes vos conversations ont été supprimées.");
    else await run(() => api("/memory", { method: "DELETE", body: { confirm: "all" } }), "La mémoire a été effacée.");
  };

  return (
    <main className="page wide-page">
      <header className="stack">
        <h1>Vie privée</h1>
        <span className="muted small-text">Ce que FOX retient, et comment l'effacer.</span>
      </header>
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner info" role="status">{notice}</div>}

      <div className="card-grid">
        <section className="card" aria-label="Mode privé">
          <div className="row-between">
            <h2>Mode privé</h2>
            <button
              type="button"
              role="switch"
              aria-checked={priv === true}
              aria-label="Mode privé"
              className={priv ? "switch on" : "switch"}
              disabled={priv === null}
              onClick={() => void togglePrivate()}
            />
          </div>
          <p className="sub">Quand il est actif, rien n'est enregistré : ni historique, ni mémoire. Le contexte temporaire disparaît au bout de quelques minutes.</p>
        </section>

        <section className="card" aria-label="Conversations">
          <h2>Conversations</h2>
          <p className="sub">Vos conversations sont enregistrées pour que vous puissiez les reprendre. Vous pouvez les supprimer à tout moment.</p>
          {confirming === "chats" ? (
            <div className="form-actions">
              <button className="secondary compact danger" onClick={() => void clearAll("chats")}>Oui, tout supprimer</button>
              <button className="secondary compact" onClick={() => setConfirming(null)}>Annuler</button>
            </div>
          ) : (
            <div>
              <button className="secondary compact danger" onClick={() => setConfirming("chats")}>Supprimer toutes mes conversations</button>
            </div>
          )}
        </section>

        <section className="card span" aria-label="Mémoire">
          <div className="row-between">
            <h2>Mémoire</h2>
            {confirming === "memory" ? (
              <span className="form-actions">
                <button className="secondary compact danger" onClick={() => void clearAll("memory")}>Oui, tout effacer</button>
                <button className="secondary compact" onClick={() => setConfirming(null)}>Annuler</button>
              </span>
            ) : (
              <button className="secondary compact danger" disabled={!memories?.length} onClick={() => setConfirming("memory")}>Tout effacer</button>
            )}
          </div>
          <p className="sub">Ce que FOX a retenu pour mieux vous aider. Vous seul pouvez le voir.</p>
          <div>
            {memories?.length === 0 && <p className="muted small-text">Rien n'est mémorisé pour l'instant.</p>}
            {memories?.map((m) => (
              <div key={m.id} className="list-item">
                <span className="grow-text">{m.content}</span>
                <span className="tag">{m.source === "fox" ? "Proposé par FOX" : "Demandé par vous"}</span>
                <button className="secondary compact" aria-label={`Supprimer : ${m.content}`} onClick={() => void removeMemory(m.id)}>Supprimer</button>
              </div>
            ))}
          </div>
          <form className="inline-form" aria-label="Ajouter à la mémoire" onSubmit={addMemory}>
            <label htmlFor="mem" className="sr-only">Ajouter à la mémoire</label>
            <input id="mem" value={newMemory} onChange={(e) => setNewMemory(e.target.value)} placeholder="Demander à FOX de retenir quelque chose" />
            <button type="submit" className="secondary compact" disabled={!newMemory.trim()}>Retenir</button>
          </form>
        </section>

        <section className="card span" aria-label="Journal des actions">
          <h2>Journal des actions</h2>
          <p className="sub">Les dernières actions faites par FOX en votre nom.</p>
          <div>
            {journal?.length === 0 && <p className="muted small-text">Aucune action pour l'instant.</p>}
            {journal?.map((entry, i) => (
              <div key={i} className="list-item">
                <span className="grow-text">{entry.text}</span>
                <span className="muted small-text">{shortDate(dayKey(entry.at))} {timeOf(entry.at)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
