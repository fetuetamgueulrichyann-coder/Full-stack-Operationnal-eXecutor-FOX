import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER, type Handler } from "../test/mockApi";

const NOTICE = "Les niveaux sont enregistrés mais pas encore appliqués. L'application arrive avec la phase 1c.";

function settings(over: Record<string, unknown> = {}) {
  return {
    enforcement_active: false,
    enforcement_notice: NOTICE,
    level: "protege",
    levels: [
      { id: "protege", label: "Protégé", summary: "Quand ce niveau sera appliqué : chaque envoi demandera ton autorisation.", applied: false },
      { id: "standard", label: "Standard", summary: "Quand ce niveau sera appliqué : le texte et le personnel partent librement.", applied: false },
      { id: "strict", label: "Strict (pas encore actif)", summary: "Quand ce niveau sera appliqué : plus rien ne partira.", applied: false },
    ],
    default_level: "protege",
    preferred_provider: null,
    provider_choices: [{ id: "groq", label: "Groq" }],
    notification_detail: "discret",
    consent: { current_version: 1, version: null, at: null, read: false, notice: "Lire cette information ne t'engage à rien et ne constitue pas une autorisation d'envoi de données." },
    block_all: false,
    ...over,
  };
}

const INFO = {
  enforcement_active: false,
  providers: [
    { id: "gemini", label: "Gemini (Google)", kind: "llm", retention: "Offre gratuite : Google peut relire les contenus.", available: false },
    { id: "groq", label: "Groq", kind: "llm", retention: "Des journaux techniques peuvent être conservés jusqu'à 30 jours.", available: true },
  ],
  declared: { gemini_tier: "free", groq_zdr: false, note: "Ces garanties sont celles déclarées dans la configuration de ce serveur par son administrateur." },
  tracking: { active: false, notice: "Le suivi des envois commence avec la phase 1c. Les envois ne sont pas encore tracés." },
  categories: [{ id: "texte_libre", label: "Ce que tu écris ou dis" }, { id: "fichier", label: "Contenu des fichiers joints" }],
};

function open(path: string, routes: Record<string, Handler> = {}) {
  const api = mockApi({
    "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }),
    "GET /privacy/settings": () => ({ body: settings() }),
    "GET /privacy/info": () => ({ body: INFO }),
    "GET /push/config": () => ({ body: { enabled: false, public_key: null, devices: 0 } }),
    "GET /briefing": () => ({ body: { enabled: false, time: "08:00" } }),
    "GET /business": () => ({ body: { business: null } }),
    "GET /timezone": () => ({ body: { timezone: "Europe/Paris", now: "2026-11-05T11:32+01:00" } }),
    "GET /voice/config": () => ({ body: { enabled: false } }),
    "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
    "GET /memory": () => ({ body: { items: [] } }),
    "GET /journal?limit=15": () => ({ body: { entries: [] } }),
    ...routes,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
  return api;
}

const card = async () => within(await screen.findByRole("region", { name: "Confidentialité" }));

describe("Carte Confidentialité (Réglages)", () => {
  it("affiche les trois niveaux, Protégé choisi, Strict jamais présenté comme actif", async () => {
    open("/reglages");
    const c = await card();
    const group = await c.findByRole("radiogroup", { name: "Niveau de protection" });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(within(group).getByRole("radio", { name: /Protégé/ })).toHaveAttribute("aria-checked", "true");
    expect(within(group).getByRole("radio", { name: /Strict \(pas encore actif\)/ })).toHaveAttribute("aria-checked", "false");
    expect(c.getByRole("note", { name: "État des protections" })).toHaveTextContent("pas encore appliquées");
  });

  it("enregistre le niveau choisi et confirme visuellement", async () => {
    let level = "protege";
    const { calls } = open("/reglages", {
      "GET /privacy/settings": () => ({ body: settings({ level }) }),
      "POST /privacy/settings": (call) => {
        level = (call.body as { level: string }).level;
        return { body: settings({ level }) };
      },
    });
    const c = await card();
    await userEvent.click(await c.findByRole("radio", { name: /Standard/ }));
    await waitFor(() => expect(c.getByRole("radio", { name: /Standard/ })).toHaveAttribute("aria-checked", "true"));
    expect(c.getAllByText("Enregistré").length).toBeGreaterThan(0);
    const post = calls.find((x) => x.method === "POST" && x.path === "/privacy/settings");
    expect(post?.body).toEqual({ level: "standard" });
    expect(post?.headers["X-CSRF-Token"]).toBe("t");
  });

  it("garde la valeur du serveur et affiche l'erreur quand l'enregistrement échoue", async () => {
    open("/reglages", { "POST /privacy/settings": () => ({ status: 400, body: { error: "niveau inconnu", code: "bad_request" } }) });
    const c = await card();
    await userEvent.click(await c.findByRole("radio", { name: /Strict/ }));
    expect(await c.findByRole("alert")).toHaveTextContent("niveau inconnu");
    expect(c.getByRole("radio", { name: /Protégé/ })).toHaveAttribute("aria-checked", "true");
    expect(c.getByRole("radio", { name: /Strict/ })).toHaveAttribute("aria-checked", "false");
    expect(c.queryByText("Enregistré")).toBeNull();
  });

  it("testeur : un seul fournisseur proposé ; propriétaire : deux", async () => {
    open("/reglages");
    let c = await card();
    let group = await c.findByRole("radiogroup", { name: "Fournisseur d'IA préféré" });
    expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual([expect.stringContaining("Automatique"), expect.stringContaining("Groq")]);
    expect(c.getByText(/ne l'utilise pas encore/)).toBeInTheDocument();
    document.body.innerHTML = "";
    open("/reglages", { "GET /privacy/settings": () => ({ body: settings({ provider_choices: [{ id: "gemini", label: "Gemini (Google)" }, { id: "groq", label: "Groq" }] }) }) });
    c = await card();
    group = await c.findByRole("radiogroup", { name: "Fournisseur d'IA préféré" });
    expect(within(group).getAllByRole("radio")).toHaveLength(3);
  });

  it("envoie null quand on revient à « Automatique »", async () => {
    const { calls } = open("/reglages", {
      "GET /privacy/settings": () => ({ body: settings({ preferred_provider: "groq" }) }),
      "POST /privacy/settings": () => ({ body: settings({ preferred_provider: null }) }),
    });
    const c = await card();
    await userEvent.click(await c.findByRole("radio", { name: /Automatique/ }));
    await waitFor(() => expect(calls.some((x) => x.method === "POST")).toBe(true));
    expect(calls.find((x) => x.method === "POST")?.body).toEqual({ preferred_provider: null });
  });

  it("notifications : discret par défaut, complet sur demande", async () => {
    const { calls } = open("/reglages", { "POST /privacy/settings": () => ({ body: settings({ notification_detail: "complet" }) }) });
    const c = await card();
    const discreet = await c.findByRole("radio", { name: /Discret/ });
    expect(discreet).toHaveAttribute("aria-checked", "true");
    expect(c.getByText(/Telegram affichent encore le titre/)).toBeInTheDocument();
    await userEvent.click(c.getByRole("radio", { name: /Complet/ }));
    await waitFor(() => expect(c.getByRole("radio", { name: /Complet/ })).toHaveAttribute("aria-checked", "true"));
    expect(calls.find((x) => x.method === "POST")?.body).toEqual({ notification_detail: "complet" });
  });

  it("consentement : enregistre la version, ne bloque rien, n'est pas une autorisation", async () => {
    const { calls } = open("/reglages", {
      "POST /privacy/consent": () => ({ body: settings({ consent: { current_version: 1, version: 1, at: "2026-11-25T08:00:00+00:00", read: true, notice: "n" } }) }),
    });
    const c = await card();
    expect(await c.findByText("Pas encore lue")).toBeInTheDocument();
    expect(c.getByText(/ne constitue pas une autorisation d'envoi/)).toBeInTheDocument();
    await userEvent.click(c.getByRole("button", { name: "J'ai lu l'information" }));
    expect(await c.findByText("Information lue")).toBeInTheDocument();
    expect(c.getByText(/Version 1, enregistrée le 25 novembre 2026/)).toBeInTheDocument();
    expect(calls.find((x) => x.path === "/privacy/consent")?.body).toEqual({ version: 1 });
  });

  it("serveur injoignable : erreur claire, bandeau honnête, bouton réessayer", async () => {
    let fail = true;
    open("/reglages", { "GET /privacy/settings": () => (fail ? { status: 500, body: { error: "Erreur du serveur." } } : { body: settings() }) });
    const c = await card();
    expect(await c.findByRole("alert")).toBeInTheDocument();
    expect(c.getByRole("note", { name: "État des protections" })).toHaveTextContent("pas encore appliqués");
    fail = false;
    await userEvent.click(c.getByRole("button", { name: "Réessayer" }));
    expect(await c.findByRole("radiogroup", { name: "Niveau de protection" })).toBeInTheDocument();
  });

  it("n'affiche plus le bandeau quand le serveur déclare les niveaux appliqués (1c)", async () => {
    open("/reglages", { "GET /privacy/settings": () => ({ body: settings({ enforcement_active: true, enforcement_notice: null }) }) });
    const c = await card();
    await c.findByRole("radiogroup", { name: "Niveau de protection" });
    expect(c.queryByRole("note", { name: "État des protections" })).toBeNull();
  });
});

describe("Page Confidentialité", () => {
  it("présente les niveaux, les fournisseurs, le bandeau et le suivi prévu en 1c", async () => {
    open("/confidentialite");
    expect(await screen.findByRole("heading", { name: "Confidentialité", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("note", { name: "État des protections" })).toHaveTextContent("phase 1c");
    expect(await screen.findByRole("heading", { name: "Strict (pas encore actif)" })).toBeInTheDocument();
    expect(screen.getByText("Votre niveau")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Gemini (Google)" })).toBeInTheDocument();
    expect(screen.getByText("Non disponible pour votre compte")).toBeInTheDocument();
    expect(screen.getByText("Disponible pour votre compte")).toBeInTheDocument();
    expect(screen.getByText(/Des journaux techniques peuvent être conservés jusqu'à 30 jours/)).toBeInTheDocument();
    expect(screen.getByText(/Le suivi des envois commence avec la phase 1c/)).toBeInTheDocument();
    expect(screen.getByText(/garanties sont celles déclarées/)).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getAllByRole("link", { name: "Vie privée" })[0]).toHaveAttribute("href", "/prive");
  });

  it("le panneau de détail s'ouvre et se ferme", async () => {
    open("/confidentialite");
    const button = await screen.findByRole("button", { name: "Qu'est-ce qui reste chez FOX ?" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("ne promet aucune protection effective : aucune phrase de garantie au présent", async () => {
    open("/confidentialite");
    await screen.findByRole("heading", { name: "Gemini (Google)" });
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/vos données sont (protégées|sécurisées|chiffrées)/i);
    expect(text).not.toMatch(/Strict (est|est maintenant) (actif|opérationnel)/i);
    expect(text).not.toMatch(/rien ne part(ira)? plus/i);
  });
});

describe("Vie privée (/prive) et inscription", () => {
  it("l'ancienne page garde ses fonctions et ajoute un accès vers Confidentialité", async () => {
    open("/prive");
    expect(await screen.findByRole("switch", { name: "Mode privé" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Mémoire" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Journal des actions" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /page Confidentialité/ })).toHaveAttribute("href", "/confidentialite");
  });

  it("l'inscription informe sans rendre le consentement obligatoire", async () => {
    open("/inscription", { "GET /auth/me": () => ({ status: 401, body: { error: "non", code: "unauthenticated" } }) });
    const note = await screen.findByRole("note", { name: "Information sur vos données" });
    expect(note).toHaveTextContent("fournisseur d'IA externe");
    expect(note).toHaveTextContent("pas encore appliqués");
    expect(note).toHaveTextContent("facultatif");
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("button", { name: /Créer mon compte/ })).toBeEnabled();
  });
});

describe("Mouvement et polices", () => {
  const css = readFileSync(join(process.cwd(), "src/privacy.css"), "utf8");
  it("coupe toutes les animations de la phase avec prefers-reduced-motion", () => {
    const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    for (const cls of [".pv-reveal", ".pv-saved", ".pv-skeleton i", ".pv-choice", ".pv-disclosure-body"]) expect(block).toContain(cls);
  });
  it("ne charge aucune ressource externe", () => {
    expect(css).not.toMatch(/https?:\/\//);
    expect(readFileSync(join(process.cwd(), "index.html"), "utf8")).not.toMatch(/fonts\.(googleapis|gstatic)/);
  });
});
