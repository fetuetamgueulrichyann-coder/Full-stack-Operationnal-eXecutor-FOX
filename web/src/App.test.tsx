import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { onUnauthorized, setCsrfToken } from "./api/client";
import { USER, mockApi, unauthenticated, type Handler } from "./test/mockApi";

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const loggedIn: Record<string, Handler> = {
  "GET /auth/me": () => ({ body: { user: USER, csrf_token: "tok" } }),
  "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
};

beforeEach(() => {
  setCsrfToken("");
  onUnauthorized(null);
});
afterEach(() => vi.unstubAllGlobals());

describe("démarrage", () => {
  it("envoie un visiteur non connecté vers la connexion", async () => {
    mockApi({ "GET /auth/me": unauthenticated });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Connexion" })).toBeInTheDocument();
  });

  it("affiche l'écran de chargement puis la discussion pour un utilisateur connecté", async () => {
    mockApi(loggedIn);
    renderApp("/");
    expect(screen.getByText("Chargement de Fox...")).toBeInTheDocument();
    expect(await screen.findByText("Que puis-je faire pour vous ?")).toBeInTheDocument();
    expect(screen.getByText("marie")).toBeInTheDocument();
  });

  it("propose Réessayer quand le serveur est injoignable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderApp("/");
    expect(await screen.findByText("Échec de la connexion.")).toBeInTheDocument();
    mockApi({ "GET /auth/me": unauthenticated });
    await userEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(await screen.findByRole("heading", { name: "Connexion" })).toBeInTheDocument();
  });

  it("renvoie un utilisateur connecté de la connexion vers la discussion", async () => {
    mockApi(loggedIn);
    renderApp("/connexion");
    expect(await screen.findByText("Que puis-je faire pour vous ?")).toBeInTheDocument();
  });
});

describe("connexion", () => {
  it("connecte l'utilisateur, garde le jeton CSRF et ouvre la discussion", async () => {
    const { calls } = mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": () => ({ body: { user: USER, csrf_token: "jeton-web" } }),
      "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
      "POST /private": () => ({ body: { private: true } }),
    });
    renderApp("/connexion");
    await userEvent.type(await screen.findByLabelText("Identifiant"), "marie");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "mot-de-passe-solide");
    await userEvent.click(screen.getByRole("button", { name: /Se connecter/ }));
    expect(await screen.findByText("Que puis-je faire pour vous ?")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/login")?.body).toEqual({ username: "marie", password: "mot-de-passe-solide" });

    await userEvent.click(screen.getByRole("switch", { name: "Mode privé" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/private")).toBe(true));
    expect(calls.find((c) => c.path === "/private")?.headers["X-CSRF-Token"]).toBe("jeton-web");
  });

  it("affiche un message clair pour un mauvais mot de passe et reste sur la page", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": () => ({ status: 401, body: { error: "identifiants invalides", code: "invalid_credentials" } }),
    });
    renderApp("/connexion");
    await userEvent.type(await screen.findByLabelText("Identifiant"), "marie");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "faux");
    await userEvent.click(screen.getByRole("button", { name: /Se connecter/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Identifiant ou mot de passe incorrect.");
    expect(screen.getByRole("heading", { name: "Connexion" })).toBeInTheDocument();
  });

  it("affiche le message du serveur après trop d'essais", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": () => ({ status: 429, body: { error: "Trop d'essais, réessayez plus tard.", code: "too_many_attempts" } }),
    });
    renderApp("/connexion");
    await userEvent.type(await screen.findByLabelText("Identifiant"), "marie");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "x");
    await userEvent.click(screen.getByRole("button", { name: /Se connecter/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Trop d'essais");
  });

  it("affiche et masque le mot de passe", async () => {
    mockApi({ "GET /auth/me": unauthenticated });
    renderApp("/connexion");
    const field = await screen.findByLabelText("Mot de passe");
    expect(field).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Afficher le mot de passe" }));
    expect(field).toHaveAttribute("type", "text");
  });
});

describe("inscription", () => {
  it("crée le compte avec le code en majuscules", async () => {
    const { calls } = mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/register": () => ({ status: 201, body: { user: USER, csrf_token: "t" } }),
      "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
    });
    renderApp("/inscription");
    await userEvent.type(await screen.findByLabelText("Code d'invitation"), "abcd2345");
    await userEvent.type(screen.getByLabelText("Identifiant"), "marie");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "mot-de-passe-solide");
    await userEvent.click(screen.getByRole("button", { name: /Créer mon compte/ }));
    expect(await screen.findByText("Que puis-je faire pour vous ?")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/register")?.body).toEqual({
      code: "ABCD2345",
      username: "marie",
      password: "mot-de-passe-solide",
    });
  });

  it("montre l'erreur d'un code d'invitation invalide", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/register": () => ({ status: 400, body: { error: "Code d'invitation invalide ou expiré.", code: "invitation_invalid" } }),
    });
    renderApp("/inscription");
    await userEvent.type(await screen.findByLabelText("Code d'invitation"), "ZZZZ");
    await userEvent.type(screen.getByLabelText("Identifiant"), "marie");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "mot-de-passe-solide");
    await userEvent.click(screen.getByRole("button", { name: /Créer mon compte/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Code d'invitation invalide");
  });
});

describe("discussion", () => {
  it("envoie un message avec la conversation active et affiche la réponse", async () => {
    const { calls } = mockApi({
      ...loggedIn,
      "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
      "POST /message": () => ({ body: { reply: "Bonjour Marie !", pending: [], chat_id: 9, private: false } }),
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Bonjour");
    await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(await screen.findByText("Bonjour Marie !")).toBeInTheDocument();
    expect(screen.getByText("Bonjour")).toBeInTheDocument();
    const sent = calls.find((c) => c.path === "/message");
    expect(sent?.body).toEqual({ text: "Bonjour" });
    expect(sent?.headers["X-CSRF-Token"]).toBe("tok");
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("garde la conversation active pour les messages suivants", async () => {
    const { calls } = mockApi({
      ...loggedIn,
      "POST /message": () => ({ body: { reply: "ok", pending: [], chat_id: 9, private: false } }),
    });
    renderApp("/");
    const box = await screen.findByLabelText("Message");
    await userEvent.type(box, "un{enter}");
    await screen.findByText("ok");
    await userEvent.type(box, "deux{enter}");
    await waitFor(() => expect(calls.filter((c) => c.path === "/message")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/message")[1].body).toEqual({ text: "deux", chat_id: 9 });
  });

  it("affiche la carte de confirmation et exécute l'action confirmée", async () => {
    const { calls } = mockApi({
      ...loggedIn,
      "POST /message": () => ({
        body: { reply: "Je peux l'ajouter.", pending: [{ id: "a1", summary: "Ajouter le rendez-vous « Dentiste »" }], chat_id: 3, private: false },
      }),
      "POST /actions/a1/confirm": () => ({ body: { reply: "Rendez-vous ajouté." } }),
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Ajoute un rdv{enter}");
    expect(await screen.findByText("Action en attente")).toBeInTheDocument();
    expect(screen.getByText("Ajouter le rendez-vous « Dentiste »")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirmer" }));
    expect(await screen.findByText("Rendez-vous ajouté.")).toBeInTheDocument();
    expect(screen.getByText("Action confirmée")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirmer" })).not.toBeInTheDocument();
    expect(calls.find((c) => c.path === "/actions/a1/confirm")?.headers["X-CSRF-Token"]).toBe("tok");
  });

  it("annule une action en attente", async () => {
    const { calls } = mockApi({
      ...loggedIn,
      "POST /message": () => ({ body: { reply: "Je peux le supprimer.", pending: [{ id: "a2", summary: "Supprimer" }], chat_id: 3, private: false } }),
      "POST /actions/a2/cancel": () => ({ body: { reply: "Annulé." } }),
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Supprime{enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Annuler" }));
    expect(await screen.findByText("Action annulée")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/actions/a2/cancel")).toBe(true);
  });

  it("garde le texte et propose Réessayer quand le modèle est indisponible", async () => {
    let attempts = 0;
    mockApi({
      ...loggedIn,
      "POST /message": () => {
        attempts += 1;
        return attempts === 1
          ? { status: 503, body: { error: "modele indisponible", code: "model_unavailable" } }
          : { body: { reply: "Me revoilà.", pending: [], chat_id: 5, private: false } };
      },
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Salut{enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("momentanément indisponible");
    await userEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(await screen.findByText("Me revoilà.")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("ne propose pas de renvoyer un message déjà traité quand une autre action échoue", async () => {
    mockApi({
      ...loggedIn,
      "POST /message": () => ({ body: { reply: "ok", pending: [], chat_id: 2, private: false } }),
      "POST /private": () => ({ status: 500, body: { error: "panne", code: "internal_error" } }),
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Salut{enter}");
    await screen.findByText("ok");
    await userEvent.click(screen.getByRole("switch", { name: "Mode privé" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("erreur est survenue");
    expect(screen.queryByRole("button", { name: "Réessayer" })).not.toBeInTheDocument();
  });

  it("explique la limite du jour", async () => {
    mockApi({
      ...loggedIn,
      "POST /message": () => ({ status: 429, body: { error: "plafond", code: "quota_exceeded", scope: "user" } }),
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Salut{enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("limite du jour");
  });

  it("revient à la connexion quand la session a expiré", async () => {
    mockApi({ ...loggedIn, "POST /message": unauthenticated });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Message"), "Salut{enter}");
    expect(await screen.findByRole("heading", { name: "Connexion" })).toBeInTheDocument();
  });

  it("reprend une conversation de la liste avec son historique", async () => {
    const { calls } = mockApi({
      ...loggedIn,
      "GET /chats": () => ({
        body: { chats: [{ id: 7, title: "Devis atelier", created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-02T10:00:00Z", messages: 2 }], current: null, private: false },
      }),
      "POST /chats/7/open": () => ({ body: { chat_id: 7, private: false } }),
      "GET /chats/7/messages": () => ({
        body: { chat_id: 7, messages: [
          { id: "1u", role: "user", text: "Fais le devis", at: "2026-11-02T10:00:00+01:00" },
          { id: "1a", role: "assistant", text: "Voici le devis.", at: "2026-11-02T10:00:00+01:00" },
        ] },
      }),
    });
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: /^Devis atelier/ }));
    expect(await screen.findByText("Voici le devis.")).toBeInTheDocument();
    expect(screen.getByText("Fais le devis")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/chats/7/open" && c.method === "POST")).toBe(true);
  });

  it("supprime une conversation", async () => {
    let chats = [{ id: 7, title: "À supprimer", created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-02T10:00:00Z", messages: 2 }];
    const { calls } = mockApi({
      ...loggedIn,
      "GET /chats": () => ({ body: { chats, current: null, private: false } }),
      "DELETE /chats/7": () => {
        chats = [];
        return { body: { status: "deleted" } };
      },
    });
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: /Supprimer la conversation À supprimer/ }));
    await waitFor(() => expect(screen.queryByText("À supprimer")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/chats/7")).toBe(true);
  });

  it("se déconnecte", async () => {
    const { calls } = mockApi({ ...loggedIn, "POST /auth/logout": () => ({ body: { status: "ok" } }) });
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: "Se déconnecter" }));
    expect(await screen.findByRole("heading", { name: "Connexion" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/logout")?.headers["X-CSRF-Token"]).toBe("tok");
  });
});
