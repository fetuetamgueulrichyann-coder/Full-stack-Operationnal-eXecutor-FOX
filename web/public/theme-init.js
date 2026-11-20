// Applique le thème choisi AVANT le premier affichage (pas d'éclair blanc en mode sombre).
// Fichier externe : la politique de sécurité (CSP) n'autorise pas les scripts écrits dans la page.
(function () {
  var choice = "auto";
  try {
    var stored = window.localStorage.getItem("fox-theme");
    if (stored === "light" || stored === "dark" || stored === "auto") choice = stored;
  } catch (e) {}
  var dark = choice === "dark";
  if (choice === "auto") {
    try {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e) {}
  }
  document.documentElement.setAttribute("data-theme", choice);
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#0e1620" : "#C2410C");
})();
