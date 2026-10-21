"""Nettoyage commun des réponses de FOX (toutes interfaces).

Le modèle écrit parfois du LaTeX brut (`\\sqrt{\\pi}`, `$x^2$`) ou laisse des blancs superflus.
`clean_reply` remplace ces écritures par une notation lisible (`√π`, `x²`) sans changer le sens.

Principes :
- le texte reste du Markdown ; le rendu propre à chaque interface (Telegram, PWA) se fait
  ensuite, côté interface ;
- les blocs de code (``` ... ``` et `...`) ne sont jamais modifiés ;
- en cas de doute (commande inconnue, écriture ambiguë), le texte reste tel quel : l'exactitude
  passe avant l'esthétique ;
- `FORMAT_REPLIES=0` désactive tout le nettoyage.

Aucune dépendance : bibliothèque standard uniquement.
"""
import os
import re

FORMAT_REPLIES = os.environ.get("FORMAT_REPLIES", "1") != "0"

# --- Symboles ------------------------------------------------------------------
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
SYMBOLS = {
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "ast": "*",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "propto": "∝", "ll": "≪", "gg": "≫",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏", "int": "∫",
    "oint": "∮", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨", "wedge": "∧", "vee": "∨",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔", "mapsto": "↦",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "implies": "⟹", "iff": "⟺",
    "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "degree": "°", "circ": "∘",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "prime": "′", "hbar": "ℏ", "ell": "ℓ",
    "star": "⋆", "bullet": "•", "oplus": "⊕", "otimes": "⊗", "langle": "⟨", "rangle": "⟩",
    "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
    "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctan", "sinh": "sinh", "cosh": "cosh",
    "tanh": "tanh", "log": "log", "ln": "ln", "lg": "lg", "exp": "exp", "lim": "lim",
    "max": "max", "min": "min", "sup": "sup", "inf": "inf", "det": "det", "dim": "dim",
    "ker": "ker", "gcd": "gcd", "deg": "deg", "arg": "arg", "mod": "mod", "bmod": "mod",
}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "H": "ℍ"}
ESCAPED_CHARS = {"%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}"}
SPACING = {",": " ", ";": " ", ":": " ", "!": "", " ": " ", ">": " ", "<": " "}
# Commandes qui ne servent qu'à la mise en page : on ne garde que leur contenu.
WRAPPERS = (
    "text", "textbf", "textit", "textrm", "textsf", "texttt", "mathrm", "mathbf", "mathit",
    "mathsf", "mathtt", "mathcal", "mathscr", "boldsymbol", "operatorname", "mbox", "bf", "rm",
    "emph", "underline", "overline", "bar", "hat", "vec", "tilde", "displaystyle",
)
SIZE_COMMANDS = ("left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr")
SPACE_WORDS = ("quad", "qquad", "enspace", "thinspace", "medspace", "thickspace")

SUPERSCRIPT = dict(zip("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾"))
SUPERSCRIPT.update(zip("abcdefghijklmnoprstuvwxyz", "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ"))
SUBSCRIPT = dict(zip("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"))
SUBSCRIPT.update(zip("aehijklmnoprstuvx", "ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))

MAX_PASSES = 20  # garde-fou : la conversion se stabilise toujours bien avant


# --- Aides -----------------------------------------------------------------------
def read_group(text, start):
    """Lit un groupe `{...}` commençant à `start` (qui doit être « { »).

    Retourne (contenu, index après l'accolade fermante), ou None si les accolades ne
    s'équilibrent pas.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for i in range(start, len(text)):
        char = text[i]
        if char == "\\" and i + 1 < len(text) and text[i + 1] in "{}":
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    return None


OPERATORS = set("+-−/=±×·÷<>≤≥≠≈ ,;")


def is_simple(expression):
    """Vrai si l'expression tient en un seul « mot » : pas d'opérateur hors parenthèses."""
    expression = expression.strip()
    if not expression:
        return False
    if re.fullmatch(r"[+\-−]?\d+(?:[.,]\d+)?", expression):
        return True
    depth = 0
    for char in expression:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and char in OPERATORS:
            return False
    return True


def wrap(expression):
    expression = expression.strip()
    return expression if is_simple(expression) else f"({expression})"


def script(content, table, marker):
    """Convertit en exposant/indice si tous les caractères existent, sinon `^(…)` / `_(…)`."""
    content = content.strip()
    if not content:
        return ""
    if all(c in table for c in content):
        return "".join(table[c] for c in content)
    # Pas d'équivalent Unicode : parenthèses explicites (jamais « e^iπ », ambigu).
    return f"{marker}{content}" if len(content) == 1 else f"{marker}({content})"


# --- Conversion d'un fragment mathématique -----------------------------------------
COMMAND = re.compile(r"(?<![A-Za-z]:)\\([A-Za-z]+)")


def convert_commands(text, in_math):
    """Convertit les commandes LaTeX connues. `in_math` : le fragment est délimité."""
    for _ in range(MAX_PASSES):
        new = convert_once(text, in_math)
        if new == text:
            break
        text = new
    return text


def convert_once(text, in_math):
    out = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue

        # Saut de ligne LaTeX « \\ »
        if text.startswith("\\\\", i):
            out.append("\n" if in_math else "\\\\")
            i += 2
            continue
        # Espaces et caractères échappés : \, \; \! \% \$ \{ …
        if i + 1 < n and not text[i + 1].isalpha():
            nxt = text[i + 1]
            if nxt in SPACING and in_math:
                out.append(SPACING[nxt])
                i += 2
                continue
            if nxt in ESCAPED_CHARS:
                out.append(ESCAPED_CHARS[nxt])
                i += 2
                continue
            out.append(char)
            i += 1
            continue

        match = COMMAND.match(text, i)
        if not match:  # chemin de type « C:\\dossier » : on ne touche à rien
            out.append(char)
            i += 1
            continue
        name = match.group(1)
        end = match.end()

        if name in ("frac", "dfrac", "tfrac"):
            first = read_group(text, end)
            second = read_group(text, first[1]) if first else None
            if first and second:
                num, den = convert_commands(first[0], True), convert_commands(second[0], True)
                out.append(f"{wrap(num)}/{wrap(den)}")
                i = second[1]
                continue
        elif name == "sqrt":
            index = None
            pos = end
            if text.startswith("[", pos):
                close = text.find("]", pos)
                if close != -1:
                    index = text[pos + 1 : close]
                    pos = close + 1
            group = read_group(text, pos)
            if group:
                inner = convert_commands(group[0], True)
                if index is None:
                    out.append("√" + (inner.strip() if is_simple(inner) else f"({inner.strip()})"))
                elif index.strip() == "3":
                    out.append("∛" + (inner.strip() if is_simple(inner) else f"({inner.strip()})"))
                else:
                    out.append(f"{wrap(inner)}^(1/{index.strip()})")
                i = group[1]
                continue
        elif name == "binom":
            first = read_group(text, end)
            second = read_group(text, first[1]) if first else None
            if first and second:
                out.append(f"C({convert_commands(first[0], True)}, {convert_commands(second[0], True)})")
                i = second[1]
                continue
        elif name == "mathbb":
            group = read_group(text, end)
            if group and group[0] in BLACKBOARD:
                out.append(BLACKBOARD[group[0]])
                i = group[1]
                continue
        elif name in WRAPPERS:
            group = read_group(text, end)
            if group:
                out.append(convert_commands(group[0], True))
                i = group[1]
                continue
            if name == "displaystyle":
                i = end
                continue
        elif name in SIZE_COMMANDS:
            i = end
            if i < n and text[i] == ".":  # « \right. » : délimiteur invisible
                i += 1
            continue
        elif name in SPACE_WORDS:
            out.append(" ")
            i = end
            continue
        elif name in GREEK:
            out.append(GREEK[name])
            i = end
            continue
        elif name in SYMBOLS:
            replacement = SYMBOLS[name]
            out.append(replacement)
            i = end
            continue

        # Commande inconnue (ou arguments incomplets) : conservée telle quelle.
        out.append(text[i:end])
        i = end
    result = "".join(out)
    result = convert_scripts(result, in_math)
    if in_math:
        result = result.replace("{", "").replace("}", "")
    return result


SUPER_PATTERN = re.compile(r"(?<=[^\s(\[{^_|])\^(\{[^{}]*\}|[+\-]?[0-9]+|[A-Za-z])")
SUB_PATTERN = re.compile(r"(?<=[^\s(\[{^_|])_(\{[^{}]*\}|[0-9]+|[A-Za-z])")


def convert_scripts(text, in_math):
    def sup(match):
        raw = match.group(1)
        content = raw[1:-1] if raw.startswith("{") else raw
        if not content.strip():
            return match.group(0)
        if not raw.startswith("{") and not all(c in SUPERSCRIPT for c in content):
            return match.group(0)  # « a^b » avec une lettre sans exposant Unicode : on laisse
        return script(content, SUPERSCRIPT, "^")

    text = SUPER_PATTERN.sub(sup, text)
    if in_math:  # les indices ne sont convertis qu'en maths délimitées (snake_case en prose)
        def sub(match):
            raw = match.group(1)
            content = raw[1:-1] if raw.startswith("{") else raw
            if not content.strip():
                return match.group(0)
            return script(content, SUBSCRIPT, "_")

        text = SUB_PATTERN.sub(sub, text)
    return text


# --- Délimiteurs mathématiques ------------------------------------------------------
LATEX_HINT = re.compile(r"[\\^_{}]")
DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
INLINE_PAREN = re.compile(r"\\\((.+?)\\\)", re.S)
INLINE_DOLLAR = re.compile(r"(?<![\\$\w])\$(?=\S)([^$\n]+?)(?<=\S)\$(?![\w$])")


def convert_math_spans(text):
    def display(match):
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        return "\n" + convert_commands(inner, True).strip() + "\n"

    def inline_paren(match):
        return convert_commands(match.group(1), True).strip()

    def inline_dollar(match):
        inner = match.group(1)
        if not LATEX_HINT.search(inner):  # « $5 et 6$ » : ce sont des prix, pas des maths
            return match.group(0)
        return convert_commands(inner, True).strip()

    text = DISPLAY.sub(display, text)
    text = INLINE_PAREN.sub(inline_paren, text)
    text = INLINE_DOLLAR.sub(inline_dollar, text)
    return text


# --- Blocs de code protégés -----------------------------------------------------------
CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]+`", re.S)  # un bloc non fermé court jusqu'à la fin


def split_code(text):
    """Découpe en [(texte, est_du_code)] : le code n'est jamais modifié."""
    parts = []
    last = 0
    for match in CODE.finditer(text):
        if match.start() > last:
            parts.append((text[last : match.start()], False))
        parts.append((match.group(0), True))
        last = match.end()
    if last < len(text):
        parts.append((text[last:], False))
    return parts


def clean_prose(text):
    text = convert_math_spans(text)
    text = convert_commands(text, False)
    return text


def normalize_whitespace(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_reply(text):
    """Texte prêt à afficher. Idempotent : nettoyer deux fois donne le même résultat."""
    if not text or not FORMAT_REPLIES:
        return text
    parts = [(clean_prose(part) if not is_code else part) for part, is_code in split_code(text)]
    return normalize_whitespace("".join(parts))
