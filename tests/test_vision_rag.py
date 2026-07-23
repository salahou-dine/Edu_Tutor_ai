"""
Indexation RAG des figures/schémas par vision. Les appels Hermes vision sont
MOCKÉS (aucun LLM) ; le cache et le vectorstore sont redirigés vers tmp.
"""

import pytest

from config import settings
from rag import vision_describe


@pytest.fixture
def vision_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", tmp_path / "vision_cache")
    monkeypatch.setattr(settings, "RAG_VISION_ENABLED", True)
    return tmp_path


# --- Description d'une image (cache, filtre décoratif) ---------------------------

class TestDescribe:
    def test_description_normale(self, vision_tmp, monkeypatch):
        monkeypatch.setattr(vision_describe, "ask_hermes_with_skill",
                            lambda *a, **k: {"status": "success",
                                             "content": "Schéma réseau IT/OT séparés par un pare-feu."})
        d = vision_describe._describe_image_file("/x.png", "hash1")
        assert d == "Schéma réseau IT/OT séparés par un pare-feu."

    def test_decoratif_filtre(self, vision_tmp, monkeypatch):
        monkeypatch.setattr(vision_describe, "ask_hermes_with_skill",
                            lambda *a, **k: {"status": "success", "content": "DECORATIF"})
        assert vision_describe._describe_image_file("/logo.png", "hash2") is None

    def test_cache_evite_le_second_appel(self, vision_tmp, monkeypatch):
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return {"status": "success", "content": "Une description."}

        monkeypatch.setattr(vision_describe, "ask_hermes_with_skill", fake)
        vision_describe._describe_image_file("/x.png", "hashC")
        vision_describe._describe_image_file("/x.png", "hashC")  # 2e fois : cache
        assert len(calls) == 1

    def test_echec_non_cache(self, vision_tmp, monkeypatch):
        monkeypatch.setattr(vision_describe, "ask_hermes_with_skill",
                            lambda *a, **k: {"status": "error", "content": "", "error": "boom"})
        assert vision_describe._describe_image_file("/x.png", "hashE") is None
        assert not (settings.VISION_CACHE_DIR / "hashE.json").exists()


# --- Segments d'un cours image ---------------------------------------------------

class TestCourseSegments:
    def _png(self, path):
        from PIL import Image
        Image.new("RGB", (400, 300), "#ccddee").save(path)

    def test_image_seule_decrite(self, vision_tmp, monkeypatch):
        monkeypatch.setattr(vision_describe, "ask_hermes_with_skill",
                            lambda *a, **k: {"status": "success", "content": "Un diagramme d'architecture."})
        img = vision_tmp / "schema.png"
        self._png(img)
        segments = vision_describe.course_vision_segments(str(img))
        assert len(segments) == 1
        assert "diagramme d'architecture" in segments[0]["text"]
        assert segments[0]["heading"] == "Figure (schéma)"

    def test_desactive_retourne_vide(self, vision_tmp, monkeypatch):
        monkeypatch.setattr(settings, "RAG_VISION_ENABLED", False)
        img = vision_tmp / "s.png"
        self._png(img)
        assert vision_describe.course_vision_segments(str(img)) == []

    def test_format_texte_ignore(self, vision_tmp):
        f = vision_tmp / "cours.md"
        f.write_text("# texte", encoding="utf-8")
        assert vision_describe.course_vision_segments(str(f)) == []


# --- Records d'indexation (ids distincts, kind=image) ----------------------------

class TestIndexerVisionRecords:
    def test_records_vision(self, vision_tmp, monkeypatch):
        from PIL import Image
        from rag import indexer
        Image.new("RGB", (400, 300), "#abcdef").save(vision_tmp / "cours_img.png")
        monkeypatch.setattr(
            "rag.vision_describe.course_vision_segments",
            lambda p: [{"text": "Figure (page 1) : un schéma.", "heading": "Figure (schéma)", "page": 1}],
        )
        records = indexer._course_vision_records(vision_tmp / "cours_img.png")
        assert records is not None
        ids, docs, metas = records
        assert all("_img_" in i for i in ids)          # ids distincts du texte
        assert all(m["kind"] == "image" for m in metas)  # marqués image
        assert "schéma" in docs[0]

    def test_enrich_desactive(self, vision_tmp, monkeypatch):
        from rag import indexer
        monkeypatch.setattr(settings, "RAG_VISION_ENABLED", False)
        assert indexer.enrich_course_vision(vision_tmp / "x.png") == 0
