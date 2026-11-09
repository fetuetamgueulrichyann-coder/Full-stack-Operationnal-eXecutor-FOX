import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";

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

const memory = (over: object = {}) => ({ id: 1, content: "Je préfère les rendez-vous le matin.", source: "user", created_at: "2026-11-01T10:00", ...over });

function privacyRoutes(state: { priv: boolean; memories: object[] }) {
  return {
    ...authed,
    "GET /chats": () => ({ body: { chats: [], current: null, private: state.priv } }),
    "GET /memory": () => ({ body: { items: state.memories, private: state.priv } }),
    "GET /journal?limit=15": () => ({ body: { entries: [{ at: "2026-11-02T08:14+01:00", text: "Rendez-vous « Dentiste » supprimé" }] } }),
  };
}

describe("Vie privée", () => {
  it("affiche la mémoire avec son origine et le journal des actions", async () => {
    mockApi(privacyRoutes({ priv: false, memories: [memory(), memory({ id: 2, content: "Je signe Marie.", source: "fox" })] }));
    open("/prive");
    expect(await screen.findByText("Je préfère les rendez-vous le matin.")).toBeInTheDocument();
    expect(screen.getByText("Demandé par vous")).toBeInTheDocument();
    expect(screen.getByText("Proposé par FOX")).toBeInTheDocument();
    expect(screen.getByText("Rendez-vous « Dentiste » supprimé")).toBeInTheDocument();
    expect(screen.getByText("02/11 08:14")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Mode privé" })).toHaveAttribute("aria-checked", "false");
  });

  it("active le mode privé", async () => {
    const state = { priv: false, memories: [] as object[] };
    const { calls } = mockApi({ ...privacyRoutes(state), "POST /private": () => { state.priv = true; return { body: { private: true } }; } });
    open("/prive");
    await userEvent.click(await screen.findByRole("switch", { name: "Mode privé" }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Mode privé" })).toHaveAttribute("aria-checked", "true"));
    expect(calls.find((c) => c.path === "/private")?.body).toEqual({ on: true });
  });

  it("ajoute puis supprime un élément de mémoire", async () => {
    const state = { priv: false, memories: [memory()] };
    const { calls } = mockApi({
      ...privacyRoutes(state),
      "POST /memory": (c) => { state.memories = [...state.memories, memory({ id: 2, content: (c.body as { text: string }).text })]; return { status: 201, body: {} }; },
      "DELETE /memory/1": () => { state.memories = state.memories.filter((m) => (m as { id: number }).id !== 1); return { body: { status: "deleted" } }; },
    });
    open("/prive");
    await userEvent.type(await screen.findByRole("textbox", { name: "Ajouter à la mémoire" }), "Mon client principal est Nkolo");
    await userEvent.click(screen.getByRole("button", { name: "Retenir" }));
    expect(await screen.findByText("Mon client principal est Nkolo")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Supprimer : Je préfère/ }));
    await waitFor(() => expect(screen.queryByText("Je préfère les rendez-vous le matin.")).toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/memory")?.body).toEqual({ text: "Mon client principal est Nkolo" });
  });

  it("demande une confirmation avant de supprimer toutes les conversations", async () => {
    const { calls } = mockApi({ ...privacyRoutes({ priv: false, memories: [] }), "DELETE /chats": () => ({ body: { status: "deleted", chats: 3 } }) });
    open("/prive");
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer toutes mes conversations" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Oui, tout supprimer" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Toutes vos conversations ont été supprimées.");
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ confirm: "all" });
  });

  it("efface toute la mémoire après confirmation", async () => {
    const { calls } = mockApi({ ...privacyRoutes({ priv: false, memories: [memory()] }), "DELETE /memory": () => ({ body: { status: "deleted", count: 1 } }) });
    open("/prive");
    await userEvent.click(await screen.findByRole("button", { name: "Tout effacer" }));
    await userEvent.click(screen.getByRole("button", { name: "Oui, tout effacer" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/memory")).toBe(true));
    expect(calls.find((c) => c.path === "/memory" && c.method === "DELETE")?.body).toEqual({ confirm: "all" });
  });
});

function settingsRoutes(extra: Record<string, never> | object = {}) {
  return {
    ...authed,
    "GET /timezone": () => ({ body: { timezone: "Europe/Paris", default: "Europe/Paris", custom: false, now: "2026-11-05T11:32+01:00" } }),
    "GET /briefing": () => ({ body: { enabled: true, time: "07:30" } }),
    "GET /business": () => ({ body: { business: { name: "Menuiserie Fotso", phone: "6 99 00 11 22", email: null, address: "Bonanjo, Douala", tax_id: null } } }),
    ...extra,
  };
}

describe("Réglages", () => {
  it("affiche le fuseau, le briefing, le profil et le compte", async () => {
    mockApi(settingsRoutes());
    open("/reglages");
    expect(await screen.findByDisplayValue("Menuiserie Fotso")).toBeInTheDocument();
    expect(screen.getByText(/Heure actuelle : 11:32/)).toBeInTheDocument();
    expect(screen.getByLabelText("Heure d'envoi")).toHaveValue("07:30");
    expect(screen.getByRole("switch", { name: "Briefing du matin" })).toHaveAttribute("aria-checked", "true");
    expect(within(screen.getByRole("region", { name: "Compte" })).getByText("marie")).toBeInTheDocument();
  });

  it("enregistre le fuseau horaire", async () => {
    const { calls } = mockApi(settingsRoutes({ "POST /timezone": () => ({ body: { timezone: "Africa/Douala", default: "Europe/Paris", custom: true, now: "2026-11-05T11:32+01:00" } }) }));
    open("/reglages");
    await userEvent.selectOptions(await screen.findByLabelText("Fuseau"), "Africa/Douala");
    const card = screen.getByRole("region", { name: "Fuseau horaire" });
    await userEvent.click(within(card).getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Fuseau horaire enregistré.");
    expect(calls.find((c) => c.path === "/timezone" && c.method === "POST")?.body).toEqual({ timezone: "Africa/Douala" });
  });

  it("désactive le briefing et affiche l'aperçu", async () => {
    const { calls } = mockApi(settingsRoutes({
      "POST /briefing": () => ({ body: { enabled: false, time: "07:30" } }),
      "GET /briefing/preview": () => ({ body: { text: "Bonjour, voici votre journée." } }),
    }));
    open("/reglages");
    await userEvent.click(await screen.findByRole("switch", { name: "Briefing du matin" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/briefing" && c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.path === "/briefing" && c.method === "POST")?.body).toEqual({ enabled: false, time: "07:30" });
    await userEvent.click(screen.getByRole("button", { name: "Voir l'aperçu" }));
    expect(await screen.findByLabelText("Aperçu du briefing")).toHaveTextContent("Bonjour, voici votre journée.");
  });

  it("envoie le profil d'entreprise au format attendu, sans point-virgule dans les champs", async () => {
    const { calls } = mockApi(settingsRoutes({ "PUT /business": () => ({ body: { business: {} } }) }));
    open("/reglages");
    const name = await screen.findByLabelText("Nom de l'entreprise");
    await userEvent.clear(name);
    await userEvent.type(name, "Fotso; Frères");
    const form = screen.getByRole("form", { name: "Profil d'entreprise" });
    await userEvent.click(within(form).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ text: "Fotso, Frères;6 99 00 11 22;;Bonanjo, Douala;" });
  });

  it("supprime le profil d'entreprise", async () => {
    let deleted = false;
    const { calls } = mockApi(settingsRoutes({
      "GET /business": () => ({ body: { business: deleted ? null : { name: "Menuiserie Fotso", phone: null, email: null, address: null, tax_id: null } } }),
      "DELETE /business": () => { deleted = true; return { body: { status: "deleted" } }; },
    }));
    open("/reglages");
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer le profil" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(screen.queryByRole("button", { name: "Supprimer le profil" })).toBeNull();
  });
});
