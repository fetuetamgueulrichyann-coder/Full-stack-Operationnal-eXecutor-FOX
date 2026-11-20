import { useState } from "react";
import { Icon, type IconName } from "./Icon";
import { readTheme, resolveTheme, setTheme, type ThemeChoice } from "../lib/theme";

const OPTIONS: { value: ThemeChoice; label: string; icon: IconName; hint: string }[] = [
  { value: "light", label: "Clair", icon: "sun", hint: "Fond clair, comme sur papier." },
  { value: "dark", label: "Sombre", icon: "moon", hint: "Fond sombre, plus doux le soir." },
  { value: "auto", label: "Auto", icon: "contrast", hint: "Suit le réglage de votre appareil." },
];

/** Choix du thème (clair, sombre, auto). Mémorisé sur cet appareil et appliqué tout de suite. */
export function AppearanceCard() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  const pick = (value: ThemeChoice) => {
    setChoice(value);
    setTheme(value);
  };
  const shown = resolveTheme(choice);
  return (
    <section className="card" aria-label="Apparence">
      <h2>Apparence</h2>
      <p className="sub">
        Choisissez le thème de FOX. Ce réglage est gardé sur cet appareil.
        {choice === "auto" ? ` Thème actuel : ${shown === "dark" ? "sombre" : "clair"}.` : ""}
      </p>
      <div className="theme-options" role="radiogroup" aria-label="Thème">
        {OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={choice === o.value}
            className={`theme-option${choice === o.value ? " on" : ""}`}
            onClick={() => pick(o.value)}
          >
            <span className={`theme-preview ${o.value}`} aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span className="theme-label">
              <Icon name={o.icon} size={18} />
              {o.label}
            </span>
            <span className="theme-hint">{o.hint}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
