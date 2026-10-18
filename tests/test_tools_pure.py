"""Utilitaires d'outils : dates, descriptions, registre (aucun appel à Google)."""
from datetime import datetime

import pytest


def test_les_outils_google_ne_sont_pas_enregistres_sans_identifiants(tools):
    for name in ("lire_agenda", "creer_evenement", "modifier_evenement", "supprimer_evenement"):
        assert name not in tools.REGISTRY


def test_outil_sensible_exige_description_et_confirmation(tools):
    sensible = tools.Tool(
        name="x", description="x", parameters=None, classification=tools.WRITE, run=lambda: 1
    )
    with pytest.raises(ValueError):
        tools.register(sensible)


def test_classification_et_confirmation(tools):
    lecture = tools.Tool(
        name="l", description="", parameters=None, classification=tools.READ, run=lambda: 1
    )
    assert lecture.needs_confirmation is False
    ecriture = tools.Tool(
        name="e", description="", parameters=None, classification=tools.IRREVERSIBLE, run=lambda: 1
    )
    assert ecriture.needs_confirmation is True


def test_parse_dt_ajoute_le_fuseau(tools):
    dt = tools.parse_dt("2026-10-12T09:00")
    assert dt.tzinfo is not None and dt.hour == 9


def test_parse_dt_fin_de_journee(tools):
    dt = tools.parse_dt("2026-10-12", end_of_day=True)
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)


def test_duree_par_defaut_une_heure(tools):
    start, end = tools.bounds("2026-10-12T09:00")
    assert (end - start).total_seconds() == 3600


def test_fin_avant_debut_refusee(tools):
    with pytest.raises(ValueError):
        tools.bounds("2026-10-12T10:00", "2026-10-12T09:00")


def test_date_invalide(tools):
    with pytest.raises(ValueError):
        tools.parse_dt("pas une date")


def test_description_de_creation(tools):
    text = tools.describe_creer({"titre": "Réunion", "debut": "2026-10-12T09:00", "lieu": "Douala"})
    assert "Réunion" in text and "12/10/2026" in text and "09:00" in text and "Douala" in text


def test_modification_sans_changement_refusee(tools):
    with pytest.raises(ValueError):
        tools.describe_modifier({"event_id": "x"})


def test_champ_horaire(tools):
    dt = datetime(2026, 10, 12, 9, 0, tzinfo=tools.TZ)
    assert tools.time_field(dt)["timeZone"] == tools.TZ_NAME
