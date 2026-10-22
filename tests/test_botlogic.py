"""Logique du bot sans Telegram : boutons, découpage des messages, inconnus."""
import botlogic
import pytest


def test_boutons_d_historique():
    chats = [
        {"id": 12, "title": "Devis maçonnerie", "updated_at": "2026-10-19T14:20:00+00:00"},
        {"id": 7, "title": None, "updated_at": "2026-10-02T09:00:00+00:00"},
        {"id": 3, "title": "x" * 80, "updated_at": "2026-09-30T09:00:00+00:00"},
    ]
    buttons = botlogic.history_buttons(chats, current_id=12)
    assert buttons[0] == ("● Devis maçonnerie · 19/10", "open:12")
    assert buttons[1] == ("Sans titre · 02/10", "open:7")
    assert buttons[2][1] == "open:3" and buttons[2][0].startswith("x" * 30)
    assert len(buttons[2][0]) < 50  # un libellé de bouton reste lisible


def test_pas_plus_de_boutons_que_la_limite():
    chats = [{"id": i, "title": f"c{i}", "updated_at": "2026-10-19T00:00:00"} for i in range(50)]
    assert len(botlogic.history_buttons(chats)) == botlogic.MAX_HISTORY_BUTTONS


def test_donnees_de_bouton_courtes_pour_telegram():
    # Telegram limite callback_data à 64 octets.
    button = botlogic.history_buttons([{"id": 10**17, "title": "t", "updated_at": "2026-10-19"}])[0]
    assert len(button[1].encode()) <= 64


def test_code_de_start():
    assert botlogic.start_code([]) is None
    assert botlogic.start_code(None) is None
    assert botlogic.start_code(["FOX-ABCD-EFGH-JKLM"]) == "FOX-ABCD-EFGH-JKLM"
    assert botlogic.start_code(["FOX", "ABCD"]) == "FOX ABCD"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_inconnu_silencieux_pendant_un_moment_puis_de_nouveau_verifie():
    clock = Clock()
    unknown = botlogic.UnknownUsers(quiet=600, clock=clock)
    assert not unknown.is_quiet(5)
    unknown.mark(5)
    assert unknown.is_quiet(5) and not unknown.is_quiet(6)
    clock.now += 599
    assert unknown.is_quiet(5)
    clock.now += 2
    assert not unknown.is_quiet(5)


def test_inconnu_oublie_apres_inscription():
    unknown = botlogic.UnknownUsers()
    unknown.mark(5)
    unknown.forget(5)
    assert not unknown.is_quiet(5)
    unknown.forget(5)  # sans effet ni erreur


def test_memoire_des_inconnus_bornee():
    clock = Clock()
    unknown = botlogic.UnknownUsers(max_entries=10, clock=clock)
    for i in range(500):
        unknown.mark(i)
    assert len(unknown._until) <= 10


# --- Tâches (phase B1) -------------------------------------------------------------
def task(id_, title, **kw):
    return {"id": id_, "title": title, "due_at": None, "priority": "normale", "status": "a_faire", "overdue": False, **kw}


def test_aucune_tache():
    assert "Aucune tâche" in botlogic.tasks_message([]) and "/tache" in botlogic.tasks_message([])


def test_liste_de_taches_avec_details():
    text = botlogic.tasks_message([
        task(3, "Payer la facture", due_at="2026-10-20T23:59+01:00", overdue=True, priority="haute"),
        task(5, "Appeler Paul", due_at="2026-10-25T09:30+01:00"),
        task(8, "Ranger", status="en_cours", priority="basse"),
        task(9, "Simple"),
    ])
    assert text == (
        "**Tes tâches (4)**\n"
        "- **#3** Payer la facture (en retard · 20/10, priorité haute)\n"
        "- **#5** Appeler Paul (pour le 25/10 09:30)\n"
        "- **#8** Ranger (priorité basse, en cours)\n"
        "- **#9** Simple"
    )


def test_liste_tronquee_a_dix_taches():
    tasks = [task(i, f"t{i}") for i in range(1, 14)]
    text = botlogic.tasks_message(tasks)
    assert text.count("- **#") == 10 and "… et 3 autre(s)." in text and text.startswith("**Tes tâches (13)**")
    assert len(botlogic.task_buttons(tasks)) == 10


def test_boutons_des_taches():
    assert botlogic.task_buttons([task(12, "x")]) == [[("Terminer #12", "tdone:12"), ("Annuler #12", "tcancel:12")]]
    assert botlogic.task_buttons([]) == []


def test_la_liste_se_rend_en_html():
    rendered, _ = botlogic.telegram_messages(botlogic.tasks_message([task(1, "a <b> & c")]))[0]
    assert "<b>#1</b> a &lt;b&gt; &amp; c" in rendered


@pytest.mark.parametrize("iso, expected", [("2026-10-25T23:59+01:00", "25/10"), ("2026-10-25T09:05+01:00", "25/10 09:05"), (None, "")])
def test_libelle_d_echeance(iso, expected):
    assert botlogic.due_label(iso) == expected
