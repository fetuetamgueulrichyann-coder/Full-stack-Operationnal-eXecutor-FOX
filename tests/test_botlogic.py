"""Logique du bot sans Telegram : boutons, découpage des messages, inconnus."""
import botlogic


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
