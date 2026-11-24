"""Passerelle d'envoi : refus par défaut, autorisations relues à chaque appel, journal sans contenu."""
import pytest

USER = 601
SECRET = "SECRET-CONTENU-À-NE-JAMAIS-JOURNALISER"
CLIENT_NAME = "Madame Nguema Eyenga"

egress = store = privacy = None


@pytest.fixture(autouse=True)
def _modules(db, monkeypatch):
    global egress, store, privacy
    import egress as e
    import privacy as p
    import privacy_store as s

    egress, store, privacy = e, s, p
    monkeypatch.delenv("PRIVACY_BLOCK_ALL", raising=False)


@pytest.fixture
def chat(db):
    return db.save_exchange(USER, "bonjour", "salut")


def seg(category, file_id=None, size=10):
    return egress.Segment(category, file_id, size)


def audit(db):
    with db.cursor() as cur:
        cur.execute("SELECT user_id, event, details FROM audit_log WHERE event = 'egress' ORDER BY id")
        return cur.fetchall()


def test_sans_contexte_rien_ne_part(db):
    with pytest.raises(egress.EgressRefused) as exc:
        egress.authorize("groq", [seg("texte_libre")])
    assert exc.value.code == "no_context"
    assert audit(db) == []


def test_le_contexte_est_retire_a_la_fin():
    token = egress.begin(USER, "owner")
    assert egress.current().user_id == USER
    egress.end(token)
    assert egress.current() is None


def test_protege_sans_autorisation_demande_une_autorisation(db):
    token = egress.begin(USER, "owner")
    try:
        with pytest.raises(egress.EgressRefused) as exc:
            egress.authorize("groq", [seg("texte_libre"), seg("personnel")])
        assert exc.value.code == "grant_required" and exc.value.missing == ("texte_libre", "personnel")
    finally:
        egress.end(token)
    assert audit(db) == []  # un refus ne journalise pas d'envoi


def test_strict_bloque_meme_avec_autorisation(db, chat):
    store.save_settings(USER, level="strict")
    token = egress.begin(USER, "owner", chat_id=chat)
    try:
        store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
        with pytest.raises(egress.EgressRefused) as exc:
            egress.authorize("groq", [seg("texte_libre")])
        assert exc.value.code == "blocked"
    finally:
        egress.end(token)


def test_interrupteur_d_urgence_bloque_tout(db, chat, monkeypatch):
    store.save_settings(USER, level="standard")
    store.record_consent(USER, 1)
    token = egress.begin(USER, "owner", chat_id=chat)
    try:
        egress.authorize("groq", [seg("texte_libre")])  # Standard consenti : passe
        monkeypatch.setenv("PRIVACY_BLOCK_ALL", "1")
        with pytest.raises(egress.EgressRefused) as exc:
            egress.authorize("groq", [seg("texte_libre")])
        assert exc.value.code == "blocked"
    finally:
        egress.end(token)


def test_un_testeur_n_atteint_jamais_gemini_ni_les_voix_payantes(db):
    token = egress.begin(USER, "tester")
    try:
        for provider in ("gemini", "gemini_tts", "elevenlabs"):
            with pytest.raises(egress.EgressRefused) as exc:
                egress.authorize(provider, [seg("texte_libre")])
            assert exc.value.code == "provider"
    finally:
        egress.end(token)


def test_fournisseur_inconnu_refuse(db):
    token = egress.begin(USER, "owner")
    try:
        with pytest.raises(egress.EgressRefused) as exc:
            egress.authorize("openai", [seg("texte_libre")])
        assert exc.value.code == "provider"
    finally:
        egress.end(token)


def test_cinq_tours_d_outils_avec_une_seule_autorisation_puis_seconde_operation_refusee(db):
    operation = egress.new_operation_id()
    token = egress.begin(USER, "owner", operation_id=operation)
    try:
        store.create_grant(USER, "groq", "texte_libre", "message", operation_id=operation)
        store.create_grant(USER, "groq", "personnel", "message", operation_id=operation)
        for _ in range(5):  # tours d'une même opération
            egress.authorize("groq", [seg("texte_libre"), seg("personnel")])
    finally:
        egress.end(token)
    second = egress.begin(USER, "owner")  # autre message = autre opération
    try:
        with pytest.raises(egress.EgressRefused) as exc:
            egress.authorize("groq", [seg("texte_libre")])
        assert exc.value.code == "grant_required"
    finally:
        egress.end(second)
    assert len(audit(db)) == 5


def test_une_autorisation_de_texte_ne_couvre_ni_les_donnees_metier_ni_un_fichier(db, chat):
    file_id = db.add_file(USER, "devis.pdf", "application/pdf", "pdf", b"%PDF-1.4", 10_000_000)
    token = egress.begin(USER, "owner", chat_id=chat)
    try:
        store.create_grant(USER, "groq", "texte_libre", "conversation", chat_id=chat)
        egress.authorize("groq", [seg("texte_libre")])
        for segment in (seg("metier"), seg("personnel"), seg("fichier", file_id)):
            with pytest.raises(egress.EgressRefused) as exc:
                egress.authorize("groq", [segment])
            assert exc.value.code == "grant_required"
    finally:
        egress.end(token)


def test_la_revocation_arrete_l_operation_au_tour_suivant(db):
    operation = egress.new_operation_id()
    token = egress.begin(USER, "owner", operation_id=operation)
    try:
        grant = store.create_grant(USER, "groq", "texte_libre", "message", operation_id=operation)
        egress.authorize("groq", [seg("texte_libre")])
        egress.authorize("groq", [seg("texte_libre")])
        store.revoke(USER, grant)  # l'utilisateur révoque entre le tour 2 et le tour 3
        with pytest.raises(egress.EgressRefused) as exc:
            egress.authorize("groq", [seg("texte_libre")])
        assert exc.value.code == "grant_required"
    finally:
        egress.end(token)


def test_un_segment_repete_n_est_compte_qu_une_fois(db):
    operation = egress.new_operation_id()
    token = egress.begin(USER, "owner", operation_id=operation)
    try:
        store.create_grant(USER, "groq", "texte_libre", "message", operation_id=operation)
        result = egress.authorize("groq", [seg("texte_libre"), seg("texte_libre")])
        assert len(result.grants) == 1
    finally:
        egress.end(token)


def test_le_journal_ne_contient_que_des_metadonnees(db, chat):
    file_id = db.add_file(USER, CLIENT_NAME + ".pdf", "application/pdf", "pdf", b"%PDF-1.4", 10_000_000)
    operation = egress.new_operation_id()
    token = egress.begin(USER, "owner", chat_id=chat, operation_id=operation)
    try:
        store.create_grant(USER, "groq", "texte_libre", "message", operation_id=operation)
        store.create_grant(USER, "groq", "fichier", "message", operation_id=operation, file_id=file_id)
        # Le contenu existe dans l'appel appelant, jamais dans les segments transmis à la passerelle.
        _ = (SECRET, CLIENT_NAME)
        egress.authorize("groq", [seg("texte_libre", size=1234), seg("fichier", file_id, size=99)])
    finally:
        egress.end(token)
    (user_id, event, details), = audit(db)
    assert user_id == USER and event == "egress"
    assert set(details) == {"provider", "categories", "operation_id", "grants", "segments", "size", "outcome", "chat_id", "file_ids"}
    assert details["categories"] == ["fichier", "texte_libre"] and details["size"] == 1333 and details["segments"] == 2
    assert details["file_ids"] == [file_id] and details["outcome"] == "authorized"
    dumped = str(details)
    assert SECRET not in dumped and CLIENT_NAME not in dumped and "devis" not in dumped


def test_les_modules_de_la_passerelle_n_importent_aucune_bibliotheque_reseau():
    import ast
    import pathlib

    forbidden = {"httpx", "requests", "urllib", "socket", "http", "smtplib", "ftplib", "aiohttp", "google", "telegram", "webpush"}
    for name in ("egress.py", "privacy.py", "privacy_store.py", "providers.py"):
        tree = ast.parse((pathlib.Path(egress.__file__).parent / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(m.split(".")[0] in forbidden for m in modules), (name, modules)
