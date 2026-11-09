import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";

const authed = {
  "GET /auth/me": () => ({ body: { user: USER, csrf_token: "t" } }),
  "GET /chats": () => ({ body: { chats: [], current: null, private: false } }),
};

function open() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

class FakeRecognition {
  static last: FakeRecognition | null = null;
  lang = "";
  interimResults = false;
  continuous = false;
  onresult: ((e: { results: unknown }) => void) | null = null;
  onerror: ((e: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn(() => {
    FakeRecognition.last = this;
  });
  stop = vi.fn(() => this.onend?.());
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
  FakeRecognition.last = null;
});

describe("Dictée vocale", () => {
  it("n'affiche pas le micro si le navigateur ne sait pas dicter", async () => {
    mockApi(authed);
    open();
    await screen.findByRole("textbox", { name: "Message" });
    expect(screen.queryByRole("button", { name: "Dicter un message" })).not.toBeInTheDocument();
  });

  it("met le texte dicté dans le champ sans l'envoyer", async () => {
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRecognition;
    const { calls } = mockApi(authed);
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Dicter un message" }));
    expect(FakeRecognition.last?.lang).toBe("fr-FR");
    act(() => FakeRecognition.last?.onresult?.({ results: [[{ transcript: " rendez-vous demain à 9 heures " }]] }));
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("rendez-vous demain à 9 heures");
    expect(calls.some((c) => c.path === "/message")).toBe(false);
  });

  it("explique un micro refusé", async () => {
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRecognition;
    mockApi(authed);
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Dicter un message" }));
    act(() => FakeRecognition.last?.onerror?.({ error: "not-allowed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Le micro est bloqué");
  });
});
