"""
Routage déterministe de l'orchestrateur : résolution de document, table
d'intention, garde de révision, réparation des préconditions, composition.

C'est ici que vivaient les deux bugs corrigés le 2026-07-12 (sur-capture de
_RE_REVISE et de _RE_COMPOSE) : ces tests les verrouillent contre la régression.
"""

import pytest

from agents.orchestrator import (
    _build_deliverable,
    _classify_to_plan,
    _compose,
    _corpus_common_tokens,
    _deliverable_lead,
    _is_revision_request,
    _match_doc,
    _repair_preconditions,
    _target_doc,
)


# --- Résolution de document (_match_doc / _target_doc) -----------------------

class TestMatchDoc:
    def test_token_court_distinctif(self, corpus):
        assert _match_doc("analyse le CM3", corpus)["filename"].startswith(
            "cybersecurity_OT_40_CM3"
        )

    def test_nom_de_fichier_complet_avec_extension(self, corpus):
        # Cas planner : le LLM renvoie le nom complet -> les tokens communs
        # (cybersecurity, 2026…) ne doivent pas rendre le match ambigu.
        q = "cybersecurity_OT_40_CM3_Spring_2026.pdf"
        assert _match_doc(q, corpus)["doc_id"] == "aaaa000000000003"

    def test_nom_complet_sans_extension(self, corpus):
        q = "cybersecurity_OT_40_CM1_Spring_2026"
        assert _match_doc(q, corpus)["doc_id"] == "aaaa000000000001"

    def test_underscore_separe_les_tokens(self, corpus):
        # \w garderait « cybersecurity_ot_40_cm4 » en un bloc -> aucun match.
        assert _match_doc("ouvre cybersecurity_ot_40_cm4", corpus) is not None

    def test_ambigu_renvoie_none(self, corpus):
        assert _match_doc("le cours de cybersécurité", corpus) is None
        assert _match_doc("résume", corpus) is None

    def test_tokens_communs_du_corpus(self, corpus):
        shared = _corpus_common_tokens(corpus)
        assert "cybersecurity" in shared
        assert "2026" in shared
        # les tokens distinctifs n'y sont pas
        assert "cm3" not in shared


class TestTargetDoc:
    def test_nomme_prioritaire_sur_selectionne(self, corpus):
        doc = _target_doc("résume le CM1", corpus, "cybersecurity_OT_40_CM3_Spring_2026.pdf")
        assert doc["doc_id"] == "aaaa000000000001"

    def test_selectionne_si_rien_de_nomme(self, corpus):
        doc = _target_doc("résume ce document", corpus, "cybersecurity_OT_40_CM4_Spring_2026.pdf")
        assert doc["doc_id"] == "aaaa000000000004"

    def test_unique_document(self, single_doc_corpus):
        assert _target_doc("résume", single_doc_corpus, None)["doc_id"] == "bbbb000000000001"

    def test_ambigu_sans_selection(self, corpus):
        assert _target_doc("résume", corpus, None) is None


# --- Garde de révision (_is_revision_request) — bug 1 -------------------------

REVISIONS_LEGITIMES = [
    "raccourcis-le",
    "raccourcis ce document.",
    "ajoute une section sur les pare-feux",
    "rends-le plus formel",
    "simplifie ce document, pour un public débutant.",
    "reformule et régénère ce document dans une autre version.",
    "développe la partie sur la détection",
    "plus court stp",
    "simplifie",
    "régénère",
    "mets à jour la fiche",
    "améliore le résumé",
    "enlève la conclusion",
]

FAUX_POSITIFS_CORRIGES = [
    "explique pourquoi on ajoute un pare-feu",
    "corrige mon exercice",
    "et si on change de protocole ?",
    "pourquoi on remplace les mots de passe par des passkeys ?",
    "comment on supprime un malware ?",
]


@pytest.mark.parametrize("question", REVISIONS_LEGITIMES)
def test_revision_legitime_detectee(question):
    assert _is_revision_request(question) is True


@pytest.mark.parametrize("question", FAUX_POSITIFS_CORRIGES)
def test_verbe_edition_sans_cible_nest_pas_une_revision(question):
    assert _is_revision_request(question) is False


def test_question_sans_verbe_edition():
    assert _is_revision_request("qu'est-ce que la kill chain ?") is False


# --- Table d'intention (_classify_to_plan) -----------------------------------

class TestClassifyIntent:
    def _intent(self, question, docs, selected=None):
        return _classify_to_plan(question, docs, selected).get("intent")

    # Bug 2 : « document/texte » génériques + mot de résumé/fiche -> ressource
    # d'étude, PAS une rédaction compose.
    def test_resume_du_document_va_en_summary(self, corpus):
        assert self._intent("fais un résumé du document CM1", corpus) == "summary"

    def test_fiche_sur_le_document_va_en_revision(self, corpus):
        assert self._intent("prépare une fiche sur le document CM3", corpus) == "revision"

    def test_resume_de_ce_document_ambigu_clarifie(self, corpus):
        # 3 cours, rien de nommé ni sélectionné -> demander lequel (décision D2).
        assert self._intent("fais-moi un résumé de ce document", corpus) == "clarify"

    def test_resume_de_ce_document_avec_selection(self, corpus):
        plan = _classify_to_plan(
            "fais-moi un résumé de ce document", corpus,
            "cybersecurity_OT_40_CM3_Spring_2026.pdf",
        )
        assert plan["intent"] == "summary"
        assert plan["steps"][0]["doc"]["doc_id"] == "aaaa000000000003"

    # Compose légitime : types spécifiques et « document » sans résumé/fiche.
    @pytest.mark.parametrize("question", [
        "écris un exposé d'une page sur la détection d'intrusion",
        "rédige-moi un rapport sur les attaques SCADA",
        "génère un document sur le CM3",
        "écris un texte sur les pare-feux",
        "rédige un rapport sur l'analyse des risques",  # « analyse » ne doit pas capter
    ])
    def test_compose_legitime(self, corpus, question):
        assert self._intent(question, corpus) == "compose"

    def test_compose_ancre_sur_le_cours_nomme(self, corpus):
        plan = _classify_to_plan("génère un document sur le CM3", corpus, None)
        assert plan["steps"][0]["doc"]["doc_id"] == "aaaa000000000003"

    def test_compose_libre_sans_cours_nomme(self, corpus):
        plan = _classify_to_plan("rédige un rapport sur les attaques SCADA", corpus, None)
        assert plan["steps"][0].get("doc") is None  # génération libre, pas de clarify

    # Autres intentions.
    def test_analyze(self, corpus):
        plan = _classify_to_plan("analyse le cours CM1", corpus, None)
        assert plan["intent"] == "analyze"
        assert [s["agent"] for s in plan["steps"]] == ["idp", "content"]

    def test_summary_simple(self, corpus):
        assert self._intent("fais un résumé du CM3", corpus) == "summary"

    def test_revision_simple(self, corpus):
        assert self._intent("fais-moi une fiche de révision du CM3", corpus) == "revision"

    def test_question_par_defaut(self, corpus):
        plan = _classify_to_plan("qu'est-ce que la kill chain ?", corpus, None)
        assert plan["intent"] == "question"
        assert plan["steps"] == [{"agent": "tutor"}]

    def test_no_document(self):
        assert self._intent("résume le cours", []) == "no_document"


# --- Réparation des préconditions ---------------------------------------------

class TestRepairPreconditions:
    def test_insere_idp_avant_content_si_non_analyse(self, corpus):
        doc = corpus[0]  # analyzed=False
        steps = _repair_preconditions([{"agent": "content", "doc": doc, "content_type": "summary"}])
        assert [s["agent"] for s in steps] == ["idp", "content"]

    def test_pas_didp_si_deja_analyse(self, corpus):
        doc = corpus[2]  # analyzed=True
        steps = _repair_preconditions([{"agent": "content", "doc": doc, "content_type": "summary"}])
        assert [s["agent"] for s in steps] == ["content"]

    def test_pas_de_doublon_idp(self, corpus):
        doc = corpus[0]
        steps = _repair_preconditions([
            {"agent": "idp", "doc": doc},
            {"agent": "content", "doc": doc, "content_type": "summary"},
        ])
        assert [s["agent"] for s in steps] == ["idp", "content"]

    def test_tutor_ancre_sur_section_exige_artefact(self, corpus):
        doc = corpus[0]
        steps = _repair_preconditions([{"agent": "tutor", "doc": doc, "sections": "réseau"}])
        assert [s["agent"] for s in steps] == ["idp", "tutor"]

    def test_tutor_simple_sans_precondition(self):
        steps = _repair_preconditions([{"agent": "tutor"}])
        assert [s["agent"] for s in steps] == ["tutor"]

    def test_compose_sans_precondition(self, corpus):
        doc = corpus[0]  # non analysé : compose ne déclenche PAS d'IDP
        steps = _repair_preconditions([{"agent": "compose", "doc": doc, "instructions": "x"}])
        assert [s["agent"] for s in steps] == ["compose"]


# --- Livrables & composition ----------------------------------------------------

class TestDeliverable:
    def test_livrable_resume(self):
        d = _build_deliverable({
            "kind": "content", "status": "success", "content_type": "summary",
            "doc": "cours.pdf", "markdown": "# Résumé\ncontenu",
        })
        assert d["type"] == "summary"
        assert d["title"] == "Résumé — cours"
        assert d["markdown"].startswith("# Résumé")

    def test_livrable_document_compose(self):
        d = _build_deliverable({
            "kind": "compose", "status": "success", "content_type": "document",
            "doc": None, "markdown": "# Mon exposé\n…", "title": "Mon exposé",
        })
        assert d["type"] == "document"
        assert d["title"] == "Mon exposé"

    def test_intros_par_type(self):
        assert "fiche" in _deliverable_lead({"type": "revision"})
        assert "document" in _deliverable_lead({"type": "document"})
        assert "résumé" in _deliverable_lead({"type": "summary"})

    def test_compose_summary_produit_un_livrable(self):
        results = [{
            "kind": "content", "status": "success", "content_type": "summary",
            "doc": "cours.pdf", "markdown": "# Résumé", "answer": "# Résumé",
        }]
        final = _compose("summary", results)
        assert final["status"] == "success"
        assert final["deliverable"]["type"] == "summary"
        assert final["answer"] != final["deliverable"]["markdown"]  # intro ≠ contenu

    def test_compose_echec_remonte_l_erreur(self):
        results = [{"kind": "content", "status": "error", "answer": "échec", "doc": "x.pdf"}]
        final = _compose("summary", results)
        assert final["status"] == "error"

    def test_analyze_compose_structure_et_resume(self):
        results = [
            {"kind": "idp", "status": "success", "answer": "## Structure", "doc": "c.pdf"},
            {"kind": "content", "status": "success", "content_type": "summary",
             "doc": "c.pdf", "markdown": "# Résumé", "answer": "# Résumé"},
        ]
        final = _compose("analyze", results)
        assert final["status"] == "success"
        assert "## Structure" in final["answer"]
        assert final["deliverable"]["type"] == "summary"
