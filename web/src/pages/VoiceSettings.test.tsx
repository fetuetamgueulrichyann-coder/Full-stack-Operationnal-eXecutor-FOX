import { render, screen, waitFor, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER, type Handler } from "../test/mockApi";
import { clearSpeechCache } from "../lib/voice";

const VOICES = [
  { name: "Kore", label: "Ferme" },
  { name: "Puck", label: "Dynamique" },
  { name: "Charon", label: "Informative" },
];

function config(extra: Record<string, unknown> = {}) {
  return { enabled: true, providers: ["gemini"], voice_choice: true, voice: "Kore", voices: VOICES, speed: 1, daily_limit: 40000, used: 1250, ...extra };
}

class FakeAudio {
  static last: FakeAudio | null = null;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  playbackRate = 1;
  constructor(public src: string) {
    FakeAudio.last = this;
  }
  play = vi.fn(() => Promise.resolve());
  pause = vi.fn();
}

function open(routes: Record<string, Handler>) {
  const api = mockApi({
    "GET /auth/me": () => ({ body: { user: USER, csrf_token: "tok" } }),
    "GET /push/config": () => ({ body: { enabled: false, public_key: null, devices: 0 } }),
    "GET /briefing": () => ({ body: { enabled: false, time: "08:00" } }),
    "GET /business": () => ({ body: { business: { name: null, phone: null, email: null, address: null, tax_id: null } } }),
    "GET /timezone": () => ({ body: { timezone: "Europe/Paris", default: "Europe/Paris", custom: false, now: "2026-11-05T11:32+01:00" } }),
    ...routes,
  });
  const base = api.fn;
  const speaks: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/voice/speak") {
        speaks.push(JSON.parse(String(init?.body)));
        return new Response(new Blob(["RIFF"], { type: "audio/wav" }), { status: 200 });
      }
      return base(input, init);
    }),
  );
  render(
    <MemoryRouter initialEntries={["/reglages"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { ...api, speaks };
}

beforeEach(() => {
  clearSpeechCache();
  FakeAudio.last = null;
  vi.stubGlobal("Audio", FakeAudio);
  URL.createObjectURL = vi.fn(() => "blob:fox");
  URL.revokeObjectURL = vi.fn();
});

describe("Réglages de la voix", () => {
  it("affiche les voix, la vitesse et le plafond du jour", async () => {
    open({ "GET /voice/config": () => ({ body: config() }) });
    const card = await screen.findByRole("region", { name: "Voix de FOX" });
    expect(card).toHaveTextContent("Kore — Ferme");
    expect(screen.getByRole("combobox", { name: "Voix" })).toHaveValue("Kore");
    expect(screen.getByRole("slider")).toHaveValue("1");
    expect(card).toHaveTextContent("1 250");
    expect(within(card).getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });

  it("n'affiche rien si la voix est désactivée ou indisponible", async () => {
    open({ "GET /voice/config": () => ({ body: config({ enabled: false }) }) });
    await screen.findByRole("region", { name: "Notifications" });
    expect(screen.queryByRole("region", { name: "Voix de FOX" })).not.toBeInTheDocument();
  });

  it("enregistre la voix et la vitesse choisies", async () => {
    const { calls } = open({
      "GET /voice/config": () => ({ body: config() }),
      "POST /voice/settings": (req) => ({ body: config(req.body as Record<string, unknown>) }),
    });
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Voix" }), "Puck");
    const slider = screen.getByRole("slider");
    act(() => {
      // jsdom : changement de valeur d'un curseur
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(slider, "1.25");
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await userEvent.click(within(screen.getByRole("region", { name: "Voix de FOX" })).getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText("Voix enregistrée.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/voice/settings")?.body).toEqual({ voice: "Puck", speed: 1.25 });
  });

  it("joue un échantillon avec la voix choisie, sans l'enregistrer", async () => {
    const { calls, speaks } = open({ "GET /voice/config": () => ({ body: config() }) });
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Voix" }), "Charon");
    await userEvent.click(screen.getByRole("button", { name: "Écouter un échantillon" }));
    await waitFor(() => expect(FakeAudio.last?.play).toHaveBeenCalled());
    expect(speaks[0]).toMatchObject({ voice: "Charon" });
    expect(calls.some((c) => c.path === "/voice/settings")).toBe(false);
    expect(await screen.findByRole("button", { name: "Arrêter l'échantillon" })).toBeInTheDocument();
    act(() => FakeAudio.last?.onended?.());
    expect(await screen.findByRole("button", { name: "Écouter un échantillon" })).toBeInTheDocument();
  });

  it("applique la vitesse à la lecture", async () => {
    open({ "GET /voice/config": () => ({ body: config({ speed: 1.3 }) }) });
    await screen.findByRole("region", { name: "Voix de FOX" });
    await userEvent.click(screen.getByRole("button", { name: "Écouter un échantillon" }));
    await waitFor(() => expect(FakeAudio.last?.play).toHaveBeenCalled());
    expect(FakeAudio.last?.playbackRate).toBe(1.3);
  });

  it("quand le serveur choisit la voix (ElevenLabs), pas de liste : seulement la vitesse", async () => {
    open({ "GET /voice/config": () => ({ body: config({ providers: ["elevenlabs", "gemini"], voice_choice: false }) }) });
    const card = await screen.findByRole("region", { name: "Voix de FOX" });
    expect(card).toHaveTextContent("choisie par le serveur (elevenlabs)");
    expect(screen.queryByRole("combobox", { name: "Voix" })).not.toBeInTheDocument();
    expect(screen.getByRole("slider")).toBeInTheDocument();
  });

  it("affiche l'erreur du serveur", async () => {
    open({
      "GET /voice/config": () => ({ body: config() }),
      "POST /voice/settings": () => ({ status: 400, body: { error: "voix inconnue" } }),
    });
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Voix" }), "Puck");
    await userEvent.click(within(screen.getByRole("region", { name: "Voix de FOX" })).getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText(/voix inconnue/)).toBeInTheDocument();
  });
});
