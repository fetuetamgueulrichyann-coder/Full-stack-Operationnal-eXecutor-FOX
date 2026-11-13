/**
 * Confirmation à voix haute : « oui », « annule »… (français et anglais).
 * Prudent par construction : la phrase entière doit être une réponse claire. « Oui mais plutôt à 15 h »
 * n'est ni un oui ni un non : elle repart comme un message normal, jamais comme une confirmation.
 */
export type Verdict = "yes" | "no" | null;

const YES = new Set([
  "oui", "ouais", "ok", "okay", "daccord", "d accord", "confirme", "confirmer", "je confirme", "c est bon", "cest bon",
  "vas y", "vasy", "allez y", "bien sur", "parfait", "tout a fait", "exactement", "valide", "je valide", "fais le", "faites le",
  "yes", "yeah", "yep", "sure", "go ahead", "do it", "confirm", "i confirm", "that s right", "thats right", "correct", "sounds good",
]);
const NO = new Set([
  "non", "annule", "annuler", "j annule", "jannule", "laisse tomber", "laissez tomber", "stop", "arrete", "arretez",
  "pas maintenant", "surtout pas", "ne le fais pas", "oublie", "oublie ca", "oubliez ca",
  "no", "nope", "cancel", "never mind", "nevermind", "don t", "dont", "do not", "forget it", "stop it",
]);
const FILLERS = new Set(["fox", "s", "il", "te", "vous", "plait", "stp", "svp", "merci", "please", "thanks", "thank", "you", "alors", "donc"]);

function normalize(text: string): string[] {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(" ")
    .filter((word) => word && !FILLERS.has(word));
}

function parseSegment(text: string): Verdict {
  const words = normalize(text);
  if (words.length === 0 || words.length > 4) return null;
  const phrase = words.join(" ");
  if (YES.has(phrase)) return "yes";
  if (NO.has(phrase)) return "no";
  return null;
}

export function parseConfirmation(text: string): Verdict {
  // « Oui, confirme » : plusieurs morceaux, tous du même avis. Un seul morceau douteux et ce n'est pas une confirmation.
  const verdicts = text.split(/[,;.!?…]+/).filter((part) => normalize(part).length > 0).map(parseSegment);
  if (verdicts.length === 0 || verdicts.length > 3 || verdicts.includes(null)) return null;
  return verdicts.every((v) => v === verdicts[0]) ? verdicts[0] : null;
}
