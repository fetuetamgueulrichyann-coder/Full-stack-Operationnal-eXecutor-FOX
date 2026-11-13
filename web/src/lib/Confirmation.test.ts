import { parseConfirmation } from "./confirmation";

describe("confirmation à voix haute", () => {
  it.each(["oui", "Oui.", "OUI !", "ouais", "ok", "d'accord", "D’accord", "c'est bon", "vas-y", "je confirme", "confirme", "bien sûr", "parfait",
    "oui s'il te plaît", "oui merci", "oui fox", "yes", "yeah", "sure", "go ahead", "yes please", "do it", "confirm", "oui, confirme"])(
    "« %s » confirme",
    (text) => expect(parseConfirmation(text)).toBe("yes"),
  );

  it.each(["non", "Non !", "annule", "annuler", "j'annule", "laisse tomber", "stop", "arrête", "pas maintenant", "surtout pas", "no", "nope", "cancel",
    "never mind", "non merci", "non, annule", "no thanks", "forget it"])(
    "« %s » annule",
    (text) => expect(parseConfirmation(text)).toBe("no"),
  );

  it.each([
    "oui mais plutôt à quinze heures",
    "non pas celui-là, l'autre",
    "oui et ajoute aussi un rappel",
    "peut-être",
    "je ne sais pas",
    "quel est mon agenda",
    "ok mais change l'heure du rendez-vous",
    "annule le rendez-vous de demain",
    "supprime tout",
    "",
    "   ",
    "...",
  ])("« %s » n'est ni oui ni non", (text) => expect(parseConfirmation(text)).toBeNull());

  it("« oui, non » se contredit : pas une confirmation", () => {
    expect(parseConfirmation("oui, non")).toBeNull();
    expect(parseConfirmation("oui, mais non")).toBeNull();
  });

  it("ne confond pas un mot qui contient « oui » ou « non »", () => {
    expect(parseConfirmation("nonobstant")).toBeNull();
    expect(parseConfirmation("louis")).toBeNull();
    expect(parseConfirmation("ouiiii")).toBeNull();
  });
});
