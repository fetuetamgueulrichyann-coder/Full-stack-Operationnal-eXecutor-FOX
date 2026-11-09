import { useCallback, useEffect, useRef, useState } from "react";

interface RecognitionEvent {
  results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }>;
}
interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type RecognitionCtor = new () => Recognition;

function ctor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const dictationSupported = () => typeof window !== "undefined" && ctor() !== null;

const MESSAGES: Record<string, string> = {
  "not-allowed": "Le micro est bloqué : autorisez-le dans les réglages du navigateur.",
  "service-not-allowed": "Le micro est bloqué : autorisez-le dans les réglages du navigateur.",
  "no-speech": "Je n'ai rien entendu. Réessayez.",
  "audio-capture": "Aucun micro détecté.",
  network: "La dictée a besoin d'une connexion internet.",
};

/** Dictée vocale (français). `onText` reçoit le texte reconnu ; le message n'est jamais envoyé tout seul. */
export function useDictation(onText: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const rec = useRef<Recognition | null>(null);
  const cb = useRef(onText);
  cb.current = onText;

  useEffect(() => () => rec.current?.stop(), []);

  const stop = useCallback(() => rec.current?.stop(), []);

  const start = useCallback(() => {
    const Ctor = ctor();
    if (!Ctor) return;
    setProblem(null);
    const r = new Ctor();
    r.lang = "fr-FR";
    r.interimResults = false;
    r.continuous = false;
    r.onresult = (e) => {
      let out = "";
      for (let i = 0; i < e.results.length; i++) out += e.results[i][0].transcript;
      const clean = out.trim();
      if (clean) cb.current(clean);
    };
    r.onerror = (e) => setProblem(MESSAGES[e.error ?? ""] ?? "La dictée a échoué. Réessayez.");
    r.onend = () => {
      setListening(false);
      rec.current = null;
    };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      setProblem("La dictée a échoué. Réessayez.");
    }
  }, []);

  return { listening, problem, start, stop, supported: dictationSupported() };
}
