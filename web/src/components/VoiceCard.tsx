import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { VoiceConfig } from "../api/types";
import { useVoice } from "../lib/voice";

const SAMPLE = "Bonjour, je suis FOX. Voici ma voix, à cette vitesse.";

/** Réglage de la voix de FOX : voix (Gemini), vitesse de lecture, échantillon. Rien n'est enregistré avant « Enregistrer ». */
export function VoiceCard() {
  const [config, setConfig] = useState<VoiceConfig | null>(null);
  const [voiceName, setVoiceName] = useState("");
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sample = useVoice(speed);

  useEffect(() => {
    let cancelled = false;
    api<VoiceConfig>("/voice/config")
      .then((data) => {
        if (cancelled) return;
        setConfig(data);
        setVoiceName(data.voice);
        setSpeed(data.speed);
      })
      .catch(() => undefined); // voix indisponible : la carte n'apparaît simplement pas
    return () => {
      cancelled = true;
    };
  }, []);

  if (!config || !config.enabled) return null;

  const dirty = voiceName !== config.voice || speed !== config.speed;

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body: { voice?: string; speed: number } = { speed };
      if (config?.voice_choice) body.voice = voiceName;
      const next = await api<VoiceConfig>("/voice/settings", { method: "POST", body });
      setConfig(next);
      setVoiceName(next.voice);
      setSpeed(next.speed);
      setNotice("Voix enregistrée.");
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Voix de FOX">
      <h2>Voix de FOX</h2>
      <p className="muted small-text">Choisissez la voix et la vitesse avec lesquelles FOX vous répond à voix haute.</p>
      <form className="stack" onSubmit={save}>
        <div className="f">
          <label htmlFor="voice-name">Voix</label>
          {config.voice_choice ? (
            <select id="voice-name" value={voiceName} onChange={(e) => setVoiceName(e.target.value)}>
              {config.voices.map((v) => (
                <option key={v.name} value={v.name}>{v.name} — {v.label}</option>
              ))}
            </select>
          ) : (
            <p className="muted small-text" id="voice-name">La voix est choisie par le serveur ({config.providers[0]}).</p>
          )}
        </div>
        <div className="f">
          <label htmlFor="voice-speed">Vitesse : {speed.toFixed(2).replace(/0$/, "")}×</label>
          <input id="voice-speed" type="range" min={0.7} max={1.5} step={0.05} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
        </div>
        <div className="form-actions">
          <button
            type="button"
            className="secondary compact"
            disabled={sample.state === "loading"}
            onClick={() => (sample.state === "idle" ? void sample.play(SAMPLE, config.voice_choice ? voiceName : undefined) : sample.stop())}
          >
            {sample.state === "loading" ? "Préparation…" : sample.state === "playing" ? "Arrêter l'échantillon" : "Écouter un échantillon"}
          </button>
          <button type="submit" className="primary compact" disabled={busy || !dirty}>Enregistrer</button>
        </div>
        <p className="muted small-text">Votre plafond du jour : {config.used.toLocaleString("fr-FR")} sur {config.daily_limit.toLocaleString("fr-FR")} caractères lus.</p>
        {sample.problem && <span className="banner error" role="alert">{sample.problem}</span>}
        {error && <span className="banner error" role="alert">{error}</span>}
        {notice && <span className="banner info" role="status">{notice}</span>}
      </form>
    </section>
  );
}
