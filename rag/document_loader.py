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
        # PDF sans texte (scanné) : on tente l'OCR page par page.
        ocr_pages = _ocr_pdf_pages(path)
        if ocr_pages:
            return _segments_from_pages(ocr_pages)
        raise ValueError(
            f"Aucun texte extractible dans le PDF « {path.name} » "
            "(probablement un PDF scanné ; l'OCR a échoué ou est indisponible)."
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


def _segments_from_pages(pages: list[tuple[int, str]]) -> list[dict]:
    """
    Segmente du texte PAGINÉ (issu de l'OCR) par titres détectés, en gardant la
    page. Pas d'info de police (texte brut) : on s'appuie sur la numérotation, la
    typographie et le markdown via `_looks_like_heading`.
    """
    segments: list[dict] = []
    current_heading: str | None = None
    current_page = 1
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            segments.append({"text": joined, "heading": current_heading, "page": current_page})

    for pno, text in pages:
        for line in text.splitlines():
            if _looks_like_heading(line):
                flush()
                body = []
                current_heading = _heading_text(line)
                current_page = pno
            else:
                if not body:
                    current_page = pno
                body.append(line)
    flush()
    return segments


# --- OCR (Tesseract) --------------------------------------------------------

def _ocr_image_to_text(image) -> str:
    """OCR d'une image PIL → texte (langues configurées). Vide si rien détecté."""
    import pytesseract

    return pytesseract.image_to_string(image, lang=settings.OCR_LANG).strip()


def _ocr_pdf_pages(path: Path) -> list[tuple[int, str]]:
    """Rend chaque page PDF en image puis l'OCR. Retourne [(page, texte), ...]."""
    try:
        import io

        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError:
        return []

    pages: list[tuple[int, str]] = []
    try:
        with fitz.open(path) as document:
            for pno in range(document.page_count):
                pixmap = document[pno].get_pixmap(dpi=settings.OCR_DPI)
                with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                    text = _ocr_image_to_text(image)
                if text:
                    pages.append((pno + 1, text))
    except Exception:
        return []
    return pages


def _segments_from_image(path: Path) -> list[dict]:
    """OCR d'un fichier image → un segment (le texte lu). Pas de compréhension visuelle."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Lecture d'image indisponible : Pillow n'est pas installé "
            "(`pip install pillow pytesseract`)."
        ) from exc

    with Image.open(path) as image:
        text = _ocr_image_to_text(image)
    if not text:
        # Image sans texte lisible (photo/diagramme) : on ne devine pas le visuel.
        raise ValueError(
            f"Aucun texte détecté par OCR dans l'image « {path.name} » "
            "(la compréhension visuelle d'un schéma nécessite un modèle de vision)."
        )
    return [{"text": text, "heading": None, "page": 1}]


# --- Word (.docx) -----------------------------------------------------------

def _segments_from_docx(path: Path) -> list[dict]:
    """Segmente un .docx : titres par style (« Heading »/« Titre »), corps, tableaux."""
    try:
        import docx
    except ImportError as exc:
        raise RuntimeError(
            "Lecture Word indisponible : python-docx n'est pas installé "
            "(`pip install python-docx`)."
        ) from exc

    document = docx.Document(str(path))
    segments: list[dict] = []
    current_heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            segments.append({"text": joined, "heading": current_heading, "page": None})

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        is_style_heading = style.startswith("heading") or style.startswith("titre") or style in ("title", "subtitle")
        if is_style_heading or _looks_like_heading(text):
            flush()
            body = []
            current_heading = _clean_heading(text)
        else:
            body.append(text)
    flush()

    # Tableaux (ordre non garanti par python-docx) : regroupés en fin de document.
    table_lines: list[str] = []
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                table_lines.append(" | ".join(cells))
    if table_lines:
        segments.append({"text": "\n".join(table_lines), "heading": "Tableaux", "page": None})

    if not segments:
        raise ValueError(f"Aucun texte exploitable dans le document « {path.name} ».")
    return segments


# --- PowerPoint (.pptx) -----------------------------------------------------

def _segments_from_pptx(path: Path) -> list[dict]:
    """Segmente un .pptx : 1 diapo = 1 section (titre = en-tête, n° de diapo = page)."""
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError(
            "Lecture PowerPoint indisponible : python-pptx n'est pas installé "
            "(`pip install python-pptx`)."
        ) from exc

    presentation = Presentation(str(path))
    segments: list[dict] = []
    for index, slide in enumerate(presentation.slides, start=1):
        title_shape = slide.shapes.title
        heading = _clean_heading(title_shape.text) if title_shape and title_shape.text.strip() else None
        # Identité stable : python-pptx recrée des wrappers, donc `is` n'est pas
        # fiable pour reconnaître le titre -> on compare par shape_id.
        title_id = title_shape.shape_id if title_shape else None
        lines: list[str] = []
        for shape in slide.shapes:
            if shape.shape_id == title_id or not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if text:
                lines.append(text)
        body = "\n".join(lines).strip()
        if heading or body:
            segments.append({"text": body, "heading": heading, "page": index})

    if not segments:
        raise ValueError(f"Aucun texte exploitable dans la présentation « {path.name} ».")
    return segments


_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")


def load_document_segments(file_path: str) -> list[dict]:
    """
    Retourne les segments structurés d'un document : `{"text", "heading", "page"}`.

    Formats : .md/.txt, .pdf (texte ou OCR si scanné), .docx, .pptx, images (OCR).

    Lève :
    - FileNotFoundError si le fichier n'existe pas ;
    - ValueError si l'extension n'est pas supportée ou si rien d'exploitable ;
    - RuntimeError si une dépendance d'extraction manque.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {file_path}")

    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return _segments_from_text(_load_text_file(path))
    if suffix == ".pdf":
        return _segments_from_pdf(path)
    if suffix == ".docx":
        return _segments_from_docx(path)
    if suffix == ".pptx":
        return _segments_from_pptx(path)
    if suffix in _IMAGE_SUFFIXES:
        return _segments_from_image(path)

    raise ValueError(
        f"Format non supporté : « {suffix or path.name} ». "
        "Formats acceptés : .md, .txt, .pdf, .docx, .pptx, images (.png/.jpg/…)."
    )
