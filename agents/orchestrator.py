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
from agents.compose_agent import generate_document, revise_document
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


def _doc_tokens(filename: str) -> set[str]:
    stem = filename.rsplit(".", 1)[0].lower()
    return {t for t in re.split(r"[\s_\-.]+", stem) if len(t) >= 3 and t not in _COMMON_TOKENS}


def _corpus_common_tokens(documents: list[dict]) -> set[str]:
    """Tokens présents dans TOUS les noms de fichiers → non distinctifs (ex. cybersecurity, 40, 2026)."""
    token_sets = [_doc_tokens(d["filename"]) for d in documents]
    return set.intersection(*token_sets) if token_sets else set()


def _match_doc(question: str, documents: list[dict]) -> dict | None:
    """Document dont un token DISTINCTIF du nom apparaît dans la question (unique).

    Les tokens partagés par tout le corpus sont ignorés : sinon un nom de fichier
    complet (ex. renvoyé par le planner) matcherait tous les cours via « cybersecurity »
    → faux positif ambigu → clarify indu.
    """
    # [a-z0-9] (pas \w) pour que « _ » sépare : un nom de fichier collé
    # « cybersecurity_ot_40_cm3 » doit se découper en tokens, pas rester un bloc.
    words = {w for w in re.findall(r"[a-z0-9]{3,}", question.lower())}
    shared = _corpus_common_tokens(documents)
    matches = [d for d in documents if (_doc_tokens(d["filename"]) - shared) & words]
    return matches[0] if len(matches) == 1 else None


def _target_doc(question: str, documents: list[dict], selected: str | None) -> dict | None:
    """Document visé : nommé dans la question, sinon le sélectionné (contexte
    actif de l'UI, quand il existe), sinon l'unique document disponible."""
    named = _match_doc(question, documents)
    if named:
        return named
    if selected:
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
# Itération sur le livrable COURANT (n'est prise en compte que s'il existe un
# livrable à réviser -> voir handle()). Verbes d'ÉDITION d'un contenu existant.
_RE_REVISE = re.compile(
    r"\b(raccourci[st]?|raccourcir|plus court|abr[èe]ge|r[ée]sume[- ]le plus|"
    r"allonge|d[ée]veloppe|d[ée]taille (?:plus|davantage|le)|plus (?:court|long|simple|formel|détaillé|clair)|"
    r"reformule|r[ée][ée]cri[st]|r[ée]cri[st]|r[ée]dige[- ]le autrement|modifie|change|corrige|"
    r"ajoute|rajoute|enl[èe]ve|retire|supprime|remplace|am[ée]liore|refais|reprends|"
    r"r[ée]g[ée]n[èe]re|mets? à jour|simplifie)\b",
    re.IGNORECASE,
)
# Le verbe d'édition seul ne suffit PAS (« explique pourquoi on AJOUTE un
# pare-feu » n'est pas une révision) : il faut aussi que la consigne VISE le
# livrable. Trois signaux acceptés :
#   1. clitique attaché au verbe : « raccourcis-le », « améliore-la » ;
#   2. référence explicite au livrable : « ce document », « la fiche »,
#      « le résumé », « ta version »… ;
#   3. objet de structure documentaire : « une section », « la conclusion »…
_RE_REVISE_TARGET = re.compile(
    r"(-l[ea]\b"
    r"|\b(?:ce|cette|le|la|ton|ta|mon|ma) +(?:document|doc|fiche|r[ée]sum[ée]|"
    r"texte|version|rapport|expos[ée]|note|synth[èe]se|contenu|livrable)\b"
    r"|\b(?:une?|la|le|l['’]|des?) *(?:sections?|parties?|paragraphes?|"
    r"introduction|conclusion|titres?|exemples?|points?)\b"
    r")",
    re.IGNORECASE,
)
# Consigne d'édition « nue » (sans autre objet) : cible forcément le livrable
# courant (« simplifie », « plus court stp », « régénère »). Une éventuelle
# suite libre (« corrige mon exercice ») NE matche pas -> pas une révision.
_RE_REVISE_BARE = re.compile(
    r"^\s*(?:raccourcis?|raccourcir|abr[èe]ge|allonge|d[ée]veloppe|reformule|"
    r"simplifie|am[ée]liore|refais|reprends|r[ée]g[ée]n[èe]re|r[ée][ée]cris|corrige|"
    r"plus (?:court|long|simple|formel|d[ée]taill[ée]|clair))"
    r"(?:[- ](?:le|la|ça|moi))?"
    r"(?:\s+(?:stp|svp|s['’]il te pla[îi]t|un peu|encore|plus))*\s*[!.…]?\s*$",
    re.IGNORECASE,
)


def _is_revision_request(question: str) -> bool:
    """Vrai si la demande est une RÉVISION du livrable courant : verbe d'édition
    ET cible identifiable (clitique / référence / structure), ou consigne nue."""
    ql = question.lower()
    if not _RE_REVISE.search(ql):
        return False
    return bool(_RE_REVISE_TARGET.search(ql) or _RE_REVISE_BARE.match(ql))
# Rédaction d'un document original : un VERBE d'écriture + un TYPE de document.
# (le verbe est exigé pour ne pas capter « résume le document » -> résumé.)
_RE_COMPOSE = re.compile(
    r"\b(écri[st]|ecri[st]|écrire|ecrire|r[ée]dige[rz]?|r[ée]daction|"
    r"compose[rz]?|r[ée]dact|produi[st]|prépare[rz]?|prepare[rz]?|fais|génère[rz]?|genere[rz]?)\b"
    r".{0,40}?"
    r"\b(document|rapport|expos[ée]|dissertation|essai|article|lettre|m[ée]mo|"
    r"note de synth[èe]se|synth[èe]se écrite|texte|billet|compte[- ]rendu|"
    r"pr[ée]sentation écrite|plan détaillé)\b",
    re.IGNORECASE,
)
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

    # 0) Rédaction d'un document original -> agent compose (source OPTIONNELLE).
    #    On n'ancre QUE si un cours est explicitement NOMMÉ (_match_doc), jamais
    #    par défaut : compose doit pouvoir générer librement.
    #    GARDE-FOU : « document »/« texte » sont des types GÉNÉRIQUES — si la
    #    demande contient aussi un mot de résumé/fiche (« fais un résumé du
    #    document CM1 »), c'est une ressource d'étude, pas une rédaction ->
    #    on laisse les branches résumé/fiche traiter. Les types spécifiques
    #    (rapport, exposé, dissertation…) restent prioritaires pour compose.
    compose_match = _RE_COMPOSE.search(ql)
    if compose_match:
        generic_type = compose_match.group(2).lower() in ("document", "texte")
        study_resource = bool(_RE_SUMMARY.search(ql) or _RE_REVISION.search(ql))
        if not (generic_type and study_resource):
            step = {"agent": "compose", "instructions": question}
            named = _match_doc(question, documents)
            if named is not None:
                step["doc"] = named
            return {"intent": "compose", "steps": [step]}

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
        return {"status": "success", "kind": "content", "answer": answer,
                "doc": doc["filename"], "content_type": content_type, "markdown": answer}
    return {"status": "error", "kind": "content",
            "answer": result.get("message") or _GENERIC_ERROR, "doc": doc["filename"]}


def _compose_grounding(doc: dict) -> str | None:
    """Contexte d'ancrage pour compose : texte condensé du cours NOMMÉ, s'il est
    déjà analysé (compose n'a pas de précondition -> on n'analyse pas à la volée)."""
    artifact = load_artifact(doc["doc_id"])
    if artifact is None or not artifact.extraction.sections:
        return None
    parts = []
    for section in artifact.extraction.sections:
        body = (section.text or "")[:400]
        parts.append(f"{section.heading or ''}\n{body}".strip())
    context = "\n\n".join(p for p in parts if p)
    return context[:_SECTION_CONTEXT_MAX] or None


def _run_compose(step: dict) -> dict:
    doc = step.get("doc")
    course_context = _compose_grounding(doc) if doc is not None else None
    result = generate_document(step.get("instructions", ""), course_context=course_context)
    if result["status"] == "success":
        return {"status": "success", "kind": "compose", "answer": result["content"],
                "doc": doc["filename"] if doc else None,
                "content_type": "document", "markdown": result["content"],
                "title": result.get("title")}
    return {"status": "error", "kind": "compose",
            "answer": result.get("message") or _GENERIC_ERROR,
            "doc": doc["filename"] if doc else None}


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
        elif step["agent"] == "compose":
            results.append(_run_compose(step))
    return results


# --- Composition de la réponse finale (unique, sans jargon) -----------------

_DELIVERABLE_TITLES = {"summary": "Résumé", "revision": "Fiche de révision"}


def _build_deliverable(result: dict) -> dict:
    """Objet livrable structuré (rendu en carte + téléchargeable) depuis un résultat
    d'agent producteur (content : résumé/fiche ; compose : document original)."""
    ctype = result.get("content_type") or "summary"
    markdown = result.get("markdown") or result.get("answer") or ""
    if ctype == "document":
        return {
            "type": "document",
            "title": result.get("title") or "Document",
            "doc": result.get("doc") or "",
            "markdown": markdown,
        }
    doc = result.get("doc") or ""
    stem = doc.rsplit(".", 1)[0]
    label = _DELIVERABLE_TITLES.get(ctype, "Ressource d'étude")
    return {
        "type": ctype,
        "title": f"{label} — {stem}" if stem else label,
        "doc": doc,
        "markdown": markdown,
    }


def _deliverable_lead(deliverable: dict) -> str:
    """Courte phrase d'intro dans le chat (le contenu complet vit dans la carte)."""
    if deliverable["type"] == "revision":
        return "Voici ta fiche de révision 👇"
    if deliverable["type"] == "document":
        return "Voici ton document 👇"
    return "Voici le résumé 👇"


def _compose(intent: str, results: list[dict]) -> dict:
    """Assemble UNE réponse étudiant à partir des résultats des étapes.

    Quand une ressource d'étude est produite (agent content), elle est renvoyée
    comme LIVRABLE structuré (`deliverable`) rendu en carte, et le texte du chat
    devient une courte intro — évite de dupliquer le contenu dans la bulle.
    """
    if not results:
        return {"status": "error", "kind": intent, "answer": _GENERIC_ERROR, "doc": None}

    by_kind = {r["kind"]: r for r in results}
    content = by_kind.get("content")
    composed = by_kind.get("compose")
    # Agent producteur du livrable : content (résumé/fiche) ou compose (document).
    producer = (content if content and content["status"] == "success"
                else composed if composed and composed["status"] == "success" else None)
    deliverable = _build_deliverable(producer) if producer else None

    # Rédaction d'un document original -> carte « document » + intro courte.
    if intent in ("compose", "planner") and composed is not None:
        if deliverable:
            return {"status": "success", "kind": "compose",
                    "answer": _deliverable_lead(deliverable),
                    "doc": composed.get("doc"), "deliverable": deliverable}
        return composed  # échec compose -> remonter l'erreur

    if intent == "analyze":
        idp = by_kind.get("idp")
        parts, doc = [], None
        if idp and idp["status"] == "success":
            parts.append(idp["answer"]); doc = idp["doc"]
        if deliverable:
            doc = doc or content["doc"]
            parts.append("Et voici le résumé de ce cours 👇")
            return {"status": "success", "kind": "analyze",
                    "answer": "\n\n---\n\n".join(parts), "doc": doc, "deliverable": deliverable}
        if parts:
            return {"status": "success", "kind": "analyze",
                    "answer": "\n\n---\n\n".join(parts), "doc": doc}
        # tout a échoué -> remonter la 1re erreur
        return next((r for r in results if r["status"] != "success"), results[-1])

    # summary / revision -> livrable en carte + intro courte
    if deliverable:
        return {"status": "success", "kind": content["kind"],
                "answer": _deliverable_lead(deliverable), "doc": content["doc"],
                "deliverable": deliverable}

    # question / explain_section / échec content -> dernière étape pertinente
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
        if agent == "compose":
            # Rédaction : consigne = demande de l'étudiant ; doc = ancrage OPTIONNEL
            # (jamais de clarify si absent -> compose peut générer librement).
            named = _match_doc(str(s.get("doc") or ""), documents) if s.get("doc") else None
            steps.append({"agent": "compose", "instructions": question, "doc": named,
                          "content_type": None, "sections": None})
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

def _handle_revise(instruction: str, last_deliverable: dict) -> dict:
    """Itère sur le livrable courant : réutilise son contenu + applique la consigne."""
    revised = revise_document(
        last_deliverable.get("markdown", ""),
        instruction,
        title=last_deliverable.get("title"),
    )
    if revised["status"] != "success":
        return {"status": "error", "kind": "compose",
                "answer": revised.get("message") or _GENERIC_ERROR, "doc": None,
                "steps_run": ["compose"], "trace": {"intent": "revise"}}
    deliverable = {
        "type": last_deliverable.get("type", "document"),  # conserve le type d'origine
        "title": revised.get("title") or last_deliverable.get("title") or "Document",
        "doc": last_deliverable.get("doc", ""),
        "markdown": revised["content"],
    }
    return {"status": "success", "kind": "compose",
            "answer": "Voici la version mise à jour 👇", "doc": deliverable["doc"],
            "deliverable": deliverable, "steps_run": ["compose"],
            "trace": {"intent": "revise"}}


def handle(question, history=None, selected_doc=None, use_planner=False,
           last_deliverable=None) -> dict:
    """
    Traite une demande d'étudiant via le système multi-agents (routage déterministe
    par défaut). Retour : {status, kind, answer (Markdown), doc, steps_run, trace}.
    """
    cleaned = (question or "").strip()
    if not cleaned:
        return {"status": "error", "kind": "tutor", "answer": _GENERIC_ERROR,
                "doc": None, "steps_run": [], "trace": {}}

    # Itération sur le livrable courant (AVANT toute planification) : si l'étudiant
    # demande une modification QUI VISE le livrable, on le RÉUTILISE. Le verbe
    # d'édition seul ne suffit pas (cf. _is_revision_request) : une question qui
    # contient « ajoute »/« corrige » sans viser le livrable va au routage normal.
    if last_deliverable and _is_revision_request(cleaned):
        return _handle_revise(cleaned, last_deliverable)

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
