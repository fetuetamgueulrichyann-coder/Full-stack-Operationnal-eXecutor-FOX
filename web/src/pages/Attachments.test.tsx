import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER, type Handler } from "../test/mockApi";

const REPLY = { reply: "Voici ce que je vois.", chat_id: 1, private: false, pending: [] as unknown[] };
let uploads = 0;

function routes(extra: Record<string, Handler> = {}): Record<string, Handler> {
  uploads = 0;
  return {
    "GET /auth/me": () => ({ body: { user: USER, csrf_token: "tok" } }),
    "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
    "GET /voice/config": () => ({ body: { enabled: false } }),
    "POST /message": () => ({ body: REPLY }),
    "POST /files": (call) => {
      const file = call.form?.get("file") as File;
      uploads += 1;
      const kind = file.type.startsWith("image/") ? "image" : file.name.endsWith(".pdf") ? "pdf" : "text";
      return { status: 201, body: { file: { id: `00000000-0000-4000-8000-00000000000${uploads}`, name: file.name, kind, size: file.size } } };
    },
    "DELETE /files/00000000-0000-4000-8000-000000000001": () => ({ body: { ok: true } }),
    ...extra,
  };
}

function open(extra: Record<string, Handler> = {}) {
  const api = mockApi(routes(extra));
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
  return api;
}

const pdf = (name = "facture.pdf") => new File(["%PDF-1.4 contenu"], name, { type: "application/pdf" });
const photo = (name = "mur.jpg") => new File([new Uint8Array(2000)], name, { type: "image/jpeg" });

beforeEach(() => {
  window.localStorage.clear();
  URL.createObjectURL = vi.fn(() => "blob:apercu");
  URL.revokeObjectURL = vi.fn();
});

describe("Discussion : fichiers joints", () => {
  it("propose Joindre et Photo", async () => {
    open();
    expect(await screen.findByRole("button", { name: "Joindre un fichier" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prendre une photo" })).toBeInTheDocument();
  });

  it("envoie le fichier tout de suite avec le jeton de sécurité, sans forcer le type de contenu", async () => {
    const api = open();
    await userEvent.upload(await screen.findByLabelText("Choisir des fichiers à joindre"), pdf());
    expect(await screen.findByText("facture.pdf")).toBeInTheDocument();
    const call = api.calls.find((c) => c.method === "POST" && c.path === "/files");
    expect(call).toBeTruthy();
    expect(call!.headers["X-CSRF-Token"]).toBe("tok");
    expect(call!.headers["Content-Type"]).toBeUndefined();
    expect((call!.form!.get("file") as File).name).toBe("facture.pdf");
    expect(await screen.findByText(/\d+ o|Ko/)).toBeInTheDocument();
  });

  it("on peut envoyer un fichier sans écrire de texte : les identifiants partent avec le message", async () => {
    const api = open();
    const send = await screen.findByRole("button", { name: "Envoyer" });
    expect(send).toBeDisabled();
    await userEvent.upload(screen.getByLabelText("Choisir des fichiers à joindre"), pdf());
    await waitFor(() => expect(send).toBeEnabled());
    await userEvent.click(send);
    expect(await screen.findByText("Voici ce que je vois.")).toBeInTheDocument();
    const message = api.calls.find((c) => c.method === "POST" && c.path === "/message")!;
    expect(message.body).toEqual({ text: "", files: ["00000000-0000-4000-8000-000000000001"] });
    expect(screen.queryByRole("list", { name: "Fichiers joints au message" })).not.toBeInTheDocument(); // la zone est vidée
    const bubble = screen.getByText("facture.pdf").closest(".bubble.user")!;
    expect(within(bubble as HTMLElement).getByRole("link")).toHaveAttribute("href", "/api/files/00000000-0000-4000-8000-000000000001");
  });

  it("une photo s'affiche en miniature et part avec le texte", async () => {
    const api = open();
    await userEvent.upload(await screen.findByLabelText("Choisir des fichiers à joindre"), photo());
    await userEvent.type(screen.getByLabelText("Message"), "Que dois-je refaire ?");
    await waitFor(() => expect(screen.getByRole("button", { name: "Envoyer" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await screen.findByText("Voici ce que je vois.");
    expect(api.calls.find((c) => c.path === "/message")!.body).toMatchObject({ text: "Que dois-je refaire ?", files: [expect.any(String)] });
    const image = screen.getByRole("img", { name: "mur.jpg" });
    expect(image).toHaveAttribute("src", "blob:apercu");
  });

  it("Envoyer reste bloqué pendant l'envoi du fichier", async () => {
    let finish: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { finish = resolve; });
    open({ "POST /files": async () => { await gate; return { status: 201, body: { file: { id: "00000000-0000-4000-8000-000000000009", name: "lent.pdf", kind: "pdf", size: 16 } } }; } });
    await userEvent.upload(await screen.findByLabelText("Choisir des fichiers à joindre"), pdf("lent.pdf"));
    await userEvent.type(screen.getByLabelText("Message"), "Bonjour");
    expect(await screen.findByText("Envoi…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeDisabled();
    finish();
    await waitFor(() => expect(screen.getByRole("button", { name: "Envoyer" })).toBeEnabled());
  });

  it("un fichier refusé montre la raison ; les autres restent envoyables", async () => {
    open({
      "POST /files": (call) => {
        const file = call.form?.get("file") as File;
        if (file.name === "virus.exe") return { status: 415, body: { error: "Type de fichier non pris en charge.", code: "file_type" } };
        return { status: 201, body: { file: { id: "00000000-0000-4000-8000-000000000001", name: file.name, kind: "pdf", size: file.size } } };
      },
    });
    const input = await screen.findByLabelText("Choisir des fichiers à joindre");
    // Le navigateur ne filtrerait pas un fichier glissé ou renommé : on teste la réponse du serveur.
    await userEvent.setup({ applyAccept: false }).upload(input, [new File(["MZ"], "virus.exe"), pdf()]);
    expect(await screen.findByText("Type de fichier non pris en charge.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("facture.pdf")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Retirer virus.exe" }));
    expect(screen.queryByText("virus.exe")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Envoyer" })).toBeEnabled());
  });

  it("au plus quatre fichiers par message", async () => {
    const api = open();
    const input = await screen.findByLabelText("Choisir des fichiers à joindre");
    await userEvent.upload(input, [pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"), pdf("d.pdf"), pdf("e.pdf")]);
    expect(await screen.findByText("4 fichiers au plus par message.")).toBeInTheDocument();
    expect(api.calls.filter((c) => c.path === "/files")).toHaveLength(4);
    expect(screen.queryByText("e.pdf")).not.toBeInTheDocument();
  });

  it("retirer un fichier déjà envoyé au serveur le supprime là-bas", async () => {
    const api = open();
    await userEvent.upload(await screen.findByLabelText("Choisir des fichiers à joindre"), pdf());
    await screen.findByText(/o$|Ko/);
    await userEvent.click(screen.getByRole("button", { name: "Retirer facture.pdf" }));
    expect(screen.queryByText("facture.pdf")).not.toBeInTheDocument();
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/files/00000000-0000-4000-8000-000000000001")).toBe(true));
  });

  it("Réessayer renvoie le message avec ses fichiers", async () => {
    let fail = true;
    const api = open({ "POST /message": () => (fail ? { status: 503, body: { error: "FOX est indisponible.", code: "llm_unavailable" } } : { body: REPLY }) });
    await userEvent.upload(await screen.findByLabelText("Choisir des fichiers à joindre"), pdf());
    await waitFor(() => expect(screen.getByRole("button", { name: "Envoyer" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await screen.findByRole("alert");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    await screen.findByText("Voici ce que je vois.");
    const messages = api.calls.filter((c) => c.path === "/message");
    expect(messages).toHaveLength(2);
    expect(messages[1].body).toEqual(messages[0].body);
  });

  it("l'historique réaffiche les photos et les documents joints", async () => {
    open({
      "GET /chats": () => ({ body: { chats: [{ id: 1, title: "Chantier", updated_at: "2026-11-18T10:00:00+01:00", messages: 1 }], current: 1, private: false } }),
      "GET /chats/1/messages": () => ({
        body: {
          messages: [
            { id: "1u", role: "user", text: "Regarde", at: "10:00", files: [
              { id: "11111111-1111-4111-8111-111111111111", name: "mur.jpg", kind: "image", size: 2000 },
              { id: "22222222-2222-4222-8222-222222222222", name: "plan.pdf", kind: "pdf", size: 4096 },
            ] },
            { id: "1b", role: "assistant", text: "Je vois un mur fissuré.", at: "10:00" },
          ],
        },
      }),
      "GET /actions/pending": () => ({ body: { actions: [] } }),
    });
    const image = await screen.findByRole("img", { name: "mur.jpg" });
    expect(image).toHaveAttribute("src", "/api/files/11111111-1111-4111-8111-111111111111");
    expect(screen.getByRole("link", { name: /plan\.pdf/ })).toHaveAttribute("href", "/api/files/22222222-2222-4222-8222-222222222222");
  });

  it("en mode privé, les fichiers de la bulle ne sont plus des liens", async () => {
    open({ "POST /message": () => ({ body: { ...REPLY, private: true, chat_id: null } }) });
    await userEvent.upload(await screen.findByLabelText("Choisir des fichiers à joindre"), pdf());
    await waitFor(() => expect(screen.getByRole("button", { name: "Envoyer" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await screen.findByText("Voici ce que je vois.");
    expect(screen.queryByRole("link", { name: /facture\.pdf/ })).not.toBeInTheDocument();
    expect(screen.getByText("facture.pdf")).toBeInTheDocument();
  });
});

describe("Discussion : appareil photo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sur téléphone, « Photo » ouvre l'appareil photo (capture) et envoie l'image", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined })));
    const api = open();
    const input = await screen.findByLabelText("Prendre une photo avec l'appareil");
    expect(input).toHaveAttribute("capture", "environment");
    expect(input).toHaveAttribute("accept", "image/*");
    const click = vi.spyOn(input, "click");
    await userEvent.click(screen.getByRole("button", { name: "Prendre une photo" }));
    expect(click).toHaveBeenCalled();
    await userEvent.upload(input, photo("camera.jpg"));
    await screen.findByText("camera.jpg");
    expect(api.calls.some((c) => c.path === "/files")).toBe(true);
  });

  it("sur ordinateur, « Photo » ouvre la webcam, puis la photo est jointe", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined })));
    const stop = vi.fn();
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] }));
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    HTMLMediaElement.prototype.play = vi.fn(async () => undefined);
    const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: vi.fn() }), toBlob: (cb: (b: Blob | null) => void) => cb(new Blob([new Uint8Array(500)], { type: "image/jpeg" })) };
    const real = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => (tag === "canvas" ? canvas : real(tag))) as typeof document.createElement);
    const api = open();
    await userEvent.click(await screen.findByRole("button", { name: "Prendre une photo" }));
    const dialog = await screen.findByRole("dialog", { name: "Prendre une photo" });
    expect(getUserMedia).toHaveBeenCalled();
    const video = within(dialog).getByLabelText("Aperçu de la caméra") as HTMLVideoElement;
    Object.defineProperty(video, "videoWidth", { value: 640 });
    Object.defineProperty(video, "videoHeight", { value: 480 });
    video.dispatchEvent(new Event("loadedmetadata"));
    await userEvent.click(await within(dialog).findByRole("button", { name: /Prendre la photo/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(stop).toHaveBeenCalled(); // la caméra est coupée
    expect(await screen.findByText(/^photo-\d{4}-\d{2}-\d{2}-\d{4}\.jpg$/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/files")).toBe(true);
  });

  it("caméra refusée : message clair, et on peut fermer", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined })));
    const getUserMedia = vi.fn(async () => { throw new DOMException("refusé", "NotAllowedError"); });
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Prendre une photo" }));
    expect(await screen.findByText(/L'accès à la caméra est refusé/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Fermer" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sans webcam, « Photo » retombe sur le choix d'un fichier image", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined })));
    Object.defineProperty(navigator, "mediaDevices", { value: undefined, configurable: true });
    open();
    const input = await screen.findByLabelText("Prendre une photo avec l'appareil");
    const click = vi.spyOn(input, "click");
    await userEvent.click(screen.getByRole("button", { name: "Prendre une photo" }));
    expect(click).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Discussion : fichiers créés par FOX", () => {
  const MADE = [
    { id: "33333333-3333-4333-8333-333333333333", name: "Rapport.pdf", kind: "pdf", size: 2400 },
    { id: "44444444-4444-4444-8444-444444444444", name: "Budget.xlsx", kind: "excel", size: 6100 },
  ];

  it("affiche une carte de téléchargement sous la réponse", async () => {
    open({ "POST /message": () => ({ body: { ...REPLY, reply: "Voilà le rapport.", files: MADE } }) });
    await userEvent.type(await screen.findByRole("textbox"), "Fais-moi un PDF");
    await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await screen.findByText("Voilà le rapport.");
    const pdfLink = screen.getByRole("link", { name: /Rapport\.pdf/ });
    expect(pdfLink).toHaveAttribute("href", "/api/files/33333333-3333-4333-8333-333333333333");
    expect(pdfLink).toHaveAttribute("download", "Rapport.pdf");
    expect(screen.getByRole("link", { name: /Budget\.xlsx/ })).toBeInTheDocument();
  });

  it("sans fichier créé, aucune carte", async () => {
    open();
    await userEvent.type(await screen.findByRole("textbox"), "Bonjour");
    await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await screen.findByText("Voici ce que je vois.");
    expect(screen.queryByRole("link", { name: /\.pdf/ })).not.toBeInTheDocument();
  });

  it("les retrouve dans l'historique de la conversation", async () => {
    open({
      "GET /chats": () => ({ body: { chats: [{ id: 1, title: "Rapport", updated_at: "2026-11-21T09:00:00+01:00" }], current: 1, private: false } }),
      "GET /chats/1/messages": () => ({
        body: { chat_id: 1, messages: [
          { id: "1u", role: "user", text: "Un PDF", at: "2026-11-21T09:00", files: [] },
          { id: "1a", role: "assistant", text: "Voilà.", at: "2026-11-21T09:00", files: [MADE[0]] },
        ] },
      }),
    });
    expect(await screen.findByRole("link", { name: /Rapport\.pdf/ })).toHaveAttribute("download", "Rapport.pdf");
  });
});
