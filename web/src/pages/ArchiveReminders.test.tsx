import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";
import { dueReminders } from "../lib/reminders";
import type { AgendaEvent, Task } from "../api/types";

const authed = { "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }) };

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const summary = (over: object = {}) => ({
  id: 3, number: "DV-2026-003", status: "accepte", status_label: "accepté", client: "Atelier Nkolo", total: "355 365 XAF", created_at: "2026-10-29T10:00", archived: false, ...over,
});
const quote = (over: object = {}) => ({
  ...summary(), client_id: 7, currency: "XAF", vat_rate: "19,25", notes: null, subtotal: "298 000 XAF", vat: "57 365 XAF",
  lines: [{ description: "Tablette", quantity: "2", unit_price: "85 000", total: "170 000" }], ...over,
});

describe("Archivage des devis", () => {
  it("archive un devis puis le retire de la liste active", async () => {
    let archived = false;
    const { calls } = mockApi({
      ...authed,
      "GET /quotes": () => ({ body: { quotes: archived ? [] : [summary()] } }),
      "GET /clients": () => ({ body: { clients: [] } }),
      "GET /quotes/3": () => ({ body: { quote: quote() } }),
      "PATCH /quotes/3": () => {
        archived = true;
        return { body: { quote: quote({ archived: true }) } };
      },
    });
    open("/devis");
    await userEvent.click(await screen.findByRole("button", { name: /DV-2026-003/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Archiver" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /DV-2026-003/ })).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ archived: true });
  });

  it("affiche les devis archivés et permet de les désarchiver", async () => {
    const { calls } = mockApi({
      ...authed,
      "GET /quotes": () => ({ body: { quotes: [] } }),
      "GET /quotes?archived=1": () => ({ body: { quotes: [summary({ archived: true })] } }),
      "GET /clients": () => ({ body: { clients: [] } }),
      "GET /quotes/3": () => ({ body: { quote: quote({ archived: true }) } }),
      "PATCH /quotes/3": () => ({ body: { quote: quote() } }),
    });
    open("/devis");
    await userEvent.click(await screen.findByRole("button", { name: "Archivés" }));
    await userEvent.click(await screen.findByRole("button", { name: /DV-2026-003/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Désarchiver" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ archived: false }));
  });
});

describe("Actions en attente dans le chat", () => {
  it("réaffiche une action non confirmée après rechargement", async () => {
    mockApi({
      ...authed,
      "GET /chats": () => ({ body: { chats: [{ id: 7, title: "Devis", updated_at: "2026-11-05T10:00:00" }], current: 7, private: false } }),
      "GET /chats/7/messages": () => ({ body: { messages: [{ id: "m1", role: "user", text: "fais un devis" }] } }),
      "GET /actions/pending": () => ({ body: { actions: [{ id: "a1", summary: "Créer le devis DV-2026-004", expires_at: "2026-11-06T10:10:00" }] } }),
    });
    open("/");
    expect(await screen.findByText("Créer le devis DV-2026-004")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirmer" })).toBeInTheDocument();
  });

  it("reste utilisable si la liste des actions est indisponible", async () => {
    mockApi({
      ...authed,
      "GET /chats": () => ({ body: { chats: [{ id: 7, title: "Devis", updated_at: "2026-11-05T10:00:00" }], current: 7, private: false } }),
      "GET /chats/7/messages": () => ({ body: { messages: [{ id: "m1", role: "user", text: "bonjour" }] } }),
    });
    open("/");
    expect(await screen.findByText("bonjour")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirmer" })).not.toBeInTheDocument();
  });
});

const NOW = new Date("2026-11-06T10:00:00");
const ev = (over: Partial<AgendaEvent> = {}): AgendaEvent => ({ id: 1, title: "RDV client", location: "Douala", start: "2026-11-06T10:20", end: "2026-11-06T11:00", ...over });
const task = (over: Partial<Task> = {}): Task => ({ id: 5, title: "Appeler Yann", priority: "normale", status: "a_faire", overdue: false, due_at: "2026-11-06T09:45", event_id: null, ...over });

describe("dueReminders", () => {
  it("rappelle un rendez-vous dans la fenêtre", () => {
    const r = dueReminders([ev()], [], NOW, 30, new Set());
    expect(r).toHaveLength(1);
    expect(r[0].detail).toContain("Dans 20 min");
    expect(r[0].detail).toContain("Douala");
  });
  it("ignore un rendez-vous trop lointain ou déjà commencé depuis longtemps", () => {
    expect(dueReminders([ev({ start: "2026-11-06T11:00" })], [], NOW, 30, new Set())).toEqual([]);
    expect(dueReminders([ev({ start: "2026-11-06T09:30" })], [], NOW, 30, new Set())).toEqual([]);
  });
  it("ne rappelle pas deux fois", () => {
    const first = dueReminders([ev()], [], NOW, 30, new Set());
    expect(dueReminders([ev()], [], NOW, 30, new Set(first.map((r) => r.key)))).toEqual([]);
  });
  it("rappelle une tâche dont l'heure est passée, pas celle sans heure ni terminée", () => {
    expect(dueReminders([], [task()], NOW, 30, new Set())).toHaveLength(1);
    expect(dueReminders([], [task({ due_at: "2026-11-06T00:00" })], NOW, 30, new Set())).toEqual([]);
    expect(dueReminders([], [task({ status: "fait" as Task["status"] })], NOW, 30, new Set())).toEqual([]);
    expect(dueReminders([], [task({ due_at: "2026-11-06T11:00" })], NOW, 30, new Set())).toEqual([]);
    expect(dueReminders([], [task({ due_at: "2026-11-06T07:00" })], NOW, 30, new Set())).toEqual([]);
  });
});

describe("Rappels dans l'interface", () => {
  beforeEach(() => window.localStorage.clear());
  it("affiche un bandeau et n'appelle jamais les endpoints consommés par Telegram", async () => {
    const soon = new Date(Date.now() + 15 * 60_000);
    const pad = (n: number) => String(n).padStart(2, "0");
    const start = `${soon.getFullYear()}-${pad(soon.getMonth() + 1)}-${pad(soon.getDate())}T${pad(soon.getHours())}:${pad(soon.getMinutes())}`;
    const { calls } = mockApi({
      ...authed,
      "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
      "GET /events?range=aujourd'hui": () => ({ body: { today: "x", reminder_minutes: 30, events: [ev({ start, end: start })] } }),
      "GET /tasks": () => ({ body: { tasks: [] } }),
    });
    open("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("RDV client");
    expect(calls.some((c) => c.path.includes("claim"))).toBe(false);
  });
});
