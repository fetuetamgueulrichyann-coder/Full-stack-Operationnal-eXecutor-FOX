import { useId, useState, type ReactNode } from "react";
import type { PrivacySettings } from "../api/types";
import { FALLBACK_NOTICE, longDate, type SaveField } from "../lib/privacy";
import { Icon } from "./Icon";

/** Bandeau permanent : visible tant que les niveaux ne sont pas appliqués. Jamais fermable. */
export function EnforcementNotice({ data }: { data: PrivacySettings | null }) {
  if (data?.enforcement_active === true) return null;
  return (
    <div className="pv-notice" role="note" aria-label="État des protections">
      <span className="pv-notice-icon"><Icon name="info" size={20} /></span>
      <div>
        <strong>Protections pas encore appliquées</strong>
        <p>{data?.enforcement_notice ?? FALLBACK_NOTICE}</p>
      </div>
    </div>
  );
}

/** Retour visuel d'un enregistrement : en cours, puis confirmé (annoncé aux lecteurs d'écran). */
export function SaveState({ field, pending, saved }: { field: SaveField; pending: SaveField | null; saved: SaveField | null }) {
  return (
    <span className="pv-state" role="status" aria-live="polite">
      {pending === field && <><i className="pv-spinner" aria-hidden="true" />Enregistrement…</>}
      {pending !== field && saved === field && <span className="pv-saved"><Icon name="check" size={16} />Enregistré</span>}
    </span>
  );
}

export interface ChoiceOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
  badge?: string;
}

/** Groupe de choix (un seul possible) présenté en cartes. Clavier : flèches, comme un groupe de boutons radio. */
export function ChoiceGroup<T extends string>({
  label, options, value, onPick, disabled,
}: { label: string; options: ChoiceOption<T>[]; value: T | null; onPick: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="pv-choices" role="radiogroup" aria-label={label}>
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-disabled={disabled || undefined}
            className={`pv-choice${on ? " on" : ""}${disabled ? " busy" : ""}`}
            onClick={() => !on && !disabled && onPick(o.value)}
            onKeyDown={(e) => {
              // Les flèches déplacent seulement le focus : le choix se fait avec Entrée ou Espace (un enregistrement par choix).
              const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
              if (!step) return;
              e.preventDefault();
              (e.currentTarget.parentElement?.children[(i + step + options.length) % options.length] as HTMLElement | undefined)?.focus();
            }}
          >
            <span className="pv-dot" aria-hidden="true" />
            <span className="pv-choice-text">
              <span className="pv-choice-title">{o.label}{o.badge && <em className="pv-badge">{o.badge}</em>}</span>
              {o.hint && <span className="pv-choice-hint">{o.hint}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Panneau repliable : s'ouvre et se ferme en douceur (sans animation si le système demande moins de mouvement). */
export function Disclosure({ title, children, defaultOpen = false }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={`pv-disclosure${open ? " open" : ""}`}>
      <button type="button" className="pv-disclosure-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <span>{title}</span>
        <Icon name="chevron" size={18} />
      </button>
      <div id={id} className="pv-disclosure-body" role="region" aria-label={title}>
        <div>{children}</div>
      </div>
    </div>
  );
}

/** État du consentement : version et date. Informatif : ne bloque rien et n'autorise aucun envoi. */
export function ConsentBlock({
  data, pending, saved, onRecord,
}: { data: PrivacySettings; pending: SaveField | null; saved: SaveField | null; onRecord: () => void }) {
  const c = data.consent;
  return (
    <div className="pv-consent">
      <div className="pv-consent-line">
        <span className={`pv-pill ${c.read ? "ok" : "todo"}`}>{c.read ? "Information lue" : "Pas encore lue"}</span>
        <span className="muted small-text">
          {c.read && c.at ? `Version ${c.version}, enregistrée le ${longDate(c.at)}.` : `Version ${c.current_version} de l'information.`}
        </span>
      </div>
      <p className="sub">{c.notice}</p>
      {!c.read && (
        <div className="form-actions">
          <button type="button" className="secondary compact" disabled={pending === "consent"} onClick={onRecord}>
            J'ai lu l'information
          </button>
          <SaveState field="consent" pending={pending} saved={saved} />
        </div>
      )}
      {c.read && <SaveState field="consent" pending={pending} saved={saved} />}
    </div>
  );
}
