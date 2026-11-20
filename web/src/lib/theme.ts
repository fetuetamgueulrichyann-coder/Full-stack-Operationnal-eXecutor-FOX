/** Thème de l'interface : clair, sombre, ou « auto » (suit le système). Le choix est gardé sur cet appareil. */
export type ThemeChoice = "light" | "dark" | "auto";

export const THEME_KEY = "fox-theme";
export const THEME_CHOICES: ThemeChoice[] = ["light", "dark", "auto"];
/** Couleur de la barre du navigateur (mobile) : l'orange de FOX en clair, le fond sombre en sombre. */
export const THEME_COLORS = { light: "#C2410C", dark: "#0e1620" } as const;

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === "string" && (THEME_CHOICES as string[]).includes(value);
}

export function readTheme(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    return isThemeChoice(stored) ? stored : "auto";
  } catch {
    return "auto"; // stockage bloqué (navigation privée…) : on suit le système
  }
}

/** Thème réellement affiché pour ce choix. */
export function resolveTheme(choice: ThemeChoice): "light" | "dark" {
  if (choice !== "auto") return choice;
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

/** Applique le choix à la page (attribut data-theme lu par le CSS, couleur de la barre du navigateur). */
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  root.dataset.theme = choice;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) meta.content = THEME_COLORS[resolveTheme(choice)];
}

/** Enregistre et applique. Une courte transition adoucit le changement (jamais au premier affichage). */
export function setTheme(choice: ThemeChoice): void {
  try {
    window.localStorage.setItem(THEME_KEY, choice);
  } catch {
    /* le choix reste appliqué pour cette visite */
  }
  const root = document.documentElement;
  root.setAttribute("data-theme-anim", "");
  window.setTimeout(() => root.removeAttribute("data-theme-anim"), 350);
  applyTheme(choice);
}

/** En mode « auto », suit les changements du système en direct. Retourne la fonction d'arrêt. */
export function watchSystemTheme(): () => void {
  let query: MediaQueryList;
  try {
    query = window.matchMedia("(prefers-color-scheme: dark)");
  } catch {
    return () => undefined;
  }
  const onChange = () => {
    if (readTheme() === "auto") applyTheme("auto");
  };
  query.addEventListener?.("change", onChange);
  return () => query.removeEventListener?.("change", onChange);
}
