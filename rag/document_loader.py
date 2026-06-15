"""
Chargement du texte des documents de cours.

Formats supportés pour le MVP :
- .md  / .txt : lecture directe du texte ;
- .pdf        : extraction page par page via PyMuPDF (fitz) si disponible.

Pas d'OCR, pas de Word pour l'instant. En cas de format non supporté ou de
dépendance manquante, on lève une erreur claire (jamais d'échec silencieux).
"""

from pathlib import Path


def _load_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_pdf_file(path: Path) -> str:
    """Extrait le texte d'un PDF page par page (nécessite PyMuPDF/fitz)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "Lecture PDF indisponible : PyMuPDF n'est pas installé. "
            "Installe-le avec `pip install PyMuPDF`, ou fournis le cours en "
            ".md / .txt."
        ) from exc

    parts: list[str] = []
    with fitz.open(path) as document:
        for page in document:
            parts.append(page.get_text())

    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError(
            f"Aucun texte extractible dans le PDF « {path.name} » "
            "(probablement un PDF scanné ; l'OCR n'est pas géré pour l'instant)."
        )
    return text


def load_document_text(file_path: str) -> str:
    """
    Retourne le texte brut d'un document de cours.

    Lève :
    - FileNotFoundError si le fichier n'existe pas ;
    - ValueError si l'extension n'est pas supportée ;
    - RuntimeError / ValueError pour les problèmes de PDF.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {file_path}")

    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return _load_text_file(path)
    if suffix == ".pdf":
        return _load_pdf_file(path)

    raise ValueError(
        f"Format non supporté : « {suffix or path.name} ». "
        "Formats acceptés : .md, .txt, .pdf."
    )
