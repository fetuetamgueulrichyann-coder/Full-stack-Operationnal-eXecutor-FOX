import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, getCsrfToken } from "../api/client";

export type VoiceState = "idle" | "loading" | "playing";

const MESSAGES: Record<string, string> = {
  voice_quota: "Le plafond de voix du jour est atteint. Vous pouvez toujours lire les réponses.",
  voice_disabled: "La voix est désactivée sur ce serveur.",
  voice_unavailable: "La voix est indisponible pour le moment. Réessayez dans un instant.",
};

/** Demande le son d'un texte au serveur (clé Gemini côté serveur) et le renvoie sous forme de Blob. */
export async function fetchSpeech(text: string, signal?: AbortSignal): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/voice/speak`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
      body: JSON.stringify({ text }),
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
  return response.blob();
}

/** Lecture à voix haute des réponses de FOX. `play` rend `true` si la lecture est allée jusqu'au bout. */
export function useVoice() {
  const [state, setState] = useState<VoiceState>("idle");
  const [problem, setProblem] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const controller = useRef<AbortController | null>(null);
  const finish = useRef<((completed: boolean) => void) | null>(null);
  const url = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
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
    const done = finish.current;
    finish.current = null;
    done?.(false);
    setState("idle");
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const play = useCallback(
    async (text: string): Promise<boolean> => {
      cleanup();
      setProblem(null);
      setState("loading");
      const ctl = new AbortController();
      controller.current = ctl;
      try {
        const blob = await fetchSpeech(text, ctl.signal);
        if (ctl.signal.aborted) return false;
        const src = URL.createObjectURL(blob);
        url.current = src;
        const el = new Audio(src);
        audio.current = el;
        return await new Promise<boolean>((resolve) => {
          finish.current = resolve;
          el.onended = () => {
            finish.current = null;
            cleanup();
            resolve(true);
          };
          el.onerror = () => {
            setProblem("Le son n'a pas pu être lu.");
            cleanup();
          };
          setState("playing");
          el.play().catch(() => {
            setProblem("Le navigateur a bloqué la lecture. Appuyez sur « Écouter ».");
            cleanup();
          });
        });
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setProblem(error instanceof Error ? error.message : "La voix a échoué. Réessayez.");
          setState("idle");
        }
        return false;
      }
    },
    [cleanup],
  );

  return { state, problem, play, stop: cleanup };
}
