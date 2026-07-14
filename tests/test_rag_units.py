"""
Unités RAG déterministes : découpage structure-aware (chunker), détection de
titres et segmentation texte (document_loader), mise en forme des indications
(source_formatter).
"""

from rag.chunker import _split_text, chunk_segments
from rag.document_loader import _clean_heading, _looks_like_heading, _segments_from_text
from rag.source_formatter import _clean_text, _make_excerpt, format_course_indications


# --- Chunker ---------------------------------------------------------------------

class TestSplitText:
    def test_texte_court_inchange(self):
        assert _split_text("Un paragraphe court.", target=900, overlap=120) == [
            "Un paragraphe court."
        ]

    def test_texte_vide(self):
        assert _split_text("   ", target=900, overlap=120) == []

    def test_decoupe_avec_overlap(self):
        text = "\n\n".join(f"Paragraphe {i}. " + "mot " * 40 for i in range(6))
        pieces = _split_text(text, target=300, overlap=60)
        assert len(pieces) > 1
        assert all(len(p) <= 300 + 120 for p in pieces)  # cible + marge d'empaquetage

    def test_phrase_demesuree_decoupee_dur(self):
        text = "x" * 2500  # aucune ponctuation
        pieces = _split_text(text, target=900, overlap=100)
        assert len(pieces) >= 3
        assert "".join(p.replace("\n\n", "") for p in pieces).startswith("x" * 100)


class TestChunkSegments:
    SEGMENTS = [
        {"text": "Contenu de l'introduction.", "heading": "Introduction", "page": 1},
        {"text": "Contenu réseau. " * 100, "heading": "Réseaux", "page": 2},
        {"text": "Sans titre détecté.", "heading": None, "page": 5},
    ]

    def test_metadonnees_completes(self):
        chunks = chunk_segments(self.SEGMENTS, source="data/courses/x.pdf", title="Mon cours")
        first = chunks[0]
        assert first["id"] == "x_chunk_1"
        assert first["title"] == "Mon cours"
        assert first["section"] == "Introduction"
        assert first["page"] == 1

    def test_section_longue_redecoupee_sans_fusion(self):
        chunks = chunk_segments(self.SEGMENTS, source="x.pdf", target_chars=300, overlap_chars=50)
        reseaux = [c for c in chunks if c.get("section") == "Réseaux"]
        assert len(reseaux) > 1  # section longue -> plusieurs chunks
        # jamais de fusion : aucun chunk ne mélange deux sections
        assert all(c.get("section") != "Introduction" or "réseau" not in c["text"].lower()
                   for c in chunks)

    def test_segment_sans_heading(self):
        chunks = chunk_segments(self.SEGMENTS, source="x.pdf")
        last = chunks[-1]
        assert "section" not in last
        assert last["page"] == 5

    def test_index_continus(self):
        chunks = chunk_segments(self.SEGMENTS, source="x.pdf", target_chars=300, overlap_chars=50)
        assert [c["chunk_index"] for c in chunks] == list(range(1, len(chunks) + 1))


# --- Détection de titres (document_loader) ------------------------------------

class TestHeadingDetection:
    def test_markdown(self):
        assert _looks_like_heading("## Les protocoles industriels") is True

    def test_numerotation_decimale(self):
        assert _looks_like_heading("2.3 Architecture des SCADA") is True

    def test_numerotation_romaine(self):
        assert _looks_like_heading("IV. Défense en profondeur") is True

    def test_mot_cle_chapitre(self):
        assert _looks_like_heading("Chapitre 2 : les automates") is True

    def test_majuscules_courtes(self):
        assert _looks_like_heading("INTRODUCTION AUX RESEAUX") is True

    def test_phrase_normale_rejetee(self):
        assert _looks_like_heading("Le protocole Modbus est utilisé depuis 1979.") is False

    def test_ligne_trop_longue_rejetee(self):
        assert _looks_like_heading("MOTS " * 30) is False

    def test_signal_faible_exige_forme_de_titre(self):
        # police élevée MAIS ne commence ni par majuscule ni par chiffre
        assert _looks_like_heading("or connected to the network", font_size=20, body_size=12) is False
        assert _looks_like_heading("Network segmentation", font_size=20, body_size=12) is True

    def test_clean_heading(self):
        assert _clean_heading("## • Sécurité   des  réseaux ##") == "Sécurité des réseaux"


class TestSegmentsFromText:
    def test_segmentation_par_titres(self):
        text = "# Intro\ncontenu un\n## Partie A\ncontenu deux\ncontenu trois"
        segments = _segments_from_text(text)
        assert [s["heading"] for s in segments] == ["Intro", "Partie A"]
        assert segments[1]["text"] == "contenu deux\ncontenu trois"

    def test_texte_sans_titres(self):
        segments = _segments_from_text("juste du texte plat\nsur deux lignes")
        assert len(segments) == 1
        assert segments[0]["heading"] is None


# --- Indications de cours (source_formatter) -----------------------------------

class TestIndications:
    def _chunk(self, **kw):
        base = {"text": "Le protocole Modbus est un standard de communication industriel très répandu.",
                "source": "data/courses/cours_ot.pdf", "title": "Cyber OT",
                "section": "Protocoles", "page": 3, "distance": 0.3}
        base.update(kw)
        return base

    def test_indication_complete(self):
        [ind] = format_course_indications([self._chunk()])
        assert ind["course"] == "Cyber OT"
        assert ind["part"] == "Protocoles"
        assert "Modbus" in ind["excerpt"]
        # jamais de champs techniques
        assert "distance" not in ind and "chunk_index" not in ind

    def test_repli_page_puis_generique(self):
        [ind] = format_course_indications([self._chunk(section=None)])
        assert ind["part"] == "Page 3"
        [ind2] = format_course_indications([self._chunk(section=None, page=None)])
        assert ind2["part"] == "Section générale"

    def test_deduplication_par_cours_et_partie(self):
        chunks = [self._chunk(), self._chunk(text="Autre extrait de la même partie.")]
        assert len(format_course_indications(chunks)) == 1

    def test_excerpt_tronque_proprement(self):
        long_text = "Première phrase complète ici. " + "Suite " * 50
        excerpt = _make_excerpt(_clean_text(long_text))
        from config import settings
        assert len(excerpt) <= settings.EXCERPT_MAX_CHARS + 5  # + « … »

    def test_excerpt_demarrage_en_debut_de_phrase(self):
        excerpt = _make_excerpt("suite coupée en plein milieu. La vraie phrase commence ici clairement.")
        assert excerpt.startswith("… ")

    def test_clean_text_retire_le_markdown(self):
        cleaned = _clean_text("Du **gras** et du `code` | tableau\n```\nbloc\n```")
        assert "`" not in cleaned and "|" not in cleaned and "bloc" not in cleaned
