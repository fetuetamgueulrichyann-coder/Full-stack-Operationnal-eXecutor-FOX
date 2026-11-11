import { render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER, type Handler } from "../test/mockApi";

const REPLY = { reply: "Voici votre agenda.", chat_id: 1, private: false, pending: [] as unknown[] };

function routes(extra: Record<string, Handler> = {}, voiceEnabled = true): Record<string, Handler> {
  return {
    "GET /auth/me": () => ({ body: { user: USER, csrf_token: "tok" } }),
    "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
    "GET /voice/config": () => ({ body: { enabled: voiceEnabled, voice: "Kore", daily_limit: 4000, used: 0 } }),
    "POST /message": () => ({ body: REPLY }),
    ...extra,
  };
}

class FakeAudio {
  static last: FakeAudio | null = null;
  static playImpl: () => Promise<void> = () => Promise.resolve();
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public src: string) {
    FakeAudio.last = this;
  }
  play = vi.fn(() => FakeAudio.playImpl());
  pause = vi.fn();
}

class FakeRecognition {
  static last: FakeRecognition | null = null;
  static starts = 0;
  lang = "";
  interimResults = false;
  continuous = false;
  onresult: ((e: { results: unknown }) => void) | null = null;
  onerror: ((e: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn(() => {
    FakeRecognition.last = this;
    FakeRecognition.starts += 1;
  });
  stop = vi.fn(() => this.onend?.());
}

/** fetch : la voix répond par un fichier son (ou une erreur), tout le reste passe par mockApi. */
function setup(extra: Record<string, Handler> = {}, opts: { voiceEnabled?: boolean; speakStatus?: number; speakCode?: string } = {}) {
  const api = mockApi(routes(extra, opts.voiceEnabled ?? true));
  const base = api.fn;
  const speaks: { body: unknown; headers: Record<string, string> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/voice/speak") {
        speaks.push({ body: JSON.parse(String(init?.body)), headers: (init?.headers ?? {}) as Record<string, string> });
        if (opts.speakStatus && opts.speakStatus >= 400) {
          return new Response(JSON.stringify({ code: opts.speakCode ?? "voice_unavailable" }), { status: opts.speakStatus });
        }
        return new Response(new Blob(["RIFF"], { type: "audio/wav" }), { status: 200 });
      }
      return base(input, init);
    }),
  );
  return { ...api, speaks };
}

function open() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  FakeAudio.last = null;
  FakeAudio.playImpl = () => Promise.resolve();
  FakeRecognition.last = null;
  FakeRecognition.starts = 0;
  vi.stubGlobal("Audio", FakeAudio);
  URL.createObjectURL = vi.fn(() => "blob:fox");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
});

async function ask(text = "mon agenda") {
  await userEvent.type(await screen.findByRole("textbox", { name: "Message" }), text);
  await userEvent.click(screen.getByRole("button", { name: "Envoyer" }));
  await screen.findByText("Voici votre agenda.");
}

describe("Voix de FOX", () => {
  it("n'affiche aucun bouton de voix si le serveur ne la propose pas", async () => {
    setup({}, { voiceEnabled: false });
    open();
    await ask();
    expect(screen.queryByRole("button", { name: "Écouter la réponse" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Démarrer la conversation orale" })).not.toBeInTheDocument();
  });

  it("lit une réponse à voix haute avec le jeton CSRF, puis revient à l'état de repos", async () => {
    const { speaks } = setup();
    open();
    await ask();
    await userEvent.click(await screen.findByRole("button", { name: "Écouter la réponse" }));
    await waitFor(() => expect(FakeAudio.last?.play).toHaveBeenCalled());
    expect(speaks[0].body).toEqual({ text: "Voici votre agenda." });
    expect(speaks[0].headers["X-CSRF-Token"]).toBe("tok");
    expect(await screen.findByRole("button", { name: "Arrêter la lecture" })).toBeInTheDocument();
    act(() => FakeAudio.last?.onended?.());
    expect(await screen.findByRole("button", { name: "Écouter la réponse" })).toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fox");
  });

  it("arrête la lecture au second clic", async () => {
    setup();
    open();
    await ask();
    await userEvent.click(await screen.findByRole("button", { name: "Écouter la réponse" }));
    await userEvent.click(await screen.findByRole("button", { name: "Arrêter la lecture" }));
    expect(FakeAudio.last?.pause).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Écouter la réponse" })).toBeInTheDocument();
  });

  it("explique le plafond de voix atteint", async () => {
    setup({}, { speakStatus: 429, speakCode: "voice_quota" });
    open();
    await ask();
    await userEvent.click(await screen.findByRole("button", { name: "Écouter la réponse" }));
    expect(await screen.findByText(/plafond de voix du jour/)).toBeInTheDocument();
    expect(FakeAudio.last).toBeNull();
  });

  it("message clair si le navigateur bloque la lecture", async () => {
    setup();
    FakeAudio.playImpl = () => Promise.reject(new Error("NotAllowedError"));
    open();
    await ask();
    await userEvent.click(await screen.findByRole("button", { name: "Écouter la réponse" }));
    expect(await screen.findByText(/bloqué la lecture/)).toBeInTheDocument();
  });
});

describe("Conversation orale", () => {
  beforeEach(() => {
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRecognition;
  });

  it("boucle : j'écoute, j'envoie ce qui est dit, FOX répond à voix haute, puis j'écoute de nouveau", async () => {
    const { calls } = setup();
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Démarrer la conversation orale" }));
    expect(FakeRecognition.starts).toBe(1);
    expect(await screen.findByText("Je vous écoute, parlez…")).toBeInTheDocument();

    act(() => FakeRecognition.last?.onresult?.({ results: [[{ transcript: "quel est mon agenda" }]] }));
    await waitFor(() => expect(calls.find((c) => c.path === "/message")?.body).toMatchObject({ text: "quel est mon agenda" }));
    await waitFor(() => expect(FakeAudio.last?.play).toHaveBeenCalled());
    expect(FakeRecognition.starts).toBe(1); // FOX parle : il n'écoute pas

    act(() => FakeAudio.last?.onended?.());
    await waitFor(() => expect(FakeRecognition.starts).toBe(2));
  });

  it("n'écoute pas pendant qu'une action attend une confirmation", async () => {
    const pending = [{ id: "a1", summary: "Créer le rendez-vous", tool: "create_event", expires_at: "" }];
    setup({ "POST /message": () => ({ body: { ...REPLY, pending } }) });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Démarrer la conversation orale" }));
    act(() => FakeRecognition.last?.onresult?.({ results: [[{ transcript: "crée un rendez-vous" }]] }));
    await waitFor(() => expect(FakeAudio.last?.play).toHaveBeenCalled());
    act(() => FakeAudio.last?.onended?.());
    await screen.findByText("Créer le rendez-vous");
    expect(FakeRecognition.starts).toBe(1);
  });

  it("l'arrêt coupe la voix et l'écoute", async () => {
    setup();
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Démarrer la conversation orale" }));
    await userEvent.click(screen.getByRole("button", { name: "Arrêter la conversation orale" }));
    expect(FakeRecognition.last?.stop).toHaveBeenCalled();
    expect(screen.queryByText("Je vous écoute, parlez…")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Démarrer la conversation orale" })).toBeInTheDocument();
  });

  it("sans conversation orale, la dictée garde son comportement : le texte attend dans le champ", async () => {
    const { calls } = setup();
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Dicter un message" }));
    act(() => FakeRecognition.last?.onresult?.({ results: [[{ transcript: "bonjour" }]] }));
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("bonjour");
    expect(calls.some((c) => c.path === "/message")).toBe(false);
  });
});
