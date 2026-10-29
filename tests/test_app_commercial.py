"""Routes et base de données des clients et devis (phase C)."""
import pytest

TOKEN = "jeton-de-service-de-test"
HEADERS = {"X-Fox-Service-Token": TOKEN}
OWNER = 4242
OTHER = 777001


@pytest.fixture
def alice(make_owner):
    return make_owner(OWNER)["id"]


@pytest.fixture
def friend(db, accounts):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, role, telegram_id) VALUES ('ami', 'tester', %s) RETURNING id", (OTHER,))
        return cur.fetchone()[0]


@pytest.fixture
def client(app_module, alice, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_TOKEN", TOKEN)
    return app_module.app.test_client()


def call(client, method, path, telegram_id=OWNER, **payload):
    if method == "GET":
        sep = "&" if "?" in path else "?"
        return client.get(f"{path}{sep}telegram_id={telegram_id}", headers=HEADERS)
    return getattr(client, method.lower())(path, json={"telegram_id": telegram_id, **payload}, headers=HEADERS)


QUOTE = "Paul Martin\nPeinture | 2 | 15000\nMain d'oeuvre | 1,5 | 50000"


# --- Accès ------------------------------------------------------------------------------------
@pytest.mark.parametrize("method, path", [
    ("GET", "/clients"), ("GET", "/clients/find?q=x"), ("POST", "/clients"), ("GET", "/clients/1"),
    ("PATCH", "/clients/1"), ("DELETE", "/clients/1"), ("GET", "/quotes"), ("POST", "/quotes"),
    ("GET", "/quotes/1"), ("PATCH", "/quotes/1"), ("DELETE", "/quotes/1"),
])
def test_les_routes_exigent_une_identite(client, method, path):
    assert call(client, method, path, telegram_id=999).status_code == 403
    if method != "GET":
        assert client.open(path, method=method, json={}).status_code == 401
        assert client.open(path, method=method, data="x", headers=HEADERS).status_code == 415


# --- Clients -----------------------------------------------------------------------------------------
def test_creer_et_lire_un_client(client):
    response = call(client, "POST", "/clients", text="Paul Martin ; 699123456 ; paul@x.fr ; Douala")
    assert response.status_code == 201
    created = response.get_json()["client"]
    assert created == {"id": created["id"], "name": "Paul Martin", "phone": "699123456", "email": "paul@x.fr",
                       "address": "Douala", "notes": None}
    assert call(client, "GET", f"/clients/{created['id']}").get_json()["client"] == created
    assert call(client, "GET", "/clients").get_json()["clients"] == [created]


def test_creer_un_client_par_champs(client):
    response = call(client, "POST", "/clients", name="Marie", notes="Cliente fidèle")
    assert response.get_json()["client"]["notes"] == "Cliente fidèle"


@pytest.mark.parametrize("payload", [{"text": ""}, {"text": "Paul ; abc"}, {"name": ""}, {"name": "A", "email": "zz"}, {}])
def test_client_invalide(client, payload):
    assert call(client, "POST", "/clients", **payload).status_code == 400


def test_client_en_double_meme_avec_une_autre_casse(client):
    call(client, "POST", "/clients", text="Paul")
    response = call(client, "POST", "/clients", text="PAUL")
    assert response.status_code == 409 and "déjà" in response.get_json()["error"]


def test_limite_de_clients(client, db, monkeypatch):
    monkeypatch.setattr(db, "MAX_CLIENTS", 2)
    assert [call(client, "POST", "/clients", text=n).status_code for n in ("A", "B", "C")] == [201, 201, 409]


def test_recherche_de_clients(client):
    for name in ("Paul Martin", "Paul Durand", "Marie", "100%_vrai"):
        call(client, "POST", "/clients", text=name)
    names = lambda q: [c["name"] for c in call(client, "GET", f"/clients?q={q}").get_json()["clients"]]  # noqa: E731
    assert names("paul") == ["Paul Durand", "Paul Martin"]
    assert names("MARI") == ["Marie"]
    assert names("%25") == ["100%_vrai"]  # les jokers saisis sont littéraux
    assert names("_") == ["100%_vrai"]
    assert names("zzz") == []


def test_trouver_un_client_exact_ou_partiel(client):
    for name in ("Paul", "Paul Martin"):
        call(client, "POST", "/clients", text=name)
    exact = call(client, "GET", "/clients/find?q=PAUL").get_json()["clients"]
    assert [c["name"] for c in exact] == ["Paul"]  # la correspondance exacte l'emporte
    assert len(call(client, "GET", "/clients/find?q=Pau").get_json()["clients"]) == 2
    assert call(client, "GET", "/clients/find?q=").status_code == 400


def test_modifier_un_client(client):
    cid = call(client, "POST", "/clients", text="Paul ; 699123456 ; p@x.fr").get_json()["client"]["id"]
    response = call(client, "PATCH", f"/clients/{cid}", text=" ; - ; Yaoundé")
    assert response.get_json()["client"] == {"id": cid, "name": "Paul", "phone": "699123456", "email": None,
                                             "address": "Yaoundé", "notes": None}
    assert call(client, "PATCH", f"/clients/{cid}", name="Paul Martin").get_json()["client"]["name"] == "Paul Martin"
    assert call(client, "PATCH", f"/clients/{cid}", text="").status_code == 400
    assert call(client, "PATCH", f"/clients/{cid}").status_code == 400
    assert call(client, "PATCH", "/clients/999", text="699123456").status_code == 404


def test_renommer_en_double_refuse(client):
    call(client, "POST", "/clients", text="Marie")
    cid = call(client, "POST", "/clients", text="Paul").get_json()["client"]["id"]
    assert call(client, "PATCH", f"/clients/{cid}", name="marie").status_code == 409
    assert call(client, "PATCH", f"/clients/{cid}", name="PAUL").status_code == 200  # son propre nom


def test_supprimer_un_client_garde_ses_devis(client):
    cid = call(client, "POST", "/clients", text="Paul Martin").get_json()["client"]["id"]
    quote = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]
    assert quote["client_id"] == cid
    assert call(client, "DELETE", f"/clients/{cid}").status_code == 200
    assert call(client, "DELETE", f"/clients/{cid}").status_code == 404
    kept = call(client, "GET", f"/quotes/{quote['number']}").get_json()["quote"]
    assert kept["client"] == "Paul Martin" and kept["client_id"] is None


def test_chacun_ses_clients_et_ses_devis(client, friend):
    cid = call(client, "POST", "/clients", text="Paul").get_json()["client"]["id"]
    quote = call(client, "POST", "/quotes", text="Paul\nA | 1 | 100").get_json()["quote"]
    assert call(client, "GET", "/clients", telegram_id=OTHER).get_json()["clients"] == []
    assert call(client, "GET", "/clients/find?q=Paul", telegram_id=OTHER).get_json()["clients"] == []
    assert call(client, "GET", f"/clients/{cid}", telegram_id=OTHER).status_code == 404
    assert call(client, "PATCH", f"/clients/{cid}", telegram_id=OTHER, text="699123456").status_code == 404
    assert call(client, "DELETE", f"/clients/{cid}", telegram_id=OTHER).status_code == 404
    assert call(client, "GET", "/quotes", telegram_id=OTHER).get_json()["quotes"] == []
    assert call(client, "GET", f"/quotes/{quote['number']}", telegram_id=OTHER).status_code == 404
    assert call(client, "PATCH", f"/quotes/{quote['number']}", telegram_id=OTHER, status="accepté").status_code == 404
    assert call(client, "DELETE", f"/quotes/{quote['number']}", telegram_id=OTHER).status_code == 404
    # le même nom est permis chez deux utilisateurs, et leurs numéros sont indépendants
    assert call(client, "POST", "/clients", telegram_id=OTHER, text="Paul").status_code == 201
    other = call(client, "POST", "/quotes", telegram_id=OTHER, text="Paul\nA | 1 | 100").get_json()["quote"]
    assert other["number"] == quote["number"] and other["id"] != quote["id"]
    assert call(client, "GET", f"/quotes/{quote['number']}").get_json()["quote"]["id"] == quote["id"]


# --- Devis ----------------------------------------------------------------------------------------------------
def test_creer_un_devis_calcule_les_totaux(client):
    response = call(client, "POST", "/quotes", text=QUOTE)
    assert response.status_code == 201
    data = response.get_json()
    quote = data["quote"]
    assert data["registered"] is False  # « Paul Martin » n'a pas de fiche
    assert quote["number"].startswith("DV-") and quote["number"].endswith("-001")
    assert quote["status"] == "brouillon" and quote["client"] == "Paul Martin" and quote["client_id"] is None
    assert quote["vat_rate"] == "19,25" and quote["currency"] == "XAF"
    assert [ln["total"] for ln in quote["lines"]] == ["30 000 XAF", "75 000 XAF"]
    assert quote["subtotal"] == "105 000 XAF" and quote["vat"] == "20 213 XAF" and quote["total"] == "125 213 XAF"
    assert quote["amounts"] == {"subtotal": "105000", "vat": "20213", "total": "125213"}


def test_le_devis_se_relie_a_la_fiche_client(client):
    cid = call(client, "POST", "/clients", text="Paul Martin").get_json()["client"]["id"]
    data = call(client, "POST", "/quotes", text="paul martin\nA | 1 | 100").get_json()
    assert data["registered"] is True and data["quote"]["client_id"] == cid and data["quote"]["client"] == "Paul Martin"
    assert call(client, "POST", "/quotes", text="Pau\nA | 1 | 100").get_json()["quote"]["client_id"] == cid  # nom partiel unique


def test_client_ambigu_refuse(client):
    call(client, "POST", "/clients", text="Paul Martin")
    call(client, "POST", "/clients", text="Paul Durand")
    response = call(client, "POST", "/quotes", text="Paul\nA | 1 | 100")
    assert response.status_code == 409 and "Paul Durand, Paul Martin" in response.get_json()["error"]
    assert call(client, "GET", "/quotes").get_json()["quotes"] == []


def test_tva_et_note_du_devis(client):
    quote = call(client, "POST", "/quotes", text="Paul\nA | 1 | 1000\nTVA 0\nnote : Merci").get_json()["quote"]
    assert quote["vat_rate"] == "0" and quote["vat"] == "0 XAF" and quote["total"] == "1 000 XAF" and quote["notes"] == "Merci"


@pytest.mark.parametrize("text", ["", "Paul", "Paul\nA | 1", "Paul\nA | 0 | 5"])
def test_devis_invalide(client, text):
    assert call(client, "POST", "/quotes", text=text).status_code == 400
    assert call(client, "GET", "/quotes").get_json()["quotes"] == []


def test_numerotation_continue_et_jamais_reutilisee(client):
    numbers = [call(client, "POST", "/quotes", text="P\nA | 1 | 1").get_json()["quote"]["number"] for _ in range(3)]
    assert [n[-3:] for n in numbers] == ["001", "002", "003"]
    assert call(client, "DELETE", f"/quotes/{numbers[2]}").status_code == 200
    assert call(client, "POST", "/quotes", text="P\nA | 1 | 1").get_json()["quote"]["number"][-3:] == "004"


def test_numerotation_par_annee(db, alice):
    kwargs = dict(client_id=None, client_name="P", lines=[{"description": "A", "quantity": 1, "unit_price": 1}], vat_rate=0, currency="XAF")
    assert db.create_quote(alice, year=2025, **kwargs)["number"] == "DV-2025-001"
    assert db.create_quote(alice, year=2025, **kwargs)["number"] == "DV-2025-002"
    assert db.create_quote(alice, year=2026, **kwargs)["number"] == "DV-2026-001"


def test_lire_un_devis_par_numero_complet_ou_court(client):
    quote = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]
    assert call(client, "GET", f"/quotes/{quote['number']}").get_json()["quote"]["id"] == quote["id"]
    assert call(client, "GET", f"/quotes/{quote['number'].lower()}").get_json()["quote"]["id"] == quote["id"]
    assert call(client, "GET", "/quotes/1").get_json()["quote"]["id"] == quote["id"]
    assert call(client, "GET", "/quotes/001").get_json()["quote"]["id"] == quote["id"]
    assert call(client, "GET", "/quotes/2").status_code == 404
    assert call(client, "GET", "/quotes/DV-1999-001").status_code == 404
    assert call(client, "GET", "/quotes/abc").status_code == 404


def test_liste_des_devis_recents_d_abord_avec_filtre(client):
    first = call(client, "POST", "/quotes", text="A\nX | 1 | 100").get_json()["quote"]
    second = call(client, "POST", "/quotes", text="B\nX | 2 | 100").get_json()["quote"]
    call(client, "PATCH", f"/quotes/{first['number']}", status="envoyé")
    listed = call(client, "GET", "/quotes").get_json()["quotes"]
    assert [q["number"] for q in listed] == [second["number"], first["number"]]
    assert listed[0]["total"] == "239 XAF" and listed[0]["client"] == "B"
    assert [q["number"] for q in call(client, "GET", "/quotes?status=envoye").get_json()["quotes"]] == [first["number"]]
    assert call(client, "GET", "/quotes?status=perdu").status_code == 400


def test_changer_le_statut(client):
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    for word, status in (("envoyé", "envoye"), ("accepté", "accepte"), ("refusé", "refuse"), ("brouillon", "brouillon")):
        assert call(client, "PATCH", f"/quotes/{number}", status=word).get_json()["quote"]["status"] == status
    assert call(client, "PATCH", f"/quotes/{number}", status="perdu").status_code == 400
    assert call(client, "PATCH", f"/quotes/{number}").status_code == 400
    assert call(client, "PATCH", "/quotes/DV-2026-999", status="envoyé").status_code == 404


def test_suppression_limitee_aux_brouillons_et_refus(client):
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    for status in ("envoyé", "accepté"):
        call(client, "PATCH", f"/quotes/{number}", status=status)
        response = call(client, "DELETE", f"/quotes/{number}")
        assert response.status_code == 409 and "refusé" in response.get_json()["error"]
    call(client, "PATCH", f"/quotes/{number}", status="refusé")
    assert call(client, "DELETE", f"/quotes/{number}").status_code == 200
    assert call(client, "DELETE", f"/quotes/{number}").status_code == 404
    assert call(client, "GET", "/quotes").get_json()["quotes"] == []


def test_suppression_efface_les_lignes(client, db):
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    call(client, "DELETE", f"/quotes/{number}")
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM quote_lines")
        assert cur.fetchone()[0] == 0


def test_limite_de_devis(client, db, monkeypatch):
    monkeypatch.setattr(db, "MAX_QUOTES", 2)
    assert [call(client, "POST", "/quotes", text="P\nA | 1 | 1").status_code for _ in range(3)] == [201, 201, 409]


def test_les_lignes_gardent_leur_ordre(client):
    text = "P\n" + "\n".join(f"Ligne {i} | 1 | {i}" for i in (3, 1, 2))
    lines = call(client, "POST", "/quotes", text=text).get_json()["quote"]["lines"]
    assert [ln["description"] for ln in lines] == ["Ligne 3", "Ligne 1", "Ligne 2"]


def test_le_journal_ne_garde_que_le_fait(client, db, alice):
    import journal

    call(client, "POST", "/clients", text="Client très confidentiel ; 699123456")
    number = call(client, "POST", "/quotes", text="Client très confidentiel\nSecret | 1 | 123456").get_json()["quote"]["number"]
    call(client, "PATCH", f"/quotes/{number}", status="refusé")
    call(client, "DELETE", f"/quotes/{number}")
    cid = call(client, "GET", "/clients").get_json()["clients"][0]["id"]
    call(client, "PATCH", f"/clients/{cid}", text="699000000")
    call(client, "DELETE", f"/clients/{cid}")
    assert {e["text"] for e in journal.entries(alice, 20)} == {
        "Client ajouté", "Client modifié", "Client supprimé", "Devis créé", "Statut de devis modifié", "Devis supprimé"}
    with db.cursor() as cur:
        cur.execute("SELECT details::text FROM audit_log WHERE event LIKE 'client_%' OR event LIKE 'quote_%'")
        assert not any(w in " ".join(r[0] for r in cur.fetchall()) for w in ("confidentiel", "Secret", "699"))


# --- PDF, e-mail et profil d'entreprise (C3) -----------------------------------------------------
@pytest.mark.parametrize("method, path", [("GET", "/quotes/1/pdf"), ("GET", "/quotes/1/mail"), ("GET", "/business"),
                                          ("PUT", "/business"), ("DELETE", "/business")])
def test_routes_c3_exigent_une_identite(client, method, path):
    assert call(client, method, path, telegram_id=999).status_code == 403


def test_pdf_du_devis(client, db, alice):
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    response = call(client, "GET", f"/quotes/{number}/pdf")
    assert response.status_code == 200 and response.mimetype == "application/pdf" and response.data.startswith(b"%PDF-")
    assert number in response.headers["Content-Disposition"] and response.headers["X-Quote-Number"] == number
    assert call(client, "GET", "/quotes/1/pdf").status_code == 200
    assert call(client, "GET", "/quotes/DV-2026-999/pdf").status_code == 404


def test_pdf_utilise_le_profil_et_la_fiche_client(client, monkeypatch, app_module):
    seen = {}
    monkeypatch.setattr(app_module.quote_pdf, "render", lambda quote, business, client: seen.update(quote=quote, business=business, client=client) or b"%PDF-x")
    call(client, "POST", "/clients", text="Paul Martin ; 699123456")
    call(client, "PUT", "/business", text="Atelier Fox ; ; ; Douala")
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    call(client, "GET", f"/quotes/{number}/pdf")
    assert seen["business"]["name"] == "Atelier Fox" and seen["client"]["phone"] == "699123456" and seen["quote"]["total"].endswith("XAF")


def test_pdf_chacun_le_sien(client, friend):
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    assert call(client, "GET", f"/quotes/{number}/pdf", telegram_id=OTHER).status_code == 404
    assert call(client, "GET", f"/quotes/{number}/mail", telegram_id=OTHER).status_code == 404


def test_mail_du_devis(client):
    call(client, "POST", "/clients", text="Paul Martin ; ; paul@x.fr")
    call(client, "PUT", "/business", text="Atelier Fox")
    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    mail = call(client, "GET", f"/quotes/{number}/mail").get_json()
    assert mail["to"] == "paul@x.fr" and mail["subject"] == f"Devis {number}"
    assert "125 213 XAF" in mail["body"] and mail["body"].endswith("Atelier Fox")
    assert call(client, "GET", "/quotes/DV-2026-999/mail").status_code == 404


def test_profil_d_entreprise(client):
    assert call(client, "GET", "/business").get_json() == {"business": None}
    response = call(client, "PUT", "/business", text="Atelier Fox ; 699 123 456 ; a@b.fr ; Douala ; M0123")
    expected = {"name": "Atelier Fox", "phone": "699 123 456", "email": "a@b.fr", "address": "Douala", "tax_id": "M0123"}
    assert response.get_json()["business"] == expected and call(client, "GET", "/business").get_json()["business"] == expected
    # un nouvel enregistrement remplace tout, il ne fusionne pas
    assert call(client, "PUT", "/business", text="Nouveau").get_json()["business"] == {
        "name": "Nouveau", "phone": None, "email": None, "address": None, "tax_id": None}
    assert call(client, "PUT", "/business", text=" ; 1").status_code == 400
    assert call(client, "PUT", "/business").status_code == 400
    assert call(client, "DELETE", "/business").status_code == 200
    assert call(client, "DELETE", "/business").status_code == 404
    assert call(client, "GET", "/business").get_json() == {"business": None}


def test_profil_isole_par_utilisateur(client, friend):
    call(client, "PUT", "/business", text="Chez Alice")
    assert call(client, "GET", "/business", telegram_id=OTHER).get_json() == {"business": None}
    call(client, "PUT", "/business", telegram_id=OTHER, text="Chez l'ami")
    assert call(client, "GET", "/business").get_json()["business"]["name"] == "Chez Alice"
    assert call(client, "DELETE", "/business", telegram_id=OTHER).status_code == 200
    assert call(client, "GET", "/business").get_json()["business"]["name"] == "Chez Alice"


def test_journal_c3(client, db, alice):
    import journal

    number = call(client, "POST", "/quotes", text=QUOTE).get_json()["quote"]["number"]
    call(client, "GET", f"/quotes/{number}/pdf")
    call(client, "PUT", "/business", text="Entreprise très secrète ; 699123456")
    texts = {e["text"] for e in journal.entries(alice, 20)}
    assert {"Devis exporté en PDF", "Profil d'entreprise modifié"} <= texts
    with db.cursor() as cur:
        cur.execute("SELECT details::text FROM audit_log WHERE event IN ('business_updated', 'quote_pdf')")
        assert not any("secrète" in r[0] or "699123456" in r[0] for r in cur.fetchall())
