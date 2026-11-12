import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import { currentEndpoint, disablePush, enablePush, pushSupported, type PushConfig } from "../lib/push";

/** Réglage des notifications : rappels de rendez-vous et de tâches même quand FOX est fermé. */
export function NotificationsCard() {
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [here, setHere] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const supported = pushSupported();
  const denied = supported && Notification.permission === "denied";

  const load = useCallback(async () => {
    try {
      setConfig(await api<PushConfig>("/push/config"));
      setHere((await currentEndpoint()) !== null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<void>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(done);
      await load();
    } catch (e) {
      setError(e instanceof Error && !("status" in e) ? e.message : describeError(e));
    } finally {
      setBusy(false);
    }
  }

  const test = async () => {
    const result = await api<{ sent: number }>("/push/test", { method: "POST", body: {} });
    if (result.sent === 0) throw new Error("Aucun appareil n'a reçu la notification.");
  };

  return (
    <section className="card" aria-label="Notifications">
      <h2>Notifications</h2>
      <p className="sub">Recevez vos rappels de rendez-vous et de tâches sur cet appareil, même quand FOX est fermé.</p>
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner info" role="status">{notice}</div>}
      {!supported && (
        <p className="muted small-text">Ce navigateur ne gère pas les notifications. Sur iPhone, ajoutez d'abord FOX à l'écran d'accueil (menu Partager), puis ouvrez-le depuis l'icône.</p>
      )}
      {supported && config && !config.enabled && (
        <p className="muted small-text">
          Les notifications ne sont pas encore configurées sur ce serveur
          {config.missing && config.missing.length > 0 ? ` : le serveur ne reçoit pas ${config.missing.join(" ni ")}. Vérifiez le fichier .env, puis relancez avec « docker compose up -d --force-recreate orchestrator push_worker ».` : "."}
        </p>
      )}
      {denied && <p className="muted small-text">Les notifications sont bloquées pour ce site : autorisez-les dans les réglages du navigateur, puis rechargez la page.</p>}
      {supported && config?.enabled && !denied && (
        <div className="form-actions">
          {here ? (
            <>
              <button className="secondary compact" disabled={busy} onClick={() => void run(test, "Notification de test envoyée.")}>Envoyer un test</button>
              <button className="secondary compact danger" disabled={busy} onClick={() => void run(disablePush, "Notifications désactivées sur cet appareil.")}>Désactiver sur cet appareil</button>
            </>
          ) : (
            <button className="primary compact" disabled={busy} onClick={() => void run(() => enablePush(config.public_key ?? ""), "Notifications activées sur cet appareil.")}>
              Activer sur cet appareil
            </button>
          )}
        </div>
      )}
      {config && config.devices > 0 && <p className="muted small-text">{config.devices} {config.devices > 1 ? "appareils reçoivent" : "appareil reçoit"} les notifications.</p>}
    </section>
  );
}
