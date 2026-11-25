import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { PrivacyInfo, PrivacySettings } from "../api/types";

/** Texte montré tant que le serveur n'a pas répondu : il reste vrai tant que la phase 1c n'est pas livrée. */
export const FALLBACK_NOTICE =
  "Les niveaux sont enregistrés mais pas encore appliqués. Pour l'instant, FOX envoie vos messages à son fournisseur d'IA comme avant, quel que soit le niveau. L'application arrive avec la phase 1c.";

export type SaveField = "level" | "preferred_provider" | "notification_detail" | "consent";
export type Changes = Partial<Pick<PrivacySettings, "level" | "preferred_provider" | "notification_detail">>;

/**
 * Charge et enregistre les réglages. Rien n'est affiché comme enregistré avant la réponse du serveur :
 * en cas d'erreur, la valeur affichée reste celle que le serveur a réellement.
 */
export function usePrivacySettings() {
  const [data, setData] = useState<PrivacySettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<SaveField | null>(null);
  const [saved, setSaved] = useState<SaveField | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const load = useCallback(async () => {
    try {
      setData(await api<PrivacySettings>("/privacy/settings"));
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(async (field: SaveField, call: () => Promise<PrivacySettings>) => {
    setPending(field);
    setSaved(null);
    setError(null);
    try {
      setData(await call());
      setSaved(field);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setSaved(null), 2600);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setPending(null);
    }
  }, []);

  const save = useCallback(
    (field: Exclude<SaveField, "consent">, changes: Changes) => run(field, () => api<PrivacySettings>("/privacy/settings", { method: "POST", body: changes })),
    [run],
  );
  const recordConsent = useCallback(
    (version: number) => run("consent", () => api<PrivacySettings>("/privacy/consent", { method: "POST", body: { version } })),
    [run],
  );

  return { data, error, pending, saved, save, recordConsent, reload: load };
}

export function usePrivacyInfo() {
  const [info, setInfo] = useState<PrivacyInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api<PrivacyInfo>("/privacy/info")
      .then((r) => alive && setInfo(r))
      .catch((e) => alive && setError(describeError(e)));
    return () => { alive = false; };
  }, []);
  return { info, error };
}

export function longDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}
