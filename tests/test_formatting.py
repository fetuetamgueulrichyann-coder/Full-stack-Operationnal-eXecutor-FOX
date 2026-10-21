"""Nettoyage commun des réponses : LaTeX en notation lisible, sans changer le sens."""
import pytest


@pytest.fixture
def clean():
    import formatting

    return formatting.clean_reply


@pytest.mark.parametrize(
    "brut, attendu",
    [
        (r"La valeur est \sqrt{\pi} environ.", "La valeur est √π environ."),
        (r"\sqrt{a+b}", "√(a+b)"),
        (r"\sqrt[3]{27}", "∛27"),
        (r"\sqrt[4]{x}", "x^(1/4)"),
        (r"\frac{1}{2}", "1/2"),
        (r"\frac{a+b}{c}", "(a+b)/c"),
        (r"\frac{\pi}{2}", "π/2"),
        (r"\frac{n(n+1)}{2}", "n(n+1)/2"),
        (r"\dfrac{x-1}{2}", "(x-1)/2"),
        (r"\binom{n}{k}", "C(n, k)"),
        (r"x^2 + y^2 = r^2", "x² + y² = r²"),
        (r"2^10 = 1024", "2¹⁰ = 1024"),
        (r"x^{-1}", "x⁻¹"),
        (r"x^{k+1}", "xᵏ⁺¹"),
        (r"e^{i\pi}", "e^(iπ)"),
        (r"\alpha \times \beta \leq \gamma", "α × β ≤ γ"),
        (r"a \neq b \approx c \geq d", "a ≠ b ≈ c ≥ d"),
        (r"x \in \mathbb{R}", "x ∈ ℝ"),
        (r"\infty \pm 1", "∞ ± 1"),
        (r"\text{aire} = \pi r^2", "aire = π r²"),
        (r"\left( \frac{a}{b} \right)^2", "( a/b )²"),
        (r"5 \% de 20", "5 % de 20"),
    ],
)
def test_conversion_latex_brut(clean, brut, attendu):
    assert clean(brut) == attendu


@pytest.mark.parametrize(
    "brut, attendu",
    [
        (r"Soit $x^2 + y^2 = r^2$.", "Soit x² + y² = r²."),
        (r"L'aire vaut \(\pi r^2\).", "L'aire vaut π r²."),
        (r"$\frac{a+b}{c}$", "(a+b)/c"),
        (r"$$E = mc^2$$", "E = mc²"),
        (r"\[ E = mc^2 \]", "E = mc²"),
        (r"$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$", "∑ᵢ₌₁ⁿ i = n(n+1)/2"),
        (r"$a_{ij}$", "aᵢⱼ"),
        (r"$a_{xy}$", "a_(xy)"),  # « y » n'existe pas en indice Unicode
        (r"$x_1 + x_2$", "x₁ + x₂"),
    ],
)
def test_formules_delimitees(clean, brut, attendu):
    assert clean(brut) == attendu


@pytest.mark.parametrize(
    "texte",
    [
        "Il coûte $5 et l'autre $6, total $11.",
        "Entre 10$ et 20$ selon l'offre",
        "Prix : $50 (hors taxes) ou $60 (toutes taxes)",
        "Écris snake_case_name et my_var_1 sans toucher",
        "Le fichier D:\\times\\fichier.txt",
        "Partage réseau \\\\serveur\\partage",
        "Une commande inconnue \\inconnu{abc} reste telle quelle",
        r"Expression inconnue \foo{\bar} conservée",
        "Version 2.0, a^q sans exposant Unicode",
        "Rien de spécial ici.",
    ],
)
def test_ce_qui_n_est_pas_du_latex_reste_intact(clean, texte):
    assert clean(texte) == texte


def test_le_code_n_est_jamais_modifie(clean):
    texte = "Dans `x^2 + \\sqrt{y}` et :\n```python\nprint(r'\\frac{1}{2}')\nx = a^2\n```\nfin $x^2$"
    assert clean(texte) == texte.replace("$x^2$", "x²")


def test_blocs_de_code_non_fermes_ne_provoquent_pas_d_erreur(clean):
    assert clean("```\nx^2 sans fermeture") == "```\nx^2 sans fermeture"


def test_espaces_normalises(clean):
    assert clean("a   \n\n\n\n\nb \r\n c  \n") == "a\n\nb\n c"
    assert clean("   \n  ") == ""


def test_texte_vide_ou_absent(clean):
    assert clean("") == ""
    assert clean(None) is None


@pytest.mark.parametrize(
    "texte",
    [
        r"\sqrt{\pi} et $x^2$ et \frac{1}{2}",
        r"$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$",
        r"e^{i\pi} + 1 = 0, \lim_{x \to 0} \frac{\sin x}{x} = 1",
        "Texte **gras** et `code` et $5 et $6",
        r"\[ \int_0^1 x^2 dx = \frac{1}{3} \]",
    ],
)
def test_nettoyer_deux_fois_donne_le_meme_resultat(clean, texte):
    once = clean(texte)
    assert clean(once) == once


def test_pas_de_boucle_infinie_sur_une_entree_pathologique(clean):
    texte = "\\frac{" * 200 + "}" * 200 + "\\sqrt{" * 100
    assert isinstance(clean(texte), str)
    assert isinstance(clean("^" * 5000 + "_" * 5000), str)


def test_le_markdown_est_conserve(clean):
    texte = "# Titre\n\n- **un** point\n- *deux*\n\n[lien](https://exemple.com)"
    assert clean(texte) == texte


def test_la_conversion_garde_les_chiffres_exacts(clean):
    assert clean(r"\frac{22}{7} \approx 3.14159, 10^{6} = 1000000") == "22/7 ≈ 3.14159, 10⁶ = 1000000"


def test_interrupteur_de_desactivation(monkeypatch):
    import formatting

    monkeypatch.setattr(formatting, "FORMAT_REPLIES", False)
    texte = "  \\sqrt{\\pi}   \n\n\n\n x^2  "
    assert formatting.clean_reply(texte) == texte
