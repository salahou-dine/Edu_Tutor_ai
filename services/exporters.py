"""
Export des livrables (résumés, fiches) vers des fichiers téléchargeables.

Le contenu généré par l'agent Content est du Markdown structuré. On le convertit
ici en octets prêts pour `st.download_button` :
- `.md`   : brut, aucune dépendance ;
- `.docx` : via python-docx (déjà présent), parsing Markdown minimal mais suffisant
            pour notre contenu (titres, listes à puces / numérotées, gras, paragraphes).

Le PDF n'est pas encore géré (nécessiterait une dépendance : reportlab / fpdf2 /
weasyprint) — à ajouter dans un second temps.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.shared import Pt

# PDF optionnel : dépend de `xhtml2pdf` + `markdown` (à installer séparément).
# Import protégé pour que l'app fonctionne AVANT l'installation ; l'UI n'affiche
# le bouton PDF que si PDF_AVAILABLE est vrai.
try:  # pragma: no cover - dépend de l'environnement
    import markdown as _markdown
    from xhtml2pdf import pisa as _pisa

    PDF_AVAILABLE = True
except Exception:  # noqa: BLE001 - toute erreur d'import => PDF indisponible
    PDF_AVAILABLE = False


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def to_markdown_bytes(markdown: str) -> bytes:
    """Livrable brut en .md (UTF-8)."""
    return (markdown or "").encode("utf-8")


def _add_runs_with_bold(paragraph, text: str) -> None:
    """Ajoute le texte à un paragraphe en rendant **gras** les segments **...**."""
    pos = 0
    for match in _BOLD.finditer(text):
        if match.start() > pos:
            paragraph.add_run(text[pos:match.start()])
        paragraph.add_run(match.group(1)).bold = True
        pos = match.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def to_docx_bytes(markdown: str, title: str | None = None) -> bytes:
    """Convertit un Markdown structuré en .docx (octets).

    Parsing volontairement simple : titres #, listes à puces / numérotées, gras,
    et paragraphes. Les lignes de séparation `---` deviennent des sauts.
    """
    document = Document()
    if title:
        document.add_heading(title, level=0)

    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.strip() in ("---", "***", "___"):
            document.add_paragraph()
            continue

        heading = _HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), 4)
            document.add_heading(heading.group(2).strip(), level=level)
            continue

        bullet = _BULLET.match(line)
        if bullet:
            para = document.add_paragraph(style="List Bullet")
            _add_runs_with_bold(para, bullet.group(1).strip())
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            para = document.add_paragraph(style="List Number")
            _add_runs_with_bold(para, numbered.group(1).strip())
            continue

        para = document.add_paragraph()
        _add_runs_with_bold(para, line.strip())

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_PDF_CSS = """
@page { size: A4; margin: 2cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 11pt; color: #222; line-height: 1.45; }
h1 { font-size: 18pt; } h2 { font-size: 14pt; } h3 { font-size: 12pt; }
h1, h2, h3, h4 { color: #1a1a2e; margin: 0.6em 0 0.3em; }
ul, ol { margin: 0.3em 0 0.6em 1.2em; }
code { font-family: Courier, monospace; background: #f2f2f2; }
hr { border: 0; border-top: 1px solid #ccc; }
"""


def to_pdf_bytes(markdown_text: str, title: str | None = None) -> bytes:
    """Convertit un Markdown en PDF (octets) via markdown -> HTML -> xhtml2pdf.

    Lève RuntimeError si les dépendances PDF ne sont pas installées (l'UI doit
    tester PDF_AVAILABLE avant d'appeler cette fonction).
    """
    if not PDF_AVAILABLE:
        raise RuntimeError(
            "Export PDF indisponible : installe les dépendances "
            "(pip install xhtml2pdf markdown)."
        )
    body = _markdown.markdown(
        markdown_text or "", extensions=["tables", "fenced_code", "sane_lists"]
    )
    heading = f"<h1>{title}</h1>" if title else ""
    html = (
        "<html><head><meta charset='utf-8'>"
        f"<style>{_PDF_CSS}</style></head><body>{heading}{body}</body></html>"
    )
    buffer = io.BytesIO()
    _pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    return buffer.getvalue()


def safe_filename(title: str, extension: str) -> str:
    """Nom de fichier propre pour le téléchargement (ex. 'Fiche de révision — CM3' -> 'Fiche_de_revision_CM3.docx')."""
    base = (title or "livrable").strip()
    base = base.replace("—", "-")
    base = re.sub(r"[^\w\s-]", "", base, flags=re.UNICODE)
    base = re.sub(r"[\s]+", "_", base).strip("_") or "livrable"
    return f"{base}.{extension.lstrip('.')}"
