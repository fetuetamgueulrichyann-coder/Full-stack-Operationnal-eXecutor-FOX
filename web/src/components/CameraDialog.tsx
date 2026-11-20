import { useEffect, useRef, useState } from "react";
import { photoName } from "../lib/attachments";
import { Icon } from "./Icon";

/** Webcam d'un ordinateur : aperçu en direct, « Prendre la photo », la photo part dans le message. */
export function CameraDialog({ onCapture, onClose }: { onCapture: (file: File) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement | null>(null);
  const [problem, setProblem] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let cancelled = false;
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then((s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        stream = s;
        if (video.current) {
          video.current.srcObject = s;
          void video.current.play?.().catch(() => undefined);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const name = error instanceof DOMException ? error.name : "";
        setProblem(
          name === "NotAllowedError"
            ? "L'accès à la caméra est refusé. Autorisez-le dans les réglages du navigateur, puis réessayez."
            : name === "NotFoundError"
              ? "Aucune caméra n'a été trouvée sur cet appareil."
              : "La caméra n'est pas disponible pour le moment.",
        );
      });
    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function capture() {
    const el = video.current;
    if (!el || !el.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = el.videoWidth;
    canvas.height = el.videoHeight;
    canvas.getContext("2d")?.drawImage(el, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setProblem("La photo n'a pas pu être prise. Réessayez.");
          return;
        }
        onCapture(new File([blob], photoName(), { type: "image/jpeg", lastModified: Date.now() }));
      },
      "image/jpeg",
      0.9,
    );
  }

  return (
    <div className="camera-overlay" role="dialog" aria-modal="true" aria-label="Prendre une photo">
      <div className="camera-box">
        {problem ? (
          <p className="banner error" role="alert">{problem}</p>
        ) : (
          <video ref={video} playsInline muted autoPlay onLoadedMetadata={() => setReady(true)} aria-label="Aperçu de la caméra" />
        )}
        <div className="camera-actions">
          {!problem && (
            <button type="button" className="primary" disabled={!ready} onClick={capture}>
              <Icon name="camera" /> Prendre la photo
            </button>
          )}
          <button type="button" className="secondary-btn" onClick={onClose}>
            {problem ? "Fermer" : "Annuler"}
          </button>
        </div>
      </div>
    </div>
  );
}
