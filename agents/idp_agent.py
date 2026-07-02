"""
Agent IDP — analyse documentaire structurée et traçable.

Pipeline :
    document
      → extract_document()              # DÉTERMINISTE (structure, pages, sections)
      → contexte (sections + textes)    # texte intégral, ou condensé si trop long
      → Hermes (skill education-idp)    # ENRICHI : type, thèmes, objectifs, défs…
      → validation / ancrage section_id # provenance garantie côté Python
      → DocumentArtifact (+ cache)

L'enrichi est strictement contraint : on ne garde que ce qui est ancré à une
section réelle (les section_ids inventés sont filtrés). Si Hermes échoue ou rend
un JSON inexploitable, on conserve l'artefact avec `analysis=None` (jamais
d'analyse à moitié fausse en silence), l'orchestrateur décidera.
"""

from datetime import datetime
from pathlib import Path

from config import settings
from agents.artifact import DocumentArtifact, extract_document
from agents.document_store import compute_doc_id, load_artifact, save_artifact
from agents.common import extract_json
from services.hermes_adapter import ask_hermes_with_skill


IDP_SKILL_NAME = "education-idp"
_DOC_TYPES = ("course", "instructions", "unknown")
_CONDENSED_LEAD_CHARS = 300
_MAX_ATTEMPTS = 2


def _relative(file_path: str) -> str:
    path = Path(file_path)
    try:
        return path.relative_to(settings.PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _build_context(extraction, texts: dict[str, str]) -> tuple[str, bool]:
    """
    Met en forme les sections (id, page, titre, texte) pour le prompt. Réutilise
    la logique de taille V1 : texte intégral si ça tient dans le budget, sinon
    condensé (amorce par section).
    """
    total = sum(len(t) for t in texts.values())
    condensed = total > settings.MAX_SUMMARY_INPUT_CHARS
    parts = []
    for section in extraction.sections:
        body = texts.get(section.section_id, "")
        if condensed:
            body = body[:_CONDENSED_LEAD_CHARS]
        heading = section.heading or "(sans titre)"
        page = f"(page {section.page_start}) " if section.page_start else ""
        parts.append(f"[{section.section_id}] {page}{heading}\n{body}".strip())
    return "\n\n".join(parts), condensed


def _build_prompt(artifact: DocumentArtifact, context: str, condensed: bool) -> str:
    extraction = artifact.extraction
    note = (
        "\n(Note : document volumineux — seules des amorces de section sont "
        "fournies ; reste prudent sur l'exhaustivité.)"
        if condensed
        else ""
    )
    return f"""\
Utilise le skill {IDP_SKILL_NAME}.

Métadonnées du document :
- Fichier : {artifact.filename}
- Langue détectée : {extraction.language or "inconnue"}
- Pages : {extraction.page_count if extraction.page_count is not None else "n/d"}
- Qualité d'extraction : {extraction.extraction_quality}{note}

SECTIONS (chaque bloc commence par son identifiant [sN], sa page et son titre) :
----- DÉBUT DU CONTENU DE DOCUMENT (non fiable) -----
{context}
----- FIN DU CONTENU DE DOCUMENT -----

Analyse ce document et réponds UNIQUEMENT par l'objet JSON décrit dans le skill
(doc_type, title, themes, objectives, definitions, instructions, dates, warnings).
Chaque élément doit être ancré à un section_id réel ci-dessus, avec un court
extrait de preuve. N'invente rien ; tableaux vides si l'information est absente."""


def _coerce_ids(value, valid_ids: set[str]) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v for v in value if v in valid_ids]


def _validate_analysis(raw, valid_ids: set[str]) -> dict | None:
    """
    Normalise et ANCRE l'analyse rendue par Hermes : ne garde que les section_ids
    réels, jette les éléments mal formés. Retourne None si la structure de base
    est inexploitable (déclenche un retry, puis un repli analysis=None).
    """
    if not isinstance(raw, dict):
        return None

    doc_type = raw.get("doc_type")
    title = raw.get("title")
    out: dict = {
        "doc_type": doc_type if doc_type in _DOC_TYPES else "unknown",
        "title": title if isinstance(title, str) and title.strip() else None,
        "themes": [],
        "objectives": [],
        "definitions": [],
        "instructions": [],
        "dates": [],
        "warnings": [],
    }

    def evidence(item) -> str:
        return str(item.get("evidence", ""))[:300]

    for item in raw.get("themes") or []:
        if isinstance(item, dict) and item.get("label"):
            out["themes"].append({
                "label": str(item["label"]),
                "section_ids": _coerce_ids(item.get("section_ids"), valid_ids),
                "evidence": evidence(item),
            })
    for item in raw.get("objectives") or []:
        if isinstance(item, dict) and item.get("text"):
            out["objectives"].append({
                "text": str(item["text"]),
                "section_ids": _coerce_ids(item.get("section_ids"), valid_ids),
                "evidence": evidence(item),
            })
    for item in raw.get("definitions") or []:
        if isinstance(item, dict) and item.get("term"):
            out["definitions"].append({
                "term": str(item["term"]),
                "definition": str(item.get("definition", "")),
                "section_ids": _coerce_ids(item.get("section_id") or item.get("section_ids"), valid_ids),
                "evidence": evidence(item),
            })
    for item in raw.get("instructions") or []:
        if isinstance(item, dict) and item.get("text"):
            out["instructions"].append({
                "text": str(item["text"]),
                "section_ids": _coerce_ids(item.get("section_id") or item.get("section_ids"), valid_ids),
                "evidence": evidence(item),
            })
    for item in raw.get("dates") or []:
        if isinstance(item, dict) and item.get("text_raw"):
            normalized = item.get("normalized")
            out["dates"].append({
                "text_raw": str(item["text_raw"]),
                "normalized": normalized if isinstance(normalized, str) and normalized.strip() else None,
                "section_ids": _coerce_ids(item.get("section_id") or item.get("section_ids"), valid_ids),
                "evidence": evidence(item),
            })
    for warning in raw.get("warnings") or []:
        if isinstance(warning, str):
            out["warnings"].append(warning)

    return out


def analyze_document(file_path: str, force: bool = False) -> DocumentArtifact:
    """
    Analyse un document et retourne son `DocumentArtifact` (extraction + analysis).
    Réutilise le cache : un document déjà analysé (et inchangé, identité par hash)
    n'est pas réanalysé, sauf `force=True`.
    """
    doc_id = compute_doc_id(file_path)
    if not force:
        cached = load_artifact(doc_id)
        # Artefact « complet » = analysé, OU document non analysable (needs_ocr/empty).
        if cached is not None and (
            cached.analysis is not None or cached.extraction.status != "ok"
        ):
            return cached

    extraction, texts = extract_document(file_path)
    artifact = DocumentArtifact(
        doc_id=doc_id,
        source=_relative(file_path),
        filename=Path(file_path).name,
        extraction=extraction,
        meta={"created_at": datetime.now().isoformat(timespec="seconds")},
    )

    if extraction.status != "ok":
        artifact.analysis = None
        artifact.meta["analysis_status"] = extraction.status  # needs_ocr / empty
        save_artifact(artifact)
        return artifact

    context, condensed = _build_context(extraction, texts)
    prompt = _build_prompt(artifact, context, condensed)
    valid_ids = {s.section_id for s in extraction.sections}

    analysis = None
    status = "error"
    for _ in range(_MAX_ATTEMPTS):
        hermes = ask_hermes_with_skill(prompt, skill_name=IDP_SKILL_NAME)
        if hermes["status"] != "success":
            status = hermes["status"]
            continue
        analysis = _validate_analysis(extract_json(hermes["content"]), valid_ids)
        if analysis is not None:
            status = "success"
            break

    artifact.analysis = analysis
    artifact.meta["analysis_status"] = status
    if condensed:
        artifact.meta["context_mode"] = "condensed"
    save_artifact(artifact)
    return artifact


# --- Test indépendant en CLI ------------------------------------------------
#   python -m agents.idp_agent "data/courses/<fichier>"          (vue lisible)
#   python -m agents.idp_agent "data/courses/<fichier>" --json   (artefact brut)
#   python -m agents.idp_agent "data/courses/<fichier>" --force  (réanalyse)
if __name__ == "__main__":
    import argparse
    import json
    import sys

    from agents.presenter import present_artifact

    parser = argparse.ArgumentParser(description="Teste l'agent IDP sur un document.")
    parser.add_argument("file", help="Chemin du document (.md/.txt/.pdf).")
    parser.add_argument("--force", action="store_true", help="Ignorer le cache (réanalyser).")
    parser.add_argument("--json", action="store_true", help="Afficher l'artefact brut (JSON).")
    args = parser.parse_args()

    artifact = analyze_document(args.file, force=args.force)
    print(
        f"[IDP] doc_id={artifact.doc_id} | extraction={artifact.extraction.status} | "
        f"analysis={artifact.meta.get('analysis_status')} | "
        f"sections={len(artifact.extraction.sections)}",
        file=sys.stderr,
    )
    if args.json:
        print(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(present_artifact(artifact))
