"""API : conversations, mode privé, purge, inscription depuis Telegram."""
import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER_TELEGRAM = 4242
FRIEND_TELEGRAM = 777001
PASSWORD = "mot-de-passe-solide"


@pytest.fixture
def seen(agent, monkeypatch):
    """Double de l'agent : écho, et note le contexte reçu (conversation, mode privé)."""
    calls = []

    def fake(user_id, text, role="owner", chat_id=None, private=False):
        calls.append({"user_id": user_id, "role": role, "chat_id": chat_id, "private": private})
        return f"écho : {text}", 5, []

    monkeypatch.setattr(agent, "chat", fake)
    return calls


@pytest.fixture
def owner(make_owner):
    return make_owner(OWNER_TELEGRAM)


@pytest.fixture
def client(app_module, owner, seen, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    monkeypatch.setattr(app_module, "MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "MAX_TOKENS_PER_DAY", 10**9)
    monkeypatch.setattr(app_module, "OWNER_MAX_CALLS_PER_DAY", 1000)
    monkeypatch.setattr(app_module, "OWNER_MAX_TOKENS_PER_DAY", 10**9)
    monkeypatch.setattr(app_module, "_last_purge", 0.0)
    return app_module.app.test_client()


def say(client, text="Bonjour", telegram_id=OWNER_TELEGRAM, **extra):
    return client.post(
        "/message", json={"telegram_id": telegram_id, "text": text, **extra}, headers=HEADERS
    )


def chats(client, telegram_id=OWNER_TELEGRAM):
    return client.get("/chats", query_string={"telegram_id": telegram_id}, headers=HEADERS)


def post(client, path, telegram_id=OWNER_TELEGRAM, **payload):
    return client.post(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


def delete(client, path, telegram_id=OWNER_TELEGRAM, **payload):
    return client.delete(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


# --- Accès -------------------------------------------------------------------
def test_les_routes_de_conversations_exigent_une_identite(client):
    assert client.get("/chats").status_code == 401
    assert client.post("/chats", json={}).status_code == 401
    assert client.delete("/chats/1", json={}).status_code == 401
    assert client.delete("/chats", json={"confirm": "all"}).status_code == 401
    assert client.post("/private", json={"on": True}).status_code == 401
    assert client.post("/chats/1/open", json={}).status_code == 401


def test_un_inconnu_telegram_est_refuse_sur_les_conversations(client):
    assert chats(client, telegram_id=999).status_code == 403
    assert post(client, "/chats", telegram_id=999).status_code == 403
    assert post(client, "/private", telegram_id=999, on=True).status_code == 403


def test_suppression_et_modification_exigent_du_json(client):
    # Défense contre les requêtes inter-sites : un corps non JSON est refusé.
    assert client.delete("/chats/1", headers=HEADERS).status_code == 415
    assert client.post("/private", data="on=1", headers=HEADERS).status_code == 415


# --- Conversations -----------------------------------------------------------
def test_un_message_cree_la_conversation_et_la_renvoie(client, db, owner):
    body = say(client, "Parle-moi de Python").get_json()
    assert body["private"] is False and isinstance(body["chat_id"], int)
    listing = chats(client).get_json()
    assert listing["current"] == body["chat_id"]
    assert listing["chats"][0]["title"] == "Parle-moi de Python"
    assert db.load_history(owner["id"], 5) == [("Parle-moi de Python", "écho : Parle-moi de Python")]


def test_nouvelle_conversation_separe_le_contexte_envoye_au_modele(client, seen):
    first = say(client, "sujet A").get_json()["chat_id"]
    created = post(client, "/chats")
    assert created.status_code == 201
    second = say(client, "sujet B").get_json()["chat_id"]
    assert second != first and second == created.get_json()["chat_id"]
    assert seen[-1]["chat_id"] == second


def test_reprendre_une_conversation_via_l_api(client, seen):
    first = say(client, "sujet A").get_json()["chat_id"]
    post(client, "/chats")
    say(client, "sujet B")
    assert post(client, f"/chats/{first}/open").status_code == 200
    assert chats(client).get_json()["current"] == first
    assert say(client, "suite").get_json()["chat_id"] == first


def test_message_vers_une_conversation_precise(client, seen):
    first = say(client, "sujet A").get_json()["chat_id"]
    post(client, "/chats")
    say(client, "sujet B")
    body = say(client, "retour à A", chat_id=first).get_json()
    assert body["chat_id"] == first and seen[-1]["chat_id"] == first


def test_conversation_d_un_autre_utilisateur_introuvable(client, db, make_user, make_owner):
    stranger = make_user()
    foreign = db.save_exchange(stranger["id"], "secret", "x")
    assert say(client, "coucou", chat_id=foreign).status_code == 404
    assert post(client, f"/chats/{foreign}/open").status_code == 404
    assert delete(client, f"/chats/{foreign}").status_code == 404
    assert db.load_history(stranger["id"], 5) == [("secret", "x")]  # rien n'a bougé


@pytest.mark.parametrize("bad", ["12", 1.5, True, [1], {"a": 1}])
def test_chat_id_invalide_refuse(client, bad):
    assert say(client, "coucou", chat_id=bad).status_code == 400


def test_supprimer_une_conversation(client, db, owner):
    a = say(client, "A").get_json()["chat_id"]
    post(client, "/chats")
    b = say(client, "B").get_json()["chat_id"]
    assert delete(client, f"/chats/{a}").status_code == 200
    assert [c["id"] for c in chats(client).get_json()["chats"]] == [b]
    assert delete(client, f"/chats/{a}").status_code == 404
    with db.cursor() as cur:
        cur.execute("SELECT event FROM audit_log WHERE event = 'chat_deleted'")
        assert cur.fetchone() is not None  # la suppression est tracée, sans le contenu


def test_supprimer_tout_demande_une_confirmation_explicite(client, db, owner):
    say(client, "A")
    assert delete(client, "/chats").status_code == 400
    assert delete(client, "/chats", confirm="oui").status_code == 400
    assert db.list_chats(owner["id"])  # rien supprimé
    response = delete(client, "/chats", confirm="all")
    assert response.status_code == 200 and response.get_json()["chats"] == 1
    assert db.list_chats(owner["id"]) == []


def test_supprimer_tout_ne_touche_pas_aux_autres(client, db, make_user):
    other = make_user()
    db.save_exchange(other["id"], "à lui", "x")
    say(client, "à moi")
    delete(client, "/chats", confirm="all")
    assert db.load_history(other["id"], 5) == [("à lui", "x")]


# --- Mode privé --------------------------------------------------------------
def test_mode_prive_n_enregistre_rien(client, db, owner, seen):
    say(client, "public")
    assert post(client, "/private", on=True).get_json() == {"private": True}
    body = say(client, "très secret").get_json()
    assert body["private"] is True and body["chat_id"] is None
    assert seen[-1]["private"] is True
    assert db.load_history(owner["id"], 10) == [("public", "écho : public")]
    assert db.load_private_history(owner["id"], 10) == [("très secret", "écho : très secret")]
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations WHERE user_message = 'très secret'")
        assert cur.fetchone()[0] == 0


def test_mode_prive_l_emporte_sur_un_chat_id(client, db, owner):
    chat_id = say(client, "public").get_json()["chat_id"]
    post(client, "/private", on=True)
    body = say(client, "secret", chat_id=chat_id).get_json()
    assert body["private"] is True
    assert db.load_history(owner["id"], 10) == [("public", "écho : public")]


def test_mode_prive_listing_sans_conversation_courante(client):
    say(client, "public")
    post(client, "/private", on=True)
    listing = chats(client).get_json()
    assert listing["private"] is True and listing["current"] is None


def test_la_confirmation_d_une_action_n_est_pas_enregistree_en_mode_prive(
    client, db, owner, app_module, agent, monkeypatch
):
    monkeypatch.setattr(agent, "resolve_action", lambda *a, **k: "Fait.")
    say(client, "public")
    post(client, "/private", on=True)
    response = post(client, "/actions/6f1f5c4e-2b0a-4a2e-9d57-5c1f3a5b7d11/confirm")
    assert response.status_code == 200
    assert db.load_history(owner["id"], 10) == [("public", "écho : public")]
    assert db.load_private_history(owner["id"], 10)[-1][1] == "Fait."


def test_sortie_du_mode_prive(client, db, owner):
    post(client, "/private", on=True)
    say(client, "secret")
    assert post(client, "/private", on=False).get_json() == {"private": False}
    body = say(client, "public").get_json()
    assert body["private"] is False and isinstance(body["chat_id"], int)
    assert db.load_private_history(owner["id"], 10) == []


def test_nouvelle_conversation_sort_du_mode_prive(client):
    post(client, "/private", on=True)
    assert post(client, "/chats").get_json()["private"] is False
    assert say(client, "visible").get_json()["private"] is False


@pytest.mark.parametrize("bad", [None, "true", 1, 0])
def test_mode_prive_valeur_invalide(client, bad):
    assert post(client, "/private", on=bad).status_code == 400


def test_mode_prive_propre_a_chaque_utilisateur(client, db, owner, make_user):
    other = make_user()
    post(client, "/private", on=True)
    assert db.private_active(owner["id"]) and not db.private_active(other["id"])


def test_le_mode_prive_est_trace_sans_contenu(client, db):
    post(client, "/private", on=True)
    say(client, "mot de passe de la banque : 1234")
    with db.cursor() as cur:
        cur.execute("SELECT event, details::text FROM audit_log WHERE event LIKE 'private%'")
        assert cur.fetchall() == [("private_on", "{}")]
        cur.execute("SELECT count(*) FROM audit_log WHERE details::text LIKE '%banque%'")
        assert cur.fetchone()[0] == 0


# --- Purge déclenchée par l'usage --------------------------------------------
def test_la_purge_s_execute_au_premier_message_puis_au_plus_une_fois_par_heure(
    client, app_module, db, owner, monkeypatch
):
    calls = []
    real = db.purge_history
    monkeypatch.setattr(db, "purge_history", lambda: calls.append(1) or real())
    say(client, "un")
    say(client, "deux")
    assert len(calls) == 1
    monkeypatch.setattr(app_module, "_last_purge", app_module._last_purge - 3601)
    say(client, "trois")
    assert len(calls) == 2


def test_un_echec_de_purge_ne_bloque_pas_les_messages(client, db, monkeypatch):
    def boom():
        raise RuntimeError("base occupée")

    monkeypatch.setattr(db, "purge_history", boom)
    assert say(client, "bonjour").status_code == 200


def test_la_purge_supprime_les_vieilles_conversations_via_un_message(client, db, owner):
    old = db.save_exchange(owner["id"], "vieux", "r")
    with db.cursor() as cur:
        cur.execute("UPDATE chats SET updated_at = now() - interval '120 days' WHERE id = %s", (old,))
    say(client, "nouveau")
    assert old not in [c["id"] for c in db.list_chats(owner["id"])]


# --- Inscription depuis Telegram ----------------------------------------------
def register_tg(client, code, telegram_id=FRIEND_TELEGRAM, headers=HEADERS):
    return client.post(
        "/auth/telegram", json={"telegram_id": telegram_id, "code": code}, headers=headers
    )


def test_inscription_telegram_cree_un_testeur(client, accounts, db):
    code = accounts.create_invitation(days=1)
    response = register_tg(client, code)
    assert response.status_code == 201
    user = response.get_json()["user"]
    assert user["role"] == "tester" and user["username"] == f"tg{FRIEND_TELEGRAM}"
    assert accounts.user_from_telegram(FRIEND_TELEGRAM)["id"] == user["id"]
    with db.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
        assert cur.fetchone()[0] is None  # pas de connexion web sans mot de passe


def test_inscription_telegram_reservee_au_bot(client, accounts):
    code = accounts.create_invitation(days=1)
    assert client.post(
        "/auth/telegram", json={"telegram_id": FRIEND_TELEGRAM, "code": code}
    ).status_code == 401
    assert register_tg(client, code, headers={"X-Fox-Service-Token": "faux"}).status_code == 401
    assert accounts.user_from_telegram(FRIEND_TELEGRAM) is None


def test_inscription_telegram_code_a_usage_unique(client, accounts):
    code = accounts.create_invitation(days=1, max_uses=1)
    assert register_tg(client, code).status_code == 201
    assert register_tg(client, code, telegram_id=FRIEND_TELEGRAM + 1).status_code == 400
    assert accounts.user_from_telegram(FRIEND_TELEGRAM + 1) is None


@pytest.mark.parametrize("bad", ["", None, "FOX-AAAA-BBBB-CCCC", "n'importe quoi"])
def test_inscription_telegram_code_invalide(client, accounts, bad):
    response = register_tg(client, bad)
    assert response.status_code == 400
    assert accounts.user_from_telegram(FRIEND_TELEGRAM) is None


@pytest.mark.parametrize("bad", [None, "abc", 0, -5, "1; DROP TABLE users"])
def test_inscription_telegram_identifiant_invalide(client, accounts, bad):
    code = accounts.create_invitation(days=1)
    assert register_tg(client, code, telegram_id=bad).status_code == 400


def test_inscription_telegram_code_revoque_ou_expire(client, accounts, db):
    revoked = accounts.create_invitation(days=1)
    accounts.revoke_invitation(accounts.list_invitations()[-1][0])
    assert register_tg(client, revoked).status_code == 400
    expired = accounts.create_invitation(days=1)
    with db.cursor() as cur:
        cur.execute("UPDATE invitation_codes SET expires_at = now() - interval '1 minute' WHERE id = (SELECT max(id) FROM invitation_codes)")
    assert register_tg(client, expired).status_code == 400


def test_compte_deja_inscrit_ne_consomme_pas_le_code(client, accounts, db):
    first = accounts.create_invitation(days=1)
    assert register_tg(client, first).status_code == 201
    second = accounts.create_invitation(days=1)
    assert register_tg(client, second).status_code == 409
    with db.cursor() as cur:
        cur.execute("SELECT uses FROM invitation_codes ORDER BY id DESC LIMIT 1")
        assert cur.fetchone()[0] == 0
    assert register_tg(client, second, telegram_id=FRIEND_TELEGRAM + 1).status_code == 201


def test_le_proprietaire_ne_peut_pas_se_reinscrire(client, accounts):
    code = accounts.create_invitation(days=1)
    assert register_tg(client, code, telegram_id=OWNER_TELEGRAM).status_code == 409


def test_inscription_telegram_limite_les_essais_de_code(client, accounts):
    for _ in range(10):
        assert register_tg(client, "FOX-FAUX-FAUX-FAUX").status_code == 400
    good = accounts.create_invitation(days=1)
    assert register_tg(client, good).status_code == 429  # bloqué pour cet identifiant
    assert register_tg(client, good, telegram_id=FRIEND_TELEGRAM + 5).status_code == 201


def test_un_testeur_inscrit_par_telegram_peut_parler_mais_reste_testeur(client, accounts, seen, db):
    code = accounts.create_invitation(days=1)
    register_tg(client, code)
    body = say(client, "Salut", telegram_id=FRIEND_TELEGRAM).get_json()
    assert body["reply"] == "écho : Salut"
    assert seen[-1]["role"] == "tester"
    friend = accounts.user_from_telegram(FRIEND_TELEGRAM)
    assert db.load_history(friend["id"], 5) == [("Salut", "écho : Salut")]


def test_deux_inscriptions_simultanees_avec_le_meme_code_a_usage_unique(app_module, client, accounts):
    import threading

    code = accounts.create_invitation(days=1, max_uses=1)
    results = []
    barrier = threading.Barrier(2)

    def go(telegram_id):
        c = app_module.app.test_client()
        barrier.wait()
        results.append(register_tg(c, code, telegram_id=telegram_id).status_code)

    threads = [threading.Thread(target=go, args=(FRIEND_TELEGRAM + i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 400]


# --- Isolation entre utilisateurs (historique) ---------------------------------
def test_chaque_utilisateur_ne_voit_que_ses_conversations(client, accounts, seen):
    accounts_code = accounts.create_invitation(days=1)
    register_tg(client, accounts_code)
    say(client, "secret du propriétaire")
    say(client, "secret de l'ami", telegram_id=FRIEND_TELEGRAM)
    mine = chats(client).get_json()["chats"]
    his = chats(client, telegram_id=FRIEND_TELEGRAM).get_json()["chats"]
    assert [c["title"] for c in mine] == ["secret du propriétaire"]
    assert [c["title"] for c in his] == ["secret de l'ami"]


def test_apres_nouveau_la_conversation_courante_est_vide_et_non_listee(client):
    first = say(client, "sujet A").get_json()["chat_id"]
    empty = post(client, "/chats").get_json()["chat_id"]
    listing = chats(client).get_json()
    assert [c["id"] for c in listing["chats"]] == [first]  # la conversation vide n'est pas listée
    assert listing["current"] is None  # donc rien à supprimer « en cours » par erreur
    assert say(client, "sujet B").get_json()["chat_id"] == empty
    assert chats(client).get_json()["current"] == empty
