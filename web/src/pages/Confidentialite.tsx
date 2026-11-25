import { Link } from "react-router-dom";
import { usePrivacyInfo, usePrivacySettings } from "../lib/privacy";
import { ConsentBlock, Disclosure, EnforcementNotice } from "../components/PrivacyParts";
import { Icon } from "../components/Icon";

const KIND_LABEL: Record<string, string> = { llm: "Intelligence artificielle", tts: "Voix (lecture à voix haute)" };

export function Confidentialite() {
  const { data, error, pending, saved, recordConsent } = usePrivacySettings();
  const { info, error: infoError } = usePrivacyInfo();
  const providers = info?.providers ?? [];

  return (
    <main className="page wide-page pv-page">
      <header className="stack">
        <h1>Confidentialité</h1>
        <span className="muted small-text">Ce que FOX envoie à des services extérieurs, et comment vous gardez la main.</span>
      </header>

      <EnforcementNotice data={data} />
      {(error || infoError) && <div className="banner error" role="alert">{error ?? infoError}</div>}

      <section className="pv-section pv-reveal" style={{ ["--i" as string]: 0 }} aria-label="Les trois niveaux">
        <h2>Les trois niveaux</h2>
        <p className="sub">Chaque niveau décide ce qui pourra partir chez un fournisseur extérieur. Vous le choisissez dans les Réglages.</p>
        <div className="pv-levels">
          {(data?.levels ?? []).map((l) => (
            <article key={l.id} className={`pv-level${data?.level === l.id ? " current" : ""}`}>
              <div className="pv-level-head">
                <h3>{l.label}</h3>
                {data?.level === l.id && <span className="pv-pill ok">Votre niveau</span>}
                {l.id === data?.default_level && data?.level !== l.id && <span className="pv-pill">Par défaut</span>}
              </div>
              <p>{l.summary}</p>
            </article>
          ))}
        </div>
        <div><Link className="secondary compact as-link" to="/reglages">Changer de niveau dans les Réglages</Link></div>
      </section>

      <section className="pv-section pv-reveal" style={{ ["--i" as string]: 1 }} aria-label="Fournisseurs">
        <h2>Les fournisseurs</h2>
        <p className="sub">Pour répondre, FOX s'appuie sur des services extérieurs. Voici ce que nous savons de chacun, sans rien promettre de plus.</p>
        <div className="pv-providers">
          {providers.map((p) => (
            <article key={p.id} className={`pv-provider${p.available ? "" : " off"}`}>
              <div className="pv-level-head">
                <h3>{p.label}</h3>
                <span className={`pv-pill ${p.available ? "ok" : ""}`}>{p.available ? "Disponible pour votre compte" : "Non disponible pour votre compte"}</span>
              </div>
              <span className="muted small-text">{KIND_LABEL[p.kind] ?? p.kind}</span>
              <p>{p.retention}</p>
            </article>
          ))}
          {!info && !infoError && <div className="pv-skeleton" aria-hidden="true"><i /><i /></div>}
        </div>
        {info && <p className="hint">{info.declared.note}</p>}
      </section>

      <section className="pv-section pv-reveal" style={{ ["--i" as string]: 2 }} aria-label="Vos données">
        <h2>Vos données</h2>
        <Disclosure title="Quelles données peuvent être envoyées ?" defaultOpen>
          <ul className="pv-list">
            {(info?.categories ?? []).map((c) => <li key={c.id}>{c.label}</li>)}
          </ul>
        </Disclosure>
        <Disclosure title="Qu'est-ce qui reste chez FOX ?">
          <p className="sub">Votre historique, votre mémoire, votre agenda et vos devis sont stockés sur le serveur de FOX. Vous pouvez les consulter et les effacer dans <Link to="/prive">Vie privée</Link>.</p>
        </Disclosure>
        <div className="pv-tracking">
          <span className="pv-pill">Bientôt, phase 1c</span>
          <p>{info?.tracking.notice ?? "Le suivi des envois commence avec la phase 1c. Les envois ne sont pas encore tracés."}</p>
        </div>
      </section>

      <section className="pv-section pv-reveal" style={{ ["--i" as string]: 3 }} aria-label="Information lue">
        <h2>Information lue</h2>
        {data ? <ConsentBlock data={data} pending={pending} saved={saved} onRecord={() => void recordConsent(data.consent.current_version)} /> : <div className="pv-skeleton" aria-hidden="true"><i /></div>}
      </section>

      <p className="pv-foot">
        <Icon name="privacy" size={18} />
        <span>Mémoire, conversations et mode privé : <Link to="/prive">Vie privée</Link></span>
      </p>
    </main>
  );
}
