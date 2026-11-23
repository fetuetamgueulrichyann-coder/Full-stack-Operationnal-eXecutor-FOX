"""Fichiers créés par FOX (phase J2) : PDF, Word, Excel et archives .zip.

Les outils fabriquent le fichier, l'enregistrent dans l'espace de l'utilisateur (même plafond que ses envois) et
le déposent dans la « boîte de sortie » de la requête en cours ; l'API les joint à la réponse (cartes de téléchargement).
Rien ne sort de FOX : aucune confirmation n'est nécessaire.

Le texte est donné par le modèle en Markdown léger : « # Titre », « ## Sous-titre », « - puce », « 1. numéro »,
« | colonne | colonne | » pour un tableau, « **gras** », une ligne vide entre deux paragraphes.
"""
import io
import re
import zipfile
from contextvars import ContextVar
from xml.sax.saxutils import escape

import db
import files
from tools import READ, Tool, register

MAX_CONTENT_CHARS = 30000
MAX_TABLE_ROWS = 300
MAX_TABLE_COLS = 12
MAX_SHEETS, MAX_SHEET_ROWS, MAX_SHEET_COLS = 8, 1000, 30
MAX_ARCHIVE_FILES = 20
MAX_TITLE = 120

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP_MIME = "application/zip"
KINDS = {PDF_MIME: "pdf", DOCX_MIME: "word", XLSX_MIME: "excel", ZIP_MIME: "archive"}


class CreationError(Exception):
    """Demande impossible ; le message est lisible par le modèle (et corrigeable par lui)."""


# --- Boîte de sortie de la requête en cours -----------------------------------------------------------
_BOX = ContextVar("fox_creations", default=None)


def begin(cap_bytes, web=True):
    """Ouvre la boîte de sortie d'une requête. Retourne le jeton pour end()."""
    return _BOX.set({"files": [], "cap": cap_bytes, "web": web})


def collect():
    """Fichiers créés pendant la requête (descriptions publiques)."""
    box = _BOX.get()
    return list(box["files"]) if box else []


def end(token):
    _BOX.reset(token)


def _box():
    box = _BOX.get()
    if box is None:
        raise CreationError("la création de fichiers n'est pas disponible ici")
    if not box["web"]:
        raise CreationError(
            "la création de fichiers n'est disponible que dans l'interface web pour le moment : "
            "dis à l'utilisateur de continuer sur le site de FOX pour recevoir son fichier"
        )
    return box


def store(user_id, name, mime, data):
    """Enregistre un fichier créé et l'ajoute à la boîte de sortie."""
    box = _box()
    if len(data) > files.MAX_FILE_BYTES:
        raise CreationError(f"le fichier dépasse {files.MAX_FILE_BYTES // files.MB} Mo : réduis son contenu")
    try:
        file_id = db.add_file(user_id, name, mime, KINDS[mime], data, box["cap"])
    except db.StorageFull:
        raise CreationError("l'espace de stockage de l'utilisateur est plein : il doit supprimer d'anciens fichiers")
    info = {"id": file_id, "name": name, "kind": KINDS[mime], "size": len(data)}
    box["files"].append(info)
    return info


# --- Noms et texte --------------------------------------------------------------------------------------
def file_name(wanted, title, extension):
    base = re.split(r"[\\/]", (wanted or title or "document").strip())[-1]
    base = re.sub(r"\.(pdf|docx?|xlsx?|zip)$", "", base, flags=re.I)
    base = files.clean_name(re.sub(r"[\\/:*?\"<>|]+", " ", base)).strip(" .") or "document"
    return f"{base[:80].strip()}.{extension}"


def check_content(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise CreationError("le contenu est vide")
    if len(text) > MAX_CONTENT_CHARS:
        raise CreationError(f"contenu trop long ({len(text)} caractères, maximum {MAX_CONTENT_CHARS}) : découpe en plusieurs fichiers")
    return text


# --- Lecture du Markdown léger --------------------------------------------------------------------------
_HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*\S)\s*$")
_NUMBER = re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def split_row(line):
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells[:MAX_TABLE_COLS]


def parse_blocks(text):
    """Texte -> [("h1"|"h2"|"h3"|"p", texte), ("ul"|"ol", [items]), ("table", [lignes])]."""
    blocks, paragraph, items, kind, rows = [], [], [], None, []

    def flush():
        nonlocal paragraph, items, kind, rows
        if paragraph:
            blocks.append(("p", " ".join(paragraph)))
        if items:
            blocks.append((kind, items))
        if rows:
            blocks.append(("table", rows[:MAX_TABLE_ROWS]))
        paragraph, items, kind, rows = [], [], None, []

    for line in text.split("\n"):
        heading, bullet, number = _HEADING.match(line), _BULLET.match(line), _NUMBER.match(line)
        if not line.strip():
            flush()
        elif heading:
            flush()
            blocks.append((f"h{len(heading.group(1))}", heading.group(2)))
        elif line.strip().startswith("|") and line.count("|") >= 2:
            if paragraph or items:
                flush()
            if not _TABLE_SEP.match(line):
                rows.append(split_row(line))
        elif bullet or number:
            wanted = "ul" if bullet else "ol"
            if paragraph or rows or (items and kind != wanted):
                flush()
            kind = wanted
            items.append((bullet or number).group(1))
        else:
            if items or rows:
                flush()
            paragraph.append(line.strip())
    flush()
    return blocks


def split_bold(text):
    """« a **b** c » -> [("a ", False), ("b", True), (" c", False)]."""
    parts = re.split(r"\*\*(.+?)\*\*", text)
    return [(part, index % 2 == 1) for index, part in enumerate(parts) if part]


def strip_marks(text):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "")


# --- PDF -------------------------------------------------------------------------------------------------
_LATIN = {"→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≠": "!=", "≈": "~", "✓": "v", "✔": "v", "×": "x", "—": "-", "–": "-",
          " ": " ", " ": " ", " ": " ", "’": "'", "‘": "'", "“": '"', "”": '"'}


def latin(text):
    """Texte que les polices standard du PDF savent écrire (le reste devient « ? »)."""
    text = "".join(_LATIN.get(c, c) for c in str(text))
    return text.encode("cp1252", "replace").decode("cp1252")


def make_pdf(title, text):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def rich(value):
        body = escape(latin(value))
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body).replace("`", "")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=latin(title), author="FOX")
    base = getSampleStyleSheet()["Normal"]
    normal = ParagraphStyle("n", parent=base, fontSize=10.5, leading=14.5, spaceAfter=6)
    cell = ParagraphStyle("c", parent=normal, fontSize=9, leading=11.5, spaceAfter=0)
    cell_bold = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")
    heads = {
        "h1": ParagraphStyle("h1", parent=normal, fontName="Helvetica-Bold", fontSize=17, leading=21, spaceBefore=10, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=normal, fontName="Helvetica-Bold", fontSize=13.5, leading=17, spaceBefore=8, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=normal, fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=6, spaceAfter=4),
    }
    story = [Paragraph(rich(title), ParagraphStyle("t", parent=heads["h1"], fontSize=22, leading=27, spaceAfter=14)), Spacer(1, 2)]
    for kind, value in parse_blocks(text):
        if kind in heads:
            story.append(Paragraph(rich(value), heads[kind]))
        elif kind == "p":
            story.append(Paragraph(rich(value), normal))
        elif kind in ("ul", "ol"):
            story.append(ListFlowable([ListItem(Paragraph(rich(v), normal)) for v in value],
                                      bulletType="bullet" if kind == "ul" else "1", leftIndent=14, bulletFontSize=9))
        else:
            width = max(len(r) for r in value)
            grid = [[Paragraph(rich(c), cell_bold if i == 0 else cell) for c in row + [""] * (width - len(row))]
                    for i, row in enumerate(value)]
            table = Table(grid, repeatRows=1, colWidths=[(170 * mm) / width] * width)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story += [table, Spacer(1, 8)]
    doc.build(story)
    return buffer.getvalue()


# --- Word -------------------------------------------------------------------------------------------------
def _runs(paragraph, text):
    for part, bold in split_bold(text.replace("`", "")):
        paragraph.add_run(part).bold = bold or None


def make_docx(title, text):
    import docx

    document = docx.Document()
    document.core_properties.title = title[:MAX_TITLE]
    document.core_properties.author = "FOX"
    document.add_heading(title, level=0)
    for kind, value in parse_blocks(text):
        if kind in ("h1", "h2", "h3"):
            document.add_heading(strip_marks(value), level=int(kind[1]))
        elif kind == "p":
            _runs(document.add_paragraph(), value)
        elif kind in ("ul", "ol"):
            for item in value:
                _runs(document.add_paragraph(style="List Bullet" if kind == "ul" else "List Number"), item)
        else:
            width = max(len(r) for r in value)
            table = document.add_table(rows=len(value), cols=width)
            table.style = "Table Grid"
            for r, row in enumerate(value):
                for c in range(width):
                    cell = table.cell(r, c)
                    cell.text = ""
                    run = cell.paragraphs[0].add_run(strip_marks(row[c]) if c < len(row) else "")
                    run.bold = True if r == 0 else None
            document.add_paragraph()
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# --- Excel ------------------------------------------------------------------------------------------------
_NUMBER_CELL = re.compile(r"^-?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?$|^-?\d+(?:[.,]\d+)?$")
_FORMULA = re.compile(r"^=\s*(?:(?:SUM|SOMME|AVERAGE|MOYENNE|MIN|MAX|COUNT|NB|ROUND|ARRONDI)\s*\(|[A-Z]{1,2}\d{1,4}|\d)[A-Z0-9:+\-*/(),.;\s$]*$")


class Text(str):
    """Cellule à garder telle quelle, même si elle commence par « = »."""


def cell_value(raw):
    """Texte d'une cellule -> nombre, formule simple autorisée, ou texte."""
    text = str(raw).strip()
    if _NUMBER_CELL.match(text):
        number = text.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
        return float(number) if "." in number else int(number)
    if text.startswith("="):
        if _FORMULA.match(text) and "!" not in text:
            return text.replace("SOMME", "SUM").replace("MOYENNE", "AVERAGE").replace("ARRONDI", "ROUND").replace(";", ",")
        return Text(text)  # toute autre formule (appel externe, etc.) reste du texte
    return text


def parse_sheets(text):
    """« ## Feuille » puis lignes « a | b | c » -> [(nom, [[valeurs]])]. Sans titre de feuille : une feuille « Feuille1 »."""
    sheets, name, rows = [], None, []

    def close():
        if rows:
            sheets.append((name or f"Feuille{len(sheets) + 1}", list(rows)))

    for line in text.split("\n"):
        heading = _HEADING.match(line)
        if heading:
            close()
            name, rows = heading.group(2), []
        elif not line.strip() or _TABLE_SEP.match(line):
            continue
        else:
            cells = [c.strip() for c in line.strip().strip("|").split("|")] if "|" in line else [c.strip() for c in re.split(r"\t|;", line)]
            rows.append(cells[:MAX_SHEET_COLS])
    close()
    return sheets[:MAX_SHEETS]


def make_xlsx(text):
    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    sheets = parse_sheets(text)
    if not sheets:
        raise CreationError("aucune ligne de données : écris une ligne par rangée, cellules séparées par « | »")
    book = openpyxl.Workbook()
    book.remove(book.active)
    used = set()
    for name, rows in sheets:
        title = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Feuille"
        while title.lower() in used:
            title = (title[:28] + f"{len(used)}")[:31]
        used.add(title.lower())
        sheet = book.create_sheet(title)
        widths = {}
        for r, row in enumerate(rows[:MAX_SHEET_ROWS], start=1):
            for c, raw in enumerate(row, start=1):
                value = cell_value(raw)
                cell = sheet.cell(row=r, column=c, value=value)
                if isinstance(value, Text):
                    cell.data_type = "s"
                if r == 1:
                    cell.font = Font(bold=True)
                widths[c] = max(widths.get(c, 8), min(len(str(raw)) + 2, 50))
        for c, width in widths.items():
            sheet.column_dimensions[get_column_letter(c)].width = width
        sheet.freeze_panes = "A2"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# --- Archive ----------------------------------------------------------------------------------------------
def make_zip(entries):
    """entries : [(nom, octets)] avec noms uniques."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return out.getvalue()


def unique_names(names):
    seen, result = {}, []
    for name in names:
        key = name.lower()
        if key in seen:
            seen[key] += 1
            stem, dot, ext = name.rpartition(".")
            name = f"{stem or name} ({seen[key]}){dot}{ext}" if dot else f"{name} ({seen[key]})"
        else:
            seen[key] = 1
        result.append(name)
    return result


# --- Outils -----------------------------------------------------------------------------------------------
def creer_pdf(user_id, titre, contenu, nom_fichier=None):
    title = strip_marks(str(titre or "").strip())[:MAX_TITLE] or "Document"
    info = store(user_id, file_name(nom_fichier, title, "pdf"), PDF_MIME, make_pdf(title, check_content(contenu)))
    return {"fichier": info["name"], "taille": info["size"], "statut": "créé, affiché à l'utilisateur sous ta réponse"}


def creer_word(user_id, titre, contenu, nom_fichier=None):
    title = strip_marks(str(titre or "").strip())[:MAX_TITLE] or "Document"
    info = store(user_id, file_name(nom_fichier, title, "docx"), DOCX_MIME, make_docx(title, check_content(contenu)))
    return {"fichier": info["name"], "taille": info["size"], "statut": "créé, affiché à l'utilisateur sous ta réponse"}


def creer_excel(user_id, titre, contenu, nom_fichier=None):
    title = strip_marks(str(titre or "").strip())[:MAX_TITLE] or "Tableau"
    info = store(user_id, file_name(nom_fichier, title, "xlsx"), XLSX_MIME, make_xlsx(check_content(contenu)))
    return {"fichier": info["name"], "taille": info["size"], "statut": "créé, affiché à l'utilisateur sous ta réponse"}


def creer_archive(user_id, nom_fichier, fichiers):
    names = [str(n).strip() for n in (fichiers or []) if str(n).strip()]
    if not names:
        raise CreationError("indique les noms des fichiers à compresser")
    if len(names) > MAX_ARCHIVE_FILES:
        raise CreationError(f"trop de fichiers (maximum {MAX_ARCHIVE_FILES} par archive)")
    _box()
    known = db.list_user_files(user_id)
    by_name = {}
    for row in known:  # le plus récent d'abord
        by_name.setdefault(row["name"].lower(), row)
    chosen, missing = [], []
    for name in names:
        row = by_name.get(name.lower())
        (chosen if row else missing).append(row or name)
    if missing:
        raise CreationError(
            "fichiers introuvables : " + ", ".join(missing) + ". Fichiers disponibles : "
            + (", ".join(sorted({r["name"] for r in known})[:30]) or "aucun")
        )
    rows = db.get_files(user_id, [r["id"] for r in chosen])
    entries = list(zip(unique_names([r["name"] for r in rows]), [r["data"] for r in rows]))
    data = make_zip(entries)
    info = store(user_id, file_name(nom_fichier, "archive", "zip"), ZIP_MIME, data)
    return {"fichier": info["name"], "taille": info["size"], "contenu": [n for n, _ in entries],
            "statut": "créé, affiché à l'utilisateur sous ta réponse"}


FORMAT_HELP = (
    "Contenu en Markdown léger : « # Titre », « ## Sous-titre », « - puce », « 1. numéro », "
    "tableau « | colonne | colonne » (une ligne par rangée), « **gras** », ligne vide entre deux paragraphes. "
    "Écris les dates en toutes lettres (21 novembre 2026)."
)


def register_creation_tools():
    common = {
        "titre": {"type": "STRING", "description": "Titre du document"},
        "nom_fichier": {"type": "STRING", "description": "Nom du fichier sans extension (facultatif)"},
    }
    register(Tool(
        name="creer_pdf",
        description=(
            "Crée un document PDF (lettre, rapport, fiche, compte rendu, devis rédigé…) que l'utilisateur pourra télécharger. "
            "À utiliser quand il demande un PDF ou un document à envoyer ou imprimer. Écris le contenu complet : rien ne sera ajouté."
        ),
        parameters={"type": "OBJECT", "properties": {**common, "contenu": {"type": "STRING", "description": FORMAT_HELP}},
                    "required": ["titre", "contenu"]},
        classification=READ, run=creer_pdf, needs_user=True,
    ))
    register(Tool(
        name="creer_word",
        description=(
            "Crée un document Word (.docx) modifiable que l'utilisateur pourra télécharger. "
            "À utiliser quand il demande un fichier Word ou un texte qu'il veut retoucher lui-même."
        ),
        parameters={"type": "OBJECT", "properties": {**common, "contenu": {"type": "STRING", "description": FORMAT_HELP}},
                    "required": ["titre", "contenu"]},
        classification=READ, run=creer_word, needs_user=True,
    ))
    register(Tool(
        name="creer_excel",
        description=(
            "Crée un classeur Excel (.xlsx) : budget, liste, planning, suivi de chantier… La première ligne est l'en-tête. "
            "Les formules simples (=SUM(B2:B9), =B2*C2) sont acceptées."
        ),
        parameters={"type": "OBJECT", "properties": {**common, "contenu": {"type": "STRING", "description": (
            "Une ligne par rangée, cellules séparées par « | ». Pour plusieurs feuilles, une ligne « ## Nom de la feuille » avant chaque feuille. "
            "Les nombres sans unité (1500, 12,5) sont des nombres ; mets l'unité dans l'en-tête."
        )}}, "required": ["titre", "contenu"]},
        classification=READ, run=creer_excel, needs_user=True,
    ))
    register(Tool(
        name="creer_archive",
        description=(
            "Regroupe en un fichier .zip des fichiers déjà présents dans la conversation (envoyés par l'utilisateur ou créés par toi). "
            "À utiliser quand il le demande, ou quand plusieurs fichiers sont à envoyer ensemble."
        ),
        parameters={"type": "OBJECT", "properties": {
            "nom_fichier": {"type": "STRING", "description": "Nom de l'archive sans extension"},
            "fichiers": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Noms exacts des fichiers à inclure"},
        }, "required": ["nom_fichier", "fichiers"]},
        classification=READ, run=creer_archive, needs_user=True,
    ))


register_creation_tools()
