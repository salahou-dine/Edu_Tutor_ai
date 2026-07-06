"""
Présentateurs — transforment les artefacts techniques en Markdown destiné à
l'ÉTUDIANT, sans aucun jargon interne (jamais de section_id, doc_id, hash, JSON,
chunk, score, prompt, vectorstore, chemin de fichier).

Utilisé par l'orchestrateur pour rendre la sortie de l'IDP lisible. La sortie de
l'agent de contenu est déjà du Markdown pédagogique : elle n'a pas besoin d'être
re-présentée.
"""

import re

_DOC_TYPE_LABEL = {
    "course": "Support de cours",
    "instructions": "Consigne / devoir",
    "unknown": "Document",
}

# Les identifiants de section (s1, s12…) sont INTERNES : ils ne doivent jamais
# apparaître côté étudiant. Hermes peut malgré tout les glisser dans les
# `warnings`/textes de l'analyse ; on les retire ici (garantie côté Python).
_SECTION_ID_RE = re.compile(
    r"\(?\s*\bs\d+\b(?:\s*(?:,|;|à|a|et|–|-|to)\s*\bs\d+\b)*\s*\)?",
    re.IGNORECASE,
)


def _strip_section_ids(text: str) -> str:
    # 1) une parenthèse entière qui parle de section_id disparait
    #    (« (s11, s12) », « (contenu de s22 redondant avec s23) »).
    text = re.sub(r"\([^)]*\bs\d+\b[^)]*\)", " ", text)
    # 2) les runs de section_id restants (« s19 à s25 ») -> espace (pas de mots collés).
    text = _SECTION_ID_RE.sub(" ", text)
    text = re.sub(r"\(\s*[,;]*\s*\)", "", text)   # parenthèses vidées
    text = re.sub(r"\s+([.,;:)])", r"\1", text)   # espace avant ponctuation
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def present_artifact(artifact) -> str:
    """Rend l'analyse documentaire (artefact IDP) en Markdown pour l'étudiant."""
    analysis = artifact.analysis or {}
    extraction = artifact.extraction
    lines: list[str] = []

    title = analysis.get("title") or _clean_filename(artifact.filename)
    lines.append(f"## {title}")
    type_label = _DOC_TYPE_LABEL.get(analysis.get("doc_type", "unknown"), "Document")
    detail = f"*{type_label}*"
    if extraction.page_count:
        detail += f" · {extraction.page_count} pages"
    detail += f" · {len(extraction.sections)} parties détectées"
    lines.append(detail)

    headings = [s.heading for s in extraction.sections if s.heading]
    if headings:
        lines.append("\n**Structure du document**")
        for heading in headings[:25]:
            lines.append(f"- {heading}")
        if len(headings) > 25:
            lines.append(f"- … (+{len(headings) - 25} autres)")

    _section(lines, "Thèmes principaux", [t.get("label") for t in analysis.get("themes", [])])
    _section(lines, "Objectifs d'apprentissage", [o.get("text") for o in analysis.get("objectives", [])])

    definitions = analysis.get("definitions", [])
    if definitions:
        lines.append("\n**Notions clés**")
        for d in definitions[:15]:
            term = d.get("term")
            if not term:
                continue
            term = _strip_section_ids(str(term))
            definition = _strip_section_ids(str(d.get("definition") or ""))
            lines.append(f"- **{term}**" + (f" : {definition}" if definition else ""))

    _section(lines, "Consignes", [i.get("text") for i in analysis.get("instructions", [])])
    _section(lines, "Dates / échéances", [d.get("text_raw") for d in analysis.get("dates", [])])

    warnings = analysis.get("warnings") or []
    if extraction.extraction_quality == "low_structure":
        warnings = list(warnings) + ["La structure de ce document est peu nette : l'analyse peut être incomplète."]
    if warnings:
        cleaned = [_strip_section_ids(str(w)) for w in warnings[:3]]
        cleaned = [w for w in cleaned if w]
        if cleaned:
            lines.append("\n> ⚠️ " + " ".join(cleaned))

    if not analysis:
        lines.append(
            "\n*Le contenu structuré n'a pas pu être extrait pour ce document.*"
        )
    return "\n".join(lines)


def _section(lines: list[str], title: str, items: list, limit: int = 12) -> None:
    cleaned = [_strip_section_ids(str(i)) for i in items if i]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return
    lines.append(f"\n**{title}**")
    for item in cleaned[:limit]:
        lines.append(f"- {item}")


def _clean_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip()
