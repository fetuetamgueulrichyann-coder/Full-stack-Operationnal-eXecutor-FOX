"""Rendu Telegram : Markdown vers HTML Telegram, échappement, découpage, repli."""
import re

import pytest

import botlogic as b


def html_of(markdown):
    rendered, _ = b.telegram_messages(markdown)[0]
    return rendered


# --- Mise en forme ---------------------------------------------------------------
@pytest.mark.parametrize(
    "markdown, attendu",
    [
        ("**gras**", "<b>gras</b>"),
        ("__gras__", "<b>gras</b>"),
        ("*italique*", "<i>italique</i>"),
        ("_italique_", "<i>italique</i>"),
        ("~~barré~~", "<s>barré</s>"),
        ("`code`", "<code>code</code>"),
        ("# Titre", "<b>Titre</b>"),
        ("### Titre **fort**", "<b>Titre fort</b>"),
        ("- un\n- deux", "• un\n• deux"),
        ("* un\n+ deux", "• un\n• deux"),
        ("- parent\n  - enfant", "• parent\n  • enfant"),
        ("1. un\n2) deux", "1. un\n2. deux"),
        ("> citation", "<blockquote>citation</blockquote>"),
        ("---", "──────────"),
        ("[site](https://exemple.com/a)", '<a href="https://exemple.com/a">site</a>'),
        ("a **b** et *c* et `d`", "a <b>b</b> et <i>c</i> et <code>d</code>"),
    ],
)
def test_conversion_markdown(markdown, attendu):
    assert html_of(markdown) == attendu


def test_bloc_de_code_et_tableau_en_preformate():
    assert html_of("```python\nprint('a')\n```") == "<pre>print('a')</pre>"
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert html_of(table) == f"<pre>{table}</pre>"


def test_bloc_de_code_non_ferme_est_ferme_proprement():
    assert html_of("```\nligne 1\nligne 2") == "<pre>ligne 1\nligne 2</pre>"


def test_le_markdown_dans_le_code_n_est_pas_interprete():
    assert html_of("`**pas gras**`") == "<code>**pas gras**</code>"
    assert html_of("```\n**x** *y* [a](https://b.c)\n```") == "<pre>**x** *y* [a](https://b.c)</pre>"


def test_snake_case_et_multiplications_ne_deviennent_pas_italiques():
    assert html_of("snake_case_name et my_var_1") == "snake_case_name et my_var_1"
    assert html_of("2*3*4 = 24") == "2*3*4 = 24"


# --- Sécurité ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "dangereux",
    [
        "<script>alert(1)</script>",
        "<b onclick=\"x()\">gras</b>",
        "<a href=\"javascript:alert(1)\">clic</a>",
        "<img src=x onerror=alert(1)>",
        "&lt;déjà échappé&gt; & <>",
    ],
)
def test_le_html_du_modele_est_toujours_echappe(dangereux):
    rendered = html_of(dangereux)
    # Aucune balise du modèle ne passe : seules celles que nous générons peuvent exister.
    assert set(re.findall(r"<(/?\w+)", rendered)) <= {"b", "/b", "i", "/i", "code", "/code"}
    assert "<script" not in rendered and "<img" not in rendered and "<a " not in rendered
    assert b.balanced(rendered)


def test_balises_du_modele_deviennent_du_texte_visible():
    assert html_of("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert html_of("a & b < c > d") == "a &amp; b &lt; c &gt; d"


@pytest.mark.parametrize(
    "lien",
    ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "tg://resolve?domain=x", "//evil.com"],
)
def test_liens_dangereux_jamais_cliquables(lien):
    rendered = html_of(f"[clic]({lien})")
    assert "<a " not in rendered


def test_guillemets_dans_une_adresse_ne_sortent_pas_de_l_attribut():
    rendered = html_of('[x](https://exemple.com/"onmouseover="alert(1))')
    assert rendered.count('"') == 2  # seulement les guillemets de href="…"
    assert b.balanced(rendered)


def test_esperluette_dans_l_adresse():
    assert html_of("[a](https://x.fr/?a=1&b=2)") == '<a href="https://x.fr/?a=1&amp;b=2">a</a>'


# --- Mise en forme croisée ou cassée -----------------------------------------------
@pytest.mark.parametrize(
    "markdown",
    ["**a *b** c*", "**non fermé", "*seul", "~~x **y~~ z**", "`code non fermé", "**a** **", "****"],
)
def test_une_mise_en_forme_cassee_ne_produit_jamais_de_html_invalide(markdown):
    rendered, plain = b.telegram_messages(markdown)[0]
    assert plain == markdown
    assert rendered is None or b.balanced(rendered)


def test_mise_en_forme_croisee_garde_le_texte():
    rendered = html_of("**a *b** c*")
    assert "a" in rendered and "b" in rendered and "c" in rendered


# --- Découpage ----------------------------------------------------------------------
def test_message_court_non_decoupe():
    assert len(b.telegram_messages("Bonjour !")) == 1


def test_decoupage_aux_paragraphes():
    paragraphs = [f"Paragraphe {i} " + "mot " * 200 for i in range(12)]
    parts = b.split_markdown("\n\n".join(paragraphs), limit=1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) > 1
    # chaque morceau commence et finit sur des paragraphes entiers
    assert all(p.startswith("Paragraphe") for p in parts)
    assert "\n\n".join(parts).count("Paragraphe") == 12
    assert [w for p in parts for w in p.split()] == [w for q in paragraphs for w in q.split()]


def test_un_paragraphe_trop_long_est_coupe_sans_perdre_de_texte():
    texte = ("phrase " * 1500).strip()
    parts = b.split_markdown(texte, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert " ".join(parts).split() == texte.split()


def test_texte_sans_espace_coupe_franchement():
    parts = b.split_markdown("x" * 9000, limit=4000)
    assert [len(p) for p in parts] == [4000, 4000, 1000]


def test_un_bloc_de_code_court_n_est_jamais_coupe():
    code = "```\n" + "\n".join(f"ligne {i}" for i in range(20)) + "\n```"
    parts = b.split_markdown("intro\n\n" + code + "\n\nfin", limit=300)
    assert any(code in p for p in parts)


def test_un_bloc_de_code_trop_long_est_recoupe_avec_ses_balises():
    code = "```\n" + "\n".join(f"ligne numéro {i}" for i in range(400)) + "\n```"
    messages = b.telegram_messages(code, limit=1000)
    assert len(messages) > 1
    for rendered, plain in messages:
        assert rendered.startswith("<pre>") and rendered.endswith("</pre>")
        assert len(plain) <= 1000 and b.balanced(rendered)
    body = "\n".join(p.removeprefix("```\n").removesuffix("\n```") for _, p in messages)
    assert body.count("ligne numéro") == 400


def test_chaque_message_rendu_respecte_la_limite_de_telegram():
    markdown = "\n\n".join(f"**Titre {i}**\n- " + "\n- ".join("élément <&>" * 3 for _ in range(15)) for i in range(40))
    for rendered, plain in b.telegram_messages(markdown):
        assert len(plain) <= b.TELEGRAM_LIMIT
        assert rendered is None or len(rendered) <= 4096


def test_html_trop_long_apres_echappement_bascule_en_texte_brut():
    # « < » devient « &lt; » (4 caractères) : le HTML peut dépasser 4096 alors que le texte non.
    markdown = "<" * 3990
    rendered, plain = b.telegram_messages(markdown)[0]
    assert rendered is None and plain == markdown


def test_message_vide():
    assert b.telegram_messages("") == []


# --- Actions en attente ---------------------------------------------------------------
def test_message_d_action_en_attente():
    rendered, plain = b.action_message("Créer l'événement « A <b> »\nQuand : demain\nLieu : Douala")
    assert rendered == "<b>Action proposée</b>\nCréer l'événement « A &lt;b&gt; »\nQuand : demain\nLieu : Douala"
    assert plain.startswith("Action proposée :\nCréer")
    assert b.balanced(rendered)
