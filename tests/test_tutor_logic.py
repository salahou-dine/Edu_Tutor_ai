"""
Logique déterministe de l'agent tuteur : choix du mode pédagogique, requête de
recherche (dilution corrigée R1), détection résumé global, extraction de la
question de vérification, reconstruction du texte de cours.
"""

import pytest

from config import settings
from agents.tutor_agent import (
    _best_distance,
    _build_retrieval_query,
    _course_condensed,
    _course_full_text,
    _is_course_meta_question,
    _is_course_summary_request,
    _needs_history_context,
    _resolve_course_for_summary,
    choose_tutor_mode,
    extract_verification_question,
)


# --- Choix du mode pédagogique -------------------------------------------------

class TestChooseMode:
    def _chunks(self, best):
        return [{"distance": best}, {"distance": best + 0.3}]

    def test_course_grounded_sous_le_seuil(self):
        mode = choose_tutor_mode(self._chunks(settings.COURSE_GROUNDED_MAX_DISTANCE - 0.05))
        assert mode == "course_grounded"

    def test_mixed_entre_les_seuils(self):
        mode = choose_tutor_mode(self._chunks(settings.MIXED_MAX_DISTANCE - 0.05))
        assert mode == "mixed"

    def test_general_au_dela(self):
        assert choose_tutor_mode(self._chunks(settings.MIXED_MAX_DISTANCE + 0.1)) == "general_tutor"

    def test_general_sans_chunks(self):
        assert choose_tutor_mode([]) == "general_tutor"

    def test_general_sans_distances(self):
        assert choose_tutor_mode([{"distance": None}]) == "general_tutor"

    def test_best_distance_prend_le_minimum(self):
        assert _best_distance([{"distance": 0.8}, {"distance": 0.3}, {"distance": None}]) == 0.3


# --- Requête de recherche (correction R1 : pas de dilution) ---------------------

HISTORY = [
    {"role": "user", "content": "Parle-moi de Stuxnet"},
    {"role": "assistant", "content": "Stuxnet est un ver…"},
    {"role": "user", "content": "Et les automates programmables ?"},
    {"role": "assistant", "content": "Les PLC…"},
]


class TestRetrievalQuery:
    def test_question_autosuffisante_cherchee_seule(self):
        q = "Quelles sont les étapes de la cyber kill chain selon Lockheed Martin ?"
        assert _build_retrieval_query(q, HISTORY) == q

    def test_relance_courte_enrichie(self):
        q = "explique-le"
        enriched = _build_retrieval_query(q, HISTORY)
        assert q in enriched and "automates" in enriched

    def test_relance_par_conjonction_enrichie(self):
        q = "et donc comment on s'en protège concrètement ?"
        assert "Stuxnet" in _build_retrieval_query(q, HISTORY) or "automates" in _build_retrieval_query(q, HISTORY)

    def test_sans_historique(self):
        assert _build_retrieval_query("explique-le", None) == "explique-le"

    def test_needs_history(self):
        assert _needs_history_context("développe") is True
        assert _needs_history_context("et Stuxnet ?") is True
        assert _needs_history_context(
            "Quelle est la différence entre IT et OT en cybersécurité industrielle ?"
        ) is False


# --- Détection résumé global & méta-cours ---------------------------------------

class TestSummaryDetection:
    @pytest.mark.parametrize("q", [
        "résume le cours",
        "fais-moi une synthèse du cours",
        "de quoi parle le cours ?",
        "donne-moi le plan du chapitre",
    ])
    def test_resume_global(self, q):
        assert _is_course_summary_request(q) is True

    @pytest.mark.parametrize("q", [
        "qu'est-ce qu'un pare-feu ?",           # ni résumé ni cours
        "résume-moi la notion de défense en profondeur",  # résumé mais pas « le cours »
    ])
    def test_pas_resume_global(self, q):
        assert _is_course_summary_request(q) is False

    def test_meta_question(self):
        assert _is_course_meta_question("de quoi parle le cours ?") is True
        assert _is_course_meta_question("qu'est-ce que la kill chain ?") is False


class TestResolveCourse:
    COURSES = [{"title": "Cyber OT CM1", "source": "a.pdf"},
               {"title": "Cyber OT CM3", "source": "b.pdf"}]

    def test_cours_unique(self):
        assert _resolve_course_for_summary("résume le cours", self.COURSES[:1]) == "Cyber OT CM1"

    def test_cours_nomme(self):
        assert _resolve_course_for_summary("résume le cours cyber ot cm3", self.COURSES) == "Cyber OT CM3"

    def test_plusieurs_sans_nom(self):
        assert _resolve_course_for_summary("résume le cours", self.COURSES) is None


# --- Extraction de la question de vérification -----------------------------------

class TestVerificationQuestion:
    def test_format_gras(self):
        answer = "**Réponse**\n…\n**Question de vérification**\nQu'est-ce qu'un PLC ?"
        assert extract_verification_question(answer) == "Qu'est-ce qu'un PLC ?"

    def test_format_numerote_herite(self):
        answer = "…\n5. Question de vérification : Cite deux modes."
        assert "Cite deux modes." in extract_verification_question(answer)

    def test_absente(self):
        assert extract_verification_question("Réponse simple sans structure.") == ""

    def test_reponse_vide(self):
        assert extract_verification_question("") == ""


# --- Reconstruction du texte de cours --------------------------------------------

CHUNKS = [
    {"text": "Introduction aux SCADA.", "section": "Intro", "page": 1, "chunk_index": 1},
    {"text": "Suite de l'intro.", "section": "Intro", "page": 1, "chunk_index": 2},
    {"text": "Les protocoles Modbus.", "section": "Protocoles", "page": 3, "chunk_index": 3},
]


class TestCourseText:
    def test_full_text_titres_non_dupliques(self):
        text = _course_full_text(CHUNKS)
        assert text.count("## Intro") == 1
        assert "## Protocoles" in text
        assert "Modbus" in text

    def test_condense_couvre_toutes_les_sections(self):
        condensed = _course_condensed(CHUNKS, lead_chars=50)
        assert "## Intro" in condensed and "## Protocoles" in condensed

    def test_condense_tronque(self):
        condensed = _course_condensed(CHUNKS, lead_chars=10)
        assert "Introduction aux SCADA." not in condensed  # amorce coupée à 10 car.
