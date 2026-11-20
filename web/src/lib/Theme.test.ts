import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, applyTheme, isThemeChoice, readTheme, resolveTheme, setTheme, watchSystemTheme } from "./theme";

function mockSystem(dark: boolean) {
  const listeners: Array<() => void> = [];
  const query = {
    matches: dark,
    addEventListener: (_: string, fn: () => void) => listeners.push(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.splice(listeners.indexOf(fn), 1),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return { query, listeners };
}

beforeEach(() => {
  window.localStorage.clear();
  document.head.innerHTML = '<meta name="theme-color" content="#C2410C" />';
  document.documentElement.removeAttribute("data-theme");
  mockSystem(false);
});
afterEach(() => vi.unstubAllGlobals());

describe("thème", () => {
  it("accepte seulement clair, sombre et auto", () => {
    expect(["light", "dark", "auto"].every(isThemeChoice)).toBe(true);
    expect([isThemeChoice("bleu"), isThemeChoice(null), isThemeChoice(3)]).toEqual([false, false, false]);
  });

  it("lit le choix enregistré, « auto » par défaut ou si la valeur est invalide", () => {
    expect(readTheme()).toBe("auto");
    window.localStorage.setItem(THEME_KEY, "dark");
    expect(readTheme()).toBe("dark");
    window.localStorage.setItem(THEME_KEY, "<script>");
    expect(readTheme()).toBe("auto");
  });

  it("« auto » suit le système", () => {
    mockSystem(true);
    expect(resolveTheme("auto")).toBe("dark");
    mockSystem(false);
    expect(resolveTheme("auto")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("applique le thème et la couleur de la barre du navigateur", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#0e1620");
    applyTheme("light");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#C2410C");
  });

  it("enregistre le choix et lance la courte transition", () => {
    vi.useFakeTimers();
    setTheme("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement).toHaveAttribute("data-theme-anim");
    vi.advanceTimersByTime(400);
    expect(document.documentElement).not.toHaveAttribute("data-theme-anim");
    vi.useRealTimers();
  });

  it("fonctionne même si le stockage est bloqué", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("bloqué"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("bloqué"); });
    expect(readTheme()).toBe("auto");
    expect(() => setTheme("dark")).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe("dark");
    vi.restoreAllMocks();
  });

  it("en mode auto, suit un changement du système en direct, puis s'arrête", () => {
    const sys = mockSystem(false);
    window.localStorage.setItem(THEME_KEY, "auto");
    const stop = watchSystemTheme();
    sys.query.matches = true;
    sys.listeners.forEach((fn) => fn());
    expect(document.documentElement.dataset.theme).toBe("auto");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#0e1620");
    stop();
    expect(sys.listeners).toHaveLength(0);
  });

  it("un choix manuel n'est pas changé par le système", () => {
    const sys = mockSystem(false);
    window.localStorage.setItem(THEME_KEY, "light");
    applyTheme("light");
    watchSystemTheme();
    sys.query.matches = true;
    sys.listeners.forEach((fn) => fn());
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#C2410C");
  });
});
