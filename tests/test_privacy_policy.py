"""Politique de confidentialité (bibliothèque pure) et étiquetage calculé par le serveur."""
import pytest

import privacy


@pytest.fixture(autouse=True)
def no_block_all(monkeypatch):
    monkeypatch.delenv("PRIVACY_BLOCK_ALL", raising=False)


def test_le_niveau_par_defaut_est_protege():
    assert privacy.DEFAULT_LEVEL == privacy.PROTEGE


@pytest.mark.parametrize("category", privacy.GRANT_CATEGORIES)
def test_protege_exige_une_autorisation_pour_tout(category):
    assert privacy.decide("protege", category) == privacy.NEEDS_GRANT


@pytest.mark.parametrize("category", privacy.GRANT_CATEGORIES)
def test_strict_ne_laisse_rien_partir(category):
    assert privacy.decide("strict", category) == privacy.BLOCKED


def test_standard_ouvre_texte_et_personnel_seulement_apres_consentement():
    assert privacy.decide("standard", "texte_libre", consented=True) == privacy.ALLOWED
    assert privacy.decide("standard", "personnel", consented=True) == privacy.ALLOWED
    for category in ("metier", "fichier", "voix"):
        assert privacy.decide("standard", category, consented=True) == privacy.NEEDS_GRANT
    # sans consentement : se comporte comme Protégé
    assert privacy.decide("standard", "texte_libre", consented=False) == privacy.NEEDS_GRANT


@pytest.mark.parametrize("level", ["", None, "STRICT ", "inconnu", 3])
def test_un_niveau_inconnu_est_traite_comme_strict(level):
    assert privacy.decide(level, "texte_libre") == privacy.BLOCKED


def test_une_categorie_inconnue_est_bloquee():
    assert privacy.decide("standard", "systeme") == privacy.BLOCKED
    assert privacy.decide("standard", "n_importe_quoi") == privacy.BLOCKED


def test_interrupteur_d_urgence_ne_peut_que_restreindre(monkeypatch):
    monkeypatch.setenv("PRIVACY_BLOCK_ALL", "1")
    for level in privacy.LEVELS:
        for category in privacy.GRANT_CATEGORIES:
            assert privacy.decide(level, category) == privacy.BLOCKED
    monkeypatch.setenv("PRIVACY_BLOCK_ALL", "0")
    assert privacy.decide("standard", "texte_libre") == privacy.ALLOWED  # 0 n'ouvre ni ne ferme rien de plus


def test_aucune_decision_n_est_plus_permissive_que_standard_consenti():
    # Garde-fou de la matrice : seuls texte_libre et personnel peuvent être ALLOWED, et seulement en Standard.
    for level in privacy.LEVELS:
        for category in privacy.GRANT_CATEGORIES:
            for consented in (True, False):
                if privacy.decide(level, category, consented) == privacy.ALLOWED:
                    assert level == "standard" and consented and category in ("texte_libre", "personnel")


def test_la_duree_maximale_d_une_autorisation_de_conversation_est_12_heures():
    assert privacy.MAX_CONVERSATION_GRANT.total_seconds() == 12 * 3600
    assert privacy.MESSAGE_GRANT_TTL.total_seconds() <= 600
    assert privacy.HELD_MESSAGE_TTL.total_seconds() <= 600


# --- Synthèse vocale (catégories lues) --------------------------------------------------------------
def test_la_voix_exige_voix_et_chaque_categorie_lue():
    assert privacy.voice_categories(["texte_libre"]) == ("voix", "texte_libre")
    assert privacy.voice_categories(["texte_libre", "personnel"]) == ("voix", "texte_libre", "personnel")
    assert privacy.voice_categories(["metier", "personnel"]) == ("voix", "personnel", "metier")


def test_texte_lu_de_categorie_inconnue_exige_tout():
    assert privacy.voice_categories(None) == ("voix",) + privacy.DATA_CATEGORIES


def test_la_voix_ne_s_applique_qu_avec_toutes_ses_categories():
    needed = privacy.voice_categories(["texte_libre", "personnel"])
    missing, blocked = privacy.covered("protege", needed, grants={"voix"})
    assert missing == ("texte_libre", "personnel") and blocked == ()
    missing, _ = privacy.covered("protege", needed, grants={"voix", "texte_libre", "personnel"})
    assert missing == ()
    # une autorisation de voix seule ne couvre pas l'agenda lu
    assert privacy.covered("protege", needed, grants={"voix", "texte_libre"})[0] == ("personnel",)
    # Strict : tout est bloqué, même avec des autorisations
    assert privacy.covered("strict", needed, grants=set(needed))[1] == needed


# --- Historique ------------------------------------------------------------------------------------
def test_historique_inclus_seulement_si_toutes_les_categories_sont_couvertes():
    assert privacy.history_included("protege", ["texte_libre"], {"texte_libre"})
    assert not privacy.history_included("protege", ["texte_libre", "personnel"], {"texte_libre"})
    assert privacy.history_included("standard", ["texte_libre", "personnel"], set())
    assert not privacy.history_included("strict", ["texte_libre"], {"texte_libre"})


def test_historique_d_etiquette_inconnue_est_sensible_partout():
    assert not privacy.history_included("protege", None, {"texte_libre"})
    assert not privacy.history_included("standard", None, {"texte_libre", "personnel"})
    assert privacy.history_included("protege", None, set(privacy.DATA_CATEGORIES))


# --- Étiquetage ------------------------------------------------------------------------------------
def test_sans_boite_rien_n_est_etiquete():
    privacy.mark("personnel")  # ne fait rien
    assert privacy.collect() == (None, None)


def test_la_boite_calcule_les_categories_reellement_utilisees():
    tokens = privacy.begin()
    try:
        assert privacy.collect() == (["texte_libre"], None)
        privacy.mark("metier")
        privacy.mark("personnel")
        privacy.mark("personnel")
        privacy.mark("systeme")  # n'expose aucune donnée : pas d'étiquette
        privacy.note_engine("groq")
        assert privacy.collect() == (["texte_libre", "personnel", "metier"], "groq")
        privacy.mark("fichier")
        assert privacy.collect()[0] == ["texte_libre", "personnel", "metier", "fichier"]
    finally:
        privacy.end(tokens)
    assert privacy.collect() == (None, None)


def test_une_origine_inconnue_donne_une_etiquette_inconnue():
    tokens = privacy.begin()
    try:
        privacy.mark("personnel")
        privacy.mark_unknown()
        assert privacy.collect()[0] is None
    finally:
        privacy.end(tokens)


def test_une_categorie_inattendue_ne_se_devine_pas():
    tokens = privacy.begin()
    try:
        privacy.mark("categorie_inventee")
        assert privacy.collect()[0] is None
    finally:
        privacy.end(tokens)


def test_un_moteur_inconnu_est_ignore():
    tokens = privacy.begin()
    try:
        privacy.note_engine("openai")
        assert privacy.collect()[1] is None
    finally:
        privacy.end(tokens)


def test_les_boites_de_deux_requetes_ne_se_melangent_pas():
    import threading

    results = {}

    def work(name, category):
        tokens = privacy.begin()
        try:
            privacy.mark(category)
            threading.Event().wait(0.05)
            results[name] = privacy.collect()[0]
        finally:
            privacy.end(tokens)

    threads = [threading.Thread(target=work, args=("a", "personnel")), threading.Thread(target=work, args=("b", "metier"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == {"a": ["texte_libre", "personnel"], "b": ["texte_libre", "metier"]}
