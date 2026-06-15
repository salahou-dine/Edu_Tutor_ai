"""
Transformation des chunks techniques (ChromaDB) en *indications de cours*
lisibles pour l'étudiant.

Côté étudiant on ne montre jamais : distance, index de chunk, top_k.
On montre : le cours, la partie du cours, et un extrait propre.

Inférence du cours et de la partie :
on lit le document source, on le découpe en sections par titres Markdown, puis
on rattache le chunk à sa section en cherchant des fragments de l'EXTRAIT VISIBLE
dans le corps des sections. On détecte donc la partie à partir de ce que
l'étudiant lit réellement (cohérence extrait <-> partie), pas du chunk brut qui
peut déborder sur la section suivante.
"""

import re
from pathlib import Path
from typing import Optional

from config import settings


# Cache {chemin: liste de sections} pour ne pas relire le fichier à chaque chunk.
_SECTIONS_CACHE: dict[str, list[dict]] = {}

# Fenêtre de texte utilisée pour rattacher un chunk à sa section. Indépendante
# de la longueur d'affichage de l'extrait (qui peut être très courte) afin que
# la détection reste fiable même avec des extraits courts.
_SECTION_PROBE_CHARS = 400

_HEADER_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def _normalize(text: str) -> str:
    """Réduit tous les blancs à un seul espace."""
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(text: str) -> str:
    """
    Nettoie un texte Markdown pour l'affichage ET la mise en correspondance :
    retire blocs de code, code inline, marqueurs, tableaux, séparateurs.
    Utilisé des deux côtés (extrait et corps de section) pour que la détection
    de section soit robuste au balisage.
    """
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"[*_#>]", " ", cleaned)
    cleaned = re.sub(r"\|", " ", cleaned)
    cleaned = re.sub(r"-{3,}", " ", cleaned)
    return _normalize(cleaned)


def _clean_title(title: str) -> str:
    """Nettoie un titre Markdown : numérotation, gras, code inline."""
    title = re.sub(r"[*`]", "", title)
    title = re.sub(r"^\d+(\.\d+)*[.)]?\s*", "", title)
    return title.strip()


def _strip_course_prefix(title: str) -> str:
    """Retire un préfixe « Cours : » éventuel du titre H1."""
    return re.sub(r"^cours\s*:\s*", "", title, flags=re.IGNORECASE).strip()


def _resolve_source(source_path: str) -> Path:
    path = Path(source_path)
    return path if path.is_absolute() else settings.PROJECT_ROOT / source_path


def _parse_sections(source_path: str) -> list[dict]:
    """
    Découpe un document Markdown en sections.

    Chaque section : {"course": <H1>, "part": <H2/H3 ou None>, "body_match": <texte nettoyé>}
    """
    if source_path in _SECTIONS_CACHE:
        return _SECTIONS_CACHE[source_path]

    sections: list[dict] = []
    course_title: Optional[str] = None
    current_part: Optional[str] = None
    current_body: list[str] = []

    def flush():
        if current_body:
            sections.append(
                {
                    "course": course_title,
                    "part": current_part,
                    "body_match": _clean_text(" ".join(current_body)),
                }
            )

    # On ne relit que les sources texte/Markdown pour en extraire les titres.
    # Les PDF (et autres binaires) n'ont pas de structure Markdown lisible :
    # on renvoie alors une liste vide (la partie tombera sur un libellé générique).
    path = _resolve_source(source_path)
    if path.suffix.lower() not in (".md", ".txt"):
        _SECTIONS_CACHE[source_path] = []
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for line in lines:
        match = _HEADER_RE.match(line)
        if match:
            level = len(match.group(1))
            title = _clean_title(match.group(2))
            flush()
            current_body = []
            if level == 1:
                course_title = _strip_course_prefix(title)
                current_part = None
            else:
                current_part = title
        else:
            current_body.append(line)

    flush()
    _SECTIONS_CACHE[source_path] = sections
    return sections


def _find_section(clean_probe: str, sections: list[dict]) -> Optional[dict]:
    """
    Rattache un texte (déjà nettoyé) à sa section dominante par scoring
    multi-ancres. Égalité -> section la plus haute dans le document.
    """
    if not clean_probe:
        return None

    span = 40
    n_anchors = 6
    anchors: list[str] = []
    if len(clean_probe) <= span:
        anchors.append(clean_probe)
    else:
        step = max(1, (len(clean_probe) - span) // (n_anchors - 1))
        for start in range(0, len(clean_probe) - span + 1, step):
            anchors.append(clean_probe[start : start + span])

    best_section: Optional[dict] = None
    best_score = 0
    for section in sections:
        score = sum(
            1 for a in anchors if len(a.strip()) >= 12 and a.strip() in section["body_match"]
        )
        if score > best_score:
            best_score = score
            best_section = section
    return best_section if best_score > 0 else None


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


def format_course_indications(chunks: list[dict]) -> list[dict]:
    """
    Transforme une liste de chunks techniques en indications lisibles.

    Entrée (par chunk) : {rank, text, source, chunk_index, distance}
    Sortie (par indication) : {course, part, excerpt, document_path}

    Dédupliquées par (course, part).
    """
    indications: list[dict] = []
    seen: set[tuple[Optional[str], Optional[str]]] = set()

    for chunk in chunks:
        source = chunk.get("source") or ""
        clean_full = _clean_text(chunk.get("text", ""))
        excerpt = _make_excerpt(clean_full)

        sections = _parse_sections(source) if source else []
        # Détection de section sur une fenêtre suffisante (indépendante de la
        # longueur d'affichage de l'extrait), pour rester fiable.
        probe = clean_full[:_SECTION_PROBE_CHARS]
        section = _find_section(probe, sections) if sections else None

        course = (section or {}).get("course") or chunk.get("title")
        if not course:
            stem = Path(source).stem if source else "Cours"
            course = stem.replace("_", " ").replace("-", " ").strip().capitalize()

        part = (section or {}).get("part") or "Section générale"

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
