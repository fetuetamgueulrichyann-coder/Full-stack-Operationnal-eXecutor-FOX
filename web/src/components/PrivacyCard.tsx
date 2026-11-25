import { Link } from "react-router-dom";
import type { NotificationDetail, PrivacyLevelId } from "../api/types";
import { usePrivacySettings } from "../lib/privacy";
import { ChoiceGroup, ConsentBlock, EnforcementNotice, SaveState, type ChoiceOption } from "./PrivacyParts";

const NOTIFICATION_OPTIONS: ChoiceOption<NotificationDetail>[] = [
  {
    value: "discret", label: "Discret", badge: "Par défaut",
    hint: "La notification affiche seulement « Rappel FOX ». Aucun titre, lieu ni détail n'est transmis au service de notification de votre navigateur.",
  },
  {
    value: "complet", label: "Complet",
    hint: "Le titre, le lieu et les détails s'affichent, y compris sur l'écran verrouillé. Ils voyagent chiffrés jusqu'à votre appareil.",
  },
];

/** Carte « Confidentialité » des Réglages : niveau, fournisseur préféré, notifications, information lue. */
export function PrivacyCard() {
  const { data, error, pending, saved, save, recordConsent, reload } = usePrivacySettings();
  const busy = pending !== null;

  const levelOptions: ChoiceOption<PrivacyLevelId>[] = (data?.levels ?? []).map((l) => ({
    value: l.id, label: l.label, hint: l.summary, badge: l.id === data?.default_level ? "Par défaut" : undefined,
  }));
  const providerOptions: ChoiceOption<string>[] = [
    { value: "", label: "Automatique", hint: "FOX choisit selon votre compte." },
    ...(data?.provider_choices ?? []).map((p) => ({ value: p.id, label: p.label })),
  ];

  return (
    <section className="card span pv-card" aria-label="Confidentialité" aria-busy={!data && !error}>
      <div className="row-between">
        <h2>Confidentialité</h2>
        <Link className="pv-link" to="/confidentialite">Tout savoir sur vos données</Link>
      </div>
      <EnforcementNotice data={data} />
      {error && (
        <div className="banner error" role="alert">
          {error}
          {!data && <> <button type="button" className="link-btn" onClick={() => void reload()}>Réessayer</button></>}
        </div>
      )}
      {!data && !error && <div className="pv-skeleton" aria-hidden="true"><i /><i /><i /></div>}
      {data && (
        <>
          <div className="pv-group pv-reveal" style={{ ["--i" as string]: 0 }}>
            <div className="pv-group-head">
              <h3>Niveau de protection</h3>
              <SaveState field="level" pending={pending} saved={saved} />
            </div>
            <ChoiceGroup label="Niveau de protection" options={levelOptions} value={data.level} disabled={busy} onPick={(level) => void save("level", { level })} />
          </div>

          <div className="pv-group pv-reveal" style={{ ["--i" as string]: 1 }}>
            <div className="pv-group-head">
              <h3>Fournisseur d'IA préféré</h3>
              <SaveState field="preferred_provider" pending={pending} saved={saved} />
            </div>
            <p className="sub">Votre préférence est enregistrée, mais FOX ne l'utilise pas encore : le choix reste automatique jusqu'à la phase 1c.</p>
            <ChoiceGroup
              label="Fournisseur d'IA préféré"
              options={providerOptions}
              value={data.preferred_provider ?? ""}
              disabled={busy}
              onPick={(v) => void save("preferred_provider", { preferred_provider: v === "" ? null : v })}
            />
          </div>

          <div className="pv-group pv-reveal" style={{ ["--i" as string]: 2 }}>
            <div className="pv-group-head">
              <h3>Détail des notifications</h3>
              <SaveState field="notification_detail" pending={pending} saved={saved} />
            </div>
            <ChoiceGroup
              label="Détail des notifications"
              options={NOTIFICATION_OPTIONS}
              value={data.notification_detail}
              disabled={busy}
              onPick={(notification_detail) => void save("notification_detail", { notification_detail })}
            />
            <p className="hint">Ne concerne que les notifications de ce site. Les rappels envoyés sur Telegram affichent encore le titre.</p>
          </div>

          <div className="pv-group pv-reveal" style={{ ["--i" as string]: 3 }}>
            <div className="pv-group-head"><h3>Information sur vos données</h3></div>
            <ConsentBlock data={data} pending={pending} saved={saved} onRecord={() => void recordConsent(data.consent.current_version)} />
          </div>
        </>
      )}
    </section>
  );
}
