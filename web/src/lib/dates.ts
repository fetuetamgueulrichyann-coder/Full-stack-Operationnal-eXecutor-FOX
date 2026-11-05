const DAYS = ["dim", "lun", "mar", "mer", "jeu", "ven", "sam"];

/** « 2026-11-04T16:15+01:00 » -> « 2026-11-04 » (le serveur écrit déjà l'heure dans le fuseau de l'utilisateur). */
export const dayKey = (iso: string): string => iso.slice(0, 10);
export const timeOf = (iso: string): string => iso.slice(11, 16);

/** « 2026-11-04 » -> « mer 04/11 » */
export function dayLabel(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  const weekday = DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${weekday} ${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}`;
}

export const shortDate = (key: string): string => `${key.slice(8, 10)}/${key.slice(5, 7)}`;

/** Échéance : la date seule quand l'heure est 23:59 (échéance « dans la journée »). */
export function dueLabel(iso: string): string {
  const time = timeOf(iso);
  return time === "23:59" ? shortDate(dayKey(iso)) : `${shortDate(dayKey(iso))} à ${time}`;
}
