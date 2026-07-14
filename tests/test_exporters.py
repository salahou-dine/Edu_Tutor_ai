"""
Exports des livrables : .docx (python-docx), .md, .pdf (xhtml2pdf), noms de
fichiers. Vérifie les octets produits (signatures) et le contenu du .docx.
"""

import io
import zipfile

from interface.exporters import (
    PDF_AVAILABLE,
    safe_filename,
    to_docx_bytes,
    to_markdown_bytes,
    to_pdf_bytes,
)

MARKDOWN = """# Fiche de révision — CM3

## 1. Points clés
- **Monitoring** : passif vs actif
- Threat hunting

## 2. À retenir
1. Modèle OSI
2. Encapsulation

---

Paragraphe final avec du **gras** au milieu.
"""


class TestDocx:
    def test_signature_zip_office(self):
        data = to_docx_bytes(MARKDOWN, "Fiche CM3")
        assert data[:2] == b"PK"

    def test_contenu_present_dans_le_document(self):
        data = to_docx_bytes(MARKDOWN, "Fiche CM3")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        for fragment in ("Fiche CM3", "Monitoring", "Threat hunting", "Encapsulation"):
            assert fragment in xml

    def test_markdown_vide(self):
        assert to_docx_bytes("", None)[:2] == b"PK"  # document valide, juste vide


class TestMarkdown:
    def test_octets_utf8(self):
        assert to_markdown_bytes(MARKDOWN).decode("utf-8") == MARKDOWN

    def test_vide(self):
        assert to_markdown_bytes("") == b""
        assert to_markdown_bytes(None) == b""


class TestPdf:
    def test_signature_pdf(self):
        if not PDF_AVAILABLE:  # environnement sans xhtml2pdf : le bouton n'existe pas
            import pytest
            pytest.skip("dépendances PDF absentes")
        data = to_pdf_bytes(MARKDOWN, "Fiche CM3")
        assert data[:4] == b"%PDF"
        assert len(data) > 1000


class TestSafeFilename:
    def test_nettoyage(self):
        name = safe_filename("Fiche de révision — CM3", "docx")
        assert name.endswith(".docx")
        assert " " not in name and "—" not in name

    def test_titre_vide(self):
        assert safe_filename("", "md") == "livrable.md"

    def test_caracteres_speciaux(self):
        name = safe_filename('Rapport : "SCADA" / OT ?', "pdf")
        assert name.endswith(".pdf")
        for ch in ':"/?':
            assert ch not in name
