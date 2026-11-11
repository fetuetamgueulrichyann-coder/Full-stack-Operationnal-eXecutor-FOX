import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { BriefingSettings, Business, TimezoneInfo } from "../api/types";
import { Markdown } from "../components/Markdown";
import { NotificationsCard } from "../components/NotificationsCard";
import { useAuth } from "../auth/AuthContext";
import { timeOf } from "../lib/dates";

const ZONES = ["Europe/Paris", "Africa/Douala", "Africa/Lagos", "Africa/Abidjan", "America/Montreal", "America/New_York"];

/** Les champs du profil sont envoyés séparés par « ; » : on retire ce caractère de la saisie. */
const clean = (value: string) => value.replace(/;/g, ",").trim();

export function Settings() {
  const { state, logout } = useAuth();
  const [zone, setZone] = useState<TimezoneInfo | null>(null);
  const [zoneChoice, setZoneChoice] = useState("");
  const [briefing, setBriefing] = useState<BriefingSettings | null>(null);
  const [briefTime, setBriefTime] = useState("07:30");
  const [preview, setPreview] = useState<string | null>(null);
  const [business, setBusiness] = useState({ name: "", phone: "", email: "", address: "", tax_id: "" });
  const [hasBusiness, setHasBusiness] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [z, b, biz] = await Promise.all([
        api<TimezoneInfo>("/timezone"),
        api<BriefingSettings>("/briefing"),
        api<{ business: Business | null }>("/business"),
      ]);
      setZone(z);
      setZoneChoice(z.timezone);
      setBriefing(b);
      setBriefTime(b.time);
      if (biz.business) {
        setHasBusiness(true);
        setBusiness({
          name: biz.business.name ?? "",
          phone: biz.business.phone ?? "",
          email: biz.business.email ?? "",
          address: biz.business.address ?? "",
          tax_id: biz.business.tax_id ?? "",
        });
      }
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, message: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(message);
      await load();
    } catch (e) {
      setError(describeError(e));
    }
  }

  const saveZone = (event: FormEvent) => {
    event.preventDefault();
    void run(() => api("/timezone", { method: "POST", body: { timezone: zoneChoice } }), "Fuseau horaire enregistré.");
  };
  const saveBriefing = (enabled: boolean) =>
    run(() => api("/briefing", { method: "POST", body: { enabled, time: briefTime } }), enabled ? "Briefing activé." : "Briefing désactivé.");
  const saveBusiness = (event: FormEvent) => {
    event.preventDefault();
    const text = [business.name, business.phone, business.email, business.address, business.tax_id].map(clean).join(";");
    void run(() => api("/business", { method: "PUT", body: { text } }), "Profil d'entreprise enregistré.");
  };
  const deleteBusiness = () =>
    run(async () => {
      await api("/business", { method: "DELETE", body: {} });
      setHasBusiness(false);
      setBusiness({ name: "", phone: "", email: "", address: "", tax_id: "" });
    }, "Profil d'entreprise supprimé.");
  async function showPreview() {
    setError(null);
    try {
      setPreview((await api<{ text: string }>("/briefing/preview")).text);
    } catch (e) {
      setError(describeError(e));
    }
  }

  const zoneOptions = zone && !ZONES.includes(zone.timezone) ? [zone.timezone, ...ZONES] : ZONES;
  const setBiz = (field: keyof typeof business) => (e: { target: { value: string } }) => setBusiness({ ...business, [field]: e.target.value });
  const user = state.status === "authenticated" ? state.user : null;

  return (
    <main className="page wide-page">
      <header className="stack">
        <h1>Réglages</h1>
        <span className="muted small-text">Fuseau horaire, notifications, briefing du matin, entreprise et compte.</span>
      </header>
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner info" role="status">{notice}</div>}

      <div className="card-grid">
        <section className="card" aria-label="Fuseau horaire">
          <h2>Fuseau horaire</h2>
          <p className="sub">Les rendez-vous, les rappels et le briefing suivent votre fuseau.{zone ? ` Heure actuelle : ${timeOf(zone.now)}.` : ""}</p>
          <form className="stack" onSubmit={saveZone}>
            <div className="f">
              <label htmlFor="tz">Fuseau</label>
              <select id="tz" value={zoneChoice} onChange={(e) => setZoneChoice(e.target.value)} disabled={!zone}>
                {zoneOptions.map((z) => (
                  <option key={z} value={z}>{z}</option>
                ))}
              </select>
            </div>
            <div><button type="submit" className="primary compact" disabled={!zone}>Enregistrer</button></div>
          </form>
        </section>

        <NotificationsCard />

        <section className="card" aria-label="Briefing du matin">
          <div className="row-between">
            <h2>Briefing du matin</h2>
            <button
              type="button"
              role="switch"
              aria-checked={briefing?.enabled === true}
              aria-label="Briefing du matin"
              className={briefing?.enabled ? "switch on" : "switch"}
              disabled={!briefing}
              onClick={() => void saveBriefing(!briefing?.enabled)}
            />
          </div>
          <p className="sub">Un résumé de votre journée envoyé sur Telegram : rendez-vous et tâches à faire.</p>
          <div className="f narrow">
            <label htmlFor="hh">Heure d'envoi</label>
            <input id="hh" type="time" value={briefTime} onChange={(e) => setBriefTime(e.target.value)} />
          </div>
          <div className="form-actions">
            <button className="primary compact" disabled={!briefing} onClick={() => void saveBriefing(briefing?.enabled ?? false)}>Enregistrer l'heure</button>
            <button className="secondary compact" onClick={() => void showPreview()}>Voir l'aperçu</button>
          </div>
          {preview !== null && <div className="preview" role="group" aria-label="Aperçu du briefing"><Markdown text={preview} /></div>}
        </section>

        <section className="card span" aria-label="Profil d'entreprise">
          <h2>Profil d'entreprise</h2>
          <p className="sub">Ces informations apparaissent sur vos devis en PDF.</p>
          <form className="card-form flat" onSubmit={saveBusiness} aria-label="Profil d'entreprise">
            <div className="f">
              <label htmlFor="bn">Nom de l'entreprise</label>
              <input id="bn" required value={business.name} onChange={setBiz("name")} />
            </div>
            <div className="f">
              <label htmlFor="bp">Téléphone</label>
              <input id="bp" type="tel" value={business.phone} onChange={setBiz("phone")} />
            </div>
            <div className="f">
              <label htmlFor="be">E-mail</label>
              <input id="be" type="email" value={business.email} onChange={setBiz("email")} />
            </div>
            <div className="f">
              <label htmlFor="ba">Adresse</label>
              <input id="ba" value={business.address} onChange={setBiz("address")} />
            </div>
            <div className="f">
              <label htmlFor="bt">N° de contribuable</label>
              <input id="bt" placeholder="Facultatif" value={business.tax_id} onChange={setBiz("tax_id")} />
            </div>
            <div className="form-actions">
              <button type="submit" className="primary compact">Enregistrer</button>
              {hasBusiness && <button type="button" className="secondary compact danger" onClick={() => void deleteBusiness()}>Supprimer le profil</button>}
            </div>
          </form>
        </section>

        <section className="card span" aria-label="Compte">
          <div className="row-between">
            <div className="stack">
              <h2>Compte</h2>
              <span className="sub">
                Connecté en tant que <strong>{user?.username}</strong> · rôle {user?.role === "owner" ? "propriétaire" : "testeur"}
              </span>
            </div>
            <button className="secondary compact" onClick={() => void logout()}>Se déconnecter</button>
          </div>
        </section>
      </div>
    </main>
  );
}
