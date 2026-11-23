"""Mémoire des fichiers : FOX revoit les fichiers de ses derniers échanges."""
from test_app_commercial import OWNER, alice, call, client, friend  # noqa: F401
from test_files import JPEG, make_pdf, seen, send, upload  # noqa: F401
from test_files import db_user_id


def _blobs(contents):
    return [p for c in contents for p in (c.parts or []) if getattr(p, "inline_data", None)]


def _texts(contents):
    return "\n".join(p.text for c in contents for p in (c.parts or []) if getattr(p, "text", None))


def test_fox_revoit_l_image_au_message_suivant(client, seen, db, agent):
    file_id = upload(client, JPEG, "mur.jpg").get_json()["file"]["id"]
    chat_id = send(client, "Regarde ce mur", [file_id]).get_json()["chat_id"]
    send(client, "Et la couleur ?", chat_id=chat_id)
    contents = agent.build_contents(db_user_id(db), "Quelle était la couleur ?", chat_id)
    assert len(_blobs(contents)) == 1 and "mur.jpg" in _texts(contents)
    assert "n'est plus visible" not in _texts(contents)


def test_texte_d_un_pdf_revu(client, seen, db, agent):
    file_id = upload(client, make_pdf(), "facture.pdf").get_json()["file"]["id"]
    chat_id = send(client, "Lis", [file_id]).get_json()["chat_id"]
    contents = agent.build_contents(db_user_id(db), "Combien déjà ?", chat_id)
    assert _blobs(contents) or "150 000" in _texts(contents)


def test_au_dela_de_la_memoire_seul_le_nom_reste(client, seen, db, agent, monkeypatch):
    monkeypatch.setattr(agent, "FILE_MEMORY_TURNS", 2)
    file_id = upload(client, JPEG, "vieux.jpg").get_json()["file"]["id"]
    chat_id = send(client, "Regarde", [file_id]).get_json()["chat_id"]
    for i in range(3):
        send(client, f"message {i}", chat_id=chat_id)
    contents = agent.build_contents(db_user_id(db), "et ?", chat_id)
    assert not _blobs(contents)
    assert "vieux.jpg" in _texts(contents) and "n'est plus visible" in _texts(contents)


def test_limite_d_images_revues(client, seen, db, agent, monkeypatch):
    monkeypatch.setattr(agent, "FILE_MEMORY_BLOBS", 1)
    ids = [upload(client, JPEG, f"p{i}.jpg").get_json()["file"]["id"] for i in range(2)]
    chat_id = send(client, "Regarde", ids[:1]).get_json()["chat_id"]
    send(client, "Et celle-ci", ids[1:], chat_id=chat_id)
    contents = agent.build_contents(db_user_id(db), "?", chat_id)
    assert len(_blobs(contents)) == 1  # la plus récente d'abord
    assert "p0.jpg" in _texts(contents) and "n'est plus visible" in _texts(contents)


def test_fichier_supprime_ne_casse_rien(client, seen, db, agent):
    file_id = upload(client, JPEG, "mur.jpg").get_json()["file"]["id"]
    chat_id = send(client, "Regarde", [file_id]).get_json()["chat_id"]
    with db.cursor() as cur:
        cur.execute("DELETE FROM files")
    contents = agent.build_contents(db_user_id(db), "?", chat_id)
    assert not _blobs(contents) and "n'est plus visible" in _texts(contents)


def test_groq_recoit_le_texte_des_fichiers_recents(client, seen, db, agent):
    file_id = upload(client, make_pdf(), "facture.pdf").get_json()["file"]["id"]
    chat_id = send(client, "Lis", [file_id]).get_json()["chat_id"]
    history = agent.groq_history(db_user_id(db), chat_id)
    assert "150 000" in history[0][0]


def test_mode_prive_sans_fichier(client, seen, db, agent):
    assert agent.history_turns(db_user_id(db), 5, None, True) == []
