"""Fichiers créés par FOX (phase J2) : PDF, Word, Excel, zip."""
import io
import zipfile

import pytest

import files
from test_app_commercial import OTHER, OWNER, alice, call, client, friend  # noqa: F401
from test_files import JPEG, db_user_id, send, upload

@pytest.fixture
def creations(db):
    import creations as module

    return module


LONG = """# Rapport de chantier
Le **mur nord** est terminé.

## Tâches
- Peinture
- Électricité
1. Sol
2. Plafond

| Poste | Montant (FCFA) |
|---|---|
| Peinture | 150 000 |
| Main d'œuvre | 50 000 |
"""


# --- Lecture du Markdown léger ----------------------------------------------------------------------
def test_parse_blocks(creations):
    blocks = creations.parse_blocks(LONG)
    assert [b[0] for b in blocks] == ["h1", "p", "h2", "ul", "ol", "table"]
    assert blocks[3][1] == ["Peinture", "Électricité"] and blocks[4][1] == ["Sol", "Plafond"]
    assert blocks[5][1] == [["Poste", "Montant (FCFA)"], ["Peinture", "150 000"], ["Main d'œuvre", "50 000"]]


def test_gras_et_marques(creations):
    assert creations.split_bold("a **b** c") == [("a ", False), ("b", True), (" c", False)]
    assert creations.strip_marks("**x** `y`") == "x y"


def test_noms_de_fichier(creations):
    assert creations.file_name("Rapport.pdf", "t", "pdf") == "Rapport.pdf"
    assert creations.file_name(None, "Devis M. Dupont", "docx") == "Devis M. Dupont.docx"
    assert creations.file_name("../../etc/passwd", "t", "pdf") == "passwd.pdf"
    assert creations.file_name("a/b:c*?.xlsx", "t", "xlsx") == "b c.xlsx" or creations.file_name("a/b:c*?.xlsx", "t", "xlsx").endswith(".xlsx")
    assert creations.file_name("", "", "pdf") == "document.pdf"


def test_contenu_vide_ou_trop_long(creations):
    with pytest.raises(creations.CreationError):
        creations.check_content("  ")
    with pytest.raises(creations.CreationError):
        creations.check_content("x" * (creations.MAX_CONTENT_CHARS + 1))


# --- Les fichiers produits sont valides et relisibles par FOX ---------------------------------------
def test_pdf_valide_et_relisible(creations):
    data = creations.make_pdf("Rapport → final ≥ 3", LONG)
    assert data.startswith(b"%PDF")
    kind, mime = files.sniff(data)
    assert kind == "pdf"
    text, total, read = files._pdf_text(data)
    assert "Rapport" in text and "Peinture" in text and "150 000" in text


def test_pdf_caracteres_hors_police(creations):
    assert creations.make_pdf("Emoji 😀 ✓", "Texte 😀 avec ≈ et — tiret").startswith(b"%PDF")
    assert creations.latin("a→b ’ 😀") == "a->b ' ?"


def test_word_valide_et_relisible(creations):
    data = creations.make_docx("Rapport", LONG)
    kind, _ = files.sniff(data)
    assert kind == "word"
    text = files.read_text("word", data)
    assert "mur nord" in text and "Peinture" in text and "150 000" in text and "**" not in text


def test_excel_valide_nombres_et_formules(creations):
    content = "Poste | Quantité | Prix\nPeinture | 2 | 15 000\nSol | 1,5 | 50000\nTotal | =SUM(B2:B3) | =B2*C2\nPiège | =HYPERLINK(\"http://x\") | =cmd|' /C calc'!A0"
    data = creations.make_xlsx(content)
    assert files.sniff(data)[0] == "excel"
    import openpyxl

    sheet = openpyxl.load_workbook(io.BytesIO(data)).active
    assert sheet["A1"].font.bold and sheet["B2"].value == 2 and sheet["C2"].value == 15000 and sheet["B3"].value == 1.5
    assert sheet["B4"].value == "=SUM(B2:B3)" and sheet["C4"].value == "=B2*C2"
    assert sheet["B5"].data_type == "s" and sheet["C5"].data_type == "s"  # formules dangereuses : du texte


def test_excel_plusieurs_feuilles(creations):
    data = creations.make_xlsx("## Janvier\nA | B\n1 | 2\n## Février\nA | B\n3 | 4\n## Janvier\nX | Y\n5 | 6")
    import openpyxl

    names = openpyxl.load_workbook(io.BytesIO(data)).sheetnames
    assert names[:2] == ["Janvier", "Février"] and len(set(n.lower() for n in names)) == 3


def test_excel_sans_donnees(creations):
    with pytest.raises(creations.CreationError):
        creations.make_xlsx("# Titre seul")


def test_noms_uniques_dans_l_archive(creations):
    assert creations.unique_names(["a.pdf", "A.pdf", "b"]) == ["a.pdf", "A (2).pdf", "b"]


# --- Intégration au chat -----------------------------------------------------------------------------
@pytest.fixture
def web(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "from_web", lambda: True)


@pytest.fixture
def maker(agent, monkeypatch):
    """Faux modèle : appelle les outils demandés, comme le ferait Gemini."""
    plan = {"calls": []}

    def fake_chat(user_id, text, role="owner", chat_id=None, private=False, attachments=None):
        outcomes = [agent.dispatch_tool_call(user_id, role, name, args) for name, args in plan["calls"]]
        plan["outcomes"] = outcomes
        return "Voilà.", 0, []

    monkeypatch.setattr(agent, "chat", fake_chat)
    return plan


def test_le_fichier_cree_est_joint_a_la_reponse(client, web, maker):
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    r = send(client, "Fais-moi un PDF")
    body = r.get_json()
    assert r.status_code == 200 and len(body["files"]) == 1
    info = body["files"][0]
    assert info["name"] == "Rapport.pdf" and info["kind"] == "pdf" and info["size"] > 500
    download = call(client, "GET", f"/files/{info['id']}")
    assert download.status_code == 200 and download.data.startswith(b"%PDF")
    assert "attachment" in download.headers["Content-Disposition"]
    messages = call(client, "GET", f"/chats/{body['chat_id']}/messages").get_json()["messages"]
    assert messages[1]["role"] == "assistant" and messages[1]["files"] == [info]


def test_sans_fichier_la_reponse_a_une_liste_vide(client, web, maker):
    assert send(client, "Bonjour").get_json()["files"] == []


def test_plusieurs_fichiers_puis_zip_des_fichiers_de_la_conversation(client, web, maker):
    uploaded = upload(client, JPEG, "mur.jpg").get_json()["file"]["id"]
    maker["calls"] = [
        ("creer_pdf", {"titre": "Rapport", "contenu": LONG}),
        ("creer_excel", {"titre": "Budget", "contenu": "A | B\n1 | 2"}),
        ("creer_archive", {"nom_fichier": "dossier", "fichiers": ["Rapport.pdf", "budget.xlsx", "mur.jpg"]}),
    ]
    body = send(client, "Prépare le dossier", [uploaded]).get_json()
    assert [f["name"] for f in body["files"]] == ["Rapport.pdf", "Budget.xlsx", "dossier.zip"]
    zip_id = body["files"][2]["id"]
    archive = zipfile.ZipFile(io.BytesIO(call(client, "GET", f"/files/{zip_id}").data))
    assert sorted(archive.namelist()) == ["Budget.xlsx", "Rapport.pdf", "mur.jpg"]
    assert all(kind == "result" for kind, _ in maker["outcomes"])


def test_archive_fichier_inconnu(client, web, maker):
    maker["calls"] = [("creer_archive", {"nom_fichier": "x", "fichiers": ["absent.pdf"]})]
    body = send(client, "zip").get_json()
    kind, value = maker["outcomes"][0]
    assert kind == "error" and "introuvables : absent.pdf" in value and body["files"] == []


def test_archive_sans_nom_de_fichier(client, web, maker):
    maker["calls"] = [("creer_archive", {"nom_fichier": "x", "fichiers": []})]
    send(client, "zip")
    assert maker["outcomes"][0][0] == "error"


def test_telegram_est_prevenu_sans_fichier_perdu(client, maker, db):
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    body = send(client, "Un PDF").get_json()  # jeton de service = Telegram
    kind, value = maker["outcomes"][0]
    assert kind == "error" and "interface web" in value and body["files"] == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM files")
        assert cur.fetchone()[0] == 0


def test_stockage_plein(client, web, maker, monkeypatch, app_module):
    monkeypatch.setattr(files, "OWNER_STORAGE_BYTES", 1000)
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    body = send(client, "Un PDF").get_json()
    kind, value = maker["outcomes"][0]
    assert kind == "error" and "stockage" in value and body["files"] == []


def test_fichier_trop_gros(client, web, maker, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 600)
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    send(client, "Un PDF")
    assert maker["outcomes"][0][0] == "error" and "dépasse" in maker["outcomes"][0][1]


def test_supprimer_la_conversation_supprime_les_fichiers_crees(client, web, maker, db):
    maker["calls"] = [("creer_word", {"titre": "Lettre", "contenu": "Bonjour"})]
    body = send(client, "Une lettre").get_json()
    file_id = body["files"][0]["id"]
    assert call(client, "GET", f"/files/{file_id}").status_code == 200
    assert call(client, "DELETE", f"/chats/{body['chat_id']}").status_code == 200
    assert call(client, "GET", f"/files/{file_id}").status_code == 404


def test_fichier_cree_non_enregistre_est_purge_comme_un_envoi_oublie(client, web, maker, db):
    db.set_private(db_user_id(db), True)
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    send(client, "Un PDF")  # mode privé : rien dans l'historique, donc rien de « joint »
    with db.cursor() as cur:
        cur.execute("SELECT attached FROM files")
        assert [r[0] for r in cur.fetchall()] == [False]


def test_chaque_utilisateur_ne_voit_que_ses_fichiers(client, web, maker, friend):
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    file_id = send(client, "Un PDF").get_json()["files"][0]["id"]
    assert call(client, "GET", f"/files/{file_id}", telegram_id=OTHER).status_code == 404
    maker["calls"] = [("creer_archive", {"nom_fichier": "x", "fichiers": ["Rapport.pdf"]})]
    send(client, "zip", telegram_id=OTHER)
    assert maker["outcomes"][0][0] == "error"  # le fichier de l'autre n'existe pas pour lui


def test_les_outils_sont_proposes_aux_deux_roles(agent):
    for role in ("owner", "tester"):
        names = {t.name for t in agent.exposed_tools(role)}
        assert {"creer_pdf", "creer_word", "creer_excel", "creer_archive"} <= names


def test_les_fichiers_crees_sont_relisibles_dans_la_memoire(client, web, maker, agent, db):
    """Un PDF créé par FOX, renvoyé ensuite par l'utilisateur, se relit comme tout PDF."""
    maker["calls"] = [("creer_pdf", {"titre": "Rapport", "contenu": LONG})]
    info = send(client, "Un PDF").get_json()["files"][0]
    data = call(client, "GET", f"/files/{info['id']}").data
    assert "Peinture" in files.prepare([{"name": "r.pdf", "kind": "pdf", "mime": "application/pdf", "data": data}], can_see=False).text_only()
