import { useEffect, useState } from "react";
import sleepingFox from "../assets/fox-sleeping.jpg";

const SLOW_AFTER_MS = 2000;

interface Props {
  /** Message d'échec : affiche « Réessayer » à la place de l'attente. */
  error?: string;
  onRetry?: () => void;
}

/** Écran de chargement : le renard dort. Après 2 secondes, « connexion lente » ; en cas d'échec, « Réessayer ». */
export function BootScreen({ error, onRetry }: Props) {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    if (error) return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [error]);

  const text = error ? "Échec de la connexion." : slow ? "Connexion lente, veuillez patienter..." : "Chargement de Fox...";
  const tone = error ? "boot-error" : slow ? "boot-slow" : "";

  return (
    <div className="boot" role="status" aria-live="polite">
      <div className="boot-glow" />
      <img className="boot-fox" src={sleepingFox} alt="" />
      <p className={`boot-text ${tone}`}>{text}</p>
      {error && (
        <>
          <p className="boot-detail">{error}</p>
          <button type="button" className="boot-retry" onClick={onRetry}>
            Réessayer
          </button>
        </>
      )}
    </div>
  );
}
