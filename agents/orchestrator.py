"""
Orchestrateur multi-agents — workflow DÉFENDABLE (pas un simple routeur LLM).

Routage PRIMAIRE = DÉTERMINISTE (table d'intention), traçable et indépendant du
modèle. Le planner LLM (skill education-orchestrator) reste disponible en OPTION
(`use_planner=True`) mais n'est pas le cœur.

Table d'intention :
    Intention                          Agents                  Résultat
    ─────────────────────────────────  ──────────────────────  ────────────────────
    Déposer et analyser un document    IDP → Content           structure + résumé
    Demander un résumé                 [IDP si besoin] → Content synthèse
    Demander une fiche de révision     [IDP si besoin] → Content fiche
    Expliquer une section              [IDP] + section → Tuteur explication ancrée
    Poser une question de cours        Tuteur                  réponse sourcée
    Question hors-source               Tuteur (mode général)   réponse honnête

Principes :
- CONTRAT de données unique = `DocumentArtifact` (document_id, sections+texte,
  pages, qualité, thèmes, consignes, provenance). Content ne relit JAMAIS le brut.
- Python est le seul EXÉCUTEUR ; il insère lui-même une étape IDP manquante.
- Erreurs gérées (sans texte / ambigu / hors-source / extraction incomplète).
- TRACE structurée (dev) ; réponse finale UNIQUE et lisible (sans jargon) côté étudiant.
"""

import re

from config import settings
from agents.registry import CAPABILITIES, list_capabilities
from agents.document_store import load_artifact
from agents.common import extract_json
from agents.presenter import present_artifact
from agents.idp_agent import analyze_document
from agents.content_agent import generate_content
from agents.tutor_agent import answer_student_question_for_ui
from services.hermes_adapter import ask_hermes_with_skill


ORCHESTRATOR_SKILL_NAME = "education-orchestrator"
_GENERIC_ERROR = "Le tuteur n'a pas pu traiter ta demande pour le moment. Réessaie."
_SECTION_CONTEXT_MAX = 6000


# --- Inventaire des documents -----------------------------------------------

def list_documents() -> list[dict]:
    """Documents disponibles (data/courses) + statut d'analyse (artefact existant)."""
    from agents.document_store import compute_doc_id

    base = settings.COURSES_DIR
    if not base.exists():
        return []
    documents = []
    for path in sorted(base.iterdir()):
        if not path.is_file() or path.suffix.lower() not in settings.SUPPORTED_EXTENSIONS:
            continue
        doc_id = compute_doc_id(str(path))
        artifact = load_artifact(doc_id)
        documents.append({
            "filename": path.name,
            "path": str(path),
            "doc_id": doc_id,
            "analyzed": artifact is not None and artifact.analysis is not None,
        })
    return documents


# --- Résolution du document cible -------------------------------------------

_COMMON_TOKENS = {"cours", "document", "fichier", "pdf", "spring", "printemps"}
_DEICTIC = ("ce document", "ce cours", "ce pdf", "ce fichier", "ce support",
            "le document", "le cours", "du document", "de ce", "ce doc")


def _doc_tokens(filename: str) -> set[str]:
    stem = filename.rsplit(".", 1)[0].lower()
    return {t for t in re.split(r"[\s_\-.]+", stem) if len(t) >= 3 and t not in _COMMON_TOKENS}


def _match_doc(question: str, documents: list[dict]) -> dict | None:
    """Document dont un token DISTINCTIF du nom apparaît dans la question (unique)."""
    words = {w for w in re.findall(r"\w{3,}", question.lower())}
    matches = [d for d in documents if _doc_tokens(d["filename"]) & words]
    return matches[0] if len(matches) == 1 else None


def _target_doc(question: str, documents: list[dict], selected: str | None) -> dict | None:
    """Document visé : nommé dans la question, sinon sélectionné, sinon l'unique."""
    named = _match_doc(question, documents)
    if named:
        return named
    ql = question.lower()
    if selected and (any(d in ql for d in _DEICTIC) or True):
        sel = next((d for d in documents if d["filename"] == selected), None)
        if sel:
            return sel
    if len(documents) == 1:
        return documents[0]
    return None


def resolve_sections(artifact, selector) -> list[str] | None:
    """Sélecteur libre (« attaques réseau ») → section_ids, ou None (= tout)."""
    if not selector:
        return None
    text = str(selector).strip().lower()
    if text in ("all", "tout", "tous", "global", "le cours", "tout le document", "intégralité"):
        return None
    tokens = re.findall(r"\w{4,}", text)
    if not tokens:
        return None
    ids = [
        s.section_id for s in artifact.extraction.sections
        if s.heading and any(tok in s.heading.lower() for tok in tokens)
    ]
    return ids or None


# --- Détection d'intention (déterministe) -----------------------------------

_RE_ANALYZE = re.compile(
    r"\b(analyse|analyser|structure|chapitres?|de quoi (?:ça |cela )?(?:parle|traite)|"
    r"que contient|m[ée]tadonn|montre[- ]?moi (?:les|la) (?:chapitres?|structure))\b",
    re.IGNORECASE,
)
_RE_REVISION = re.compile(r"\b(fiche|r[ée]vision|r[ée]viser|flashcards?)\b", re.IGNORECASE)
_RE_SUMMARY = re.compile(
    r"\b(r[ée]sum[eé]?s?|r[ée]sume[rz]|synth[èe]se|aper[çc]u|sommaire|summary|summarize)\b",
    re.IGNORECASE,
)
_RE_EXPLAIN = re.compile(r"\b(explique|expliquer|d[ée]taille|reformule|clarifie)\b", re.IGNORECASE)
_RE_SECTION_WORD = re.compile(r"\b(section|chapitre|partie|paragraphe|diapo)\b", re.IGNORECASE)
_RE_SECTION_REF = re.compile(
    r"\b(?:sur|section|chapitre|partie|paragraphe|concernant|à propos de|du th[èe]me)\s+(.+)$",
    re.IGNORECASE,
)


def _section_selector(question: str) -> str | None:
    match = _RE_SECTION_REF.search(question)
    return match.group(1).strip() if match else None


def _clarify_plan(documents: list[dict]) -> dict:
    return {"intent": "clarify", "clarification": _clarify_message(documents)}


def _clarify_message(documents: list[dict]) -> str:
    names = "\n".join(f"- {d['filename']}" for d in documents)
    return (
        "Sur quel document veux-tu que je travaille ?\n\n"
        f"{names}\n\nNomme-le et je m'en occupe."
    )


def _classify_to_plan(question: str, documents: list[dict], selected: str | None) -> dict:
    """
    Classe la demande de l'étudiant en INTENTION + plan d'étapes (déterministe).
    Retourne soit {"intent": "clarify", "clarification": ...}, soit
    {"intent": ..., "steps": [{agent, doc, content_type?, sections?}]}.
    """
    ql = question.lower()
    doc = _target_doc(question, documents, selected)
    doc_needed_but_missing = doc is None and len(documents) >= 1

    # 1) Analyse complète d'un document -> IDP puis Content (résumé).
    if _RE_ANALYZE.search(ql):
        if doc is None:
            return _clarify_plan(documents) if documents else _no_doc_plan()
        return {"intent": "analyze", "steps": [
            {"agent": "idp", "doc": doc},
            {"agent": "content", "doc": doc, "content_type": "summary"},
        ]}

    # 2) Fiche de révision -> Content (revision).
    if _RE_REVISION.search(ql):
        if doc is None:
            return _clarify_plan(documents) if documents else _no_doc_plan()
        return {"intent": "revision", "steps": [
            {"agent": "content", "doc": doc, "content_type": "revision",
             "sections": _section_selector(question)},
        ]}

    # 3) Résumé -> Content (summary). (« résume le cours » sans cible -> clarify)
    if _RE_SUMMARY.search(ql):
        if doc is None:
            return _clarify_plan(documents) if documents else _no_doc_plan()
        return {"intent": "summary", "steps": [
            {"agent": "content", "doc": doc, "content_type": "summary",
             "sections": _section_selector(question)},
        ]}

    # 4) Expliquer une section précise -> Tuteur, ancré sur la section.
    if _RE_EXPLAIN.search(ql) and _RE_SECTION_WORD.search(ql) and doc is not None:
        return {"intent": "explain_section", "steps": [
            {"agent": "tutor", "doc": doc, "sections": _section_selector(question)},
        ]}

    # 5) Défaut : question de cours (ou hors-source) -> Tuteur.
    #    (Le tuteur décide lui-même grounded/mixed/général et reste honnête.)
    _ = doc_needed_but_missing  # (info dispo pour la trace si besoin)
    return {"intent": "question", "steps": [{"agent": "tutor"}]}


def _no_doc_plan() -> dict:
    """Aucun document disponible mais demande documentaire -> message clair."""
    return {"intent": "no_document", "steps": []}


# --- Réparation des préconditions -------------------------------------------

def _repair_preconditions(steps: list[dict]) -> list[dict]:
    """
    Insère une étape `idp` avant toute étape qui requiert un artefact (content, ou
    tutor ancré sur une section) si le document n'est pas encore analysé — sans
    doublonner l'IDP déjà prévu dans le plan.
    """
    out: list[dict] = []
    will_analyze: set[str] = set()
    for step in steps:
        doc = step.get("doc")
        if step["agent"] == "idp" and doc:
            will_analyze.add(doc["doc_id"])
        needs_artifact = (
            (step["agent"] == "content" and doc)
            or (step["agent"] == "tutor" and doc and step.get("sections"))
        )
        if needs_artifact and not doc["analyzed"] and doc["doc_id"] not in will_analyze:
            out.append({"agent": "idp", "doc": doc})
            will_analyze.add(doc["doc_id"])
        out.append(step)
    return out


# --- Exécution (Python exécute) ---------------------------------------------

def _run_idp(doc: dict) -> dict:
    artifact = analyze_document(doc["path"])
    if artifact.extraction.status != "ok":
        msg = (
            "Ce document n'a pas de texte exploitable : l'OCR a échoué ou l'image "
            "ne contient pas de texte (la compréhension visuelle d'un schéma "
            "nécessiterait un modèle de vision, non disponible pour l'instant)."
        )
        return {"status": "error", "kind": "idp", "answer": msg, "doc": doc["filename"]}
    if artifact.analysis is None:
        return {"status": "error", "kind": "idp",
                "answer": "Je n'ai pas pu analyser ce document pour le moment. Réessaie.",
                "doc": doc["filename"]}
    return {"status": "success", "kind": "idp",
            "answer": present_artifact(artifact), "doc": doc["filename"]}


def _run_content(step: dict) -> dict:
    doc = step["doc"]
    content_type = step.get("content_type") or "summary"
    section_ids = None
    selector = step.get("sections")
    if selector:
        artifact = load_artifact(doc["doc_id"])
        if artifact is not None:
            section_ids = resolve_sections(artifact, selector)

    result = generate_content(doc["path"], content_type, section_ids=section_ids)
    if result["status"] == "success":
        answer = result["content"]
        used = result.get("sections_used") or []
        if section_ids and used:
            headings = [u["heading"] for u in used if u.get("heading")][:6]
            if headings:
                answer += "\n\n*Parties utilisées : " + " · ".join(headings) + "*"
        return {"status": "success", "kind": "content", "answer": answer, "doc": doc["filename"]}
    return {"status": "error", "kind": "content",
            "answer": result.get("message") or _GENERIC_ERROR, "doc": doc["filename"]}


def _run_tutor(question: str, history, doc: dict | None = None, sections=None) -> dict:
    augmented = question
    if doc and sections:
        artifact = load_artifact(doc["doc_id"])
        if artifact is not None:
            ids = resolve_sections(artifact, sections)
            chosen = [
                s for s in artifact.extraction.sections
                if ids and s.section_id in ids and s.text
            ]
            context = "\n\n".join(f"{s.heading or ''}\n{s.text}".strip() for s in chosen)
            if context:
                augmented = (
                    "En t'appuyant sur cette partie du cours :\n"
                    "----- DÉBUT DE LA PARTIE -----\n"
                    f"{context[:_SECTION_CONTEXT_MAX]}\n"
                    "----- FIN DE LA PARTIE -----\n\n"
                    f"{question}"
                )
    try:
        result = answer_student_question_for_ui(augmented, history=history)
    except Exception:
        result = None
    if not result or result.get("status") != "success":
        return {"status": "error", "kind": "tutor", "answer": _GENERIC_ERROR, "doc": None}
    return {"status": "success", "kind": "tutor", "answer": result["student_answer"],
            "doc": None, "payload": {"mode": result.get("mode")}}


def _execute_all(steps: list[dict], question: str, history) -> list[dict]:
    results = []
    for step in steps:
        if step["agent"] == "tutor":
            results.append(_run_tutor(question, history, step.get("doc"), step.get("sections")))
        elif step["agent"] == "idp":
            results.append(_run_idp(step["doc"]))
        elif step["agent"] == "content":
            results.append(_run_content(step))
    return results


# --- Composition de la réponse finale (unique, sans jargon) -----------------

def _compose(intent: str, results: list[dict]) -> dict:
    """Assemble UNE réponse étudiant à partir des résultats des étapes."""
    if not results:
        return {"status": "error", "kind": intent, "answer": _GENERIC_ERROR, "doc": None}

    by_kind = {r["kind"]: r for r in results}

    if intent == "analyze":
        idp, content = by_kind.get("idp"), by_kind.get("content")
        parts, doc = [], None
        if idp and idp["status"] == "success":
            parts.append(idp["answer"]); doc = idp["doc"]
        if content and content["status"] == "success":
            parts.append("## Résumé\n\n" + content["answer"]); doc = doc or content["doc"]
        if parts:
            return {"status": "success", "kind": "analyze",
                    "answer": "\n\n---\n\n".join(parts), "doc": doc}
        # tout a échoué -> remonter la 1re erreur
        return next((r for r in results if r["status"] != "success"), results[-1])

    # summary / revision / question / explain_section -> dernière étape pertinente
    return results[-1]


# --- Planner LLM (option) ---------------------------------------------------

def _history_block(history) -> str:
    if not history:
        return ""
    lines = []
    for message in history[-4:]:
        role = "Étudiant" if message.get("role") == "user" else "Tuteur"
        lines.append(f"{role} : {(message.get('content') or '').strip()[:200]}")
    return "Historique récent :\n" + "\n".join(lines) + "\n\n"


def _plan_with_llm(question, history, documents, selected) -> dict | None:
    docs = "\n".join(
        f"- {d['filename']} {'[analysé]' if d['analyzed'] else '[non analysé]'}"
        for d in documents
    ) or "(aucun document)"
    menu = "\n".join(f"- {c.name} : {c.description}" for c in list_capabilities())
    prompt = (
        f"Utilise le skill {ORCHESTRATOR_SKILL_NAME}.\n\n"
        f"Demande de l'étudiant :\n{question}\n\n{_history_block(history)}"
        f"Documents disponibles :\n{docs}\n"
        f"Document sélectionné : {selected or '(aucun)'}\n\n"
        f"Agents disponibles :\n{menu}\n\n"
        "Réponds UNIQUEMENT par le plan JSON décrit dans le skill."
    )
    hermes = ask_hermes_with_skill(prompt, skill_name=ORCHESTRATOR_SKILL_NAME)
    if hermes["status"] != "success":
        return None
    raw = extract_json(hermes["content"])
    if not isinstance(raw, dict):
        return None
    # On résout les noms de documents -> objets, et on classe en "planner".
    steps = []
    for s in raw.get("steps") or []:
        agent = s.get("agent")
        if agent not in CAPABILITIES:
            continue
        doc = None
        if agent in ("idp", "content"):
            doc = _target_doc(str(s.get("doc") or ""), documents, selected)
            if doc is None:
                return {"intent": "clarify", "clarification": _clarify_message(documents)}
        steps.append({"agent": agent, "doc": doc,
                      "content_type": s.get("content_type"), "sections": s.get("sections")})
    if raw.get("needs_clarification") and not steps:
        return {"intent": "clarify", "clarification": raw.get("clarification") or _clarify_message(documents)}
    return {"intent": "planner", "steps": steps or [{"agent": "tutor"}]}


# --- Action explicite (boutons UI, déterministe) ----------------------------

def run_action(action: str, doc_filename: str, history=None) -> dict:
    """Action explicite (analyze / summary / revision) sur un document, sans planner."""
    documents = list_documents()
    doc = next((d for d in documents if d["filename"] == doc_filename), None)
    if doc is None:
        return {"status": "error", "kind": "clarify",
                "answer": "Ce document n'est plus disponible.", "doc": None, "steps_run": []}
    intent = "analyze" if action == "analyze" else ("revision" if action == "revision" else "summary")
    if action == "analyze":
        steps = [{"agent": "idp", "doc": doc}, {"agent": "content", "doc": doc, "content_type": "summary"}]
    else:
        steps = [{"agent": "content", "doc": doc, "content_type": intent}]
    steps = _repair_preconditions(steps)
    results = _execute_all(steps, "", history)
    final = _compose(intent, results)
    final["trace"] = _trace(intent, steps, results)
    return final


# --- Trace (réservée dev) ----------------------------------------------------

def _trace(intent: str, steps: list[dict], results: list[dict]) -> dict:
    return {
        "intent": intent,
        "steps": [
            {"agent": s["agent"], "doc": (s.get("doc") or {}).get("filename"),
             "content_type": s.get("content_type")}
            for s in steps
        ],
        "results": [{"kind": r["kind"], "status": r["status"], "doc": r.get("doc")} for r in results],
    }


# --- API publique ------------------------------------------------------------

def handle(question, history=None, selected_doc=None, use_planner=False) -> dict:
    """
    Traite une demande d'étudiant via le système multi-agents (routage déterministe
    par défaut). Retour : {status, kind, answer (Markdown), doc, steps_run, trace}.
    """
    cleaned = (question or "").strip()
    if not cleaned:
        return {"status": "error", "kind": "tutor", "answer": _GENERIC_ERROR,
                "doc": None, "steps_run": [], "trace": {}}

    documents = list_documents()
    plan = _plan_with_llm(cleaned, history, documents, selected_doc) if use_planner else None
    if plan is None:
        plan = _classify_to_plan(cleaned, documents, selected_doc)

    intent = plan.get("intent", "question")

    if intent == "clarify":
        return {"status": "clarify", "kind": "clarify",
                "answer": plan["clarification"], "doc": None,
                "steps_run": ["clarify"], "trace": {"intent": "clarify"}}
    if intent == "no_document":
        return {"status": "clarify", "kind": "clarify",
                "answer": "Aucun document n'est disponible. Ajoute un cours, puis "
                          "redemande l'analyse ou le résumé.", "doc": None,
                "steps_run": ["no_document"], "trace": {"intent": "no_document"}}

    steps = _repair_preconditions(plan.get("steps", []))
    results = _execute_all(steps, cleaned, history)
    final = _compose(intent, results)
    final["steps_run"] = [s["agent"] for s in steps]
    final["trace"] = _trace(intent, steps, results)
    return final
