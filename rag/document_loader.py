"""
Chargement STRUCTURÉ du texte des documents de cours.

Formats supportés :
- .md / .txt : lecture directe ;
- .pdf       : extraction via PyMuPDF (fitz) avec infos de police.

Deux niveaux d'API :
- `load_document_text(path)` -> texte plat (rétrocompat, usages simples) ;
- `load_document_segments(path)` -> liste de SEGMENTS structurés
  `{"text", "heading", "page"}`, utilisée par l'indexeur pour un découpage
  structure-aware.

Détection de titre/section MULTI-SIGNAUX (volontairement format-agnostique, car
les documents réels sont hétérogènes : slides, Word, rapports, livres, md, txt) :
1. numérotation en début de ligne (décimale, romaine, lettrée, mots-clés) ;
2. police plus grande/grasse que le corps (PDF) ;
3. typographie (ligne courte en MAJUSCULES, sans ponctuation finale) ;
4. titres Markdown (#).
Aucun signal n'est fiable seul ; on les cumule. Repli : le numéro de page.
"""

import re
from collections import Counter
from pathlib import Path

from config import settings


# --- Détection de titre / section (partagée PDF + texte) --------------------

_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")

# Numérotation de titre en début de ligne : "1.", "1.2.3", "3)", "IV.", "A)".
_NUM_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[.)]?|[IVXLCDM]+[.)]|[A-Z][.)])\s+\S"
)

# Mots-clés de section (FR/EN) suivis d'un numéro : "Chapitre 2", "Section III".
_KEYWORD_HEADING = re.compile(
    r"^\s*(?:chapitre|chapter|section|partie|part|lecture|le[cç]on|module|"
    r"unit[ée]?|annexe|appendix)\s+(?:\d+|[IVXLCDM]+)\b",
    re.IGNORECASE,
)

# Lignes de bruit récurrent dans les PDF : date seule, numéro de page seul.
_DATE_LINE = re.compile(r"^\s*\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s*$")
_NUMBER_LINE = re.compile(r"^\s*\d{1,4}\s*$")


def _clean_heading(text: str) -> str:
    """Normalise un titre détecté : espaces, marqueurs markdown, puces, flèches."""
    text = re.sub(r"[•●▪◦‣]", " ", text)               # puces n'importe où
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\s#*·\-–—>→»]+", "", text)        # marqueurs en tête
    return text.strip(" #*").strip()


def _looks_like_heading(
    line: str,
    font_size: float | None = None,
    body_size: float | None = None,
    is_bold: bool = False,
) -> bool:
    """
    Vrai si la ligne ressemble à un titre/section, en cumulant les signaux.
    `font_size`/`body_size`/`is_bold` ne sont fournis que pour les PDF.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return False

    # 1) Markdown / numérotation / mots-clés (format-agnostique, fiables).
    if _MD_HEADING.match(line):
        return True
    if _NUM_HEADING.match(stripped):
        return True
    if _KEYWORD_HEADING.match(stripped):
        return True

    # Les signaux « faibles » (police, gras, majuscules) exigent une FORME de
    # titre : commencer par une majuscule ou un chiffre. Cela élimine les
    # fragments de phrase en gros caractères des diapos (« or connected… »).
    first = stripped[0]
    if not (first.isupper() or first.isdigit()):
        return False
    words = stripped.split()

    # 2) Police (PDF) : plus grande que le corps (et raisonnablement courte).
    if (
        font_size
        and body_size
        and font_size >= body_size * settings.HEADING_FONT_RATIO
        and len(words) <= 12
    ):
        return True
    if is_bold and len(words) <= 10 and not stripped.endswith((".", ";", ",")):
        return True

    # 3) Typographie : ligne courte ENTIÈREMENT EN MAJUSCULES, sans ponctuation
    #    finale. Conservateur (les MAJUSCULES limitent les faux positifs).
    if len(words) <= 10 and not stripped.endswith((".", ";", ",")):
        letters = [c for c in stripped if c.isalpha()]
        if letters and len(letters) >= 3 and stripped.upper() == stripped:
            return True

    return False


def _heading_text(line: str) -> str:
    """Texte de section à conserver (titre markdown nettoyé, sinon ligne brute)."""
    match = _MD_HEADING.match(line)
    return _clean_heading(match.group(1) if match else line)


# --- Chargement texte brut (rétrocompat) ------------------------------------

def _load_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_document_text(file_path: str) -> str:
    """
    Retourne le texte brut d'un document de cours (rétrocompat / usages simples).
    Pour le découpage, préférer `load_document_segments`.
    """
    segments = load_document_segments(file_path)
    return "\n\n".join(seg["text"] for seg in segments).strip()


# --- Extraction en segments structurés --------------------------------------

def _segments_from_text(text: str) -> list[dict]:
    """Segmente un texte (md/txt) par titres détectés. page = None."""
    segments: list[dict] = []
    current_heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            segments.append({"text": joined, "heading": current_heading, "page": None})

    for line in text.splitlines():
        if _looks_like_heading(line):
            flush()
            body = []
            current_heading = _heading_text(line)
        else:
            body.append(line)
    flush()

    if not segments and text.strip():
        segments.append({"text": text.strip(), "heading": None, "page": None})
    return segments


def _segments_from_pdf(path: Path) -> list[dict]:
    """
    Segmente un PDF : extrait les lignes avec leur police, détecte la taille du
    corps (taille modale), filtre le bruit récurrent (en-têtes/pieds/numéros) et
    coupe sur les titres détectés. Chaque segment porte la page où il commence.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "Lecture PDF indisponible : PyMuPDF n'est pas installé. "
            "Installe-le avec `pip install PyMuPDF`, ou fournis le cours en "
            ".md / .txt."
        ) from exc

    pages: list[tuple[int, list[dict]]] = []
    with fitz.open(path) as document:
        for pno in range(document.page_count):
            data = document[pno].get_text("dict")
            lines: list[dict] = []
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    txt = "".join(s.get("text", "") for s in spans).strip()
                    if not txt:
                        continue
                    size = max((s.get("size", 0) for s in spans), default=0)
                    bold = any(
                        (s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower()
                        for s in spans
                    )
                    lines.append({"text": txt, "size": round(size, 1), "bold": bold})
            pages.append((pno + 1, lines))

    all_lines = [pl["text"] for _, pls in pages for pl in pls]
    if not all_lines:
        raise ValueError(
            f"Aucun texte extractible dans le PDF « {path.name} » "
            "(probablement un PDF scanné ; l'OCR n'est pas géré pour l'instant)."
        )

    # Taille « corps » = taille de police la plus fréquente.
    size_counts = Counter(round(pl["size"]) for _, pls in pages for pl in pls)
    body_size = size_counts.most_common(1)[0][0] if size_counts else 0

    # Bruit récurrent : lignes répétées sur beaucoup de pages, dates, numéros.
    line_counts = Counter(all_lines)
    noise_threshold = max(3, int(0.3 * len(pages)))

    def is_noise(text: str) -> bool:
        if _DATE_LINE.match(text) or _NUMBER_LINE.match(text):
            return True
        return line_counts[text] >= noise_threshold and len(text) < 80

    segments: list[dict] = []
    current_heading: str | None = None
    heading_size: float | None = None
    current_page = 1
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            segments.append(
                {"text": joined, "heading": current_heading, "page": current_page}
            )

    for pno, lines in pages:
        for pl in lines:
            text = pl["text"]
            if is_noise(text):
                continue
            if _looks_like_heading(
                text, font_size=pl["size"], body_size=body_size, is_bold=pl["bold"]
            ):
                # Titre multi-lignes : on agrège les lignes de titre consécutives
                # de même police tant qu'aucun corps n'a encore été écrit.
                if (
                    not body
                    and current_heading is not None
                    and pl["size"] == heading_size
                    and len(current_heading) < 70
                ):
                    current_heading = _clean_heading(f"{current_heading} {text}")
                else:
                    flush()
                    body = []
                    current_heading = _clean_heading(text)
                    current_page = pno
                heading_size = pl["size"]
            else:
                if not body:
                    current_page = pno  # page où débute le contenu de ce segment
                body.append(text)
    flush()

    if not segments:
        raise ValueError(
            f"Aucun texte exploitable dans le PDF « {path.name} »."
        )
    return segments


def load_document_segments(file_path: str) -> list[dict]:
    """
    Retourne les segments structurés d'un document : `{"text", "heading", "page"}`.

    Lève :
    - FileNotFoundError si le fichier n'existe pas ;
    - ValueError si l'extension n'est pas supportée ou le PDF est vide ;
    - RuntimeError si PyMuPDF manque pour un PDF.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {file_path}")

    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return _segments_from_text(_load_text_file(path))
    if suffix == ".pdf":
        return _segments_from_pdf(path)

    raise ValueError(
        f"Format non supporté : « {suffix or path.name} ». "
        "Formats acceptés : .md, .txt, .pdf."
    )
