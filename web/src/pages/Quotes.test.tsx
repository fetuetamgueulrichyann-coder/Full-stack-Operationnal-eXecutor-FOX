import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";

const authed = { "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }) };

function open() {
  return render(
    <MemoryRouter initialEntries={["/devis"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const summary = (over: object = {}) => ({
  id: 3, number: "DV-2026-003", status: "envoye", status_label: "envoyé", client: "Atelier Nkolo", total: "355 365 XAF", created_at: "2026-10-29T10:00", ...over,
});
const quote = (over: object = {}) => ({
  ...summary(), client_id: 7, currency: "XAF", vat_rate: "19,25", notes: null, subtotal: "298 000 XAF", vat: "57 365 XAF",
  lines: [
    { description: "Tablette de comptoir", quantity: "2", unit_price: "85 000", total: "170 000" },
    { description: "Étagères murales", quantity: "4", unit_price: "32 000", total: "128 000" },
  ],
  ...over,
});
const client = (over: object = {}) => ({ id: 7, name: "Atelier Nkolo", phone: "6 99 12 34 56", email: "nkolo@example.cm", address: "Douala", notes: null, ...over });

const lists = {
  "GET /quotes": () => ({ body: { quotes: [summary()] } }),
  "GET /clients": () => ({ body: { clients: [client()] } }),
};

describe("Clients et devis", () => {
  it("liste les devis et les clients, puis affiche le détail avec les totaux", async () => {
    mockApi({ ...authed, ...lists, "GET /quotes/3": () => ({ body: { quote: quote() } }) });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /DV-2026-003/ }));
    const table = await screen.findByRole("region", { name: "Lignes du devis" });
    expect(within(table).getByText("Tablette de comptoir")).toBeInTheDocument();
    const totals = screen.getByRole("region", { name: "Totaux" });
    expect(within(totals).getByText("TVA 19,25 %")).toBeInTheDocument();
    expect(within(totals).getByText("355 365 XAF")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Télécharger le PDF" })).toHaveAttribute("href", "/api/quotes/3/pdf");
    // le nom apparaît dans la liste des devis et dans celle des clients
    expect(screen.getAllByText("Atelier Nkolo", { selector: ".item-name" })).toHaveLength(2);
  });

  it("change le statut d'un devis", async () => {
    const { calls } = mockApi({
      ...authed, ...lists,
      "GET /quotes/3": () => ({ body: { quote: quote() } }),
      "PATCH /quotes/3": () => ({ body: { quote: quote({ status: "accepte", status_label: "accepté" }) } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /DV-2026-003/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Marquer accepté" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Marquer accepté" })).toBeNull());
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "accepte" });
  });

  it("explique pourquoi un devis envoyé ne peut pas être supprimé", async () => {
    mockApi({
      ...authed, ...lists,
      "GET /quotes/3": () => ({ body: { quote: quote() } }),
      "DELETE /quotes/3": () => ({ status: 409, body: { error: "Un devis envoyé ou accepté ne peut pas être supprimé. Passe-le d'abord en refusé.", code: "conflict" } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /DV-2026-003/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ne peut pas être supprimé");
  });

  it("crée un devis au format attendu par l'API", async () => {
    const { calls } = mockApi({
      ...authed, ...lists,
      "POST /quotes": () => ({ status: 201, body: { quote: quote({ id: 4, number: "DV-2026-004" }), registered: true } }),
      "GET /quotes/4": () => ({ body: { quote: quote({ id: 4, number: "DV-2026-004" }) } }),
    });
    open();
    await userEvent.click((await screen.findAllByRole("button", { name: "Nouveau" }))[0]);
    await userEvent.type(screen.getByLabelText("Client"), "Atelier Nkolo");
    await userEvent.type(screen.getByLabelText("Désignation 1"), "Étagères");
    await userEvent.clear(screen.getByLabelText("Quantité 1"));
    await userEvent.type(screen.getByLabelText("Quantité 1"), "4");
    await userEvent.type(screen.getByLabelText("Prix unitaire 1"), "32000");
    await userEvent.click(screen.getByRole("button", { name: "Ajouter une ligne" }));
    await userEvent.type(screen.getByLabelText("Désignation 2"), "Pose");
    await userEvent.type(screen.getByLabelText("Prix unitaire 2"), "0");
    await userEvent.type(screen.getByLabelText("TVA en % (facultatif)"), "18");
    await userEvent.click(screen.getByRole("button", { name: "Créer le devis" }));
    expect(await screen.findByRole("region", { name: "Lignes du devis" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/quotes")?.body).toEqual({
      text: "Atelier Nkolo\nÉtagères | 4 | 32000\nPose | 1 | 0\nTVA 18",
    });
  });

  it("affiche le brouillon d'e-mail avec un lien de messagerie", async () => {
    mockApi({
      ...authed, ...lists,
      "GET /quotes/3": () => ({ body: { quote: quote() } }),
      "GET /quotes/3/mail": () => ({ body: { to: "nkolo@example.cm", subject: "Devis DV-2026-003", body: "Bonjour Atelier Nkolo," } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /DV-2026-003/ }));
    await userEvent.click(await screen.findByRole("button", { name: "E-mail au client" }));
    const link = await screen.findByRole("link", { name: "Ouvrir dans ma messagerie" });
    expect(link.getAttribute("href")).toContain("mailto:nkolo%40example.cm?subject=Devis%20DV-2026-003");
  });

  it("modifie puis supprime un client", async () => {
    const { calls } = mockApi({
      ...authed, ...lists,
      "PATCH /clients/7": () => ({ body: { client: client({ phone: "6 00 00 00 00" }) } }),
      "DELETE /clients/7": () => ({ body: { status: "deleted" } }),
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /^Atelier Nkolo/ }));
    const phone = await screen.findByLabelText("Téléphone");
    await userEvent.clear(phone);
    await userEvent.type(phone, "6 00 00 00 00");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Client enregistré.");
    expect((calls.find((c) => c.method === "PATCH")?.body as { phone: string }).phone).toBe("6 00 00 00 00");
    await userEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });
});
