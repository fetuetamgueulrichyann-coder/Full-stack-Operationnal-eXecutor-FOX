"""Briefing du matin (phase B3) : contenu, réglages, envoi unique par jour, routes."""
from datetime import date, datetime, time, timedelta

import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001


@pytest.fixture
def briefing(agent):
    import briefing as module

    return module


@pytest.fixture
def tasks(agent):
    import tasks as module

    return module


@pytest.fixture
def alice(make_owner):
    return make_owner(OWNER)["id"]


@pytest.fixture
def friend(db, accounts):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return cur.fetchone()[0]


@pytest.fixture
def now(tasks):
    return datetime(2026, 10, 22, 8, 0, tzinfo=tasks.TZ)  # un jeudi


@pytest.fixture
def no_agenda(tools, monkeypatch):
    monkeypatch.delitem(tools.REGISTRY, "lire_agenda", raising=False)


def add(db, tasks, user, title, due=None, priority="normale"):
    return db.create_task(user, title, priority, tasks.parse_due(due) if due else None)


# --- Réglages d'heure -------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("7:30", "07:30"), ("07:30", "07:30"), ("7h30", "07:30"), ("8h", "08:00"), ("8:", "08:00"),
    ("0:00", "00:00"), ("23h59", "23:59"), (" 9 h 15 ", "09:15"),
])
def test_heures_reconnues(briefing, text, expected):
    assert briefing.parse_time(text) == expected


@pytest.mark.parametrize("text", ["", None, "8", "24:00", "7:75", "matin", "7h300", "-1h"])
def test_heures_invalides(briefing, text):
    with pytest.raises(briefing.BriefingInputError):
        briefing.parse_time(text)


def test_par_defaut_le_briefing_est_desactive(db, alice):
    assert db.get_briefing(alice) == {"enabled": False, "time": "08:00"}


def test_activer_et_regler_l_heure(db, briefing, alice, now):
    assert briefing.configure(alice, enabled=True, now=now) == {"enabled": True, "time": "08:00"}
    assert briefing.configure(alice, time="6h45", now=now) == {"enabled": True, "time": "06:45"}
    assert briefing.configure(alice, enabled=False, now=now) == {"enabled": False, "time": "06:45"}


def test_les_reglages_sont_prives(db, briefing, alice, friend, now):
    briefing.configure(alice, enabled=True, time="7:00", now=now)
    assert db.get_briefing(friend) == {"enabled": False, "time": "08:00"}


# --- Contenu -------------------------------------------------------------------------
def test_briefing_complet(db, briefing, tasks, alice, now, no_agenda):
    add(db, tasks, alice, "Payer la facture", "2026-10-20")
    add(db, tasks, alice, "Appeler Paul", "2026-10-22 11:25")
    add(db, tasks, alice, "Ranger le bureau", "2026-10-22")
    add(db, tasks, alice, "Envoyer le devis", "2026-10-24", priority="haute")
    add(db, tasks, alice, "Bien plus tard", "2026-11-30")
    add(db, tasks, alice, "Sans échéance")
    done = add(db, tasks, alice, "Terminée", "2026-10-21")
    db.update_task(alice, done["id"], status="terminee")
    assert briefing.build(alice, "owner", now) == (
        "**Briefing du jeudi 22/10**\n\n"
        "**En retard (1)**\n- depuis le 20/10 · Payer la facture\n\n"
        "**Aujourd'hui (2)**\n- 11:25 Appeler Paul\n- Ranger le bureau\n\n"
        "**Dans les 3 prochains jours (1)**\n- 24/10 · Envoyer le devis (priorité haute)\n\n"
        "1 tâche(s) sans échéance. /taches pour les voir."
    )


def test_rien_de_prevu(db, briefing, alice, now, no_agenda):
    assert briefing.build(alice, "owner", now) == "**Briefing du jeudi 22/10**\n\nRien de prévu aujourd'hui."


def test_les_taches_lointaines_seules_ne_font_pas_un_briefing(db, briefing, tasks, alice, now, no_agenda):
    add(db, tasks, alice, "Dans deux mois", "2026-12-25")
    assert "Rien de prévu" in briefing.build(alice, "owner", now)


def test_limite_de_lignes_par_section(db, briefing, tasks, alice, now, no_agenda):
    for i in range(11):
        add(db, tasks, alice, f"tâche {i:02d}", "2026-10-22")
    text = briefing.build(alice, "owner", now)
    assert "**Aujourd'hui (11)**" in text and "tâche 07" in text and "tâche 08" not in text
    assert "… et 3 autre(s)." in text


def test_le_briefing_ne_montre_que_les_taches_de_l_utilisateur(db, briefing, tasks, alice, friend, now, no_agenda):
    add(db, tasks, alice, "Secret d'Alice", "2026-10-22")
    assert "Secret d'Alice" not in briefing.build(friend, "tester", now)


def test_nom_des_jours(briefing, db, alice, no_agenda, tasks):
    for day, name in ((date(2026, 10, 19), "lundi"), (date(2026, 10, 25), "dimanche")):
        moment = datetime(day.year, day.month, day.day, 8, 0, tzinfo=tasks.TZ)
        assert briefing.build(alice, "owner", moment).startswith(f"**Briefing du {name} {day:%d/%m}**")


def fake_agenda(tools, monkeypatch, result=None, error=None, roles=("owner",)):
    calls = []

    def run(date_debut, date_fin=None):
        calls.append(date_debut)
        if error:
            raise error
        return result

    monkeypatch.setitem(tools.REGISTRY, "lire_agenda", tools.Tool(
        name="lire_agenda", description="", parameters=None, classification=tools.READ, run=run, roles=roles))
    return calls


def test_agenda_du_proprietaire(db, briefing, tools, alice, now, monkeypatch):
    calls = fake_agenda(tools, monkeypatch, [
        {"titre": "Réunion équipe", "debut": "2026-10-22T09:00:00+01:00", "fin": "2026-10-22T10:00:00+01:00", "lieu": "Douala"},
        {"titre": "Anniversaire", "debut": "2026-10-22", "fin": "2026-10-23", "lieu": None},
    ])
    text = briefing.build(alice, "owner", now)
    assert calls == ["2026-10-22"]
    assert "**Agenda (2)**\n- 09:00-10:00 Réunion équipe (Douala)\n- toute la journée Anniversaire" in text
    assert "Rien de prévu" not in text


def test_agenda_vide_n_ajoute_rien(db, briefing, tools, alice, now, monkeypatch):
    fake_agenda(tools, monkeypatch, [])
    assert "Agenda" not in briefing.build(alice, "owner", now)


def test_agenda_en_panne_est_signale(db, briefing, tools, alice, now, monkeypatch):
    fake_agenda(tools, monkeypatch, error=RuntimeError("Google indisponible"))
    text = briefing.build(alice, "owner", now)
    assert "Agenda Google indisponible pour le moment." in text and "RuntimeError" not in text


def test_la_cause_de_la_panne_d_agenda_est_dans_les_journaux(db, briefing, tools, alice, now, monkeypatch, caplog):
    fake_agenda(tools, monkeypatch, error=RuntimeError("invalid_grant: Token has been expired or revoked."))
    with caplog.at_level("WARNING", logger="orchestrator.briefing"):
        briefing.build(alice, "owner", now)
    assert "Agenda indisponible pour le briefing : RuntimeError: invalid_grant" in caplog.text


def test_pas_d_agenda_pour_un_testeur(db, briefing, tools, friend, now, monkeypatch):
    calls = fake_agenda(tools, monkeypatch, [{"titre": "Privé", "debut": "2026-10-22T09:00", "fin": "2026-10-22T10:00"}])
    assert "Privé" not in briefing.build(friend, "tester", now) and calls == []


# --- Envoi unique, à l'heure --------------------------------------------------------------
def claim(db, hour, minute=0, day=date(2026, 10, 22)):
    return db.claim_due_briefings(day, time(hour, minute))


def enable(db, user, send_time="08:00"):
    db.set_briefing(user, True, send_time)


def test_pas_de_briefing_tant_qu_il_n_est_pas_active(db, alice):
    assert claim(db, 9) == []


def test_pas_de_briefing_avant_l_heure(db, alice):
    enable(db, alice)
    assert claim(db, 7, 59) == []


def test_briefing_du_a_l_heure_et_une_seule_fois_par_jour(db, alice):
    enable(db, alice)
    assert [(c["telegram_id"], c["role"]) for c in claim(db, 8, 0)] == [(OWNER, "owner")]
    assert claim(db, 8, 1) == [] and claim(db, 12) == []
    assert len(claim(db, 8, 0, day=date(2026, 10, 23))) == 1  # le lendemain, de nouveau


def test_un_briefing_manque_est_encore_envoye_dans_les_six_heures(db, alice):
    enable(db, alice)
    assert len(claim(db, 13, 59)) == 1


def test_un_briefing_trop_en_retard_est_saute(db, alice):
    enable(db, alice)
    assert claim(db, 14, 1) == []


def test_desactive_pas_de_briefing(db, alice):
    enable(db, alice)
    db.set_briefing(alice, False)
    assert claim(db, 9) == []


def test_pas_de_briefing_sans_telegram_ou_compte_desactive(db, alice, friend, make_user):
    for user in (alice, friend, make_user("sans")["id"]):
        enable(db, user)
    with db.cursor() as cur:
        cur.execute("UPDATE users SET disabled_at = now() WHERE id = %s", (friend,))
    assert [c["telegram_id"] for c in claim(db, 9)] == [OWNER]


def test_chacun_a_sa_propre_heure(db, alice, friend):
    enable(db, alice, "07:00")
    enable(db, friend, "09:30")
    assert [c["telegram_id"] for c in claim(db, 8)] == [OWNER]
    assert [c["telegram_id"] for c in claim(db, 9, 30)] == [OTHER]


def test_activer_apres_l_heure_ne_declenche_pas_d_envoi_le_jour_meme(db, briefing, alice, now):
    late = now.replace(hour=10)
    briefing.configure(alice, enabled=True, now=late)
    assert db.claim_due_briefings(late.date(), late.time()) == []
    assert len(db.claim_due_briefings(late.date() + timedelta(days=1), time(8, 0))) == 1


def test_activer_avant_l_heure_laisse_le_briefing_partir_aujourd_hui(db, briefing, alice, now):
    briefing.configure(alice, enabled=True, now=now.replace(hour=6))
    assert len(db.claim_due_briefings(now.date(), time(8, 0))) == 1


def test_choisir_une_heure_deja_passee_saute_aujourd_hui(db, briefing, alice, now):
    briefing.configure(alice, enabled=True, time="9:00", now=now.replace(hour=6))
    briefing.configure(alice, time="7:00", now=now.replace(hour=8))
    assert db.claim_due_briefings(now.date(), time(8, 30)) == []


def test_deux_releves_simultanes_n_envoient_qu_un_briefing(db, alice, friend):
    import threading

    enable(db, alice)
    enable(db, friend)
    start, got = threading.Barrier(4), []

    def worker():
        start.wait()
        got.extend(c["telegram_id"] for c in claim(db, 9))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == sorted([OWNER, OTHER])


# --- Routes ----------------------------------------------------------------------------------
@pytest.fixture
def client(app_module, alice, no_agenda, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


def get(client, path, telegram_id=OWNER):
    return client.get(path, query_string={"telegram_id": telegram_id}, headers=HEADERS)


def post(client, path="/briefing", telegram_id=OWNER, **payload):
    return client.post(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


def test_les_routes_du_briefing_exigent_une_identite(client):
    assert client.get("/briefing").status_code == 401
    assert client.post("/briefing", json={"enabled": True}).status_code == 401
    assert client.get("/briefing/preview").status_code == 401
    assert get(client, "/briefing", telegram_id=999).status_code == 403
    assert client.post("/briefing", data="x", headers=HEADERS).status_code == 415


def test_reglages_par_l_api(client):
    assert get(client, "/briefing").get_json() == {"enabled": False, "time": "08:00"}
    assert post(client, enabled=True, time="7h30").get_json() == {"enabled": True, "time": "07:30"}
    assert get(client, "/briefing").get_json() == {"enabled": True, "time": "07:30"}
    assert post(client, enabled=False).get_json() == {"enabled": False, "time": "07:30"}


@pytest.mark.parametrize("payload", [{}, {"enabled": "oui"}, {"time": "matin"}, {"enabled": True, "time": "25:00"}])
def test_reglages_invalides(client, payload):
    assert post(client, **payload).status_code == 400


def test_apercu(client, db, tasks, alice):
    add(db, tasks, alice, "Appeler Paul", datetime.now(tasks.TZ).strftime("%Y-%m-%d"))
    text = get(client, "/briefing/preview").get_json()["text"]
    assert text.startswith("**Briefing du ") and "Appeler Paul" in text


def due_now_setting(db, tasks, user, minutes_ago=1):
    """Règle l'heure d'envoi sur « il y a une minute » (jamais avant minuit : la fenêtre est le jour même)."""
    now = datetime.now(tasks.TZ)
    moment = max(now - timedelta(minutes=minutes_ago), now.replace(hour=0, minute=0, second=0))
    enable(db, user, moment.strftime("%H:%M"))
    with db.cursor() as cur:
        cur.execute("UPDATE briefing_settings SET last_sent_on = NULL")


def test_l_apercu_ne_consomme_pas_le_briefing_du_jour(client, db, tasks, alice):
    due_now_setting(db, tasks, alice)
    get(client, "/briefing/preview")
    assert len(client.post("/briefings/claim", json={}, headers=HEADERS).get_json()["briefings"]) == 1


def test_route_des_briefings_reservee_au_bot(client, db, alice, tasks):
    due_now_setting(db, tasks, alice)
    assert client.post("/briefings/claim", json={}).status_code == 401
    assert client.post("/briefings/claim", json={}, headers={"X-Fox-Service-Token": "faux"}).status_code == 401
    assert client.post("/briefings/claim", data="x", headers=HEADERS).status_code == 415
    body = client.post("/briefings/claim", json={}, headers=HEADERS).get_json()
    assert [b["telegram_id"] for b in body["briefings"]] == [OWNER]
    assert body["briefings"][0]["text"].startswith("**Briefing du ")


def test_un_briefing_n_est_rendu_qu_une_fois_par_la_route(client, db, alice, tasks):
    due_now_setting(db, tasks, alice)
    body = client.post("/briefings/claim", json={}, headers=HEADERS).get_json()
    assert [b["telegram_id"] for b in body["briefings"]] == [OWNER]
    assert client.post("/briefings/claim", json={}, headers=HEADERS).get_json() == {"briefings": []}


def test_un_briefing_qui_ne_peut_pas_etre_construit_ne_bloque_pas_les_autres(client, db, alice, friend, tasks, monkeypatch, app_module):
    due_now_setting(db, tasks, alice)
    due_now_setting(db, tasks, friend)
    real = app_module.briefing.build

    def build(user_id, role="owner", when=None):
        if role == "owner":
            raise RuntimeError("panne")
        return real(user_id, role, when)

    monkeypatch.setattr(app_module.briefing, "build", build)
    body = client.post("/briefings/claim", json={}, headers=HEADERS).get_json()
    assert [b["telegram_id"] for b in body["briefings"]] == [OTHER]


def test_le_reglage_est_journalise_sans_donnees_personnelles(client, db, alice):
    post(client, enabled=True, time="7:30")
    with db.cursor() as cur:
        cur.execute("SELECT details FROM audit_log WHERE event = 'briefing_set'")
        assert cur.fetchall() == [({"time": "07:30", "enabled": True},)]
