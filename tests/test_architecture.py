"""Architecture : où le code peut-il envoyer des données à l'extérieur ?

Ce test lit le code source (aucun import, aucun réseau) et échoue si un fichier ouvre un nouveau chemin vers
l'extérieur sans être déclaré, justifié et limité dans tests/architecture_allowlist.py. En phase 1a il gèle
aussi les appels externes d'agent.py : emplacement (fonction), appelé et signature, pas seulement leur nombre.
"""
import ast
import pathlib
import re

import pytest

import architecture_allowlist as al
from conftest import BOT_DIR, ORCHESTRATOR_DIR, SCRIPTS_DIR

# Dossiers du code, tels que vus par les tests (en conteneur : /srv, /bot, /scripts).
SOURCES = {"orchestrator": ORCHESTRATOR_DIR, "telegram_bot": BOT_DIR, "scripts": SCRIPTS_DIR}

# Modules par lesquels du contenu peut sortir (préfixes de noms normalisés).
FORBIDDEN = (
    "httpx", "requests", "urllib3", "urllib.request", "http.client", "socket", "ssl", "smtplib", "ftplib",
    "imaplib", "poplib", "telnetlib", "xmlrpc", "aiohttp", "websockets", "websocket", "paramiko",
    "google", "googleapiclient", "google_auth_oauthlib", "openai", "anthropic", "groq", "elevenlabs",
    "telegram", "webpush",  # webpush : module interne d'envoi push
    "subprocess", "importlib", "ctypes", "multiprocessing.connection", "pycurl", "grpc", "boto3",
)
# Appels qui ouvrent une connexion ou un processus, quel que soit l'import.
FORBIDDEN_CALLS = {
    "urlopen", "urlretrieve", "create_connection", "open_connection", "open_unix_connection", "getaddrinfo",
    "gethostbyname", "system", "popen", "Popen", "spawnl", "spawnv", "execv", "execl", "fork",
}
DYNAMIC_CALLS = {"__import__", "eval", "exec", "import_module"}


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def imported_names(tree):
    """Noms de modules importés, normalisés (`from urllib import request` -> `urllib.request`)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def is_forbidden(name):
    return any(name == p or name.startswith(p + ".") for p in FORBIDDEN)


def scan_source(source):
    """(imports interdits, appels interdits, appels dynamiques) trouvés dans un code source."""
    tree = ast.parse(source)
    imports = {n for n in imported_names(tree) if is_forbidden(n)}
    calls, dynamic = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            last = name.split(".")[-1] if name else getattr(node.func, "attr", None)
            if last in FORBIDDEN_CALLS:
                calls.add(last)
            if last in DYNAMIC_CALLS:
                dynamic.add(last)
    return imports, calls, dynamic


def python_files():
    for name, base in SOURCES.items():
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield f"{name}/{path.relative_to(base).as_posix()}", path


def source(rel):
    name, _, rest = rel.partition("/")
    return SOURCES[name] / rest


def modules_of(entry):
    return [m["module"] for m in entry]


def allowed(name, entry):
    return any(name == a or name.startswith(a + ".") for a in modules_of(entry))


# --- Le détecteur lui-même doit voir tous les vecteurs ---------------------------------------------------------
@pytest.mark.parametrize("source, kind", [
    ("import httpx", "imports"),
    ("import requests as r", "imports"),
    ("from urllib import request", "imports"),
    ("import urllib.request", "imports"),
    ("from urllib.request import urlopen", "imports"),
    ("from http import client", "imports"),
    ("import http.client", "imports"),
    ("import socket", "imports"),
    ("import smtplib", "imports"),
    ("import ftplib", "imports"),
    ("import imaplib", "imports"),
    ("import aiohttp", "imports"),
    ("import websockets", "imports"),
    ("from google import genai", "imports"),
    ("import google.generativeai", "imports"),
    ("from googleapiclient.discovery import build", "imports"),
    ("import telegram", "imports"),
    ("import webpush", "imports"),
    ("import subprocess", "imports"),
    ("import importlib", "imports"),
    ("def f():\n    import httpx", "imports"),  # import tardif dans une fonction
    ("import os\nos.system('curl x')", "calls"),
    ("import os\nos.popen('curl x')", "calls"),
    ("x.urlopen(u)", "calls"),
    ("asyncio.open_connection(h, 1)", "calls"),
    ("socket.create_connection(a)", "calls"),
    ("__import__('socket')", "dynamic"),
    ("importlib.import_module('httpx')", "dynamic"),
    ("eval('1')", "dynamic"),
    ("exec('import socket')", "dynamic"),
])
def test_le_detecteur_voit_chaque_vecteur(source, kind):
    imports, calls, dynamic = scan_source(source)
    found = {"imports": imports, "calls": calls, "dynamic": dynamic}[kind]
    assert found, f"non détecté : {source!r}"


@pytest.mark.parametrize("source", [
    "import json, os, re\nfrom urllib.parse import urlsplit\nimport urllib.error",
    "from datetime import datetime\nimport psycopg2",
    "import privacy\nimport egress\nimport db",
])
def test_le_detecteur_ne_signale_pas_les_imports_inoffensifs(source):
    assert scan_source(source) == (set(), set(), set())


# --- Le dépôt -------------------------------------------------------------------------------------------------------
def test_aucun_fichier_n_ouvre_un_chemin_externe_non_declare():
    problems = []
    for rel, path in python_files():
        imports, calls, dynamic = scan_source(path.read_text(encoding="utf-8"))
        entry = al.ALLOWLIST.get(rel)
        for name in sorted(imports):
            if entry is None or not allowed(name, entry):
                problems.append(f"{rel} importe {name} sans être déclaré")
        for name in sorted(calls):
            if entry is None:
                problems.append(f"{rel} appelle {name}() sans être déclaré")
        for name in sorted(dynamic):
            problems.append(f"{rel} utilise l'appel dynamique {name}() (interdit partout)")
    assert not problems, "\n".join(problems)


def test_pas_d_appel_dynamique_meme_dans_les_fichiers_autorises():
    for rel, path in python_files():
        assert not scan_source(path.read_text(encoding="utf-8"))[2], rel


def test_la_liste_blanche_est_exacte_sans_entree_morte():
    files = dict(python_files())
    for rel, entry in al.ALLOWLIST.items():
        assert rel in files, f"{rel} n'existe plus : retire l'entrée"
        imports, _, _ = scan_source(files[rel].read_text(encoding="utf-8"))
        for module in modules_of(entry):
            assert any(n == module or n.startswith(module + ".") for n in imports), (
                f"{rel} n'importe plus {module} : retire-le de la liste blanche"
            )
        for name in imports:
            assert allowed(name, entry), f"{rel} importe {name}, non déclaré"


def test_chaque_entree_est_justifiee_et_bornee():
    assert len(al.ALLOWLIST) <= al.MAX_ENTRIES
    assert sum(len(e) for e in al.ALLOWLIST.values()) <= al.MAX_MODULES
    for rel, entry in al.ALLOWLIST.items():
        assert entry, rel
        names = modules_of(entry)
        assert len(names) == len(set(names)), f"{rel} : module déclaré deux fois"
        for item in entry:
            assert item["status"] in ("temporary", "permanent"), (rel, item["module"])
            assert len(item["why"].strip()) >= 30, f"{rel}/{item['module']} : raison trop courte"
            if item["status"] == "temporary":
                assert item.get("remove_in") in al.PHASES, f"{rel}/{item['module']} : phase de retrait manquante"
            else:
                assert "remove_in" not in item, (rel, item["module"])


def test_les_entrees_temporaires_sont_strictement_limitees():
    temporary = {(rel, m["module"]) for rel, e in al.ALLOWLIST.items() for m in e if m["status"] == "temporary"}
    assert temporary == al.TEMPORARY_MODULES  # on ne crée pas de nouvelle dérogation temporaire en silence


def test_une_entree_temporaire_echue_doit_etre_retiree():
    now = al.PHASES.index(al.CURRENT_PHASE)
    for rel, entry in al.ALLOWLIST.items():
        for item in entry:
            if item["status"] == "temporary":
                assert now < al.PHASES.index(item["remove_in"]), (
                    f"{rel}/{item['module']} : la phase {item['remove_in']} est atteinte, "
                    "l'envoi doit passer par egress.py et l'entrée disparaître"
                )


def test_la_passerelle_et_la_politique_n_ont_aucune_entree_ni_import_reseau():
    for name in ("egress", "privacy", "privacy_store", "providers"):
        rel = f"orchestrator/{name}.py"
        assert rel not in al.ALLOWLIST
        assert scan_source(source(rel).read_text(encoding="utf-8")) == (set(), set(), set()), rel


# --- Gel d'agent.py (phase 1a : aucun nouvel appel externe) ------------------------------------------------------------
class _Sites(ast.NodeVisitor):
    def __init__(self, wanted):
        self.stack, self.found, self.wanted = [], [], wanted

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        name = dotted(node.func)
        if name and self.wanted(name):
            keywords = tuple(sorted(k.arg for k in node.keywords if k.arg))
            self.found.append((self.stack[-1] if self.stack else "<module>", name, len(node.args), keywords))
        self.generic_visit(node)


def agent_external_sites():
    tree = ast.parse(source("orchestrator/agent.py").read_text(encoding="utf-8"))
    visitor = _Sites(lambda n: n.split(".")[0] in ("httpx", "genai", "client") or n.endswith("generate_content"))
    visitor.visit(tree)
    return visitor.found


def test_agent_py_garde_exactement_ses_appels_externes():
    """Emplacement (fonction), appelé et signature : un appel déplacé ou modifié est détecté, pas seulement ajouté."""
    assert sorted(agent_external_sites()) == sorted(al.AGENT_EXTERNAL_SITES)


def test_agent_py_ne_reference_les_clients_externes_que_par_ces_appels():
    """Pas d'alias (`post = httpx.post`) ni de référence détournée aux clients externes."""
    tree = ast.parse(source("orchestrator/agent.py").read_text(encoding="utf-8"))
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)) and id(node) not in called:
            name = dotted(node)
            if name and name.split(".")[0] in ("httpx", "genai") and not isinstance(node, ast.Name):
                # un attribut non appelé n'est toléré que pour intercepter une exception (httpx.TransportError...)
                assert re.fullmatch(r"httpx\.[A-Za-z]*(Error|Exception|Timeout)", name), name


def test_agent_py_n_alias_pas_les_fonctions_reseau():
    tree = ast.parse(source("orchestrator/agent.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Attribute, ast.Name)):
            name = dotted(node.value)
            assert name not in ("httpx.post", "httpx.get", "httpx.request", "httpx.Client", "client.models.generate_content"), name


def entry_calls():
    result = []
    names = {"groq_post", "_call_with_retry", "generate_with_retry", "groq_chat", "groq_complete", "complete"}
    for rel, path in python_files():
        if not rel.startswith("orchestrator/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if rel.endswith("/agent.py"):
            visitor = _Sites(lambda n: n.split(".")[-1] in names)
        else:
            visitor = _Sites(lambda n: n.startswith("agent.") and n.split(".")[-1] in names | {"chat"})
        visitor.visit(tree)
        result += [(rel, fn, callee, pos, kw) for fn, callee, pos, kw in visitor.found]
    return result


def test_les_chemins_vers_les_appels_externes_sont_geles():
    """Aucun nouvel appelant de chat/complete/groq_* : tout nouveau chemin vers un fournisseur est visible."""
    assert sorted(entry_calls()) == sorted(al.AGENT_ENTRY_CALLS)


# --- Outils : catégorie obligatoire, même pour les outils enregistrés plus tard (Google Calendar) ---------------------------
def test_chaque_outil_du_code_declare_une_categorie():
    missing, total = [], 0
    for rel, path in python_files():
        if not rel.startswith("orchestrator/"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and dotted(node.func) in ("Tool", "tools.Tool"):
                total += 1
                if "category" not in {k.arg for k in node.keywords}:
                    missing.append(f"{rel}:{node.lineno}")
    assert total >= 30, total  # 29 outils + l'annulation
    assert not missing, missing
