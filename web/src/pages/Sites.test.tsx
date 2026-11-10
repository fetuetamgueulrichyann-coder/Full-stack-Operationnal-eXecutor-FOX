import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";
import type { LedgerEntry, Project } from "../api/types";

const authed = { "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }) };

function open(path = "/chantiers") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const project = (over: Partial<Project> = {}): Project => ({
  id: 5, name: "Villa Nkolo", status: "en_cours", status_label: "en cours", client_id: null, client: "Atelier Nkolo", quote: "DV-2026-001",
  start_date: "2026-11-01", end_date: null, notes: null, archived: false, currency: "XAF",
  income: "150000.00", income_label: "150 000 XAF", expense: "52500.00", expense_label: "52 500 XAF", margin: "97500.00", margin_label: "97 500 XAF",
  budget: "100000.00", budget_label: "100 000 XAF", budget_left: "47500.00", budget_left_label: "47 500 XAF", budget_used_percent: 53, ...over,
});
const entry = (over: Partial<LedgerEntry> = {}): LedgerEntry => ({
  id: 1, kind: "depense", kind_label: "dépense", amount: "40000.00", amount_label: "40 000 XAF", currency: "XAF", date: "2026-11-04",
  category: "matériaux", description: "Ciment", project_id: 5, project: "Villa Nkolo", ...over,
});

describe("Chantiers", () => {
  it("liste les chantiers et affiche budget, recettes, dépenses et marge", async () => {
    mockApi({
      ...authed,
      "GET /projects": () => ({ body: { projects: [project()] } }),
      "GET /projects/5": () => ({ body: { project: project() } }),
      "GET /ledger?month=all&project=5": () => ({ body: { month: "all", entries: [entry()] } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Villa Nkolo/ }));
    const metrics = await screen.findByRole("region", { name: "Chiffres du chantier" });
    expect(within(metrics).getByText("150 000 XAF")).toBeInTheDocument();
    expect(within(metrics).getByText("52 500 XAF")).toBeInTheDocument();
    expect(within(metrics).getByText("97 500 XAF")).toBeInTheDocument();
    expect(screen.getByText(/53 % du budget dépensé · reste 47 500 XAF/)).toBeInTheDocument();
    expect(screen.getByText(/Ciment · matériaux/)).toBeInTheDocument();
  });

  it("signale un dépassement de budget", async () => {
    mockApi({
      ...authed,
      "GET /projects": () => ({ body: { projects: [project()] } }),
      "GET /projects/5": () => ({ body: { project: project({ budget_used_percent: 120, budget_left_label: "-20 000 XAF" }) } }),
      "GET /ledger?month=all&project=5": () => ({ body: { month: "all", entries: [] } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Villa Nkolo/ }));
    expect(await screen.findByText(/dépassement de 20 000 XAF/)).toBeInTheDocument();
  });

  it("crée un chantier", async () => {
    const { calls } = mockApi({
      ...authed,
      "GET /projects": () => ({ body: { projects: [] } }),
      "POST /projects": () => ({ status: 201, body: { project: project() } }),
      "GET /projects/5": () => ({ body: { project: project() } }),
      "GET /ledger?month=all&project=5": () => ({ body: { month: "all", entries: [] } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Nouveau" }));
    await userEvent.type(screen.getByLabelText("Nom du chantier"), "Villa Nkolo");
    await userEvent.type(screen.getByLabelText("Budget de dépenses"), "100000");
    await userEvent.click(screen.getByRole("button", { name: "Créer le chantier" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "Villa Nkolo", budget: "100000" }));
    expect(await screen.findByRole("region", { name: "Chiffres du chantier" })).toBeInTheDocument();
  });

  it("affiche l'erreur du serveur à la création", async () => {
    mockApi({
      ...authed,
      "GET /projects": () => ({ body: { projects: [] } }),
      "POST /projects": () => ({ status: 409, body: { error: "Un chantier porte déjà ce nom.", code: "conflict" } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Nouveau" }));
    await userEvent.type(screen.getByLabelText("Nom du chantier"), "Villa Nkolo");
    await userEvent.click(screen.getByRole("button", { name: "Créer le chantier" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Un chantier porte déjà ce nom.");
  });

  it("change le statut puis archive", async () => {
    let archived = false;
    const { calls } = mockApi({
      ...authed,
      "GET /projects": () => ({ body: { projects: archived ? [] : [project()] } }),
      "GET /projects/5": () => ({ body: { project: project() } }),
      "GET /ledger?month=all&project=5": () => ({ body: { month: "all", entries: [] } }),
      "PATCH /projects/5": (c) => {
        const body = c.body as { archived?: boolean };
        if (body.archived) archived = true;
        return { body: { project: project({ status: "termine", status_label: "terminé" }) } };
      },
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Villa Nkolo/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Terminé" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { status?: string }).status === "termine")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Archiver" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /Villa Nkolo/ })).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "PATCH" && (c.body as { archived?: boolean }).archived === true)).toBe(true);
  });
});

describe("Compta", () => {
  const month = new Date();
  const key = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`;
  const summary = {
    month: key, currency: "XAF", income: "1000.00", income_label: "1 000 XAF", expense: "600.00", expense_label: "600 XAF",
    balance: "400.00", balance_label: "400 XAF", entries: 2,
    by_category: [{ kind: "depense" as const, category: "matériaux", total: "500.00", total_label: "500 XAF" }],
  };

  async function openLedger(routes = {}) {
    const mocked = mockApi({
      ...authed,
      "GET /projects": () => ({ body: { projects: [project()] } }),
      [`GET /ledger/summary?month=${key}`]: () => ({ body: { summary } }),
      [`GET /ledger?month=${key}`]: () => ({ body: { month: key, entries: [entry()] } }),
      ...routes,
    });
    open();
    await userEvent.click(await screen.findByRole("tab", { name: "Compta" }));
    return mocked;
  }

  it("affiche le bilan du mois, les catégories et les écritures", async () => {
    await openLedger();
    const bilan = await screen.findByRole("region", { name: "Bilan du mois" });
    expect(within(bilan).getByText("1 000 XAF")).toBeInTheDocument();
    expect(within(bilan).getByText("600 XAF")).toBeInTheDocument();
    expect(within(bilan).getByText("400 XAF")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Par catégorie" })).toHaveTextContent("matériaux");
    expect(screen.getByText(/Ciment · matériaux · Villa Nkolo/)).toBeInTheDocument();
  });

  it("enregistre une dépense rattachée à un chantier", async () => {
    const { calls } = await openLedger({ "POST /ledger": () => ({ status: 201, body: { entry: entry() } }) });
    await screen.findByRole("region", { name: "Bilan du mois" });
    await userEvent.type(screen.getByLabelText("Montant"), "25000");
    await userEvent.type(screen.getByLabelText("Description"), "Transport");
    await userEvent.selectOptions(screen.getByLabelText("Chantier"), "Villa Nkolo");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ kind: "depense", amount: "25000", description: "Transport", project: 5 }));
  });

  it("supprime une écriture", async () => {
    const { calls } = await openLedger({ "DELETE /ledger/1": () => ({ body: { status: "deleted" } }) });
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer l'écriture Ciment" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/ledger/1")).toBe(true));
  });

  it("montre l'erreur de saisie renvoyée par le serveur", async () => {
    await openLedger({ "POST /ledger": () => ({ status: 400, body: { error: "Le montant doit être supérieur à zéro.", code: "bad_request" } }) });
    await screen.findByRole("region", { name: "Bilan du mois" });
    await userEvent.type(screen.getByLabelText("Montant"), "0");
    await userEvent.type(screen.getByLabelText("Description"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("supérieur à zéro");
  });
});
