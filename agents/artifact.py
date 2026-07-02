"""
DocumentArtifact — représentation structurée COMMUNE d'un document.

C'est le contrat qui circule entre l'agent IDP (qui le produit), l'agent de
contenu (qui le consomme) et l'orchestrateur. Deux blocs nettement séparés :

- `extraction` : DÉTERMINISTE (Python, zéro Hermes). Réutilise l'extraction RAG
  existante (`rag.document_loader.load_document_segments`) : structure, pages,
  langue, qualité. C'est la « vérité machine ».
- `analysis`   : ENRICHI par Hermes (type de doc, thèmes, objectifs, définitions,
  consignes, dates…), chaque élément ANCRÉ à des `section_id` + extrait de preuve.
  Rempli en Phase 1 (IDP) ; vaut None tant que le document n'a pas été analysé.

Cette séparation garantit la traçabilité (on sait toujours ce qui est extrait vs
inféré) et la provenance (tout élément enrichi pointe vers une section réelle).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from config import settings
from rag.document_loader import load_document_segments


# --- Schéma -----------------------------------------------------------------

@dataclass
class Section:
    """Une section déterministe du document (issue de load_document_segments)."""
    section_id: str
    heading: str | None
    page_start: int | None
    char_len: int
    structure_source: str  # "heading" | "page_fallback"
    text: str = ""         # texte de la section : CONTRAT complet -> l'agent de
    #                        contenu (et le tuteur) lisent ICI, jamais le PDF brut.


@dataclass
class Extraction:
    """Bloc déterministe : ce que la machine extrait sans LLM."""
    status: str               # "ok" | "needs_ocr" | "empty"
    language: str | None      # "fr" | "en" | None (heuristique approximative)
    page_count: int | None
    sections: list[Section]
    extraction_quality: str   # "good" | "low_structure" | "none"


@dataclass
class DocumentArtifact:
    doc_id: str               # SHA-256 du contenu (cf. document_store)
    source: str               # chemin relatif
    filename: str
    extraction: Extraction
    analysis: dict | None = None             # enrichi par Hermes (IDP), Phase 1
    schema_version: int = settings.ARTIFACT_SCHEMA_VERSION
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentArtifact":
        ext = data.get("extraction") or {}
        extraction = Extraction(
            status=ext.get("status", "empty"),
            language=ext.get("language"),
            page_count=ext.get("page_count"),
            sections=[Section(**s) for s in ext.get("sections", [])],
            extraction_quality=ext.get("extraction_quality", "none"),
        )
        return cls(
            doc_id=data["doc_id"],
            source=data.get("source", ""),
            filename=data.get("filename", ""),
            extraction=extraction,
            analysis=data.get("analysis"),
            schema_version=data.get("schema_version", 0),
            meta=data.get("meta", {}),
        )


# --- Détection de langue (heuristique légère, déterministe) ------------------

_FR_HINTS = (" le ", " la ", " les ", " des ", " une ", " est ", " et ", " dans ",
             " pour ", " que ", " qui ", " avec ", " sur ", " ces ", " aux ")
_EN_HINTS = (" the ", " and ", " is ", " are ", " of ", " to ", " in ", " for ",
             " that ", " with ", " this ", " as ", " on ", " by ", " from ")


def _detect_language(text: str) -> str | None:
    """FR / EN approximatif par fréquence de mots outils. None si indéterminé."""
    sample = " " + text.lower()[:4000] + " "
    fr = sum(sample.count(w) for w in _FR_HINTS)
    en = sum(sample.count(w) for w in _EN_HINTS)
    if fr == 0 and en == 0:
        return None
    return "fr" if fr >= en else "en"


# --- Construction du bloc déterministe --------------------------------------

def extract_document(file_path: str) -> tuple[Extraction, dict[str, str]]:
    """
    Extraction déterministe + texte par section.

    Retourne `(extraction, {section_id: texte})`. Le mapping de textes sert à
    l'enrichissement IDP (qui doit voir le contenu de chaque section), sans
    relire le document deux fois. Ne lève pas pour un document illisible :
    encode l'état dans `status` (« needs_ocr » pour un PDF sans texte extractible).
    """
    try:
        segments = load_document_segments(file_path)
    except ValueError as exc:
        message = str(exc).lower()
        needs_ocr = "extractible" in message or "scann" in message
        status = "needs_ocr" if needs_ocr else "empty"
        return Extraction(status, None, None, [], "none"), {}

    sections: list[Section] = []
    texts: dict[str, str] = {}
    for index, segment in enumerate(segments, start=1):
        section_id = f"s{index}"
        text = segment.get("text", "")
        texts[section_id] = text
        sections.append(
            Section(
                section_id=section_id,
                heading=segment.get("heading"),
                page_start=segment.get("page"),
                char_len=len(text),
                structure_source="heading" if segment.get("heading") else "page_fallback",
                text=text,
            )
        )

    if not sections:
        return Extraction("empty", None, None, [], "none"), {}

    pages = [s.page_start for s in sections if s.page_start]
    page_count = max(pages) if pages else None
    with_heading = sum(1 for s in sections if s.structure_source == "heading")
    quality = "good" if with_heading / len(sections) >= 0.3 else "low_structure"

    extraction = Extraction(
        status="ok",
        language=_detect_language(" ".join(texts.values())),
        page_count=page_count,
        sections=sections,
        extraction_quality=quality,
    )
    return extraction, texts


def build_extraction(file_path: str) -> Extraction:
    """Bloc `extraction` déterministe seul (sans les textes de section)."""
    return extract_document(file_path)[0]
