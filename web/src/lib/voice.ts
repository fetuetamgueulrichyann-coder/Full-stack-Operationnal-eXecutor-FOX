import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, getCsrfToken } from "../api/client";

export type VoiceState = "idle" | "loading" | "playing";

const MESSAGES: Record<string, string> = {
  voice_quota: "Le plafond de voix du jour est atteint. Vous pouvez toujours lire les réponses.",
  voice_busy: "La voix est saturée pour le moment. Réessayez dans une minute.",
  voice_disabled: "La voix est désactivée sur ce serveur.",
  voice_unavailable: "La voix est indisponible pour le moment. Réessayez dans un instant.",
};

/**
 * Coupe un texte en deux morceaux au plus : la première phrase (lue vite, pendant que la suite se prépare),
 * puis le reste. Deux demandes par réponse au maximum, pour ménager le quota de la voix.
 */
export function splitSpeech(text: string): string[] {
  const t = text.trim();
  if (t.length <= 260) return t ? [t] : [];
  let cut = -1;
  const ends = /[.!?…]\s|\n/g;
  for (let m = ends.exec(t); m; m = ends.exec(t)) {
    if (m.index >= 40 && m.index <= 220) {
      cut = m.index + 1;
      break;
    }
  }
  if (cut < 0) {
    cut = t.lastIndexOf(" ", 200);
    if (cut < 40) cut = 200;
  }
  const first = t.slice(0, cut).trim();
  const rest = t.slice(cut).trim().slice(0, 900);
  return rest ? [first, rest] : [first];
}

// Les derniers sons déjà demandés : « Écouter » une seconde fois ne coûte rien.
const CACHE_SIZE = 24;
const cache = new Map<string, Blob>();

export function clearSpeechCache(): void {
  cache.clear();
}

/** Demande le son d'un texte au serveur (clé Gemini côté serveur) et le renvoie sous forme de Blob. */
export async function fetchSpeech(text: string, signal?: AbortSignal, voice?: string): Promise<Blob> {
  const key = `${voice ?? ""}|${text}`;
  const hit = cache.get(key);
  if (hit) return hit;
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/voice/speak`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
      body: JSON.stringify(voice ? { text, voice } : { text }),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new Error("Impossible de joindre FOX. Vérifiez votre connexion.");
  }
  if (!response.ok) {
    let code = "";
    try {
      code = ((await response.json()) as { code?: string }).code ?? "";
    } catch {
      code = "";
    }
    throw new Error(MESSAGES[code] ?? "La voix a échoué. Réessayez.");
  }
  const blob = await response.blob();
  cache.set(key, blob);
  if (cache.size > CACHE_SIZE) cache.delete(cache.keys().next().value as string);
  return blob;
}

// --- Volume de la voix (pour faire vibrer le logo) ---------------------------------------------
interface Graph {
  ctx: AudioContext;
  analyser: AnalyserNode;
}
let graph: Graph | null = null;

function audioGraph(): Graph | null {
  try {
    if (graph) return graph;
    const w = window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext };
    const Ctor = w.AudioContext ?? w.webkitAudioContext;
    if (!Ctor) return null;
    const ctx = new Ctor();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.connect(ctx.destination);
    graph = { ctx, analyser };
    return graph;
  } catch {
    return null;
  }
}

/** Branche la lecture sur l'analyseur ; renvoie de quoi débrancher, ou null si le navigateur ne sait pas. */
interface Tap {
  disconnect: () => void;
  read: () => { level: number; tone: number };
}

function listen(el: HTMLAudioElement): Tap | null {
  const g = audioGraph();
  if (!g) return null;
  try {
    void g.ctx.resume();
    const source = g.ctx.createMediaElementSource(el);
    source.connect(g.analyser);
    const buf = new Uint8Array(g.analyser.fftSize);
    const freq = new Uint8Array(g.analyser.frequencyBinCount);
    const hzPerBin = g.ctx.sampleRate / g.analyser.fftSize;
    return {
      disconnect: () => {
        try {
          source.disconnect();
        } catch {
          // déjà débranché
        }
      },
      read: () => {
        g.analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        // Tonalité : centre de gravité du spectre (voix grave ~ 300 Hz, voix claire et aiguë ~ 2 500 Hz), ramené à 0..1.
        g.analyser.getByteFrequencyData(freq);
        let weight = 0;
        let moment = 0;
        for (let i = 1; i < freq.length; i++) {
          weight += freq[i];
          moment += freq[i] * i * hzPerBin;
        }
        const hz = weight > 0 ? moment / weight : 0;
        return { level: Math.min(1, Math.sqrt(sum / buf.length) * 3.2), tone: Math.max(0, Math.min(1, (hz - 300) / 2200)) };
      },
    };
  } catch {
    return null;
  }
}

/**
 * Lecture à voix haute des réponses de FOX. `play` rend `true` si la lecture est allée jusqu'au bout.
 * `levelRef` (volume), `toneRef` (tonalité, grave à aiguë) et `beatRef` (impulsion sur les syllabes appuyées),
 * tous de 0 à 1 : le logo s'en sert pour vibrer comme la voix.
 */
export function useVoice(speed = 1) {
  const speedRef = useRef(speed);
  speedRef.current = speed;
  const [state, setState] = useState<VoiceState>("idle");
  const [problem, setProblem] = useState<string | null>(null);
  const levelRef = useRef(0);
  const toneRef = useRef(0.5);
  const beatRef = useRef(0);
  const audio = useRef<HTMLAudioElement | null>(null);
  const controller = useRef<AbortController | null>(null);
  const finish = useRef<((completed: boolean) => void) | null>(null);
  const url = useRef<string | null>(null);
  const tap = useRef<Tap | null>(null);
  const frame = useRef<number | null>(null);

  const stopMeter = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    tap.current?.disconnect();
    tap.current = null;
    levelRef.current = 0;
    toneRef.current = 0.5;
    beatRef.current = 0;
  }, []);

  const releaseAudio = useCallback(() => {
    stopMeter();
    if (audio.current) {
      audio.current.onended = null;
      audio.current.onerror = null;
      audio.current.pause();
      audio.current = null;
    }
    if (url.current) {
      URL.revokeObjectURL(url.current);
      url.current = null;
    }
  }, [stopMeter]);

  const cleanup = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    releaseAudio();
    const done = finish.current;
    finish.current = null;
    done?.(false);
    setState("idle");
  }, [releaseAudio]);

  useEffect(() => cleanup, [cleanup]);

  const startMeter = useCallback(
    (el: HTMLAudioElement) => {
      tap.current = listen(el);
      let smooth = 0;
      let tone = 0.5;
      const tick = (now: number) => {
        // Sans analyse (navigateur limité) : un battement doux remplace la mesure.
        const sample = tap.current ? tap.current.read() : { level: 0.35 + 0.25 * Math.sin(now / 180), tone: 0.5 + 0.25 * Math.sin(now / 700) };
        // Rythme : un sursaut brusque du volume (début de mot, syllabe appuyée) donne une brève impulsion.
        if (sample.level > smooth * 1.5 + 0.08) beatRef.current = 1;
        else beatRef.current *= 0.86;
        smooth = Math.max(sample.level, smooth * 0.82);
        tone += (sample.tone - tone) * 0.15;
        levelRef.current = smooth;
        toneRef.current = tone;
        frame.current = requestAnimationFrame(tick);
      };
      frame.current = requestAnimationFrame(tick);
    },
    [],
  );

  /** Joue un son jusqu'au bout. `false` si coupé ou en erreur. */
  const playBlob = useCallback(
    (blob: Blob) =>
      new Promise<boolean>((resolve) => {
        releaseAudio();
        const src = URL.createObjectURL(blob);
        url.current = src;
        const el = new Audio(src);
        el.playbackRate = speedRef.current; // vitesse de lecture choisie, sans changer la hauteur de la voix
        (el as HTMLAudioElement & { preservesPitch?: boolean }).preservesPitch = true;
        audio.current = el;
        finish.current = resolve;
        el.onended = () => {
          finish.current = null;
          releaseAudio();
          resolve(true);
        };
        el.onerror = () => {
          setProblem("Le son n'a pas pu être lu.");
          cleanup();
        };
        setState("playing");
        startMeter(el);
        el.play().catch(() => {
          setProblem("Le navigateur a bloqué la lecture. Appuyez sur « Écouter ».");
          cleanup();
        });
      }),
    [cleanup, releaseAudio, startMeter],
  );

  const play = useCallback(
    async (text: string, voice?: string): Promise<boolean> => {
      cleanup();
      setProblem(null);
      const chunks = splitSpeech(text);
      if (chunks.length === 0) return false;
      setState("loading");
      const ctl = new AbortController();
      controller.current = ctl;
      try {
        const first = await fetchSpeech(chunks[0], ctl.signal, voice);
        if (ctl.signal.aborted) return false;
        // La suite se prépare pendant que la première phrase est lue.
        const second = chunks[1] ? fetchSpeech(chunks[1], ctl.signal, voice) : null;
        second?.catch(() => undefined);
        if (!(await playBlob(first))) return false;
        if (second) {
          setState("loading");
          const next = await second;
          if (ctl.signal.aborted) return false;
          if (!(await playBlob(next))) return false;
        }
        setState("idle");
        return true;
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setProblem(error instanceof Error ? error.message : "La voix a échoué. Réessayez.");
          setState("idle");
        }
        return false;
      }
    },
    [cleanup, playBlob],
  );

  return { state, problem, play, stop: cleanup, levelRef, toneRef, beatRef };
}
