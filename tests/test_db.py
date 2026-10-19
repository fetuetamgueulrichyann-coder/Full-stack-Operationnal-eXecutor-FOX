"""Couche base de données : plafonds, historique, actions en attente."""
import pytest


def usage(db):
    with db.cursor() as cur:
        cur.execute("SELECT calls, tokens FROM api_usage")
        return cur.fetchone()


def test_plafond_global_d_appels(db, make_user):
    a, b = make_user(), make_user()
    db.reserve_quota(a["id"], 100, 10**6, 2, 1000)
    db.reserve_quota(b["id"], 100, 10**6, 2, 1000)
    with pytest.raises(db.QuotaExceeded) as exc:
        db.reserve_quota(a["id"], 100, 10**6, 2, 1000)
    assert exc.value.scope == "global"
    assert usage(db) == (2, 0)


def test_plafond_global_de_tokens(db, make_user):
    a = make_user()
    db.reserve_quota(a["id"], 100, 10**6, 100, 50)
    db.add_tokens(a["id"], 60)
    with pytest.raises(db.QuotaExceeded):
        db.reserve_quota(a["id"], 100, 10**6, 100, 50)


def test_historique_ordre_limite_et_separation_par_utilisateur(db):
    for i in range(5):
        db.save_exchange(1, f"q{i}", f"r{i}")
    db.save_exchange(2, "secret", "confidentiel")
    history = db.load_history(1, 3)
    assert history == [("q2", "r2"), ("q3", "r3"), ("q4", "r4")]
    assert all("secret" not in message for pair in history for message in pair)
    assert db.load_history(2, 10) == [("secret", "confidentiel")]
    assert db.load_history(3, 10) == []


def test_cycle_de_vie_d_une_action(db):
    action = db.create_pending(1, "outil", {"a": 1}, "résumé", 10)
    assert db.confirm_pending(action, 2) is None  # mauvais utilisateur
    assert db.confirm_pending(action, 1) == ("outil", {"a": 1})
    assert db.confirm_pending(action, 1) is None  # une seule confirmation


def test_annulation_et_expiration(db):
    a = db.create_pending(1, "outil", {}, "r", 10)
    assert db.cancel_pending(a, 2) is None
    assert db.cancel_pending(a, 1) == ("outil",)
    b = db.create_pending(1, "outil", {}, "r", 0)
    assert db.confirm_pending(b, 1) is None  # expirée
    assert db.expire_pending(b, 1) == ("outil",)


def test_journal_d_audit(db):
    db.audit(1, "proposed", "outil", {"x": 1})
    db.audit(None, "denied", None, {"reason": "test"})
    with db.cursor() as cur:
        cur.execute("SELECT user_id, event, tool, details FROM audit_log ORDER BY id")
        rows = cur.fetchall()
    assert rows == [(1, "proposed", "outil", {"x": 1}), (None, "denied", None, {"reason": "test"})]


def test_resultat_non_serialisable_est_enregistre(db):
    import datetime

    a = db.create_pending(1, "outil", {}, "r", 10)
    db.confirm_pending(a, 1)
    db.finish_pending(a, "executed", {"quand": datetime.datetime(2026, 1, 1)})
    with db.cursor() as cur:
        cur.execute("SELECT status, result FROM pending_actions WHERE id = %s", (a,))
        status, result = cur.fetchone()
    assert status == "executed" and "2026" in result["quand"]
