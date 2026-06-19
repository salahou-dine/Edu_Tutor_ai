"""
Transformation des chunks techniques (ChromaDB) en *indications de cours*
lisibles pour l'étudiant.

Côté étudiant on ne montre jamais : distance, index de chunk, top_k.
On montre : le cours, la partie du cours, et un extrait propre.

La « partie » provient désormais des MÉTADONNÉES calculées à l'indexation par le
découpage structure-aware (`section` = titre de section/diapo détecté ; `page` =
repli quand aucun titre n'a pu être détecté). On ne re-parse donc plus le
document ici : la partie est cohérente avec ce qui a réellement été indexé.
"""

import re
from pathlib import Path
from typing import Optional

from config import settings


def _normalize(text: str) -> str:
    """Réduit tous les blancs à un seul espace."""
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(text: str) -> str:
    """
    Nettoie un texte (Markdown ou brut) pour l'affichage de l'extrait :
    retire blocs de code, code inline, marqueurs, tableaux, séparateurs.
    """
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"[*_#>]", " ", cleaned)
    cleaned = re.sub(r"\|", " ", cleaned)
    cleaned = re.sub(r"-{3,}", " ", cleaned)
    return _normalize(cleaned)


def _make_excerpt(clean_text: str) -> str:
    """
    Produit un extrait lisible depuis un texte déjà nettoyé : démarrage propre
    au début de phrase si besoin, troncature entre EXCERPT_MIN/MAX_CHARS.
    """
    text = clean_text.lstrip(" .,;:)-]")
    prefix = ""
    if text and text[0].islower():
        match = re.search(r"\s([A-ZÀ-Ý][a-zà-ÿ])", text[:120])
        if match:
            text = text[match.start(1) :]
        prefix = "… "

    if len(text) <= settings.EXCERPT_MAX_CHARS:
        return (prefix + text).strip()

    window = text[: settings.EXCERPT_MAX_CHARS]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut < settings.EXCERPT_MIN_CHARS:
        cut = window.rfind(" ")
    if cut < settings.EXCERPT_MIN_CHARS:
        cut = settings.EXCERPT_MAX_CHARS
    return (prefix + window[: cut + 1].strip() + " …").strip()


def _course_name(chunk: dict, source: str) -> str:
    """Nom de cours affiché : titre indexé, sinon nom de fichier nettoyé."""
    course = chunk.get("title")
    if course:
        return course
    stem = Path(source).stem if source else "Cours"
    return stem.replace("_", " ").replace("-", " ").strip().capitalize()


def _part_label(chunk: dict) -> str:
    """
    Partie du cours : titre de section détecté à l'indexation, sinon repli sur
    la page (PDF), sinon libellé générique.
    """
    section = chunk.get("section")
    if section:
        return section
    page = chunk.get("page")
    if page is not None:
        return f"Page {page}"
    return "Section générale"


def format_course_indications(chunks: list[dict]) -> list[dict]:
    """
    Transforme une liste de chunks techniques en indications lisibles.

    Entrée (par chunk) : {rank, text, source, chunk_index, title, section, page, distance}
    Sortie (par indication) : {course, part, excerpt, document_path}

    Dédupliquées par (course, part).
    """
    indications: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for chunk in chunks:
        source = chunk.get("source") or ""
        excerpt = _make_excerpt(_clean_text(chunk.get("text", "")))
        course = _course_name(chunk, source)
        part = _part_label(chunk)

        key = (course, part)
        if key in seen:
            continue
        seen.add(key)

        indications.append(
            {
                "course": course,
                "part": part,
                "excerpt": excerpt,
                "document_path": source,
            }
        )

    return indications
