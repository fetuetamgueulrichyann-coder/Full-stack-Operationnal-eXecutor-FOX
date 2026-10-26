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
        "1. Payer la facture (en retard · 20/10, priorité haute)\n"
        "2. Appeler Paul (pour le 25/10 09:30)\n"
        "3. Ranger (priorité basse, en cours)\n"
        "4. Simple"
    )


def test_liste_tronquee_a_dix_taches():
    tasks = [task(i, f"t{i}") for i in range(1, 14)]
    text = botlogic.tasks_message(tasks)
    assert "10. t10" in text and "11. t11" not in text and "… et 3 autre(s)." in text and text.startswith("**Tes tâches (13)**")
    assert len(botlogic.task_buttons(tasks)) == 10


def test_boutons_des_taches():
    # Le libellé est la position dans la liste ; le bouton garde l'identifiant réel de la tâche.
    assert botlogic.task_buttons([task(12, "x"), task(40, "y")]) == [
        [("Terminer 1", "tdone:12"), ("Annuler 1", "tcancel:12")],
        [("Terminer 2", "tdone:40"), ("Annuler 2", "tcancel:40")],
    ]
    assert botlogic.task_buttons([]) == []


def test_la_liste_se_rend_en_html():
    rendered, _ = botlogic.telegram_messages(botlogic.tasks_message([task(1, "a <b> & c")]))[0]
    assert "1. a &lt;b&gt; &amp; c" in rendered


@pytest.mark.parametrize("iso, expected", [("2026-10-25T23:59+01:00", "25/10"), ("2026-10-25T09:05+01:00", "25/10 09:05"), (None, "")])
def test_libelle_d_echeance(iso, expected):
    assert botlogic.due_label(iso) == expected


def test_numerotation_repart_de_un_quand_les_anciennes_taches_sont_terminees():
    assert botlogic.tasks_message([task(57, "Nouvelle")]).splitlines()[1] == "1. Nouvelle"


# --- Rappels, mémoire, journal (phase B2) --------------------------------------------
def test_message_de_rappel():
    text, rows = botlogic.reminder_message({"id": 7, "title": "Appeler Paul", "due_at": "2026-10-22T11:25+01:00"})
    assert text == "**Rappel**\nAppeler Paul (prévu à 11:25)"
    assert rows == [[("Terminer", "tdone:7")]]
    assert botlogic.reminder_message({"id": 7, "title": "x", "due_at": None})[0] == "**Rappel**\nx"


def memo(id_, content):
    return {"id": id_, "content": content}


def test_memoire_vide():
    assert "/retiens" in botlogic.memory_message([])
    assert botlogic.memory_buttons([]) == []


def test_liste_de_memoire_et_boutons():
    items = [memo(31, "Je préfère les réunions le matin"), memo(12, "Projet : cabinet Douala")]
    assert botlogic.memory_message(items) == (
        "**Ce que je retiens (2)**\n1. Je préfère les réunions le matin\n2. Projet : cabinet Douala"
    )
    assert botlogic.memory_buttons(items) == [[("Oublier 1", "mdel:31")], [("Oublier 2", "mdel:12")], [("Tout effacer", "mall")]]


def test_memoire_tronquee_a_dix_elements():
    items = [memo(i, f"m{i}") for i in range(1, 15)]
    text = botlogic.memory_message(items)
    assert "10. m10" in text and "11. m11" not in text and "… et 4 autre(s) plus anciennes." in text
    assert len(botlogic.memory_buttons(items)) == 11  # 10 éléments + « Tout effacer »


def test_journal():
    assert "vide" in botlogic.journal_message([])
    text = botlogic.journal_message([
        {"at": "2026-10-22T11:25+01:00", "text": "Exécutée : Créer la tâche « Appeler Paul »"},
        {"at": "2026-10-21T09:05+01:00", "text": "Tâche ajoutée"},
    ])
    assert text == (
        "**Journal des actions (2 dernières)**\n"
        "- 22/10 11:25 · Exécutée : Créer la tâche « Appeler Paul »\n"
        "- 21/10 09:05 · Tâche ajoutée"
    )


# --- Briefing du matin (phase B3) ----------------------------------------------------
@pytest.mark.parametrize("args, expected", [
    ([], ("status",)),
    (["maintenant"], ("preview",)), (["Now"], ("preview",)), (["voir"], ("preview",)),
    (["on"], ("set", {"enabled": True})), (["OUI"], ("set", {"enabled": True})), (["activer"], ("set", {"enabled": True})),
    (["off"], ("set", {"enabled": False})), (["désactiver"], ("set", {"enabled": False})), (["stop"], ("set", {"enabled": False})),
    (["07:30"], ("set", {"enabled": True, "time": "07:30"})), (["7h30"], ("set", {"enabled": True, "time": "7h30"})),
    (["8h"], ("set", {"enabled": True, "time": "8h"})), (["7", "h", "30"], ("set", {"enabled": True, "time": "7 h 30"})),
    (["demain"], ("invalid",)), (["on", "off"], ("invalid",)), (["25:61:00"], ("invalid",)), (["8"], ("invalid",)),
])
def test_commande_briefing(args, expected):
    assert botlogic.briefing_command(args) == expected


def test_etat_du_briefing():
    on = botlogic.briefing_status({"enabled": True, "time": "07:30"})
    assert on.startswith("Briefing du matin activé à 07:30.") and "/briefing off" in on and "/briefing maintenant" in on
    off = botlogic.briefing_status({"enabled": False, "time": "08:00"})
    assert off.startswith("Briefing du matin désactivé.") and "(08:00 par défaut)" in off and "/briefing on" in off
