"""
Description par VISION des images d'un cours, pour l'indexation RAG.

Un cours peut contenir des schémas, diagrammes, captures — invisibles au RAG
texte. Ce module extrait les images significatives (PDF via PyMuPDF, ou fichier
image seul), les fait décrire par le modèle multimodal (Hermes `vision_analyze`,
appelé implicitement en passant le chemin), et renvoie des SEGMENTS
`{"text", "heading", "page"}` — indexés comme du texte, donc recherchables.

Garde-fous (coûteux : un appel LLM par image) :
- filtre de taille (ignore logos/icônes) ;
- plafond d'images par cours ;
- cache par HASH du contenu de l'image (re-index gratuit) ;
- images purement décoratives filtrées (le modèle répond « DECORATIF »).
"""

import hashlib
import json
import tempfile
from pathlib import Path

from config import settings
from services.hermes_adapter import ask_hermes_with_skill

_DECORATIVE = "DECORATIF"

_PROMPT = (
    "Figure extraite d'un cours : {path}. Décris-la en détail (éléments, "
    "structure, relations, ce qu'elle illustre) en 3 à 5 phrases, en français, "
    "pour permettre de la retrouver par recherche. Si l'image est purement "
    "décorative (logo, photo d'illustration, bandeau sans information "
    "pédagogique), réponds UNIQUEMENT le mot : " + _DECORATIVE
)


def _cache_path(image_hash: str) -> Path:
    return settings.VISION_CACHE_DIR / f"{image_hash}.json"


def _load_cached(image_hash: str) -> dict | None:
    path = _cache_path(image_hash)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cached(image_hash: str, payload: dict) -> None:
    try:
        settings.VISION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(image_hash).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _describe_image_file(image_path: str, image_hash: str) -> str | None:
    """Description d'une image (cache par hash). None si décorative / échec."""
    cached = _load_cached(image_hash)
    if cached is not None:
        return cached.get("description")  # None si marquée décorative

    hermes = ask_hermes_with_skill(
        _PROMPT.format(path=image_path),
        skill_name=None,
        model=settings.RAG_VISION_MODEL or None,
    )
    if hermes["status"] != "success":
        return None  # échec transitoire : ne pas cacher, réessayable au re-index

    text = (hermes["content"] or "").strip()
    decorative = (not text) or text.upper().replace(".", "").strip() == _DECORATIVE
    description = None if decorative else text
    _save_cached(image_hash, {"description": description})
    return description


def _significant_pdf_images(pdf_path: Path):
    """Itère les images significatives DISTINCTES d'un PDF : (page, png_bytes)."""
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as document:
        seen: set[int] = set()
        for pno in range(document.page_count):
            for img in document[pno].get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(document, xref)
                    if pix.width < settings.RAG_VISION_MIN_WIDTH or \
                            pix.height < settings.RAG_VISION_MIN_HEIGHT:
                        continue
                    if pix.n - pix.alpha >= 4:  # CMYK/autre -> RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    yield pno + 1, pix.tobytes("png")
                except Exception:
                    continue


def course_vision_segments(file_path: str) -> list[dict]:
    """
    Segments de description des images d'un cours (PDF ou fichier image), prêts à
    chunker/indexer. Chaque segment : {"text", "heading": "Figure (schéma)",
    "page": N}. Retourne [] si vision désactivée, format non concerné, ou aucune
    image exploitable.
    """
    if not settings.RAG_VISION_ENABLED:
        return []
    path = Path(file_path)
    suffix = path.suffix.lower()

    # Source des images : (page, bytes) pour un PDF ; le fichier lui-même sinon.
    if suffix == ".pdf":
        sources = list(_significant_pdf_images(path))
    elif suffix in settings.IMAGE_EXTENSIONS:
        try:
            sources = [(1, path.read_bytes())]
        except OSError:
            return []
    else:
        return []

    segments: list[dict] = []
    for page, png_bytes in sources[: settings.RAG_VISION_MAX_IMAGES]:
        image_hash = hashlib.sha256(png_bytes).hexdigest()[:16]
        # On écrit l'image dans un fichier temporaire : Hermes lit un chemin.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            tmp.write(png_bytes)
            tmp.flush()
            description = _describe_image_file(tmp.name, image_hash)
        if description:
            page_label = f" (page {page})" if page else ""
            segments.append({
                "text": f"Figure{page_label} : {description}",
                "heading": "Figure (schéma)",
                "page": page,
            })
    return segments
