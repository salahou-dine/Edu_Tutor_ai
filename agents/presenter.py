"""
Présentateurs — transforment les artefacts techniques en Markdown destiné à
l'ÉTUDIANT, sans aucun jargon interne (jamais de section_id, doc_id, hash, JSON,
chunk, score, prompt, vectorstore, chemin de fichier).

Utilisé par l'orchestrateur pour rendre la sortie de l'IDP lisible. La sortie de
l'agent de contenu est déjà du Markdown pédagogique : elle n'a pas besoin d'être
re-présentée.
"""

_DOC_TYPE_LABEL = {
    "course": "Support de cours",
    "instructions": "Consigne / devoir",
    "unknown": "Document",
}


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
            definition = d.get("definition")
            lines.append(f"- **{term}**" + (f" : {definition}" if definition else ""))

    _section(lines, "Consignes", [i.get("text") for i in analysis.get("instructions", [])])
    _section(lines, "Dates / échéances", [d.get("text_raw") for d in analysis.get("dates", [])])

    warnings = analysis.get("warnings") or []
    if extraction.extraction_quality == "low_structure":
        warnings = list(warnings) + ["La structure de ce document est peu nette : l'analyse peut être incomplète."]
    if warnings:
        lines.append("\n> ⚠️ " + " ".join(str(w) for w in warnings[:3]))

    if not analysis:
        lines.append(
            "\n*Le contenu structuré n'a pas pu être extrait pour ce document.*"
        )
    return "\n".join(lines)


def _section(lines: list[str], title: str, items: list, limit: int = 12) -> None:
    cleaned = [str(i) for i in items if i]
    if not cleaned:
        return
    lines.append(f"\n**{title}**")
    for item in cleaned[:limit]:
        lines.append(f"- {item}")


def _clean_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip()
