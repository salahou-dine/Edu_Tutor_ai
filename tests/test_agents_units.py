"""
Briques déterministes des agents : parsing JSON tolérant, validation/ancrage de
l'analyse IDP, presenter (filtrage du jargon), artefact (langue, round-trip),
caches disque (document_store), helpers compose et titler.
"""

import pytest

from agents.common import extract_json
from agents.idp_agent import _coerce_ids, _validate_analysis
from agents.presenter import _strip_section_ids, present_artifact
from agents.artifact import DocumentArtifact, Extraction, Section, _detect_language
from agents.compose_agent import _extract_title, _fallback_title
from agents.titler import _clean_title
from agents.registry import CAPABILITIES, capability


# --- extract_json (parsing tolérant des sorties LLM) ---------------------------

class TestExtractJson:
    def test_json_pur(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_barrieres_markdown(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_texte_autour(self):
        text = 'Voici le plan demandé :\n{"steps": [{"agent": "tutor"}]}\nVoilà !'
        assert extract_json(text) == {"steps": [{"agent": "tutor"}]}

    def test_tableau(self):
        assert extract_json("resultat: [1, 2, 3]") == [1, 2, 3]

    def test_inexploitable(self):
        assert extract_json("aucun json ici") is None
        assert extract_json("") is None
        assert extract_json(None) is None


# --- Validation / ancrage de l'analyse IDP --------------------------------------

VALID_IDS = {"s1", "s2", "s3"}


class TestValidateAnalysis:
    def test_ancrage_filtre_les_sections_inventees(self):
        raw = {"doc_type": "course", "themes": [
            {"label": "SCADA", "section_ids": ["s1", "s99"], "evidence": "…"},
        ]}
        out = _validate_analysis(raw, VALID_IDS)
        assert out["themes"][0]["section_ids"] == ["s1"]  # s99 inventé -> jeté

    def test_elements_malformes_jetes(self):
        raw = {"doc_type": "course", "themes": [
            {"label": "OK", "section_ids": ["s1"]},
            {"pas_de_label": True},
            "une chaîne au lieu d'un dict",
        ]}
        out = _validate_analysis(raw, VALID_IDS)
        assert len(out["themes"]) == 1

    def test_doc_type_inconnu_normalise(self):
        out = _validate_analysis({"doc_type": "invoice"}, VALID_IDS)
        assert out["doc_type"] == "unknown"

    def test_date_ambigue_non_devinee(self):
        raw = {"doc_type": "course", "dates": [
            {"text_raw": "avant la fin du semestre", "normalized": "", "section_ids": ["s2"]},
        ]}
        out = _validate_analysis(raw, VALID_IDS)
        assert out["dates"][0]["normalized"] is None

    def test_structure_inexploitable(self):
        assert _validate_analysis(None, VALID_IDS) is None
        assert _validate_analysis(["liste"], VALID_IDS) is None

    def test_coerce_ids_accepte_chaine_unique(self):
        assert _coerce_ids("s1", VALID_IDS) == ["s1"]
        assert _coerce_ids(["s1", "s99"], VALID_IDS) == ["s1"]
        assert _coerce_ids(42, VALID_IDS) == []


# --- Presenter : jamais de jargon côté étudiant ----------------------------------

class TestPresenter:
    def test_strip_section_ids(self):
        assert "s12" not in _strip_section_ids("Ce thème (s11, s12) est central")
        assert _strip_section_ids("Voir s19 à s25 pour le détail") == "Voir pour le détail"

    def test_strip_conserve_les_mots_normaux(self):
        # « s » suivi de chiffres seulement : les mots normaux restent intacts.
        text = "Les systèmes SCADA en 2026"
        assert _strip_section_ids(text) == text

    def _artifact(self, analysis):
        extraction = Extraction(
            status="ok", language="fr", page_count=10,
            sections=[Section("s1", "Introduction", 1, 100, "heading", "texte")],
            extraction_quality="good",
        )
        return DocumentArtifact(
            doc_id="abc", source="x.pdf", filename="x.pdf",
            extraction=extraction, analysis=analysis,
        )

    def test_present_artifact_sans_jargon(self):
        artifact = self._artifact({
            "doc_type": "course", "title": "Cybersécurité OT",
            "themes": [{"label": "SCADA (s1)", "section_ids": ["s1"], "evidence": ""}],
            "objectives": [], "definitions": [], "instructions": [], "dates": [],
            "warnings": ["contenu de s1 partiellement redondant"],
        })
        rendered = present_artifact(artifact)
        assert "Cybersécurité OT" in rendered
        for forbidden in ("s1", "doc_id", "section_id", "json", "chunk"):
            assert forbidden not in rendered, f"jargon « {forbidden} » visible"

    def test_present_artifact_sans_analyse(self):
        rendered = present_artifact(self._artifact(None))
        assert "n'a pas pu être extrait" in rendered


# --- Artefact : langue + round-trip ----------------------------------------------

class TestArtifact:
    def test_detection_langue(self):
        assert _detect_language("le cours est dans la salle avec les étudiants pour une heure") == "fr"
        assert _detect_language("the course is in the room with the students for one hour") == "en"
        assert _detect_language("xyz 123") is None

    def test_round_trip_dict(self):
        extraction = Extraction("ok", "fr", 3,
                                [Section("s1", "Intro", 1, 42, "heading", "contenu")], "good")
        artifact = DocumentArtifact("id01", "a.pdf", "a.pdf", extraction, {"doc_type": "course"})
        rebuilt = DocumentArtifact.from_dict(artifact.to_dict())
        assert rebuilt.doc_id == "id01"
        assert rebuilt.extraction.sections[0].text == "contenu"
        assert rebuilt.analysis == {"doc_type": "course"}


# --- document_store : identité + caches (répertoires temporaires) ----------------

class TestDocumentStore:
    def test_doc_id_depend_du_contenu_pas_du_nom(self, tmp_path):
        from agents.document_store import compute_doc_id
        a = tmp_path / "a.txt"; a.write_text("même contenu")
        b = tmp_path / "renommé.txt"; b.write_text("même contenu")
        c = tmp_path / "c.txt"; c.write_text("autre contenu")
        assert compute_doc_id(str(a)) == compute_doc_id(str(b))
        assert compute_doc_id(str(a)) != compute_doc_id(str(c))

    def test_cache_artefact_round_trip(self, tmp_path, monkeypatch):
        from config import settings
        from agents import document_store
        monkeypatch.setattr(settings, "ARTIFACTS_DIR", tmp_path / "artifacts")
        extraction = Extraction("ok", "fr", 1, [], "good")
        artifact = DocumentArtifact("cafe000000000001", "x.pdf", "x.pdf", extraction)
        document_store.save_artifact(artifact)
        loaded = document_store.load_artifact("cafe000000000001")
        assert loaded is not None and loaded.filename == "x.pdf"

    def test_cache_invalide_si_schema_change(self, tmp_path, monkeypatch):
        from config import settings
        from agents import document_store
        monkeypatch.setattr(settings, "ARTIFACTS_DIR", tmp_path / "artifacts")
        extraction = Extraction("ok", "fr", 1, [], "good")
        artifact = DocumentArtifact("cafe000000000002", "x.pdf", "x.pdf", extraction)
        artifact.schema_version = 1  # schéma obsolète
        document_store.save_artifact(artifact)
        assert document_store.load_artifact("cafe000000000002") is None

    def test_cache_generated_par_options(self, tmp_path, monkeypatch):
        from config import settings
        from agents import document_store
        monkeypatch.setattr(settings, "GENERATED_DIR", tmp_path / "generated")
        document_store.save_generated("doc1", "summary", {"content": "A"}, {"sections": "all"})
        document_store.save_generated("doc1", "summary", {"content": "B"}, {"sections": ["s1"]})
        assert document_store.load_generated("doc1", "summary", {"sections": "all"})["content"] == "A"
        assert document_store.load_generated("doc1", "summary", {"sections": ["s1"]})["content"] == "B"
        assert document_store.load_generated("doc1", "revision", {"sections": "all"}) is None


# --- Compose : titre ---------------------------------------------------------------

class TestComposeHelpers:
    def test_titre_extrait_du_h1(self):
        assert _extract_title("# Mon exposé\n\ncontenu") == "Mon exposé"

    def test_titre_h1_plus_loin(self):
        assert _extract_title("---\n\n# Titre réel\ncorps") == "Titre réel"

    def test_pas_de_h1(self):
        assert _extract_title("## Sous-titre seulement") is None

    def test_fallback_depuis_la_consigne(self):
        assert _fallback_title("écris une note sur les mots de passe") == \
            "écris une note sur les mots de passe"
        long = "écris " + "très " * 30 + "long"
        assert _fallback_title(long).endswith("…")
        assert _fallback_title("") == "Document"


# --- Titler : nettoyage du titre ----------------------------------------------------

class TestTitlerClean:
    def test_guillemets_et_ponctuation(self):
        assert _clean_title('« Différence IT vs OT. »') == "Différence IT vs OT"

    def test_premiere_ligne_non_vide(self):
        assert _clean_title("\n\nTitre utile\nexplication superflue") == "Titre utile"

    def test_troncature(self):
        assert len(_clean_title("mot " * 40)) <= 49

    def test_vide(self):
        assert _clean_title("") is None
        assert _clean_title("  \n  ") is None


# --- Registre : cohérence des capacités ----------------------------------------------

class TestRegistry:
    def test_les_quatre_agents(self):
        assert set(CAPABILITIES) == {"tutor", "idp", "content", "compose"}

    def test_content_exige_artefact(self):
        assert "artifact" in capability("content").preconditions

    def test_compose_et_tutor_sans_precondition(self):
        assert capability("compose").preconditions == ()
        assert capability("tutor").preconditions == ()

    def test_capability_inconnue(self):
        assert capability("inexistant") is None
