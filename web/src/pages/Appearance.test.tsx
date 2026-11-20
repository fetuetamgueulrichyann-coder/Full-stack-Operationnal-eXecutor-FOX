import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppRoutes } from "../App";
import { mockApi, USER } from "../test/mockApi";

function open() {
  mockApi({
    "GET /auth/me": () => ({ body: { user: USER, csrf_token: "tok" } }),
    "GET /push/config": () => ({ body: { enabled: false, public_key: null, devices: 0 } }),
    "GET /voice/config": () => ({ body: { enabled: false, providers: [], voice_choice: false, voice: "Kore", voices: [], speed: 1, daily_limit: 0, used: 0 } }),
    "GET /briefing": () => ({ body: { enabled: false, time: "08:00" } }),
    "GET /business": () => ({ body: { business: { name: null, phone: null, email: null, address: null, tax_id: null } } }),
    "GET /timezone": () => ({ body: { timezone: "Europe/Paris", default: "Europe/Paris", custom: false, now: "2026-11-05T11:32+01:00" } }),
  });
  return render(
    <MemoryRouter initialEntries={["/reglages"]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined })));
});

describe("Réglages : apparence", () => {
  it("propose Clair, Sombre et Auto, Auto étant choisi par défaut", async () => {
    open();
    const group = await screen.findByRole("radiogroup", { name: "Thème" });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Clair/ })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /Sombre/ })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /Auto/ })).toHaveAttribute("aria-checked", "true");
  });

  it("choisir Sombre l'applique tout de suite et le mémorise", async () => {
    open();
    await userEvent.click(await screen.findByRole("radio", { name: /Sombre/ }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("fox-theme")).toBe("dark");
    expect(screen.getByRole("radio", { name: /Sombre/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /Auto/ })).toHaveAttribute("aria-checked", "false");
  });

  it("revient à Clair, puis reprend le choix mémorisé à la visite suivante", async () => {
    window.localStorage.setItem("fox-theme", "dark");
    const view = open();
    expect(await screen.findByRole("radio", { name: /Sombre/ })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: /Clair/ }));
    expect(document.documentElement.dataset.theme).toBe("light");
    view.unmount();
    open();
    expect(await screen.findByRole("radio", { name: /Clair/ })).toHaveAttribute("aria-checked", "true");
  });

  it("indique le thème actuel quand « Auto » est choisi", async () => {
    open();
    expect(await screen.findByText(/Thème actuel : clair/)).toBeInTheDocument();
  });
});
