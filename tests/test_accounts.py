"""Comptes : mots de passe, codes d'invitation, connexion, sessions."""
import threading

import pytest


def table_text(db, table):
    with db.cursor() as cur:
        cur.execute(f"SELECT t::text FROM {table} t")
        return " ".join(row[0] for row in cur.fetchall())


# --- Mots de passe -----------------------------------------------------------
def test_hachage_aller_retour_et_sel_different(accounts):
    first = accounts.hash_password("un-mot-de-passe-long")
    second = accounts.hash_password("un-mot-de-passe-long")
    assert first != second  # sel aléatoire
    assert accounts.verify_password("un-mot-de-passe-long", first)
    assert not accounts.verify_password("autre-mot-de-passe", first)
    assert "un-mot-de-passe-long" not in first


@pytest.mark.parametrize("stored", ["", "n-importe-quoi", "bcrypt$1$2$3$4$5", "scrypt$x$y$z$a$b"])
def test_empreinte_invalide_ne_valide_jamais(accounts, stored):
    assert accounts.verify_password("peu-importe-ici", stored) is False


@pytest.mark.parametrize("password", ["court", "123456789", "", None])
def test_mot_de_passe_trop_faible_refuse(accounts, password):
    with pytest.raises(accounts.AuthError) as exc:
        accounts.check_password_policy(password)
    assert exc.value.code == "password_weak"


@pytest.mark.parametrize("username", ["ab", "a b c", "nom<script>", "x" * 33, "é-accent", None])
def test_nom_d_utilisateur_invalide_refuse(accounts, username):
    with pytest.raises(accounts.AuthError) as exc:
        accounts.check_username(username)
    assert exc.value.code == "username_invalid"


# --- Codes d'invitation ------------------------------------------------------
def test_inscription_avec_code_valide(accounts, db):
    code = accounts.create_invitation(days=1)
    user = accounts.register(code, "marie", "mot-de-passe-solide")
    assert user["role"] == "tester" and user["username"] == "marie"
    # Ni le code ni le mot de passe ne sont stockés en clair.
    assert code not in table_text(db, "invitation_codes")
    assert "mot-de-passe-solide" not in table_text(db, "users")


def test_code_a_usage_unique(accounts):
    code = accounts.create_invitation(days=1, max_uses=1)
    accounts.register(code, "premier", "mot-de-passe-solide")
    with pytest.raises(accounts.AuthError) as exc:
        accounts.register(code, "second", "mot-de-passe-solide")
    assert exc.value.code == "invitation_invalid"


def test_code_a_usages_multiples_respecte_la_limite(accounts):
    code = accounts.create_invitation(days=1, max_uses=2)
    accounts.register(code, "un-premier", "mot-de-passe-solide")
    accounts.register(code, "un-second", "mot-de-passe-solide")
    with pytest.raises(accounts.AuthError):
        accounts.register(code, "un-troisieme", "mot-de-passe-solide")


def test_code_expire_ou_revoque_refuse(accounts, db):
    expired = accounts.create_invitation(days=1)
    with db.cursor() as cur:
        cur.execute("UPDATE invitation_codes SET expires_at = now() - interval '1 minute'")
    with pytest.raises(accounts.AuthError):
        accounts.register(expired, "expire", "mot-de-passe-solide")

    revoked = accounts.create_invitation(days=1)
    with db.cursor() as cur:
        cur.execute("SELECT id FROM invitation_codes WHERE expires_at > now()")
        invitation_id = cur.fetchone()[0]
    assert accounts.revoke_invitation(invitation_id) is True
    with pytest.raises(accounts.AuthError):
        accounts.register(revoked, "revoque", "mot-de-passe-solide")


@pytest.mark.parametrize("code", ["", None, "FOX-AAAA-BBBB-CCCC", "n'importe quoi"])
def test_code_inconnu_refuse(accounts, code):
    with pytest.raises(accounts.AuthError) as exc:
        accounts.register(code, "inconnu", "mot-de-passe-solide")
    assert exc.value.code == "invitation_invalid"


def test_code_saisi_en_minuscules_et_sans_tirets(accounts):
    code = accounts.create_invitation(days=1)
    typed = code.lower().replace("-", " ")
    assert accounts.register(typed, "saisie", "mot-de-passe-solide")["username"] == "saisie"


def test_nom_deja_pris_sans_tenir_compte_de_la_casse(accounts):
    accounts.register(accounts.create_invitation(days=1), "Marie", "mot-de-passe-solide")
    code = accounts.create_invitation(days=1)
    with pytest.raises(accounts.AuthError) as exc:
        accounts.register(code, "marie", "mot-de-passe-solide")
    assert exc.value.code == "username_taken"
    # Le code n'a pas été consommé par l'échec : il reste utilisable.
    assert accounts.register(code, "marie2", "mot-de-passe-solide")["username"] == "marie2"


def test_inscriptions_simultanees_ne_depassent_pas_la_limite(accounts):
    code = accounts.create_invitation(days=1, max_uses=1)
    results = []

    def attempt(name):
        try:
            accounts.register(code, name, "mot-de-passe-solide")
            results.append("ok")
        except accounts.AuthError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=attempt, args=(f"course{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1


# --- Connexion et sessions ---------------------------------------------------
def test_connexion_ouvre_une_session(accounts, db, make_user):
    user = make_user("alice")
    token, logged = accounts.login("ALICE", user["password"], "10.0.0.1")
    assert logged["id"] == user["id"]
    assert accounts.user_from_session(token)["username"] == "alice"
    assert token not in table_text(db, "sessions")  # seule l'empreinte est stockée


def test_identifiants_faux_meme_message_que_utilisateur_inconnu(accounts, make_user):
    make_user("bob")
    with pytest.raises(accounts.AuthError) as wrong:
        accounts.login("bob", "mauvais-mot-de-passe", "10.0.0.2")
    with pytest.raises(accounts.AuthError) as unknown:
        accounts.login("fantome", "mauvais-mot-de-passe", "10.0.0.3")
    assert (wrong.value.code, wrong.value.message) == (unknown.value.code, unknown.value.message)


def test_blocage_apres_trop_d_echecs_meme_avec_le_bon_mot_de_passe(accounts, make_user):
    user = make_user("carole")
    for _ in range(accounts.MAX_FAILED_PER_USERNAME):
        with pytest.raises(accounts.AuthError) as exc:
            accounts.login("carole", "mauvais-mot-de-passe", "10.0.0.4")
        assert exc.value.code == "invalid_credentials"
    with pytest.raises(accounts.AuthError) as blocked:
        accounts.login("carole", user["password"], "10.0.0.4")
    assert blocked.value.code == "too_many_attempts"


def test_blocage_par_adresse_ip(accounts, make_user, monkeypatch):
    make_user("dave")
    monkeypatch.setattr(accounts, "MAX_FAILED_PER_IP", 3)
    for i in range(3):
        with pytest.raises(accounts.AuthError):
            accounts.login(f"inconnu{i}", "mauvais-mot-de-passe", "10.0.0.5")
    with pytest.raises(accounts.AuthError) as blocked:
        accounts.login("dave", "mot-de-passe-solide", "10.0.0.5")
    assert blocked.value.code == "too_many_attempts"


def test_compte_desactive_ne_peut_pas_se_connecter_et_perd_ses_sessions(accounts, make_user):
    user = make_user("emma")
    token, _ = accounts.login("emma", user["password"], "10.0.0.6")
    assert accounts.set_disabled("emma", True) is True
    assert accounts.user_from_session(token) is None
    with pytest.raises(accounts.AuthError):
        accounts.login("emma", user["password"], "10.0.0.6")
    accounts.set_disabled("emma", False)
    assert accounts.login("emma", user["password"], "10.0.0.6")[1]["username"] == "emma"


def test_session_expiree_ou_fermee_est_refusee(accounts, db, make_user):
    user = make_user("farid")
    token, _ = accounts.login("farid", user["password"], "10.0.0.7")
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert accounts.user_from_session(token) is None

    token, _ = accounts.login("farid", user["password"], "10.0.0.7")
    accounts.logout(token)
    assert accounts.user_from_session(token) is None
    assert accounts.user_from_session("jeton-invente") is None
    assert accounts.user_from_session(None) is None


def test_changement_de_mot_de_passe_ferme_les_sessions(accounts, make_user):
    user = make_user("gina")
    token, _ = accounts.login("gina", user["password"], "10.0.0.8")
    assert accounts.set_password("gina", "nouveau-mot-de-passe") is True
    assert accounts.user_from_session(token) is None
    with pytest.raises(accounts.AuthError):
        accounts.login("gina", user["password"], "10.0.0.8")
    assert accounts.login("gina", "nouveau-mot-de-passe", "10.0.0.8")[1]["username"] == "gina"


def test_compte_sans_mot_de_passe_ne_peut_pas_se_connecter(accounts, make_owner):
    make_owner(username="sansmdp")
    with pytest.raises(accounts.AuthError) as exc:
        accounts.login("sansmdp", "", "10.0.0.9")
    assert exc.value.code == "invalid_credentials"


def test_utilisateur_depuis_telegram(accounts, make_owner):
    owner = make_owner(telegram_id=555)
    assert accounts.user_from_telegram(555)["id"] == owner["id"]
    assert accounts.user_from_telegram("555")["id"] == owner["id"]
    assert accounts.user_from_telegram(556) is None
    assert accounts.user_from_telegram("pas-un-nombre") is None
    accounts.set_disabled("proprio", True)
    assert accounts.user_from_telegram(555) is None


def test_un_seul_proprietaire(db, make_owner):
    import psycopg2

    make_owner(telegram_id=1, username="premier")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        make_owner(telegram_id=2, username="second")


def test_session_d_un_compte_desactive_est_refusee_meme_si_elle_existe_encore(accounts, db, make_user):
    """Défense en profondeur : la désactivation ne dépend pas de la fermeture des sessions."""
    user = make_user("hugo")
    token, _ = accounts.login("hugo", user["password"], "10.0.0.10")
    with db.cursor() as cur:
        cur.execute("UPDATE users SET disabled_at = now() WHERE username = 'hugo'")
    assert accounts.user_from_session(token) is None
