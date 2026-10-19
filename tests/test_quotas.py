"""Quotas par utilisateur, plafond global et plafond du fournisseur gratuit (Groq)."""
from types import SimpleNamespace

import pytest


def user_usage(db, user_id):
    with db.cursor() as cur:
        cur.execute("SELECT calls, tokens FROM user_usage WHERE user_id = %s", (user_id,))
        return cur.fetchone()


def test_les_quotas_des_utilisateurs_sont_independants(db, make_user):
    a, b = make_user(), make_user()
    for _ in range(2):
        db.reserve_quota(a["id"], 2, 10**6, 100, 10**9)
    with pytest.raises(db.QuotaExceeded) as exc:
        db.reserve_quota(a["id"], 2, 10**6, 100, 10**9)
    assert exc.value.scope == "user"
    db.reserve_quota(b["id"], 2, 10**6, 100, 10**9)  # B n'est pas affecté par A
    assert user_usage(db, a["id"]) == (2, 0) and user_usage(db, b["id"]) == (1, 0)


def test_plafond_de_tokens_par_utilisateur(db, make_user):
    a, b = make_user(), make_user()
    db.reserve_quota(a["id"], 100, 50, 100, 10**9)
    db.add_tokens(a["id"], 60)
    with pytest.raises(db.QuotaExceeded) as exc:
        db.reserve_quota(a["id"], 100, 50, 100, 10**9)
    assert exc.value.scope == "user"
    db.reserve_quota(b["id"], 100, 50, 100, 10**9)


def test_refus_global_n_use_pas_le_quota_de_l_utilisateur(db, make_user):
    a, b = make_user(), make_user()
    db.reserve_quota(a["id"], 5, 10**6, 1, 10**9)
    with pytest.raises(db.QuotaExceeded) as exc:
        db.reserve_quota(b["id"], 5, 10**6, 1, 10**9)
    assert exc.value.scope == "global"
    assert user_usage(db, b["id"]) is None  # la réservation de B a été annulée


def test_tokens_comptes_pour_l_utilisateur_et_le_global(db, make_user):
    a, b = make_user(), make_user()
    for user in (a, b):
        db.reserve_quota(user["id"], 5, 10**6, 100, 10**9)
    db.add_tokens(a["id"], 100)
    db.add_tokens(b["id"], 25)
    assert user_usage(db, a["id"]) == (1, 100) and user_usage(db, b["id"]) == (1, 25)
    with db.cursor() as cur:
        cur.execute("SELECT calls, tokens FROM api_usage")
        assert cur.fetchone() == (2, 125)


def test_plafond_du_fournisseur(db):
    assert db.reserve_provider("groq", 2, 1000) is True
    assert db.reserve_provider("groq", 2, 1000) is True
    assert db.reserve_provider("groq", 2, 1000) is False
    assert db.reserve_provider("autre", 2, 1000) is True  # compteur propre à chaque fournisseur


def test_plafond_de_tokens_du_fournisseur(db):
    assert db.reserve_provider("groq", 100, 50) is True
    db.add_provider_tokens("groq", 60)
    assert db.reserve_provider("groq", 100, 50) is False


@pytest.fixture
def groq(agent, monkeypatch):
    state = SimpleNamespace(calls=0)

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 70}}

    def fake_post(url, **kwargs):
        state.calls += 1
        return Reply()

    monkeypatch.setattr(agent.httpx, "post", fake_post)
    monkeypatch.setattr(agent, "GROQ_API_KEY", "cle-groq-de-test")
    return state


def test_groq_compte_ses_appels_et_ses_tokens(agent, db, groq):
    agent.groq_reply(1, "bonjour")
    with db.cursor() as cur:
        cur.execute("SELECT provider, calls, tokens FROM provider_usage")
        assert cur.fetchone() == ("groq", 1, 70)


def test_groq_refuse_sans_appel_reseau_quand_le_plafond_est_atteint(agent, db, groq, monkeypatch):
    monkeypatch.setattr(agent, "GROQ_MAX_CALLS_PER_DAY", 1)
    agent.groq_reply(1, "premier")
    with pytest.raises(agent.ProviderQuotaExceeded):
        agent.groq_reply(1, "second")
    assert groq.calls == 1  # aucune requête envoyée à Groq pour le second message


def test_plafond_de_tokens_groq_protege_le_quota_du_compte(agent, db, groq, monkeypatch):
    monkeypatch.setattr(agent, "GROQ_MAX_TOKENS_PER_DAY", 50)
    agent.groq_reply(1, "premier")  # consomme 70 tokens
    with pytest.raises(agent.ProviderQuotaExceeded):
        agent.groq_reply(1, "second")
